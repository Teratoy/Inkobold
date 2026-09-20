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

    def transform_branches(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> list[tuple[float, float, bool]]:
        """Return unique (x, y, reverse_orientation) branches for one pointer sample.

        *reverse_orientation* is True when the branch is an odd reflection (axis
        mirror). Handed constructions (e.g. circular arcs from a chord) must
        flip their bulge on those branches so the result matches a true mirror.
        Rotations preserve orientation.
        """
        if not self.enabled:
            return [(float(x), float(y), False)]
        self.clamp()
        cx = float(width) * 0.5
        cy = float(height) * 0.5
        base: list[tuple[float, float, bool]] = [(float(x), float(y), False)]
        if self.horizontal:
            base.append((2.0 * cx - float(x), float(y), True))
        if self.vertical:
            base = list(base) + [
                (px, 2.0 * cy - py, not rev) for px, py, rev in base
            ]

        n = self.radials
        if n <= 1:
            return _unique_branches(base)

        out: list[tuple[float, float, bool]] = []
        for k in range(n):
            ang = (2.0 * math.pi * k) / n
            cos_a = math.cos(ang)
            sin_a = math.sin(ang)
            for px, py, rev in base:
                dx = px - cx
                dy = py - cy
                out.append(
                    (
                        cx + dx * cos_a - dy * sin_a,
                        cy + dx * sin_a + dy * cos_a,
                        rev,
                    )
                )
        return _unique_branches(out)

    def transform_points(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> list[tuple[float, float]]:
        """Return unique document-space points for one pointer sample."""
        return [(px, py) for px, py, _rev in self.transform_branches(x, y, width, height)]

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


def _unique_branches(
    branches: list[tuple[float, float, bool]],
) -> list[tuple[float, float, bool]]:
    seen: set[tuple[float, float]] = set()
    out: list[tuple[float, float, bool]] = []
    for x, y, rev in branches:
        key = (round(x, 4), round(y, 4))
        if key in seen:
            continue
        seen.add(key)
        out.append((x, y, rev))
    return out
