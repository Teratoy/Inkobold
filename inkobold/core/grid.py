"""Canvas grid overlay — visual guide only, does not affect drawing."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GridOverlay:
    """Document-space row/column guide drawn over the canvas.

    *rows* / *columns* are how many evenly spaced interior guides to draw
    (0–64). They divide the canvas into equal parts (``columns`` vertical
    guides → ``columns + 1`` equal columns). 0 omits that axis; 1×1 is a
    centered crosshair. *ox_norm* / *oy_norm* phase-shift the whole grid
    (document fractions, wrapped). The overlay never snaps tools or alters
    pixels.
    """

    enabled: bool = False
    rows: int = 8
    columns: int = 8
    ox_norm: float = 0.0
    oy_norm: float = 0.0

    def clamp(self) -> None:
        self.rows = max(0, min(64, int(self.rows)))
        self.columns = max(0, min(64, int(self.columns)))
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
