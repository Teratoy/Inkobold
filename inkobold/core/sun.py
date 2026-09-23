"""Shared directional sun light for 3D-illusion tools and relief effects."""

from __future__ import annotations

import math
from dataclasses import dataclass

# Classic upper-left key light used before the sun control existed.
DEFAULT_LIGHT_DIR: tuple[float, float, float] = (-0.45, -0.55, 0.70)
DEFAULT_MILK_LIGHT_DIR: tuple[float, float, float] = (-0.40, -0.50, 0.76)


def normalize_light(
    lx: float, ly: float, lz: float
) -> tuple[float, float, float]:
    inv = 1.0 / max(1e-8, math.sqrt(lx * lx + ly * ly + lz * lz))
    return lx * inv, ly * inv, lz * inv


def fill_light_from_key(
    lx: float, ly: float, lz: float
) -> tuple[float, float, float]:
    """Cooler fill opposite the key (Addiction / Drift dual-light)."""
    return normalize_light(-lx * 0.55 + 0.05, -ly * 0.25 - 0.05, 0.92)


@dataclass
class SunLight:
    """Document-space sun guide that drives directional shading.

    When *enabled*, a sun glyph is drawn on the canvas and its position
    (relative to the document center) sets the key-light azimuth. Elevation
    controls how frontal vs. raking the light is. When disabled, shaders fall
    back to :data:`DEFAULT_LIGHT_DIR`.
    """

    enabled: bool = False
    # Normalized document position (0–1). Default upper-left ≈ classic key.
    x_norm: float = 0.18
    y_norm: float = 0.18
    # Height above the canvas plane (higher = flatter / more frontal).
    elevation: float = 0.70

    def clamp(self) -> None:
        self.enabled = bool(self.enabled)
        self.x_norm = max(0.0, min(1.0, float(self.x_norm)))
        self.y_norm = max(0.0, min(1.0, float(self.y_norm)))
        self.elevation = max(0.15, min(1.5, float(self.elevation)))

    def doc_xy(self, width: float, height: float) -> tuple[float, float]:
        return float(self.x_norm) * float(width), float(self.y_norm) * float(height)

    def set_doc_xy(self, x: float, y: float, width: float, height: float) -> None:
        w = max(1.0, float(width))
        h = max(1.0, float(height))
        self.x_norm = float(x) / w
        self.y_norm = float(y) / h
        self.clamp()

    def light_dir(
        self,
        width: float = 1.0,
        height: float = 1.0,
        *,
        fallback: tuple[float, float, float] = DEFAULT_LIGHT_DIR,
    ) -> tuple[float, float, float]:
        """Unit vector toward the light in image space (y increases downward)."""
        if not self.enabled:
            return normalize_light(*fallback)
        w = max(1.0, float(width))
        h = max(1.0, float(height))
        sx, sy = self.doc_xy(w, h)
        lx = sx - w * 0.5
        ly = sy - h * 0.5
        span = max(math.hypot(w, h) * 0.5, 1.0)
        lz = float(self.elevation) * span
        if abs(lx) < 1e-6 and abs(ly) < 1e-6:
            return 0.0, 0.0, 1.0
        return normalize_light(lx, ly, lz)

    def milk_light_dir(
        self, width: float = 1.0, height: float = 1.0
    ) -> tuple[float, float, float]:
        return self.light_dir(width, height, fallback=DEFAULT_MILK_LIGHT_DIR)
