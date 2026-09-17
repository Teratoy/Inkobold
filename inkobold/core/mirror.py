"""Live drawing mirror / radial symmetry modifier."""

from __future__ import annotations

import math
from dataclasses import dataclass


ORIENTATIONS = (
    ("horizontal", "Horizontal"),
    ("vertical", "Vertical"),
    ("both", "Both"),
)


@dataclass
class MirrorModifier:
    """Symmetry applied to paint strokes around the document center.

    *orientation* selects axis mirrors (across the vertical and/or horizontal
    midlines). *radials* adds rotational copies (1 = none beyond the
    orientation set).
    """

    enabled: bool = False
    orientation: str = "horizontal"  # horizontal | vertical | both
    radials: int = 1

    def clamp(self) -> None:
        if self.orientation not in {"horizontal", "vertical", "both"}:
            self.orientation = "horizontal"
        self.radials = max(1, min(16, int(self.radials)))

    @property
    def horizontal(self) -> bool:
        return self.orientation in ("horizontal", "both")

    @property
    def vertical(self) -> bool:
        return self.orientation in ("vertical", "both")

    def transform_points(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> list[tuple[float, float]]:
        """Return unique document-space points for one pointer sample."""
        if not self.enabled:
            return [(x, y)]
        self.clamp()
        cx = float(width) * 0.5
        cy = float(height) * 0.5
        base: list[tuple[float, float]] = [(float(x), float(y))]
        if self.horizontal:
            base.append((2.0 * cx - float(x), float(y)))
        if self.vertical:
            base = list(base) + [(px, 2.0 * cy - py) for px, py in base]

        n = self.radials
        if n <= 1:
            return _unique(base)

        out: list[tuple[float, float]] = []
        for k in range(n):
            ang = (2.0 * math.pi * k) / n
            cos_a = math.cos(ang)
            sin_a = math.sin(ang)
            for px, py in base:
                dx = px - cx
                dy = py - cy
                out.append((cx + dx * cos_a - dy * sin_a, cy + dx * sin_a + dy * cos_a))
        return _unique(out)

    def guide_segments(
        self,
        width: float,
        height: float,
    ) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        """Document-space line segments for axis / radial guides."""
        if not self.enabled:
            return []
        self.clamp()
        w = float(width)
        h = float(height)
        cx, cy = w * 0.5, h * 0.5
        segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
        if self.horizontal:
            segs.append(((cx, 0.0), (cx, h)))
        if self.vertical:
            segs.append(((0.0, cy), (w, cy)))
        n = self.radials
        if n >= 2:
            # Radial rays from center; length reaches the farther canvas edge.
            radius = math.hypot(cx, cy)
            for k in range(n):
                ang = (2.0 * math.pi * k) / n
                # Skip axes already drawn by H/V mirrors when they coincide.
                end = (cx + math.cos(ang) * radius, cy + math.sin(ang) * radius)
                start = (cx - math.cos(ang) * radius, cy - math.sin(ang) * radius)
                if n % 2 == 0 and k >= n // 2:
                    continue  # opposite ray already covered by start→end
                segs.append((start, end))
        return segs


def _unique(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    seen: set[tuple[float, float]] = set()
    out: list[tuple[float, float]] = []
    for x, y in points:
        key = (round(x, 4), round(y, 4))
        if key in seen:
            continue
        seen.add(key)
        out.append((x, y))
    return out
