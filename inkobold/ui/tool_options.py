"""Tools tab: the tool buttons and the per-tool options column.

``build_toolbox_page`` builds the Tools tab content; ``ToolOptionsMixin`` holds
the ``MainWindow`` methods that load a tool's settings into the widgets and push
widget changes back: size / opacity / modes, brush tips, patterns, fonts, the
primary / secondary color pair and palette, Symmetry (mirror), and tool
visibility.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from inkobold.core.effects import LIQUIFY_BRUSH_MODES
from inkobold.core.flavor import current as current_flavor
from inkobold.core.image_meta import to_display_u8
from inkobold.core.libraries import (
    category_dir,
    list_brushes,
    list_fonts,
    list_patterns,
    list_system_fonts,
)
from inkobold.core.mirror import ARRAY_COUNT_MAX, ARRAY_SPACING_MAX, MODES, ORIENTATIONS
from inkobold.core.settings import save_settings
from inkobold.core.shortcuts import format_accels
from inkobold.ui.color_picker import ColorSelectButton, rgba_to_tuple, tuple_to_rgba
from inkobold.ui.dialogs import VisibleToolsDialog, configure_numeric_spin
from inkobold.ui.palette_panel import PalettePanel


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


def build_toolbox_page(win) -> Gtk.Box:
    """Tool buttons, the per-tool options column, colors / palette and the
    Symmetry / Wrap / Pixel box. The window wraps it in the Tools tab."""
    toolbox_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["toolbox"])
    toolbox_page.set_size_request(win._side_panel_w, -1)

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

    win.tool_buttons: dict[str, Gtk.ToggleButton] = {}
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
        btn.connect("toggled", win._on_tool_toggled, tid)
        win.tool_buttons[tid] = btn
        tool_flow.append(btn)

    win._refresh_tool_shortcut_tips()
    win._apply_tool_visibility()

    toolbox_page.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL, margin_top=4, margin_bottom=4))

    opts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    toolbox_page.append(opts)
    _add_stroke_rows(win, opts)
    _add_modes_rows(win, opts)
    _add_pattern_rows(win, opts)
    _add_type_rows(win, opts)
    _add_color_rows(win, opts)
    _add_shading_rows(win, opts)

    _add_symmetry_box(win, toolbox_page)
    return toolbox_page


def _add_stroke_rows(win, opts: Gtk.Box) -> None:
    """Size, Opacity and the Brush mode / tip / density rows."""
    win.size_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    opts.append(win.size_row)
    win.size_label = Gtk.Label(label="Size", xalign=0)
    win.size_row.append(win.size_label)
    win.brush_spin = Gtk.SpinButton.new_with_range(0.1, 256, 1)
    configure_numeric_spin(win.brush_spin)
    win.brush_spin.set_hexpand(True)
    win.brush_spin.connect("value-changed", win._on_brush_changed)
    win.size_row.append(win.brush_spin)

    win.opacity_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
    opts.append(win.opacity_row)
    win.opacity_row.append(Gtk.Label(label="Opacity", xalign=0))
    win.opacity_spin = Gtk.SpinButton.new_with_range(0, 100, 1)
    configure_numeric_spin(win.opacity_spin)
    win.opacity_spin.set_value(100)
    win.opacity_spin.set_hexpand(True)
    win.opacity_spin.set_tooltip_text(
        "Stroke / fill / erase strength. Combines with the color's alpha when painting."
    )
    win.opacity_spin.connect("value-changed", win._on_opacity_changed)
    win.opacity_row.append(win.opacity_spin)

    win.brush_opts_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
    opts.append(win.brush_opts_row)
    win.brush_opts_row.append(Gtk.Label(label="Brush", xalign=0))
    brush_modes = Gtk.StringList.new(["Round", "Custom", "Bubbles"])
    win.brush_mode_dropdown = Gtk.DropDown(model=brush_modes)
    win.brush_mode_dropdown.set_hexpand(True)
    win.brush_mode_dropdown.set_tooltip_text(
        "Round: soft disk. Custom: stamp a tip from Libraries → Brushes (tinted with Color). "
        "Bubbles: milk-style air-bubble clusters tinted with Color."
    )
    win.brush_mode_dropdown.connect("notify::selected", win._on_brush_mode_changed)
    win.brush_opts_row.append(win.brush_mode_dropdown)

    win.brush_image_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=2)
    win.brush_opts_row.append(win.brush_image_row)
    brush_head = Gtk.Box(spacing=4)
    brush_head.append(Gtk.Label(label="Tip", xalign=0, hexpand=True))
    refresh_brush = Gtk.Button(label="↻")
    refresh_brush.set_tooltip_text("Reload brushes from Libraries folder")
    refresh_brush.connect("clicked", lambda *_: win._refresh_brush_dropdown(
        select_path=getattr(win._active_tool(), "brush_path", None)
    ))
    brush_head.append(refresh_brush)
    win.brush_image_row.append(brush_head)
    win._brush_paths: list[Path] = []
    win._brush_thumb_size = 56
    win._brush_preview_size = 40
    win.brush_preview = Gtk.Picture()
    win.brush_preview.set_size_request(win._brush_preview_size, win._brush_preview_size)
    win.brush_preview.set_content_fit(Gtk.ContentFit.COVER)
    win.brush_preview.set_can_shrink(False)
    win.brush_menu_btn = Gtk.MenuButton()
    win.brush_menu_btn.set_hexpand(True)
    win.brush_menu_btn.set_always_show_arrow(True)
    win.brush_menu_btn.set_child(win.brush_preview)
    win.brush_menu_btn.set_tooltip_text("Choose a brush tip")
    brush_popover = Gtk.Popover()
    brush_scroll = Gtk.ScrolledWindow()
    brush_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    brush_scroll.set_min_content_height(168)
    brush_scroll.set_max_content_height(280)
    brush_scroll.set_min_content_width(200)
    win.brush_flow = Gtk.FlowBox()
    win.brush_flow.set_selection_mode(Gtk.SelectionMode.NONE)
    win.brush_flow.set_homogeneous(True)
    win.brush_flow.set_max_children_per_line(4)
    win.brush_flow.set_min_children_per_line(3)
    win.brush_flow.set_column_spacing(4)
    win.brush_flow.set_row_spacing(4)
    win.brush_flow.set_valign(Gtk.Align.START)
    brush_scroll.set_child(win.brush_flow)
    brush_popover.set_child(brush_scroll)
    win.brush_menu_btn.set_popover(brush_popover)
    win.brush_image_row.append(win.brush_menu_btn)

    win.brush_density_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=2)
    win.brush_opts_row.append(win.brush_density_row)
    win.brush_density_row.append(Gtk.Label(label="Density", xalign=0))
    win.brush_density_spin = Gtk.SpinButton.new_with_range(0, 100, 1)
    configure_numeric_spin(win.brush_density_spin)
    win.brush_density_spin.set_value(10)
    win.brush_density_spin.set_hexpand(True)
    win.brush_density_spin.set_tooltip_text(
        "Bubble packing along the stroke. Low = sparse solitary bubbles, "
        "high = packed foam clusters."
    )
    win.brush_density_spin.connect("value-changed", win._on_brush_density_changed)
    win.brush_density_row.append(win.brush_density_spin)


def _add_modes_rows(win, opts: Gtk.Box) -> None:
    """Per-tool mode dropdowns (Curve, Eraser, Replace, Fill, Lay) and the arc Degrees spin."""
    win.curve_mode_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
    opts.append(win.curve_mode_row)
    win.curve_mode_row.append(Gtk.Label(label="Mode", xalign=0))
    curve_modes = Gtk.StringList.new(["Freehand", "Arc", "Circle"])
    win.curve_mode_dropdown = Gtk.DropDown(model=curve_modes)
    win.curve_mode_dropdown.set_hexpand(True)
    win.curve_mode_dropdown.set_tooltip_text(
        "Freehand: fit a curve to your drag. "
        "Arc: circular arc; set Degrees for the central angle "
        "(180° = semicircle on the drag diameter). "
        "Circle: press center, drag radius (Alt = drag as diameter)."
    )
    win.curve_mode_dropdown.connect("notify::selected", win._on_curve_mode_changed)
    win.curve_mode_row.append(win.curve_mode_dropdown)

    win.eraser_mode_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
    opts.append(win.eraser_mode_row)
    win.eraser_mode_row.append(Gtk.Label(label="Mode", xalign=0))
    eraser_modes = Gtk.StringList.new(["Freehand", "Line"])
    win.eraser_mode_dropdown = Gtk.DropDown(model=eraser_modes)
    win.eraser_mode_dropdown.set_hexpand(True)
    win.eraser_mode_dropdown.set_tooltip_text(
        "Freehand: erase along the stroke. "
        "Line: click-drag a straight erase segment (Shift snaps to 45°)."
    )
    win.eraser_mode_dropdown.connect("notify::selected", win._on_eraser_mode_changed)
    win.eraser_mode_row.append(win.eraser_mode_dropdown)

    win.replace_action_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
    opts.append(win.replace_action_row)
    win.replace_action_row.append(Gtk.Label(label="Action", xalign=0))
    replace_actions = Gtk.StringList.new(["Replace", "Erase"])
    win.replace_action_dropdown = Gtk.DropDown(model=replace_actions)
    win.replace_action_dropdown.set_hexpand(True)
    win.replace_action_dropdown.set_tooltip_text(
        "Replace: paint the tool color over matching pixels. "
        "Erase: clear alpha on pixels matching the erase color (background eraser)."
    )
    win.replace_action_dropdown.connect("notify::selected", win._on_replace_action_changed)
    win.replace_action_row.append(win.replace_action_dropdown)

    win.apply_mode_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
    opts.append(win.apply_mode_row)
    win.apply_mode_row.append(Gtk.Label(label="Mode", xalign=0))
    apply_modes = Gtk.StringList.new(["Brush", "Fill", "All"])
    win.apply_mode_dropdown = Gtk.DropDown(model=apply_modes)
    win.apply_mode_dropdown.set_hexpand(True)
    win.apply_mode_dropdown.set_tooltip_text(
        "Brush: under the stroke. "
        "Fill: connected region. "
        "All: every matching pixel on the layer."
    )
    win.apply_mode_dropdown.connect("notify::selected", win._on_apply_mode_changed)
    win.apply_mode_row.append(win.apply_mode_dropdown)

    win.fill_opts_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
    opts.append(win.fill_opts_row)
    win.fill_opts_row.append(Gtk.Label(label="Fill", xalign=0))
    fill_modes = Gtk.StringList.new(["Color", "Pattern", "Maze", "Puzzle"])
    win.fill_mode_dropdown = Gtk.DropDown(model=fill_modes)
    win.fill_mode_dropdown.set_hexpand(True)
    win.fill_mode_dropdown.set_tooltip_text(
        "Solid color, tiled pattern, solvable maze, or interlocking puzzle pieces"
    )
    win.fill_mode_dropdown.connect("notify::selected", win._on_fill_mode_changed)
    win.fill_opts_row.append(win.fill_mode_dropdown)

    win.lay_opts_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
    opts.append(win.lay_opts_row)
    win.lay_opts_row.append(Gtk.Label(label="Shape", xalign=0))
    lay_modes = Gtk.StringList.new(["Line", "Freehand", "Arc", "Circle"])
    win.lay_mode_dropdown = Gtk.DropDown(model=lay_modes)
    win.lay_mode_dropdown.set_hexpand(True)
    win.lay_mode_dropdown.set_tooltip_text(
        "Line: straight path (Shift snaps to 45°). "
        "Freehand: fit a curve to your drag. "
        "Arc: circular arc; set Degrees for the central angle. "
        "Circle: press center, drag radius (Alt = drag as diameter)."
    )
    win.lay_mode_dropdown.connect("notify::selected", win._on_lay_mode_changed)
    win.lay_opts_row.append(win.lay_mode_dropdown)

    # After Curve Mode / Lay Shape so Degrees sits next to the active shape control.
    win.arc_degrees_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
    opts.append(win.arc_degrees_row)
    win.arc_degrees_row.append(Gtk.Label(label="Degrees", xalign=0))
    win.arc_degrees_spin = Gtk.SpinButton.new_with_range(1, 359, 1)
    configure_numeric_spin(win.arc_degrees_spin)
    win.arc_degrees_spin.set_value(180)
    win.arc_degrees_spin.set_hexpand(True)
    win.arc_degrees_spin.set_tooltip_text(
        "Central angle of the arc. 180° draws a semicircle using the drag as the diameter. "
        "Alt flips which side the arc bulges toward."
    )
    win.arc_degrees_spin.connect("value-changed", win._on_arc_degrees_changed)
    win.arc_degrees_row.append(win.arc_degrees_spin)


def _add_pattern_rows(win, opts: Gtk.Box) -> None:
    """Pattern picker (shared by Fill and Lay), Lay distance, Tile Scale and maze Cell Size."""
    # Shared by Fill (pattern mode) and Lay — keep as a top-level opts child
    # so it stays visible when fill_opts_row is hidden.
    win.pattern_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
    opts.append(win.pattern_row)
    pat_head = Gtk.Box(spacing=4)
    pat_head.append(Gtk.Label(label="Pattern", xalign=0, hexpand=True))
    refresh_pat = Gtk.Button(label="↻")
    refresh_pat.set_tooltip_text("Reload patterns from Libraries folder")
    refresh_pat.connect("clicked", lambda *_: win._refresh_pattern_dropdown(
        select_path=getattr(win._active_tool(), "pattern_path", None)
    ))
    pat_head.append(refresh_pat)
    win.pattern_row.append(pat_head)
    win._pattern_paths: list[Path] = []
    win._pattern_thumb_size = 56
    win._pattern_preview_size = 40
    win.pattern_preview = Gtk.Picture()
    win.pattern_preview.set_size_request(win._pattern_preview_size, win._pattern_preview_size)
    win.pattern_preview.set_content_fit(Gtk.ContentFit.COVER)
    win.pattern_preview.set_can_shrink(False)
    win.pattern_menu_btn = Gtk.MenuButton()
    win.pattern_menu_btn.set_hexpand(True)
    win.pattern_menu_btn.set_always_show_arrow(True)
    win.pattern_menu_btn.set_child(win.pattern_preview)
    win.pattern_menu_btn.set_tooltip_text("Choose a pattern from Libraries → Patterns")
    pattern_popover = Gtk.Popover()
    pattern_scroll = Gtk.ScrolledWindow()
    pattern_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    pattern_scroll.set_min_content_height(168)
    pattern_scroll.set_max_content_height(280)
    pattern_scroll.set_min_content_width(200)
    win.pattern_flow = Gtk.FlowBox()
    win.pattern_flow.set_selection_mode(Gtk.SelectionMode.NONE)
    win.pattern_flow.set_homogeneous(True)
    win.pattern_flow.set_max_children_per_line(4)
    win.pattern_flow.set_min_children_per_line(3)
    win.pattern_flow.set_column_spacing(4)
    win.pattern_flow.set_row_spacing(4)
    win.pattern_flow.set_valign(Gtk.Align.START)
    pattern_scroll.set_child(win.pattern_flow)
    pattern_popover.set_child(pattern_scroll)
    win.pattern_menu_btn.set_popover(pattern_popover)
    win.pattern_row.append(win.pattern_menu_btn)

    win.lay_spacing_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=2)
    opts.append(win.lay_spacing_row)
    win.lay_spacing_row.append(Gtk.Label(label="Distance", xalign=0))
    win.lay_spacing_spin = Gtk.SpinButton.new_with_range(1.0, 512.0, 1.0)
    configure_numeric_spin(win.lay_spacing_spin)
    win.lay_spacing_spin.set_value(40.0)
    win.lay_spacing_spin.set_hexpand(True)
    win.lay_spacing_spin.set_tooltip_text(
        "Spacing between laid pattern elements along the path (pixels)"
    )
    win.lay_spacing_spin.connect("value-changed", win._on_lay_spacing_changed)
    win.lay_spacing_row.append(win.lay_spacing_spin)

    win.tile_scale_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=2)
    opts.append(win.tile_scale_row)
    win.tile_scale_row.append(Gtk.Label(label="Tile Scale", xalign=0))
    win.tile_scale_spin = Gtk.SpinButton.new_with_range(0.1, 16.0, 0.1)
    configure_numeric_spin(win.tile_scale_spin)
    win.tile_scale_spin.set_value(1.0)
    win.tile_scale_spin.set_hexpand(True)
    win.tile_scale_spin.set_tooltip_text("Pattern tile size multiplier (1 = native image size)")
    win.tile_scale_spin.connect("value-changed", win._on_tile_scale_changed)
    win.tile_scale_row.append(win.tile_scale_spin)

    win.maze_cell_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=2)
    opts.append(win.maze_cell_row)
    win.maze_cell_label = Gtk.Label(label="Cell Size", xalign=0)
    win.maze_cell_row.append(win.maze_cell_label)
    win.maze_cell_spin = Gtk.SpinButton.new_with_range(3.0, 128.0, 1.0)
    configure_numeric_spin(win.maze_cell_spin)
    win.maze_cell_spin.set_value(8.0)
    win.maze_cell_spin.set_hexpand(True)
    win.maze_cell_spin.set_tooltip_text("Maze cell size in pixels (walls + corridor)")
    win.maze_cell_spin.connect("value-changed", win._on_maze_cell_changed)
    win.maze_cell_row.append(win.maze_cell_spin)


def _add_type_rows(win, opts: Gtk.Box) -> None:
    """Type tool: font source and font picker."""
    win.type_opts_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
    opts.append(win.type_opts_row)
    win.type_opts_row.append(Gtk.Label(label="Font Source", xalign=0))
    font_sources = Gtk.StringList.new(["System", "Library"])
    win.font_source_dropdown = Gtk.DropDown(model=font_sources)
    win.font_source_dropdown.set_hexpand(True)
    win.font_source_dropdown.set_tooltip_text(
        "System: fonts installed on this computer. "
        "Library: .ttf / .otf files in Libraries → Fonts."
    )
    win.font_source_dropdown.connect("notify::selected", win._on_font_source_changed)
    win.type_opts_row.append(win.font_source_dropdown)

    font_head = Gtk.Box(spacing=4, margin_top=2)
    font_head.append(Gtk.Label(label="Font", xalign=0, hexpand=True))
    refresh_font = Gtk.Button(label="↻")
    refresh_font.set_tooltip_text("Reload fonts from system / Libraries folder")
    refresh_font.connect("clicked", lambda *_: win._refresh_font_dropdown(
        select_path=getattr(win._active_tool(), "font_path", None),
        refresh_system=True,
    ))
    font_head.append(refresh_font)
    win.type_opts_row.append(font_head)
    win._font_paths: list[Path] = []
    win.font_dropdown = Gtk.DropDown(model=Gtk.StringList.new(["(no fonts)"]))
    win.font_dropdown.set_hexpand(True)
    win.font_dropdown.set_tooltip_text(
        "Click canvas to place text, type to edit, drag to move, Enter to rasterize. "
        "Esc cancels · Shift+Enter inserts a newline."
    )
    win.font_dropdown.connect("notify::selected", win._on_font_changed)
    win.type_opts_row.append(win.font_dropdown)


def _add_color_rows(win, opts: Gtk.Box) -> None:
    """Primary / secondary color pair, swap button and the palette panel."""
    # Primary / secondary color pair; X swaps them
    win.color_pair_row = Gtk.Grid(column_spacing=4, row_spacing=4, margin_top=4)
    opts.append(win.color_pair_row)
    win.color_label = Gtk.Label(label="Color", xalign=0)
    win.secondary_label = Gtk.Label(label="Secondary", xalign=0)
    win.color_pair_row.attach(win.color_label, 0, 0, 1, 1)
    win.color_pair_row.attach(win.secondary_label, 1, 0, 1, 1)
    win.color_btn = ColorSelectButton(use_alpha=True)
    win.color_btn.set_hexpand(True)
    win.color_btn.set_tooltip_text("Primary color: open color picker (hue + recent colors)")
    win.color_btn.set_recent_colors(win.app_settings.recent_colors)
    win.color_btn.connect("color-set", win._on_color)
    win.color_pair_row.attach(win.color_btn, 0, 1, 1, 1)
    win.secondary_color_btn = ColorSelectButton(use_alpha=True)
    win.secondary_color_btn.set_hexpand(True)
    win.secondary_color_btn.set_tooltip_text(
        "Secondary color: Gradient end, maze corridors, puzzle pieces"
    )
    win.secondary_color_btn.set_recent_colors(win.app_settings.recent_colors)
    win.secondary_color_btn.connect("color-set", win._on_secondary_color)
    win.color_pair_row.attach(win.secondary_color_btn, 1, 1, 1, 1)
    swap_colors = Gtk.Button(label="⇄", valign=Gtk.Align.CENTER)
    swap_colors.set_tooltip_text("Swap primary and secondary colors (X)")
    swap_colors.connect("clicked", win.action_swap_colors)
    win.color_pair_row.attach(swap_colors, 2, 1, 1, 1)

    win.palette_panel = PalettePanel(
        active_name=win.app_settings.active_palette,
        get_primary=lambda: tuple(int(v) for v in win._active_tool().color[:3]),
        on_pick=win._on_palette_pick,
        on_active_changed=win._on_active_palette_changed,
        on_colors_changed=win._on_palette_colors_changed,
        on_error=win._alert,
        get_layer=win._active_layer_for_palette,
    )
    opts.append(win.palette_panel)


def _add_shading_rows(win, opts: Gtk.Box) -> None:
    """Threshold, Intensity, Liquify warp, 3D Fill shading spins and 3D Pen Frequency."""
    win.threshold_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
    opts.append(win.threshold_row)
    win.threshold_row.append(Gtk.Label(label="Threshold", xalign=0))
    win.threshold_spin = Gtk.SpinButton.new_with_range(0, 255, 1)
    configure_numeric_spin(win.threshold_spin)
    win.threshold_spin.set_hexpand(True)
    win.threshold_spin.set_tooltip_text(
        "Color match tolerance (variance allowed). "
        "Fill / Replace / Gradient: how close pixels must be to the target color. "
        "Eraser: 255=all pixels, lower=only similar to click color."
    )
    win.threshold_spin.connect("value-changed", win._on_threshold_changed)
    win.threshold_row.append(win.threshold_spin)

    win.intensity_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
    opts.append(win.intensity_row)
    win.intensity_label = Gtk.Label(label="Intensity", xalign=0)
    win.intensity_row.append(win.intensity_label)
    win.intensity_spin = Gtk.SpinButton.new_with_range(0, 100, 1)
    configure_numeric_spin(win.intensity_spin)
    win.intensity_spin.set_hexpand(True)
    win.intensity_spin.set_tooltip_text(
        "Smear / liquify strength, or weld-brush fillet amount. "
        "0 = none, 100 = strong. Pen pressure fine-tunes smear/liquify within this."
    )
    win.intensity_spin.connect("value-changed", win._on_intensity_changed)
    win.intensity_row.append(win.intensity_spin)

    win.liquify_mode_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
    opts.append(win.liquify_mode_row)
    win.liquify_mode_row.append(Gtk.Label(label="Warp", xalign=0))
    liquify_modes = Gtk.StringList.new(list(LIQUIFY_BRUSH_MODES))
    win.liquify_mode_dropdown = Gtk.DropDown(model=liquify_modes)
    win.liquify_mode_dropdown.set_hexpand(True)
    win.liquify_mode_dropdown.set_tooltip_text(
        "Push: drag pixels along the stroke. "
        "Swirl / Pinch / Bulge: warp around the brush tip."
    )
    win.liquify_mode_dropdown.connect("notify::selected", win._on_liquify_mode_changed)
    win.liquify_mode_row.append(win.liquify_mode_dropdown)

    win.shade3d_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
    opts.append(win.shade3d_row)

    win.fill3d_type_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    win.shade3d_row.append(win.fill3d_type_row)
    win.fill3d_type_row.append(Gtk.Label(label="Fill Type", xalign=0))
    fill3d_types = Gtk.StringList.new(
        ["Classic", "Addiction", "Wavy", "Drift", "Depression"]
    )
    win.fill3d_type_dropdown = Gtk.DropDown(model=fill3d_types)
    win.fill3d_type_dropdown.set_hexpand(True)
    win.fill3d_type_dropdown.set_tooltip_text(
        "Classic: soft emboss. Addiction: deeper multi-scale sculpting with lobed ridges and dual highlights. "
        "Wavy: corner-distance ripples with light from top corners and shadow from bottom. "
        "Drift: Addiction lighting with silhouette bands and directional ridges (no center pinch). "
        "Depression: inverted classic — carved inward with a dark basin and lit lip."
    )
    win.fill3d_type_dropdown.connect("notify::selected", win._on_fill3d_type_changed)
    win.fill3d_type_row.append(win.fill3d_type_dropdown)

    def _add_3d_spin(label: str, tooltip: str) -> Gtk.SpinButton:
        win.shade3d_row.append(Gtk.Label(label=label, xalign=0))
        spin = Gtk.SpinButton.new_with_range(0, 100, 1)
        configure_numeric_spin(spin)
        spin.set_hexpand(True)
        spin.set_tooltip_text(tooltip)
        spin.connect("value-changed", win._on_3d_setting_changed)
        win.shade3d_row.append(spin)
        return spin

    win.depth_spin = _add_3d_spin("Depth", "Shading contrast — low is flat, high is strongly embossed.")
    win.highlight_spin = _add_3d_spin("Highlight", "Specular highlight strength on lit edges.")
    win.bevel_spin = _add_3d_spin(
        "Bevel",
        "Edge softness / roundness. Higher = softer, more rounded 3D rim.",
    )

    win.frequency_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
    opts.append(win.frequency_row)
    win.frequency_row.append(Gtk.Label(label="Frequency", xalign=0))
    win.frequency_spin = Gtk.SpinButton.new_with_range(0, 100, 1)
    configure_numeric_spin(win.frequency_spin)
    win.frequency_spin.set_hexpand(True)
    win.frequency_spin.set_tooltip_text(
        "Stamp density along the stroke. Low = spaced beads, high = continuous tube."
    )
    win.frequency_spin.connect("value-changed", win._on_frequency_changed)
    win.frequency_row.append(win.frequency_spin)


def _add_symmetry_box(win, toolbox_page: Gtk.Box) -> None:
    """Symmetry (mirror / radial / linear array), Wrap and Pixel toggles."""
    toolbox_page.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL, margin_top=6, margin_bottom=2))
    mirror_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    toolbox_page.append(mirror_box)
    win.mirror_toggle = Gtk.CheckButton(label="Symmetry")
    win.mirror_toggle.set_tooltip_text(
        "Live symmetry while drawing. Alt-drag the hub to move the center; "
        "Alt-drag a guide line to rotate. Mirror reflects; Radial rotates copies; "
        "Linear Array repeats the stroke at a set distance and direction."
    )
    win.mirror_toggle.connect("toggled", win._on_mirror_toggled)
    mirror_box.append(win.mirror_toggle)

    win.tile_wrap_toggle = Gtk.CheckButton(label="Wrap")
    win.tile_wrap_toggle.set_tooltip_text(
        "Paint and transform across tile edges: content that leaves one side "
        "reappears on the opposite side. Turns on with Tile Preview and off "
        "when preview is disabled; you can still toggle it manually."
    )
    win.tile_wrap_toggle.set_active(False)
    win.tile_wrap_toggle.connect("toggled", win._on_tile_wrap_toggled)
    mirror_box.append(win.tile_wrap_toggle)

    win.pixel_mode_toggle = Gtk.CheckButton(label="Pixel")
    win.pixel_mode_toggle.set_tooltip_text(
        "Pixel art: paint tools draw hard pixels with no soft edges, Size is "
        "the stroke width in pixels, pressure is ignored and 1 px freehand "
        "strokes skip L-shaped corners (pixel-perfect). Saved with the document."
    )
    win.pixel_mode_toggle.set_active(win.pixel_mode)
    win.pixel_mode_toggle.connect("toggled", win._on_pixel_mode_toggled)
    mirror_box.append(win.pixel_mode_toggle)

    win.mirror_opts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    win.mirror_opts.set_visible(False)
    mirror_box.append(win.mirror_opts)

    win.mirror_opts.append(Gtk.Label(label="Mode", xalign=0))
    mode_labels = [label for _key, label in MODES]
    win.mirror_mode_dropdown = Gtk.DropDown(model=Gtk.StringList.new(mode_labels))
    win.mirror_mode_dropdown.set_hexpand(True)
    win.mirror_mode_dropdown.set_tooltip_text(
        "Mirror: reflect across axes (kaleidoscope). "
        "Radial: rotate same-facing copies (pinwheel). "
        "Linear Array: repeat copies at regular intervals along a direction."
    )
    win.mirror_mode_dropdown.connect("notify::selected", win._on_mirror_mode_changed)
    win.mirror_opts.append(win.mirror_mode_dropdown)

    win.mirror_orient_label = Gtk.Label(label="Orientation", xalign=0)
    win.mirror_opts.append(win.mirror_orient_label)
    orient_labels = [label for _key, label in ORIENTATIONS]
    win.mirror_orient_dropdown = Gtk.DropDown(model=Gtk.StringList.new(orient_labels))
    win.mirror_orient_dropdown.set_hexpand(True)
    win.mirror_orient_dropdown.set_tooltip_text(
        "Rotates the axis set. Horizontal: left↔right first. "
        "Vertical: top↔bottom first. Diagonal: 45°."
    )
    win.mirror_orient_dropdown.connect("notify::selected", win._on_mirror_orient_changed)
    win.mirror_opts.append(win.mirror_orient_dropdown)

    win.mirror_axes_label = Gtk.Label(label="Axes", xalign=0)
    win.mirror_opts.append(win.mirror_axes_label)
    win.mirror_axes_spin = Gtk.SpinButton.new_with_range(1, 16, 1)
    configure_numeric_spin(win.mirror_axes_spin, digits=0)
    win.mirror_axes_spin.set_value(1)
    win.mirror_axes_spin.set_hexpand(True)
    win.mirror_axes_spin.set_tooltip_text(
        "Equally spaced mirror axes through the center. "
        "1 = single axis; 2 = four-way (perpendicular); 3+ = kaleidoscope."
    )
    win.mirror_axes_spin.connect("value-changed", win._on_mirror_axes_changed)
    win.mirror_opts.append(win.mirror_axes_spin)

    win.mirror_array_opts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    win.mirror_array_opts.set_visible(False)
    win.mirror_opts.append(win.mirror_array_opts)
    win.mirror_array_opts.append(Gtk.Label(label="Copies", xalign=0))
    win.mirror_array_count_spin = Gtk.SpinButton.new_with_range(1, ARRAY_COUNT_MAX, 1)
    configure_numeric_spin(win.mirror_array_count_spin, digits=0)
    win.mirror_array_count_spin.set_hexpand(True)
    win.mirror_array_count_spin.set_tooltip_text(
        "Total instances along the array, the stroke you draw included."
    )
    win.mirror_array_opts.append(win.mirror_array_count_spin)
    win.mirror_array_opts.append(Gtk.Label(label="Spacing (px)", xalign=0))
    win.mirror_array_spacing_spin = Gtk.SpinButton.new_with_range(0.0, ARRAY_SPACING_MAX, 1.0)
    configure_numeric_spin(win.mirror_array_spacing_spin, digits=1)
    win.mirror_array_spacing_spin.set_hexpand(True)
    win.mirror_array_spacing_spin.set_tooltip_text(
        "Distance between neighboring copies, in document pixels. "
        "Alt-drag the square handle on the canvas to set distance and direction together."
    )
    win.mirror_array_opts.append(win.mirror_array_spacing_spin)
    win.mirror_array_opts.append(Gtk.Label(label="Direction (°)", xalign=0))
    win.mirror_array_direction_spin = Gtk.SpinButton.new_with_range(-180.0, 180.0, 1.0)
    configure_numeric_spin(win.mirror_array_direction_spin, digits=1)
    win.mirror_array_direction_spin.set_wrap(True)
    win.mirror_array_direction_spin.set_hexpand(True)
    win.mirror_array_direction_spin.set_tooltip_text(
        "Heading of the array: 0° = right, 90° = up, -90° = down. "
        "Alt-drag the guide line to rotate it."
    )
    win.mirror_array_opts.append(win.mirror_array_direction_spin)
    win._sync_mirror_array_ui()
    for spin in (
        win.mirror_array_count_spin,
        win.mirror_array_spacing_spin,
        win.mirror_array_direction_spin,
    ):
        spin.connect("value-changed", win._on_mirror_array_changed)

    reset_btn = Gtk.Button(label="Reset Guides")
    reset_btn.set_tooltip_text(
        "Move the hub back to the canvas center and snap rotation to Horizontal "
        "(Linear Array: heading back to 0°). Works in every mode."
    )
    reset_btn.connect("clicked", win._on_mirror_reset)
    win.mirror_opts.append(reset_btn)
    win._update_mirror_mode_ui()


class ToolOptionsMixin:
    """Tool selection and the options column for ``MainWindow``."""

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

    def _on_tile_wrap_toggled(self, btn: Gtk.CheckButton) -> None:
        enabled = bool(btn.get_active())
        self.tile_wrap = enabled
        action = self.lookup_action("tile_wrap")
        if action is not None:
            current = bool(action.get_state().get_boolean())  # type: ignore[union-attr]
            if current != enabled:
                action.change_state(GLib.Variant.new_boolean(enabled))

    def _on_pixel_mode_toggled(self, btn: Gtk.CheckButton) -> None:
        enabled = bool(btn.get_active())
        action = self.lookup_action("pixel_mode")
        if action is not None:
            current = bool(action.get_state().get_boolean())  # type: ignore[union-attr]
            if current != enabled:
                action.change_state(GLib.Variant.new_boolean(enabled))

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

    def action_brush_smaller(self, *_a) -> None:
        self.brush_spin.set_value(max(0.1, self.brush_spin.get_value() - 1))

    def action_brush_larger(self, *_a) -> None:
        self.brush_spin.set_value(min(256, self.brush_spin.get_value() + 1))

    def _refresh_tool_shortcut_tips(self) -> None:
        for tid, label in TOOL_ORDER:
            btn = self.tool_buttons.get(tid)
            if btn is None:
                continue
            keys = format_accels(self.shortcuts.get(f"win.tool_{tid}", []))
            tip = f"{label}  ({keys})" if keys != "None" else label
            btn.set_tooltip_text(tip)

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
