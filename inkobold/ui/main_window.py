from __future__ import annotations

import sys
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GLib, Gtk  # noqa: E402

from inkobold.core.document import Document
from inkobold.core.assets import asset_root
from inkobold.core import autosave
from inkobold.core import character as ch
from inkobold.core.flavor import current as current_flavor
from inkobold.core.grid import GridOverlay
from inkobold.core.history import History
from inkobold.core.libraries import CATEGORIES, ensure_libraries, open_in_file_manager
from inkobold.core.mirror import MirrorModifier
from inkobold.core.settings import load_settings, save_settings
from inkobold.core.shortcuts import merge_shortcuts
from inkobold.core.sun import SunLight
from inkobold.input import InputHub
from inkobold.tools import default_tools
from inkobold.ui.actions import ActionsMixin
from inkobold.ui.asset_browser import AssetBrowser
from inkobold.ui.canvas import Canvas
from inkobold.ui.character_ops import CharacterOpsMixin
from inkobold.ui.doc_tabs import DocTabsMixin, _DocTab
from inkobold.ui.effects_actions import EffectsMixin
from inkobold.ui.file_ops import FileOpsMixin
from inkobold.ui.menus import MenusMixin, build_menubar
from inkobold.ui.popup_keys import attach_tab_among_editables
from inkobold.ui.rig_panel import RigPanel
from inkobold.ui.rig_preview import RigPreview
from inkobold.ui.sidebar import SidebarMixin, build_sidebar
from inkobold.ui.tool_options import ToolOptionsMixin, build_toolbox_page
from inkobold.ui.theme import DEFAULT_CSS, build_theme_css


