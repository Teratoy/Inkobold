from __future__ import annotations

import math

from inkobold.core.effects import LIQUIFY_BRUSH_MODES, liquify_brush_stamp
from inkobold.tools.base import BaseTool, ToolContext


class LiquifyTool(BaseTool):
    """Warp pixels under the brush (push / swirl / pinch / bulge).

    Intensity sets warp strength; pressure fine-tunes size and strength.
    Push follows the stroke direction; the other modes warp around the tip.
    """

    name = "Liquify"
    id = "liquify"
    default_size = 48.0
    default_intensity = 45.0
    uses_color = False
    uses_intensity = True
    uses_liquify_modes = True
    default_liquify_mode = "Push"

    def __init__(self) -> None:
        super().__init__()
        self.liquify_mode: str = self.default_liquify_mode
        self._drawing = False
        self._lx = 0.0
        self._ly = 0.0

    def _radius(self, ctx: ToolContext) -> float:
        p = max(0.0, min(1.0, float(ctx.pressure)))
        return max(1.0, ctx.brush_size * (0.55 + 0.45 * p))

    def _strength(self, ctx: ToolContext) -> float:
        base = max(0.0, min(100.0, float(ctx.intensity))) / 100.0
        p = max(0.0, min(1.0, float(ctx.pressure)))
        return max(0.0, min(1.0, base * (0.55 + 0.45 * p)))

    def _mode(self, ctx: ToolContext) -> str:
        mode = str(getattr(self, "liquify_mode", self.default_liquify_mode)).strip().title()
        if mode not in LIQUIFY_BRUSH_MODES:
            return self.default_liquify_mode
        return mode

    def on_press(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        self._drawing = True
        self._lx, self._ly = x, y
        # Stationary swirl / pinch / bulge on click
        mode = self._mode(ctx)
        if mode == "Push":
            return
        strength = self._strength(ctx)
        if strength <= 0.0:
            return
        liquify_brush_stamp(
            ctx.document.active_layer.pixels,
            x,
            y,
            self._radius(ctx),
            mode=mode,
            strength=strength * 0.35,
            mask=self._mask(ctx),
            wrap=ctx.tile_wrap,
        )
        ctx.document.mark_dirty()

    def on_drag(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        if not self._drawing:
            return
        r = self._radius(ctx)
        strength = self._strength(ctx)
        if strength <= 0.0:
            self._lx, self._ly = x, y
            return
        dist = float(math.hypot(x - self._lx, y - self._ly))
        if dist < 0.35:
            return
        mode = self._mode(ctx)
        steps = max(1, int(dist / max(0.5, r * 0.35)))
        pixels = ctx.document.active_layer.pixels
        mask = self._mask(ctx)
        wrap = ctx.tile_wrap
        for i in range(1, steps + 1):
            t = i / steps
            px = self._lx + (x - self._lx) * t
            py = self._ly + (y - self._ly) * t
            liquify_brush_stamp(
                pixels,
                px,
                py,
                r,
                mode=mode,
                strength=strength,
                dir_x=x - self._lx,
                dir_y=y - self._ly,
                mask=mask,
                wrap=wrap,
            )
        self._lx, self._ly = x, y
        ctx.document.mark_dirty()

    def on_release(self, ctx: ToolContext, x: float, y: float) -> None:
        self._drawing = False

    def reset(self) -> None:
        self._drawing = False
