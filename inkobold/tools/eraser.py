from __future__ import annotations

from typing import Optional

from inkobold.tools.base import BaseTool, ToolContext
from inkobold.tools.paint import stroke_segment


class EraserTool(BaseTool):
    name = "Eraser"
    id = "eraser"
    default_size = 24.0
    default_color = (0, 0, 0, 255)
    default_threshold = 255
    uses_color = False
    uses_threshold = True
    uses_opacity = True

    def __init__(self) -> None:
        super().__init__()
        self._drawing = False
        self._lx = 0.0
        self._ly = 0.0
        self._key: Optional[tuple[int, int, int, int]] = None

    def on_press(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        self._drawing = True
        self._lx, self._ly = x, y
        ly = ctx.document.active_layer
        ix, iy = int(x), int(y)
        if 0 <= ix < ly.width and 0 <= iy < ly.height:
            self._key = tuple(int(c) for c in ly.pixels[iy, ix])  # type: ignore[assignment]
        else:
            self._key = (0, 0, 0, 0)
        # threshold max → erase all under brush; lower → only similar colors
        thr = self._threshold(ctx)
        key = self._key if ctx.threshold < 255 else None
        stroke_segment(
            ly.pixels,
            x, y, x, y,
            self._radius(ctx) * 1.2,
            (0, 0, 0, 0),
            erase=True,
            mask=self._mask(ctx),
            key_color=key,
            threshold=thr if key is not None else None,
            opacity=self._opacity_factor(ctx),
        )
        ctx.document.mark_dirty()

    def on_drag(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        if not self._drawing:
            return
        thr = self._threshold(ctx)
        key = self._key if ctx.threshold < 255 else None
        stroke_segment(
            ctx.document.active_layer.pixels,
            self._lx, self._ly, x, y,
            self._radius(ctx) * 1.2,
            (0, 0, 0, 0),
            erase=True,
            mask=self._mask(ctx),
            key_color=key,
            threshold=thr if key is not None else None,
            opacity=self._opacity_factor(ctx),
        )
        self._lx, self._ly = x, y
        ctx.document.mark_dirty()

    def on_release(self, ctx: ToolContext, x: float, y: float) -> None:
        self._drawing = False
        self._key = None

    def reset(self) -> None:
        self._drawing = False
        self._key = None
