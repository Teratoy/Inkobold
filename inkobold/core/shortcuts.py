"""Default keyboard shortcuts and display labels."""

from __future__ import annotations

# action_name -> list of accelerator strings (GTK parse format)
DEFAULT_SHORTCUTS: dict[str, list[str]] = {
    "win.new": ["<Control>n"],
    "win.open": ["<Control>o"],
    "win.save": ["<Control>s"],
    "win.save_as": ["<Control><Shift>s"],
    "win.import_image": ["<Control>i"],
    "win.export": ["<Control>e"],
    "win.export_layers": [],
    "win.export_anim_gif": [],
    "win.export_anim_png": [],
    "win.fit": ["<Control>0", "<Control>f"],
    "win.window_fullscreen": ["F11"],
    "win.clear_selection": ["Escape", "<Control>d"],
    "win.undo": ["<Control>z"],
    "win.redo": ["<Control><Shift>z", "<Control>y"],
    "win.add_layer": ["<Control><Shift>n"],
    "win.delete_layer": ["<Control><Shift>Delete"],
    "win.merge_layer_down": ["<Control><Shift>e"],
    "win.merge_all_layers": [],
    "win.add_frame": ["<Control><Alt>n"],
    "win.duplicate_frame": ["<Control><Alt>d"],
    "win.delete_frame": ["<Control><Alt>Delete"],
    "win.play_animation": ["space", "F5"],
    "win.stop_animation": ["F6"],
    "win.brush_smaller": ["bracketleft"],
    "win.brush_larger": ["bracketright"],
    "win.tool_pen": ["p", "1"],
    "win.tool_line": ["l"],
    "win.tool_curve": ["u"],
    "win.tool_brush": ["b"],
    "win.tool_fill": ["g", "2"],
    "win.tool_eraser": ["e", "3"],
    "win.tool_smear": ["m"],
    "win.tool_liquify": ["y"],
    "win.tool_replace": ["c", "0", "r", "5"],
    "win.tool_move": ["v", "6"],
    "win.tool_lasso": ["q", "7"],
    "win.tool_pen3d": ["d", "8"],
    "win.tool_fill3d": ["f", "9"],
    "win.tool_type": ["t"],
}

SHORTCUT_LABELS: dict[str, str] = {
    "win.new": "New File",
    "win.open": "Open File",
    "win.save": "Save File",
    "win.save_as": "Save As",
    "win.import_image": "Import Image",
    "win.export": "Export",
    "win.export_layers": "Export Separated Layers",
    "win.export_anim_gif": "Export Animation as GIF",
    "win.export_anim_png": "Export Animation as PNG Folder",
    "win.fit": "Fit Canvas",
    "win.window_fullscreen": "Fullscreen",
    "win.window_borderless": "Borderless Window",
    "win.window_windowed": "Windowed",
    "win.clear_selection": "Clear Selection",
    "win.undo": "Undo",
    "win.redo": "Redo",
    "win.add_layer": "Add Layer",
    "win.delete_layer": "Delete Layer",
    "win.merge_layer_down": "Merge with Layer Below",
    "win.merge_all_layers": "Merge All",
    "win.add_frame": "Add Frame",
    "win.duplicate_frame": "Duplicate Frame",
    "win.delete_frame": "Delete Frame",
    "win.play_animation": "Play / Pause Animation",
    "win.stop_animation": "Stop Animation",
    "win.brush_smaller": "Smaller Brush",
    "win.brush_larger": "Larger Brush",
    "win.tool_pen": "Tool: Pen",
    "win.tool_line": "Tool: Line",
    "win.tool_curve": "Tool: Curve",
    "win.tool_brush": "Tool: Brush",
    "win.tool_fill": "Tool: Fill",
    "win.tool_eraser": "Tool: Eraser",
    "win.tool_smear": "Tool: Smear",
    "win.tool_liquify": "Tool: Liquify",
    "win.tool_replace": "Tool: Replace",
    "win.tool_move": "Tool: Transform",
    "win.tool_lasso": "Tool: Lasso",
    "win.tool_pen3d": "Tool: 3D Pen",
    "win.tool_fill3d": "Tool: 3D Fill",
    "win.tool_type": "Tool: Type",
}

# Stable order for menus / editors
SHORTCUT_ORDER: list[str] = list(DEFAULT_SHORTCUTS.keys())


def merge_shortcuts(overrides: dict[str, list[str]] | None) -> dict[str, list[str]]:
    merged = {k: list(v) for k, v in DEFAULT_SHORTCUTS.items()}
    if not overrides:
        return merged
    for key, accels in overrides.items():
        if key not in merged:
            continue
        if not isinstance(accels, list):
            continue
        cleaned = [str(a) for a in accels if isinstance(a, str) and a.strip()]
        merged[key] = cleaned
    return merged


def format_accel(accel: str) -> str:
    """Human-readable accelerator (Ctrl+N)."""
    s = accel
    s = s.replace("<Control>", "Ctrl+").replace("<Primary>", "Ctrl+")
    s = s.replace("<Shift>", "Shift+").replace("<Alt>", "Alt+")
    s = s.replace("<Super>", "Super+")
    # strip angle brackets left on obscure keys
    s = s.replace("<", "").replace(">", "")
    if s.lower() == "bracketleft":
        return "["
    if s.lower() == "bracketright":
        return "]"
    if s.lower() == "space":
        return "Space"
    if len(s) == 1:
        return s.upper()
    # F11, Escape, Delete, etc.
    return s[:1].upper() + s[1:] if s else s


def format_accels(accels: list[str]) -> str:
    if not accels:
        return "None"
    return ", ".join(format_accel(a) for a in accels)
