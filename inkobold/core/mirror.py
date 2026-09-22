"""Live drawing mirror / radial symmetry modifier."""

from __future__ import annotations

import math
from dataclasses import dataclass


# First-axis angle offset (degrees). The axis *line* angle: Horizontal = left↔right
# mirror (vertical line), Vertical = top↔bottom (horizontal line), Diagonal = 45°.
ORIENTATIONS = (
    ("horizontal", "Horizontal"),
    ("vertical", "Vertical"),
    ("diagonal", "Diagonal"),
)

_BASE_ANGLE = {
    "horizontal": math.pi * 0.5,  # vertical axis line
    "vertical": 0.0,              # horizontal axis line
    "diagonal": math.pi * 0.25,
}


@dataclass
class MirrorModifier:
    """Symmetry applied to paint strokes around the document center.

    *axes* is the number of equally spaced reflection axes through the center
    (angle between neighbors is always 180°/axes). *orientation* rotates the
    whole set so the first axis is horizontal, vertical, or diagonal.
    """

    enabled: bool = False
    orientation: str = "horizontal"  # horizontal | vertical | diagonal
    axes: int = 1

    def clamp(self) -> None:
        if self.orientation not in _BASE_ANGLE:
            self.orientation = "horizontal"
        self.axes = max(1, min(16, int(self.axes)))

    # Back-compat alias used by older call sites / UI wiring during rename.
    @property
    def radials(self) -> int:
        return self.axes

    @radials.setter
    def radials(self, value: int) -> None:
        self.axes = int(value)

    def _base_angle(self) -> float:
        return _BASE_ANGLE.get(self.orientation, math.pi * 0.5)

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
        dx = float(x) - cx
        dy = float(y) - cy
        n = self.axes
        base = self._base_angle()
        fx, fy = _reflect_across(dx, dy, base)

        out: list[tuple[float, float, bool]] = []
        for k in range(n):
            ang = (2.0 * math.pi * k) / n
            rx, ry = _rotate(dx, dy, ang)
            out.append((cx + rx, cy + ry, False))
            rx, ry = _rotate(fx, fy, ang)
            out.append((cx + rx, cy + ry, True))
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
        """Document-space line segments for the equally spaced mirror axes."""
        if not self.enabled:
            return []
        self.clamp()
        w = float(width)
        h = float(height)
        cx, cy = w * 0.5, h * 0.5
        radius = math.hypot(cx, cy)
        base = self._base_angle()
        n = self.axes
        segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
        for k in range(n):
            ang = base + (math.pi * k) / n
            c, s = math.cos(ang), math.sin(ang)
            segs.append(
                (
                    (cx - c * radius, cy - s * radius),
                    (cx + c * radius, cy + s * radius),
                )
            )
        return segs


def _rotate(dx: float, dy: float, ang: float) -> tuple[float, float]:
    c, s = math.cos(ang), math.sin(ang)
    return dx * c - dy * s, dx * s + dy * c


def _reflect_across(dx: float, dy: float, axis_angle: float) -> tuple[float, float]:
    """Reflect (dx, dy) across the line through the origin at *axis_angle*."""
    # Rotate so the axis lies on +X, flip Y, rotate back.
    c, s = math.cos(axis_angle), math.sin(axis_angle)
    x1 = dx * c + dy * s
    y1 = -dx * s + dy * c
    y1 = -y1
    return x1 * c - y1 * s, x1 * s + y1 * c


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
