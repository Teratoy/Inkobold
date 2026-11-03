"""Rig overlay for character canvases: bones, joint handles, art-top / foot-sole lines.

Drawn with Cairo on the canvas' guide layer. Left joints are filled, their mirrored
right twins hollow (dragging either moves the pair), centre joints and hat anchors
have their own shapes, and the hovered / dragged handle shows its name and pixel.
"""

from __future__ import annotations

import math
from typing import Callable, Optional

from inkobold.core.character import CharacterRig, RigHandle, RigView

ToScreen = Callable[[float, float], tuple[float, float]]

POINT_HIT_PX = 9.0
LINE_HIT_PX = 5.0
_INK = (0.08, 0.1, 0.08, 0.85)
_BONE = (0.55, 0.85, 0.35, 0.95)
_LINK = (0.55, 0.85, 0.35, 0.55)
_LEFT = (0.47, 0.82, 0.27, 1.0)
_RIGHT = (0.47, 0.82, 0.27, 1.0)
_CENTER = (0.95, 0.55, 0.85, 1.0)
_ANCHOR = (1.0, 0.8, 0.25, 1.0)
_LINE = (1.0, 0.8, 0.25, 0.75)


def hit_handle(rig: CharacterRig, sx: float, sy: float, to_screen: ToScreen) -> Optional[str]:
    """Handle key under screen point (sx, sy): nearest point first, then a guide line."""
    best: Optional[str] = None
    best_d2 = POINT_HIT_PX ** 2
    lines: list[RigHandle] = []
    for h in rig.handles():
        if h.kind == "line":
            lines.append(h)
            continue
        hx, hy = to_screen(h.x, h.y)
        d2 = (sx - hx) ** 2 + (sy - hy) ** 2
        if d2 <= best_d2:
            best, best_d2 = h.key, d2
    if best is not None:
        return best
    x0, _ = to_screen(0.0, 0.0)
    x1, _ = to_screen(rig.canvas_width, 0.0)
    if not (min(x0, x1) <= sx <= max(x0, x1)):
        return None
    for h in lines:
        _, hy = to_screen(h.x, h.y)
        if abs(sy - hy) <= LINE_HIT_PX:
            return h.key
    return None


def draw(cr, rig: CharacterRig, view: RigView, to_screen: ToScreen) -> None:
    """Paint the overlay (cr: cairo.Context in screen space)."""
    cr.save()
    cr.set_line_cap(1)  # ROUND
    cr.set_line_join(1)
    x0, top = to_screen(0.0, 0.0)
    x1, bottom = to_screen(rig.canvas_width, rig.canvas_height)

    # Mirror axis.
    ax, _ = to_screen(rig.canvas_width * 0.5, 0.0)
    cr.set_source_rgba(1.0, 1.0, 1.0, 0.25)
    cr.set_line_width(1.0)
    cr.set_dash([3.0, 5.0])
    cr.move_to(ax, top)
    cr.line_to(ax, bottom)
    cr.stroke()

    # Art top / foot sole.
    for h in rig.handles():
        if h.kind != "line":
            continue
        _, y = to_screen(h.x, h.y)
        hot = h.key in (view.hover, view.active)
        cr.set_source_rgba(*_LINE[:3], 1.0 if hot else _LINE[3])
        cr.set_line_width(2.0 if hot else 1.0)
        cr.set_dash([6.0, 4.0])
        cr.move_to(x0, y)
        cr.line_to(x1, y)
        cr.stroke()
    cr.set_dash([])

    # Bones: dark under-stroke so they read over any art.
    for (a, b, kind) in rig.bones():
        sa, sb = to_screen(*a), to_screen(*b)
        width = 2.5 if kind == "bone" else 1.2
        cr.set_source_rgba(*_INK)
        cr.set_line_width(width + 2.0)
        cr.move_to(*sa)
        cr.line_to(*sb)
        cr.stroke()
        cr.set_source_rgba(*(_BONE if kind == "bone" else _LINK))
        cr.set_line_width(width)
        if kind == "link":
            cr.set_dash([4.0, 3.0])
        cr.move_to(*sa)
        cr.line_to(*sb)
        cr.stroke()
        cr.set_dash([])

    # Hat brim: a short tick under the crown.
    cx, cy = to_screen(*rig.point("hat_crown"))
    bx, by = to_screen(*rig.point("hat_brim"))
    cr.set_source_rgba(*_ANCHOR[:3], 0.6)
    cr.set_line_width(1.0)
    cr.set_dash([2.0, 2.0])
    cr.move_to(cx, cy)
    cr.line_to(bx, by)
    cr.stroke()
    cr.set_dash([])

    label: Optional[tuple[RigHandle, float, float]] = None
    for h in rig.handles():
        if h.kind == "line":
            continue
        sx, sy = to_screen(h.x, h.y)
        hot = h.key in (view.hover, view.active)
        r = 7.0 if hot else 5.0
        _handle_shape(cr, h.kind, h.key, sx, sy, r)
        if hot:
            label = (h, sx, sy)
    for h in rig.handles():
        if h.kind == "line" and h.key in (view.hover, view.active):
            _, sy = to_screen(h.x, h.y)
            label = (h, max(x0, 0.0) + 8.0, sy)
    if label is not None:
        h, sx, sy = label
        value = f"y {h.y:g}" if h.kind == "line" else f"{h.x:g}, {h.y:g}"
        _label(cr, f"{h.label}  {value}", sx + 10.0, sy - 10.0)
    cr.restore()


def _handle_shape(cr, kind: str, key: str, sx: float, sy: float, r: float) -> None:
    if kind in ("left", "right"):
        cr.arc(sx, sy, r, 0.0, math.tau)
    elif kind == "center":
        cr.move_to(sx, sy - r * 1.25)
        cr.line_to(sx + r * 1.25, sy)
        cr.line_to(sx, sy + r * 1.25)
        cr.line_to(sx - r * 1.25, sy)
        cr.close_path()
    else:
        cr.rectangle(sx - r, sy - r, r * 2.0, r * 2.0)
    color = {"left": _LEFT, "right": _RIGHT, "center": _CENTER}.get(kind, _ANCHOR)
    if kind == "right":
        cr.set_source_rgba(*_INK)
        cr.fill_preserve()
    else:
        cr.set_source_rgba(*color)
        cr.fill_preserve()
    cr.set_source_rgba(*(color if kind == "right" else _INK))
    cr.set_line_width(2.0 if kind == "right" else 1.5)
    cr.stroke()


def _label(cr, text: str, x: float, y: float) -> None:
    cr.select_font_face("Sans", 0, 1)  # NORMAL, BOLD
    cr.set_font_size(12.0)
    ext = cr.text_extents(text)
    pad = 4.0
    cr.set_source_rgba(0.05, 0.07, 0.05, 0.85)
    cr.rectangle(x - pad, y + ext.y_bearing - pad, ext.width + pad * 2.0, ext.height + pad * 2.0)
    cr.fill()
    cr.set_source_rgba(0.9, 1.0, 0.85, 1.0)
    cr.move_to(x, y)
    cr.show_text(text)
