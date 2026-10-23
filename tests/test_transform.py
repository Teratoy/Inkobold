"""Transform tool float / import placement tests (no GTK / OpenGL needed).

Run with ``python -m pytest tests`` or, without pytest, ``python tests/test_transform.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inkobold.core import Document  # noqa: E402
from inkobold.tools import ToolContext, default_tools  # noqa: E402


def _image(w: int, h: int) -> np.ndarray:
    """Opaque RGBA gradient so every pixel is distinct-ish."""
    ys, xs = np.mgrid[0:h, 0:w]
    img = np.zeros((h, w, 4), dtype=np.uint8)
    img[..., 0] = (xs * 255 // max(1, w - 1)).astype(np.uint8)
    img[..., 1] = (ys * 255 // max(1, h - 1)).astype(np.uint8)
    img[..., 2] = ((xs + ys) % 256).astype(np.uint8)
    img[..., 3] = 255
    return img


def _placed(w: int = 200, h: int = 100, doc_size: int = 64):
    doc = Document.blank(doc_size, doc_size)
    doc.add_layer(name="import")
    tool = default_tools()["move"]
    ctx = ToolContext(document=doc, color=(0, 0, 0, 255), brush_size=4)
    src = _image(w, h)
    tool.begin_placement(ctx, src)
    return doc, tool, ctx, src


def _drag_corner(tool, ctx, corner: int, to: tuple[float, float], shift: bool = False) -> None:
    cx, cy = tool.guide_corners()[corner]
    tool.on_press(ctx, cx, cy, shift=shift)
    tool.on_drag(ctx, to[0], to[1], shift=shift)
    tool.on_release(ctx, to[0], to[1])


def _drag_move(tool, ctx, dx: float, dy: float) -> None:
    cx, cy, *_ = tool.guide_box()
    tool.on_press(ctx, cx, cy)
    tool.on_drag(ctx, cx + dx, cy + dy)
    tool.on_release(ctx, cx + dx, cy + dy)


def test_placement_is_actual_size_and_centered():
    doc, tool, _ctx, src = _placed(200, 100, 64)
    cx, cy, w, h, angle = tool.guide_box()
    assert (w, h, angle) == (200.0, 100.0, 0.0)
    assert tool.floating
    # Top-left of the image sits at ((64-200)//2, (64-100)//2) = (-68, -18).
    px = doc.active_layer.pixels
    assert np.array_equal(px, src[18 : 18 + 64, 68 : 68 + 64])


def test_scale_down_then_up_restores_original():
    doc, tool, ctx, src = _placed(200, 100, 64)
    before = doc.active_layer.pixels.copy()
    x0, y0 = tool.guide_corners()[0]
    # Shrink from the SE corner toward the NW corner (anchored) ...
    _drag_corner(tool, ctx, 2, (x0 + 50, y0 + 25))
    assert tool.floating
    shrunk = doc.active_layer.pixels.copy()
    assert not np.array_equal(shrunk, before)
    # ... then pull it back out to the exact original size.
    _drag_corner(tool, ctx, 2, (x0 + 200, y0 + 100))
    assert np.array_equal(doc.active_layer.pixels, before)


def test_off_canvas_content_survives_move():
    doc, tool, ctx, _src = _placed(200, 100, 64)
    before = doc.active_layer.pixels.copy()
    _drag_move(tool, ctx, 300, 0)
    assert doc.active_layer.pixels[..., 3].max() == 0
    _drag_move(tool, ctx, -300, 0)
    assert np.array_equal(doc.active_layer.pixels, before)


def test_hq_downscale_averages_detail():
    doc = Document.blank(64, 64)
    doc.add_layer(name="checker")
    tool = default_tools()["move"]
    ctx = ToolContext(document=doc, color=(0, 0, 0, 255), brush_size=4)
    ys, xs = np.mgrid[0:256, 0:256]
    checker = np.zeros((256, 256, 4), dtype=np.uint8)
    checker[..., :3] = (((xs + ys) % 2) * 255)[..., None]
    checker[..., 3] = 255
    tool.begin_placement(ctx, checker)
    x0, y0 = tool.guide_corners()[0]
    # 256 → 37: an uneven ratio, where plain bilinear aliases into stripes.
    _drag_corner(tool, ctx, 2, (x0 + 37, y0 + 37))
    cx, cy, *_ = tool.guide_box()
    _drag_move(tool, ctx, 32 - cx, 32 - cy)
    ly = doc.active_layer.pixels
    inner = ly[22:42, 22:42, 0].astype(int)
    # A downscaled 1px checker should be flat mid-gray, not aliased stripes.
    assert abs(inner.mean() - 127.5) < 6
    assert inner.std() < 6


def test_commit_and_outside_edits_drop_float():
    doc, tool, ctx, _src = _placed(200, 100, 64)
    assert tool.commit()
    assert not tool.floating
    tool.sync_box(ctx)
    # Committed: the box re-fits to the visible (cropped) content.
    assert tool.guide_box()[2:4] == (64.0, 64.0)

    doc, tool, ctx, _src = _placed(200, 100, 64)
    doc.active_layer.pixels[0, 0] = (1, 2, 3, 255)
    doc.mark_dirty()
    tool.sync_box(ctx)
    assert not tool.floating


def test_rotate_round_trip_keeps_float_source():
    doc, tool, ctx, _src = _placed(40, 20, 64)
    before = doc.active_layer.pixels.copy()
    cx, cy, *_ = tool.guide_box()
    nx, ny = tool.guide_corners()[2]
    for target in ((cx - (ny - cy), cy + (nx - cx)), (nx, ny)):
        sx, sy = tool.guide_corners()[2]
        tool.on_press(ctx, sx, sy, alt=True)
        tool.on_drag(ctx, *target)
        tool.on_release(ctx, *target)
    # Back at angle 0 / 1:1, the float resamples exactly from the source again.
    assert abs(tool.guide_box()[4]) < 1e-9
    assert np.array_equal(doc.active_layer.pixels, before)


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
