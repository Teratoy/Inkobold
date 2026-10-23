"""Core regression tests (no GTK / OpenGL needed).

Run with ``python -m pytest tests`` or, without pytest, ``python tests/test_core.py``.
"""

from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inkobold.core import Document, History  # noqa: E402
from inkobold.core import document as document_mod  # noqa: E402
from inkobold.core.image_meta import (  # noqa: E402
    COLOR_DEPTH_GRAY8,
    COLOR_DEPTH_RGB24,
    COLOR_DEPTH_RGBA16,
    COLOR_DEPTH_RGBA32,
    constrain_pixels,
)
from inkobold.tools import ToolContext, default_tools  # noqa: E402


def _tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="inkobold-test-"))


def _stroke(doc: Document, tool_id: str = "pen", color=(200, 30, 30, 255)) -> None:
    tool = default_tools()[tool_id]
    ctx = ToolContext(document=doc, color=color, brush_size=4)
    tool.on_press(ctx, 5, 5)
    tool.on_drag(ctx, 20, 12)
    tool.on_release(ctx, 20, 12)


# --- saving -----------------------------------------------------------------


def test_save_open_roundtrip_multi_frame():
    for depth in (COLOR_DEPTH_RGBA32, COLOR_DEPTH_RGBA16):
        doc = Document.blank(40, 30, color_depth=depth)
        _stroke(doc)
        doc.add_layer()
        doc.add_frame(duplicate=True)
        _stroke(doc, color=(0, 0, 255, 255))
        doc.workspace = {"tool_id": "pen", "view": {"zoom": 2.0}}
        path = doc.save(_tmpdir() / "a.inkobold")
        assert not doc.dirty and doc.path == path
        back = Document.open(path)
        assert back.frame_count == 2 and back.workspace == doc.workspace
        for fa, fb in zip(doc.frames, back.frames):
            for la, lb in zip(fa.layers, fb.layers):
                assert np.array_equal(la.pixels, lb.pixels)


def test_layer_pixels_stored_once():
    doc = Document.blank(16, 16)
    path = doc.save(_tmpdir() / "a.inkobold")
    names = zipfile.ZipFile(path).namelist()
    assert names.count("document.json") == 1
    assert [n for n in names if n.endswith(".png")] == ["frames/000/layers/000.png"]


def test_failed_save_keeps_existing_file():
    tmp = _tmpdir()
    target = tmp / "art.inkobold"
    doc = Document.blank(32, 32)
    doc.add_layer()
    doc.save(target)
    before = target.read_bytes()

    _stroke(doc)
    orig = document_mod._write_layer_pixels
    calls = [0]

    def boom(*a, **k):
        calls[0] += 1
        if calls[0] == 2:
            raise OSError(28, "No space left on device")
        return orig(*a, **k)

    document_mod._write_layer_pixels = boom
    try:
        try:
            doc.save(target)
            raise AssertionError("save should have failed")
        except OSError:
            pass
    finally:
        document_mod._write_layer_pixels = orig

    assert target.read_bytes() == before, "existing file was modified"
    assert Document.open(target).width == 32
    assert doc.dirty, "a failed save must leave the document dirty"
    assert [p.name for p in tmp.iterdir()] == ["art.inkobold"], "temp file left behind"


def test_prepare_save_snapshots_pixels():
    doc = Document.blank(8, 8)
    job = doc.prepare_save(_tmpdir() / "a.inkobold")
    doc.active_layer.pixels[:] = 255  # user keeps drawing while the job writes
    doc.mark_dirty()
    job.write()
    back = Document.open(job.target)
    assert back.active_layer.pixels.max() == 0
    assert doc.dirty


# --- export -----------------------------------------------------------------


def test_transparent_export_keeps_straight_color():
    doc = Document.blank(4, 4)
    doc.layers[0].pixels[:] = (255, 0, 0, 128)
    assert tuple(doc.flat_rgba()[0, 0]) == (255, 0, 0, 128)
    out = doc.export_image(_tmpdir() / "x.png")
    assert tuple(np.array(Image.open(out))[0, 0]) == (255, 0, 0, 128)


def test_export_two_semi_transparent_layers():
    doc = Document.blank(2, 2)
    doc.layers[0].pixels[:] = (0, 0, 255, 128)
    doc.add_layer()
    doc.layers[1].pixels[:] = (255, 0, 0, 128)
    r, g, b, a = (int(v) for v in doc.flat_rgba()[0, 0])
    # Straight-alpha "over": a = .5 + .5*.5 = .75; rgb = (red*.5 + blue*.25) / .75
    assert abs(a - 191) <= 1 and abs(r - 170) <= 1 and g == 0 and abs(b - 85) <= 1


