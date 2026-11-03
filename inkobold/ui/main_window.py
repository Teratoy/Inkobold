from __future__ import annotations

import os
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from inkobold.core.document import EXPORT_SCALES, Document
from inkobold.core.assets import asset_root
from inkobold.core import autosave
from inkobold.core import character as ch
from inkobold.core.flavor import current as current_flavor
from inkobold.core.grid import GridOverlay
from inkobold.core.history import History
from inkobold.core.libraries import CATEGORIES, ensure_libraries, open_in_file_manager
from inkobold.core.mirror import MirrorModifier
from inkobold.core.settings import default_theme_rgb, load_settings, save_settings
from inkobold.core.shortcuts import (
    DEFAULT_SHORTCUTS,
    SHORTCUT_LABELS,
    SHORTCUT_ORDER,
    format_accels,
    merge_shortcuts,
)
from inkobold.core.sun import SunLight
from inkobold.input import InputHub
from inkobold.tools import default_tools
from inkobold.ui.asset_browser import AssetBrowser
from inkobold.ui.rig_panel import RigPanel
from inkobold.ui.rig_preview import RigPreview
from inkobold.ui.canvas import Canvas
from inkobold.ui.character_ops import CharacterOpsMixin
from inkobold.ui.file_ops import FileOpsMixin
from inkobold.ui.effects_actions import EffectsMixin
from inkobold.ui.sidebar import SidebarMixin, build_sidebar
from inkobold.ui.tool_options import TOOL_ORDER, ToolOptionsMixin, build_toolbox_page
from inkobold.ui.doc_tabs import DocTabsMixin, _DocTab, build_doc_tabs
from inkobold.ui.dialogs import (
    CaptureShortcutDialog,
    ColorDepthDialog,
    CropDialog,
    DpiDialog,
    GridOverlayDialog,
    HistoryStepsDialog,
    SunSettingsDialog,
    ShortcutsDialog,
    ThemeColorDialog,
)
from inkobold.ui.popup_keys import attach_tab_among_editables
from inkobold.ui.theme import DEFAULT_CSS, build_theme_css


