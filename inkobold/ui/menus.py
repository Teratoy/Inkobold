"""Menu bar for ``MainWindow``: the Gio.Menu models behind each top-level menu.

``build_menubar`` builds the bar (every item targets a ``win.*`` action registered
by ``actions.py``); ``MenusMixin`` holds the window methods the bar needs: the
toolbar app icon and the Settings → Shortcuts submenu rebuilt on every rebind.
"""

from __future__ import annotations

import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from inkobold.core.document import EXPORT_SCALES
from inkobold.core.flavor import current as current_flavor
from inkobold.core.shortcuts import SHORTCUT_LABELS, SHORTCUT_ORDER, format_accels
from inkobold.ui.doc_tabs import build_doc_tabs


def build_menubar(win) -> Gtk.Box:
    """Top bar: app icon, File / Edit / Effects / View / Layer / Animation /
    Settings menus, the document tabs, and the Reload / Exit buttons."""
    menubar = Gtk.Box(spacing=4, css_classes=["toolbar"])

    menubar.append(win._build_toolbar_app_icon())

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

    win._shortcuts_menu = Gio.Menu()
    settings_menu.append_submenu("Shortcuts", win._shortcuts_menu)
    settings_menu.append("Debug Mode", "win.debug_mode")
    settings_btn.set_menu_model(settings_menu)
    menubar.append(settings_btn)
    win._rebuild_shortcuts_submenu()

    menubar.append(build_doc_tabs(win))

    win.reload_btn = Gtk.Button(label="Reload")
    win.reload_btn.set_tooltip_text(f"Reload {current_flavor().display_name} (debug)")
    win.reload_btn.add_css_class("exit-btn")
    win.reload_btn.set_halign(Gtk.Align.END)
    win.reload_btn.set_visible(bool(win.app_settings.debug_mode))
    win.reload_btn.connect("clicked", win.request_reload)
    menubar.append(win.reload_btn)

    exit_btn = Gtk.Button(label="Exit")
    exit_btn.set_tooltip_text(f"Exit {current_flavor().display_name}")
    exit_btn.add_css_class("exit-btn")
    exit_btn.set_halign(Gtk.Align.END)
    exit_btn.connect("clicked", win.request_quit)
    menubar.append(exit_btn)
    return menubar


class MenusMixin:
    """Menu-bar helpers for ``MainWindow``."""

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
