"""Live drawing mirror / radial / linear-array symmetry modifier."""

from __future__ import annotations

import math
from dataclasses import dataclass


# First-axis / guide phase presets. Horizontal = left↔right mirror (vertical line),
# Vertical = top↔bottom (horizontal line), Diagonal = 45°.
ORIENTATIONS = (
    ("horizontal", "Horizontal"),
    ("vertical", "Vertical"),
    ("diagonal", "Diagonal"),
)

MODES = (
    ("mirror", "Mirror"),
    ("radial", "Radial"),
    ("linear", "Linear Array"),
)

ARRAY_COUNT_MAX = 64
ARRAY_SPACING_MAX = 8192.0

_BASE_ANGLE = {
    "horizontal": math.pi * 0.5,  # vertical axis line
    "vertical": 0.0,              # horizontal axis line
    "diagonal": math.pi * 0.25,
}


@dataclass
class MirrorModifier:
    """Symmetry applied to paint strokes around a movable center.

    *mode* ``mirror`` (dihedral): *axes* equally spaced reflection axes
    through the center (neighbors 180°/axes apart). Odd reflection branches
    reverse orientation.

    *mode* ``radial`` (cyclic): *axes* same-facing rotational copies at
    360°/axes. No reflections.

    *mode* ``linear`` (translation): *array_count* same-facing copies of the
    stroke, each offset *array_spacing* document px further along
    *array_direction* (radians, screen space: 0 = right, π/2 = down). The hub
    only anchors the guide; copies follow the pointer, not the hub.

    *angle* is the continuous first-axis / first-ray phase (radians). The
    Orientation dropdown snaps it to presets. *cx_norm* / *cy_norm* place the
    hub (default canvas center).
    """

    enabled: bool = False
    mode: str = "mirror"  # mirror | radial | linear
    orientation: str = "horizontal"  # horizontal | vertical | diagonal
    axes: int = 1
    # Continuous guide phase (radians). Defaults match Horizontal.
    angle: float = math.pi * 0.5
    # Normalized hub position (0–1). Default = document center.
    cx_norm: float = 0.5
    cy_norm: float = 0.5
    # Linear Array: total instances (original included), step length, heading.
    array_count: int = 4
    array_spacing: float = 64.0
    array_direction: float = 0.0

    def clamp(self) -> None:
        if self.mode not in {k for k, _ in MODES}:
            self.mode = "mirror"
        if self.orientation not in _BASE_ANGLE:
            self.orientation = "horizontal"
        self.axes = max(1, min(16, int(self.axes)))
        self.angle = float(self.angle) % (2.0 * math.pi)
        self.cx_norm = max(0.0, min(1.0, float(self.cx_norm)))
        self.cy_norm = max(0.0, min(1.0, float(self.cy_norm)))
        self.array_count = max(1, min(ARRAY_COUNT_MAX, int(self.array_count)))
        self.array_spacing = max(0.0, min(ARRAY_SPACING_MAX, float(self.array_spacing)))
        self.array_direction = float(self.array_direction) % (2.0 * math.pi)

    @property
    def guide_angle(self) -> float:
        """Angle the rotate-guide drag edits: array heading in Linear, else axis phase."""
        return float(self.array_direction if self.mode == "linear" else self.angle)

    @guide_angle.setter
    def guide_angle(self, value: float) -> None:
        if self.mode == "linear":
            self.array_direction = float(value)
        else:
            self.angle = float(value)
        self.clamp()

    def array_step(self) -> tuple[float, float]:
        """Document-space offset between neighboring Linear Array copies."""
        s = float(self.array_spacing)
        return math.cos(self.array_direction) * s, math.sin(self.array_direction) * s

    def array_handle(self, width: float, height: float) -> tuple[float, float]:
        """Document position of the step handle (hub + one interval)."""
        cx, cy = self.center(width, height)
        sx, sy = self.array_step()
        return cx + sx, cy + sy

    def set_array_step_to(self, x: float, y: float, width: float, height: float) -> None:
        """Set spacing and direction so one interval ends at document (x, y)."""
        cx, cy = self.center(width, height)
        dx = float(x) - cx
        dy = float(y) - cy
        self.array_spacing = math.hypot(dx, dy)
        if self.array_spacing > 1e-9:
            self.array_direction = math.atan2(dy, dx)
        self.clamp()

    # Back-compat alias used by older call sites / UI wiring during rename.
    @property
    def radials(self) -> int:
        return self.axes

    @radials.setter
    def radials(self, value: int) -> None:
        self.axes = int(value)

    def _base_angle(self) -> float:
        return float(self.angle)

    def center(self, width: float, height: float) -> tuple[float, float]:
        return float(self.cx_norm) * float(width), float(self.cy_norm) * float(height)

    def set_center(self, x: float, y: float, width: float, height: float) -> None:
        w = max(1.0, float(width))
        h = max(1.0, float(height))
        self.cx_norm = float(x) / w
        self.cy_norm = float(y) / h
        self.clamp()

    def apply_orientation_preset(self, key: str) -> None:
        """Snap *angle* to a named Orientation preset."""
        if key not in _BASE_ANGLE:
            return
        self.orientation = key
        self.angle = _BASE_ANGLE[key]
        self.clamp()

    def reset_guides(self) -> None:
        """Restore hub to canvas center, angle to Horizontal, array heading to +X."""
        self.cx_norm = 0.5
        self.cy_norm = 0.5
        self.array_direction = 0.0
        self.apply_orientation_preset("horizontal")

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
        Rotations preserve orientation. Radial and Linear modes never reverse.
        """
        if not self.enabled:
            return [(float(x), float(y), False)]
        self.clamp()
        if self.mode == "linear":
            sx, sy = self.array_step()
            return _unique_branches(
                [(float(x) + k * sx, float(y) + k * sy, False) for k in range(self.array_count)]
            )
        cx, cy = self.center(width, height)
        dx = float(x) - cx
        dy = float(y) - cy
        n = self.axes

        out: list[tuple[float, float, bool]] = []
        if self.mode == "radial":
            for k in range(n):
                ang = (2.0 * math.pi * k) / n
                rx, ry = _rotate(dx, dy, ang)
                out.append((cx + rx, cy + ry, False))
            return _unique_branches(out)

        base = self._base_angle()
        fx, fy = _reflect_across(dx, dy, base)
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
        """Document-space guide lines around the hub."""
        if not self.enabled:
            return []
        self.clamp()
        w = float(width)
        h = float(height)
        cx, cy = self.center(w, h)
        # Reach past every canvas corner from the (possibly off-center) hub.
        radius = math.hypot(max(cx, w - cx), max(cy, h - cy))
        if self.mode == "linear":
            # Array span from the hub; at least one step so it stays grabbable.
            sx, sy = self.array_step()
            k = max(1, self.array_count - 1)
            return [((cx, cy), (cx + sx * k, cy + sy * k))]
        base = self._base_angle()
        n = self.axes
        segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
        if self.mode == "radial":
            # N rays from the hub (not diameters — those look like 2n spokes).
            for k in range(n):
                ang = base + (2.0 * math.pi * k) / n
                c, s = math.cos(ang), math.sin(ang)
                segs.append(
                    (
                        (cx, cy),
                        (cx + c * radius, cy + s * radius),
                    )
                )
            return segs

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


def segment_distance_sq(
    px: float,
    py: float,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
) -> float:
    """Squared distance from point to a finite segment."""
    dx = x1 - x0
    dy = y1 - y0
    len_sq = dx * dx + dy * dy
    if len_sq < 1e-12:
        ex, ey = px - x0, py - y0
        return ex * ex + ey * ey
    t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / len_sq))
    ex, ey = px - (x0 + t * dx), py - (y0 + t * dy)
    return ex * ex + ey * ey
