from __future__ import annotations

import numpy as np

from inkobold.tools.base import BaseTool, ToolContext
from inkobold.tools.paint import stroke_segment


class RectangleTool(BaseTool):
    """Click-drag a rectangle outline; Shift constrains to square, Alt draws from center."""

    name = "Rectangle"
    id = "rectangle"
    default_size = 8.0
    default_color = (0, 0, 0, 255)
    uses_opacity = True

    # Shared across mirror clones so only one branch restores the snapshot
    # before every clone redraws its outline on the same layer.
    _active_master: RectangleTool | None = None

    def __init__(self) -> None:
        super().__init__()
        self._drawing = False
        self._sx = 0.0
        self._sy = 0.0
        self._r = 0.5
        self._snapshot: np.ndarray | None = None

    @staticmethod
    def _corners(
        sx: float, sy: float, x: float, y: float, shift: bool, alt: bool
    ) -> tuple[float, float, float, float]:
        """Return axis-aligned (x0, y0, x1, y1) for the rectangle outline."""
        if alt:
            hw = abs(x - sx)
            hh = abs(y - sy)
            if shift:
                side = max(hw, hh)
                hw = hh = side
            return sx - hw, sy - hh, sx + hw, sy + hh

        x0, y0, x1, y1 = sx, sy, x, y
        if shift:
            dx = x1 - x0
            dy = y1 - y0
            side = max(abs(dx), abs(dy))
            sx_sign = 1.0 if dx >= 0.0 else -1.0
            sy_sign = 1.0 if dy >= 0.0 else -1.0
            x1 = x0 + sx_sign * side
            y1 = y0 + sy_sign * side
        return x0, y0, x1, y1

    def _paint_rect(self, ctx: ToolContext, x: float, y: float, shift: bool, alt: bool) -> None:
        pixels = ctx.document.active_layer.pixels
        x0, y0, x1, y1 = self._corners(self._sx, self._sy, x, y, shift, alt)
        color = self._paint_color(ctx)
        mask = self._mask(ctx)
        op = self._opacity_factor(ctx)
        wrap = ctx.tile_wrap
        r = self._r
        # Four edges; corners get stamped twice (same as adjacent line joins).
        stroke_segment(pixels, x0, y0, x1, y0, r, color, mask=mask, opacity=op, wrap=wrap)
        stroke_segment(pixels, x1, y0, x1, y1, r, color, mask=mask, opacity=op, wrap=wrap)
        stroke_segment(pixels, x1, y1, x0, y1, r, color, mask=mask, opacity=op, wrap=wrap)
        stroke_segment(pixels, x0, y1, x0, y0, r, color, mask=mask, opacity=op, wrap=wrap)

    def on_press(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        pixels = ctx.document.active_layer.pixels
        self._drawing = True
        self._sx, self._sy = x, y
        self._r = self._radius(ctx)
        if RectangleTool._active_master is None:
            RectangleTool._active_master = self
            self._snapshot = pixels.copy()
        self._paint_rect(ctx, x, y, shift, alt)
        ctx.document.mark_dirty()

    def on_drag(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        if not self._drawing:
            return
        pixels = ctx.document.active_layer.pixels
        if RectangleTool._active_master is self and self._snapshot is not None:
            np.copyto(pixels, self._snapshot)
        self._paint_rect(ctx, x, y, shift, alt)
        ctx.document.mark_dirty()

    def on_release(self, ctx: ToolContext, x: float, y: float) -> None:
        self._drawing = False
        if RectangleTool._active_master is self:
            RectangleTool._active_master = None
            self._snapshot = None

    def reset(self) -> None:
        self._drawing = False
        if RectangleTool._active_master is self:
            RectangleTool._active_master = None
        self._snapshot = None
