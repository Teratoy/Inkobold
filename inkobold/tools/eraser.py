from __future__ import annotations

import math
from typing import Optional

import numpy as np

from inkobold.tools.base import BaseTool, ToolContext
from inkobold.tools.paint import stroke_segment, wrap_pixel_coords


class EraserTool(BaseTool):
    """Erase under the brush. Mode Freehand strokes freely; Line click-drags a
    straight segment (Shift snaps to 45°)."""

    name = "Eraser"
    id = "eraser"
    default_size = 24.0
    default_color = (0, 0, 0, 255)
    default_threshold = 255
    uses_color = False
    uses_threshold = True
    uses_opacity = True
    uses_eraser_modes = True
    default_eraser_mode = "freehand"

    # Shared across mirror clones so only one branch restores the snapshot
    # before every clone redraws its segment on the same layer.
    _active_master: EraserTool | None = None

    def __init__(self) -> None:
        super().__init__()
        self.eraser_mode: str = self.default_eraser_mode
        self._drawing = False
        self._lx = 0.0
        self._ly = 0.0
        self._sx = 0.0
        self._sy = 0.0
        self._r = 0.5
        self._key: Optional[tuple[int, int, int, int]] = None
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

    def _mode(self) -> str:
        mode = str(getattr(self, "eraser_mode", self.default_eraser_mode)).strip().lower()
        return mode if mode in ("freehand", "line") else self.default_eraser_mode

    def _sample_key(self, ctx: ToolContext, x: float, y: float) -> tuple[int, int, int, int]:
        ly = ctx.document.active_layer
        if ctx.tile_wrap:
            ix, iy = wrap_pixel_coords(x, y, ly.width, ly.height)
            return tuple(int(c) for c in ly.pixels[iy, ix])  # type: ignore[return-value]
        ix, iy = int(x), int(y)
        if 0 <= ix < ly.width and 0 <= iy < ly.height:
            return tuple(int(c) for c in ly.pixels[iy, ix])  # type: ignore[return-value]
        return (0, 0, 0, 0)

    def _erase_kwargs(self, ctx: ToolContext) -> dict:
        thr = self._threshold(ctx)
        key = self._key if ctx.threshold < 255 else None
        return {
            "erase": True,
            "mask": self._mask(ctx),
            "key_color": key,
            "threshold": thr if key is not None else None,
            "opacity": self._opacity_factor(ctx),
            "wrap": ctx.tile_wrap,
        }

    def _erase_segment(
        self,
        ctx: ToolContext,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        radius: float,
    ) -> None:
        stroke_segment(
            ctx.document.active_layer.pixels,
            x0, y0, x1, y1,
            radius,
            (0, 0, 0, 0),
            **self._erase_kwargs(ctx),
        )

    def on_press(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        self._drawing = True
        self._key = self._sample_key(ctx, x, y)
        # threshold max → erase all under brush; lower → only similar colors
        r = self._radius(ctx) * 1.2
        if self._mode() == "line":
            pixels = ctx.document.active_layer.pixels
            self._sx, self._sy = x, y
            self._r = r
            if EraserTool._active_master is None:
                EraserTool._active_master = self
                self._snapshot = pixels.copy()
            self._erase_segment(ctx, x, y, x, y, self._r)
        else:
            self._lx, self._ly = x, y
            self._erase_segment(ctx, x, y, x, y, r)
        ctx.document.mark_dirty()

    def on_drag(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        if not self._drawing:
            return
        if self._mode() == "line":
            pixels = ctx.document.active_layer.pixels
            if EraserTool._active_master is self and self._snapshot is not None:
                np.copyto(pixels, self._snapshot)
            ex, ey = self._snap_end(self._sx, self._sy, x, y, shift)
            self._erase_segment(ctx, self._sx, self._sy, ex, ey, self._r)
        else:
            self._erase_segment(
                ctx, self._lx, self._ly, x, y, self._radius(ctx) * 1.2,
            )
            self._lx, self._ly = x, y
        ctx.document.mark_dirty()

    def on_release(self, ctx: ToolContext, x: float, y: float) -> None:
        self._drawing = False
        self._key = None
        if EraserTool._active_master is self:
            EraserTool._active_master = None
            self._snapshot = None

    def reset(self) -> None:
        self._drawing = False
        self._key = None
        if EraserTool._active_master is self:
            EraserTool._active_master = None
        self._snapshot = None
