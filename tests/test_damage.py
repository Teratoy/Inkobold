"""Partial-upload (damage tracking) tests — no GTK / real OpenGL needed.

1. Completeness: for every tool that opts into damage tracking, random strokes
   in every mode must never change a pixel outside the reported rectangle
   (otherwise the screen would show stale pixels).
2. End to end: the renderer, driven through an emulated GL, must keep its
   texture identical to the layer while uploading only rectangles.

Run with ``python -m pytest tests`` or ``python tests/test_damage.py``.
"""

from __future__ import annotations

import copy
import importlib
import random
import sys
import tempfile
import types
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inkobold.core import Document, damage  # noqa: E402
from inkobold.core.effects import LIQUIFY_BRUSH_MODES  # noqa: E402
from inkobold.core.image_meta import COLOR_DEPTH_RGBA16, COLOR_DEPTH_RGBA32, to_display_u8  # noqa: E402
from inkobold.tools import ToolContext, default_tools  # noqa: E402

W, H = 96, 72


def _tip_png() -> Path:
    path = Path(tempfile.mkdtemp(prefix="inkobold-tip-")) / "tip.png"
    rng = np.random.default_rng(3)
    tip = np.zeros((9, 13, 4), np.uint8)
    tip[..., :3] = 255
    tip[..., 3] = rng.integers(0, 255, (9, 13))
    Image.fromarray(tip, "RGBA").save(path)
    return path


def _variants():
    """(label, tool_id, configure(tool)) for every tracked tool / mode."""
    tip = _tip_png()
    out = [("pen", "pen", None), ("smear", "smear", None), ("pen3d", "pen3d", None),
           ("weld", "weld_brush", None), ("eraser", "eraser", None)]
    for mode in ("round", "custom", "bubbles"):
        def cfg(t, mode=mode):
            t.brush_mode = mode
            t.set_brush_path(tip)
        out.append((f"brush-{mode}", "brush", cfg))
    for mode in LIQUIFY_BRUSH_MODES:
        out.append((f"liquify-{mode}", "liquify", lambda t, m=mode: setattr(t, "liquify_mode", m)))
    return out


