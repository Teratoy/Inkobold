from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass, field

import numpy as np

from inkobold.core.image_meta import COLOR_DEPTH_RGBA32, storage_dtype, valid_color_depth

# Process-wide monotonic revision source. Every bump() yields a value never
# handed out before, so (layer.id, layer.revision) uniquely identifies a pixel
# state even across undo/redo (which recreates Layer objects with the same id).
# GPU texture and history-snapshot caches rely on this.
_revision_counter = itertools.count(1)


def next_revision() -> int:
    return next(_revision_counter)


@dataclass
class Layer:
    name: str
    width: int
    height: int
    visible: bool = True
    opacity: float = 1.0
    offset_x: int = 0
    offset_y: int = 0
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    revision: int = 0
    color_depth: int = COLOR_DEPTH_RGBA32
    # Straight RGBA — uint8 for 8 bpc modes, uint16 for 16 bpc
    pixels: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.color_depth = valid_color_depth(self.color_depth)
        self.pixels = np.zeros(
            (self.height, self.width, 4),
            dtype=storage_dtype(self.color_depth),
        )
        self.revision = next_revision()

    def clear(self) -> None:
        self.pixels.fill(0)
        self.bump()

    def bump(self) -> None:
        self.revision = next_revision()

    def resize_canvas(self, width: int, height: int) -> None:
        """Top-left crop or pad to a new document size."""
        self.crop(0, 0, width, height)

    def crop(self, x: int, y: int, width: int, height: int) -> None:
        """Keep the rectangle (x, y, width, height); transparent-pad if needed."""
        width = max(1, int(width))
        height = max(1, int(height))
        x, y = int(x), int(y)
        new = np.zeros((height, width, 4), dtype=self.pixels.dtype)
        sx0 = max(0, x)
        sy0 = max(0, y)
        sx1 = min(self.width, x + width)
        sy1 = min(self.height, y + height)
        if sx1 > sx0 and sy1 > sy0:
            dx0 = sx0 - x
            dy0 = sy0 - y
            new[dy0 : dy0 + (sy1 - sy0), dx0 : dx0 + (sx1 - sx0)] = self.pixels[sy0:sy1, sx0:sx1]
        self.pixels = new
        self.width = width
        self.height = height
        self.offset_x = 0
        self.offset_y = 0
        self.bump()

    def apply_offset(self, *, wrap: bool = False) -> bool:
        """Bake offset into pixels and reset offset to (0, 0).

        Keeps the layer buffer document-aligned so paint tools can reach the
        full canvas after a move. Returns True if pixels were rewritten.

        When ``wrap`` is True, content that leaves an edge re-enters on the
        opposite side (seamless tile move); otherwise vacated areas are cleared.
        """
        ox, oy = int(self.offset_x), int(self.offset_y)
        if ox == 0 and oy == 0:
            return False
        src = self.pixels
        if wrap:
            out = src
            if oy:
                out = np.roll(out, oy, axis=0)
            if ox:
                out = np.roll(out, ox, axis=1)
            self.pixels = np.ascontiguousarray(out)
        else:
            dst = np.zeros_like(src)
            h, w = self.height, self.width
            # dst[y, x] comes from src[y - oy, x - ox] when that sample is in-bounds
            dx0 = max(0, ox)
            dy0 = max(0, oy)
            dx1 = min(w, w + ox)
            dy1 = min(h, h + oy)
            if dx1 > dx0 and dy1 > dy0:
                sx0 = dx0 - ox
                sy0 = dy0 - oy
                dst[dy0:dy1, dx0:dx1] = src[sy0 : sy0 + (dy1 - dy0), sx0 : sx0 + (dx1 - dx0)]
            self.pixels = dst
        self.offset_x = 0
        self.offset_y = 0
        self.bump()
        return True

    def dirty_stamp(self) -> int:
        return self.revision
