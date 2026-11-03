"""Gio actions for ``MainWindow``: the ``win.*`` action table, keyboard shortcuts,
the stateful View toggles (grid, sun, tile preview, pixel art, export scale, …)
and the Settings dialogs (grid, sun, theme, memory, image, shortcuts).

``ActionsMixin`` is mixed into ``MainWindow``. Action names match the ids in
``inkobold.core.shortcuts``; the menu bar (``menus.py``) targets them as
``win.<name>``.
"""

from __future__ import annotations

from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from inkobold.core.document import EXPORT_SCALES
from inkobold.core.settings import default_theme_rgb
from inkobold.core.shortcuts import DEFAULT_SHORTCUTS, SHORTCUT_LABELS
from inkobold.ui.dialogs import (
    CaptureShortcutDialog,
    ColorDepthDialog,
    CropDialog,
    DpiDialog,
    GridOverlayDialog,
    HistoryStepsDialog,
    ShortcutsDialog,
    SunSettingsDialog,
    ThemeColorDialog,
)
from inkobold.ui.tool_options import TOOL_ORDER


class ActionsMixin:
    """Action table, shortcuts, view toggles and settings dialogs for ``MainWindow``."""

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