def _random_doc(depth: int, rng: np.random.Generator) -> Document:
    doc = Document.blank(W, H, color_depth=depth)
    px = doc.active_layer.pixels
    top = 65535 if px.dtype == np.uint16 else 255
    # Blobs of solid colour (so smear / weld / eraser keys have something to act on)
    for _ in range(6):
        x, y = rng.integers(0, W), rng.integers(0, H)
        r = rng.integers(4, 20)
        px[max(0, y - r): y + r, max(0, x - r): x + r] = rng.integers(0, top, 4)
    px[..., 3][px[..., 3] < top // 3] = top
    doc.active_layer.bump()
    return doc


def _point(rng) -> tuple[float, float]:
    # Bias towards edges so clipping / wrap paths get exercised.
    return float(rng.uniform(-12, W + 12)), float(rng.uniform(-12, H + 12))


def test_tracked_tools_report_every_write():
    rng = np.random.default_rng(1234)
    random.seed(1234)
    tools = default_tools()
    checked = partial = 0
    for label, tool_id, cfg in _variants():
        assert tools[tool_id].damage_tracked(), f"{tool_id} should be tracked"
        for depth in (COLOR_DEPTH_RGBA32, COLOR_DEPTH_RGBA16):
            for wrap in (False, True):
                for use_mask in (False, True):
                    for branches in (1, 2):
                        doc = _random_doc(depth, rng)
                        if use_mask:
                            m = np.zeros((H, W), np.uint8)
                            m[10:60, 20:80] = 255
                            doc.selection.mask = m
                        base = copy.deepcopy(tools[tool_id])
                        base.reset()
                        if cfg:
                            cfg(base)
                        clones = [base] + [copy.deepcopy(base) for _ in range(branches - 1)]
                        ctx = ToolContext(
                            document=doc,
                            color=tuple(int(c) for c in rng.integers(0, 256, 4)),
                            brush_size=float(rng.uniform(1.5, 14)),
                            pressure=float(rng.uniform(0.2, 1.0)),
                            threshold=int(rng.choice([255, 60])),
                            intensity=float(rng.uniform(20, 100)),
                            opacity=float(rng.uniform(30, 100)),
                            tile_wrap=wrap,
                        )
                        layer = doc.active_layer
                        events = [("press", [_point(rng) for _ in clones])]
                        events += [("drag", [_point(rng) for _ in clones]) for _ in range(5)]
                        for kind, pts in events:
                            before = layer.pixels.copy()
                            layer.take_damage()  # as if the renderer just uploaded
                            with damage.track(layer, enabled=True):
                                for t, (x, y) in zip(clones, pts):
                                    if kind == "press":
                                        t.on_press(ctx, x, y)
                                    else:
                                        t.on_drag(ctx, x, y)
                            changed = np.any(before != layer.pixels, axis=2)
                            dmg = layer.damage
                            checked += 1
                            if dmg is damage.FULL:
                                continue
                            partial += 1
                            if dmg is None:
                                assert not changed.any(), f"{label}: changed but no damage"
                                continue
                            x0, y0, x1, y1 = dmg
                            changed[y0:y1, x0:x1] = False
                            assert not changed.any(), (
                                f"{label} depth={depth} wrap={wrap} mask={use_mask} "
                                f"branches={branches}: write outside {dmg}"
                            )
                        for t in clones:
                            t.on_release(ctx, 0, 0)
    # The tracking must actually kick in, not just fall back to FULL.
    assert partial > checked * 0.9, (partial, checked)


def test_untracked_tools_fall_back_to_full():
    tools = default_tools()
    for tool_id in ("fill", "gradient", "line", "rectangle", "curve", "replace"):
        assert not tools[tool_id].damage_tracked()
    eraser = tools["eraser"]
    eraser.eraser_mode = "line"
    assert not eraser.damage_tracked()
    doc = Document.blank(32, 32)
    layer = doc.active_layer
    layer.take_damage()
    with damage.track(layer, enabled=False):
        tools["fill"].on_press(ToolContext(document=doc, color=(9, 9, 9, 255)), 5, 5)
    assert layer.damage is damage.FULL


def test_aliasing_write_poisons_scope():
    doc = Document.blank(32, 32)
    layer = doc.active_layer
    layer.take_damage()
    with damage.track(layer):
        view = layer.pixels[4:8, 4:8]
        damage.note(view, 0, 0, 4, 4)  # coordinates relative to a view: unsafe
        damage.note(layer.pixels, 0, 0, 2, 2)
        view[...] = 1
        layer.bump()
    assert layer.damage is damage.FULL


# --- renderer end to end with an emulated GL ---------------------------------


class _FakeGL(types.ModuleType):
    """Just enough of OpenGL.GL to emulate 2D RGBA texture storage."""

    def __init__(self) -> None:
        super().__init__("OpenGL.GL")
        self.tex: dict[int, np.ndarray] = {}
        self.bound = 0
        self.next_id = 1
        self.uploaded_bytes = 0
        self.full_uploads = 0
        self.sub_uploads = 0

    def __getattr__(self, name):
        if name.startswith("GL_"):
            return name
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: None

    def glGenTextures(self, n):
        tid = self.next_id
        self.next_id += 1
        return tid

    def glBindTexture(self, target, tid):
        self.bound = tid

    def glTexImage2D(self, target, level, ifmt, w, h, border, fmt, typ, data):
        self.tex[self.bound] = np.array(data, copy=True).reshape(h, w, 4)
        self.uploaded_bytes += self.tex[self.bound].nbytes
        self.full_uploads += 1

    def glTexSubImage2D(self, target, level, x, y, w, h, fmt, typ, data):
        arr = np.asarray(data).reshape(h, w, 4)
        self.tex[self.bound][y:y + h, x:x + w] = arr
        self.uploaded_bytes += arr.nbytes
        if (w, h) == self.tex[self.bound].shape[1::-1]:
            self.full_uploads += 1
        else:
            self.sub_uploads += 1


def _renderer_with_fake_gl():
    fake = _FakeGL()
    saved = {k: sys.modules.get(k) for k in ("OpenGL", "OpenGL.GL", "inkobold.gpu.renderer")}
    pkg = types.ModuleType("OpenGL")
    pkg.GL = fake
    sys.modules["OpenGL"] = pkg
    sys.modules["OpenGL.GL"] = fake
    sys.modules.pop("inkobold.gpu.renderer", None)
    try:
        mod = importlib.import_module("inkobold.gpu.renderer")
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return mod.GpuRenderer(), fake


def test_renderer_partial_uploads_keep_texture_exact():
    renderer, gl = _renderer_with_fake_gl()
    rng = np.random.default_rng(7)
    for depth in (COLOR_DEPTH_RGBA32, COLOR_DEPTH_RGBA16):
        doc = Document.blank(256, 192, color_depth=depth)
        layer = doc.active_layer
        tools = default_tools()

        def sync():
            renderer.upload_layer(layer.id, layer.pixels, layer.dirty_stamp(), layer)
            tex = gl.tex[renderer.textures[layer.id]]
            assert np.array_equal(tex, to_display_u8(layer.pixels)), "texture out of sync"

        sync()
        bytes_before = gl.uploaded_bytes
        for tool_id in ("pen", "brush", "eraser", "smear", "weld_brush", "liquify"):
            tool = tools[tool_id]
            ctx = ToolContext(document=doc, color=(200, 40, 90, 255), brush_size=6)
            x, y = 128.0, 96.0
            with damage.track(layer, enabled=tool.damage_tracked()):
                tool.on_press(ctx, x, y)
            sync()
            for _ in range(15):
                x += float(rng.uniform(-12, 12))
                y += float(rng.uniform(-12, 12))
                with damage.track(layer, enabled=tool.damage_tracked()):
                    tool.on_drag(ctx, x, y)
                sync()
            tool.on_release(ctx, x, y)
            # Release / whole-layer operations go through bump() -> full upload.
            layer.pixels[0, 0] = 1
            layer.bump()
            sync()
        # Mostly partial uploads; far fewer bytes than whole-layer re-sends.
        events = 6 * 16
        full_cost = events * 256 * 192 * 4
        assert gl.sub_uploads >= events * 0.8, gl.sub_uploads
        assert gl.uploaded_bytes - bytes_before < full_cost * 0.35
        gl.sub_uploads = 0


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
