from __future__ import annotations

import numpy as np

from inkobold.core.image_meta import channel_max, prepare_paint_color
from inkobold.tools.base import BaseTool, ToolContext
from inkobold.tools.line import LineTool
from inkobold.tools.paint import flood_fill_gradient, stroke_segment


class GradientTool(BaseTool):
    """Drag a guide line; on release, flood-fill a gradient along that axis.

    The guide line is preview-only and is not left in the result.
    """

    name = "Gradient"
    id = "gradient"
    default_color = (0, 0, 0, 255)
    default_end_color = (255, 255, 255, 255)
    default_threshold = 200
    uses_size = False
    uses_threshold = True
    uses_opacity = True
    uses_end_color = True

    # Shared across mirror clones so only one branch restores the snapshot
    # before every clone redraws its guide on the same layer.
    _active_master: GradientTool | None = None

    def __init__(self) -> None:
        super().__init__()
        self.end_color: tuple[int, int, int, int] = self.default_end_color
        self._drawing = False
        self._sx = 0.0
        self._sy = 0.0
        self._shift = False
        self._snapshot: np.ndarray | None = None

    def _end_paint_color(self, ctx: ToolContext) -> tuple[int, int, int, int]:
        r, g, b, a = getattr(ctx, "end_color", None) or self.end_color
        color = (r, g, b, int(round(a * self._opacity_factor(ctx))))
        return prepare_paint_color(color, ctx.document.color_depth)

    def _preview_color(self, ctx: ToolContext) -> tuple[int, int, int, int]:
        """Semi-transparent start color for the temporary guide line."""
        r, g, b, _a = self._paint_color(ctx)
        max_v = channel_max(ctx.document.color_depth)
        return (r, g, b, int(round(max_v * 0.55)))

    def on_press(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        pixels = ctx.document.active_layer.pixels
        self._drawing = True
        self._sx, self._sy = x, y
        self._shift = shift
        if GradientTool._active_master is None:
            GradientTool._active_master = self
            self._snapshot = pixels.copy()
        stroke_segment(
            pixels,
            x, y, x, y,
            1.0,
            self._preview_color(ctx),
            mask=self._mask(ctx),
            opacity=1.0,
            wrap=ctx.tile_wrap,
        )
        ctx.document.mark_dirty()

    def on_drag(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        if not self._drawing:
            return
        self._shift = shift
        pixels = ctx.document.active_layer.pixels
        if GradientTool._active_master is self and self._snapshot is not None:
            np.copyto(pixels, self._snapshot)
        ex, ey = LineTool._snap_end(self._sx, self._sy, x, y, shift)
        stroke_segment(
            pixels,
            self._sx, self._sy, ex, ey,
            1.0,
            self._preview_color(ctx),
            mask=self._mask(ctx),
            opacity=1.0,
            wrap=ctx.tile_wrap,
        )
        ctx.document.mark_dirty()

    def on_release(self, ctx: ToolContext, x: float, y: float) -> None:
        if not self._drawing:
            return
        pixels = ctx.document.active_layer.pixels
        if GradientTool._active_master is self and self._snapshot is not None:
            np.copyto(pixels, self._snapshot)
        ex, ey = LineTool._snap_end(self._sx, self._sy, x, y, self._shift)
        flood_fill_gradient(
            pixels,
            int(round(self._sx)),
            int(round(self._sy)),
            self._sx,
            self._sy,
            ex,
            ey,
            self._paint_color(ctx),
            self._end_paint_color(ctx),
            tolerance=self._threshold(ctx),
            mask=self._mask(ctx),
            wrap=ctx.tile_wrap,
        )
        self._drawing = False
        if GradientTool._active_master is self:
            GradientTool._active_master = None
            self._snapshot = None
        ctx.document.mark_dirty()

    def reset(self) -> None:
        self._drawing = False
        if GradientTool._active_master is self:
            GradientTool._active_master = None
        self._snapshot = None
