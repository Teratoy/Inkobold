from __future__ import annotations

from typing import Optional

import numpy as np

from inkobold.tools.base import BaseTool, ToolContext
from inkobold.tools.paint import smear_sample_tip, smear_stamp


class SmearTool(BaseTool):
    """Smudge existing pixels along the stroke. Intensity sets strength; pressure fine-tunes it."""

    name = "Smear"
    id = "smear"
    default_size = 20.0
    default_intensity = 50.0
    uses_color = False
    uses_intensity = True

    def __init__(self) -> None:
        super().__init__()
        self._drawing = False
        self._lx = 0.0
        self._ly = 0.0
        self._tip: Optional[np.ndarray] = None

    def _radius(self, ctx: ToolContext) -> float:
        # Mild pressure on size so light strokes smear a smaller area
        p = max(0.0, min(1.0, float(ctx.pressure)))
        return max(0.5, ctx.brush_size * (0.55 + 0.45 * p))

    def _strength(self, ctx: ToolContext) -> float:
        # Intensity 0–100 → base strength; pressure scales it mildly (mouse ≈ full)
        base = max(0.0, min(100.0, float(ctx.intensity))) / 100.0
        p = max(0.0, min(1.0, float(ctx.pressure)))
        return max(0.0, min(1.0, base * (0.55 + 0.45 * p)))

    def on_press(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        self._drawing = True
        self._lx, self._ly = x, y
        self._tip = smear_sample_tip(ctx.document.active_layer.pixels, x, y, self._radius(ctx))

    def on_drag(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        if not self._drawing or self._tip is None:
            return
        r = self._radius(ctx)
        strength = self._strength(ctx)
        if strength <= 0.0:
            self._lx, self._ly = x, y
            return
        dist = float(np.hypot(x - self._lx, y - self._ly))
        steps = max(1, int(dist / max(0.5, r * 0.35)))
        pixels = ctx.document.active_layer.pixels
        mask = self._mask(ctx)
        for i in range(1, steps + 1):
            t = i / steps
            px = self._lx + (x - self._lx) * t
            py = self._ly + (y - self._ly) * t
            smear_stamp(pixels, px, py, r, self._tip, strength=strength, mask=mask)
        self._lx, self._ly = x, y
        ctx.document.mark_dirty()

    def on_release(self, ctx: ToolContext, x: float, y: float) -> None:
        self._drawing = False
        self._tip = None

    def reset(self) -> None:
        self._drawing = False
        self._tip = None
