"""Pixel mode: hard footprints, Bresenham strokes and pixel-perfect corners.

Run with ``python -m pytest tests`` or, without pytest, ``python tests/test_pixel.py``.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inkobold.core import Document  # noqa: E402
from inkobold.tools import ToolContext, default_tools  # noqa: E402
from inkobold.tools import paint  # noqa: E402

RED = (220, 20, 20, 255)


@contextmanager
def _pixel(enabled: bool = True):
    before = paint.pixel_mode()
    paint.set_pixel_mode(enabled)
    try:
        yield
    finally:
        paint.set_pixel_mode(before)


def _blank(w: int = 32, h: int = 32) -> np.ndarray:
    return np.zeros((h, w, 4), dtype=np.uint8)


def _painted(px: np.ndarray) -> set[tuple[int, int]]:
    ys, xs = np.nonzero(px[..., 3])
    return {(int(x), int(y)) for x, y in zip(xs, ys)}


def _drag(tool_id: str, points, size: float = 1.0, doc: Document | None = None) -> Document:
    doc = doc or Document.blank(32, 32)
    doc.active_layer.pixels[...] = 0
    tool = default_tools()[tool_id]
    ctx = ToolContext(document=doc, color=RED, brush_size=size)
    tool.on_press(ctx, *points[0])
    for p in points[1:]:
        tool.on_drag(ctx, *p)
    tool.on_release(ctx, *points[-1])
    return doc


def test_footprints():
    assert paint.pixel_footprint(1).sum() == 1
    assert paint.pixel_footprint(2).sum() == 4
    # 3 px is a plus; bigger tips are round with clipped corners
    assert paint.pixel_footprint(3).sum() == 5
    assert paint.pixel_footprint(4).sum() == 12
    assert paint.pixel_footprint(5)[0, 0] == 0 and paint.pixel_footprint(5)[2, 2] == 1


def test_stamp_is_hard_and_exact():
    px = _blank()
    with _pixel():
        paint.stamp_disk(px, 10.7, 4.2, 1.0, RED)
    assert _painted(px) == {(10, 4)}
    assert tuple(px[4, 10]) == RED
    # Soft mode leaves partial-alpha rim pixels
    soft = _blank()
    paint.stamp_disk(soft, 10.7, 4.2, 3.0, RED)
    assert set(np.unique(soft[..., 3])) - {0, 255}


def test_alpha_is_binary_for_wide_tips():
    px = _blank()
    with _pixel():
        paint.stroke_segment(px, 3, 3, 25, 17, 5.0, RED)
    assert set(np.unique(px[..., 3])) <= {0, 255}


def test_line_is_bresenham():
    px = _blank()
    with _pixel():
        paint.stroke_segment(px, 2.5, 2.5, 12.5, 6.5, 1.0, RED)
    assert _painted(px) == set(paint.bresenham(2, 2, 12, 6))


def test_freehand_pen_drops_l_corners():
    # Staircase drag: each step moves one right then one down → L-corners
    pts = [(2.5, 2.5)]
    for i in range(6):
        pts.append((3.5 + i, 2.5 + i))
        pts.append((3.5 + i, 3.5 + i))
    with _pixel(False):
        soft_pixels = _painted(_drag("pen", pts).active_layer.pixels)
    with _pixel():
        doc = _drag("pen", pts)
    cells = _painted(doc.active_layer.pixels)
    assert cells == {(2 + i, 2 + i) for i in range(7)}
    assert len(soft_pixels) > len(cells)


def test_pixel_perfect_only_at_1px():
    pts = [(2.5, 2.5), (3.5, 2.5), (3.5, 3.5)]
    with _pixel():
        doc = _drag("pen", pts, size=2)
    # 2 px tips keep every stamp
    assert len(_painted(doc.active_layer.pixels)) > 4


def test_elbow_restores_underlying_color():
    doc = Document.blank(16, 16)
    doc.active_layer.pixels[...] = 0
    doc.active_layer.pixels[3, 3] = (0, 0, 255, 255)
    tool = default_tools()["pen"]
    ctx = ToolContext(document=doc, color=RED, brush_size=1)
    with _pixel():
        tool.on_press(ctx, 2.5, 3.5)
        tool.on_drag(ctx, 3.5, 3.5)  # elbow sits on the blue pixel
        tool.on_drag(ctx, 3.5, 4.5)
        tool.on_release(ctx, 3.5, 4.5)
    # Right then down makes (3, 3) the elbow; dropping it shows the blue again
    assert tuple(doc.active_layer.pixels[3, 3]) == (0, 0, 255, 255)
    assert tuple(doc.active_layer.pixels[3, 2]) == RED
    assert tuple(doc.active_layer.pixels[4, 3]) == RED


def test_brush_ignores_soft_rim_and_eraser_is_exact():
    with _pixel():
        doc = _drag("brush", [(4.5, 4.5), (20.5, 4.5)], size=3)
        px = doc.active_layer.pixels
        assert set(np.unique(px[..., 3])) <= {0, 255}
        tool = default_tools()["eraser"]
        ctx = ToolContext(document=doc, color=RED, brush_size=1, threshold=255)
        tool.on_press(ctx, 10.5, 4.5)
        tool.on_release(ctx, 10.5, 4.5)
    assert px[4, 10, 3] == 0
    assert px[4, 9, 3] == 255 and px[4, 11, 3] == 255


def test_rectangle_and_curve_are_hard():
    with _pixel():
        doc = _drag("rectangle", [(3.5, 3.5), (20.5, 15.5)])
        assert set(np.unique(doc.active_layer.pixels[..., 3])) <= {0, 255}
        px = _blank()
        paint.stroke_circle(px, 16, 16, 9, 1.0, RED)
    assert set(np.unique(px[..., 3])) <= {0, 255}
    # A 1 px circle stays one pixel thick: no cell has 3+ painted side neighbours
    cells = _painted(px)
    for x, y in cells:
        side = sum((x + dx, y + dy) in cells for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))
        assert side <= 2


def test_soft_mode_unchanged_by_default():
    assert paint.pixel_mode() is False


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok    {name}")
            except Exception as exc:  # pragma: no cover - manual runner
                failures += 1
                print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failures else 0)
