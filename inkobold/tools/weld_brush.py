from __future__ import annotations

from inkobold.tools.base import BaseTool, ToolContext
from inkobold.tools.paint import stroke_segment_weld


class WeldBrushTool(BaseTool):
    """Round pressure brush that welds into overlapping same-color strokes.

    Welding happens live: after each segment is stamped, a disk morphological
    close around it fillets the concave junctions the new paint just created
    (metaball / ink-pool look), so what you see while dragging is the final
    result. Intensity (0–300) controls fillet size relative to brush radius;
    the fillet follows pressure like the brush radius does.
    """

    name = "Weld Brush"
    id = "weld_brush"
    default_size = 12.0
    default_color = (0, 0, 0, 255)
    default_intensity = 65.0
    uses_opacity = True
    uses_intensity = True
    uses_brush_options = False

    def __init__(self) -> None:
        super().__init__()
        self._drawing = False
        self._lx = 0.0
        self._ly = 0.0
        self._lr = 0.5

    def _pressure_radius(self, ctx: ToolContext) -> float:
        p = max(0.0, min(1.0, float(ctx.pressure)))
        shaped = p**0.75
        return max(0.5, ctx.brush_size * (0.06 + 0.94 * shaped))

    def _weld_factor(self, ctx: ToolContext) -> float:
        return max(0.0, min(300.0, float(ctx.intensity))) / 100.0

    def _segment(self, ctx: ToolContext, x: float, y: float, r1: float) -> None:
        stroke_segment_weld(
            ctx.document.active_layer.pixels,
            self._lx, self._ly, x, y,
            self._lr, r1,
            self._paint_color(ctx),
            mask=self._mask(ctx),
            opacity=self._opacity_factor(ctx),
            wrap=ctx.tile_wrap,
            weld=self._weld_factor(ctx),
        )
        self._lx, self._ly = x, y
        self._lr = r1
        ctx.document.mark_dirty()

    def on_press(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        self._drawing = True
        self._lx, self._ly = x, y
        self._lr = self._pressure_radius(ctx)
        self._segment(ctx, x, y, self._lr)

    def on_drag(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        if not self._drawing:
            return
        self._segment(ctx, x, y, self._pressure_radius(ctx))

    def on_release(self, ctx: ToolContext, x: float, y: float) -> None:
        self._drawing = False

    def reset(self) -> None:
        self._drawing = False
