"""Pixel mode: hard footprints, Bresenham strokes and pixel-perfect corners.

Run with ``python -m pytest tests`` or, without pytest, ``python tests/test_pixel.py``.
"""

from __future__ import annotations

import json
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inkobold.core import Document  # noqa: E402
from inkobold.core.document import upscale_nearest  # noqa: E402
from inkobold.core.grid import (  # noqa: E402
    PIXEL_ZOOM_LEVELS,
    GridOverlay,
    grid_lines,
    pixel_zoom_floor,
    pixel_zoom_step,
)
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



# --- canvas helpers ----------------------------------------------------------


def test_zoom_ladder_steps_and_clamps():
    assert pixel_zoom_step(1.0, True) == 2.0
    assert pixel_zoom_step(2.0, False) == 1.0
    # Off-ladder zooms land on the next level in that direction
    assert pixel_zoom_step(2.4, True) == 3.0
    assert pixel_zoom_step(2.4, False) == 2.0
    assert pixel_zoom_step(64.0, True) == 64.0
    assert pixel_zoom_step(PIXEL_ZOOM_LEVELS[0], False) == PIXEL_ZOOM_LEVELS[0]
    assert all(z == int(z) for z in PIXEL_ZOOM_LEVELS if z >= 1)
    assert pixel_zoom_floor(13.7) == 12.0
    assert pixel_zoom_floor(0.01) == PIXEL_ZOOM_LEVELS[0]


def test_grid_lines_visible_range():
    assert grid_lines(0, 64, 16, 64) == [0, 16, 32, 48, 64]
    assert grid_lines(-10, 20.5, 8, 64) == [0, 8, 16]
    assert grid_lines(17, 31, 16, 64) == []
    assert grid_lines(3.2, 6.9, 1, 64) == [4, 5, 6]


def test_grid_tile_clamps():
    g = GridOverlay(tile=-3)
    g.clamp()
    assert g.tile == 0
    g.tile = 5000
    g.clamp()
    assert g.tile == 1024


def test_tip_outline_encloses_footprint():
    for n in (1, 2, 3, 4, 7):
        edges = paint.pixel_footprint_outline(n)
        # Outline length equals the footprint perimeter in cell edges
        foot = np.pad(paint.pixel_footprint(n) > 0, 1)
        perimeter = int((foot[1:] != foot[:-1]).sum() + (foot[:, 1:] != foot[:, :-1]).sum())
        assert len(edges) == perimeter


def test_hover_outline_matches_painted_cells():
    # The cursor outline sits exactly where a click paints
    for n in (1, 2, 3, 5, 6):
        px = _blank()
        with _pixel():
            paint.stroke_segment(px, 13.3, 9.8, 13.3, 9.8, float(n), RED)
        ox, oy = paint.pixel_tip_origin(13.3, 9.8, n)
        foot = paint.pixel_footprint(n) > 0
        expected = {(ox + int(x), oy + int(y)) for y, x in zip(*np.nonzero(foot))}
        assert _painted(px) == expected, n



# --- export ------------------------------------------------------------------


def _sprite_doc() -> Document:
    """4×3 sprite, three frames each with one coloured pixel at a different x."""
    doc = Document.blank(4, 3)
    doc.active_layer.pixels[...] = 0
    doc.active_layer.pixels[1, 0] = RED
    doc.active_layer.bump()
    for x in (1, 2):
        doc.add_frame(duplicate=False)
        doc.active_layer.pixels[...] = 0
        doc.active_layer.pixels[1, x] = (20, 200, 40, 255)
        doc.active_layer.bump()
    return doc


def test_upscale_nearest_is_exact_blocks():
    src = np.arange(2 * 3 * 4, dtype=np.uint8).reshape(2, 3, 4)
    big = upscale_nearest(src, 3)
    assert big.shape == (6, 9, 4)
    assert (big[3:6, 6:9] == src[1, 2]).all()
    assert upscale_nearest(src, 1) is src


def test_export_image_scale_keeps_hard_pixels():
    from PIL import Image

    doc = Document.blank(4, 3)
    doc.active_layer.pixels[...] = 0
    doc.active_layer.pixels[1, 2] = RED
    doc.active_layer.bump()
    out = Path(tempfile.mkdtemp()) / "sprite.png"
    doc.export_image(out, scale=4)
    arr = np.array(Image.open(out).convert("RGBA"))
    assert arr.shape == (12, 16, 4)
    assert (arr[4:8, 8:12] == RED).all()
    assert set(np.unique(arr[..., 3])) == {0, 255}


def test_sprite_sheet_row_and_json():
    from PIL import Image

    doc = _sprite_doc()
    folder = Path(tempfile.mkdtemp())
    path = doc.export_sprite_sheet(folder / "walk", scale=2)
    assert path.name == "walk.png"
    sheet = np.array(Image.open(path).convert("RGBA"))
    assert sheet.shape == (6, 24, 4)
    meta = json.loads((folder / "walk.json").read_text())
    assert meta["image"] == "walk.png"
    assert (meta["frame_width"], meta["frame_height"], meta["columns"], meta["rows"]) == (8, 6, 3, 1)
    assert [f["x"] for f in meta["frames"]] == [0, 8, 16]
    # Frame i has its pixel at x = i → sheet x = i*8 + i*2 (scaled block)
    for i, f in enumerate(meta["frames"]):
        block = sheet[2:4, f["x"] + i * 2 : f["x"] + i * 2 + 2]
        assert (block[..., 3] == 255).all(), i
    assert int((sheet[..., 3] > 0).sum()) == 3 * 4


def test_animation_exports_scale():
    from PIL import Image

    doc = _sprite_doc()
    folder = Path(tempfile.mkdtemp())
    gif = doc.export_animation_gif(folder / "a.gif", scale=3)
    assert Image.open(gif).size == (12, 9)
    seq = doc.export_animation_png_sequence(folder / "seq", scale=2)
    assert sorted(p.name for p in seq.iterdir()) == ["frame_1.png", "frame_2.png", "frame_3.png"]
    assert Image.open(seq / "frame_2.png").size == (8, 6)
    layers = doc.export_layers_png(folder / "layers", scale=2)
    assert all(Image.open(p).size == (8, 6) for p in layers.iterdir())


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
