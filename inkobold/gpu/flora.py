"""GPU multipass Flora look (local contrast, grit, selective sat, Orton bloom).

Uses the active OpenGL context (caller must ``make_current``). Falls back to
``None`` when GL is unavailable so the CPU path can take over. Intermediate
targets are reused across calls and kept at image size (RGBA8) to limit VRAM.
Wide blurs are done at a reduced resolution, then upsampled.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from OpenGL import GL

from inkobold.gpu.renderer import _prelude, _program

_VERT = """
layout(location = 0) in vec2 a_pos;
layout(location = 1) in vec2 a_uv;
out vec2 v_uv;
void main() {
    gl_Position = vec4(a_pos * 2.0 - 1.0, 0.0, 1.0);
    v_uv = a_uv;
}
"""

_FRAG_BLIT = """
in vec2 v_uv;
uniform sampler2D u_tex;
out vec4 frag;
void main() {
    frag = texture(u_tex, v_uv);
}
"""

_FRAG_BLUR = """
in vec2 v_uv;
uniform sampler2D u_tex;
uniform vec2 u_texel;
uniform vec2 u_dir;
uniform float u_radius;
out vec4 frag;
void main() {
    float r = max(u_radius, 0.0);
    if (r < 0.5) {
        frag = texture(u_tex, v_uv);
        return;
    }
    // Separable Gaussian; σ ≈ r/2, fixed tap budget (radius scaled into dir).
    float sigma = max(r * 0.5, 0.5);
    float two_s2 = 2.0 * sigma * sigma;
    vec4 acc = texture(u_tex, v_uv) * 1.0;
    float wsum = 1.0;
    vec2 step = u_dir * u_texel;
    // 7 positive taps → 15 samples total; enough for soft HDR-style blur.
    for (int i = 1; i <= 7; ++i) {
        float x = float(i) * (r / 7.0);
        float w = exp(-(x * x) / two_s2);
        vec2 d = step * x;
        acc += texture(u_tex, v_uv + d) * w;
        acc += texture(u_tex, v_uv - d) * w;
        wsum += 2.0 * w;
    }
    frag = acc / wsum;
}
"""

_FRAG_FLORA = """
in vec2 v_uv;
uniform sampler2D u_src;
uniform sampler2D u_soft;
uniform sampler2D u_bloom;
uniform float u_clarity;
uniform float u_bloom;
uniform float u_grit;
uniform float u_neon;
uniform float u_bleach;
uniform float u_lift;
uniform vec2 u_size;
out vec4 frag;

float luma(vec3 c) {
    return dot(c, vec3(0.299, 0.587, 0.114));
}

vec3 overlay(vec3 base, vec3 blend) {
    vec3 low = 2.0 * base * blend;
    vec3 high = 1.0 - 2.0 * (1.0 - base) * (1.0 - blend);
    return mix(low, high, step(0.5, base));
}

vec3 soft_light(vec3 base, vec3 blend) {
    return mix(
        2.0 * base * blend + base * base * (1.0 - 2.0 * blend),
        sqrt(base) * (2.0 * blend - 1.0) + 2.0 * base * (1.0 - blend),
        step(0.5, blend)
    );
}

vec3 screen(vec3 a, vec3 b) {
    return 1.0 - (1.0 - a) * (1.0 - b);
}

// Cheap hash for procedural grit (stable per pixel).
float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

vec3 rgb_to_hsv(vec3 c) {
    float mx = max(c.r, max(c.g, c.b));
    float mn = min(c.r, min(c.g, c.b));
    float d = mx - mn;
    float h = 0.0;
    if (d > 1e-5) {
        if (mx == c.r) h = mod((c.g - c.b) / d, 6.0);
        else if (mx == c.g) h = (c.b - c.r) / d + 2.0;
        else h = (c.r - c.g) / d + 4.0;
        h /= 6.0;
    }
    float s = mx > 1e-5 ? d / mx : 0.0;
    return vec3(h, s, mx);
}

