"""Dirty-rectangle ("damage") tracking for partial GPU texture uploads.

A paint stroke usually changes a few hundred pixels, but a plain
``Layer.bump()`` makes the renderer re-send the whole layer to the GPU on every
pointer event. This module lets the canvas find out which rectangle a tool
event actually touched, so only that rectangle is uploaded.

How it fits together:

* The canvas wraps a tool event in ``track(layer, enabled=...)``.
* The low-level paint primitives call ``note(pixels, x0, y0, x1, y1)`` right
  where they write into a layer buffer. Outside a tracking scope this is a
  no-op costing one global lookup.
* When the scope ends, if every write went through ``note`` (the tool opted in
  and nothing suspicious happened), the layer's damage is narrowed from
  "everything" to the union of the noted rectangles.

Anything unexpected — an untracked tool, a write through a different view of
the same buffer, a reallocated buffer, an exception — leaves the damage at
"everything", i.e. the old full upload. Being conservative can only cost
speed, never show stale pixels.

Set ``INKOBOLD_VERIFY_DAMAGE=1`` to diff every tracked event against a copy of
the layer and report (and repair) any write that escaped its rectangle. It is
slow; use it while developing tools.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from typing import Iterator, Optional

import numpy as np

# Damage values stored on a Layer:
#   FULL           -> whole layer must be (re)uploaded
#   None           -> nothing changed since the last take
#   (x0, y0, x1, y1) exclusive rectangle in layer pixels
FULL = "full"

Rect = tuple[int, int, int, int]

VERIFY = os.environ.get("INKOBOLD_VERIFY_DAMAGE", "").strip() not in ("", "0")


def union(a, b):
    """Union of two damage values (FULL / None / rect)."""
    if a is FULL or b is FULL:
        return FULL
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


class _Scope:
    __slots__ = ("pixels", "rect", "poisoned")

    def __init__(self, pixels: np.ndarray) -> None:
        self.pixels = pixels
        self.rect: Optional[Rect] = None
        self.poisoned = False


_current: Optional[_Scope] = None


def note(pixels: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> None:
    """Record that ``pixels[y0:y1, x0:x1]`` is about to be (or was) written."""
    s = _current
    if s is None:
        return
    if pixels is not s.pixels:
        # A write into some other buffer is irrelevant — unless it aliases the
        # layer (a view), in which case our coordinates would be wrong.
        if np.may_share_memory(pixels, s.pixels):
            s.poisoned = True
        return
    h, w = pixels.shape[:2]
    x0 = max(0, int(x0))
    y0 = max(0, int(y0))
    x1 = min(w, int(x1))
    y1 = min(h, int(y1))
    if x0 >= x1 or y0 >= y1:
        return
    r = s.rect
    if r is None:
        s.rect = (x0, y0, x1, y1)
    else:
        s.rect = (min(r[0], x0), min(r[1], y0), max(r[2], x1), max(r[3], y1))


def note_full(pixels: np.ndarray) -> None:
    """Record a write that may touch anywhere in ``pixels``."""
    s = _current
    if s is not None and (pixels is s.pixels or np.may_share_memory(pixels, s.pixels)):
        s.poisoned = True


@contextmanager
def track(layer, enabled: bool = True) -> Iterator[None]:
    """Narrow ``layer``'s damage to what noted writes touched during the block.

    ``enabled`` should be True only for tools whose every pixel write goes
    through :func:`note`. Nested scopes are not supported (the inner one wins);
    tool events never nest.
    """
    global _current
    if not enabled or layer is None:
        yield
        return
    pixels = layer.pixels
    pre_damage = layer.damage
    pre_revision = layer.revision
    before = pixels.copy() if VERIFY else None
    scope = _Scope(pixels)
    prev = _current
    _current = scope
    ok = False
    try:
        yield
        ok = True
    finally:
        _current = prev
    if not ok:
        return  # exception: whatever bump() set (FULL) stands
    if (
        scope.poisoned
        or scope.rect is None
        or layer.pixels is not pixels
        or pre_damage is FULL
        or layer.revision == pre_revision
    ):
        return
    rect = scope.rect
    if before is not None:
        rect = _verify(before, pixels, rect)
        if rect is None:
            return
    layer.damage = union(pre_damage, rect)


def _verify(before: np.ndarray, after: np.ndarray, rect: Rect) -> Optional[Rect]:
    changed = np.any(before != after, axis=2)
    x0, y0, x1, y1 = rect
    outside = changed.copy()
    outside[y0:y1, x0:x1] = False
    if outside.any():
        ys, xs = np.nonzero(outside)
        print(
            "inkobold: damage tracking missed writes at "
            f"x={xs.min()}..{xs.max()} y={ys.min()}..{ys.max()} (noted {rect}); "
            "falling back to a full upload",
            file=sys.stderr,
        )
        return None
    return rect
