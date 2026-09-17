"""Canvas grid overlay — visual guide only, does not affect drawing."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GridOverlay:
    """Document-space row/column guide drawn over the canvas.

    *rows* / *columns* are how many evenly spaced interior guides to draw
    (through the center of each equal band). 1×1 is a centered crosshair.
    The overlay never snaps tools or alters pixels.
    """

    enabled: bool = False
    rows: int = 8
    columns: int = 8

    def clamp(self) -> None:
        self.rows = max(1, min(64, int(self.rows)))
        self.columns = max(1, min(64, int(self.columns)))

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
        cols = self.columns
        rows = self.rows
        for i in range(cols):
            x = w * (i + 0.5) / cols
            segs.append(((x, 0.0), (x, h)))
        for j in range(rows):
            y = h * (j + 0.5) / rows
            segs.append(((0.0, y), (w, y)))
        return segs