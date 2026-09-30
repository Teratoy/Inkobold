from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from inkobold.tools.base import BaseTool, ToolContext
from inkobold.tools.paint import (
    lay_pattern_along_circle,
    lay_pattern_along_circular_arc,
    lay_pattern_along_quadratic,
    lay_pattern_along_segment,
)


class LayTool(BaseTool):
    """Lay pattern-library elements along a line or curve path.

    Modes mirror Line + Curve: straight segment, freehand Bézier, circular arc,
    or full circle. Elements follow the path tangent. Size is the longest side
    of each stamp; Distance is spacing along the path between stamp centers.
    """

    name = "Lay"
    id = "lay"
    default_size = 32.0
    default_color = (0, 0, 0, 255)
    uses_color = False
    uses_opacity = True
    uses_lay_options = True
    uses_curve_modes = False  # own shape modes include Line
    default_lay_mode = "line"
    default_arc_degrees = 180.0
    default_lay_spacing = 40.0

    _active_master: LayTool | None = None
    _DEFAULT_BULGE = 0.35
    _MIN_BOW = 1.5
    _LAY_MODES = ("line", "freehand", "arc", "circle")

    def __init__(self) -> None:
        super().__init__()
        self.lay_mode: str = self.default_lay_mode
        self.arc_degrees: float = self.default_arc_degrees
        self.lay_spacing: float = self.default_lay_spacing
        self.pattern_path: Optional[Path] = None
        self._pattern_cache_path: Optional[Path] = None
        self._pattern_cache_mtime: float = -1.0
        self._pattern_pixels: Optional[np.ndarray] = None
        self._drawing = False
        self._sx = 0.0
        self._sy = 0.0
        self._samples: list[tuple[float, float]] = []
        self._snapshot: np.ndarray | None = None

    def set_pattern_path(self, path: Optional[Path]) -> None:
        self.pattern_path = Path(path) if path is not None else None
        self._pattern_pixels = None
        self._pattern_cache_path = None
        self._pattern_cache_mtime = -1.0

    def _load_pattern(self) -> Optional[np.ndarray]:
        path = self.pattern_path
        if path is None or not path.is_file():
            return None
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        if (
            self._pattern_pixels is not None
            and self._pattern_cache_path == path
            and self._pattern_cache_mtime == mtime
        ):
            return self._pattern_pixels
        try:
            arr = np.array(Image.open(path).convert("RGBA"), dtype=np.uint8)
        except OSError:
            return None
        self._pattern_pixels = arr
        self._pattern_cache_path = path
        self._pattern_cache_mtime = mtime
        return arr

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

    @classmethod
    def _control_point(
        cls,
        samples: list[tuple[float, float]],
        p0: tuple[float, float],
        p2: tuple[float, float],
        alt: bool,
    ) -> tuple[float, float]:
        dx = p2[0] - p0[0]
        dy = p2[1] - p0[1]
        chord = math.hypot(dx, dy)
        mid = ((p0[0] + p2[0]) * 0.5, (p0[1] + p2[1]) * 0.5)
        if chord < 1e-6:
            return mid

        nx = -dy / chord
        ny = dx / chord
        best_pt: tuple[float, float] | None = None
        best_abs = 0.0
        best_signed = 0.0
        for x, y in samples:
            signed = (x - p0[0]) * nx + (y - p0[1]) * ny
            a = abs(signed)
            if a > best_abs:
                best_abs = a
                best_signed = signed
                best_pt = (x, y)

        if best_pt is None or best_abs < cls._MIN_BOW:
            sign = -1.0 if alt else 1.0
            if not alt:
                return mid
            offset = chord * cls._DEFAULT_BULGE * sign
            through = (mid[0] + nx * offset, mid[1] + ny * offset)
        else:
            through = best_pt
            if alt:
                proj = best_signed
                through = (through[0] - 2.0 * proj * nx, through[1] - 2.0 * proj * ny)

        return (2.0 * through[0] - mid[0], 2.0 * through[1] - mid[1])

    def _element_size(self, ctx: ToolContext) -> float:
        return max(1.0, float(ctx.brush_size))

    def _spacing(self, ctx: ToolContext) -> float:
        return max(1.0, float(getattr(ctx, "lay_spacing", self.lay_spacing)))

    def _paint(self, ctx: ToolContext, ex: float, ey: float, alt: bool) -> None:
        pattern = self._load_pattern()
        if pattern is None:
            return
        pixels = ctx.document.active_layer.pixels
        mask = self._mask(ctx)
        wrap = ctx.tile_wrap
        op = self._opacity_factor(ctx)
        size = self._element_size(ctx)
        spacing = self._spacing(ctx)
        mode = self.lay_mode if self.lay_mode in self._LAY_MODES else "line"

        if mode == "line":
            lay_pattern_along_segment(
                pixels, self._sx, self._sy, ex, ey, pattern, size, spacing,
                mask=mask, opacity=op, wrap=wrap,
            )
        elif mode == "arc":
            lay_pattern_along_circular_arc(
                pixels,
                self._sx, self._sy, ex, ey,
                float(self.arc_degrees),
                pattern, size, spacing,
                mask=mask, opacity=op, flip=alt, wrap=wrap,
            )
        elif mode == "circle":
            if alt:
                cx = (self._sx + ex) * 0.5
                cy = (self._sy + ey) * 0.5
                r_circ = math.hypot(ex - self._sx, ey - self._sy) * 0.5
            else:
                cx, cy = self._sx, self._sy
                r_circ = math.hypot(ex - self._sx, ey - self._sy)
            lay_pattern_along_circle(
                pixels, cx, cy, r_circ, pattern, size, spacing,
                mask=mask, opacity=op, wrap=wrap,
            )
        else:
            p0 = (self._sx, self._sy)
            p2 = (ex, ey)
            p1 = self._control_point(self._samples, p0, p2, alt)
            lay_pattern_along_quadratic(
                pixels,
                p0[0], p0[1], p1[0], p1[1], p2[0], p2[1],
                pattern, size, spacing,
                mask=mask, opacity=op, wrap=wrap,
            )
        ctx.document.mark_dirty()

    def on_press(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        pixels = ctx.document.active_layer.pixels
        self._drawing = True
        self._sx, self._sy = x, y
        self._samples = [(x, y)]
        if LayTool._active_master is None:
            LayTool._active_master = self
            self._snapshot = pixels.copy()
        self._paint(ctx, x, y, alt)

    def on_drag(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        if not self._drawing:
            return
        pixels = ctx.document.active_layer.pixels
        if LayTool._active_master is self and self._snapshot is not None:
            np.copyto(pixels, self._snapshot)
        ex, ey = self._snap_end(self._sx, self._sy, x, y, shift)
        if self.lay_mode == "freehand":
            self._samples.append((ex, ey))
            if len(self._samples) > 512:
                self._samples = self._samples[::2]
        self._paint(ctx, ex, ey, alt)

    def on_release(self, ctx: ToolContext, x: float, y: float) -> None:
        self._drawing = False
        self._samples = []
        if LayTool._active_master is self:
            LayTool._active_master = None
            self._snapshot = None

    def reset(self) -> None:
        self._drawing = False
        self._samples = []
        if LayTool._active_master is self:
            LayTool._active_master = None
        self._snapshot = None
