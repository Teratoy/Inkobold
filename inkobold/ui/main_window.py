from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from inkobold.core.document import Document
from inkobold.core.effects import (
    DITHER_MODES,
    LIQUIFY_BRUSH_MODES,
    LIQUIFY_MODES,
    blend_effect,
    dither,
    gaussian_blur,
    kuwahara,
    liquify,
    pixelate,
    posterize,
)
from inkobold.core.grid import GridOverlay
from inkobold.core.history import History
from inkobold.core.libraries import (
    CATEGORIES,
    category_dir,
    ensure_libraries,
    list_fonts,
    list_patterns,
    list_system_fonts,
    open_in_file_manager,
)
from inkobold.core.mirror import ORIENTATIONS, MirrorModifier
from inkobold.core.settings import DEFAULT_THEME_RGB, load_settings, save_settings
from inkobold.core.shortcuts import (
    DEFAULT_SHORTCUTS,
    SHORTCUT_LABELS,
    SHORTCUT_ORDER,
    format_accels,
    merge_shortcuts,
)
from inkobold.input import InputHub
from inkobold.tools import default_tools
from inkobold.ui.canvas import Canvas
from inkobold.ui.color_picker import ColorSelectButton
from inkobold.ui.dialogs import (
    CaptureShortcutDialog,
    ColorDepthDialog,
    CropDialog,
    DpiDialog,
    EffectOption,
    EffectPreviewDialog,
    GridOverlayDialog,
    HistoryStepsDialog,
    NewFileDialog,
    QuitConfirmDialog,
    ShortcutsDialog,
    StartupDialog,
    ThemeColorDialog,
)
from inkobold.ui.theme import DEFAULT_CSS, build_theme_css


TOOL_ORDER = [
    ("pen", "Pen", "P", "1"),
    ("line", "Line", "L", ""),
    ("curve", "Curve", "U", ""),
    ("brush", "Brush", "B", ""),
    ("fill", "Fill", "G", "2"),
    ("pen3d", "3D Pen", "D", "8"),
    ("fill3d", "3D Fill", "F", "9"),
    ("eraser", "Eraser", "E", "3"),
    ("smear", "Smear", "M", ""),
    ("liquify", "Liquify", "Y", ""),
    ("replace", "Replace", "C/R", "0/5"),
    ("move", "Transform", "V", "6"),
    ("lasso", "Lasso", "Q", "7"),
    ("type", "Type", "T", ""),
]


