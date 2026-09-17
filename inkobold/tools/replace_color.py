from __future__ import annotations

from typing import Optional

from inkobold.tools.base import BaseTool, ToolContext
from inkobold.tools.paint import (
    background_erase_stroke,
    erase_matching_colors,
    flood_erase,
    flood_fill,
    replace_matching_colors,
    stroke_segment,
)


class ReplaceColorTool(BaseTool):
    """Replace or erase colors by brush, flood, or whole-layer match.

    *Replace* paints the tool color over matches (click samples the key).
    *Erase* clears alpha on pixels matching the tool color (BG-eraser style).
    Scope: Brush, Fill (connected), or All (global matches).
    """

    name = "Replace"
    id = "replace"
    default_color = (0, 0, 0, 255)
    default_size = 28.0
    default_threshold = 40
    uses_threshold = True
    uses_opacity = True
    uses_replace_modes = True

    def __init__(self) -> None:
        super().__init__()
        self.replace_action: str = "replace"  # "replace" | "erase"
        self.apply_mode: str = "all"  # "brush" | "fill" | "all"
        self._drawing = False
        self._lx = 0.0
        self._ly = 0.0
        self._key: Optional[tuple[int, int, int, int]] = None

    def _action(self, ctx: ToolContext) -> str:
        return getattr(ctx, "replace_action", None) or self.replace_action

    def _mode(self, ctx: ToolContext) -> str:
        return getattr(ctx, "apply_mode", None) or self.apply_mode

    def _match_key(self, ctx: ToolContext) -> tuple[int, int, int, int]:
        """Selected color as RGB match key (erase / BG-eraser style)."""
        from inkobold.core.image_meta import prepare_paint_color

        r, g, b, _a = ctx.color
        return prepare_paint_color((r, g, b, 255), ctx.document.color_depth)

    def _sample_key(self, ctx: ToolContext, x: float, y: float) -> Optional[tuple[int, int, int, int]]:
        ly = ctx.document.active_layer
        ix, iy = int(x), int(y)
        if not (0 <= ix < ly.width and 0 <= iy < ly.height):
            return None
        return tuple(int(c) for c in ly.pixels[iy, ix])  # type: ignore[return-value]

    def on_press(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        action = self._action(ctx)
        mode = self._mode(ctx)
        pixels = ctx.document.active_layer.pixels
        thr = self._threshold(ctx)
        mask = self._mask(ctx)
        op = self._opacity_factor(ctx)

        if action == "erase":
            key = self._match_key(ctx)
            self._key = key
            if mode == "all":
                erase_matching_colors(pixels, key, tolerance=thr, mask=mask, opacity=op)
                ctx.document.mark_dirty()
                return
            if mode == "fill":
                flood_erase(pixels, int(x), int(y), key, tolerance=thr, mask=mask, opacity=op)
                ctx.document.mark_dirty()
                return
            # brush
            self._drawing = True
            self._lx, self._ly = x, y
            background_erase_stroke(
                pixels, x, y, x, y, self._radius(ctx) * 1.4, key,
                tolerance=thr, mask=mask, opacity=op,
            )
            ctx.document.mark_dirty()
            return

        # replace — key from clicked pixel
        key = self._sample_key(ctx, x, y)
        self._key = key
        if key is None:
            return
        paint = self._paint_color(ctx)
        if mode == "all":
            replace_matching_colors(pixels, int(x), int(y), paint, tolerance=thr, mask=mask)
            ctx.document.mark_dirty()
            return
        if mode == "fill":
            flood_fill(pixels, int(x), int(y), paint, tolerance=thr, mask=mask)
            ctx.document.mark_dirty()
            return
        # brush
        self._drawing = True
        self._lx, self._ly = x, y
        stroke_segment(
            pixels, x, y, x, y, self._radius(ctx) * 1.4, paint,
            mask=mask, key_color=key, threshold=thr, replace=True, opacity=op,
        )
        ctx.document.mark_dirty()

    def on_drag(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        if self._mode(ctx) != "brush" or not self._drawing or self._key is None:
            return
        pixels = ctx.document.active_layer.pixels
        thr = self._threshold(ctx)
        mask = self._mask(ctx)
        op = self._opacity_factor(ctx)
        r = self._radius(ctx) * 1.4
        if self._action(ctx) == "erase":
            background_erase_stroke(
                pixels, self._lx, self._ly, x, y, r, self._key,
                tolerance=thr, mask=mask, opacity=op,
            )
        else:
            stroke_segment(
                pixels, self._lx, self._ly, x, y, r, self._paint_color(ctx),
                mask=mask, key_color=self._key, threshold=thr, replace=True, opacity=op,
            )
        self._lx, self._ly = x, y
        ctx.document.mark_dirty()

    def on_release(self, ctx: ToolContext, x: float, y: float) -> None:
        self._drawing = False

    def reset(self) -> None:
        self._drawing = False
        self._key = None
