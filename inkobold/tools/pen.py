from __future__ import annotations

from inkobold.tools.base import BaseTool, ToolContext
from inkobold.tools.paint import stroke_segment


class PenTool(BaseTool):
    """Freehand stroke with constant width (Size only; ignores pressure)."""

    name = "Pen"
    id = "pen"
    default_size = 8.0
    default_color = (0, 0, 0, 255)
    uses_opacity = True

    def __init__(self) -> None:
        super().__init__()
        self._drawing = False
        self._lx = 0.0
        self._ly = 0.0

    def _radius(self, ctx: ToolContext) -> float:
        return max(0.5, float(ctx.brush_size))

    def on_press(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        self._drawing = True
        self._lx, self._ly = x, y
        stroke_segment(
            ctx.document.active_layer.pixels,
            x, y, x, y,
            self._radius(ctx),
            self._paint_color(ctx),
            mask=self._mask(ctx),
            opacity=self._opacity_factor(ctx),
            wrap=ctx.tile_wrap,
        )
        ctx.document.mark_dirty()

    def on_drag(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        if not self._drawing:
            return
        stroke_segment(
            ctx.document.active_layer.pixels,
            self._lx, self._ly, x, y,
            self._radius(ctx),
            self._paint_color(ctx),
            mask=self._mask(ctx),
            opacity=self._opacity_factor(ctx),
            wrap=ctx.tile_wrap,
        )
        self._lx, self._ly = x, y
        ctx.document.mark_dirty()

    def on_release(self, ctx: ToolContext, x: float, y: float) -> None:
        self._drawing = False

    def reset(self) -> None:
        self._drawing = False
