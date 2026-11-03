from __future__ import annotations

import math
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
from inkobold.core.effects import LIQUIFY_BRUSH_MODES
from inkobold.core.assets import asset_root
from inkobold.core import autosave
from inkobold.core import character as ch
from inkobold.core.flavor import current as current_flavor
from inkobold.core.grid import GridOverlay
from inkobold.core.history import History
from inkobold.core.image_meta import to_display_u8
from inkobold.core.libraries import (
    CATEGORIES,
    category_dir,
    ensure_libraries,
    list_brushes,
    list_fonts,
    list_patterns,
    list_system_fonts,
    open_in_file_manager,
)
from inkobold.core.mirror import ARRAY_COUNT_MAX, ARRAY_SPACING_MAX, MODES, ORIENTATIONS, MirrorModifier
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
from inkobold.ui.color_picker import ColorSelectButton, rgba_to_tuple, tuple_to_rgba
from inkobold.ui.palette_panel import PalettePanel
from inkobold.ui.character_ops import CharacterOpsMixin
from inkobold.ui.file_ops import FileOpsMixin
from inkobold.ui.effects_actions import EffectsMixin
from inkobold.ui.sidebar import SidebarMixin, build_sidebar
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
    VisibleToolsDialog,
    configure_numeric_spin,
)
from inkobold.ui.popup_keys import attach_tab_among_editables
from inkobold.ui.theme import DEFAULT_CSS, build_theme_css


TOOL_ORDER = [
    ("pen", "Pen"),
    ("line", "Line"),
    ("rectangle", "Rectangle"),
    ("curve", "Curve"),
    ("lay", "Lay"),
    ("brush", "Brush"),
    ("weld_brush", "Weld Brush"),
    ("fill", "Fill"),
    ("gradient", "Gradient"),
    ("pen3d", "3D Pen"),
    ("fill3d", "3D Fill"),
    ("eraser", "Eraser"),
    ("smear", "Smear"),
    ("liquify", "Liquify"),
    ("replace", "Replace"),
    ("move", "Transform"),
    ("lasso", "Lasso"),
    ("type", "Type"),
]


