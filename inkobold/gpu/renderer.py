"""OpenGL / OpenGL ES compositor for layered RGBA documents."""

from __future__ import annotations

from typing import Optional

import numpy as np
from OpenGL import GL

from inkobold.core.document import Document
from inkobold.core.image_meta import to_display_u8

VERT_BODY = """
layout(location = 0) in vec2 a_pos;
layout(location = 1) in vec2 a_uv;
uniform vec2 u_view_size;
uniform vec2 u_doc_size;
uniform vec2 u_pan;
uniform float u_zoom;
uniform vec2 u_layer_offset;
out vec2 v_uv;
void main() {
    vec2 pixel = a_pos * u_doc_size + u_layer_offset;
    vec2 screen = (pixel * u_zoom) + u_pan;
    vec2 ndc = vec2(
        (screen.x / u_view_size.x) * 2.0 - 1.0,
        1.0 - (screen.y / u_view_size.y) * 2.0
    );
    gl_Position = vec4(ndc, 0.0, 1.0);
    v_uv = a_uv;
}
"""

FRAG_LAYER_BODY = """
in vec2 v_uv;
uniform sampler2D u_tex;
uniform float u_opacity;
out vec4 frag;
void main() {
    vec4 c = texture(u_tex, v_uv);
    frag = vec4(c.rgb, c.a * u_opacity);
}
"""

FRAG_CHECKER_BODY = """
in vec2 v_uv;
uniform vec2 u_doc_size;
uniform float u_zoom;
uniform vec3 u_checker_a;
uniform vec3 u_checker_b;
out vec4 frag;
void main() {
    vec2 p = v_uv * u_doc_size;
    float cell = 16.0;
    vec2 g = floor(p / cell);
    float checker = mod(g.x + g.y, 2.0);
    frag = vec4(mix(u_checker_a, u_checker_b, checker), 1.0);
}
"""

FRAG_SEL_BODY = """
in vec2 v_uv;
uniform sampler2D u_tex;
out vec4 frag;
void main() {
    float m = texture(u_tex, v_uv).r;
    if (m < 0.5) discard;
    float march = step(0.5, fract((gl_FragCoord.x + gl_FragCoord.y) * 0.08));
    frag = vec4(0.9, 0.9, 0.95, 0.35 * march + 0.15);
}
"""


def _prelude(es: bool) -> str:
    if es:
        return "#version 300 es\nprecision highp float;\nprecision highp int;\nprecision highp sampler2D;\n"
    return "#version 330 core\n"


def _compile(src: str, kind: int) -> int:
    sid = GL.glCreateShader(kind)
    GL.glShaderSource(sid, src)
    GL.glCompileShader(sid)
    if not GL.glGetShaderiv(sid, GL.GL_COMPILE_STATUS):
        log = GL.glGetShaderInfoLog(sid)
        if isinstance(log, bytes):
            log = log.decode(errors="replace")
        raise RuntimeError(log)
    return sid


def _program(vert: str, frag: str) -> int:
    vs = _compile(vert, GL.GL_VERTEX_SHADER)
    fs = _compile(frag, GL.GL_FRAGMENT_SHADER)
    prog = GL.glCreateProgram()
    GL.glAttachShader(prog, vs)
    GL.glAttachShader(prog, fs)
    GL.glLinkProgram(prog)
    GL.glDeleteShader(vs)
    GL.glDeleteShader(fs)
    if not GL.glGetProgramiv(prog, GL.GL_LINK_STATUS):
        log = GL.glGetProgramInfoLog(prog)
        if isinstance(log, bytes):
            log = log.decode(errors="replace")
        raise RuntimeError(log)
    return prog


def _gl_string(name: int) -> str:
    raw = GL.glGetString(name)
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode(errors="replace")
    return str(raw)


_COMMON_UNIFORMS = ("u_view_size", "u_doc_size", "u_pan", "u_zoom", "u_layer_offset")