class MainWindow(ToolOptionsMixin, SidebarMixin, EffectsMixin, FileOpsMixin, CharacterOpsMixin, DocTabsMixin, Gtk.ApplicationWindow):
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

    def _build_actions(self) -> None:
        mapping = {
            "new": self.action_new,
            "open": self.action_open,
            "save": self.action_save,
            "save_as": self.action_save_as,
            "new_character": self.action_new_character,
            "open_character": self.action_open_character,
            "recover_autosave": self.action_recover_autosave,
            "memory_autosave": self.action_memory_autosave,
            "import_image": self.action_import_image,
            "export": self.action_export,
            "export_layer": self.action_export_layer,
            "export_layers": self.action_export_layers,
            "export_anim_gif": self.action_export_anim_gif,
            "export_anim_png": self.action_export_anim_png,
            "export_anim_sheet": self.action_export_anim_sheet,
            "fit": lambda *_a: self.canvas.request_fit(),
            "window_fullscreen": lambda *_a: self.apply_window_mode("fullscreen"),
            "window_borderless": lambda *_a: self.apply_window_mode("borderless"),
            "window_windowed": lambda *_a: self.apply_window_mode("windowed"),
            "clear_selection": self.action_clear_selection,
            "undo": self.action_undo,
            "redo": self.action_redo,
            "mirror_horizontal": self.action_mirror_horizontal,
            "mirror_vertical": self.action_mirror_vertical,
            "flip_horizontal": self.action_flip_horizontal,
            "flip_vertical": self.action_flip_vertical,
            "rotate_cw": self.action_rotate_cw,
            "rotate_ccw": self.action_rotate_ccw,
            "rotate_180": self.action_rotate_180,
            "effect_pixelate": self.action_effect_pixelate,
            "effect_kuwahara": self.action_effect_kuwahara,
            "effect_gaussian_blur": self.action_effect_gaussian_blur,
            "effect_sharpen": self.action_effect_sharpen,
            "effect_drop_shadow": self.action_effect_drop_shadow,
            "effect_dither": self.action_effect_dither,
            "effect_posterize": self.action_effect_posterize,
            "effect_flora": self.action_effect_flora,
            "effect_color_basics": self.action_effect_color_basics,
            "effect_grayscale": self.action_effect_grayscale,
            "effect_invert": self.action_effect_invert,
            "effect_solarize": self.action_effect_solarize,
            "effect_curves": self.action_effect_curves,
            "effect_threshold": self.action_effect_threshold,
            "effect_liquify": self.action_effect_liquify,
            "effect_edge_detect": self.action_effect_edge_detect,
            "effect_emboss": self.action_effect_emboss,
            "effect_normal_map": self.action_effect_normal_map,
            "effect_metal_relief": self.action_effect_metal_relief,
            "effect_milk": self.action_effect_milk,
            "add_layer": self.action_add_layer,
            "duplicate_layer": self.action_duplicate_layer,
            "delete_layer": self.action_delete_layer,
            "rename_layer": self.action_rename_layer,
            "move_layer_to_top": self.action_move_layer_to_top,
            "move_layer_to_bottom": self.action_move_layer_to_bottom,
            "merge_layer_down": self.action_merge_layer_down,
            "merge_visible_layers": self.action_merge_visible_layers,
            "merge_all_layers": self.action_merge_all_layers,
            "add_frame": self.action_add_frame,
            "duplicate_frame": self.action_duplicate_frame,
            "delete_frame": self.action_delete_frame,
            "play_animation": self.action_play_animation,
            "stop_animation": self.action_stop_animation,
            "brush_smaller": self.action_brush_smaller,
            "brush_larger": self.action_brush_larger,
            "swap_colors": self.action_swap_colors,
            "reset_colors": self.action_reset_colors,
            "theme_color": self.action_theme_color,
            "theme_reset": self.action_theme_reset,
            "memory_steps": self.action_memory_steps,
            "visible_tools": self.action_visible_tools,
            "image_dpi": self.action_image_dpi,
            "image_color_depth": self.action_image_color_depth,
            "crop_canvas": self.action_crop_canvas,
            "shortcuts_manage": self.action_shortcuts_manage,
            "shortcuts_reset": self.action_shortcuts_reset,
            "quit": self.request_quit,
        }
        for name, cb in mapping.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", cb)
            self.add_action(action)

        show_grid = Gio.SimpleAction.new_stateful(
            "show_grid", None, GLib.Variant.new_boolean(False)
        )
        show_grid.connect("change-state", self._on_show_grid_change)
        self.add_action(show_grid)

        show_sun = Gio.SimpleAction.new_stateful(
            "show_sun",
            None,
            GLib.Variant.new_boolean(bool(self.sun.enabled)),
        )
        show_sun.connect("change-state", self._on_show_sun_change)
        self.add_action(show_sun)

        use_sun = Gio.SimpleAction.new_stateful(
            "use_sun",
            None,
            GLib.Variant.new_boolean(bool(self.sun.active)),
        )
        use_sun.connect("change-state", self._on_use_sun_change)
        self.add_action(use_sun)

        deactivate_sun = Gio.SimpleAction.new("deactivate_sun", None)
        deactivate_sun.connect("activate", self.action_deactivate_sun)
        self.add_action(deactivate_sun)

        tile_preview = Gio.SimpleAction.new_stateful(
            "tile_preview", None, GLib.Variant.new_boolean(False)
        )
        tile_preview.connect("change-state", self._on_tile_preview_change)
        self.add_action(tile_preview)

        tile_wrap = Gio.SimpleAction.new_stateful(
            "tile_wrap", None, GLib.Variant.new_boolean(False)
        )
        tile_wrap.connect("change-state", self._on_tile_wrap_change)
        self.add_action(tile_wrap)

        pixel_mode = Gio.SimpleAction.new_stateful(
            "pixel_mode", None, GLib.Variant.new_boolean(self.pixel_mode)
        )
        pixel_mode.connect("change-state", self._on_pixel_mode_change)
        self.add_action(pixel_mode)

        export_scale = Gio.SimpleAction.new_stateful(
            "export_scale", GLib.VariantType.new("s"), GLib.Variant.new_string("1")
        )
        export_scale.connect("change-state", self._on_export_scale_change)
        self.add_action(export_scale)

        checker_light = Gio.SimpleAction.new_stateful(
            "checker_light",
            None,
            GLib.Variant.new_boolean(bool(self.app_settings.checker_light)),
        )
        checker_light.connect("change-state", self._on_checker_light_change)
        self.add_action(checker_light)

        color_follows = Gio.SimpleAction.new_stateful(
            "color_follows_tools",
            None,
            GLib.Variant.new_boolean(bool(self.app_settings.color_follows_tools)),
        )
        color_follows.connect("change-state", self._on_color_follows_tools_change)
        self.add_action(color_follows)

        debug_mode = Gio.SimpleAction.new_stateful(
            "debug_mode",
            None,
            GLib.Variant.new_boolean(bool(self.app_settings.debug_mode)),
        )
        debug_mode.connect("change-state", self._on_debug_mode_change)
        self.add_action(debug_mode)

        grid_settings = Gio.SimpleAction.new("grid_settings", None)
        grid_settings.connect("activate", self.action_grid_settings)
        self.add_action(grid_settings)

        sun_settings = Gio.SimpleAction.new("sun_settings", None)
        sun_settings.connect("activate", self.action_sun_settings)
        self.add_action(sun_settings)

        rebind = Gio.SimpleAction.new("shortcut_rebind", GLib.VariantType.new("s"))
        rebind.connect("activate", self._on_shortcut_rebind_action)
        self.add_action(rebind)

        for tid, _label in TOOL_ORDER:
            action = Gio.SimpleAction.new(f"tool_{tid}", None)
            action.connect("activate", self._action_select_tool, tid)
            self.add_action(action)

    def _install_shortcuts(self) -> None:
        app = self.get_application()
        if app is None:
            return
        # Clear previous accelerators for known actions, then apply current map
        for action in DEFAULT_SHORTCUTS:
            app.set_accels_for_action(action, [])
        for action, accels in self.shortcuts.items():
            app.set_accels_for_action(action, accels)
        if self._typing_shortcuts_suspended:
            self._apply_typing_shortcut_filter(True)
        self._rebuild_shortcuts_submenu()
        if getattr(self, "tool_buttons", None):
            self._refresh_tool_shortcut_tips()

    @staticmethod
    def _is_text_input_widget(widget: Optional[Gtk.Widget]) -> bool:
        """True if focus is (inside) an Entry, SpinButton, TextView, etc."""
        w = widget
        while w is not None:
            if isinstance(w, (Gtk.Editable, Gtk.TextView)):
                return True
            w = w.get_parent()
        return False

    def _watch_focus_for_shortcuts(self, window: Gtk.Window) -> None:
        """Suspend bare-key accelerators while this window focuses a text field."""
        if window in self._shortcut_focus_windows:
            return
        self._shortcut_focus_windows.add(window)

        def _on_focus_widget(*_a) -> None:
            # Defer so leave→enter between fields sees the final focus widget.
            GLib.idle_add(self._refresh_typing_shortcut_suspend)
            return

        def _on_destroy(*_a) -> None:
            self._shortcut_focus_windows.discard(window)
            self._refresh_typing_shortcut_suspend()

        window.connect("notify::focus-widget", _on_focus_widget)
        window.connect("destroy", _on_destroy)

    def _any_text_input_focused(self) -> bool:
        for win in list(self._shortcut_focus_windows):
            if self._is_text_input_widget(win.get_focus()):
                return True
        return False

    def _should_suspend_typing_shortcuts(self) -> bool:
        type_tool = self.tools.get("type")
        if type_tool is not None and getattr(type_tool, "editing", False):
            return True
        return self._any_text_input_focused()

    def _refresh_typing_shortcut_suspend(self) -> bool:
        suspend = self._should_suspend_typing_shortcuts()
        if self._typing_shortcuts_suspended == suspend:
            return False
        self._typing_shortcuts_suspended = suspend
        self._apply_typing_shortcut_filter(suspend)
        return False

    def _apply_typing_shortcut_filter(self, suspend: bool) -> None:
        """Drop bare letter/digit accelerators while typing so they insert text."""
        app = self.get_application()
        if app is None:
            return
        if not suspend:
            for action in DEFAULT_SHORTCUTS:
                app.set_accels_for_action(action, [])
            for action, accels in self.shortcuts.items():
                app.set_accels_for_action(action, accels)
            return
        for action, accels in self.shortcuts.items():
            kept = [a for a in accels if "<" in a]
            app.set_accels_for_action(action, kept)

    def _cancel_type_edit_if_any(self) -> None:
        type_tool = self.tools.get("type")
        if type_tool is None or not getattr(type_tool, "editing", False):
            return
        ctx = self.canvas._ctx()
        if ctx is not None and hasattr(type_tool, "cancel"):
            type_tool.cancel(ctx)
            self.canvas.renderer.invalidate()
            self.canvas.queue_render()
            self.canvas.refresh_guides()
            self._rebuild_layers()
            self._on_doc_changed()
        else:
            type_tool.reset()
        self._refresh_typing_shortcut_suspend()

    def _on_show_grid_change(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        enabled = bool(value.get_boolean())
        action.set_state(value)
        self.grid.enabled = enabled
        if hasattr(self, "canvas"):
            self.canvas.refresh_guides()

    def _on_show_sun_change(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        enabled = bool(value.get_boolean())
        action.set_state(value)
        self.sun.enabled = enabled
        self.app_settings.sun_visible = enabled
        if enabled and not self.sun.active:
            self._set_sun_active(True)
        else:
            self._persist_settings()
            if hasattr(self, "canvas"):
                self.canvas.refresh_guides()

    def _on_use_sun_change(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        self._set_sun_active(bool(value.get_boolean()), action=action, value=value)

    def action_deactivate_sun(self, *_a) -> None:
        self._set_sun_active(False)

    def _set_sun_active(
        self,
        active: bool,
        *,
        action: Gio.SimpleAction | None = None,
        value: GLib.Variant | None = None,
    ) -> None:
        active = bool(active)
        self.sun.active = active
        self.app_settings.sun_active = active
        if not active and self.sun.enabled:
            show = self.lookup_action("show_sun")
            if show is not None:
                show.change_state(GLib.Variant.new_boolean(False))
            else:
                self.sun.enabled = False
                self.app_settings.sun_visible = False
        use = action or self.lookup_action("use_sun")
        if use is not None:
            use.set_state(value if value is not None else GLib.Variant.new_boolean(active))
        self._persist_settings()
        if hasattr(self, "canvas"):
            self.canvas.refresh_guides()

    def _on_sun_moved(self) -> None:
        self.app_settings.sun_x_norm = float(self.sun.x_norm)
        self.app_settings.sun_y_norm = float(self.sun.y_norm)
        self.app_settings.sun_elevation = float(self.sun.elevation)
        if not self.sun.active:
            self._set_sun_active(True)
        else:
            self._persist_settings()

    def _sun_light_dir(self) -> tuple[float, float, float]:
        doc = self.document
        if doc is None:
            return self.sun.light_dir()
        return self.sun.light_dir(float(doc.width), float(doc.height))

    def _sun_milk_light_dir(self) -> tuple[float, float, float]:
        doc = self.document
        if doc is None:
            return self.sun.milk_light_dir()
        return self.sun.milk_light_dir(float(doc.width), float(doc.height))

    def _on_tile_preview_change(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        enabled = bool(value.get_boolean())
        action.set_state(value)
        if hasattr(self, "canvas"):
            self.canvas.renderer.tile_preview = enabled
            self.canvas.refresh_guides()
            self.canvas.queue_render()
            if enabled:
                self.canvas.request_fit()
            self._set_status()
        # Wrap follows Tile Preview by default (on with preview, off without).
        wrap_action = self.lookup_action("tile_wrap")
        if wrap_action is not None:
            wrap_action.change_state(GLib.Variant.new_boolean(enabled))

    def _on_tile_wrap_change(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        enabled = bool(value.get_boolean())
        action.set_state(value)
        self.tile_wrap = enabled
        if hasattr(self, "tile_wrap_toggle") and self.tile_wrap_toggle.get_active() != enabled:
            self.tile_wrap_toggle.set_active(enabled)
        self._set_status()

    def _on_pixel_mode_change(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        enabled = bool(value.get_boolean())
        changed = enabled != self.pixel_mode
        action.set_state(value)
        self.pixel_mode = enabled
        if 0 <= self._active_tab < len(self._tabs):
            self._tabs[self._active_tab].pixel = enabled
        if hasattr(self, "pixel_mode_toggle") and self.pixel_mode_toggle.get_active() != enabled:
            self.pixel_mode_toggle.set_active(enabled)
        if hasattr(self, "canvas"):
            if enabled and changed:
                self.canvas.snap_pixel_view()
            else:
                self.canvas.refresh_guides()
        self._set_status()

    def _on_export_scale_change(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        try:
            scale = int(value.get_string())
        except ValueError:
            return
        if scale not in EXPORT_SCALES:
            return
        action.set_state(value)
        self.export_scale = scale

    def _on_checker_light_change(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        enabled = bool(value.get_boolean())
        action.set_state(value)
        self.app_settings.checker_light = enabled
        self._persist_settings()
        if hasattr(self, "canvas"):
            self.canvas.renderer.checker_light = enabled
            self.canvas.queue_render()

    def _on_color_follows_tools_change(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        enabled = bool(value.get_boolean())
        action.set_state(value)
        self.app_settings.color_follows_tools = enabled
        if enabled:
            # Start shared mode from the colors currently shown for the active tool
            self._take_shared_colors_from_active_tool()
            self._apply_shared_color_to_tools()
        self._persist_settings()
        if hasattr(self, "color_btn"):
            self._load_active_tool_settings()

    def _on_debug_mode_change(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        enabled = bool(value.get_boolean())
        action.set_state(value)
        self.app_settings.debug_mode = enabled
        self._persist_settings()
        if hasattr(self, "reload_btn"):
            self.reload_btn.set_visible(enabled)

    def action_grid_settings(self, *_a) -> None:
        dlg = GridOverlayDialog(self, self.grid.rows, self.grid.columns, self.grid.tile)
        dlg.connect_response(self._on_grid_settings_response)
        dlg.connect_reset_offset(self._on_grid_reset_offset)
        dlg.present()

    def _on_grid_reset_offset(self) -> None:
        self.grid.reset_offset()
        if hasattr(self, "canvas"):
            self.canvas.refresh_guides()

    def _on_grid_settings_response(self, dlg: GridOverlayDialog, response: int) -> None:
        if response != Gtk.ResponseType.OK:
            return
        self.grid.rows = dlg.rows()
        self.grid.columns = dlg.columns()
        self.grid.tile = dlg.tile()
        self.grid.clamp()
        if hasattr(self, "canvas"):
            self.canvas.refresh_guides()

    def action_sun_settings(self, *_a) -> None:
        dlg = SunSettingsDialog(self, self.sun.elevation)
        dlg.connect_response(self._on_sun_settings_response)
        dlg.present()

    def _on_sun_settings_response(self, dlg: SunSettingsDialog, response: int) -> None:
        if response == Gtk.ResponseType.REJECT:
            self._set_sun_active(False)
            return
        if response != Gtk.ResponseType.OK:
            return
        self.sun.elevation = dlg.elevation()
        self.sun.clamp()
        self.app_settings.sun_elevation = float(self.sun.elevation)
        if not self.sun.active:
            self._set_sun_active(True)
        else:
            self._persist_settings()
            if hasattr(self, "canvas"):
                self.canvas.refresh_guides()

    @staticmethod
    def _default_icon_path() -> Path:
        flavor = current_flavor()
        env = os.environ.get(f"{flavor.env_prefix}_ICON")
        if env:
            return Path(env)
        return Path(__file__).resolve().parents[2] / "scripts" / flavor.icon_file

    def _build_toolbar_app_icon(self) -> Gtk.Widget:
        size = 28
        pic = Gtk.Picture()
        pic.set_size_request(size, size)
        pic.set_content_fit(Gtk.ContentFit.CONTAIN)
        pic.set_can_shrink(False)
        pic.add_css_class("app-icon")
        pic.set_tooltip_text(current_flavor().display_name)
        path = self._default_icon_path()
        if path.is_file():
            try:
                pix = GdkPixbuf.Pixbuf.new_from_file_at_size(str(path), size, size)
                tex = Gdk.Texture.new_for_pixbuf(pix)
                # Keep a strong reference so the texture stays alive
                self._toolbar_icon_tex = tex  # type: ignore[attr-defined]
                pic.set_paintable(tex)
            except Exception:
                pass
        return pic

    def _build_ui(self) -> None:
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), self._css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._apply_theme_css()

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(root)

        # Menu bar
        menubar = Gtk.Box(spacing=4, css_classes=["toolbar"])
        root.append(menubar)

        menubar.append(self._build_toolbar_app_icon())

        file_btn = Gtk.MenuButton(label="File")
        file_menu = Gio.Menu()
        file_menu.append("New File…", "win.new")
        file_menu.append("Open File…", "win.open")
        file_menu.append("Save File", "win.save")
        file_menu.append("Save As…", "win.save_as")
        if current_flavor().character_rig:
            file_menu.append("New Character…", "win.new_character")
            file_menu.append("Open Character…", "win.open_character")
        file_menu.append("Recover Autosave…", "win.recover_autosave")
        file_menu.append("Export…", "win.export")
        file_menu.append("Export Selected Layer…", "win.export_layer")
        file_menu.append("Export Separated Layers…", "win.export_layers")
        export_anim_menu = Gio.Menu()
        export_anim_menu.append("As GIF…", "win.export_anim_gif")
        export_anim_menu.append("As PNG Folder…", "win.export_anim_png")
        export_anim_menu.append("As Sprite Sheet…", "win.export_anim_sheet")
        file_menu.append_submenu("Export Animation", export_anim_menu)
        scale_menu = Gio.Menu()
        for scale in EXPORT_SCALES:
            item = Gio.MenuItem.new(f"{scale}×", None)
            item.set_action_and_target_value("win.export_scale", GLib.Variant.new_string(str(scale)))
            scale_menu.append_item(item)
        file_menu.append_submenu("Export Scale", scale_menu)
        file_menu.append("Import Image as Layer…", "win.import_image")
        file_btn.set_menu_model(file_menu)
        menubar.append(file_btn)

        edit_btn = Gtk.MenuButton(label="Edit")
        edit_menu = Gio.Menu()
        edit_menu.append("Undo", "win.undo")
        edit_menu.append("Redo", "win.redo")
        mirror_menu = Gio.Menu()
        mirror_menu.append("Horizontal", "win.mirror_horizontal")
        mirror_menu.append("Vertical", "win.mirror_vertical")
        edit_menu.append_submenu("Mirror", mirror_menu)
        flip_menu = Gio.Menu()
        flip_menu.append("Horizontal", "win.flip_horizontal")
        flip_menu.append("Vertical", "win.flip_vertical")
        edit_menu.append_submenu("Flip", flip_menu)
        rotate_menu = Gio.Menu()
        rotate_menu.append("90° Clockwise", "win.rotate_cw")
        rotate_menu.append("90° Counter-Clockwise", "win.rotate_ccw")
        rotate_menu.append("180°", "win.rotate_180")
        edit_menu.append_submenu("Rotate", rotate_menu)
        edit_menu.append("Crop Canvas…", "win.crop_canvas")
        edit_btn.set_menu_model(edit_menu)
        menubar.append(edit_btn)

        effects_btn = Gtk.MenuButton(label="Effects")
        effects_menu = Gio.Menu()

        blur_menu = Gio.Menu()
        blur_menu.append("Gaussian Blur…", "win.effect_gaussian_blur")
        blur_menu.append("Sharpen…", "win.effect_sharpen")
        blur_menu.append("Kuwahara…", "win.effect_kuwahara")
        effects_menu.append_submenu("Blur & Sharpen", blur_menu)

        stylize_menu = Gio.Menu()
        stylize_menu.append("Pixelate…", "win.effect_pixelate")
        stylize_menu.append("Drop Shadow…", "win.effect_drop_shadow")
        stylize_menu.append("Dither…", "win.effect_dither")
        stylize_menu.append("Posterize…", "win.effect_posterize")
        stylize_menu.append("Flora…", "win.effect_flora")
        effects_menu.append_submenu("Stylize", stylize_menu)

        color_menu = Gio.Menu()
        color_menu.append("Color Basics…", "win.effect_color_basics")
        color_menu.append("Grayscale…", "win.effect_grayscale")
        color_menu.append("Invert…", "win.effect_invert")
        color_menu.append("Solarize…", "win.effect_solarize")
        color_menu.append("Curves…", "win.effect_curves")
        color_menu.append("Threshold…", "win.effect_threshold")
        effects_menu.append_submenu("Color", color_menu)

        distort_menu = Gio.Menu()
        distort_menu.append("Liquify…", "win.effect_liquify")
        effects_menu.append_submenu("Distort", distort_menu)

        edge_menu = Gio.Menu()
        edge_menu.append("Edge Detect…", "win.effect_edge_detect")
        edge_menu.append("Emboss…", "win.effect_emboss")
        edge_menu.append("Normal Map…", "win.effect_normal_map")
        effects_menu.append_submenu("Edge & Depth", edge_menu)

        materials_menu = Gio.Menu()
        materials_menu.append("Metal Relief…", "win.effect_metal_relief")
        materials_menu.append("Milk…", "win.effect_milk")
        effects_menu.append_submenu("Materials", materials_menu)

        effects_btn.set_menu_model(effects_menu)
        menubar.append(effects_btn)

        view_btn = Gtk.MenuButton(label="View")
        view_menu = Gio.Menu()
        view_menu.append("Fit Canvas", "win.fit")
        view_menu.append("Show Grid", "win.show_grid")
        view_menu.append("Grid…", "win.grid_settings")
        view_menu.append("Use Sun Light", "win.use_sun")
        view_menu.append("Show Sun", "win.show_sun")
        view_menu.append("Deactivate Sun", "win.deactivate_sun")
        view_menu.append("Sun…", "win.sun_settings")
        view_menu.append("Tile Preview", "win.tile_preview")
        view_menu.append("Wrap Moves", "win.tile_wrap")
        view_menu.append("Pixel Art", "win.pixel_mode")
        view_menu.append("Light Transparent Background", "win.checker_light")
        view_menu.append("Fullscreen", "win.window_fullscreen")
        view_menu.append("Borderless Window", "win.window_borderless")
        view_menu.append("Windowed", "win.window_windowed")
        view_btn.set_menu_model(view_menu)
        menubar.append(view_btn)

        layer_btn = Gtk.MenuButton(label="Layer")
        layer_menu = Gio.Menu()
        layer_menu.append("Add Layer", "win.add_layer")
        layer_menu.append("Duplicate Layer", "win.duplicate_layer")
        layer_menu.append("Delete Layer", "win.delete_layer")
        layer_menu.append("Rename Layer…", "win.rename_layer")
        layer_menu.append("Merge with Layer Below", "win.merge_layer_down")
        layer_menu.append("Merge Visible", "win.merge_visible_layers")
        layer_menu.append("Merge All", "win.merge_all_layers")
        layer_menu.append("Export Selected Layer…", "win.export_layer")
        layer_menu.append("Export Separated Layers…", "win.export_layers")
        layer_btn.set_menu_model(layer_menu)
        menubar.append(layer_btn)

        anim_btn = Gtk.MenuButton(label="Animation")
        anim_menu = Gio.Menu()
        anim_menu.append("Add Frame", "win.add_frame")
        anim_menu.append("Duplicate Frame", "win.duplicate_frame")
        anim_menu.append("Delete Frame", "win.delete_frame")
        anim_menu.append("Play / Pause", "win.play_animation")
        anim_menu.append("Stop", "win.stop_animation")
        export_anim_menu = Gio.Menu()
        export_anim_menu.append("As GIF…", "win.export_anim_gif")
        export_anim_menu.append("As PNG Folder…", "win.export_anim_png")
        export_anim_menu.append("As Sprite Sheet…", "win.export_anim_sheet")
        anim_menu.append_submenu("Export", export_anim_menu)
        anim_btn.set_menu_model(anim_menu)
        menubar.append(anim_btn)

        settings_btn = Gtk.MenuButton(label="Settings")
        settings_menu = Gio.Menu()
        theme_menu = Gio.Menu()
        theme_menu.append("Choose Color…", "win.theme_color")
        theme_menu.append("Reset to Default", "win.theme_reset")
        settings_menu.append_submenu("Theme", theme_menu)
        memory_menu = Gio.Menu()
        memory_menu.append("Undo/Redo Steps…", "win.memory_steps")
        memory_menu.append("Autosave…", "win.memory_autosave")
        settings_menu.append_submenu("Memory", memory_menu)

        image_menu = Gio.Menu()
        image_menu.append("Crop Canvas…", "win.crop_canvas")
        image_menu.append("DPI…", "win.image_dpi")
        image_menu.append("Color Depth…", "win.image_color_depth")
        settings_menu.append_submenu("Image", image_menu)

        tools_menu = Gio.Menu()
        tools_menu.append("Color Follows Tools", "win.color_follows_tools")
        tools_menu.append("Visible Tools…", "win.visible_tools")
        settings_menu.append_submenu("Tools", tools_menu)

        self._shortcuts_menu = Gio.Menu()
        settings_menu.append_submenu("Shortcuts", self._shortcuts_menu)
        settings_menu.append("Debug Mode", "win.debug_mode")
        settings_btn.set_menu_model(settings_menu)
        menubar.append(settings_btn)
        self._rebuild_shortcuts_submenu()

        menubar.append(build_doc_tabs(self))

        self.reload_btn = Gtk.Button(label="Reload")
        self.reload_btn.set_tooltip_text(f"Reload {current_flavor().display_name} (debug)")
        self.reload_btn.add_css_class("exit-btn")
        self.reload_btn.set_halign(Gtk.Align.END)
        self.reload_btn.set_visible(bool(self.app_settings.debug_mode))
        self.reload_btn.connect("clicked", self.request_reload)
        menubar.append(self.reload_btn)

        exit_btn = Gtk.Button(label="Exit")
        exit_btn.set_tooltip_text(f"Exit {current_flavor().display_name}")
        exit_btn.add_css_class("exit-btn")
        exit_btn.set_halign(Gtk.Align.END)
        exit_btn.connect("clicked", self.request_quit)
        menubar.append(exit_btn)

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

    def action_theme_color(self, *_a) -> None:
        seed = self.app_settings.theme_rgb if self.app_settings.use_custom_theme else default_theme_rgb()
        dlg = ThemeColorDialog(self, seed, on_preview=self._apply_theme_css)
        dlg.connect_response(self._on_theme_color_response)
        dlg.present()

    def _on_theme_color_response(self, dlg: ThemeColorDialog, response: int) -> None:
        if response != Gtk.ResponseType.OK:
            self._apply_theme_css()
            return
        self.app_settings.theme_rgb = dlg.rgb()
        self.app_settings.use_custom_theme = True
        self._persist_settings()
        self._apply_theme_css()

    def action_theme_reset(self, *_a) -> None:
        self.app_settings.use_custom_theme = False
        self.app_settings.theme_rgb = default_theme_rgb()
        self._persist_settings()
        self._apply_theme_css()

    def action_memory_steps(self, *_a) -> None:
        dlg = HistoryStepsDialog(self, self.app_settings.history_steps)
        dlg.connect_response(self._on_memory_steps_response)
        dlg.present()

    def _on_memory_steps_response(self, dlg: HistoryStepsDialog, response: int) -> None:
        if response != Gtk.ResponseType.OK:
            return
        steps = dlg.steps()
        self.app_settings.history_steps = steps
        self.history.set_max_steps(steps)
        for tab in self._tabs:
            tab.history.set_max_steps(steps)
        self._persist_settings()

    def _rebuild_shortcuts_submenu(self) -> None:
        menu = getattr(self, "_shortcuts_menu", None)
        if menu is None:
            return
        menu.remove_all()
        menu.append("Manage Shortcuts…", "win.shortcuts_manage")
        section = Gio.Menu()
        for action in SHORTCUT_ORDER:
            label = SHORTCUT_LABELS.get(action, action)
            keys = format_accels(self.shortcuts.get(action, []))
            item = Gio.MenuItem.new(f"{label}  ·  {keys}", None)
            item.set_action_and_target_value(
                "win.shortcut_rebind", GLib.Variant.new_string(action)
            )
            section.append_item(item)
        menu.append_section(None, section)
        menu.append("Reset to Defaults", "win.shortcuts_reset")

    def _persist_shortcut_overrides(self) -> None:
        overrides: dict[str, list[str]] = {}
        for key, accels in self.shortcuts.items():
            default = DEFAULT_SHORTCUTS.get(key, [])
            if list(accels) != list(default):
                overrides[key] = list(accels)
        self.app_settings.shortcut_overrides = overrides
        self._persist_settings()

    def _apply_shortcuts_map(self, shortcuts: dict[str, list[str]]) -> None:
        self.shortcuts = {k: list(v) for k, v in shortcuts.items()}
        for key in DEFAULT_SHORTCUTS:
            self.shortcuts.setdefault(key, list(DEFAULT_SHORTCUTS[key]))
        self._install_shortcuts()
        self._persist_shortcut_overrides()
        self._refresh_tool_shortcut_tips()

    def action_crop_canvas(self, *_a) -> None:
        if not self.document:
            return
        doc = self.document
        dlg = CropDialog(
            self,
            doc.width,
            doc.height,
            selection_bounds=doc.selection_bounds(),
        )
        dlg.connect_response(self._on_crop_canvas_response)
        dlg.present()

    def _on_crop_canvas_response(self, dlg: CropDialog, response: int) -> None:
        if response != Gtk.ResponseType.OK or not self.document:
            return
        x, y, w, h = dlg.rect()
        if w == self.document.width and h == self.document.height and x == 0 and y == 0:
            return
        self._push_history()
        self.document.crop(x, y, w, h)
        self.canvas.renderer.invalidate()
        self.canvas.request_fit()
        self.canvas.queue_render()
        self._set_status()

    def action_image_dpi(self, *_a) -> None:
        current = self.document.dpi if self.document else self.app_settings.default_dpi
        dlg = DpiDialog(self, current)
        dlg.connect_response(self._on_image_dpi_response)
        dlg.present()

    def _on_image_dpi_response(self, dlg: DpiDialog, response: int) -> None:
        if response != Gtk.ResponseType.OK:
            return
        dpi = dlg.dpi()
        self.app_settings.default_dpi = dpi
        if self.document:
            self.document.dpi = dpi
            self.document.dirty = True
        self._persist_settings()
        self._set_status()

    def action_image_color_depth(self, *_a) -> None:
        current = (
            self.document.color_depth
            if self.document
            else self.app_settings.default_color_depth
        )
        dlg = ColorDepthDialog(self, current)
        dlg.connect_response(self._on_image_color_depth_response)
        dlg.present()

    def _on_image_color_depth_response(self, dlg: ColorDepthDialog, response: int) -> None:
        if response != Gtk.ResponseType.OK:
            return
        depth = dlg.color_depth()
        self.app_settings.default_color_depth = depth
        if self.document:
            self._push_history()
            self.document.set_color_depth(depth)
            self.canvas.renderer.invalidate()
            self.canvas.queue_render()
        self._persist_settings()
        self._set_status()

    def action_shortcuts_manage(self, *_a) -> None:
        dlg = ShortcutsDialog(self, self.shortcuts)
        dlg.connect_response(self._on_shortcuts_manage_response)
        dlg.present()

    def _on_shortcuts_manage_response(self, dlg: ShortcutsDialog, response: int) -> None:
        if response != Gtk.ResponseType.OK:
            return
        self._apply_shortcuts_map(dlg.result_shortcuts())

    def action_shortcuts_reset(self, *_a) -> None:
        self._apply_shortcuts_map({k: list(v) for k, v in DEFAULT_SHORTCUTS.items()})

    def _on_shortcut_rebind_action(self, _action, param: GLib.Variant) -> None:
        action_name = param.get_string() if param is not None else ""
        if not action_name:
            return
        label = SHORTCUT_LABELS.get(action_name, action_name)
        dlg = CaptureShortcutDialog(self, action_name, label)
        dlg.connect_response(self._on_menu_capture_done)
        dlg.present()

    def _on_menu_capture_done(self, dlg: CaptureShortcutDialog, response: int) -> None:
        if response != Gtk.ResponseType.OK:
            return
        accel = dlg.accel()
        if accel is None:
            return
        action = dlg.action
        updated = {k: list(v) for k, v in self.shortcuts.items()}
        if accel == "":
            updated[action] = []
        else:
            for other, accels in updated.items():
                if other != action and accel in accels:
                    updated[other] = [a for a in accels if a != accel]
            updated[action] = [accel]
        self._apply_shortcuts_map(updated)

    # --- startup ---
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

    def action_clear_selection(self, *_a) -> None:
        type_tool = self.tools.get("type")
        if type_tool is not None and getattr(type_tool, "editing", False):
            self._cancel_type_edit_if_any()
            return
        if self.document and self.document.selection.active:
            self._push_history()
            self.document.selection.clear()
            self.canvas.queue_render()
            self._set_status()

    def shutdown_input(self) -> None:
        self.action_stop_animation()
        self.input_hub.close()
