from __future__ import annotations

import math

import numpy as np

from inkobold.tools.base import BaseTool, ToolContext
from inkobold.tools.paint import stroke_circle, stroke_circular_arc, stroke_quadratic


class CurveTool(BaseTool):
    """Click-drag a freehand curve, circular arc, or full circle.

    Mode "freehand" (default): fits a Bézier through the start, farthest bow
    from the chord, and the end. Shift snaps the end to 45°; Alt flips the
    bulge (or adds a default arc when the drag is nearly straight).

    Mode "arc": circular arc using the drag as the chord; Degrees sets the
    central angle (180° = semicircle on that diameter). Shift snaps the chord
    end; Alt flips which side of the chord the arc bulges toward.

    Mode "circle": press sets the center; drag sets the radius. Shift snaps the
    radius direction to 45°. Alt treats the drag as a diameter instead.
    """

    name = "Curve"
    id = "curve"
    default_size = 8.0
    default_color = (0, 0, 0, 255)
    uses_opacity = True
    uses_curve_modes = True
    default_curve_mode = "freehand"
    default_arc_degrees = 180.0

    _active_master: CurveTool | None = None
    _DEFAULT_BULGE = 0.35
    _MIN_BOW = 1.5
    _CURVE_MODES = ("freehand", "arc", "circle")

    def __init__(self) -> None:
        super().__init__()
        self.curve_mode: str = self.default_curve_mode
        self.arc_degrees: float = self.default_arc_degrees
        self._drawing = False
        self._sx = 0.0
        self._sy = 0.0
        self._r = 0.5
        self._samples: list[tuple[float, float]] = []
        self._snapshot: np.ndarray | None = None

    @staticmethod
    def _snap_end(sx: float, sy: float, x: float, y: float, shift: bool) -> tuple[float, float]:
        if not shift:
            return x, y
        dx = x - sx
        dy = y - sy
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            return x, y
        angle = math.atan2(dy, dx)
        snapped = round(angle / (math.pi / 4.0)) * (math.pi / 4.0)
        return sx + dist * math.cos(snapped), sy + dist * math.sin(snapped)

    @classmethod
    def _control_point(
        cls,
        samples: list[tuple[float, float]],
        p0: tuple[float, float],
        p2: tuple[float, float],
        alt: bool,
    ) -> tuple[float, float]:
        dx = p2[0] - p0[0]
        dy = p2[1] - p0[1]
        chord = math.hypot(dx, dy)
        mid = ((p0[0] + p2[0]) * 0.5, (p0[1] + p2[1]) * 0.5)
        if chord < 1e-6:
            return mid

        nx = -dy / chord
        ny = dx / chord
        best_pt: tuple[float, float] | None = None
        best_abs = 0.0
        best_signed = 0.0
        for x, y in samples:
            signed = (x - p0[0]) * nx + (y - p0[1]) * ny
            a = abs(signed)
            if a > best_abs:
                best_abs = a
                best_signed = signed
                best_pt = (x, y)

        if best_pt is None or best_abs < cls._MIN_BOW:
            sign = -1.0 if alt else 1.0
            if not alt:
                return mid
            offset = chord * cls._DEFAULT_BULGE * sign
            through = (mid[0] + nx * offset, mid[1] + ny * offset)
        else:
            through = best_pt
            if alt:
                proj = best_signed
                through = (through[0] - 2.0 * proj * nx, through[1] - 2.0 * proj * ny)

        return (2.0 * through[0] - mid[0], 2.0 * through[1] - mid[1])

    def _paint(self, ctx: ToolContext, ex: float, ey: float, alt: bool) -> None:
        pixels = ctx.document.active_layer.pixels
        color = self._paint_color(ctx)
        mask = self._mask(ctx)
        mode = self.curve_mode
        if mode == "arc":
            stroke_circular_arc(
                pixels,
                self._sx, self._sy, ex, ey,
                float(self.arc_degrees),
                self._r,
                color,
                mask=mask,
                flip=alt,
            )
        elif mode == "circle":
            if alt:
                cx = (self._sx + ex) * 0.5
                cy = (self._sy + ey) * 0.5
                r_circ = math.hypot(ex - self._sx, ey - self._sy) * 0.5
            else:
                cx, cy = self._sx, self._sy
                r_circ = math.hypot(ex - self._sx, ey - self._sy)
            stroke_circle(
                pixels,
                cx, cy, r_circ,
                self._r,
                color,
                mask=mask,
            )
        else:
            p0 = (self._sx, self._sy)
            p2 = (ex, ey)
            p1 = self._control_point(self._samples, p0, p2, alt)
            stroke_quadratic(
                pixels,
                p0[0], p0[1],
                p1[0], p1[1],
                p2[0], p2[1],
                self._r,
                color,
                mask=mask,
            )
        ctx.document.mark_dirty()

    def on_press(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        pixels = ctx.document.active_layer.pixels
        self._drawing = True
        self._sx, self._sy = x, y
        self._r = self._radius(ctx)
        self._samples = [(x, y)]
        if CurveTool._active_master is None:
            CurveTool._active_master = self
            self._snapshot = pixels.copy()
        self._paint(ctx, x, y, alt)

    def on_drag(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        if not self._drawing:
            return
        pixels = ctx.document.active_layer.pixels
        if CurveTool._active_master is self and self._snapshot is not None:
            np.copyto(pixels, self._snapshot)
        ex, ey = self._snap_end(self._sx, self._sy, x, y, shift)
        if self.curve_mode == "freehand":
            self._samples.append((ex, ey))
            if len(self._samples) > 512:
                self._samples = self._samples[::2]
        self._paint(ctx, ex, ey, alt)

    def on_release(self, ctx: ToolContext, x: float, y: float) -> None:
        self._drawing = False
        self._samples = []
        if CurveTool._active_master is self:
            CurveTool._active_master = None
            self._snapshot = None

    def reset(self) -> None:
        self._drawing = False
        self._samples = []
        if CurveTool._active_master is self:
            CurveTool._active_master = None
        self._snapshot = None