class GpuRenderer:
    def __init__(self) -> None:
        self.ready = False
        self.es = False
        self.prog_layer = 0
        self.prog_checker = 0
        self.prog_sel = 0
        self.vao = 0
        self.vbo = 0
        self.textures: dict[str, int] = {}
        self.tex_stamps: dict[str, int] = {}
        # Allocated (w, h) per texture so re-uploads can use glTexSubImage2D
        # (no driver-side reallocation) when the layer size is unchanged.
        self.tex_sizes: dict[str, tuple[int, int]] = {}
        self.sel_tex = 0
        self._sel_size: tuple[int, int] | None = None
        self._sel_stamp: int | None = None
        self._sel_use_rgba = False
        # program id -> {uniform name -> location}
        self._uniforms: dict[int, dict[str, int]] = {}
        self.view_w = 1
        self.view_h = 1
        self.pan_x = 40.0
        self.pan_y = 40.0
        self.zoom = 1.0
        self.checker_light = False
        # When True, composite a 5×5 repeat so seamless tiles can be judged live.
        self.tile_preview = False
        self.init_error: str | None = None
        self.flora = None  # lazy FloraGpu

    def _loc(self, prog: int, name: str) -> int:
        table = self._uniforms.get(prog)
        if table is None:
            table = self._uniforms[prog] = {}
        loc = table.get(name)
        if loc is None:
            loc = table[name] = int(GL.glGetUniformLocation(prog, name))
        return loc

    def init_gl(self) -> None:
        version = _gl_string(GL.GL_VERSION)
        self.es = "ES" in version.upper()
        pre = _prelude(self.es)
        try:
            self.prog_layer = _program(pre + VERT_BODY, pre + FRAG_LAYER_BODY)
            self.prog_checker = _program(pre + VERT_BODY, pre + FRAG_CHECKER_BODY)
            self.prog_sel = _program(pre + VERT_BODY, pre + FRAG_SEL_BODY)
        except RuntimeError as exc:
            # Retry the other dialect if the first guess was wrong
            self.es = not self.es
            pre = _prelude(self.es)
            try:
                self.prog_layer = _program(pre + VERT_BODY, pre + FRAG_LAYER_BODY)
                self.prog_checker = _program(pre + VERT_BODY, pre + FRAG_CHECKER_BODY)
                self.prog_sel = _program(pre + VERT_BODY, pre + FRAG_SEL_BODY)
            except RuntimeError as exc2:
                self.init_error = f"{exc} | retry: {exc2}"
                raise RuntimeError(self.init_error) from exc2

        verts = np.array(
            [
                0, 0, 0, 0,
                1, 0, 1, 0,
                0, 1, 0, 1,
                1, 1, 1, 1,
            ],
            dtype=np.float32,
        )
        self.vao = int(GL.glGenVertexArrays(1))
        self.vbo = int(GL.glGenBuffers(1))
        GL.glBindVertexArray(self.vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, verts.nbytes, verts, GL.GL_STATIC_DRAW)
        GL.glEnableVertexAttribArray(0)
        GL.glVertexAttribPointer(0, 2, GL.GL_FLOAT, GL.GL_FALSE, 16, GL.ctypes.c_void_p(0))
        GL.glEnableVertexAttribArray(1)
        GL.glVertexAttribPointer(1, 2, GL.GL_FLOAT, GL.GL_FALSE, 16, GL.ctypes.c_void_p(8))
        GL.glBindVertexArray(0)

        self.sel_tex = int(GL.glGenTextures(1))
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.sel_tex)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)

        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        self._uniforms.clear()
        if self.flora is None:
            from inkobold.gpu.flora import FloraGpu

            self.flora = FloraGpu()
        try:
            self.flora.ensure(self.es)
        except Exception:
            self.flora = None
        self.ready = True
        self.init_error = None

    def apply_flora(self, pixels: np.ndarray, **params) -> Optional[np.ndarray]:
        """Run Flora on the current GL context; ``None`` if not ready / failed."""
        if not self.ready:
            return None
        if self.flora is None:
            from inkobold.gpu.flora import FloraGpu

            self.flora = FloraGpu()
        return self.flora.apply(pixels, es=self.es, **params)

    def _tex_for(self, key: str) -> int:
        tid = self.textures.get(key)
        if tid is None:
            tid = int(GL.glGenTextures(1))
            GL.glBindTexture(GL.GL_TEXTURE_2D, tid)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
            self.textures[key] = tid
        return tid

    def upload_layer(self, layer_id: str, pixels: np.ndarray, stamp: int) -> None:
        if self.tex_stamps.get(layer_id) == stamp:
            return
        tid = self._tex_for(layer_id)
        h, w = pixels.shape[:2]
        contiguous = np.ascontiguousarray(to_display_u8(pixels))
        GL.glBindTexture(GL.GL_TEXTURE_2D, tid)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        if self.tex_sizes.get(layer_id) == (w, h):
            # Same storage: update in place instead of reallocating the texture.
            GL.glTexSubImage2D(
                GL.GL_TEXTURE_2D, 0, 0, 0, w, h, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, contiguous
            )
        else:
            GL.glTexImage2D(
                GL.GL_TEXTURE_2D,
                0,
                GL.GL_RGBA,
                w,
                h,
                0,
                GL.GL_RGBA,
                GL.GL_UNSIGNED_BYTE,
                contiguous,
            )
            self.tex_sizes[layer_id] = (w, h)
        self.tex_stamps[layer_id] = stamp

    def upload_selection(self, mask: Optional[np.ndarray], stamp: int | None = None) -> None:
        """Upload the selection mask; skipped when ``stamp`` matches the last upload."""
        if mask is None:
            return
        if stamp is not None and stamp == self._sel_stamp:
            return
        h, w = mask.shape
        contiguous = np.ascontiguousarray(mask)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.sel_tex)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        same_size = self._sel_size == (w, h)
        if not self._sel_use_rgba:
            # GL_R8 / GL_RED can be flaky on some GLES; fall back to an RGBA
            # upload of the expanded mask (and remember that for next time).
            try:
                if same_size:
                    GL.glTexSubImage2D(
                        GL.GL_TEXTURE_2D, 0, 0, 0, w, h, GL.GL_RED, GL.GL_UNSIGNED_BYTE, contiguous
                    )
                else:
                    GL.glTexImage2D(
                        GL.GL_TEXTURE_2D, 0, GL.GL_R8, w, h, 0, GL.GL_RED, GL.GL_UNSIGNED_BYTE, contiguous
                    )
                self._sel_size = (w, h)
                self._sel_stamp = stamp
                return
            except Exception:
                self._sel_use_rgba = True
                same_size = False
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        rgba[..., 0] = contiguous
        rgba[..., 3] = contiguous
        if same_size:
            GL.glTexSubImage2D(GL.GL_TEXTURE_2D, 0, 0, 0, w, h, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, rgba)
        else:
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA, w, h, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, rgba)
        self._sel_size = (w, h)
        self._sel_stamp = stamp

    def invalidate(self, layer_id: str | None = None) -> None:
        if layer_id is None:
            self.tex_stamps.clear()
            self._sel_stamp = None
        else:
            self.tex_stamps.pop(layer_id, None)

    def prune_textures(self, keep: set[str]) -> None:
        """Free GPU textures for layers that no longer exist in the document."""
        dead = [key for key in self.textures if key not in keep]
        if not dead:
            return
        ids = np.array([self.textures[k] for k in dead], dtype=np.uint32)
        GL.glDeleteTextures(len(dead), ids)
        for k in dead:
            del self.textures[k]
            self.tex_stamps.pop(k, None)
            self.tex_sizes.pop(k, None)

    def set_view(self, w: int, h: int) -> None:
        self.view_w = max(1, w)
        self.view_h = max(1, h)

    def screen_to_doc(self, sx: float, sy: float) -> tuple[float, float]:
        z = self.zoom if self.zoom else 1.0
        return ((sx - self.pan_x) / z, (sy - self.pan_y) / z)

    def doc_to_screen(self, dx: float, dy: float) -> tuple[float, float]:
        z = self.zoom if self.zoom else 1.0
        return (dx * z + self.pan_x, dy * z + self.pan_y)

    def fit_canvas(self, doc: Document) -> None:
        margin = 48
        vw = max(1, self.view_w)
        vh = max(1, self.view_h)
        if vw <= 32 or vh <= 32:
            self.zoom = 1.0
            self.pan_x = 0.0
            self.pan_y = 0.0
            return
        # Tile preview draws a 5×5 repeat; fit ~3 tiles so the center stays
        # large while neighbors (and seams) remain visible at the edges.
        span = 3 if self.tile_preview else 1
        zx = (vw - margin * 2) / max(1, doc.width * span)
        zy = (vh - margin * 2) / max(1, doc.height * span)
        self.zoom = max(0.05, min(zx, zy, 8.0))
        self.pan_x = (vw - doc.width * self.zoom) * 0.5
        self.pan_y = (vh - doc.height * self.zoom) * 0.5

    def draw(self, doc: Document, *, playing: bool = False) -> None:
        if not self.ready:
            return
        GL.glViewport(0, 0, int(self.view_w), int(self.view_h))
        if self.checker_light:
            GL.glClearColor(0.72, 0.72, 0.72, 1.0)
            checker_a = (0.85, 0.85, 0.85)
            checker_b = (0.55, 0.55, 0.55)
        else:
            GL.glClearColor(0.0, 0.0, 0.0, 1.0)
            checker_a = (0.16, 0.16, 0.16)
            checker_b = (0.10, 0.10, 0.10)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        GL.glBindVertexArray(self.vao)
        GL.glActiveTexture(GL.GL_TEXTURE0)

        view_w, view_h = float(self.view_w), float(self.view_h)
        doc_w, doc_h = float(doc.width), float(doc.height)
        pan_x, pan_y, zoom = float(self.pan_x), float(self.pan_y), float(self.zoom)
        loc = self._loc
        if self.tile_preview:
            tile_offsets = [
                (float(dx) * doc_w, float(dy) * doc_h)
                for dy in (-2, -1, 0, 1, 2)
                for dx in (-2, -1, 0, 1, 2)
            ]
        else:
            tile_offsets = [(0.0, 0.0)]

        def set_common(prog: int, ox: float = 0.0, oy: float = 0.0) -> None:
            GL.glUseProgram(prog)
            GL.glUniform2f(loc(prog, "u_view_size"), view_w, view_h)
            GL.glUniform2f(loc(prog, "u_doc_size"), doc_w, doc_h)
            GL.glUniform2f(loc(prog, "u_pan"), pan_x, pan_y)
            GL.glUniform1f(loc(prog, "u_zoom"), zoom)
            GL.glUniform2f(loc(prog, "u_layer_offset"), ox, oy)

        prog_layer = self.prog_layer
        loc_tex = loc(prog_layer, "u_tex")
        loc_opacity = loc(prog_layer, "u_opacity")
        loc_offset = loc(prog_layer, "u_layer_offset")

        def draw_layers(layers, opacity_scale: float = 1.0) -> None:
            if opacity_scale <= 0.001:
                return
            first = True
            for tox, toy in tile_offsets:
                for ly in layers:
                    if not ly.visible:
                        continue
                    self.upload_layer(ly.id, ly.pixels, ly.dirty_stamp())
                    ox = float(ly.offset_x) + tox
                    oy = float(ly.offset_y) + toy
                    if first:
                        # View uniforms are shared by every layer; set them once.
                        set_common(prog_layer, ox, oy)
                        GL.glUniform1i(loc_tex, 0)
                        first = False
                    else:
                        GL.glUniform2f(loc_offset, ox, oy)
                    GL.glBindTexture(GL.GL_TEXTURE_2D, self.textures[ly.id])
                    GL.glUniform1f(loc_opacity, float(ly.opacity) * float(opacity_scale))
                    GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)

        for tox, toy in tile_offsets:
            set_common(self.prog_checker, tox, toy)
            GL.glUniform3f(loc(self.prog_checker, "u_checker_a"), *checker_a)
            GL.glUniform3f(loc(self.prog_checker, "u_checker_b"), *checker_b)
            GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)

        # Onion skin: previous frame faintly under the current cel while editing
        show_onion = (not playing) and bool(doc.onion_skin) and float(doc.onion_opacity) > 0.001
        if show_onion:
            prev = doc.previous_frame()
            if prev is not None and prev.layers:
                draw_layers(prev.layers, opacity_scale=float(doc.onion_opacity))

        draw_layers(doc.layers, opacity_scale=1.0)

        sel = doc.selection
        if sel.active and sel.mask is not None:
            self.upload_selection(sel.mask, sel.revision)
            set_common(self.prog_sel)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self.sel_tex)
            GL.glUniform1i(loc(self.prog_sel, "u_tex"), 0)
            GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)

        GL.glBindVertexArray(0)
        GL.glUseProgram(0)

        # Drop textures for layers that no longer exist anywhere in the document
        # (deleted layers / frames, undo). Cheap: one set build per frame.
        if len(self.textures) > sum(len(fr.layers) for fr in doc.frames):
            keep = {ly.id for fr in doc.frames for ly in fr.layers}
            keep.update(ly.id for ly in doc.layers)
            self.prune_textures(keep)