@dataclass
class _DocTab:
    """One open document and its undo/view state."""

    document: Document
    history: History
    zoom: float = 1.0
    pan_x: float = 40.0
    pan_y: float = 40.0
    root: Optional[Gtk.Widget] = field(default=None, repr=False)
    label: Optional[Gtk.Label] = field(default=None, repr=False)


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="Inkobold")
        self.set_default_size(1280, 860)

        self.document: Optional[Document] = None
        self.tools = default_tools()
        self.tool_id = "pen"
        self._syncing_tool_ui = False
        self.app_settings = load_settings()
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
        self._window_mode = "borderless"
        self._anim_playing = False
        self._anim_timer_id: Optional[int] = None
        self._effect_session: Optional[dict] = None
        self.mirror = MirrorModifier()
        self.grid = GridOverlay()
        self.input_hub = InputHub()
        self.input_hub.start()
        ensure_libraries()
        self._css_provider = Gtk.CssProvider()

        self._build_actions()
        self._build_ui()
        self._install_shortcuts()
        self._open_at_startup = False
        GLib.idle_add(self._prompt_startup)

        GLib.timeout_add_seconds(2, self._refresh_status)

    def _build_actions(self) -> None:
        mapping = {
            "new": self.action_new,
            "open": self.action_open,
            "save": self.action_save,
            "save_as": self.action_save_as,
            "import_image": self.action_import_image,
            "export": self.action_export,
            "export_layers": self.action_export_layers,
            "export_anim_gif": self.action_export_anim_gif,
            "export_anim_png": self.action_export_anim_png,
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
            "effect_dither": self.action_effect_dither,
            "effect_posterize": self.action_effect_posterize,
            "effect_liquify": self.action_effect_liquify,
            "add_layer": self.action_add_layer,
            "delete_layer": self.action_delete_layer,
            "rename_layer": self.action_rename_layer,
            "merge_layer_down": self.action_merge_layer_down,
            "merge_all_layers": self.action_merge_all_layers,
            "add_frame": self.action_add_frame,
            "duplicate_frame": self.action_duplicate_frame,
            "delete_frame": self.action_delete_frame,
            "play_animation": self.action_play_animation,
            "stop_animation": self.action_stop_animation,
            "brush_smaller": self.action_brush_smaller,
            "brush_larger": self.action_brush_larger,
            "theme_color": self.action_theme_color,
            "theme_reset": self.action_theme_reset,
            "memory_steps": self.action_memory_steps,
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

        checker_light = Gio.SimpleAction.new_stateful(
            "checker_light",
            None,
            GLib.Variant.new_boolean(bool(self.app_settings.checker_light)),
        )
        checker_light.connect("change-state", self._on_checker_light_change)
        self.add_action(checker_light)

        grid_settings = Gio.SimpleAction.new("grid_settings", None)
        grid_settings.connect("activate", self.action_grid_settings)
        self.add_action(grid_settings)

        rebind = Gio.SimpleAction.new("shortcut_rebind", GLib.VariantType.new("s"))
        rebind.connect("activate", self._on_shortcut_rebind_action)
        self.add_action(rebind)

        for tid, _label, _letter, _num in TOOL_ORDER:
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
            rgba = self.color_btn.get_rgba()
            r, g, b, a = tool.color
            rgba.red, rgba.green, rgba.blue, rgba.alpha = r / 255.0, g / 255.0, b / 255.0, a / 255.0
            self.color_btn.set_rgba(rgba)
            if show_replace and getattr(tool, "replace_action", "replace") == "erase":
                self.color_label.set_label("Erase Color")
            else:
                self.color_label.set_label("Color")
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
                    {"classic": 0, "addiction": 1, "wavy": 2, "drift": 3}.get(ftype, 0)
                )
            show_freq = bool(getattr(tool, "uses_frequency", False))
            self.frequency_row.set_visible(show_freq)
            if show_freq:
                self.frequency_spin.set_value(float(getattr(tool, "frequency", 60)))
            show_fill = bool(getattr(tool, "uses_fill_options", False))
            self.fill_opts_row.set_visible(show_fill)
            if show_fill:
                mode = getattr(tool, "fill_mode", "color")
                self.fill_mode_dropdown.set_selected(1 if mode == "pattern" else 0)
                self.tile_scale_spin.set_value(float(getattr(tool, "tile_scale", 1.0)))
                self._refresh_pattern_dropdown(select_path=getattr(tool, "pattern_path", None))
            self._update_fill_mode_visibility()
            show_intensity = bool(getattr(tool, "uses_intensity", False))
            self.intensity_row.set_visible(show_intensity)
            if show_intensity:
                self.intensity_spin.set_value(float(getattr(tool, "intensity", 50)))
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
            show_type = bool(getattr(tool, "uses_type_options", False))
            self.type_opts_row.set_visible(show_type)
            if show_type:
                self.size_label.set_label("Font Size")
                src = getattr(tool, "font_source", "system")
                self.font_source_dropdown.set_selected(1 if src == "library" else 0)
                self._refresh_font_dropdown(select_path=getattr(tool, "font_path", None))
            else:
                self.size_label.set_label("Size")
        finally:
            self._syncing_tool_ui = False

    def _update_curve_mode_visibility(self) -> None:
        tool = self._active_tool()
        show_arc = (
            bool(getattr(tool, "uses_curve_modes", False))
            and getattr(tool, "curve_mode", "freehand") == "arc"
        )
        self.arc_degrees_row.set_visible(show_arc)

    def _update_fill_mode_visibility(self) -> None:
        tool = self._active_tool()
        uses_fill = bool(getattr(tool, "uses_fill_options", False))
        pattern_mode = uses_fill and getattr(tool, "fill_mode", "color") == "pattern"
        show_color = bool(getattr(tool, "uses_color", True)) and not pattern_mode
        self.color_label.set_visible(show_color)
        self.color_btn.set_visible(show_color)
        self.pattern_row.set_visible(pattern_mode)
        self.tile_scale_row.set_visible(pattern_mode)

    def _refresh_pattern_dropdown(self, select_path: Optional[Path] = None) -> None:
        paths = list_patterns()
        self._pattern_paths = paths
        strings = Gtk.StringList.new([p.name for p in paths] if paths else ["(no patterns)"])
        self.pattern_dropdown.set_model(strings)
        self.pattern_dropdown.set_sensitive(bool(paths))
        if not paths:
            tool = self._active_tool()
            if getattr(tool, "uses_fill_options", False):
                tool.set_pattern_path(None)
            return
        idx = 0
        if select_path is not None:
            try:
                idx = next(i for i, p in enumerate(paths) if p.resolve() == Path(select_path).resolve())
            except StopIteration:
                idx = 0
        self.pattern_dropdown.set_selected(idx)
        tool = self._active_tool()
        if getattr(tool, "uses_fill_options", False):
            tool.set_pattern_path(paths[idx])

    def _on_fill_mode_changed(self, dropdown: Gtk.DropDown, *_a) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if not getattr(tool, "uses_fill_options", False):
            return
        tool.fill_mode = "pattern" if dropdown.get_selected() == 1 else "color"
        self._update_fill_mode_visibility()

    def _on_replace_action_changed(self, dropdown: Gtk.DropDown, *_a) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if not getattr(tool, "uses_replace_modes", False):
            return
        tool.replace_action = "erase" if dropdown.get_selected() == 1 else "replace"
        self.color_label.set_label("Erase Color" if tool.replace_action == "erase" else "Color")

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

    def _on_pattern_changed(self, dropdown: Gtk.DropDown, *_a) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if not getattr(tool, "uses_fill_options", False):
            return
        paths = getattr(self, "_pattern_paths", [])
        idx = int(dropdown.get_selected())
        if 0 <= idx < len(paths):
            tool.set_pattern_path(paths[idx])
        else:
            tool.set_pattern_path(None)

    def _on_tile_scale_changed(self, spin: Gtk.SpinButton) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if getattr(tool, "uses_fill_options", False):
            tool.tile_scale = float(spin.get_value())

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
        self._set_typing_shortcuts_suspended(active)
        if hasattr(self, "canvas"):
            if active:
                self.canvas._catcher.grab_focus()
            # Text creates/removes a layer — keep the sidebar in sync.
            self._rebuild_layers()
            self.canvas.queue_render()
            self.canvas.refresh_guides()
            self._set_status()

    def _set_typing_shortcuts_suspended(self, suspend: bool) -> None:
        """Drop bare letter/digit accelerators while typing so they insert text."""
        app = self.get_application()
        if app is None:
            return
        if not suspend:
            self._install_shortcuts()
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
        self._set_typing_shortcuts_suspended(False)

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

    def _on_arc_degrees_changed(self, spin: Gtk.SpinButton) -> None:
        if self._syncing_tool_ui:
            return
        tool = self._active_tool()
        if getattr(tool, "uses_curve_modes", False):
            tool.arc_degrees = float(spin.get_value())

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
        tool.fill3d_type = ("classic", "addiction", "wavy", "drift")[min(dropdown.get_selected(), 3)]

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

    def _on_mirror_orient_changed(self, dropdown: Gtk.DropDown, *_a) -> None:
        idx = int(dropdown.get_selected())
        if 0 <= idx < len(ORIENTATIONS):
            self.mirror.orientation = ORIENTATIONS[idx][0]
        if hasattr(self, "canvas"):
            self.canvas.refresh_guides()

    def _on_mirror_radials_changed(self, spin: Gtk.SpinButton) -> None:
        self.mirror.radials = int(spin.get_value())
        self.mirror.clamp()
        if hasattr(self, "canvas"):
            self.canvas.refresh_guides()

    def _on_show_grid_change(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        enabled = bool(value.get_boolean())
        action.set_state(value)
        self.grid.enabled = enabled
        if hasattr(self, "canvas"):
            self.canvas.refresh_guides()

    def _on_checker_light_change(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        enabled = bool(value.get_boolean())
        action.set_state(value)
        self.app_settings.checker_light = enabled
        self._persist_settings()
        if hasattr(self, "canvas"):
            self.canvas.renderer.checker_light = enabled
            self.canvas.queue_render()

    def action_grid_settings(self, *_a) -> None:
        dlg = GridOverlayDialog(self, self.grid.rows, self.grid.columns)
        dlg.connect_response(self._on_grid_settings_response)
        dlg.present()

    def _on_grid_settings_response(self, dlg: GridOverlayDialog, response: int) -> None:
        if response != Gtk.ResponseType.OK:
            return
        self.grid.rows = dlg.rows()
        self.grid.columns = dlg.columns()
        self.grid.clamp()
        if hasattr(self, "canvas"):
            self.canvas.refresh_guides()

    def action_brush_smaller(self, *_a) -> None:
        self.brush_spin.set_value(max(1, self.brush_spin.get_value() - 1))

    def action_brush_larger(self, *_a) -> None:
        self.brush_spin.set_value(min(256, self.brush_spin.get_value() + 1))

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

        file_btn = Gtk.MenuButton(label="File")
        file_menu = Gio.Menu()
        file_menu.append("New File…", "win.new")
        file_menu.append("Open File…", "win.open")
        file_menu.append("Save File", "win.save")
        file_menu.append("Save As…", "win.save_as")
        file_menu.append("Export…", "win.export")
        file_menu.append("Export Separated Layers…", "win.export_layers")
        export_anim_menu = Gio.Menu()
        export_anim_menu.append("As GIF…", "win.export_anim_gif")
        export_anim_menu.append("As PNG Folder…", "win.export_anim_png")
        file_menu.append_submenu("Export Animation", export_anim_menu)
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
        effects_menu.append("Pixelate…", "win.effect_pixelate")
        effects_menu.append("Kuwahara…", "win.effect_kuwahara")
        effects_menu.append("Gaussian Blur…", "win.effect_gaussian_blur")
        effects_menu.append("Dither…", "win.effect_dither")
        effects_menu.append("Posterize…", "win.effect_posterize")
        effects_menu.append("Liquify…", "win.effect_liquify")
        effects_btn.set_menu_model(effects_menu)
        menubar.append(effects_btn)

        view_btn = Gtk.MenuButton(label="View")
        view_menu = Gio.Menu()
        view_menu.append("Fit Canvas", "win.fit")
        view_menu.append("Show Grid", "win.show_grid")
        view_menu.append("Grid…", "win.grid_settings")
        view_menu.append("Light Transparent Background", "win.checker_light")
        view_menu.append("Fullscreen", "win.window_fullscreen")
        view_menu.append("Borderless Window", "win.window_borderless")
        view_menu.append("Windowed", "win.window_windowed")
        view_btn.set_menu_model(view_menu)
        menubar.append(view_btn)

        layer_btn = Gtk.MenuButton(label="Layer")
        layer_menu = Gio.Menu()
        layer_menu.append("Add Layer", "win.add_layer")
        layer_menu.append("Delete Layer", "win.delete_layer")
        layer_menu.append("Rename Layer…", "win.rename_layer")
        layer_menu.append("Merge with Layer Below", "win.merge_layer_down")
        layer_menu.append("Merge All", "win.merge_all_layers")
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
        settings_menu.append_submenu("Memory", memory_menu)

        image_menu = Gio.Menu()
        image_menu.append("Crop Canvas…", "win.crop_canvas")
        image_menu.append("DPI…", "win.image_dpi")
        image_menu.append("Color Depth…", "win.image_color_depth")
        settings_menu.append_submenu("Image", image_menu)

        self._shortcuts_menu = Gio.Menu()
        settings_menu.append_submenu("Shortcuts", self._shortcuts_menu)
        settings_btn.set_menu_model(settings_menu)
        menubar.append(settings_btn)
        self._rebuild_shortcuts_submenu()

        tabs_host = Gtk.Box(spacing=4, hexpand=True, halign=Gtk.Align.FILL)
        tabs_host.add_css_class("doc-tabs-host")
        menubar.append(tabs_host)

        tabs_scroll = Gtk.ScrolledWindow()
        tabs_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        tabs_scroll.set_hexpand(True)
        tabs_scroll.set_vexpand(False)
        tabs_scroll.set_overlay_scrolling(True)
        tabs_host.append(tabs_scroll)

        self.doc_tabs = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.doc_tabs.add_css_class("doc-tabs")
        tabs_scroll.set_child(self.doc_tabs)

        new_tab_btn = Gtk.Button(label="+")
        new_tab_btn.set_tooltip_text("New canvas tab")
        new_tab_btn.add_css_class("doc-tab-new")
        new_tab_btn.set_valign(Gtk.Align.CENTER)
        new_tab_btn.connect("clicked", lambda *_: self.action_new())
        tabs_host.append(new_tab_btn)

        exit_btn = Gtk.Button(label="Exit")
        exit_btn.set_tooltip_text("Exit Inkobold")
        exit_btn.add_css_class("exit-btn")
        exit_btn.set_halign(Gtk.Align.END)
        exit_btn.connect("clicked", self.request_quit)
        menubar.append(exit_btn)

        # Shared default width for Tools and Layers columns
        self._side_panel_w = 180
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
        for tid, label, letter, num in TOOL_ORDER:
            btn = Gtk.ToggleButton(label=label)
            btn.add_css_class("tool-btn")
            tip = f"{label}  ({letter})" if not num else f"{label}  ({letter} / {num})"
            btn.set_tooltip_text(tip)
            btn.set_hexpand(True)
            if group is None:
                group = btn
                btn.set_active(True)
            else:
                btn.set_group(group)
            btn.connect("toggled", self._on_tool_toggled, tid)
            self.tool_buttons[tid] = btn
            tool_flow.append(btn)

        toolbox_page.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL, margin_top=4, margin_bottom=4))

        opts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        toolbox_page.append(opts)
        self.size_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        opts.append(self.size_row)
        self.size_label = Gtk.Label(label="Size", xalign=0)
        self.size_row.append(self.size_label)
        self.brush_spin = Gtk.SpinButton.new_with_range(1, 256, 1)
        self.brush_spin.set_hexpand(True)
        self.brush_spin.connect("value-changed", self._on_brush_changed)
        self.size_row.append(self.brush_spin)

        self.opacity_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        opts.append(self.opacity_row)
        self.opacity_row.append(Gtk.Label(label="Opacity", xalign=0))
        self.opacity_spin = Gtk.SpinButton.new_with_range(0, 100, 1)
        self.opacity_spin.set_value(100)
        self.opacity_spin.set_hexpand(True)
        self.opacity_spin.set_tooltip_text(
            "Stroke / fill / erase strength. Combines with the color's alpha when painting."
        )
        self.opacity_spin.connect("value-changed", self._on_opacity_changed)
        self.opacity_row.append(self.opacity_spin)

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

        self.arc_degrees_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        opts.append(self.arc_degrees_row)
        self.arc_degrees_row.append(Gtk.Label(label="Degrees", xalign=0))
        self.arc_degrees_spin = Gtk.SpinButton.new_with_range(1, 359, 1)
        self.arc_degrees_spin.set_value(180)
        self.arc_degrees_spin.set_hexpand(True)
        self.arc_degrees_spin.set_tooltip_text(
            "Central angle of the arc. 180° draws a semicircle using the drag as the diameter. "
            "Alt flips which side the arc bulges toward."
        )
        self.arc_degrees_spin.connect("value-changed", self._on_arc_degrees_changed)
        self.arc_degrees_row.append(self.arc_degrees_spin)

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
        fill_modes = Gtk.StringList.new(["Color", "Pattern"])
        self.fill_mode_dropdown = Gtk.DropDown(model=fill_modes)
        self.fill_mode_dropdown.set_hexpand(True)
        self.fill_mode_dropdown.set_tooltip_text("Solid color or tiled pattern from Libraries → Patterns")
        self.fill_mode_dropdown.connect("notify::selected", self._on_fill_mode_changed)
        self.fill_opts_row.append(self.fill_mode_dropdown)

        self.pattern_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=2)
        self.fill_opts_row.append(self.pattern_row)
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
        self.pattern_dropdown = Gtk.DropDown(model=Gtk.StringList.new(["(no patterns)"]))
        self.pattern_dropdown.set_hexpand(True)
        self.pattern_dropdown.connect("notify::selected", self._on_pattern_changed)
        self.pattern_row.append(self.pattern_dropdown)

        self.tile_scale_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=2)
        self.fill_opts_row.append(self.tile_scale_row)
        self.tile_scale_row.append(Gtk.Label(label="Tile Scale", xalign=0))
        self.tile_scale_spin = Gtk.SpinButton.new_with_range(0.1, 16.0, 0.1)
        self.tile_scale_spin.set_digits(2)
        self.tile_scale_spin.set_value(1.0)
        self.tile_scale_spin.set_hexpand(True)
        self.tile_scale_spin.set_tooltip_text("Pattern tile size multiplier (1 = native image size)")
        self.tile_scale_spin.connect("value-changed", self._on_tile_scale_changed)
        self.tile_scale_row.append(self.tile_scale_spin)

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

        self.color_label = Gtk.Label(label="Color", xalign=0, margin_top=4)
        opts.append(self.color_label)
        self.color_btn = ColorSelectButton(use_alpha=True)
        self.color_btn.set_hexpand(True)
        self.color_btn.set_tooltip_text("Open color picker (hue + recent colors)")
        self.color_btn.set_recent_colors(self.app_settings.recent_colors)
        self.color_btn.connect("color-set", self._on_color)
        opts.append(self.color_btn)

        self.threshold_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        opts.append(self.threshold_row)
        self.threshold_row.append(Gtk.Label(label="Threshold", xalign=0))
        self.threshold_spin = Gtk.SpinButton.new_with_range(0, 255, 1)
        self.threshold_spin.set_hexpand(True)
        self.threshold_spin.set_tooltip_text(
            "Color match tolerance (variance allowed). "
            "Fill / Replace: how close pixels must be to the target color. "
            "Eraser: 255=all pixels, lower=only similar to click color."
        )
        self.threshold_spin.connect("value-changed", self._on_threshold_changed)
        self.threshold_row.append(self.threshold_spin)

        self.intensity_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        opts.append(self.intensity_row)
        self.intensity_row.append(Gtk.Label(label="Intensity", xalign=0))
        self.intensity_spin = Gtk.SpinButton.new_with_range(0, 100, 1)
        self.intensity_spin.set_hexpand(True)
        self.intensity_spin.set_tooltip_text(
            "Smear / liquify strength. 0 = none, 100 = strong. Pen pressure fine-tunes within this."
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
        fill3d_types = Gtk.StringList.new(["Classic", "Addiction", "Wavy", "Drift"])
        self.fill3d_type_dropdown = Gtk.DropDown(model=fill3d_types)
        self.fill3d_type_dropdown.set_hexpand(True)
        self.fill3d_type_dropdown.set_tooltip_text(
            "Classic: soft emboss. Addiction: deeper multi-scale sculpting with lobed ridges and dual highlights. "
            "Wavy: corner-distance ripples with light from top corners and shadow from bottom. "
            "Drift: Addiction lighting with silhouette bands and directional ridges (no center pinch)."
        )
        self.fill3d_type_dropdown.connect("notify::selected", self._on_fill3d_type_changed)
        self.fill3d_type_row.append(self.fill3d_type_dropdown)

        def _add_3d_spin(label: str, tooltip: str) -> Gtk.SpinButton:
            self.shade3d_row.append(Gtk.Label(label=label, xalign=0))
            spin = Gtk.SpinButton.new_with_range(0, 100, 1)
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
        self.frequency_spin.set_hexpand(True)
        self.frequency_spin.set_tooltip_text(
            "Stamp density along the stroke. Low = spaced beads, high = continuous tube."
        )
        self.frequency_spin.connect("value-changed", self._on_frequency_changed)
        self.frequency_row.append(self.frequency_spin)

        toolbox_page.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL, margin_top=6, margin_bottom=2))
        mirror_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        toolbox_page.append(mirror_box)
        self.mirror_toggle = Gtk.CheckButton(label="Mirror")
        self.mirror_toggle.set_tooltip_text(
            "Live symmetry while drawing. Mirrors strokes across the canvas center."
        )
        self.mirror_toggle.connect("toggled", self._on_mirror_toggled)
        mirror_box.append(self.mirror_toggle)

        self.mirror_opts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.mirror_opts.set_visible(False)
        mirror_box.append(self.mirror_opts)

        self.mirror_opts.append(Gtk.Label(label="Orientation", xalign=0))
        orient_labels = [label for _key, label in ORIENTATIONS]
        self.mirror_orient_dropdown = Gtk.DropDown(model=Gtk.StringList.new(orient_labels))
        self.mirror_orient_dropdown.set_hexpand(True)
        self.mirror_orient_dropdown.set_tooltip_text(
            "Horizontal: left↔right. Vertical: top↔bottom. Both: four-way."
        )
        self.mirror_orient_dropdown.connect("notify::selected", self._on_mirror_orient_changed)
        self.mirror_opts.append(self.mirror_orient_dropdown)

        self.mirror_opts.append(Gtk.Label(label="Radials", xalign=0))
        self.mirror_radials_spin = Gtk.SpinButton.new_with_range(1, 16, 1)
        self.mirror_radials_spin.set_value(1)
        self.mirror_radials_spin.set_hexpand(True)
        self.mirror_radials_spin.set_tooltip_text(
            "Rotational copies around the center. 1 = orientation mirrors only; "
            "2+ adds equal-angle radial symmetry (combined with orientation)."
        )
        self.mirror_radials_spin.connect("value-changed", self._on_mirror_radials_changed)
        self.mirror_opts.append(self.mirror_radials_spin)

        # Libraries tab — open category folders in the system file manager
        libraries_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["toolbox"])
        libraries_page.append(Gtk.Label(
            label="Open a category folder to add assets. Patterns (.png / .jpeg) appear in Fill; "
            "fonts (.ttf / .otf) appear in the Type tool Library source.",
            xalign=0,
            wrap=True,
        ))
        for cat_id, cat_label in CATEGORIES:
            btn = Gtk.Button(label=cat_label)
            btn.add_css_class("lib-cat-btn")
            btn.set_hexpand(True)
            if cat_id == "brushes":
                btn.set_tooltip_text("Open brushes folder (not implemented yet)")
            elif cat_id == "fonts":
                btn.set_tooltip_text("Open fonts folder — drop .ttf / .otf files here")
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
            on_type_editing=self._on_type_editing_changed,
        )
        type_tool = self.tools.get("type")
        if type_tool is not None and hasattr(type_tool, "set_editing_changed_callback"):
            type_tool.set_editing_changed_callback(self._on_type_editing_changed)
        self.canvas.animation_playing = False
        self.canvas.renderer.checker_light = bool(self.app_settings.checker_light)
        right_split.set_start_child(self.canvas)

        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, css_classes=["sidebar"])
        side.set_size_request(self._side_panel_w, -1)
        side.set_hexpand(False)
        side.set_vexpand(True)
        right_split.set_end_child(side)

        # Frames | Layers share height so section footers (+/−, FPS, …) stay on-screen.
        self._sidebar_split = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self._sidebar_split.set_wide_handle(True)
        self._sidebar_split.set_resize_start_child(True)
        self._sidebar_split.set_resize_end_child(True)
        self._sidebar_split.set_shrink_start_child(True)
        self._sidebar_split.set_shrink_end_child(True)
        side.append(self._sidebar_split)

        # --- Animation frames (collapsible) ---
        self.frames_expander = Gtk.Expander(label="Frames", expanded=False)
        self.frames_expander.set_hexpand(True)
        self.frames_expander.set_vexpand(False)
        frames_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.frames_expander.set_child(frames_body)
        self._sidebar_split.set_start_child(self.frames_expander)

        self.frame_list = Gtk.ListBox()
        self.frame_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.frame_list.set_valign(Gtk.Align.START)
        self.frame_list.connect("row-selected", self._on_frame_selected)
        frame_drop = Gtk.DropTarget.new(GObject.TYPE_INT64, Gdk.DragAction.MOVE)
        frame_drop.connect("drop", self._on_frame_drop)
        self.frame_list.add_controller(frame_drop)
        self.frame_scroll = Gtk.ScrolledWindow(vexpand=False, hexpand=True)
        self.frame_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.frame_scroll.set_min_content_height(48)
        self.frame_scroll.set_child(self.frame_list)
        frames_body.append(self.frame_scroll)

        frame_btns = Gtk.Box(spacing=4)
        add_fr = Gtk.Button(label="+")
        add_fr.set_tooltip_text("Add blank frame after current")
        add_fr.connect("clicked", lambda *_: self.action_add_frame(None, None))
        dup_fr = Gtk.Button(label="Dup")
        dup_fr.set_tooltip_text("Duplicate current frame")
        dup_fr.connect("clicked", lambda *_: self.action_duplicate_frame(None, None))
        del_fr = Gtk.Button(label="−")
        del_fr.set_tooltip_text("Delete current frame")
        del_fr.connect("clicked", lambda *_: self.action_delete_frame(None, None))
        frame_btns.append(add_fr)
        frame_btns.append(dup_fr)
        frame_btns.append(del_fr)
        frames_body.append(frame_btns)

        play_row = Gtk.Box(spacing=4)
        self.play_btn = Gtk.Button(label="▶")
        self.play_btn.set_tooltip_text("Play / pause animation (Space)")
        self.play_btn.connect("clicked", lambda *_: self.action_play_animation(None, None))
        self.stop_btn = Gtk.Button(label="■")
        self.stop_btn.set_tooltip_text("Stop playback")
        self.stop_btn.connect("clicked", lambda *_: self.action_stop_animation(None, None))
        play_row.append(self.play_btn)
        play_row.append(self.stop_btn)
        frames_body.append(play_row)

        fps_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        fps_row.append(Gtk.Label(label="FPS", xalign=0))
        self.fps_spin = Gtk.SpinButton.new_with_range(1, 60, 1)
        self.fps_spin.set_value(12)
        self.fps_spin.set_hexpand(True)
        self.fps_spin.set_tooltip_text("Playback speed in frames per second")
        self.fps_spin.connect("value-changed", self._on_fps_changed)
        fps_row.append(self.fps_spin)
        frames_body.append(fps_row)

        onion_row = Gtk.Box(spacing=6)
        self.onion_check = Gtk.CheckButton(label="Onion skin")
        self.onion_check.set_active(True)
        self.onion_check.set_tooltip_text("Show the previous frame faintly while drawing")
        self.onion_check.connect("toggled", self._on_onion_toggled)
        onion_row.append(self.onion_check)
        frames_body.append(onion_row)

        self.frames_expander.connect(
            "notify::expanded",
            self._on_sidebar_section_expanded,
            self.frame_scroll,
        )

        # --- Layers (collapsible) ---
        self.layers_expander = Gtk.Expander(label="Layers", expanded=False)
        self.layers_expander.set_hexpand(True)
        self.layers_expander.set_vexpand(False)
        layers_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.layers_expander.set_child(layers_body)
        self._sidebar_split.set_end_child(self.layers_expander)

        self.layer_list = Gtk.ListBox()
        self.layer_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.layer_list.set_valign(Gtk.Align.START)
        self.layer_list.connect("row-selected", self._on_layer_selected)
        # Drop target for layer reorder (document indices carried as int64)
        drop = Gtk.DropTarget.new(GObject.TYPE_INT64, Gdk.DragAction.MOVE)
        drop.connect("drop", self._on_layer_drop)
        self.layer_list.add_controller(drop)
        self.layer_scroll = Gtk.ScrolledWindow(vexpand=False, hexpand=True)
        self.layer_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.layer_scroll.set_min_content_height(48)
        self.layer_scroll.set_child(self.layer_list)
        layers_body.append(self.layer_scroll)
        side_btns = Gtk.Box(spacing=6)
        add_b = Gtk.Button(label="+")
        add_b.connect("clicked", lambda *_: self.action_add_layer(None, None))
        del_b = Gtk.Button(label="−")
        del_b.connect("clicked", lambda *_: self.action_delete_layer(None, None))
        side_btns.append(add_b)
        side_btns.append(del_b)
        layers_body.append(side_btns)

        self.layers_expander.connect(
            "notify::expanded",
            self._on_sidebar_section_expanded,
            self.layer_scroll,
        )

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

    def _on_sidebar_height_notify(self, *_a) -> None:
        if not self._sidebar_sized:
            self._apply_default_sidebar_split()

    def _apply_default_sidebar_split(self) -> bool:
        """Give Frames a smaller share so Layers (and +/−) stay usable."""
        if self._sidebar_sized:
            return False
        split = self._sidebar_split
        h = split.get_height()
        if h < 160:
            return False
        # ~38% frames / 62% layers — layers need room for opacity rows + buttons
        split.set_position(max(120, int(h * 0.38)))
        self._sidebar_sized = True
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
        elif category == "fonts":
            if getattr(self._active_tool(), "uses_type_options", False):
                self._refresh_font_dropdown(
                    select_path=getattr(self._active_tool(), "font_path", None)
                )

    def _on_left_tab_switched(self, _nb: Gtk.Notebook, _page, page_num: int) -> None:
        # Libraries tab index 1 — refresh pattern/font lists when visiting Tools again
        if page_num == 0:
            tool = self._active_tool()
            if getattr(tool, "uses_fill_options", False):
                self._refresh_pattern_dropdown(select_path=getattr(tool, "pattern_path", None))
            if getattr(tool, "uses_type_options", False):
                self._refresh_font_dropdown(select_path=getattr(tool, "font_path", None))

    def _on_color(self, btn: ColorSelectButton) -> None:
        if self._syncing_tool_ui:
            return
        c = btn.get_rgba()
        self._active_tool().color = (
            int(c.red * 255),
            int(c.green * 255),
            int(c.blue * 255),
            int(c.alpha * 255),
        )
        self._refresh_type_preview()
        self.app_settings.recent_colors = btn.get_recent_colors()
        save_settings(self.app_settings)

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
        self._set_status()

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

    def _apply_layer_transform(self, op) -> None:
        if not self.document:
            return
        self._push_history()
        op(self.document)
        self.canvas.renderer.invalidate()
        self.canvas.queue_render()
        self._set_status()

    def action_mirror_horizontal(self, *_a) -> None:
        self._apply_layer_transform(lambda d: d.mirror_active_layer(horizontal=True))

    def action_mirror_vertical(self, *_a) -> None:
        self._apply_layer_transform(lambda d: d.mirror_active_layer(horizontal=False))

    def action_flip_horizontal(self, *_a) -> None:
        self._apply_layer_transform(lambda d: d.flip_active_layer(horizontal=True))

    def action_flip_vertical(self, *_a) -> None:
        self._apply_layer_transform(lambda d: d.flip_active_layer(horizontal=False))

    def action_rotate_cw(self, *_a) -> None:
        self._apply_layer_transform(lambda d: d.rotate_active_layer(3))

    def action_rotate_ccw(self, *_a) -> None:
        self._apply_layer_transform(lambda d: d.rotate_active_layer(1))

    def action_rotate_180(self, *_a) -> None:
        self._apply_layer_transform(lambda d: d.rotate_active_layer(2))

    def action_effect_pixelate(self, *_a) -> None:
        self._open_effect_dialog(
            title="Pixelate",
            blurb="Average the active layer into square color blocks.",
            options=[
                EffectOption("block_size", "Block size", "spin", 8, minimum=2, maximum=128, step=1),
            ],
            effect_fn=lambda src, p: pixelate(src, int(p["block_size"])),
            debounce_ms=40,
        )

    def action_effect_kuwahara(self, *_a) -> None:
        self._open_effect_dialog(
            title="Kuwahara",
            blurb="Edge-preserving paint-like blur on the active layer.",
            options=[
                EffectOption("radius", "Radius", "spin", 3, minimum=1, maximum=12, step=1),
            ],
            effect_fn=lambda src, p: kuwahara(src, int(p["radius"])),
            debounce_ms=80,
            preview_max_side=720,
        )

    def action_effect_gaussian_blur(self, *_a) -> None:
        self._open_effect_dialog(
            title="Gaussian Blur",
            blurb="Soft Gaussian blur on the active layer (all channels).",
            options=[
                EffectOption("radius", "Radius", "spin", 3, minimum=1, maximum=64, step=1),
            ],
            effect_fn=lambda src, p: gaussian_blur(src, int(p["radius"])),
            debounce_ms=80,
            preview_max_side=720,
        )

    def action_effect_dither(self, *_a) -> None:
        self._open_effect_dialog(
            title="Dither",
            blurb="Reduce color depth with Floyd–Steinberg, ordered, or hard threshold dithering.",
            options=[
                EffectOption(
                    "mode",
                    "Mode",
                    "choice",
                    "Floyd–Steinberg",
                    choices=DITHER_MODES,
                ),
                EffectOption("levels", "Levels", "spin", 2, minimum=2, maximum=16, step=1),
            ],
            effect_fn=lambda src, p: dither(src, mode=str(p["mode"]), levels=int(p["levels"])),
            debounce_ms=60,
        )

    def action_effect_posterize(self, *_a) -> None:
        self._open_effect_dialog(
            title="Posterize",
            blurb="Clamp each RGB channel to a fixed number of tonal steps.",
            options=[
                EffectOption("levels", "Levels", "spin", 4, minimum=2, maximum=32, step=1),
            ],
            effect_fn=lambda src, p: posterize(src, int(p["levels"])),
            debounce_ms=40,
        )

    def action_effect_liquify(self, *_a) -> None:
        self._open_effect_dialog(
            title="Liquify",
            blurb="Warp the whole active layer from a grid of origins (swirl, pinch, or bulge).",
            options=[
                EffectOption(
                    "mode",
                    "Mode",
                    "choice",
                    "Swirl",
                    choices=LIQUIFY_MODES,
                ),
                EffectOption("strength", "Strength", "spin", 50, minimum=0, maximum=100, step=1),
                EffectOption("grid", "Origins grid", "spin", 3, minimum=1, maximum=8, step=1),
                EffectOption("radius_pct", "Radius %", "spin", 100, minimum=20, maximum=150, step=5),
            ],
            effect_fn=lambda src, p: liquify(
                src,
                mode=str(p["mode"]),
                strength=float(p["strength"]),
                grid=int(p["grid"]),
                radius_pct=float(p["radius_pct"]),
            ),
            debounce_ms=50,
            preview_max_side=1280,
        )

    def _open_effect_dialog(
        self,
        *,
        title: str,
        blurb: str,
        options: list,
        effect_fn,
        debounce_ms: int = 60,
        preview_max_side: int | None = None,
    ) -> None:
        if not self.document or self._effect_session is not None:
            return
        ly = self.document.active_layer
        ly.apply_offset()
        snapshot = ly.pixels.copy()
        sel_mask = None
        if self.document.selection.active and self.document.selection.mask is not None:
            sel_mask = self.document.selection.mask.copy()

        self._push_history()
        self._effect_session = {
            "snapshot": snapshot,
            "layer_id": ly.id,
            "selection": sel_mask,
            "effect_fn": effect_fn,
            "preview_max_side": preview_max_side,
            "last_params": None,
        }

        def on_preview(params: dict) -> None:
            self._preview_effect(params, final=False)

        dlg = EffectPreviewDialog(
            self,
            title,
            blurb,
            options,
            on_preview,
            debounce_ms=debounce_ms,
        )
        dlg.connect_response(self._on_effect_dialog_response)
        dlg.present()

    def _run_effect(self, session: dict, params: dict, *, final: bool) -> np.ndarray:
        src = session["snapshot"]
        effect_fn = session["effect_fn"]
        max_side = session.get("preview_max_side")
        h, w = src.shape[:2]
        if final or not max_side or max(h, w) <= max_side:
            return effect_fn(src, params)

        scale = max_side / float(max(h, w))
        small_h = max(1, int(round(h * scale)))
        small_w = max(1, int(round(w * scale)))
        ys = (np.linspace(0, h - 1, small_h)).astype(np.int32)
        xs = (np.linspace(0, w - 1, small_w)).astype(np.int32)
        small = src[ys][:, xs]
        preview_params = dict(params)
        if "radius" in preview_params:
            preview_params["radius"] = max(1, int(round(int(preview_params["radius"]) * scale)))
        if "block_size" in preview_params:
            preview_params["block_size"] = max(1, int(round(int(preview_params["block_size"]) * scale)))
        small_out = effect_fn(small, preview_params)
        y_up = (np.linspace(0, small_h - 1, h)).astype(np.int32)
        x_up = (np.linspace(0, small_w - 1, w)).astype(np.int32)
        return np.ascontiguousarray(small_out[y_up][:, x_up])

    def _preview_effect(self, params: dict, *, final: bool = False) -> None:
        session = self._effect_session
        if not session or not self.document:
            return
        ly = self.document.active_layer
        if ly.id != session["layer_id"]:
            return
        session["last_params"] = dict(params)
        effected = self._run_effect(session, params, final=final)
        blended = blend_effect(session["snapshot"], effected, session["selection"])
        np.copyto(ly.pixels, blended)
        ly.bump()
        self.document.dirty = True
        self.canvas.renderer.invalidate(ly.id)
        self.canvas.queue_render()
        self._set_status()

    def _on_effect_dialog_response(self, dlg: EffectPreviewDialog, response: int) -> None:
        session = self._effect_session
        self._effect_session = None
        if not self.document or session is None:
            return
        ly = self.document.active_layer
        if response == Gtk.ResponseType.OK:
            params = dlg.params()
            # Re-apply at full resolution (preview may have been downscaled).
            self._effect_session = session
            self._preview_effect(params, final=True)
            self._effect_session = None
            return
        # Cancel: restore original pixels and drop the undo entry pushed on open.
        if ly.id == session["layer_id"]:
            np.copyto(ly.pixels, session["snapshot"])
            ly.bump()
            self.canvas.renderer.invalidate(ly.id)
            self.canvas.queue_render()
        self.history.discard_last_push()
        self._set_status()

    def _new_document(self, w: int, h: int) -> None:
        self.action_stop_animation()
        doc = Document.blank(
            w,
            h,
            dpi=self.app_settings.default_dpi,
            color_depth=self.app_settings.default_color_depth,
        )
        hist = History(max_steps=self.app_settings.history_steps)
        self._add_tab(doc, hist, fit=True)

    def _tab_display_name(self, doc: Optional[Document]) -> str:
        if doc and doc.path:
            return doc.path.name
        return "untitled.inkobold"

    def _tab_is_unsaved(self, doc: Optional[Document]) -> bool:
        if not doc:
            return False
        return (not doc.path) or bool(doc.dirty)

    def _tab_title_text(self, doc: Document) -> tuple[str, str, str]:
        name = self._tab_display_name(doc)
        unsaved = self._tab_is_unsaved(doc)
        state = "Unsaved" if unsaved else "Saved"
        mark = "*" if unsaved else ""
        label = f"{name}{mark}  ·  {state}"
        tip_path = str(doc.path) if doc.path else name
        tip = f"{tip_path}\n{state}"
        return label, tip, f"{name}{mark}"

    def _stash_active_view(self) -> None:
        if not (0 <= self._active_tab < len(self._tabs)):
            return
        tab = self._tabs[self._active_tab]
        r = self.canvas.renderer
        tab.zoom = float(r.zoom)
        tab.pan_x = float(r.pan_x)
        tab.pan_y = float(r.pan_y)

    def _add_tab(self, document: Document, history: History, *, fit: bool = True) -> None:
        self.action_stop_animation()
        self._stash_active_view()
        tab = _DocTab(document=document, history=history)
        self._tabs.append(tab)
        self._build_tab_widget(tab, len(self._tabs) - 1)
        self._activate_tab(len(self._tabs) - 1, fit=fit)

    def _build_tab_widget(self, tab: _DocTab, index: int) -> None:
        root = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER)
        root.add_css_class("doc-tab")
        root.set_overflow(Gtk.Overflow.HIDDEN)

        label = Gtk.Label(xalign=0.5, hexpand=True)
        label.add_css_class("doc-title")
        label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        label.set_max_width_chars(28)
        label.set_selectable(False)
        # Click target covers the title area; close button is separate.
        hit = Gtk.Box(hexpand=True)
        hit.append(label)
        root.append(hit)

        close = Gtk.Button(label="×")
        close.set_tooltip_text("Close canvas")
        close.add_css_class("doc-tab-close")
        close.set_has_frame(False)
        close.set_focus_on_click(False)
        close.connect("clicked", lambda *_a, i=index: self._request_close_tab(i))
        root.append(close)

        click = Gtk.GestureClick.new()
        click.set_button(1)
        click.connect("released", lambda *_a, i=index: self._on_tab_clicked(i))
        hit.add_controller(click)

        tab.root = root
        tab.label = label
        self.doc_tabs.append(root)
        self._refresh_tab_label(tab)

    def _reindex_tab_widgets(self) -> None:
        """Rebuild click/close callbacks after tabs are inserted/removed."""
        while (child := self.doc_tabs.get_first_child()) is not None:
            self.doc_tabs.remove(child)
        for i, tab in enumerate(self._tabs):
            self._build_tab_widget(tab, i)
        self._refresh_tab_styles()

    def _on_tab_clicked(self, index: int) -> None:
        if index == self._active_tab:
            return
        self._activate_tab(index, fit=False)

    def _activate_tab(self, index: int, *, fit: bool = False) -> None:
        if not (0 <= index < len(self._tabs)):
            return
        if self._effect_session is not None and index != self._active_tab:
            return
        self.action_stop_animation()
        if index != self._active_tab:
            self._stash_active_view()
        self._active_tab = index
        tab = self._tabs[index]
        self.document = tab.document
        self.history = tab.history
        self.canvas.renderer.invalidate()
        if fit:
            self.canvas.request_fit()
        else:
            r = self.canvas.renderer
            r.zoom = tab.zoom
            r.pan_x = tab.pan_x
            r.pan_y = tab.pan_y
        self._sync_anim_controls_from_doc()
        self._rebuild_frames()
        self._rebuild_layers()
        self._refresh_tab_styles()
        self._set_status()
        self.canvas.queue_render()

    def _refresh_tab_label(self, tab: _DocTab) -> None:
        if tab.label is None:
            return
        text, tip, _ = self._tab_title_text(tab.document)
        tab.label.set_text(text)
        tab.label.set_tooltip_text(tip)
        if tab.root is not None:
            tab.root.set_tooltip_text(tip)

    def _refresh_tab_styles(self) -> None:
        for i, tab in enumerate(self._tabs):
            if tab.root is None:
                continue
            if i == self._active_tab:
                tab.root.add_css_class("active")
            else:
                tab.root.remove_css_class("active")
            self._refresh_tab_label(tab)

    def _request_close_tab(self, index: int) -> None:
        if not (0 <= index < len(self._tabs)):
            return
        if self._effect_session is not None:
            return
        # Resolve by current list order: close buttons capture index at build time,
        # so reindex after mutations. Prefer matching via widget if stale.
        if index >= len(self._tabs):
            return
        tab = self._tabs[index]
        if self._tab_is_unsaved(tab.document):
            self._activate_tab(index, fit=False)
            self._closing_tab_index = index
            self._quit_dialog_open = True
            name = self._tab_display_name(tab.document)
            dlg = QuitConfirmDialog(self, name)
            # Retitle for close-tab context
            dlg.set_title("Close Canvas")
            dlg.connect_response(self._on_close_tab_response)
            dlg.present()
            return
        self._close_tab(index)

    def _on_close_tab_response(self, _dlg: QuitConfirmDialog, response: int) -> None:
        self._quit_dialog_open = False
        index = self._closing_tab_index
        self._closing_tab_index = None
        if index is None or not (0 <= index < len(self._tabs)):
            return
        if response == Gtk.ResponseType.CANCEL:
            return
        if response == Gtk.ResponseType.NO:
            self._close_tab(index)
            return
        if response == Gtk.ResponseType.YES:
            self._activate_tab(index, fit=False)
            self._closing_tab_index = index
            self._quit_after_save = False
            if self.document and self.document.path and self.document.path.suffix.lower() in {
                ".inkobold",
                ".scribbler",
            }:
                self.document.save()
                self._set_status()
                self._close_tab(index)
                return
            dialog = Gtk.FileDialog(title="Save File")
            dialog.set_initial_name("drawing.inkobold")
            dialog.save(self, None, self._on_save_done_close_tab)

    def _on_save_done_close_tab(self, dialog: Gtk.FileDialog, result) -> None:
        index = self._closing_tab_index
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            self._closing_tab_index = None
            return
        if not file or not self.document:
            self._closing_tab_index = None
            return
        self.document.save(Path(file.get_path()))
        self._set_status()
        self._closing_tab_index = None
        if index is not None:
            self._close_tab(index)

    def _close_tab(self, index: int) -> None:
        if not (0 <= index < len(self._tabs)):
            return
        self.action_stop_animation()
        was_active = index == self._active_tab
        if was_active:
            self._stash_active_view()
        self._tabs.pop(index)
        if not self._tabs:
            self._active_tab = -1
            self.document = None
            self.history = History(max_steps=self.app_settings.history_steps)
            self.canvas.renderer.invalidate()
            self._rebuild_frames()
            self._rebuild_layers()
            self._reindex_tab_widgets()
            self._set_status()
            self.canvas.queue_render()
            return
        next_index = self._active_tab
        if index < self._active_tab:
            next_index = self._active_tab - 1
        elif was_active:
            next_index = min(index, len(self._tabs) - 1)
        self._reindex_tab_widgets()
        if was_active:
            self._active_tab = -1
            self._activate_tab(next_index, fit=False)
        else:
            self._active_tab = next_index
            self._refresh_tab_styles()
            self._set_status()

    def _first_unsaved_tab_index(self) -> Optional[int]:
        for i, tab in enumerate(self._tabs):
            if self._tab_is_unsaved(tab.document):
                return i
        return None

    def _sync_anim_controls_from_doc(self) -> None:
        if not self.document:
            return
        self._syncing_frames = True
        try:
            self.fps_spin.set_value(float(self.document.fps))
            self.onion_check.set_active(bool(self.document.onion_skin))
        finally:
            self._syncing_frames = False

    def _on_sidebar_section_expanded(self, expander: Gtk.Expander, _pspec, scroll: Gtk.ScrolledWindow) -> None:
        """Collapse frees vertical space in the Frames|Layers paned."""
        expanded = bool(expander.get_expanded())
        expander.set_vexpand(expanded)
        scroll.set_vexpand(expanded)
        # Let the other section claim the freed space.
        other = self.layers_expander if expander is self.frames_expander else self.frames_expander
        if expanded and other.get_expanded():
            # Both open — keep sharing; re-apply a balanced split once laid out.
            self._sidebar_sized = False
            GLib.idle_add(self._apply_default_sidebar_split)
        elif not expanded and other.get_expanded():
            other.set_vexpand(True)

    def _rebuild_frames(self) -> None:
        self._syncing_frames = True
        try:
            while (child := self.frame_list.get_row_at_index(0)) is not None:
                self.frame_list.remove(child)
            if not self.document:
                return
            for i, fr in enumerate(self.document.frames):
                row = Gtk.ListBoxRow()
                row.set_activatable(True)
                row.set_vexpand(False)
                top = Gtk.Box(spacing=6, margin_top=2, margin_bottom=2, margin_start=4, margin_end=4)
                grip = Gtk.Label(label="≡", xalign=0.5)
                grip.add_css_class("dim-label")
                grip.set_tooltip_text("Drag to reorder")
                top.append(grip)
                label = Gtk.Label(label=f"{i + 1}. {fr.name}", xalign=0, hexpand=True)
                label.set_tooltip_text("Drag to reorder")
                top.append(label)
                row.set_child(top)
                row._frame_index = i  # type: ignore[attr-defined]
                self._attach_frame_drag(top, i)
                self.frame_list.append(row)
                if i == self.document.current_frame_index:
                    self.frame_list.select_row(row)
        finally:
            self._syncing_frames = False

    def _attach_frame_drag(self, widget: Gtk.Widget, frame_index: int) -> None:
        source = Gtk.DragSource.new()
        source.set_actions(Gdk.DragAction.MOVE)

        def _prepare(_src, _x, _y, idx=frame_index):
            self._drag_frame_from = idx
            val = GObject.Value(GObject.TYPE_INT64, idx)
            return Gdk.ContentProvider.new_for_value(val)

        def _end(_src, _drag, _delete, *_a):
            self._drag_frame_from = None

        source.connect("prepare", _prepare)
        source.connect("drag-end", _end)
        widget.add_controller(source)

    def _on_frame_drop(self, _target, value, x, y) -> bool:
        if not self.document or self._anim_playing:
            return False
        # Only accept drags that started on a frame row (not a layer).
        from_idx = self._drag_frame_from
        if from_idx is None:
            return False
        try:
            if value is not None:
                from_idx = int(value)
        except (TypeError, ValueError):
            pass
        dest_row = None
        picked = self.frame_list.pick(x, y, Gtk.PickFlags.DEFAULT)
        w = picked
        while w is not None:
            if isinstance(w, Gtk.ListBoxRow):
                dest_row = w
                break
            w = w.get_parent()
        if dest_row is None:
            dest_row = self.frame_list.get_row_at_y(int(y))
        if dest_row is None:
            return False
        to_idx = getattr(dest_row, "_frame_index", None)
        if to_idx is None or to_idx == from_idx:
            return False
        self._push_history()
        self.document.move_frame(int(from_idx), int(to_idx))
        self.canvas.renderer.invalidate()
        self._rebuild_frames()
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()
        return True

    def _on_frame_selected(self, _lb, row: Optional[Gtk.ListBoxRow]) -> None:
        if self._syncing_frames or row is None or not self.document:
            return
        if self._anim_playing:
            return
        idx = getattr(row, "_frame_index", 0)
        if idx == self.document.current_frame_index:
            return
        self.document.set_current_frame(int(idx))
        self.canvas.renderer.invalidate()
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()

    def _on_fps_changed(self, spin: Gtk.SpinButton) -> None:
        if self._syncing_frames or not self.document:
            return
        self.document.fps = float(spin.get_value())
        self.document.dirty = True
        if self._anim_playing:
            self._restart_anim_timer()

    def _on_onion_toggled(self, btn: Gtk.CheckButton) -> None:
        if self._syncing_frames or not self.document:
            return
        self.document.onion_skin = bool(btn.get_active())
        self.document.dirty = True
        self.canvas.queue_render()

    def _goto_frame(self, index: int, *, rebuild_list: bool = True) -> None:
        if not self.document:
            return
        self.document.set_current_frame(index)
        self.canvas.renderer.invalidate()
        if rebuild_list:
            self._rebuild_frames()
        else:
            self._syncing_frames = True
            try:
                row = self.frame_list.get_row_at_index(index)
                if row is not None:
                    self.frame_list.select_row(row)
            finally:
                self._syncing_frames = False
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()

    def action_add_frame(self, *_a) -> None:
        if not self.document:
            return
        self.action_stop_animation()
        self._push_history()
        self.document.add_frame(duplicate=False)
        self.canvas.renderer.invalidate()
        self._rebuild_frames()
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()

    def action_duplicate_frame(self, *_a) -> None:
        if not self.document:
            return
        self.action_stop_animation()
        self._push_history()
        self.document.add_frame(duplicate=True)
        self.canvas.renderer.invalidate()
        self._rebuild_frames()
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()

    def action_delete_frame(self, *_a) -> None:
        if not self.document or self.document.frame_count <= 1:
            return
        self.action_stop_animation()
        self._push_history()
        self.document.delete_frame()
        self.canvas.renderer.invalidate()
        self._rebuild_frames()
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()

    def action_play_animation(self, *_a) -> None:
        """Start playback, or pause (stop) if already playing."""
        if not self.document or self.document.frame_count < 1:
            return
        if self._anim_playing:
            self.action_stop_animation()
            return
        self._anim_playing = True
        self.canvas.animation_playing = True
        self._restart_anim_timer()
        self.canvas.queue_render()
        self._set_status()

    def action_stop_animation(self, *_a) -> None:
        self._anim_playing = False
        self.canvas.animation_playing = False
        if self._anim_timer_id is not None:
            GLib.source_remove(self._anim_timer_id)
            self._anim_timer_id = None
        self.canvas.queue_render()
        self._set_status()

    def _restart_anim_timer(self) -> None:
        if self._anim_timer_id is not None:
            GLib.source_remove(self._anim_timer_id)
            self._anim_timer_id = None
        if not self._anim_playing or not self.document:
            return
        fps = max(1.0, float(self.document.fps))
        interval_ms = max(1, int(round(1000.0 / fps)))
        self._anim_timer_id = GLib.timeout_add(interval_ms, self._anim_tick)

    def _anim_tick(self) -> bool:
        if not self._anim_playing or not self.document:
            self._anim_timer_id = None
            return False
        n = self.document.frame_count
        if n <= 0:
            return True
        nxt = (self.document.current_frame_index + 1) % n
        self._goto_frame(nxt, rebuild_list=False)
        return True

    def _rebuild_layers(self) -> None:
        self._dismiss_layer_context_menu()
        self._layer_rename_entry = None
        self._layer_rename_done = True
        self._syncing_layers = True
        try:
            while (child := self.layer_list.get_row_at_index(0)) is not None:
                self.layer_list.remove(child)
            if not self.document:
                return
            # Show top layer first
            for i, ly in enumerate(reversed(self.document.layers)):
                idx = len(self.document.layers) - 1 - i
                row = Gtk.ListBoxRow()
                row.set_activatable(True)
                row.set_vexpand(False)
                col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=4, margin_bottom=4, margin_start=6, margin_end=6)
                col.set_vexpand(False)

                top = Gtk.Box(spacing=6)
                grip = Gtk.Label(label="≡", xalign=0.5)
                grip.add_css_class("dim-label")
                grip.set_tooltip_text("Drag to reorder")
                top.append(grip)
                vis = Gtk.CheckButton()
                vis.set_active(ly.visible)
                vis.set_tooltip_text("Visible")
                vis.connect("toggled", self._toggle_vis, idx)
                top.append(vis)
                name_slot = Gtk.Box(hexpand=True)
                name = Gtk.Label(label=ly.name, xalign=0, hexpand=True)
                name.set_tooltip_text("Double-click to rename")
                name_slot.append(name)
                top.append(name_slot)
                col.append(top)

                opac = Gtk.Box(spacing=4)
                opac.append(Gtk.Label(label="Opacity", xalign=0))
                scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
                scale.set_value(max(0, min(100, ly.opacity * 100.0)))
                scale.set_draw_value(True)
                scale.set_value_pos(Gtk.PositionType.RIGHT)
                scale.set_hexpand(True)
                scale.set_vexpand(False)
                scale.set_valign(Gtk.Align.CENTER)
                scale.set_digits(0)
                scale.set_tooltip_text("Layer transparency")
                scale.connect("value-changed", self._on_layer_opacity, idx)
                # Arm history once per slider interaction (mouse or keyboard)
                scale.connect("change-value", self._on_opacity_change_value)
                opac.append(scale)
                col.append(opac)

                row.set_child(col)
                row._layer_index = idx  # type: ignore[attr-defined]
                row._name_slot = name_slot  # type: ignore[attr-defined]
                row._name_label = name  # type: ignore[attr-defined]
                # Drag from name row only so the opacity slider stays usable
                self._attach_layer_drag(top, idx)
                self._attach_layer_rename(name, row)
                self._attach_layer_context_menu(row)
                self.layer_list.append(row)
                if idx == self.document.active_layer_index:
                    self.layer_list.select_row(row)
        finally:
            self._syncing_layers = False
            self._opacity_hist_armed = False

    def _layer_context_menu_model(self) -> Gio.Menu:
        menu = getattr(self, "_layer_context_menu", None)
        if menu is None:
            menu = Gio.Menu()
            menu.append("Rename Layer…", "win.rename_layer")
            menu.append("Merge with Layer Below", "win.merge_layer_down")
            menu.append("Merge All", "win.merge_all_layers")
            self._layer_context_menu = menu
        return menu

    def _dismiss_layer_context_menu(self) -> None:
        popover = getattr(self, "_layer_context_popover", None)
        if popover is None:
            return
        popover.popdown()
        popover.unparent()
        self._layer_context_popover = None

    def _attach_layer_context_menu(self, row: Gtk.ListBoxRow) -> None:
        click = Gtk.GestureClick.new()
        click.set_button(Gdk.BUTTON_SECONDARY)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def _pressed(gesture: Gtk.GestureClick, n_press: int, x: float, y: float, r=row) -> None:
            if n_press != 1:
                return
            self.layer_list.select_row(r)
            self._dismiss_layer_context_menu()
            popover = Gtk.PopoverMenu.new_from_model(self._layer_context_menu_model())
            popover.set_parent(r)
            popover.set_has_arrow(False)
            rect = Gdk.Rectangle()
            rect.x = int(x)
            rect.y = int(y)
            rect.width = 1
            rect.height = 1
            popover.set_pointing_to(rect)
            self._layer_context_popover = popover
            popover.popup()
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

        click.connect("pressed", _pressed)
        row.add_controller(click)

    def _attach_layer_rename(self, label: Gtk.Label, row: Gtk.ListBoxRow) -> None:
        click = Gtk.GestureClick.new()
        click.set_button(Gdk.BUTTON_PRIMARY)

        def _released(gesture: Gtk.GestureClick, n_press: int, _x: float, _y: float, r=row) -> None:
            if n_press != 2:
                return
            self.layer_list.select_row(r)
            self._begin_layer_rename(r)
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

        click.connect("released", _released)
        label.add_controller(click)

    def _begin_layer_rename(self, row: Gtk.ListBoxRow) -> None:
        if not self.document or getattr(self, "_layer_rename_entry", None) is not None:
            return
        idx = getattr(row, "_layer_index", None)
        slot = getattr(row, "_name_slot", None)
        if idx is None or slot is None or not (0 <= idx < len(self.document.layers)):
            return
        self._dismiss_layer_context_menu()
        while (child := slot.get_first_child()) is not None:
            slot.remove(child)
        entry = Gtk.Entry()
        entry.set_text(self.document.layers[idx].name)
        entry.set_hexpand(True)
        entry.set_width_chars(8)
        self._layer_rename_entry = entry
        self._layer_rename_index = idx
        self._layer_rename_done = False

        def _finish(cancelled: bool = False) -> None:
            if self._layer_rename_done:
                return
            self._layer_rename_done = True
            self._layer_rename_entry = None
            rename_idx = self._layer_rename_index
            text = entry.get_text().strip()
            if (
                not cancelled
                and self.document
                and 0 <= rename_idx < len(self.document.layers)
                and text
                and text != self.document.layers[rename_idx].name
            ):
                self._push_history()
                self.document.layers[rename_idx].name = text
                self.document.dirty = True
                self.document._sync_frame_from_layers()
            self._rebuild_layers()
            self._set_status()

        entry.connect("activate", lambda *_: _finish(False))
        key = Gtk.EventControllerKey.new()

        def _on_key(_ctrl, keyval, _keycode, _state):
            if keyval == Gdk.KEY_Escape:
                _finish(True)
                return True
            return False

        key.connect("key-pressed", _on_key)
        entry.add_controller(key)

        def _on_focus_leave(_ctrl):
            GLib.idle_add(lambda: (_finish(False), False)[1])

        focus = Gtk.EventControllerFocus.new()
        focus.connect("leave", _on_focus_leave)
        entry.add_controller(focus)

        slot.append(entry)
        entry.grab_focus()
        entry.select_region(0, -1)

    def action_rename_layer(self, *_a) -> None:
        if not self.document:
            return
        row = self.layer_list.get_selected_row()
        if row is None:
            return
        self._begin_layer_rename(row)

    def _attach_layer_drag(self, widget: Gtk.Widget, layer_index: int) -> None:
        source = Gtk.DragSource.new()
        source.set_actions(Gdk.DragAction.MOVE)

        def _prepare(_src, _x, _y, idx=layer_index):
            self._drag_layer_from = idx
            val = GObject.Value(GObject.TYPE_INT64, idx)
            return Gdk.ContentProvider.new_for_value(val)

        def _end(_src, _drag, _delete, *_a):
            self._drag_layer_from = None

        source.connect("prepare", _prepare)
        source.connect("drag-end", _end)
        widget.add_controller(source)

    def _on_layer_drop(self, _target, value, x, y) -> bool:
        if not self.document:
            return False
        # Only accept drags that started on a layer row (not a frame).
        from_idx = self._drag_layer_from
        if from_idx is None:
            return False
        try:
            if value is not None:
                from_idx = int(value)
        except (TypeError, ValueError):
            pass
        # Find destination row under pointer
        dest_row = None
        picked = self.layer_list.pick(x, y, Gtk.PickFlags.DEFAULT)
        w = picked
        while w is not None:
            if isinstance(w, Gtk.ListBoxRow):
                dest_row = w
                break
            w = w.get_parent()
        if dest_row is None:
            dest_row = self.layer_list.get_row_at_y(int(y))
        if dest_row is None:
            return False
        to_idx = getattr(dest_row, "_layer_index", None)
        if to_idx is None or to_idx == from_idx:
            return False
        self._push_history()
        self.document.move_layer(int(from_idx), int(to_idx))
        self.canvas.renderer.invalidate()
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()
        return True

    def _on_opacity_change_value(self, _scale, _scroll, _value) -> bool:
        self._opacity_hist_armed = True
        return False

    def _on_layer_opacity(self, scale: Gtk.Scale, index: int) -> None:
        if self._syncing_layers or not self.document:
            return
        if not (0 <= index < len(self.document.layers)):
            return
        layer = self.document.layers[index]
        new_op = max(0.0, min(1.0, scale.get_value() / 100.0))
        if abs(layer.opacity - new_op) < 0.0005:
            return
        if self._opacity_hist_armed:
            self._push_history()
            self._opacity_hist_armed = False
        layer.opacity = new_op
        self.document.dirty = True
        self.canvas.queue_render()
        self._set_status()

    def _toggle_vis(self, btn: Gtk.CheckButton, index: int) -> None:
        if self._syncing_layers or not self.document:
            return
        layer = self.document.layers[index]
        if layer.visible == btn.get_active():
            return
        self._push_history()
        layer.visible = btn.get_active()
        self.canvas.renderer.invalidate(layer.id)
        self.canvas.queue_render()
        self._set_status()

    def _on_layer_selected(self, _lb, row: Optional[Gtk.ListBoxRow]) -> None:
        if row is None or not self.document:
            return
        self.document.active_layer_index = getattr(row, "_layer_index", 0)
        self.document._sync_frame_from_layers()
        self._set_status()

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
            self.set_title("Inkobold")
            return
        _text, _tip, marked = self._tab_title_text(self.document)
        self.set_title(f"Inkobold — {marked}")

    def _set_status(self) -> None:
        self._update_doc_title()
        if not self.document:
            self.status.set_text(self.input_hub.status)
            return
        d = self.document
        path = d.path.name if d.path else "untitled.inkobold"
        dirty = "*" if d.dirty else ""
        ly = d.active_layer
        gl = "GLES" if self.canvas.renderer.es else "GL"
        if not self.canvas.renderer.ready:
            gl = "GL-FAIL"
        depth_label = {
            64: "16bpc",
            32: "8bpc",
            24: "RGB8",
            8: "Gray8",
        }.get(int(d.color_depth), f"{d.color_depth}-bit")
        self.status.set_text(
            f"{path}{dirty}  {d.width}×{d.height}  {d.dpi}dpi  {depth_label}  "
            f"frame={d.current_frame_index + 1}/{d.frame_count}@{d.fps:g}fps  "
            f"layer={ly.name}  tool={self.tool_id}  "
            f"zoom={self.canvas.renderer.zoom:.2f}  {gl}  |  {self.input_hub.status}"
        )

    def _apply_theme_css(self, rgb: tuple[int, int, int] | None = None) -> None:
        if rgb is not None:
            css_text = build_theme_css(*rgb)
        elif self.app_settings.use_custom_theme:
            r, g, b = self.app_settings.theme_rgb
            css_text = build_theme_css(r, g, b)
        else:
            css_text = DEFAULT_CSS
        self._css_provider.load_from_data(css_text.encode("utf-8"))

    def _persist_settings(self) -> None:
        save_settings(self.app_settings)

    def action_theme_color(self, *_a) -> None:
        seed = self.app_settings.theme_rgb if self.app_settings.use_custom_theme else DEFAULT_THEME_RGB
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
        self.app_settings.theme_rgb = DEFAULT_THEME_RGB
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
        for tid, label, _letter, _num in TOOL_ORDER:
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
    def _prompt_startup(self) -> bool:
        dlg = StartupDialog(self)
        dlg.connect_response(self._on_startup_response)
        dlg.present()
        return False

    def _on_startup_response(self, _dlg: StartupDialog, response: int) -> None:
        if response == Gtk.ResponseType.YES:
            dlg = NewFileDialog(self)
            dlg.connect_response(self._on_startup_new_response)
            dlg.present()
            return
        if response == Gtk.ResponseType.NO:
            self._open_at_startup = True
            self.action_open()
            return
        self._do_quit()

    def _on_startup_new_response(self, dlg: NewFileDialog, response: int) -> None:
        if response == Gtk.ResponseType.OK:
            w, h = dlg.size()
            self._new_document(w, h)
            return
        GLib.idle_add(self._prompt_startup)

    # --- actions ---
    def action_new(self, *_a) -> None:
        dlg = NewFileDialog(self)
        dlg.connect_response(self._on_new_response)
        dlg.present()

    def _on_new_response(self, dlg: NewFileDialog, response: int) -> None:
        if response == Gtk.ResponseType.OK:
            w, h = dlg.size()
            self._new_document(w, h)

    def action_open(self, *_a) -> None:
        dialog = Gtk.FileDialog(title="Open File")
        filt = Gtk.FileFilter()
        filt.set_name("Inkobold / Images")
        filt.add_pattern("*.inkobold")
        filt.add_pattern("*.scribbler")
        filt.add_pattern("*.png")
        filt.add_pattern("*.jpg")
        filt.add_pattern("*.jpeg")
        filt.add_pattern("*.webp")
        filt.add_pattern("*.gif")
        dialog.set_default_filter(filt)
        dialog.open(self, None, self._on_open_done)

    def _on_open_done(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            if self._open_at_startup:
                self._open_at_startup = False
                GLib.idle_add(self._prompt_startup)
            return
        if not file:
            if self._open_at_startup:
                self._open_at_startup = False
                GLib.idle_add(self._prompt_startup)
            return
        path = Path(file.get_path())
        self._open_at_startup = False
        self.action_stop_animation()
        doc = Document.open(path)
        hist = History(max_steps=self.app_settings.history_steps)
        self._add_tab(doc, hist, fit=True)

    def action_save(self, *_a) -> None:
        if not self.document:
            return
        if self.document.path and self.document.path.suffix.lower() in {".inkobold", ".scribbler"}:
            self.document.save()
            self._set_status()
            return
        self.action_save_as()

    def action_save_as(self, *_a) -> None:
        dialog = Gtk.FileDialog(title="Save File")
        dialog.set_initial_name("drawing.inkobold")
        dialog.save(self, None, self._on_save_done)

    def _on_save_done(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            self._quit_after_save = False
            return
        if not file or not self.document:
            self._quit_after_save = False
            return
        self.document.save(Path(file.get_path()))
        self._set_status()
        if self._quit_after_save:
            self._quit_after_save = False
            # Continue quit flow for any remaining unsaved tabs.
            self.request_quit()

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

    def request_quit(self, *_a) -> None:
        if self._quit_dialog_open or self._allow_close:
            return
        if not self._tabs:
            self._do_quit()
            return
        unsaved = self._first_unsaved_tab_index()
        if unsaved is None:
            self._do_quit()
            return
        self._activate_tab(unsaved, fit=False)
        self._quit_dialog_open = True
        name = self._doc_display_name()
        dlg = QuitConfirmDialog(self, name)
        dlg.connect_response(self._on_quit_response)
        dlg.present()

    def _on_quit_response(self, _dlg: QuitConfirmDialog, response: int) -> None:
        self._quit_dialog_open = False
        if response == Gtk.ResponseType.CANCEL:
            return
        if response == Gtk.ResponseType.NO:
            self._do_quit()
            return
        if response == Gtk.ResponseType.YES:
            self._save_then_quit()

    def _save_then_quit(self) -> None:
        if not self.document:
            self._do_quit()
            return
        if self.document.path and self.document.path.suffix.lower() in {".inkobold", ".scribbler"}:
            self.document.save()
            self._set_status()
            # More unsaved tabs?
            nxt = self._first_unsaved_tab_index()
            if nxt is None:
                self._do_quit()
            else:
                self.request_quit()
            return
        self._quit_after_save = True
        dialog = Gtk.FileDialog(title="Save File")
        dialog.set_initial_name("drawing.inkobold")
        dialog.save(self, None, self._on_save_done)

    def _do_quit(self) -> None:
        self._allow_close = True
        self.shutdown_input()
        app = self.get_application()
        if app is not None:
            app.quit()
        else:
            self.destroy()

    def action_export(self, *_a) -> None:
        if not self.document:
            return
        dialog = Gtk.FileDialog(title="Export Image")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        for name, patterns in (
            ("PNG image", ("*.png",)),
            ("JPEG image", ("*.jpg", "*.jpeg")),
            ("WebP image", ("*.webp",)),
        ):
            f = Gtk.FileFilter()
            f.set_name(name)
            for p in patterns:
                f.add_pattern(p)
            filters.append(f)
        dialog.set_filters(filters)
        dialog.set_default_filter(filters.get_item(0))
        stem = self.document.path.stem if self.document.path else "drawing"
        dialog.set_initial_name(f"{stem}.png")
        dialog.save(self, None, self._on_export_done)

    def _on_export_done(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            return
        if not file or not self.document:
            return
        path = Path(file.get_path())
        # If the chosen filter implies a type but the name has no/unknown suffix, fix it
        filt = dialog.get_default_filter()
        if filt is not None and path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            name = (filt.get_name() or "").lower()
            if "jpeg" in name or "jpg" in name:
                path = path.with_suffix(".jpg")
            elif "webp" in name:
                path = path.with_suffix(".webp")
            else:
                path = path.with_suffix(".png")
        try:
            self.document.export_image(path)
        except Exception as exc:
            err = Gtk.AlertDialog()
            err.set_message("Export failed")
            err.set_detail(str(exc))
            err.show(self)
            return
        self._set_status()

    def action_export_anim_gif(self, *_a) -> None:
        if not self.document:
            return
        dialog = Gtk.FileDialog(title="Export Animation as GIF")
        filt = Gtk.FileFilter()
        filt.set_name("GIF animation")
        filt.add_pattern("*.gif")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filt)
        dialog.set_filters(filters)
        dialog.set_default_filter(filt)
        stem = self.document.path.stem if self.document.path else "animation"
        dialog.set_initial_name(f"{stem}.gif")
        dialog.save(self, None, self._on_export_anim_gif_done)

    def _on_export_anim_gif_done(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            return
        if not file or not self.document:
            return
        path = Path(file.get_path())
        if path.suffix.lower() != ".gif":
            path = path.with_suffix(".gif")
        try:
            self.document.export_animation_gif(path)
        except Exception as exc:
            err = Gtk.AlertDialog()
            err.set_message("GIF export failed")
            err.set_detail(str(exc))
            err.show(self)
            return
        self._set_status()

    def action_export_anim_png(self, *_a) -> None:
        if not self.document:
            return
        dialog = Gtk.FileDialog(title="Export Animation as PNG Folder")
        dialog.select_folder(self, None, self._on_export_anim_png_done)

    def _on_export_anim_png_done(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if not folder or not self.document:
            return
        path = Path(folder.get_path())
        try:
            self.document.export_animation_png_sequence(path)
        except Exception as exc:
            err = Gtk.AlertDialog()
            err.set_message("PNG sequence export failed")
            err.set_detail(str(exc))
            err.show(self)
            return
        self._set_status()

    def action_export_layers(self, *_a) -> None:
        if not self.document:
            return
        dialog = Gtk.FileDialog(title="Export Separated Layers")
        dialog.select_folder(self, None, self._on_export_layers_done)

    def _on_export_layers_done(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if not folder or not self.document:
            return
        path = Path(folder.get_path())
        try:
            self.document.export_layers_png(path)
        except Exception as exc:
            err = Gtk.AlertDialog()
            err.set_message("Layer export failed")
            err.set_detail(str(exc))
            err.show(self)
            return
        self._set_status()

    def action_import_image(self, *_a) -> None:
        dialog = Gtk.FileDialog(title="Import Image as New Layer")
        filt = Gtk.FileFilter()
        filt.set_name("Images")
        for p in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp", "*.gif"):
            filt.add_pattern(p)
        dialog.set_default_filter(filt)
        dialog.open(self, None, self._on_import_done)

    def _on_import_done(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            return
        if not file or not self.document:
            return
        path = Path(file.get_path())
        img = Image.open(path).convert("RGBA")
        arr = np.array(img, dtype=np.uint8)
        # Fit into document canvas
        h = min(arr.shape[0], self.document.height)
        w = min(arr.shape[1], self.document.width)
        layer_pixels = np.zeros((self.document.height, self.document.width, 4), dtype=np.uint8)
        layer_pixels[:h, :w] = arr[:h, :w]
        self._push_history()
        self.document.add_layer(name=path.stem, fill=layer_pixels)
        self.canvas.renderer.invalidate()
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()

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

    def action_add_layer(self, *_a) -> None:
        if not self.document:
            return
        self._push_history()
        self.document.add_layer()
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()

    def action_delete_layer(self, *_a) -> None:
        if not self.document or len(self.document.layers) <= 1:
            return
        self._push_history()
        self.document.delete_layer(self.document.active_layer_index)
        self.canvas.renderer.invalidate()
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()

    def action_merge_layer_down(self, *_a) -> None:
        if not self.document or self.document.active_layer_index <= 0:
            return
        self._push_history()
        if not self.document.merge_down():
            return
        self.canvas.renderer.invalidate()
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()

    def action_merge_all_layers(self, *_a) -> None:
        if not self.document or len(self.document.layers) <= 1:
            return
        self._push_history()
        if not self.document.merge_all():
            return
        self.canvas.renderer.invalidate()
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()

    def shutdown_input(self) -> None:
        self.action_stop_animation()
        self.input_hub.close()
