from __future__ import annotations

from inkobold.tools.base import BaseTool, ToolContext
from inkobold.tools.paint import stroke_segment_tapered


class BrushTool(BaseTool):
    """Freehand stroke whose width tracks pen pressure (Size = max width)."""

    name = "Brush"
    id = "brush"
    default_size = 12.0
    default_color = (0, 0, 0, 255)
    uses_opacity = True

    def __init__(self) -> None:
        super().__init__()
        self._drawing = False
        self._lx = 0.0
        self._ly = 0.0
        self._lr = 0.5

    def _pressure_radius(self, ctx: ToolContext) -> float:
        # Size is the maximum radius; light pressure → thin, full press → Size.
        p = max(0.0, min(1.0, float(ctx.pressure)))
        # Mild power curve feels more natural on tablets than pure linear.
        shaped = p**0.75
        return max(0.5, ctx.brush_size * (0.06 + 0.94 * shaped))

    def on_press(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        self._drawing = True
        self._lx, self._ly = x, y
        self._lr = self._pressure_radius(ctx)
        stroke_segment_tapered(
            ctx.document.active_layer.pixels,
            x, y, x, y,
            self._lr, self._lr,
            self._paint_color(ctx),
            mask=self._mask(ctx),
        )
        ctx.document.mark_dirty()

    def on_drag(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        if not self._drawing:
            return
        r1 = self._pressure_radius(ctx)
        stroke_segment_tapered(
            ctx.document.active_layer.pixels,
            self._lx, self._ly, x, y,
            self._lr, r1,
            self._paint_color(ctx),
            mask=self._mask(ctx),
        )
        self._lx, self._ly = x, y
        self._lr = r1
        ctx.document.mark_dirty()

    def on_release(self, ctx: ToolContext, x: float, y: float) -> None:
        self._drawing = False

    def reset(self) -> None:
        self._drawing = False
