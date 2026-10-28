"""Palette file tests (no GTK needed).

Run with ``python -m pytest tests`` or, without pytest, ``python tests/test_palette.py``.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inkobold.core import palette as pal  # noqa: E402


def _tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="inkobold-palette-"))


def test_gpl_round_trip_keeps_names_order_and_columns():
    p = pal.Palette("Swamp", columns=4)
    p.add((10, 20, 30), "Mud")
    p.add((200, 210, 220))
    p.add((10, 20, 30), "Duplicate")  # already present: ignored
    folder = _tmpdir()
    path = folder / "Swamp.gpl"
    pal.save_palette(p, path)
    back = pal.load_palette(path)
    assert back.name == "Swamp" and back.columns == 4
    assert [(s.rgb, s.name) for s in back.swatches] == [((10, 20, 30), "Mud"), ((200, 210, 220), "")]
    assert not (folder / "Swamp.gpl.tmp").exists()


def test_gpl_parser_skips_comments_and_broken_lines():
    text = (
        "﻿GIMP Palette\n"
        "Name: Odd\n"
        "Columns: nope\n"
        "# comment\n"
        "\n"
        "255 0 0\tRed with\ttabs\n"
        "  1   2\n"
        "x y z\n"
        "300 -5 7 Clamped\n"
    )
    p = pal.parse_gpl(text)
    assert p.name == "Odd" and p.columns == 0
    assert [(s.rgb, s.name) for s in p.swatches] == [((255, 0, 0), "Red with\ttabs"), ((255, 0, 7), "Clamped")]


def test_gpl_without_header_is_rejected():
    try:
        pal.parse_gpl("0 0 0 Black\n")
    except pal.PaletteError:
        return
    raise AssertionError("expected PaletteError")


def test_hex_import_accepts_hash_and_alpha_suffix():
    p = pal.parse_hex("#ff0000\n00ff00cc\nnot a color\n0000FF\n", name="Hex")
    assert [s.rgb for s in p.swatches] == [(255, 0, 0), (0, 255, 0), (0, 0, 255)]


def test_image_import_reading_order_skips_transparent():
    px = np.zeros((2, 3, 4), dtype=np.uint8)
    px[0, 0] = (9, 9, 9, 255)
    px[0, 1] = (1, 2, 3, 0)  # transparent: skipped
    px[0, 2] = (50, 60, 70, 255)
    px[1, :] = (9, 9, 9, 255)
    path = _tmpdir() / "strip.png"
    Image.fromarray(px, "RGBA").save(path)
    p = pal.load_palette(path)
    assert p.name == "strip"
    assert [s.rgb for s in p.swatches] == [(9, 9, 9), (50, 60, 70)]


def test_image_import_refuses_photos():
    rng = np.random.default_rng(1)
    px = rng.integers(0, 256, (32, 32, 3), dtype=np.uint8)
    path = _tmpdir() / "noise.png"
    Image.fromarray(px, "RGB").save(path)
    try:
        pal.load_palette(path)
    except pal.PaletteError as exc:
        assert "at most" in str(exc)
        return
    raise AssertionError("expected PaletteError")


def test_import_palette_writes_unique_gpl():
    folder = _tmpdir()
    src = folder / "src" / "Neon.hex"
    src.parent.mkdir()
    src.write_text("ff00ff\n", encoding="utf-8")
    first = pal.import_palette(src, folder)
    second = pal.import_palette(src, folder)
    assert first.name == "Neon.gpl" and second.name == "Neon 2.gpl"
    assert pal.load_palette(second).swatches[0].rgb == (255, 0, 255)
    assert pal.unique_palette_path(folder, 'a/b:c').name == "a_b_c.gpl"


def test_move_reorders_swatches():
    p = pal.Palette("M")
    for v in range(4):
        p.add((v, v, v))
    p.move(0, 2)
    assert [s.rgb[0] for s in p.swatches] == [1, 2, 0, 3]
    p.move(3, 99)
    assert [s.rgb[0] for s in p.swatches] == [1, 2, 0, 3]


def test_new_palettes_library_is_seeded_once():
    from inkobold.core import libraries

    old = os.environ.get("INKOBOLD_DATA_HOME")
    os.environ["INKOBOLD_DATA_HOME"] = str(_tmpdir())
    try:
        libraries.ensure_libraries()
        files = libraries.list_palettes()
        assert [f.name for f in files] == ["Default.gpl"]
        assert len(pal.load_palette(files[0]).swatches) == 36
        files[0].unlink()
        libraries.ensure_libraries()  # folder exists: not re-seeded
        assert libraries.list_palettes() == []
    finally:
        if old is None:
            os.environ.pop("INKOBOLD_DATA_HOME", None)
        else:
            os.environ["INKOBOLD_DATA_HOME"] = old


def test_palette_from_pixels_keeps_few_colors_in_reading_order():
    rgba = np.zeros((2, 3, 4), dtype=np.uint8)
    rgba[0, 0] = (0, 0, 255, 255)
    rgba[0, 2] = (255, 0, 0, 128)
    rgba[1, 1] = (0, 0, 255, 255)
    rgba[1, 2] = (9, 9, 9, 0)  # transparent: ignored
    got = pal.palette_from_pixels(rgba, name="Layer 1")
    assert got.name == "Layer 1"
    assert [s.rgb for s in got.swatches] == [(0, 0, 255), (255, 0, 0)]


def test_palette_from_pixels_reduces_busy_layers():
    ramp = np.zeros((1, 1000, 4), dtype=np.uint8)
    ramp[0, :, 0] = np.arange(1000) % 256
    ramp[0, :, 1] = np.arange(1000) // 4
    ramp[..., 3] = 255
    got = pal.palette_from_pixels(ramp, name="Busy", max_colors=16)
    assert 0 < len(got.swatches) <= 16
    assert len({s.rgb for s in got.swatches}) == len(got.swatches)


def test_palette_from_pixels_empty_layer_raises():
    try:
        pal.palette_from_pixels(np.zeros((4, 4, 4), dtype=np.uint8), name="Empty")
    except pal.PaletteError:
        return
    raise AssertionError("expected PaletteError")


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

