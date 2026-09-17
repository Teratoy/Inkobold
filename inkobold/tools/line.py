from __future__ import annotations

import math

import numpy as np

from inkobold.tools.base import BaseTool, ToolContext
from inkobold.tools.paint import stroke_segment


class LineTool(BaseTool):
    """Click-drag a straight segment; Shift snaps to 45° increments."""

    name = "Line"
    id = "line"
    default_size = 8.0
    default_color = (0, 0, 0, 255)
    uses_opacity = True

    # Shared across mirror clones so only one branch restores the snapshot
    # before every clone redraws its segment on the same layer.
    _active_master: LineTool | None = None

    def __init__(self) -> None:
        super().__init__()
        self._drawing = False
        self._sx = 0.0
        self._sy = 0.0
        self._r = 0.5
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

    def on_press(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        pixels = ctx.document.active_layer.pixels
        self._drawing = True
        self._sx, self._sy = x, y
        self._r = self._radius(ctx)
        if LineTool._active_master is None:
            LineTool._active_master = self
            self._snapshot = pixels.copy()
        stroke_segment(
            pixels,
            x, y, x, y,
            self._r,
            self._paint_color(ctx),
            mask=self._mask(ctx),
        )
        ctx.document.mark_dirty()

    def on_drag(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        if not self._drawing:
            return
        pixels = ctx.document.active_layer.pixels
        if LineTool._active_master is self and self._snapshot is not None:
            np.copyto(pixels, self._snapshot)
        ex, ey = self._snap_end(self._sx, self._sy, x, y, shift)
        stroke_segment(
            pixels,
            self._sx, self._sy, ex, ey,
            self._r,
            self._paint_color(ctx),
            mask=self._mask(ctx),
        )
        ctx.document.mark_dirty()

    def on_release(self, ctx: ToolContext, x: float, y: float) -> None:
        self._drawing = False
        if LineTool._active_master is self:
            LineTool._active_master = None
            self._snapshot = None

    def reset(self) -> None:
        self._drawing = False
        if LineTool._active_master is self:
            LineTool._active_master = None
        self._snapshot = None
