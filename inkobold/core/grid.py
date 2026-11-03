"""Canvas grid overlay — visual guide only, does not affect drawing."""

from __future__ import annotations

import math
from dataclasses import dataclass

# Pixel-art zoom ladder: whole multiples above 1×, halvings below, so every
# document pixel covers the same number of screen pixels.
PIXEL_ZOOM_LEVELS: tuple[float, ...] = (
    0.125, 0.25, 0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0, 32.0, 48.0, 64.0,
)
# Per-pixel lines appear from this zoom up (pixel mode only).
PIXEL_GRID_MIN_ZOOM = 8.0


def pixel_zoom_step(zoom: float, zoom_in: bool) -> float:
    """Next pixel-ladder zoom above (or below) *zoom*; clamps at the ends."""
    z = float(zoom)
    if zoom_in:
        for level in PIXEL_ZOOM_LEVELS:
            if level > z * 1.001:
                return level
        return PIXEL_ZOOM_LEVELS[-1]
    for level in reversed(PIXEL_ZOOM_LEVELS):
        if level < z * 0.999:
            return level
    return PIXEL_ZOOM_LEVELS[0]


def pixel_zoom_floor(zoom: float) -> float:
    """Largest pixel-ladder zoom not above *zoom* (the smallest level at least)."""
    z = float(zoom)
    best = PIXEL_ZOOM_LEVELS[0]
    for level in PIXEL_ZOOM_LEVELS:
        if level <= z * 1.001:
            best = level
    return best


def grid_lines(lo: float, hi: float, step: int, limit: int) -> list[int]:
    """Multiples of *step* inside [lo, hi] clamped to [0, limit] (visible lines only)."""
    s = max(1, int(step))
    start = max(0, int(math.ceil(max(0.0, float(lo)) / s)) * s)
    end = min(int(limit), int(math.floor(float(hi))))
    return list(range(start, end + 1, s))


@dataclass
class GridOverlay:
    """Document-space row/column guide drawn over the canvas.

    *rows* / *columns* are how many evenly spaced interior guides to draw
    (0–64). They divide the canvas into equal parts (``columns`` vertical
    guides → ``columns + 1`` equal columns). 0 omits that axis; 1×1 is a
    centered crosshair. *ox_norm* / *oy_norm* phase-shift the whole grid
    (document fractions, wrapped). The overlay never snaps tools or alters
    pixels.

    In Pixel Art mode Show Grid also draws a tile grid every *tile* pixels
    (0 = none); the per-pixel grid shows on its own when zoomed in.
    """

    enabled: bool = False
    rows: int = 8
    columns: int = 8
    ox_norm: float = 0.0
    oy_norm: float = 0.0
    tile: int = 16

    def clamp(self) -> None:
        self.rows = max(0, min(64, int(self.rows)))
        self.columns = max(0, min(64, int(self.columns)))
        self.tile = max(0, min(1024, int(self.tile)))
        self.ox_norm = float(self.ox_norm) % 1.0
        self.oy_norm = float(self.oy_norm) % 1.0

    def reset_offset(self) -> None:
        """Restore the default equal-division layout (no phase offset)."""
        self.ox_norm = 0.0
        self.oy_norm = 0.0

    def nudge_offset(self, dx_norm: float, dy_norm: float) -> None:
        self.ox_norm += float(dx_norm)
        self.oy_norm += float(dy_norm)
        self.clamp()

    @staticmethod
    def _guide_positions(count: int, span: float, offset_norm: float) -> list[float]:
        """Evenly spaced interior guide coordinates along *span*."""
        n = int(count)
        denom = n + 1
        off = float(offset_norm)
        s = float(span)
        return [s * (((i + 1) / denom + off) % 1.0) for i in range(n)]

    def guide_segments(
        self,
        width: float,
        height: float,
    ) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        """Document-space interior guide lines (no canvas-edge border)."""
        if not self.enabled:
            return []
        self.clamp()
        w = float(width)
        h = float(height)
        segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
        for x in self._guide_positions(self.columns, w, self.ox_norm):
            segs.append(((x, 0.0), (x, h)))
        for y in self._guide_positions(self.rows, h, self.oy_norm):
            segs.append(((0.0, y), (w, y)))
        return segs

    def hit_axis(
        self,
        doc_x: float,
        doc_y: float,
        width: float,
        height: float,
        *,
        tol: float,
    ) -> str | None:
        """Return ``x``, ``y``, ``both``, or None for a nearby guide line."""
        if not self.enabled:
            return None
        self.clamp()
        w = max(1.0, float(width))
        h = max(1.0, float(height))
        tol = max(0.5, float(tol))
        hit_v = False
        hit_h = False
        for x in self._guide_positions(self.columns, w, self.ox_norm):
            if abs(float(doc_x) - x) <= tol:
                hit_v = True
                break
        for y in self._guide_positions(self.rows, h, self.oy_norm):
            if abs(float(doc_y) - y) <= tol:
                hit_h = True
                break
        if hit_v and hit_h:
            return "both"
        if hit_v:
            return "x"
        if hit_h:
            return "y"
        return None