class MainWindow(
    ActionsMixin,
    MenusMixin,
    ToolOptionsMixin,
    SidebarMixin,
    EffectsMixin,
    FileOpsMixin,
    CharacterOpsMixin,
    DocTabsMixin,
    Gtk.ApplicationWindow,
):
    """The application window: owns the document, tools, history and settings.

    Behaviour lives in the mixins (one module each under ``inkobold.ui``); this
    class holds the state, builds the top-level layout in ``_build_ui`` and
    keeps the small cross-cutting helpers (history, status bar, theme, window
    mode).
    """

    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title=current_flavor().display_name)
        self.set_default_size(1280, 860)

        self.document: Optional[Document] = None
        self.tools = default_tools()
        self.tool_id = "pen"
        self._syncing_tool_ui = False
        self.app_settings = load_settings()
        # Shared primary / secondary paint colors when color_follows_tools is on
        self._shared_color: tuple[int, int, int, int] = self.tools["pen"].color
        self._shared_secondary: tuple[int, int, int, int] = self.tools["pen"].secondary_color
        self.shortcuts = merge_shortcuts(self.app_settings.shortcut_overrides)
        self.history = History(max_steps=self.app_settings.history_steps)
        self._tabs: list[_DocTab] = []
        self._active_tab: int = -1
        self._closing_tab_index: Optional[int] = None
        self._syncing_layers = False
        self._syncing_frames = False
        self._opacity_hist_armed = False
        self._drag_layer_from: Optional[int] = None
        self._drag_frame_from: Optional[int] = None
        self._allow_close = False
        self._quit_dialog_open = False
        self._quit_after_save = False
        # Documents the user chose "Don't Save" for during the current quit.
        self._quit_discarded: set[int] = set()
        # One background writer, so saves land on disk in the order they were
        # requested (a later save can never be overwritten by an earlier one).
        self._save_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="inkobold-save")
        self._save_futures: list[Future] = []
        # Crash-recovery copies of unsaved documents (core.autosave); None if the
        # autosave folder can't be created.
        try:
            self._autosave: Optional[autosave.Session] = autosave.Session.start()
        except OSError as exc:
            print(f"autosave disabled: {exc}", file=sys.stderr)
            self._autosave = None
        self._autosave_timer_id: Optional[int] = None
        self._autosave_warned = False
        self._window_mode = "borderless"
        self._anim_playing = False
        self._anim_timer_id: Optional[int] = None
        self._effect_session: Optional[dict] = None
        self._shown_title: Optional[str] = None
        self._shown_status: Optional[str] = None
        self._layers_sig: Optional[tuple] = None
        self.mirror = MirrorModifier()
        self.grid = GridOverlay()
        self.rig_view = ch.RigView()
        self.rig_panel: Optional[RigPanel] = None
        self.rig_preview: Optional[RigPreview] = None
        self._rig_page_index: Optional[int] = None
        self.sun = SunLight(
            active=bool(self.app_settings.sun_active),
            enabled=bool(self.app_settings.sun_visible),
            x_norm=float(self.app_settings.sun_x_norm),
            y_norm=float(self.app_settings.sun_y_norm),
            elevation=float(self.app_settings.sun_elevation),
        )
        self.sun.clamp()
        self.tile_wrap = False
        self.pixel_mode = current_flavor().pixel_default
        # Whole-number enlargement for every export (nearest neighbour)
        self.export_scale = 1
        self.input_hub = InputHub()
        self.input_hub.start()
        ensure_libraries()
        self._css_provider = Gtk.CssProvider()

        self._typing_shortcuts_suspended = False
        self._shortcut_focus_windows: set[Gtk.Window] = set()

        self._build_actions()
        self._build_ui()
        self._install_shortcuts()
        self._watch_focus_for_shortcuts(self)
        attach_tab_among_editables(self)
        self._open_at_startup = False
        # Cancelled when a path is opened via CLI / desktop "Open with"
        # (HANDLES_OPEN) before the idle runs.
        self._startup_pending = True
        GLib.idle_add(self._maybe_prompt_startup)

        GLib.timeout_add_seconds(2, self._refresh_status)
        self._schedule_autosave()

    def _build_ui(self) -> None:
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), self._css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._apply_theme_css()

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(root)

        # Menu bar
        menubar = build_menubar(self)
        root.append(menubar)

        # Shared default width for Tools and Layers columns
        # (~2 tool buttons: min-width + padding + flow spacing + toolbox chrome)
        self._side_panel_w = 260
        self._panes_sized = False
        self._sidebar_sized = False

        # Toolbox | (canvas + layers) — drag handle on the toolbox's right edge
        self._left_split = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, vexpand=True)
        split = self._left_split
        split.set_wide_handle(True)
        split.set_resize_start_child(False)
        split.set_shrink_start_child(False)
        split.set_resize_end_child(True)
        split.set_shrink_end_child(False)
        root.append(split)

        toolbox_page = build_toolbox_page(self)

        # Libraries tab — open category folders in the system file manager
        libraries_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["toolbox"])
        libraries_page.append(Gtk.Label(
            label="Open a category folder to add assets. Brushes (.png) appear in Brush → Custom; "
            "patterns (.png / .jpeg) appear in Fill; fonts (.ttf / .otf) appear in the Type tool Library source.",
            xalign=0,
            wrap=True,
        ))
        for cat_id, cat_label in CATEGORIES:
            btn = Gtk.Button(label=cat_label)
            btn.add_css_class("lib-cat-btn")
            btn.set_hexpand(True)
            if cat_id == "brushes":
                btn.set_tooltip_text("Open brushes folder — drop .png tip images here")
            elif cat_id == "fonts":
                btn.set_tooltip_text("Open fonts folder — drop .ttf / .otf files here")
            elif cat_id == "palettes":
                btn.set_tooltip_text("Open palettes folder — drop GIMP .gpl palettes here")
            else:
                btn.set_tooltip_text("Open patterns folder — drop .png / .jpeg tiles here")
            btn.connect("clicked", self._on_library_category, cat_id)
            libraries_page.append(btn)

        left_notebook = Gtk.Notebook()
        left_notebook.set_size_request(self._side_panel_w, -1)
        tools_scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        tools_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        tools_scroll.set_child(toolbox_page)
        left_notebook.append_page(tools_scroll, Gtk.Label(label="Tools"))
        left_notebook.append_page(libraries_page, Gtk.Label(label="Libraries"))
        self.asset_browser: Optional[AssetBrowser] = None
        if current_flavor().asset_root is not None:
            self.asset_browser = AssetBrowser(
                asset_root(self.app_settings.asset_root),
                on_open=self.open_path,
                on_import=self.import_image_path,
                on_root_changed=self._on_asset_root_changed,
                on_open_character=self.open_character_path if current_flavor().character_rig else None,
                on_new_character=self.action_new_character if current_flavor().character_rig else None,
            )
            left_notebook.append_page(self.asset_browser, Gtk.Label(label="Assets"))
        if current_flavor().character_rig:
            self.rig_panel = RigPanel(
                get_document=lambda: self.document,
                view=self.rig_view,
                push_history=self._push_history,
                on_changed=self._on_rig_changed,
                on_new=self.action_new_character,
                on_open=self.action_open_character,
                on_save=self.action_save,
            )
            self._rig_page_index = left_notebook.append_page(self.rig_panel, Gtk.Label(label="Rig"))
        self._left_notebook = left_notebook
        left_notebook.connect("switch-page", self._on_left_tab_switched)
        split.set_start_child(left_notebook)

        self._load_active_tool_settings()

        # Canvas | Layers — drag handle on the layers panel's left edge
        self._right_split = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, vexpand=True)
        right_split = self._right_split
        right_split.set_wide_handle(True)
        right_split.set_resize_start_child(True)
        right_split.set_shrink_start_child(False)
        right_split.set_resize_end_child(False)
        right_split.set_shrink_end_child(False)
        split.set_end_child(right_split)

        self.canvas = Canvas(
            get_document=lambda: self.document,
            get_tool=lambda: self.tools[self.tool_id],
            get_color=lambda: self._active_tool().color,
            get_brush=lambda: self._active_tool().brush_size,
            input_hub=self.input_hub,
            on_changed=self._on_doc_changed,
            push_history=self._push_history,
            get_mirror=lambda: self.mirror,
            get_grid=lambda: self.grid,
            get_sun=lambda: self.sun,
            get_tile_wrap=lambda: self.tile_wrap,
            get_pixel_mode=lambda: self.pixel_mode,
            on_type_editing=self._on_type_editing_changed,
            on_sun_moved=self._on_sun_moved,
            get_rig_view=lambda: self.rig_view,
            on_rig_changed=self._on_rig_changed,
            on_mirror_changed=self._sync_mirror_array_ui,
        )
        type_tool = self.tools.get("type")
        if type_tool is not None and hasattr(type_tool, "set_editing_changed_callback"):
            type_tool.set_editing_changed_callback(self._on_type_editing_changed)
        self.canvas.animation_playing = False
        self.canvas.renderer.checker_light = bool(self.app_settings.checker_light)
        if current_flavor().character_rig:
            # Canvas | live rig preview (shown for character tabs only)
            canvas_split = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, vexpand=True)
            canvas_split.set_wide_handle(True)
            canvas_split.set_resize_start_child(True)
            canvas_split.set_shrink_start_child(False)
            canvas_split.set_resize_end_child(False)
            canvas_split.set_shrink_end_child(False)
            canvas_split.set_start_child(self.canvas)
            self.rig_preview = RigPreview(get_document=lambda: self.document)
            self.rig_preview.set_size_request(300, -1)
            self.rig_preview.set_visible(False)
            canvas_split.set_end_child(self.rig_preview)
            right_split.set_start_child(canvas_split)
        else:
            right_split.set_start_child(self.canvas)

        side = build_sidebar(self)
        right_split.set_end_child(side)

        # Apply equal defaults after the paneds have a real allocation
        right_split.connect("notify::width", self._on_pane_width_notify)
        split.connect("notify::width", self._on_pane_width_notify)
        self._sidebar_split.connect("notify::height", self._on_sidebar_height_notify)
        self._sidebar_sized = False
        GLib.idle_add(self._apply_default_pane_widths)
        GLib.idle_add(self._apply_default_sidebar_split)

        self.status = Gtk.Label(xalign=0, css_classes=["status"], hexpand=True)
        root.append(self.status)
        self._set_status()

    def _on_pane_width_notify(self, *_a) -> None:
        if not self._panes_sized:
            self._apply_default_pane_widths()

    def _apply_default_pane_widths(self) -> bool:
        """Set Tools and Layers columns to the same default width once laid out."""
        if self._panes_sized:
            return False
        panel_w = self._side_panel_w
        left = self._left_split
        right = self._right_split
        lw = left.get_width()
        rw = right.get_width()
        # Wait for a real layout; notify::width will retry
        if lw < panel_w * 2 or rw < panel_w * 2:
            return False
        left.set_position(panel_w)
        right.set_position(max(0, rw - panel_w))
        left_ok = abs(left.get_position() - panel_w) <= 2
        right_ok = abs((right.get_width() - right.get_position()) - panel_w) <= 4
        if left_ok and right_ok:
            self._panes_sized = True
        return False

    def _on_library_category(self, _btn: Gtk.Button, category: str) -> None:
        ensure_libraries()
        from inkobold.core.libraries import category_dir

        folder = category_dir(category)
        if not open_in_file_manager(folder):
            err = Gtk.AlertDialog()
            err.set_message("Could not open folder")
            err.set_detail(str(folder))
            err.show(self)
            return
        if category == "patterns":
            # Patterns may change while the folder is open; refresh list when returning
            self._refresh_pattern_dropdown(
                select_path=getattr(self._active_tool(), "pattern_path", None)
            )
        elif category == "brushes":
            self._refresh_brush_dropdown(
                select_path=getattr(self._active_tool(), "brush_path", None)
            )
        elif category == "fonts":
            if getattr(self._active_tool(), "uses_type_options", False):
                self._refresh_font_dropdown(
                    select_path=getattr(self._active_tool(), "font_path", None)
                )

    def _on_left_tab_switched(self, _nb: Gtk.Notebook, _page, page_num: int) -> None:
        # Libraries tab index 1 — refresh pattern/font lists when visiting Tools again
        if page_num == 0:
            tool = self._active_tool()
            if self._tool_uses_pattern_picker(tool):
                self._refresh_pattern_dropdown(select_path=getattr(tool, "pattern_path", None))
            if getattr(tool, "uses_brush_options", False):
                self._refresh_brush_dropdown(select_path=getattr(tool, "brush_path", None))
            if getattr(tool, "uses_type_options", False):
                self._refresh_font_dropdown(select_path=getattr(tool, "font_path", None))

    def _push_history(self) -> None:
        if self.document is not None:
            self.history.push(self.document)

    def _apply_history_restore(self) -> None:
        if not self.document:
            return
        self.action_stop_animation()
        self.canvas.renderer.invalidate()
        self._sync_anim_controls_from_doc()
        self._rebuild_frames()
        self._rebuild_layers()
        self.canvas.queue_render()
        self.canvas.refresh_guides()
        self._set_status()
        self._sync_rig_panel()

    def action_undo(self, *_a) -> None:
        if not self.document or not self.history.can_undo:
            return
        # An open type edit shares the same checkpoint as Undo — cancel instead
        # of popping an extra history step.
        type_tool = self.tools.get("type")
        if type_tool is not None and getattr(type_tool, "editing", False):
            self._cancel_type_edit_if_any()
            return
        if self.history.undo(self.document):
            self._apply_history_restore()

    def action_redo(self, *_a) -> None:
        if not self.document or not self.history.can_redo:
            return
        if self.history.redo(self.document):
            self._apply_history_restore()

    def _on_doc_changed(self) -> None:
        self._set_status()

    def _refresh_status(self) -> bool:
        self._set_status()
        return True

    def _doc_display_name(self) -> str:
        return self._tab_display_name(self.document)

    def _update_doc_title(self) -> None:
        self._refresh_tab_styles()
        if not self.document:
            title = current_flavor().display_name
        else:
            _text, _tip, marked = self._tab_title_text(self.document)
            title = f"{current_flavor().display_name} — {marked}"
        if title != self._shown_title:
            self._shown_title = title
            self.set_title(title)

    _DEPTH_LABELS = {64: "16bpc", 32: "8bpc", 24: "RGB8", 8: "Gray8"}

    def _set_status(self) -> None:
        # Called after every pointer event; only touch GTK when the text changes.
        self._update_doc_title()
        if not self.document:
            text = self.input_hub.status
        else:
            d = self.document
            path = self._tab_display_name(d)
            dirty = "*" if d.dirty else ""
            ly = d.active_layer
            renderer = self.canvas.renderer
            gl = "GLES" if renderer.es else "GL"
            if not renderer.ready:
                gl = "GL-FAIL"
            depth_label = self._DEPTH_LABELS.get(int(d.color_depth), f"{d.color_depth}-bit")
            text = (
                f"{path}{dirty}  {d.width}×{d.height}  {d.dpi}dpi  {depth_label}  "
                f"frame={d.current_frame_index + 1}/{d.frame_count}@{d.fps:g}fps  "
                f"layer={ly.name}  tool={self.tool_id}  "
                f"zoom={renderer.zoom:.2f}  {gl}  |  {self.input_hub.status}"
            )
        if text != self._shown_status:
            self._shown_status = text
            self.status.set_text(text)

    def _apply_theme_css(self, rgb: tuple[int, int, int] | None = None) -> None:
        if rgb is not None:
            css_text = build_theme_css(*rgb)
        elif self.app_settings.use_custom_theme:
            r, g, b = self.app_settings.theme_rgb
            css_text = build_theme_css(r, g, b)
        elif current_flavor().theme_rgb is not None:
            css_text = build_theme_css(*current_flavor().theme_rgb)
        else:
            css_text = DEFAULT_CSS
        self._css_provider.load_from_data(css_text.encode("utf-8"))

    def _persist_settings(self) -> None:
        save_settings(self.app_settings)

    def apply_window_mode(self, mode: str) -> None:
        """Switch between fullscreen, borderless (maximized), and windowed."""
        if mode not in ("fullscreen", "borderless", "windowed"):
            return
        self._window_mode = mode
        if mode == "fullscreen":
            self.set_decorated(False)
            self.fullscreen()
        elif mode == "borderless":
            self.unfullscreen()
            self.set_decorated(False)
            self.maximize()
        else:  # windowed
            self.unfullscreen()
            self.set_decorated(True)
            self.unmaximize()
            self.set_default_size(1280, 860)
        GLib.idle_add(self.canvas.request_fit)

    def shutdown_input(self) -> None:
        self.action_stop_animation()
        self.input_hub.close()