vec3 hsv_to_rgb(vec3 c) {
    float h = c.x * 6.0;
    float s = c.y;
    float v = c.z;
    float i = floor(h);
    float f = h - i;
    float p = v * (1.0 - s);
    float q = v * (1.0 - f * s);
    float t = v * (1.0 - (1.0 - f) * s);
    if (i < 1.0) return vec3(v, t, p);
    if (i < 2.0) return vec3(q, v, p);
    if (i < 3.0) return vec3(p, v, t);
    if (i < 4.0) return vec3(p, q, v);
    if (i < 5.0) return vec3(t, p, v);
    return vec3(v, p, q);
}

void main() {
    vec4 src4 = texture(u_src, v_uv);
    vec3 src = src4.rgb;
    float a = src4.a;
    vec3 soft = texture(u_soft, v_uv).rgb;
    vec3 bloom_c = texture(u_bloom, v_uv).rgb;

    // 1) Lift blacks + mild S-curve.
    float lift = clamp(u_lift, 0.0, 1.0);
    vec3 toned = lift + (1.0 - lift) * src;
    toned = toned * toned * (3.0 - 2.0 * toned); // smoothstep-ish contrast
    toned = mix(src, toned, 0.55);

    // 2) Local contrast / Dragan: soft-light + overlay of inverted wide blur.
    float clarity = clamp(u_clarity, 0.0, 1.5);
    vec3 inv_soft = 1.0 - soft;
    vec3 local = mix(toned, soft_light(toned, inv_soft), clarity * 0.65);
    vec3 hp = clamp(toned - soft + 0.5, 0.0, 1.0);
    local = mix(local, overlay(local, hp), clarity * 0.55);

    // Fine grit-ready detail from a tighter residual.
    vec3 fine = clamp(local - soft + 0.5, 0.0, 1.0);
    local = mix(local, overlay(local, fine), clarity * 0.25);

    // 3) Orton bloom (screen).
    float bloom = clamp(u_bloom, 0.0, 1.0);
    vec3 glow = clamp(bloom_c * 1.15, 0.0, 1.0);
    local = mix(local, screen(local, glow), bloom);

    // 4) Selective saturation: bleach flesh/neutrals, neon yellow/magenta/cyan.
    vec3 hsv = rgb_to_hsv(clamp(local, 0.0, 1.0));
    float h = hsv.x;
    float s = hsv.y;
    float v = hsv.z;
    float bleach = clamp(u_bleach, 0.0, 1.0);
    float neon = clamp(u_neon, 0.0, 1.5);

    // Flesh / warm neutrals (~0.00–0.12) and low-chroma midtones.
    float flesh = smoothstep(0.14, 0.02, abs(h - 0.05)) * smoothstep(0.55, 0.15, s);
    float neutral = smoothstep(0.35, 0.08, s);
    float bleach_w = max(flesh, neutral * 0.85);
    s = mix(s, s * (1.0 - 0.75 * bleach), bleach_w * bleach);
    // Cool ash shift on bleached areas.
    local = hsv_to_rgb(vec3(h, s, v));
    local = mix(local, mix(local, vec3(luma(local)), 0.35) * vec3(0.96, 0.99, 1.04), bleach_w * bleach * 0.85);

    hsv = rgb_to_hsv(clamp(local, 0.0, 1.0));
    h = hsv.x; s = hsv.y; v = hsv.z;
    float yellow = smoothstep(0.18, 0.08, abs(h - 0.14));
    float magenta = smoothstep(0.14, 0.04, min(abs(h - 0.92), abs(h - 0.08)));
    // Magenta band around 0.83–0.95.
    magenta = max(magenta, smoothstep(0.12, 0.04, abs(h - 0.88)));
    float cyan = smoothstep(0.14, 0.04, abs(h - 0.50));
    float accent = max(yellow, max(magenta, cyan)) * smoothstep(0.12, 0.40, hsv.y);
    s = clamp(s * (1.0 + neon * 0.95 * accent) + neon * 0.12 * accent, 0.0, 1.0);
    local = hsv_to_rgb(vec3(h, s, v));

    // 5) Midtone grit weighted by inverse edge strength.
    float grit = clamp(u_grit, 0.0, 1.0);
    if (grit > 1e-4) {
        vec2 texel = 1.0 / max(u_size, vec2(1.0));
        float l = luma(local);
        float l_x = luma(texture(u_src, v_uv + vec2(texel.x, 0.0)).rgb)
                  - luma(texture(u_src, v_uv - vec2(texel.x, 0.0)).rgb);
        float l_y = luma(texture(u_src, v_uv + vec2(0.0, texel.y)).rgb)
                  - luma(texture(u_src, v_uv - vec2(0.0, texel.y)).rgb);
        float edge = clamp(length(vec2(l_x, l_y)) * 2.5, 0.0, 1.0);
        float mid = smoothstep(0.08, 0.35, l) * smoothstep(0.95, 0.55, l);
        float n = hash21(floor(v_uv * u_size) + 17.0) * 2.0 - 1.0;
        float n2 = hash21(floor(v_uv * u_size * 0.5) + 91.0) * 2.0 - 1.0;
        float speck = n * 0.65 + n2 * 0.35;
        local += speck * grit * 0.22 * mid * (1.0 - edge);
    }

    frag = vec4(clamp(local, 0.0, 1.0), a);
}
"""


class FloraGpu:
    """Reusable FBO / shader state for the Flora effect."""

    def __init__(self) -> None:
        self.ready = False
        self.es = False
        self.prog_blit = 0
        self.prog_blur = 0
        self.prog_flora = 0
        self.vao = 0
        self.vbo = 0
        self.fbo = 0
        self._tex: dict[str, int] = {}
        self._size: tuple[int, int] | None = None
        self._blur_size: tuple[int, int] | None = None
        self._uniforms: dict[int, dict[str, int]] = {}

    def _loc(self, prog: int, name: str) -> int:
        table = self._uniforms.setdefault(prog, {})
        loc = table.get(name)
        if loc is None:
            loc = table[name] = int(GL.glGetUniformLocation(prog, name))
        return loc

    def ensure(self, es: bool) -> None:
        if self.ready:
            return
        self.es = es
        pre = _prelude(es)
        self.prog_blit = _program(pre + _VERT, pre + _FRAG_BLIT)
        self.prog_blur = _program(pre + _VERT, pre + _FRAG_BLUR)
        self.prog_flora = _program(pre + _VERT, pre + _FRAG_FLORA)

        verts = np.array(
            [0, 0, 0, 0, 1, 0, 1, 0, 0, 1, 0, 1, 1, 1, 1, 1],
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

        self.fbo = int(GL.glGenFramebuffers(1))
        self.ready = True

    def _tex(self, key: str) -> int:
        tid = self._tex.get(key)
        if tid is None:
            tid = int(GL.glGenTextures(1))
            GL.glBindTexture(GL.GL_TEXTURE_2D, tid)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
            self._tex[key] = tid
        return tid

    def _alloc(self, key: str, w: int, h: int) -> int:
        tid = self._tex(key)
        GL.glBindTexture(GL.GL_TEXTURE_2D, tid)
        GL.glTexImage2D(
            GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, w, h, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, None
        )
        return tid

    def _bind_fbo(self, color_tex: int, w: int, h: int) -> None:
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
        GL.glFramebufferTexture2D(
            GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, color_tex, 0
        )
        GL.glViewport(0, 0, w, h)

    def _draw(self) -> None:
        GL.glBindVertexArray(self.vao)
        GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)
        GL.glBindVertexArray(0)

    def _blur_to(
        self,
        src_tex: int,
        dst_tex: int,
        scratch_tex: int,
        w: int,
        h: int,
        radius: float,
    ) -> None:
        """Separable blur src → dst using scratch (same size)."""
        GL.glDisable(GL.GL_BLEND)
        GL.glUseProgram(self.prog_blur)
        GL.glUniform2f(self._loc(self.prog_blur, "u_texel"), 1.0 / max(w, 1), 1.0 / max(h, 1))
        GL.glUniform1f(self._loc(self.prog_blur, "u_radius"), float(radius))
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glUniform1i(self._loc(self.prog_blur, "u_tex"), 0)

        # Horizontal → scratch
        self._bind_fbo(scratch_tex, w, h)
        GL.glUniform2f(self._loc(self.prog_blur, "u_dir"), 1.0, 0.0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, src_tex)
        self._draw()

        # Vertical → dst
        self._bind_fbo(dst_tex, w, h)
        GL.glUniform2f(self._loc(self.prog_blur, "u_dir"), 0.0, 1.0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, scratch_tex)
        self._draw()

    def _blit(self, src_tex: int, dst_tex: int, w: int, h: int) -> None:
        GL.glDisable(GL.GL_BLEND)
        GL.glUseProgram(self.prog_blit)
        self._bind_fbo(dst_tex, w, h)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, src_tex)
        GL.glUniform1i(self._loc(self.prog_blit, "u_tex"), 0)
        self._draw()

    def apply(
        self,
        pixels: np.ndarray,
        *,
        clarity: float = 75.0,
        bloom: float = 28.0,
        grit: float = 45.0,
        neon: float = 70.0,
        bleach: float = 55.0,
        lift: float = 30.0,
        clarity_radius: int = 28,
        bloom_radius: int = 14,
        es: bool = False,
    ) -> Optional[np.ndarray]:
        """Run Flora on ``pixels`` (HxWx4). Returns new array or ``None`` on failure."""
        h, w = pixels.shape[:2]
        if min(h, w) < 2:
            return np.ascontiguousarray(pixels.copy())

        try:
            self.ensure(es)
        except Exception:
            return None

        # Cap working size — Flora wrapper already limits to ≤1280, but guard anyway.
        max_full = 1280
        work_scale = 1.0
        if max(h, w) > max_full:
            work_scale = max_full / float(max(h, w))
        fw = max(2, int(round(w * work_scale)))
        fh = max(2, int(round(h * work_scale)))

        blur_scale = 1.0
        max_blur_side = 480
        if max(fh, fw) > max_blur_side:
            blur_scale = max_blur_side / float(max(fh, fw))
        bw = max(2, int(round(fw * blur_scale)))
        bh = max(2, int(round(fh * blur_scale)))
        # Map radii into the reduced blur buffer (from original pixels).
        c_r = max(1.0, min(12.0, float(clarity_radius) * work_scale * blur_scale))
        b_r = max(1.0, min(12.0, float(bloom_radius) * work_scale * blur_scale))

        prev_fbo = GL.glGetIntegerv(GL.GL_FRAMEBUFFER_BINDING)
        prev_vp = GL.glGetIntegerv(GL.GL_VIEWPORT)
        prev_blend = GL.glIsEnabled(GL.GL_BLEND)

        try:
            if self._size != (fw, fh):
                self._alloc("src", fw, fh)
                self._alloc("out", fw, fh)
                self._size = (fw, fh)
            if self._blur_size != (bw, bh):
                self._alloc("blur_a", bw, bh)
                self._alloc("blur_b", bw, bh)
                self._alloc("soft", bw, bh)
                self._alloc("bloom", bw, bh)
                self._blur_size = (bw, bh)

            src = np.ascontiguousarray(pixels)
            if src.dtype != np.uint8:
                vmax = (
                    float(np.iinfo(np.uint8).max)
                    if np.issubdtype(src.dtype, np.integer)
                    else 1.0
                )
                src_u8 = np.clip(
                    np.rint(src.astype(np.float32) * (255.0 / max(vmax, 1e-6))), 0, 255
                ).astype(np.uint8)
            else:
                src_u8 = src

            if (fw, fh) != (w, h):
                from PIL import Image

                src_u8 = np.asarray(
                    Image.fromarray(src_u8, mode="RGBA").resize(
                        (fw, fh), Image.Resampling.BILINEAR
                    )
                )

            tid_src = self._tex["src"]
            GL.glBindTexture(GL.GL_TEXTURE_2D, tid_src)
            GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
            GL.glTexImage2D(
                GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, fw, fh, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, src_u8
            )
            self._size = (fw, fh)

            # Downsample src → blur_a
            self._blit(tid_src, self._tex["blur_a"], bw, bh)
            self._blur_to(self._tex["blur_a"], self._tex["soft"], self._tex["blur_b"], bw, bh, c_r)
            self._blur_to(self._tex["blur_a"], self._tex["bloom"], self._tex["blur_b"], bw, bh, b_r)

            GL.glUseProgram(self.prog_flora)
            self._bind_fbo(self._tex["out"], fw, fh)
            status = int(GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER))
            if status != int(GL.GL_FRAMEBUFFER_COMPLETE):
                return None
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tid_src)
            GL.glUniform1i(self._loc(self.prog_flora, "u_src"), 0)
            GL.glActiveTexture(GL.GL_TEXTURE1)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self._tex["soft"])
            GL.glUniform1i(self._loc(self.prog_flora, "u_soft"), 1)
            GL.glActiveTexture(GL.GL_TEXTURE2)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self._tex["bloom"])
            GL.glUniform1i(self._loc(self.prog_flora, "u_bloom"), 2)
            GL.glUniform1f(self._loc(self.prog_flora, "u_clarity"), float(clarity) / 100.0)
            GL.glUniform1f(self._loc(self.prog_flora, "u_bloom"), float(bloom) / 100.0)
            GL.glUniform1f(self._loc(self.prog_flora, "u_grit"), float(grit) / 100.0)
            GL.glUniform1f(self._loc(self.prog_flora, "u_neon"), float(neon) / 100.0)
            GL.glUniform1f(self._loc(self.prog_flora, "u_bleach"), float(bleach) / 100.0)
            GL.glUniform1f(self._loc(self.prog_flora, "u_lift"), float(lift) / 100.0 * 0.45)
            GL.glUniform2f(self._loc(self.prog_flora, "u_size"), float(fw), float(fh))
            self._draw()
            GL.glFinish()  # ensure readback is ready; avoids driver stalls stacking up

            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
            if not self.es:
                try:
                    GL.glReadBuffer(GL.GL_COLOR_ATTACHMENT0)
                except Exception:
                    pass
            GL.glPixelStorei(GL.GL_PACK_ALIGNMENT, 1)
            out = np.empty((fh, fw, 4), dtype=np.uint8)
            GL.glReadPixels(0, 0, fw, fh, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, out)
            out = np.ascontiguousarray(out)

            if (fw, fh) != (w, h):
                from PIL import Image

                out = np.asarray(
                    Image.fromarray(out, mode="RGBA").resize((w, h), Image.Resampling.BILINEAR)
                )

            # Always restore source alpha at the caller's resolution.
            if pixels.dtype == np.uint8 and out.shape[:2] == pixels.shape[:2]:
                out = out.copy()
                out[..., 3] = pixels[..., 3]
                return out
            if pixels.dtype != np.uint8:
                vmax = (
                    float(np.iinfo(pixels.dtype).max)
                    if np.issubdtype(pixels.dtype, np.integer)
                    else 1.0
                )
                scaled = out.astype(np.float32) * (vmax / 255.0)
                if pixels.shape[2] >= 4 and scaled.shape[:2] == pixels.shape[:2]:
                    scaled[..., 3] = pixels[..., 3].astype(np.float32)
                return scaled.astype(pixels.dtype, copy=False)
            return out
        except Exception:
            return None
        finally:
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, int(prev_fbo))
            if prev_vp is not None and len(prev_vp) >= 4:
                GL.glViewport(int(prev_vp[0]), int(prev_vp[1]), int(prev_vp[2]), int(prev_vp[3]))
            if prev_blend:
                GL.glEnable(GL.GL_BLEND)
            else:
                GL.glDisable(GL.GL_BLEND)
            GL.glUseProgram(0)
            GL.glBindVertexArray(0)
            GL.glActiveTexture(GL.GL_TEXTURE0)