def test_export_layers_file_names():
    doc = Document.blank(2, 2)
    doc.add_layer()
    doc.add_layer()
    doc.layers[0].name, doc.layers[1].name, doc.layers[2].name = "head", "arm", "arm"
    dup = f"arm_{doc.layers[2].id}.png"
    numbered = {p.name for p in doc.export_layers_png(_tmpdir()).iterdir()}
    assert numbered == {"0_head.png", "1_arm.png", "2_arm.png"}
    plain = {p.name for p in doc.export_layers_png(_tmpdir(), index_prefix=False).iterdir()}
    assert plain == {"head.png", "arm.png", dup}


def test_merge_down_matches_flat():
    doc = Document.blank(3, 3)
    doc.layers[0].pixels[:] = (0, 0, 255, 128)
    doc.add_layer()
    doc.layers[1].pixels[:] = (255, 0, 0, 128)
    flat = doc.flat_rgba()
    assert doc.merge_down(1)
    assert np.abs(doc.flat_rgba().astype(int) - flat.astype(int)).max() <= 1


# --- merges -----------------------------------------------------------------


def test_merge_down_refuses_hidden_layers():
    for hidden in (0, 1):
        doc = Document.blank(4, 4)
        doc.layers[0].pixels[:] = (0, 0, 255, 255)
        doc.add_layer()
        doc.layers[1].pixels[:2] = (255, 0, 0, 255)
        doc.layers[hidden].visible = False
        snap = [ly.pixels.copy() for ly in doc.layers]
        assert not doc.merge_down(1)
        assert len(doc.layers) == 2
        assert all(np.array_equal(a, ly.pixels) for a, ly in zip(snap, doc.layers))


def test_merge_visible_keeps_hidden():
    doc = Document.blank(4, 4)
    doc.add_layer()
    doc.add_layer()
    doc.layers[1].visible = False
    assert doc.hidden_layer_count() == 1
    assert doc.merge_visible()
    assert len(doc.layers) == 2 and not doc.layers[1].visible


# --- RGB / grayscale documents ------------------------------------------------


def test_rgb_and_gray_documents_start_white_and_stay_layered():
    for depth in (COLOR_DEPTH_RGB24, COLOR_DEPTH_GRAY8):
        doc = Document.blank(32, 32, color_depth=depth)
        assert (doc.layers[0].pixels == 255).all(), "canvas should start white"
        _stroke(doc, color=(255, 0, 0, 255))
        constrain_pixels(doc.active_layer.pixels, depth)  # what the canvas does per event
        assert tuple(doc.active_layer.pixels[30, 30]) == (255, 255, 255, 255)

        doc.add_layer()
        _stroke(doc, color=(0, 0, 0, 255))
        constrain_pixels(doc.active_layer.pixels, depth)
        top = doc.active_layer.pixels
        assert top[30, 30, 3] == 0, "untouched pixels of an upper layer stay transparent"
        assert top[5, 5, 3] == 255
        if depth == COLOR_DEPTH_GRAY8:
            assert (top[..., 0] == top[..., 1]).all() and (top[..., 1] == top[..., 2]).all()


def test_convert_to_rgb_flattens_only_bottom_layer():
    doc = Document.blank(4, 4)  # transparent RGBA
    doc.add_layer()
    doc.layers[1].pixels[0, 0] = (255, 0, 0, 255)
    doc.set_color_depth(COLOR_DEPTH_RGB24)
    assert (doc.layers[0].pixels == 255).all(), "bottom layer composited on white"
    assert doc.layers[1].pixels[3, 3, 3] == 0, "upper layer keeps transparency"
    assert tuple(doc.flat_rgba()[3, 3]) == (255, 255, 255, 255)
    assert tuple(doc.flat_rgba()[0, 0]) == (255, 0, 0, 255)


# --- history ------------------------------------------------------------------


def test_discarded_push_restores_redo():
    doc = Document.blank(8, 8)
    hist = History()
    hist.push(doc)
    _stroke(doc)
    assert hist.undo(doc) and hist.can_redo
    hist.push(doc)  # e.g. effect dialog opened
    assert not hist.can_redo
    hist.discard_last_push()  # dialog cancelled
    assert hist.can_redo, "cancelling must not wipe redo"
    assert hist.redo(doc)


def test_all_tools_run():
    doc = Document.blank(64, 64)
    for tool_id in default_tools():
        _stroke(doc, tool_id)


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