class MainWindow(SidebarMixin, EffectsMixin, FileOpsMixin, CharacterOpsMixin, DocTabsMixin, Gtk.ApplicationWindow):
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

    def _action_select_tool(self, _action, _param, tid: str) -> None:
        self.select_tool(tid)

    def select_tool(self, tid: str) -> None:
        if tid not in self.tools:
            return
        btn = self.tool_buttons.get(tid)
        if btn is not None:
            btn.set_active(True)
        else:
            self.tool_id = tid
            for t in self.tools.values():
                t.reset()
            self._load_active_tool_settings()
        self._set_status()

    def _active_tool(self):
        return self.tools[self.tool_id]

    def _load_active_tool_settings(self) -> None:
        """Push the active tool's size/color/threshold into the toolbox widgets."""
        tool = self._active_tool()
        self._syncing_tool_ui = True
        try:
            show_replace = bool(getattr(tool, "uses_replace_modes", False))
            self.replace_action_row.set_visible(show_replace)
            self.apply_mode_row.set_visible(show_replace)
            if show_replace:
                action = getattr(tool, "replace_action", "replace")
                self.replace_action_dropdown.set_selected(1 if action == "erase" else 0)
                mode = getattr(tool, "apply_mode", "all")
                self.apply_mode_dropdown.set_selected(
                    {"brush": 0, "fill": 1, "all": 2}.get(mode, 2)
                )
            hide_size = show_replace and getattr(tool, "apply_mode", "all") != "brush"
            show_size = bool(getattr(tool, "uses_size", True)) and not hide_size
            self.size_row.set_visible(show_size)
            if show_size:
                self.brush_spin.set_value(tool.brush_size)
            if self.app_settings.color_follows_tools:
                tool.color = self._shared_color
                tool.secondary_color = self._shared_secondary
            self._sync_color_buttons()
            show_thr = bool(getattr(tool, "uses_threshold", False))
            self.threshold_row.set_visible(show_thr)
            if show_thr:
                self.threshold_spin.set_value(tool.threshold)
            show_3d = bool(getattr(tool, "uses_3d_settings", False))
            self.shade3d_row.set_visible(show_3d)
            show_fill3d_type = show_3d and bool(getattr(tool, "uses_fill3d_types", False))
            self.fill3d_type_row.set_visible(show_fill3d_type)
            if show_3d:
                self.depth_spin.set_value(getattr(tool, "depth", 70))
                self.highlight_spin.set_value(getattr(tool, "highlight", 55))
                self.bevel_spin.set_value(getattr(tool, "bevel", 40))
            if show_fill3d_type:
                ftype = getattr(tool, "fill3d_type", "classic")
                self.fill3d_type_dropdown.set_selected(
                    {"classic": 0, "addiction": 1, "wavy": 2, "drift": 3, "depression": 4}.get(ftype, 0)
                )
            show_freq = bool(getattr(tool, "uses_frequency", False))
            self.frequency_row.set_visible(show_freq)
            if show_freq:
                self.frequency_spin.set_value(float(getattr(tool, "frequency", 60)))
            show_fill = bool(getattr(tool, "uses_fill_options", False))
            self.fill_opts_row.set_visible(show_fill)
            if show_fill:
                mode = getattr(tool, "fill_mode", "color")
                self.fill_mode_dropdown.set_selected(
                    {"color": 0, "pattern": 1, "maze": 2, "puzzle": 3}.get(mode, 0)
                )
                self.tile_scale_spin.set_value(float(getattr(tool, "tile_scale", 1.0)))
                self.maze_cell_spin.set_value(float(getattr(tool, "maze_cell_size", 8.0)))
            show_lay = bool(getattr(tool, "uses_lay_options", False))
            self.lay_opts_row.set_visible(show_lay)
            if show_lay:
                lmode = getattr(tool, "lay_mode", "line")
                self.lay_mode_dropdown.set_selected(
                    {"line": 0, "freehand": 1, "arc": 2, "circle": 3}.get(lmode, 0)
                )
                self.lay_spacing_spin.set_value(float(getattr(tool, "lay_spacing", 40.0)))
                self.arc_degrees_spin.set_value(float(getattr(tool, "arc_degrees", 180)))
            if show_fill or show_lay:
                self._refresh_pattern_dropdown(select_path=getattr(tool, "pattern_path", None))
            self._update_fill_mode_visibility()
            self._update_lay_mode_visibility()
            if show_lay:
                self.size_label.set_label("Element Size")
            show_brush_opts = bool(getattr(tool, "uses_brush_options", False))
            self.brush_opts_row.set_visible(show_brush_opts)
            if show_brush_opts:
                bmode = getattr(tool, "brush_mode", "round")
                self.brush_mode_dropdown.set_selected(
                    {"round": 0, "custom": 1, "bubbles": 2}.get(bmode, 0)
                )
                self.brush_density_spin.set_value(float(getattr(tool, "density", 50)))
                self._refresh_brush_dropdown(select_path=getattr(tool, "brush_path", None))
            self._update_brush_mode_visibility()
            show_intensity = bool(getattr(tool, "uses_intensity", False))
            self.intensity_row.set_visible(show_intensity)
            if show_intensity:
                is_weld = getattr(tool, "id", "") == "weld_brush"
                self.intensity_label.set_label("Weld" if is_weld else "Intensity")
                # Weld goes to 300 (larger junction fillets); smear/liquify stay 0–100.
                self.intensity_spin.set_range(0, 300 if is_weld else 100)
                self.intensity_spin.set_value(float(getattr(tool, "intensity", 50)))
                self.intensity_spin.set_tooltip_text(
                    "Junction fillet size. 0 = sharp corners, 100 ≈ brush width, "
                    "300 = very large weld."
                    if is_weld
                    else "Smear / liquify strength. 0 = none, 100 = strong. "
                    "Pen pressure fine-tunes within this."
                )
            show_liquify = bool(getattr(tool, "uses_liquify_modes", False))
            self.liquify_mode_row.set_visible(show_liquify)
            if show_liquify:
                mode = str(getattr(tool, "liquify_mode", "Push")).strip().title()
                try:
                    idx = list(LIQUIFY_BRUSH_MODES).index(mode)
                except ValueError:
                    idx = 0
                self.liquify_mode_dropdown.set_selected(idx)
            show_opacity = bool(getattr(tool, "uses_opacity", False))
            self.opacity_row.set_visible(show_opacity)
            if show_opacity:
                self.opacity_spin.set_value(float(getattr(tool, "opacity", 100)))
            show_curve = bool(getattr(tool, "uses_curve_modes", False))
            self.curve_mode_row.set_visible(show_curve)
            if show_curve:
                mode = getattr(tool, "curve_mode", "freehand")
                self.curve_mode_dropdown.set_selected(
                    {"freehand": 0, "arc": 1, "circle": 2}.get(mode, 0)
                )
                self.arc_degrees_spin.set_value(float(getattr(tool, "arc_degrees", 180)))
            self._update_curve_mode_visibility()
            show_eraser = bool(getattr(tool, "uses_eraser_modes", False))
            self.eraser_mode_row.set_visible(show_eraser)
            if show_eraser:
                emode = getattr(tool, "eraser_mode", "freehand")
                self.eraser_mode_dropdown.set_selected(
                    {"freehand": 0, "line": 1}.get(emode, 0)
                )
            show_type = bool(getattr(tool, "uses_type_options", False))
            self.type_opts_row.set_visible(show_type)
            if show_type:
                self.size_label.set_label("Font Size")
                src = getattr(tool, "font_source", "system")
                self.font_source_dropdown.set_selected(1 if src == "library" else 0)
                self._refresh_font_dropdown(select_path=getattr(tool, "font_path", None))
            elif not bool(getattr(tool, "uses_lay_options", False)):
                self.size_label.set_label("Size")
        finally:
            self._syncing_tool_ui = False

    def _update_curve_mode_visibility(self) -> None:
        tool = self._active_tool()
        show_arc = (
            bool(getattr(tool, "uses_curve_modes", False))
            and getattr(tool, "curve_mode", "freehand") == "arc"
        ) or (
            bool(getattr(tool, "uses_lay_options", False))
            and getattr(tool, "lay_mode", "line") == "arc"
        )
        self.arc_degrees_row.set_visible(show_arc)

    def _update_lay_mode_visibility(self) -> None:
        self._update_curve_mode_visibility()

    def _tool_uses_pattern_picker(self, tool=None) -> bool:
        tool = tool if tool is not None else self._active_tool()
        if getattr(tool, "uses_lay_options", False):
            return True
        return (
            bool(getattr(tool, "uses_fill_options", False))
            and getattr(tool, "fill_mode", "color") == "pattern"
        )

    def _update_fill_mode_visibility(self) -> None:
        tool = self._active_tool()
        uses_fill = bool(getattr(tool, "uses_fill_options", False))
        uses_lay = bool(getattr(tool, "uses_lay_options", False))
        mode = getattr(tool, "fill_mode", "color") if uses_fill else "color"
        pattern_mode = mode == "pattern"
        maze_mode = mode == "maze"
        puzzle_mode = mode == "puzzle"
        procedural = maze_mode or puzzle_mode
        show_color = bool(getattr(tool, "uses_color", True)) and not pattern_mode and not uses_lay
        self.color_pair_row.set_visible(show_color)
        self.palette_panel.set_visible(show_color)
        if maze_mode:
            self.maze_cell_label.set_label("Cell Size")
            self.maze_cell_spin.set_tooltip_text("Maze cell size in pixels (walls + corridor)")
        elif puzzle_mode:
            self.maze_cell_label.set_label("Piece Size")
            self.maze_cell_spin.set_tooltip_text("Approximate jigsaw piece size in pixels")
        self._update_color_labels()
        self.pattern_row.set_visible(self._tool_uses_pattern_picker(tool))
        self.tile_scale_row.set_visible(uses_fill and pattern_mode)
        self.maze_cell_row.set_visible(uses_fill and procedural)
        self.lay_spacing_row.set_visible(uses_lay)

    def _update_brush_mode_visibility(self) -> None:
        tool = self._active_tool()
        uses_brush = bool(getattr(tool, "uses_brush_options", False))
        bmode = getattr(tool, "brush_mode", "round") if uses_brush else "round"
        self.brush_image_row.set_visible(uses_brush and bmode == "custom")
        self.brush_density_row.set_visible(uses_brush and bmode == "bubbles")

    def _pattern_texture(self, path: Path, size: int) -> Optional[Gdk.Texture]:
        try:
            pix = GdkPixbuf.Pixbuf.new_from_file_at_size(str(path), size, size)
            return Gdk.Texture.new_for_pixbuf(pix)
        except Exception:
            return None

    def _set_pattern_preview(self, path: Optional[Path]) -> None:
        if path is None:
            self.pattern_preview.set_paintable(None)
            self.pattern_menu_btn.set_tooltip_text("No patterns in Libraries → Patterns")
            return
        tex = self._pattern_texture(path, self._pattern_preview_size)
        self.pattern_preview.set_paintable(tex)
        self.pattern_menu_btn.set_tooltip_text(path.name)

    def _clear_pattern_flow(self) -> None:
        child = self.pattern_flow.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.pattern_flow.remove(child)
            child = nxt

    def _refresh_pattern_dropdown(self, select_path: Optional[Path] = None) -> None:
        paths = list_patterns()
        self._pattern_paths = paths
        self._clear_pattern_flow()
        self.pattern_menu_btn.set_sensitive(bool(paths))
        if not paths:
            self._set_pattern_preview(None)
            tool = self._active_tool()
            if self._tool_uses_pattern_picker(tool):
                tool.set_pattern_path(None)
            empty = Gtk.Label(label="(no patterns)", xalign=0.5)
            empty.add_css_class("dim-label")
            self.pattern_flow.append(empty)
            return
        idx = 0
        if select_path is not None:
            try:
                idx = next(i for i, p in enumerate(paths) if p.resolve() == Path(select_path).resolve())
            except StopIteration:
                idx = 0
        group: Optional[Gtk.ToggleButton] = None
        prev_sync = self._syncing_tool_ui
        self._syncing_tool_ui = True
        try:
            for i, path in enumerate(paths):
                btn = Gtk.ToggleButton()
                btn.add_css_class("pattern-thumb")
                if group is None:
                    group = btn
                else:
                    btn.set_group(group)
                pic = Gtk.Picture()
                pic.set_size_request(self._pattern_thumb_size, self._pattern_thumb_size)
                pic.set_content_fit(Gtk.ContentFit.COVER)
                tex = self._pattern_texture(path, self._pattern_thumb_size)
                if tex is not None:
                    pic.set_paintable(tex)
                btn.set_child(pic)
                btn.set_tooltip_text(path.name)
                btn.connect("toggled", self._on_pattern_toggled, i)
                self.pattern_flow.append(btn)
                if i == idx:
                    btn.set_active(True)
        finally:
            self._syncing_tool_ui = prev_sync
        self._set_pattern_preview(paths[idx])
        tool = self._active_tool()
        if self._tool_uses_pattern_picker(tool):
            tool.set_pattern_path(paths[idx])

    def _set_brush_preview(self, path: Optional[Path]) -> None:
        if path is None:
            self.brush_preview.set_paintable(None)
            self.brush_menu_btn.set_tooltip_text("No brushes in Libraries → Brushes")
            return
        tex = self._pattern_texture(path, self._brush_preview_size)
        self.brush_preview.set_paintable(tex)
        self.brush_menu_btn.set_tooltip_text(path.name)

    def _clear_brush_flow(self) -> None:
        child = self.brush_flow.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.brush_flow.remove(child)
            child = nxt

    def _refresh_brush_dropdown(self, select_path: Optional[Path] = None) -> None:
        paths = list_brushes()
        self._brush_paths = paths
        self._clear_brush_flow()
        self.brush_menu_btn.set_sensitive(bool(paths))
        if not paths:
            self._set_brush_preview(None)
            tool = self._active_tool()
            if getattr(tool, "uses_brush_options", False):
                tool.set_brush_path(None)
            empty = Gtk.Label(label="(no brushes)", xalign=0.5)
            empty.add_css_class("dim-label")
            self.brush_flow.append(empty)
            return
        idx = 0
        if select_path is not None:
            try:
                idx = next(i for i, p in enumerate(paths) if p.resolve() == Path(select_path).resolve())
            except StopIteration:
                idx = 0
        group: Optional[Gtk.ToggleButton] = None
        prev_sync = self._syncing_tool_ui
        self._syncing_tool_ui = True
        try:
            for i, path in enumerate(paths):
                btn = Gtk.ToggleButton()
                btn.add_css_class("pattern-thumb")
                if group is None:
                    group = btn
                else:
                    btn.set_group(group)
                pic = Gtk.Picture()
                pic.set_size_request(self._brush_thumb_size, self._brush_thumb_size)
                pic.set_content_fit(Gtk.ContentFit.COVER)
                tex = self._pattern_texture(path, self._brush_thumb_size)
                if tex is not None:
                    pic.set_paintable(tex)
                btn.set_child(pic)
                btn.set_tooltip_text(path.name)
                btn.connect("toggled", self._on_brush_toggled, i)
                self.brush_flow.append(btn)
                if i == idx:
                    btn.set_active(True)
        finally:
            self._syncing_tool_ui = prev_sync
        self._set_brush_preview(paths[idx])
        tool = self._active_tool()
        if getattr(tool, "uses_brush_options", False):
            tool.set_brush_path(paths[idx])

    def _on_fill_mode_changed(self, dropdown: Gtk.DropDown, *_a) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if not getattr(tool, "uses_fill_options", False):
            return
        tool.fill_mode = {0: "color", 1: "pattern", 2: "maze", 3: "puzzle"}.get(
            int(dropdown.get_selected()), "color"
        )
        self._update_fill_mode_visibility()

    def _on_brush_mode_changed(self, dropdown: Gtk.DropDown, *_a) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if not getattr(tool, "uses_brush_options", False):
            return
        tool.brush_mode = {0: "round", 1: "custom", 2: "bubbles"}.get(
            int(dropdown.get_selected()), "round"
        )
        self._update_brush_mode_visibility()
        if tool.brush_mode == "custom":
            self._refresh_brush_dropdown(select_path=getattr(tool, "brush_path", None))

    def _on_brush_density_changed(self, spin: Gtk.SpinButton) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if getattr(tool, "uses_brush_options", False):
            tool.density = float(spin.get_value())

    def _on_replace_action_changed(self, dropdown: Gtk.DropDown, *_a) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if not getattr(tool, "uses_replace_modes", False):
            return
        tool.replace_action = "erase" if dropdown.get_selected() == 1 else "replace"
        self._update_color_labels()

    def _on_apply_mode_changed(self, dropdown: Gtk.DropDown, *_a) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if not getattr(tool, "uses_replace_modes", False):
            return
        tool.apply_mode = {0: "brush", 1: "fill", 2: "all"}.get(int(dropdown.get_selected()), "all")
        hide_size = tool.apply_mode != "brush"
        show_size = bool(getattr(tool, "uses_size", True)) and not hide_size
        self.size_row.set_visible(show_size)

    def _on_pattern_toggled(self, btn: Gtk.ToggleButton, idx: int) -> None:
        if self._syncing_tool_ui or not btn.get_active():
            return
        tool = self._active_tool()
        if not self._tool_uses_pattern_picker(tool):
            return
        paths = getattr(self, "_pattern_paths", [])
        if 0 <= idx < len(paths):
            tool.set_pattern_path(paths[idx])
            self._set_pattern_preview(paths[idx])
        else:
            tool.set_pattern_path(None)
            self._set_pattern_preview(None)
        popover = self.pattern_menu_btn.get_popover()
        if popover is not None:
            popover.popdown()

    def _on_brush_toggled(self, btn: Gtk.ToggleButton, idx: int) -> None:
        if self._syncing_tool_ui or not btn.get_active():
            return
        tool = self._active_tool()
        if not getattr(tool, "uses_brush_options", False):
            return
        paths = getattr(self, "_brush_paths", [])
        if 0 <= idx < len(paths):
            tool.set_brush_path(paths[idx])
            self._set_brush_preview(paths[idx])
        else:
            tool.set_brush_path(None)
            self._set_brush_preview(None)
        popover = self.brush_menu_btn.get_popover()
        if popover is not None:
            popover.popdown()

    def _on_tile_scale_changed(self, spin: Gtk.SpinButton) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if getattr(tool, "uses_fill_options", False):
            tool.tile_scale = float(spin.get_value())

    def _on_maze_cell_changed(self, spin: Gtk.SpinButton) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if getattr(tool, "uses_fill_options", False):
            tool.maze_cell_size = float(spin.get_value())

    def _refresh_font_dropdown(
        self,
        select_path: Optional[Path] = None,
        *,
        refresh_system: bool = False,
    ) -> None:
        tool = self._active_tool()
        source = getattr(tool, "font_source", "system") if getattr(tool, "uses_type_options", False) else "system"
        paths: list[Path] = []
        labels: list[str] = []
        if source == "library":
            paths = list_fonts()
            root = category_dir("fonts")
            labels = []
            for p in paths:
                try:
                    labels.append(str(p.relative_to(root)))
                except ValueError:
                    labels.append(p.name)
        else:
            fonts = list_system_fonts(refresh=refresh_system)
            paths = [f.path for f in fonts]
            labels = [f.label for f in fonts]
        self._font_paths = paths
        strings = Gtk.StringList.new(labels if labels else ["(no fonts)"])
        self.font_dropdown.set_model(strings)
        self.font_dropdown.set_sensitive(bool(paths))
        if not paths:
            if getattr(tool, "uses_type_options", False):
                tool.set_font_path(None)
                self._refresh_type_preview()
            return
        idx = 0
        if select_path is not None:
            try:
                idx = next(i for i, p in enumerate(paths) if p.resolve() == Path(select_path).resolve())
            except (StopIteration, OSError):
                idx = 0
        self.font_dropdown.set_selected(idx)
        if getattr(tool, "uses_type_options", False):
            tool.set_font_path(paths[idx])
            self._refresh_type_preview()

    def _on_font_source_changed(self, dropdown: Gtk.DropDown, *_a) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if not getattr(tool, "uses_type_options", False):
            return
        tool.set_font_source("library" if dropdown.get_selected() == 1 else "system")
        self._refresh_font_dropdown(select_path=getattr(tool, "font_path", None))

    def _on_font_changed(self, dropdown: Gtk.DropDown, *_a) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if not getattr(tool, "uses_type_options", False):
            return
        paths = getattr(self, "_font_paths", [])
        idx = int(dropdown.get_selected())
        if 0 <= idx < len(paths):
            tool.set_font_path(paths[idx])
        else:
            tool.set_font_path(None)
        self._refresh_type_preview()

    def _refresh_type_preview(self) -> None:
        canvas = getattr(self, "canvas", None)
        if canvas is not None and hasattr(canvas, "refresh_type_preview"):
            canvas.refresh_type_preview()

    def _on_type_editing_changed(self, active: bool) -> None:
        self._refresh_typing_shortcut_suspend()
        if hasattr(self, "canvas"):
            if active:
                self.canvas._catcher.grab_focus()
            # Text creates/removes a layer — keep the sidebar in sync.
            self._rebuild_layers()
            self.canvas.queue_render()
            self.canvas.refresh_guides()
            self._set_status()

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

    def _on_intensity_changed(self, spin: Gtk.SpinButton) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if getattr(tool, "uses_intensity", False):
            tool.intensity = float(spin.get_value())

    def _on_liquify_mode_changed(self, dropdown: Gtk.DropDown, *_a) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if not getattr(tool, "uses_liquify_modes", False):
            return
        idx = min(int(dropdown.get_selected()), len(LIQUIFY_BRUSH_MODES) - 1)
        tool.liquify_mode = LIQUIFY_BRUSH_MODES[max(0, idx)]

    def _on_opacity_changed(self, spin: Gtk.SpinButton) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if getattr(tool, "uses_opacity", False):
            tool.opacity = float(spin.get_value())
            self._refresh_type_preview()

    def _on_curve_mode_changed(self, dropdown: Gtk.DropDown, *_a) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if not getattr(tool, "uses_curve_modes", False):
            return
        tool.curve_mode = ("freehand", "arc", "circle")[min(int(dropdown.get_selected()), 2)]
        self._update_curve_mode_visibility()

    def _on_eraser_mode_changed(self, dropdown: Gtk.DropDown, *_a) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if not getattr(tool, "uses_eraser_modes", False):
            return
        tool.eraser_mode = ("freehand", "line")[min(int(dropdown.get_selected()), 1)]

    def _on_arc_degrees_changed(self, spin: Gtk.SpinButton) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if getattr(tool, "uses_curve_modes", False) or getattr(tool, "uses_lay_options", False):
            tool.arc_degrees = float(spin.get_value())

    def _on_lay_mode_changed(self, dropdown: Gtk.DropDown, *_a) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if not getattr(tool, "uses_lay_options", False):
            return
        tool.lay_mode = ("line", "freehand", "arc", "circle")[
            min(int(dropdown.get_selected()), 3)
        ]
        self._update_lay_mode_visibility()

    def _on_lay_spacing_changed(self, spin: Gtk.SpinButton) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if getattr(tool, "uses_lay_options", False):
            tool.lay_spacing = float(spin.get_value())

    def _on_brush_changed(self, spin: Gtk.SpinButton) -> None:
        if self._syncing_tool_ui:
            return
        self._active_tool().brush_size = float(spin.get_value())
        self._refresh_type_preview()

    def _on_threshold_changed(self, spin: Gtk.SpinButton) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if getattr(tool, "uses_threshold", False):
            tool.threshold = int(spin.get_value())

    def _on_3d_setting_changed(self, _spin: Gtk.SpinButton) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if not getattr(tool, "uses_3d_settings", False):
            return
        tool.depth = float(self.depth_spin.get_value())
        tool.highlight = float(self.highlight_spin.get_value())
        tool.bevel = float(self.bevel_spin.get_value())

    def _on_fill3d_type_changed(self, dropdown: Gtk.DropDown, *_a) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if not getattr(tool, "uses_fill3d_types", False):
            return
        tool.fill3d_type = ("classic", "addiction", "wavy", "drift", "depression")[
            min(dropdown.get_selected(), 4)
        ]

    def _on_frequency_changed(self, spin: Gtk.SpinButton) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if getattr(tool, "uses_frequency", False):
            tool.frequency = float(spin.get_value())

    def _on_mirror_toggled(self, btn: Gtk.CheckButton) -> None:
        self.mirror.enabled = bool(btn.get_active())
        self.mirror_opts.set_visible(self.mirror.enabled)
        if hasattr(self, "canvas"):
            self.canvas.refresh_guides()

    def _update_mirror_mode_ui(self) -> None:
        """Swap Axes/Copies label and tooltips for the active symmetry mode."""
        if not hasattr(self, "mirror_axes_label"):
            return
        radial = self.mirror.mode == "radial"
        linear = self.mirror.mode == "linear"
        # Linear Array has its own Copies / Spacing / Direction block.
        for widget in (
            getattr(self, "mirror_orient_label", None),
            getattr(self, "mirror_orient_dropdown", None),
            self.mirror_axes_label,
            getattr(self, "mirror_axes_spin", None),
        ):
            if widget is not None:
                widget.set_visible(not linear)
        if hasattr(self, "mirror_array_opts"):
            self.mirror_array_opts.set_visible(linear)
        self.mirror_axes_label.set_text("Copies" if radial else "Axes")
        if hasattr(self, "mirror_axes_spin"):
            if radial:
                self.mirror_axes_spin.set_tooltip_text(
                    "Same-facing rotational copies around the center. "
                    "2 = opposite pair; 3+ = pinwheel."
                )
            else:
                self.mirror_axes_spin.set_tooltip_text(
                    "Equally spaced mirror axes through the center. "
                    "1 = single axis; 2 = four-way (perpendicular); 3+ = kaleidoscope."
                )
        if hasattr(self, "mirror_orient_dropdown"):
            if radial:
                self.mirror_orient_dropdown.set_tooltip_text(
                    "Phases the guide rays only. Does not flip stroke direction."
                )
            else:
                self.mirror_orient_dropdown.set_tooltip_text(
                    "Rotates the axis set. Horizontal: left↔right first. "
                    "Vertical: top↔bottom first. Diagonal: 45°."
                )

    def _on_mirror_mode_changed(self, dropdown: Gtk.DropDown, *_a) -> None:
        idx = int(dropdown.get_selected())
        if 0 <= idx < len(MODES):
            self.mirror.mode = MODES[idx][0]
        self.mirror.clamp()
        self._update_mirror_mode_ui()
        if hasattr(self, "canvas"):
            self.canvas.refresh_guides()

    def _on_mirror_orient_changed(self, dropdown: Gtk.DropDown, *_a) -> None:
        idx = int(dropdown.get_selected())
        if 0 <= idx < len(ORIENTATIONS):
            self.mirror.apply_orientation_preset(ORIENTATIONS[idx][0])
        if hasattr(self, "canvas"):
            self.canvas.refresh_guides()

    def _on_mirror_axes_changed(self, spin: Gtk.SpinButton) -> None:
        self.mirror.axes = int(spin.get_value())
        self.mirror.clamp()
        if hasattr(self, "canvas"):
            self.canvas.refresh_guides()

    def _sync_mirror_array_ui(self) -> None:
        """Push Linear Array values into the panel (after a canvas drag or load)."""
        if not hasattr(self, "mirror_array_count_spin"):
            return
        self._syncing_mirror_ui = True
        try:
            self.mirror_array_count_spin.set_value(self.mirror.array_count)
            self.mirror_array_spacing_spin.set_value(self.mirror.array_spacing)
            # Panel shows math-style degrees: 0° = right, 90° = up (screen Y is down).
            deg = -math.degrees(self.mirror.array_direction)
            deg = (deg + 180.0) % 360.0 - 180.0
            self.mirror_array_direction_spin.set_value(deg)
        finally:
            self._syncing_mirror_ui = False

    def _on_mirror_array_changed(self, *_a) -> None:
        if getattr(self, "_syncing_mirror_ui", False):
            return
        self.mirror.array_count = int(self.mirror_array_count_spin.get_value())
        self.mirror.array_spacing = float(self.mirror_array_spacing_spin.get_value())
        self.mirror.array_direction = -math.radians(self.mirror_array_direction_spin.get_value())
        self.mirror.clamp()
        if hasattr(self, "canvas"):
            self.canvas.refresh_guides()

    def _on_mirror_reset(self, *_a) -> None:
        """Reset hub to center and angle to Horizontal (all modes)."""
        self.mirror.reset_guides()
        self._sync_mirror_array_ui()
        if hasattr(self, "mirror_orient_dropdown"):
            idx = next(
                (i for i, (k, _) in enumerate(ORIENTATIONS) if k == self.mirror.orientation),
                0,
            )
            self.mirror_orient_dropdown.set_selected(idx)
        if hasattr(self, "canvas"):
            self.canvas.refresh_guides()

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

    def _on_tile_wrap_toggled(self, btn: Gtk.CheckButton) -> None:
        enabled = bool(btn.get_active())
        self.tile_wrap = enabled
        action = self.lookup_action("tile_wrap")
        if action is not None:
            current = bool(action.get_state().get_boolean())  # type: ignore[union-attr]
            if current != enabled:
                action.change_state(GLib.Variant.new_boolean(enabled))

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

    def _on_pixel_mode_toggled(self, btn: Gtk.CheckButton) -> None:
        enabled = bool(btn.get_active())
        action = self.lookup_action("pixel_mode")
        if action is not None:
            current = bool(action.get_state().get_boolean())  # type: ignore[union-attr]
            if current != enabled:
                action.change_state(GLib.Variant.new_boolean(enabled))

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

    def _take_shared_colors_from_active_tool(self) -> None:
        tool = self._active_tool()
        self._shared_color = tuple(int(v) for v in tool.color)
        self._shared_secondary = tuple(int(v) for v in tool.secondary_color)

    def _apply_shared_color_to_tools(self) -> None:
        for tool in self.tools.values():
            if getattr(tool, "uses_color", True):
                tool.color = self._shared_color
                tool.secondary_color = self._shared_secondary

    @staticmethod
    def _default_visible_tools() -> list[str]:
        """Toolbox when the user has not picked tools: everything the flavor does not hide."""
        hidden = set(current_flavor().hidden_tools)
        return [tid for tid, *_ in TOOL_ORDER if tid not in hidden]

    def _resolved_visible_tools(self) -> list[str]:
        chosen = [tid for tid in self.app_settings.visible_tools if tid in self.tools]
        if not chosen:
            return self._default_visible_tools()
        return chosen

    def _apply_tool_visibility(self) -> None:
        if not getattr(self, "tool_buttons", None):
            return
        visible = set(self._resolved_visible_tools())
        for tid, btn in self.tool_buttons.items():
            show = tid in visible
            btn.set_visible(show)
            # FlowBox wraps each button in a FlowBoxChild — hide that too
            parent = btn.get_parent()
            if parent is not None:
                parent.set_visible(show)
        # If the active tool is hidden, switch to the first visible tool
        if self.tool_id not in visible:
            for tid, *_ in TOOL_ORDER:
                if tid in visible:
                    self.select_tool(tid)
                    break

    def action_visible_tools(self, *_a) -> None:
        tools = [(tid, label) for tid, label, *_ in TOOL_ORDER]
        dlg = VisibleToolsDialog(self, tools, self._resolved_visible_tools())
        dlg.connect_response(self._on_visible_tools_response)
        dlg.present()

    def _on_visible_tools_response(self, dlg: VisibleToolsDialog, response: int) -> None:
        if response != Gtk.ResponseType.OK:
            return
        chosen = dlg.visible_ids()
        # Store empty when it matches the default so new tools appear by default
        if set(chosen) == set(self._default_visible_tools()):
            self.app_settings.visible_tools = []
        else:
            self.app_settings.visible_tools = [tid for tid, *_ in TOOL_ORDER if tid in chosen]
        self._persist_settings()
        self._apply_tool_visibility()

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

    def action_brush_smaller(self, *_a) -> None:
        self.brush_spin.set_value(max(0.1, self.brush_spin.get_value() - 1))

    def action_brush_larger(self, *_a) -> None:
        self.brush_spin.set_value(min(256, self.brush_spin.get_value() + 1))

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

        toolbox_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["toolbox"])
        toolbox_page.set_size_request(self._side_panel_w, -1)

        tool_flow = Gtk.FlowBox()
        tool_flow.set_valign(Gtk.Align.START)
        tool_flow.set_max_children_per_line(12)
        tool_flow.set_min_children_per_line(1)
        tool_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        tool_flow.set_homogeneous(True)
        tool_flow.set_column_spacing(2)
        tool_flow.set_row_spacing(2)
        tool_flow.set_hexpand(True)
        toolbox_page.append(tool_flow)

        self.tool_buttons: dict[str, Gtk.ToggleButton] = {}
        group: Optional[Gtk.ToggleButton] = None
        for tid, label in TOOL_ORDER:
            btn = Gtk.ToggleButton(label=label)
            btn.add_css_class("tool-btn")
            btn.set_tooltip_text(label)
            btn.set_hexpand(True)
            if group is None:
                group = btn
                btn.set_active(True)
            else:
                btn.set_group(group)
            btn.connect("toggled", self._on_tool_toggled, tid)
            self.tool_buttons[tid] = btn
            tool_flow.append(btn)

        self._refresh_tool_shortcut_tips()
        self._apply_tool_visibility()

        toolbox_page.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL, margin_top=4, margin_bottom=4))

        opts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        toolbox_page.append(opts)
        self.size_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        opts.append(self.size_row)
        self.size_label = Gtk.Label(label="Size", xalign=0)
        self.size_row.append(self.size_label)
        self.brush_spin = Gtk.SpinButton.new_with_range(0.1, 256, 1)
        configure_numeric_spin(self.brush_spin)
        self.brush_spin.set_hexpand(True)
        self.brush_spin.connect("value-changed", self._on_brush_changed)
        self.size_row.append(self.brush_spin)

        self.opacity_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        opts.append(self.opacity_row)
        self.opacity_row.append(Gtk.Label(label="Opacity", xalign=0))
        self.opacity_spin = Gtk.SpinButton.new_with_range(0, 100, 1)
        configure_numeric_spin(self.opacity_spin)
        self.opacity_spin.set_value(100)
        self.opacity_spin.set_hexpand(True)
        self.opacity_spin.set_tooltip_text(
            "Stroke / fill / erase strength. Combines with the color's alpha when painting."
        )
        self.opacity_spin.connect("value-changed", self._on_opacity_changed)
        self.opacity_row.append(self.opacity_spin)

        self.brush_opts_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        opts.append(self.brush_opts_row)
        self.brush_opts_row.append(Gtk.Label(label="Brush", xalign=0))
        brush_modes = Gtk.StringList.new(["Round", "Custom", "Bubbles"])
        self.brush_mode_dropdown = Gtk.DropDown(model=brush_modes)
        self.brush_mode_dropdown.set_hexpand(True)
        self.brush_mode_dropdown.set_tooltip_text(
            "Round: soft disk. Custom: stamp a tip from Libraries → Brushes (tinted with Color). "
            "Bubbles: milk-style air-bubble clusters tinted with Color."
        )
        self.brush_mode_dropdown.connect("notify::selected", self._on_brush_mode_changed)
        self.brush_opts_row.append(self.brush_mode_dropdown)

        self.brush_image_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=2)
        self.brush_opts_row.append(self.brush_image_row)
        brush_head = Gtk.Box(spacing=4)
        brush_head.append(Gtk.Label(label="Tip", xalign=0, hexpand=True))
        refresh_brush = Gtk.Button(label="↻")
        refresh_brush.set_tooltip_text("Reload brushes from Libraries folder")
        refresh_brush.connect("clicked", lambda *_: self._refresh_brush_dropdown(
            select_path=getattr(self._active_tool(), "brush_path", None)
        ))
        brush_head.append(refresh_brush)
        self.brush_image_row.append(brush_head)
        self._brush_paths: list[Path] = []
        self._brush_thumb_size = 56
        self._brush_preview_size = 40
        self.brush_preview = Gtk.Picture()
        self.brush_preview.set_size_request(self._brush_preview_size, self._brush_preview_size)
        self.brush_preview.set_content_fit(Gtk.ContentFit.COVER)
        self.brush_preview.set_can_shrink(False)
        self.brush_menu_btn = Gtk.MenuButton()
        self.brush_menu_btn.set_hexpand(True)
        self.brush_menu_btn.set_always_show_arrow(True)
        self.brush_menu_btn.set_child(self.brush_preview)
        self.brush_menu_btn.set_tooltip_text("Choose a brush tip")
        brush_popover = Gtk.Popover()
        brush_scroll = Gtk.ScrolledWindow()
        brush_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        brush_scroll.set_min_content_height(168)
        brush_scroll.set_max_content_height(280)
        brush_scroll.set_min_content_width(200)
        self.brush_flow = Gtk.FlowBox()
        self.brush_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.brush_flow.set_homogeneous(True)
        self.brush_flow.set_max_children_per_line(4)
        self.brush_flow.set_min_children_per_line(3)
        self.brush_flow.set_column_spacing(4)
        self.brush_flow.set_row_spacing(4)
        self.brush_flow.set_valign(Gtk.Align.START)
        brush_scroll.set_child(self.brush_flow)
        brush_popover.set_child(brush_scroll)
        self.brush_menu_btn.set_popover(brush_popover)
        self.brush_image_row.append(self.brush_menu_btn)

        self.brush_density_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=2)
        self.brush_opts_row.append(self.brush_density_row)
        self.brush_density_row.append(Gtk.Label(label="Density", xalign=0))
        self.brush_density_spin = Gtk.SpinButton.new_with_range(0, 100, 1)
        configure_numeric_spin(self.brush_density_spin)
        self.brush_density_spin.set_value(10)
        self.brush_density_spin.set_hexpand(True)
        self.brush_density_spin.set_tooltip_text(
            "Bubble packing along the stroke. Low = sparse solitary bubbles, "
            "high = packed foam clusters."
        )
        self.brush_density_spin.connect("value-changed", self._on_brush_density_changed)
        self.brush_density_row.append(self.brush_density_spin)

        self.curve_mode_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        opts.append(self.curve_mode_row)
        self.curve_mode_row.append(Gtk.Label(label="Mode", xalign=0))
        curve_modes = Gtk.StringList.new(["Freehand", "Arc", "Circle"])
        self.curve_mode_dropdown = Gtk.DropDown(model=curve_modes)
        self.curve_mode_dropdown.set_hexpand(True)
        self.curve_mode_dropdown.set_tooltip_text(
            "Freehand: fit a curve to your drag. "
            "Arc: circular arc; set Degrees for the central angle "
            "(180° = semicircle on the drag diameter). "
            "Circle: press center, drag radius (Alt = drag as diameter)."
        )
        self.curve_mode_dropdown.connect("notify::selected", self._on_curve_mode_changed)
        self.curve_mode_row.append(self.curve_mode_dropdown)

        self.eraser_mode_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        opts.append(self.eraser_mode_row)
        self.eraser_mode_row.append(Gtk.Label(label="Mode", xalign=0))
        eraser_modes = Gtk.StringList.new(["Freehand", "Line"])
        self.eraser_mode_dropdown = Gtk.DropDown(model=eraser_modes)
        self.eraser_mode_dropdown.set_hexpand(True)
        self.eraser_mode_dropdown.set_tooltip_text(
            "Freehand: erase along the stroke. "
            "Line: click-drag a straight erase segment (Shift snaps to 45°)."
        )
        self.eraser_mode_dropdown.connect("notify::selected", self._on_eraser_mode_changed)
        self.eraser_mode_row.append(self.eraser_mode_dropdown)

        self.replace_action_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        opts.append(self.replace_action_row)
        self.replace_action_row.append(Gtk.Label(label="Action", xalign=0))
        replace_actions = Gtk.StringList.new(["Replace", "Erase"])
        self.replace_action_dropdown = Gtk.DropDown(model=replace_actions)
        self.replace_action_dropdown.set_hexpand(True)
        self.replace_action_dropdown.set_tooltip_text(
            "Replace: paint the tool color over matching pixels. "
            "Erase: clear alpha on pixels matching the erase color (background eraser)."
        )
        self.replace_action_dropdown.connect("notify::selected", self._on_replace_action_changed)
        self.replace_action_row.append(self.replace_action_dropdown)

        self.apply_mode_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        opts.append(self.apply_mode_row)
        self.apply_mode_row.append(Gtk.Label(label="Mode", xalign=0))
        apply_modes = Gtk.StringList.new(["Brush", "Fill", "All"])
        self.apply_mode_dropdown = Gtk.DropDown(model=apply_modes)
        self.apply_mode_dropdown.set_hexpand(True)
        self.apply_mode_dropdown.set_tooltip_text(
            "Brush: under the stroke. "
            "Fill: connected region. "
            "All: every matching pixel on the layer."
        )
        self.apply_mode_dropdown.connect("notify::selected", self._on_apply_mode_changed)
        self.apply_mode_row.append(self.apply_mode_dropdown)

        self.fill_opts_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        opts.append(self.fill_opts_row)
        self.fill_opts_row.append(Gtk.Label(label="Fill", xalign=0))
        fill_modes = Gtk.StringList.new(["Color", "Pattern", "Maze", "Puzzle"])
        self.fill_mode_dropdown = Gtk.DropDown(model=fill_modes)
        self.fill_mode_dropdown.set_hexpand(True)
        self.fill_mode_dropdown.set_tooltip_text(
            "Solid color, tiled pattern, solvable maze, or interlocking puzzle pieces"
        )
        self.fill_mode_dropdown.connect("notify::selected", self._on_fill_mode_changed)
        self.fill_opts_row.append(self.fill_mode_dropdown)

        self.lay_opts_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        opts.append(self.lay_opts_row)
        self.lay_opts_row.append(Gtk.Label(label="Shape", xalign=0))
        lay_modes = Gtk.StringList.new(["Line", "Freehand", "Arc", "Circle"])
        self.lay_mode_dropdown = Gtk.DropDown(model=lay_modes)
        self.lay_mode_dropdown.set_hexpand(True)
        self.lay_mode_dropdown.set_tooltip_text(
            "Line: straight path (Shift snaps to 45°). "
            "Freehand: fit a curve to your drag. "
            "Arc: circular arc; set Degrees for the central angle. "
            "Circle: press center, drag radius (Alt = drag as diameter)."
        )
        self.lay_mode_dropdown.connect("notify::selected", self._on_lay_mode_changed)
        self.lay_opts_row.append(self.lay_mode_dropdown)

        # After Curve Mode / Lay Shape so Degrees sits next to the active shape control.
        self.arc_degrees_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        opts.append(self.arc_degrees_row)
        self.arc_degrees_row.append(Gtk.Label(label="Degrees", xalign=0))
        self.arc_degrees_spin = Gtk.SpinButton.new_with_range(1, 359, 1)
        configure_numeric_spin(self.arc_degrees_spin)
        self.arc_degrees_spin.set_value(180)
        self.arc_degrees_spin.set_hexpand(True)
        self.arc_degrees_spin.set_tooltip_text(
            "Central angle of the arc. 180° draws a semicircle using the drag as the diameter. "
            "Alt flips which side the arc bulges toward."
        )
        self.arc_degrees_spin.connect("value-changed", self._on_arc_degrees_changed)
        self.arc_degrees_row.append(self.arc_degrees_spin)

        # Shared by Fill (pattern mode) and Lay — keep as a top-level opts child
        # so it stays visible when fill_opts_row is hidden.
        self.pattern_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        opts.append(self.pattern_row)
        pat_head = Gtk.Box(spacing=4)
        pat_head.append(Gtk.Label(label="Pattern", xalign=0, hexpand=True))
        refresh_pat = Gtk.Button(label="↻")
        refresh_pat.set_tooltip_text("Reload patterns from Libraries folder")
        refresh_pat.connect("clicked", lambda *_: self._refresh_pattern_dropdown(
            select_path=getattr(self._active_tool(), "pattern_path", None)
        ))
        pat_head.append(refresh_pat)
        self.pattern_row.append(pat_head)
        self._pattern_paths: list[Path] = []
        self._pattern_thumb_size = 56
        self._pattern_preview_size = 40
        self.pattern_preview = Gtk.Picture()
        self.pattern_preview.set_size_request(self._pattern_preview_size, self._pattern_preview_size)
        self.pattern_preview.set_content_fit(Gtk.ContentFit.COVER)
        self.pattern_preview.set_can_shrink(False)
        self.pattern_menu_btn = Gtk.MenuButton()
        self.pattern_menu_btn.set_hexpand(True)
        self.pattern_menu_btn.set_always_show_arrow(True)
        self.pattern_menu_btn.set_child(self.pattern_preview)
        self.pattern_menu_btn.set_tooltip_text("Choose a pattern from Libraries → Patterns")
        pattern_popover = Gtk.Popover()
        pattern_scroll = Gtk.ScrolledWindow()
        pattern_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        pattern_scroll.set_min_content_height(168)
        pattern_scroll.set_max_content_height(280)
        pattern_scroll.set_min_content_width(200)
        self.pattern_flow = Gtk.FlowBox()
        self.pattern_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.pattern_flow.set_homogeneous(True)
        self.pattern_flow.set_max_children_per_line(4)
        self.pattern_flow.set_min_children_per_line(3)
        self.pattern_flow.set_column_spacing(4)
        self.pattern_flow.set_row_spacing(4)
        self.pattern_flow.set_valign(Gtk.Align.START)
        pattern_scroll.set_child(self.pattern_flow)
        pattern_popover.set_child(pattern_scroll)
        self.pattern_menu_btn.set_popover(pattern_popover)
        self.pattern_row.append(self.pattern_menu_btn)

        self.lay_spacing_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=2)
        opts.append(self.lay_spacing_row)
        self.lay_spacing_row.append(Gtk.Label(label="Distance", xalign=0))
        self.lay_spacing_spin = Gtk.SpinButton.new_with_range(1.0, 512.0, 1.0)
        configure_numeric_spin(self.lay_spacing_spin)
        self.lay_spacing_spin.set_value(40.0)
        self.lay_spacing_spin.set_hexpand(True)
        self.lay_spacing_spin.set_tooltip_text(
            "Spacing between laid pattern elements along the path (pixels)"
        )
        self.lay_spacing_spin.connect("value-changed", self._on_lay_spacing_changed)
        self.lay_spacing_row.append(self.lay_spacing_spin)

        self.tile_scale_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=2)
        opts.append(self.tile_scale_row)
        self.tile_scale_row.append(Gtk.Label(label="Tile Scale", xalign=0))
        self.tile_scale_spin = Gtk.SpinButton.new_with_range(0.1, 16.0, 0.1)
        configure_numeric_spin(self.tile_scale_spin)
        self.tile_scale_spin.set_value(1.0)
        self.tile_scale_spin.set_hexpand(True)
        self.tile_scale_spin.set_tooltip_text("Pattern tile size multiplier (1 = native image size)")
        self.tile_scale_spin.connect("value-changed", self._on_tile_scale_changed)
        self.tile_scale_row.append(self.tile_scale_spin)

        self.maze_cell_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=2)
        opts.append(self.maze_cell_row)
        self.maze_cell_label = Gtk.Label(label="Cell Size", xalign=0)
        self.maze_cell_row.append(self.maze_cell_label)
        self.maze_cell_spin = Gtk.SpinButton.new_with_range(3.0, 128.0, 1.0)
        configure_numeric_spin(self.maze_cell_spin)
        self.maze_cell_spin.set_value(8.0)
        self.maze_cell_spin.set_hexpand(True)
        self.maze_cell_spin.set_tooltip_text("Maze cell size in pixels (walls + corridor)")
        self.maze_cell_spin.connect("value-changed", self._on_maze_cell_changed)
        self.maze_cell_row.append(self.maze_cell_spin)


        self.type_opts_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        opts.append(self.type_opts_row)
        self.type_opts_row.append(Gtk.Label(label="Font Source", xalign=0))
        font_sources = Gtk.StringList.new(["System", "Library"])
        self.font_source_dropdown = Gtk.DropDown(model=font_sources)
        self.font_source_dropdown.set_hexpand(True)
        self.font_source_dropdown.set_tooltip_text(
            "System: fonts installed on this computer. "
            "Library: .ttf / .otf files in Libraries → Fonts."
        )
        self.font_source_dropdown.connect("notify::selected", self._on_font_source_changed)
        self.type_opts_row.append(self.font_source_dropdown)

        font_head = Gtk.Box(spacing=4, margin_top=2)
        font_head.append(Gtk.Label(label="Font", xalign=0, hexpand=True))
        refresh_font = Gtk.Button(label="↻")
        refresh_font.set_tooltip_text("Reload fonts from system / Libraries folder")
        refresh_font.connect("clicked", lambda *_: self._refresh_font_dropdown(
            select_path=getattr(self._active_tool(), "font_path", None),
            refresh_system=True,
        ))
        font_head.append(refresh_font)
        self.type_opts_row.append(font_head)
        self._font_paths: list[Path] = []
        self.font_dropdown = Gtk.DropDown(model=Gtk.StringList.new(["(no fonts)"]))
        self.font_dropdown.set_hexpand(True)
        self.font_dropdown.set_tooltip_text(
            "Click canvas to place text, type to edit, drag to move, Enter to rasterize. "
            "Esc cancels · Shift+Enter inserts a newline."
        )
        self.font_dropdown.connect("notify::selected", self._on_font_changed)
        self.type_opts_row.append(self.font_dropdown)

        # Primary / secondary color pair; X swaps them
        self.color_pair_row = Gtk.Grid(column_spacing=4, row_spacing=4, margin_top=4)
        opts.append(self.color_pair_row)
        self.color_label = Gtk.Label(label="Color", xalign=0)
        self.secondary_label = Gtk.Label(label="Secondary", xalign=0)
        self.color_pair_row.attach(self.color_label, 0, 0, 1, 1)
        self.color_pair_row.attach(self.secondary_label, 1, 0, 1, 1)
        self.color_btn = ColorSelectButton(use_alpha=True)
        self.color_btn.set_hexpand(True)
        self.color_btn.set_tooltip_text("Primary color: open color picker (hue + recent colors)")
        self.color_btn.set_recent_colors(self.app_settings.recent_colors)
        self.color_btn.connect("color-set", self._on_color)
        self.color_pair_row.attach(self.color_btn, 0, 1, 1, 1)
        self.secondary_color_btn = ColorSelectButton(use_alpha=True)
        self.secondary_color_btn.set_hexpand(True)
        self.secondary_color_btn.set_tooltip_text(
            "Secondary color: Gradient end, maze corridors, puzzle pieces"
        )
        self.secondary_color_btn.set_recent_colors(self.app_settings.recent_colors)
        self.secondary_color_btn.connect("color-set", self._on_secondary_color)
        self.color_pair_row.attach(self.secondary_color_btn, 1, 1, 1, 1)
        swap_colors = Gtk.Button(label="⇄", valign=Gtk.Align.CENTER)
        swap_colors.set_tooltip_text("Swap primary and secondary colors (X)")
        swap_colors.connect("clicked", self.action_swap_colors)
        self.color_pair_row.attach(swap_colors, 2, 1, 1, 1)

        self.palette_panel = PalettePanel(
            active_name=self.app_settings.active_palette,
            get_primary=lambda: tuple(int(v) for v in self._active_tool().color[:3]),
            on_pick=self._on_palette_pick,
            on_active_changed=self._on_active_palette_changed,
            on_colors_changed=self._on_palette_colors_changed,
            on_error=self._alert,
            get_layer=self._active_layer_for_palette,
        )
        opts.append(self.palette_panel)

        self.threshold_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        opts.append(self.threshold_row)
        self.threshold_row.append(Gtk.Label(label="Threshold", xalign=0))
        self.threshold_spin = Gtk.SpinButton.new_with_range(0, 255, 1)
        configure_numeric_spin(self.threshold_spin)
        self.threshold_spin.set_hexpand(True)
        self.threshold_spin.set_tooltip_text(
            "Color match tolerance (variance allowed). "
            "Fill / Replace / Gradient: how close pixels must be to the target color. "
            "Eraser: 255=all pixels, lower=only similar to click color."
        )
        self.threshold_spin.connect("value-changed", self._on_threshold_changed)
        self.threshold_row.append(self.threshold_spin)

        self.intensity_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        opts.append(self.intensity_row)
        self.intensity_label = Gtk.Label(label="Intensity", xalign=0)
        self.intensity_row.append(self.intensity_label)
        self.intensity_spin = Gtk.SpinButton.new_with_range(0, 100, 1)
        configure_numeric_spin(self.intensity_spin)
        self.intensity_spin.set_hexpand(True)
        self.intensity_spin.set_tooltip_text(
            "Smear / liquify strength, or weld-brush fillet amount. "
            "0 = none, 100 = strong. Pen pressure fine-tunes smear/liquify within this."
        )
        self.intensity_spin.connect("value-changed", self._on_intensity_changed)
        self.intensity_row.append(self.intensity_spin)

        self.liquify_mode_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        opts.append(self.liquify_mode_row)
        self.liquify_mode_row.append(Gtk.Label(label="Warp", xalign=0))
        liquify_modes = Gtk.StringList.new(list(LIQUIFY_BRUSH_MODES))
        self.liquify_mode_dropdown = Gtk.DropDown(model=liquify_modes)
        self.liquify_mode_dropdown.set_hexpand(True)
        self.liquify_mode_dropdown.set_tooltip_text(
            "Push: drag pixels along the stroke. "
            "Swirl / Pinch / Bulge: warp around the brush tip."
        )
        self.liquify_mode_dropdown.connect("notify::selected", self._on_liquify_mode_changed)
        self.liquify_mode_row.append(self.liquify_mode_dropdown)

        self.shade3d_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        opts.append(self.shade3d_row)

        self.fill3d_type_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.shade3d_row.append(self.fill3d_type_row)
        self.fill3d_type_row.append(Gtk.Label(label="Fill Type", xalign=0))
        fill3d_types = Gtk.StringList.new(
            ["Classic", "Addiction", "Wavy", "Drift", "Depression"]
        )
        self.fill3d_type_dropdown = Gtk.DropDown(model=fill3d_types)
        self.fill3d_type_dropdown.set_hexpand(True)
        self.fill3d_type_dropdown.set_tooltip_text(
            "Classic: soft emboss. Addiction: deeper multi-scale sculpting with lobed ridges and dual highlights. "
            "Wavy: corner-distance ripples with light from top corners and shadow from bottom. "
            "Drift: Addiction lighting with silhouette bands and directional ridges (no center pinch). "
            "Depression: inverted classic — carved inward with a dark basin and lit lip."
        )
        self.fill3d_type_dropdown.connect("notify::selected", self._on_fill3d_type_changed)
        self.fill3d_type_row.append(self.fill3d_type_dropdown)

        def _add_3d_spin(label: str, tooltip: str) -> Gtk.SpinButton:
            self.shade3d_row.append(Gtk.Label(label=label, xalign=0))
            spin = Gtk.SpinButton.new_with_range(0, 100, 1)
            configure_numeric_spin(spin)
            spin.set_hexpand(True)
            spin.set_tooltip_text(tooltip)
            spin.connect("value-changed", self._on_3d_setting_changed)
            self.shade3d_row.append(spin)
            return spin

        self.depth_spin = _add_3d_spin("Depth", "Shading contrast — low is flat, high is strongly embossed.")
        self.highlight_spin = _add_3d_spin("Highlight", "Specular highlight strength on lit edges.")
        self.bevel_spin = _add_3d_spin(
            "Bevel",
            "Edge softness / roundness. Higher = softer, more rounded 3D rim.",
        )

        self.frequency_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        opts.append(self.frequency_row)
        self.frequency_row.append(Gtk.Label(label="Frequency", xalign=0))
        self.frequency_spin = Gtk.SpinButton.new_with_range(0, 100, 1)
        configure_numeric_spin(self.frequency_spin)
        self.frequency_spin.set_hexpand(True)
        self.frequency_spin.set_tooltip_text(
            "Stamp density along the stroke. Low = spaced beads, high = continuous tube."
        )
        self.frequency_spin.connect("value-changed", self._on_frequency_changed)
        self.frequency_row.append(self.frequency_spin)

        toolbox_page.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL, margin_top=6, margin_bottom=2))
        mirror_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        toolbox_page.append(mirror_box)
        self.mirror_toggle = Gtk.CheckButton(label="Symmetry")
        self.mirror_toggle.set_tooltip_text(
            "Live symmetry while drawing. Alt-drag the hub to move the center; "
            "Alt-drag a guide line to rotate. Mirror reflects; Radial rotates copies; "
            "Linear Array repeats the stroke at a set distance and direction."
        )
        self.mirror_toggle.connect("toggled", self._on_mirror_toggled)
        mirror_box.append(self.mirror_toggle)

        self.tile_wrap_toggle = Gtk.CheckButton(label="Wrap")
        self.tile_wrap_toggle.set_tooltip_text(
            "Paint and transform across tile edges: content that leaves one side "
            "reappears on the opposite side. Turns on with Tile Preview and off "
            "when preview is disabled; you can still toggle it manually."
        )
        self.tile_wrap_toggle.set_active(False)
        self.tile_wrap_toggle.connect("toggled", self._on_tile_wrap_toggled)
        mirror_box.append(self.tile_wrap_toggle)

        self.pixel_mode_toggle = Gtk.CheckButton(label="Pixel")
        self.pixel_mode_toggle.set_tooltip_text(
            "Pixel art: paint tools draw hard pixels with no soft edges, Size is "
            "the stroke width in pixels, pressure is ignored and 1 px freehand "
            "strokes skip L-shaped corners (pixel-perfect). Saved with the document."
        )
        self.pixel_mode_toggle.set_active(self.pixel_mode)
        self.pixel_mode_toggle.connect("toggled", self._on_pixel_mode_toggled)
        mirror_box.append(self.pixel_mode_toggle)

        self.mirror_opts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.mirror_opts.set_visible(False)
        mirror_box.append(self.mirror_opts)

        self.mirror_opts.append(Gtk.Label(label="Mode", xalign=0))
        mode_labels = [label for _key, label in MODES]
        self.mirror_mode_dropdown = Gtk.DropDown(model=Gtk.StringList.new(mode_labels))
        self.mirror_mode_dropdown.set_hexpand(True)
        self.mirror_mode_dropdown.set_tooltip_text(
            "Mirror: reflect across axes (kaleidoscope). "
            "Radial: rotate same-facing copies (pinwheel). "
            "Linear Array: repeat copies at regular intervals along a direction."
        )
        self.mirror_mode_dropdown.connect("notify::selected", self._on_mirror_mode_changed)
        self.mirror_opts.append(self.mirror_mode_dropdown)

        self.mirror_orient_label = Gtk.Label(label="Orientation", xalign=0)
        self.mirror_opts.append(self.mirror_orient_label)
        orient_labels = [label for _key, label in ORIENTATIONS]
        self.mirror_orient_dropdown = Gtk.DropDown(model=Gtk.StringList.new(orient_labels))
        self.mirror_orient_dropdown.set_hexpand(True)
        self.mirror_orient_dropdown.set_tooltip_text(
            "Rotates the axis set. Horizontal: left↔right first. "
            "Vertical: top↔bottom first. Diagonal: 45°."
        )
        self.mirror_orient_dropdown.connect("notify::selected", self._on_mirror_orient_changed)
        self.mirror_opts.append(self.mirror_orient_dropdown)

        self.mirror_axes_label = Gtk.Label(label="Axes", xalign=0)
        self.mirror_opts.append(self.mirror_axes_label)
        self.mirror_axes_spin = Gtk.SpinButton.new_with_range(1, 16, 1)
        configure_numeric_spin(self.mirror_axes_spin, digits=0)
        self.mirror_axes_spin.set_value(1)
        self.mirror_axes_spin.set_hexpand(True)
        self.mirror_axes_spin.set_tooltip_text(
            "Equally spaced mirror axes through the center. "
            "1 = single axis; 2 = four-way (perpendicular); 3+ = kaleidoscope."
        )
        self.mirror_axes_spin.connect("value-changed", self._on_mirror_axes_changed)
        self.mirror_opts.append(self.mirror_axes_spin)

        self.mirror_array_opts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.mirror_array_opts.set_visible(False)
        self.mirror_opts.append(self.mirror_array_opts)
        self.mirror_array_opts.append(Gtk.Label(label="Copies", xalign=0))
        self.mirror_array_count_spin = Gtk.SpinButton.new_with_range(1, ARRAY_COUNT_MAX, 1)
        configure_numeric_spin(self.mirror_array_count_spin, digits=0)
        self.mirror_array_count_spin.set_hexpand(True)
        self.mirror_array_count_spin.set_tooltip_text(
            "Total instances along the array, the stroke you draw included."
        )
        self.mirror_array_opts.append(self.mirror_array_count_spin)
        self.mirror_array_opts.append(Gtk.Label(label="Spacing (px)", xalign=0))
        self.mirror_array_spacing_spin = Gtk.SpinButton.new_with_range(0.0, ARRAY_SPACING_MAX, 1.0)
        configure_numeric_spin(self.mirror_array_spacing_spin, digits=1)
        self.mirror_array_spacing_spin.set_hexpand(True)
        self.mirror_array_spacing_spin.set_tooltip_text(
            "Distance between neighboring copies, in document pixels. "
            "Alt-drag the square handle on the canvas to set distance and direction together."
        )
        self.mirror_array_opts.append(self.mirror_array_spacing_spin)
        self.mirror_array_opts.append(Gtk.Label(label="Direction (°)", xalign=0))
        self.mirror_array_direction_spin = Gtk.SpinButton.new_with_range(-180.0, 180.0, 1.0)
        configure_numeric_spin(self.mirror_array_direction_spin, digits=1)
        self.mirror_array_direction_spin.set_wrap(True)
        self.mirror_array_direction_spin.set_hexpand(True)
        self.mirror_array_direction_spin.set_tooltip_text(
            "Heading of the array: 0° = right, 90° = up, -90° = down. "
            "Alt-drag the guide line to rotate it."
        )
        self.mirror_array_opts.append(self.mirror_array_direction_spin)
        self._sync_mirror_array_ui()
        for spin in (
            self.mirror_array_count_spin,
            self.mirror_array_spacing_spin,
            self.mirror_array_direction_spin,
        ):
            spin.connect("value-changed", self._on_mirror_array_changed)

        reset_btn = Gtk.Button(label="Reset Guides")
        reset_btn.set_tooltip_text(
            "Move the hub back to the canvas center and snap rotation to Horizontal "
            "(Linear Array: heading back to 0°). Works in every mode."
        )
        reset_btn.connect("clicked", self._on_mirror_reset)
        self.mirror_opts.append(reset_btn)
        self._update_mirror_mode_ui()

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

    def _on_tool_toggled(self, btn: Gtk.ToggleButton, tid: str) -> None:
        if btn.get_active():
            self._cancel_type_edit_if_any()
            self.tool_id = tid
            for t in self.tools.values():
                t.reset()
            self._load_active_tool_settings()
            self._set_status()
            if tid == "type":
                type_tool = self.tools.get("type")
                if type_tool is not None and hasattr(type_tool, "set_editing_changed_callback"):
                    type_tool.set_editing_changed_callback(self._on_type_editing_changed)

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

    def _on_color(self, btn: ColorSelectButton) -> None:
        if self._syncing_tool_ui:
            return
        self._set_colors(primary=rgba_to_tuple(btn.get_rgba()))
        self._remember_recent_colors(btn)

    def _on_secondary_color(self, btn: ColorSelectButton) -> None:
        if self._syncing_tool_ui:
            return
        self._set_colors(secondary=rgba_to_tuple(btn.get_rgba()))
        self._remember_recent_colors(btn)

    def _remember_recent_colors(self, btn: ColorSelectButton) -> None:
        recent = btn.get_recent_colors()
        for other in (self.color_btn, self.secondary_color_btn):
            other.set_recent_colors(recent)
        self.app_settings.recent_colors = recent
        save_settings(self.app_settings)

    def _set_colors(
        self,
        *,
        primary: Optional[tuple[int, int, int, int]] = None,
        secondary: Optional[tuple[int, int, int, int]] = None,
    ) -> None:
        """Set the paint color pair: shared by all tools, or the active tool's own."""
        if self.app_settings.color_follows_tools:
            if primary is not None:
                self._shared_color = primary
            if secondary is not None:
                self._shared_secondary = secondary
            self._apply_shared_color_to_tools()
        else:
            tool = self._active_tool()
            if primary is not None:
                tool.color = primary
            if secondary is not None:
                tool.secondary_color = secondary
        self._refresh_type_preview()

    def _on_palette_pick(self, rgb: tuple[int, int, int], primary: bool) -> None:
        """Palette swatches are opaque RGB: keep the slot's current alpha."""
        tool = self._active_tool()
        alpha = int((tool.color if primary else tool.secondary_color)[3])
        color = (int(rgb[0]), int(rgb[1]), int(rgb[2]), alpha)
        if primary:
            self._set_colors(primary=color)
        else:
            self._set_colors(secondary=color)
        self._sync_color_buttons()

    def _active_layer_for_palette(self):
        """(name, uint8 RGBA) of the active layer for Palette ⋯ → From Selected Layer."""
        if self.document is None:
            return None
        layer = self.document.active_layer
        return layer.name, to_display_u8(layer.pixels)

    def _on_active_palette_changed(self, name: str) -> None:
        if self.app_settings.active_palette != name:
            self.app_settings.active_palette = name
            save_settings(self.app_settings)

    def _on_palette_colors_changed(self, colors: list[tuple[int, int, int]]) -> None:
        for btn in (self.color_btn, self.secondary_color_btn):
            btn.set_palette_colors(colors)

    def _sync_color_buttons(self) -> None:
        tool = self._active_tool()
        self.color_btn.set_rgba(tuple_to_rgba(tool.color))
        self.secondary_color_btn.set_rgba(tuple_to_rgba(tool.secondary_color))

    def _update_color_labels(self) -> None:
        tool = self._active_tool()
        primary, secondary = getattr(tool, "color_labels", ("Color", "Secondary"))
        mode = getattr(tool, "fill_mode", "color") if getattr(tool, "uses_fill_options", False) else ""
        if mode == "maze":
            primary, secondary = "Wall", "Corridor"
        elif mode == "puzzle":
            primary, secondary = "Outline", "Piece"
        elif getattr(tool, "uses_replace_modes", False) and getattr(tool, "replace_action", "replace") == "erase":
            primary = "Erase Color"
        self.color_label.set_label(primary)
        self.secondary_label.set_label(secondary)

    def action_swap_colors(self, *_a) -> None:
        tool = self._active_tool()
        self._set_colors(primary=tool.secondary_color, secondary=tool.color)
        self._sync_color_buttons()

    def action_reset_colors(self, *_a) -> None:
        self._set_colors(primary=(0, 0, 0, 255), secondary=(255, 255, 255, 255))
        self._sync_color_buttons()

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

    def _refresh_tool_shortcut_tips(self) -> None:
        for tid, label in TOOL_ORDER:
            btn = self.tool_buttons.get(tid)
            if btn is None:
                continue
            keys = format_accels(self.shortcuts.get(f"win.tool_{tid}", []))
            tip = f"{label}  ({keys})" if keys != "None" else label
            btn.set_tooltip_text(tip)

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
