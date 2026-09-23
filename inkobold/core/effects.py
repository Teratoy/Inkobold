"""CPU image effects for the Effects menu (NumPy, no extra deps)."""

from __future__ import annotations

import math

import numpy as np

from inkobold.core.bubbles import (
    bubble_influence_bbox,
    place_bubble_cluster,
    render_bubbles_list,
)
from inkobold.core.sun import DEFAULT_LIGHT_DIR, DEFAULT_MILK_LIGHT_DIR, normalize_light

LIQUIFY_MODES = ("Swirl", "Pinch", "Bulge")
LIQUIFY_BRUSH_MODES = ("Push", "Swirl", "Pinch", "Bulge")
DITHER_MODES = ("Floyd–Steinberg", "Ordered", "Threshold")
EDGE_STYLES = ("Light on dark", "Dark on light")
NORMAL_MAP_Y = ("OpenGL", "DirectX")
METAL_PRESETS = ("Copper", "Silver", "Gold", "Brass", "Bronze")
CURVES_CHANNELS = ("RGB", "Red", "Green", "Blue", "Value")

# Base metal albedo in linear-ish 0–1 RGB (warm metals tint specular too).
_METAL_BASE: dict[str, tuple[float, float, float]] = {
    "Copper": (0.78, 0.42, 0.22),
    "Silver": (0.86, 0.87, 0.89),
    "Gold": (0.90, 0.72, 0.28),
    "Brass": (0.82, 0.68, 0.30),
    "Bronze": (0.70, 0.48, 0.26),
}

# Standard 8×8 Bayer matrix (values 0..63), used for ordered dithering.
_BAYER_8 = np.array(
    [
        [0, 48, 12, 60, 3, 51, 15, 63],
        [32, 16, 44, 28, 35, 19, 47, 31],
        [8, 56, 4, 52, 11, 59, 7, 55],
        [40, 24, 36, 20, 43, 27, 39, 23],
        [2, 50, 14, 62, 1, 49, 13, 61],
        [34, 18, 46, 30, 33, 17, 45, 29],
        [10, 58, 6, 54, 9, 57, 5, 53],
        [42, 26, 38, 22, 41, 25, 37, 21],
    ],
    dtype=np.float64,
)


def pixelate(pixels: np.ndarray, block_size: int = 8) -> np.ndarray:
    """Average colors into square color blocks (nearest upscale)."""
    bs = max(1, int(block_size))
    h, w = pixels.shape[:2]
    if bs <= 1:
        return np.ascontiguousarray(pixels.copy())

    out = np.empty_like(pixels)
    src = pixels.astype(np.float64, copy=False)
    nh, nw = h // bs, w // bs
    if nh > 0 and nw > 0:
        cropped = src[: nh * bs, : nw * bs]
        blocks = cropped.reshape(nh, bs, nw, bs, -1).mean(axis=(1, 3))
        tiled = np.repeat(np.repeat(blocks, bs, axis=0), bs, axis=1)
        out[: nh * bs, : nw * bs] = tiled
    # Remainder strips (right / bottom) — average partial blocks
    if nw * bs < w:
        for y0 in range(0, nh * bs, bs):
            y1 = min(h, y0 + bs)
            block = src[y0:y1, nw * bs : w]
            out[y0:y1, nw * bs : w] = block.mean(axis=(0, 1))
    if nh * bs < h:
        for x0 in range(0, w, bs):
            x1 = min(w, x0 + bs)
            block = src[nh * bs : h, x0:x1]
            out[nh * bs : h, x0:x1] = block.mean(axis=(0, 1))
    return out.astype(pixels.dtype, copy=False)


def _integral_2d(img: np.ndarray) -> np.ndarray:
    """Inclusive prefix sums with a zero pad row/col; shape (h+1, w+1)."""
    out = np.zeros((img.shape[0] + 1, img.shape[1] + 1), dtype=np.float64)
    out[1:, 1:] = img.cumsum(axis=0).cumsum(axis=1)
    return out


def _rect_sum(integ: np.ndarray, y0: np.ndarray, x0: np.ndarray, y1: np.ndarray, x1: np.ndarray) -> np.ndarray:
    """Sum of integ over inclusive [y0,y1] x [x0,x1] via (h+1,w+1) integral."""
    return (
        integ[y1 + 1, x1 + 1]
        - integ[y0, x1 + 1]
        - integ[y1 + 1, x0]
        + integ[y0, x0]
    )


def kuwahara(pixels: np.ndarray, radius: int = 3) -> np.ndarray:
    """Classic Kuwahara filter (edge-preserving blur) via integral images.

    Region choice uses luma variance; output is the mean RGBA of the chosen quadrant.
    """
    r = max(1, int(radius))
    h, w = pixels.shape[:2]
    if r < 1 or min(h, w) < 2:
        return np.ascontiguousarray(pixels.copy())

    src = pixels.astype(np.float64, copy=False)
    # Rec. 601 luma in channel units (works for gray and RGB)
    luma = 0.299 * src[..., 0] + 0.587 * src[..., 1] + 0.114 * src[..., 2]
    S1 = _integral_2d(luma)
    S2 = _integral_2d(luma * luma)
    ch_sums = [_integral_2d(src[..., c]) for c in range(pixels.shape[2])]

    yy, xx = np.mgrid[0:h, 0:w]
    # Four overlapping (r+1)×(r+1) quadrants around each pixel
    quadrants = (
        (yy - r, xx - r, yy, xx),  # NW
        (yy - r, xx, yy, xx + r),  # NE
        (yy, xx - r, yy + r, xx),  # SW
        (yy, xx, yy + r, xx + r),  # SE
    )

    best_var = np.full((h, w), np.inf, dtype=np.float64)
    best_mean = np.zeros_like(src)

    for y0a, x0a, y1a, x1a in quadrants:
        y0 = np.clip(y0a, 0, h - 1)
        x0 = np.clip(x0a, 0, w - 1)
        y1 = np.clip(y1a, 0, h - 1)
        x1 = np.clip(x1a, 0, w - 1)
        # Ensure non-empty after clip
        y1 = np.maximum(y1, y0)
        x1 = np.maximum(x1, x0)
        n = ((y1 - y0 + 1) * (x1 - x0 + 1)).astype(np.float64)
        n = np.maximum(n, 1.0)
        s1 = _rect_sum(S1, y0, x0, y1, x1)
        s2 = _rect_sum(S2, y0, x0, y1, x1)
        var = (s2 / n) - (s1 / n) ** 2
        var = np.maximum(var, 0.0)
        mean = np.stack(
            [_rect_sum(cs, y0, x0, y1, x1) / n for cs in ch_sums],
            axis=-1,
        )
        better = var < best_var
        best_var = np.where(better, var, best_var)
        best_mean = np.where(better[..., None], mean, best_mean)

    out = np.clip(best_mean, 0, np.iinfo(pixels.dtype).max if np.issubdtype(pixels.dtype, np.integer) else 1)
    return out.astype(pixels.dtype, copy=False)


def _gaussian_kernel_1d(radius: int) -> np.ndarray:
    """Normalized 1D Gaussian with support [-radius, radius]; σ = radius / 2."""
    r = max(1, int(radius))
    sigma = max(r / 2.0, 0.5)
    x = np.arange(-r, r + 1, dtype=np.float64)
    k = np.exp(-(x * x) / (2.0 * sigma * sigma))
    k /= k.sum()
    return k


_BLUR_BLOCK_ROWS = 16


def _separable_convolve(src: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """Apply a symmetric 1D kernel separably (H then V) with edge padding.

    The tap loop is memory-bandwidth bound, so work is done on cache-sized
    blocks (rows for the horizontal pass, column strips for the vertical one)
    with a reused scratch buffer. Accumulation order per element is unchanged,
    so the result is bit-identical to the straightforward full-image loop.
    """
    r = len(kernel) // 2
    h, w, c = src.shape
    taps = list(enumerate(kernel))

    # Horizontal pass, blocked over rows.
    padded = np.pad(src, ((0, 0), (r, r), (0, 0)), mode="edge")
    horiz = np.zeros((h, w, c), dtype=np.float64)
    rows = min(h, _BLUR_BLOCK_ROWS)
    tmp = np.empty((rows, w, c), dtype=np.float64)
    for y0 in range(0, h, rows):
        y1 = min(h, y0 + rows)
        t = tmp[: y1 - y0]
        acc = horiz[y0:y1]
        pb = padded[y0:y1]
        for i, k in taps:
            np.multiply(pb[:, i : i + w, :], k, out=t)
            np.add(acc, t, out=acc)

    # Vertical pass, blocked over column strips of similar byte size.
    padded = np.pad(horiz, ((r, r), (0, 0), (0, 0)), mode="edge")
    out = np.zeros((h, w, c), dtype=np.float64)
    cols = max(8, min(w, (rows * w) // max(1, h)))
    tmp = np.empty((h, cols, c), dtype=np.float64)
    for x0 in range(0, w, cols):
        x1 = min(w, x0 + cols)
        t = tmp[:, : x1 - x0]
        acc = out[:, x0:x1]
        pb = padded[:, x0:x1]
        for i, k in taps:
            np.multiply(pb[i : i + h], k, out=t)
            np.add(acc, t, out=acc)
    return out


def gaussian_blur(pixels: np.ndarray, radius: int = 3) -> np.ndarray:
    """Separable Gaussian blur of all channels (including alpha)."""
    r = max(0, int(radius))
    h, w = pixels.shape[:2]
    if r < 1 or min(h, w) < 2:
        return np.ascontiguousarray(pixels.copy())

    src = pixels.astype(np.float64, copy=False)
    blurred = _separable_convolve(src, _gaussian_kernel_1d(r))
    lo, hi = 0, np.iinfo(pixels.dtype).max if np.issubdtype(pixels.dtype, np.integer) else 1
    return np.clip(blurred, lo, hi).astype(pixels.dtype, copy=False)


def _dilate_2d(src: np.ndarray, radius: int) -> np.ndarray:
    """Circular max dilation of a 2D float array (zeros outside bounds)."""
    r = max(0, int(radius))
    if r < 1:
        return src
    yy, xx = np.ogrid[-r : r + 1, -r : r + 1]
    disk = (xx * xx + yy * yy) <= r * r
    padded = np.pad(src, r, mode="constant", constant_values=0.0)
    windows = np.lib.stride_tricks.sliding_window_view(padded, (2 * r + 1, 2 * r + 1))
    # Max over disk taps only.
    return windows[:, :, disk].max(axis=-1)


def _shift_fill(src: np.ndarray, dx: int, dy: int, fill: float = 0.0) -> np.ndarray:
    """Translate HxW[xC] by (dx, dy); vacated pixels get ``fill`` (no wrap)."""
    ox, oy = int(dx), int(dy)
    if ox == 0 and oy == 0:
        return np.ascontiguousarray(src.copy())
    out = np.full_like(src, fill, dtype=np.float64)
    h, w = src.shape[:2]
    src_y0, dst_y0 = (0, oy) if oy >= 0 else (-oy, 0)
    src_x0, dst_x0 = (0, ox) if ox >= 0 else (-ox, 0)
    src_y1 = h - max(oy, 0)
    src_x1 = w - max(ox, 0)
    dst_y1 = dst_y0 + (src_y1 - src_y0)
    dst_x1 = dst_x0 + (src_x1 - src_x0)
    if src_y1 > src_y0 and src_x1 > src_x0:
        out[dst_y0:dst_y1, dst_x0:dst_x1] = src[src_y0:src_y1, src_x0:src_x1]
    return out


def drop_shadow(
    pixels: np.ndarray,
    *,
    distance: int = 8,
    direction: float = 135.0,
    blur: int = 4,
    spread: int = 0,
    color: tuple[int, int, int, int] = (0, 0, 0, 255),
) -> np.ndarray:
    """Cast a drop shadow from the active-layer alpha, then composite the layer on top.

    Direction is degrees in image space: 0° = right, 90° = down.
    Spread dilates the alpha silhouette before blur (CSS-style).
    ``color`` is 8-bit RGBA; alpha controls shadow opacity (default opaque black).
    """
    h, w = pixels.shape[:2]
    vmax = _channel_vmax(pixels)
    src = pixels.astype(np.float64, copy=False)
    src_a = (src[..., 3] / max(vmax, 1e-6)) if src.shape[2] >= 4 else np.ones((h, w), dtype=np.float64)

    cr, cg, cb, ca = color
    shadow_rgb = (
        float(cr) / 255.0 * vmax,
        float(cg) / 255.0 * vmax,
        float(cb) / 255.0 * vmax,
    )
    shadow_op = float(np.clip(ca / 255.0, 0.0, 1.0))

    mask = src_a * shadow_op
    sp = max(0, int(spread))
    if sp >= 1:
        mask = _dilate_2d(mask, sp)

    # Premultiplied shadow buffer so blur softens coverage without RGB fringing.
    a_px = mask * vmax
    shadow = np.zeros((h, w, 4), dtype=np.float64)
    shadow[..., 0] = shadow_rgb[0] * mask
    shadow[..., 1] = shadow_rgb[1] * mask
    shadow[..., 2] = shadow_rgb[2] * mask
    shadow[..., 3] = a_px

    br = max(0, int(blur))
    if br >= 1 and min(h, w) >= 2:
        shadow = _separable_convolve(shadow, _gaussian_kernel_1d(br))

    dist = max(0, int(distance))
    ang = math.radians(float(direction))
    dx = int(round(dist * math.cos(ang)))
    dy = int(round(dist * math.sin(ang)))
    if dx or dy:
        shadow = _shift_fill(shadow, dx, dy, fill=0.0)

    # Unpremultiply shadow for straight-alpha Porter–Duff over the source.
    ba = np.clip(shadow[..., 3] / max(vmax, 1e-6), 0.0, 1.0)
    safe = np.maximum(ba, 1e-8)
    shadow_rgb_s = np.zeros((h, w, 3), dtype=np.float64)
    for c in range(3):
        shadow_rgb_s[..., c] = np.where(ba > 1e-6, (shadow[..., c] / safe) , 0.0)

    # Original layer over shadow (source-over).
    sa = np.clip(src[..., 3] / max(vmax, 1e-6), 0.0, 1.0)
    out_a = sa + ba * (1.0 - sa)
    out = np.zeros_like(src)
    denom = np.maximum(out_a, 1e-8)
    for c in range(3):
        out[..., c] = (src[..., c] * sa + shadow_rgb_s[..., c] * ba * (1.0 - sa)) / denom
    out[..., 3] = out_a * vmax
    out[..., :3] = np.where(out_a[..., None] > 1e-6, out[..., :3], 0.0)
    out = np.clip(out, 0.0, vmax)
    return out.astype(pixels.dtype, copy=False)


def posterize(pixels: np.ndarray, levels: int = 4) -> np.ndarray:
    """Reduce each RGB channel to a fixed number of tonal steps (alpha preserved)."""
    n = max(2, min(256, int(levels)))
    if n >= 256 and np.issubdtype(pixels.dtype, np.integer) and pixels.dtype == np.uint8:
        return np.ascontiguousarray(pixels.copy())

    vmax = float(np.iinfo(pixels.dtype).max) if np.issubdtype(pixels.dtype, np.integer) else 1.0
    # Full precision for this dtype — no-op.
    if np.issubdtype(pixels.dtype, np.integer) and n > int(vmax):
        return np.ascontiguousarray(pixels.copy())

    src = pixels.astype(np.float64, copy=False)
    out = src.copy()
    out[..., :3] = _quantize_levels(src[..., :3], n, vmax)
    return out.astype(pixels.dtype, copy=False)


def threshold(
    pixels: np.ndarray,
    level: float = 50.0,
    style: str = "Light on dark",
) -> np.ndarray:
    """Hard luma cutoff to black / white (alpha preserved).

    ``level`` is the cutoff as a percent of full scale (0–100): pixels with
    Rec. 601 luma at or above the level become white (or black if inverted).
    """
    h, w = pixels.shape[:2]
    if min(h, w) < 1:
        return np.ascontiguousarray(pixels.copy())

    thr = max(0.0, min(100.0, float(level))) / 100.0
    luma = _luma01(pixels)
    bright = luma >= thr

    style_key = str(style).strip()
    if style_key.lower().startswith("dark"):
        # Dark on light: high luma → black.
        on = ~bright
    else:
        on = bright

    vmax = _channel_vmax(pixels)
    src = pixels.astype(np.float64, copy=False)
    out = src.copy()
    tone = np.where(on, vmax, 0.0)
    out[..., 0] = tone
    out[..., 1] = tone
    out[..., 2] = tone
    return out.astype(pixels.dtype, copy=False)


def curves(
    pixels: np.ndarray,
    points: list[tuple[float, float]] | tuple[tuple[float, float], ...] | None = None,
    channel: str = "RGB",
) -> np.ndarray:
    """Remap tones with a smooth curve of control points in [0, 1]² (alpha preserved).

    ``channel`` is one of :data:`CURVES_CHANNELS`. ``RGB`` applies the same LUT to
    R, G, and B; ``Value`` remaps Rec. 601 luma while preserving chromatic ratios.
    """
    h, w = pixels.shape[:2]
    if min(h, w) < 1:
        return np.ascontiguousarray(pixels.copy())

    pts = _normalize_curve_points(points)
    vmax = _channel_vmax(pixels)
    n_lut = int(round(vmax)) + 1 if np.issubdtype(pixels.dtype, np.integer) else 256
    n_lut = max(2, n_lut)
    lut01 = _curve_lut(pts, n_lut)  # [0, 1] per entry

    src = pixels.astype(np.float64, copy=False)
    out = src.copy()
    ch = str(channel).strip().lower()

    def apply_lut(plane: np.ndarray) -> np.ndarray:
        """Map channel values in [0, vmax] through the curve back to [0, vmax]."""
        if np.issubdtype(pixels.dtype, np.integer):
            idx = np.clip(np.rint(plane), 0, n_lut - 1).astype(np.intp)
            return lut01[idx] * vmax
        t = np.clip(plane / max(vmax, 1e-6), 0.0, 1.0)
        pos = t * (n_lut - 1)
        i0 = np.floor(pos).astype(np.intp)
        i1 = np.minimum(i0 + 1, n_lut - 1)
        f = pos - i0
        return (lut01[i0] * (1.0 - f) + lut01[i1] * f) * vmax

    if ch in ("red", "r"):
        out[..., 0] = apply_lut(src[..., 0])
    elif ch in ("green", "g"):
        out[..., 1] = apply_lut(src[..., 1])
    elif ch in ("blue", "b"):
        out[..., 2] = apply_lut(src[..., 2])
    elif ch in ("value", "luma", "v"):
        luma = _luma01(pixels)
        mapped = apply_lut(luma * vmax) / max(vmax, 1e-6)
        scale = np.ones_like(luma)
        nz = luma > 1e-8
        scale[nz] = mapped[nz] / luma[nz]
        # Near-black: lift toward gray so the curve still has an effect.
        out[..., 0] = np.clip(src[..., 0] * scale, 0.0, vmax)
        out[..., 1] = np.clip(src[..., 1] * scale, 0.0, vmax)
        out[..., 2] = np.clip(src[..., 2] * scale, 0.0, vmax)
        black = ~nz
        if black.any():
            lift = mapped[black] * vmax
            out[black, 0] = lift
            out[black, 1] = lift
            out[black, 2] = lift
    else:
        out[..., 0] = apply_lut(src[..., 0])
        out[..., 1] = apply_lut(src[..., 1])
        out[..., 2] = apply_lut(src[..., 2])

    return out.astype(pixels.dtype, copy=False)


def apply_tone_curves(
    pixels: np.ndarray,
    curves_by_channel: dict[str, list[tuple[float, float]] | tuple[tuple[float, float], ...]]
    | None = None,
) -> np.ndarray:
    """Apply per-channel tone curves in order RGB → Red → Green → Blue → Value."""
    out = pixels
    if not curves_by_channel:
        return np.ascontiguousarray(pixels.copy())
    for ch in CURVES_CHANNELS:
        pts = curves_by_channel.get(ch)
        if pts is None or _curve_is_identity(pts):
            continue
        out = curves(out, pts, ch)
    return out if out is not pixels else np.ascontiguousarray(pixels.copy())


def _curve_is_identity(
    points: list[tuple[float, float]] | tuple[tuple[float, float], ...] | None,
) -> bool:
    """True when the curve is (approximately) y = x."""
    pts = _normalize_curve_points(points)
    if len(pts) == 2 and abs(pts[0][1]) < 1e-9 and abs(pts[1][1] - 1.0) < 1e-9:
        return True
    for x, y in pts:
        if abs(x - y) > 1e-6:
            return False
    return True


def _normalize_curve_points(
    points: list[tuple[float, float]] | tuple[tuple[float, float], ...] | None,
) -> list[tuple[float, float]]:
    """Clamp, dedupe by x, and ensure endpoints at x=0 and x=1."""
    raw: list[tuple[float, float]] = []
    if points:
        for p in points:
            if not isinstance(p, (tuple, list)) or len(p) < 2:
                continue
            x = max(0.0, min(1.0, float(p[0])))
            y = max(0.0, min(1.0, float(p[1])))
            raw.append((x, y))
    if not raw:
        raw = [(0.0, 0.0), (1.0, 1.0)]

    raw.sort(key=lambda p: (p[0], p[1]))
    # Keep last point at each distinct x (within a small epsilon).
    uniq: list[tuple[float, float]] = []
    for x, y in raw:
        if uniq and abs(uniq[-1][0] - x) < 1e-9:
            uniq[-1] = (x, y)
        else:
            uniq.append((x, y))

    if uniq[0][0] > 1e-9:
        uniq.insert(0, (0.0, uniq[0][1]))
    else:
        uniq[0] = (0.0, uniq[0][1])
    if uniq[-1][0] < 1.0 - 1e-9:
        uniq.append((1.0, uniq[-1][1]))
    else:
        uniq[-1] = (1.0, uniq[-1][1])

    if len(uniq) < 2:
        return [(0.0, 0.0), (1.0, 1.0)]
    return uniq


def _curve_lut(points: list[tuple[float, float]], size: int) -> np.ndarray:
    """Build a size-entry LUT with values in [0, 1] from normalized curve points."""
    n = max(2, int(size))
    xs = np.array([p[0] for p in points], dtype=np.float64)
    ys = np.array([p[1] for p in points], dtype=np.float64)
    t = np.linspace(0.0, 1.0, n, dtype=np.float64)
    return np.clip(_eval_curve_hermite(xs, ys, t), 0.0, 1.0)


def _eval_curve_hermite(xs: np.ndarray, ys: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Piecewise cubic Hermite with Catmull-Rom tangents; output in curve units."""
    m = len(xs)
    if m == 0:
        return np.zeros_like(t)
    if m == 1:
        return np.full_like(t, float(ys[0]))

    # Finite-difference / Catmull-Rom tangents along x.
    tangents = np.zeros(m, dtype=np.float64)
    for i in range(m):
        if i == 0:
            dx = xs[1] - xs[0]
            tangents[i] = (ys[1] - ys[0]) / dx if dx > 1e-12 else 0.0
        elif i == m - 1:
            dx = xs[-1] - xs[-2]
            tangents[i] = (ys[-1] - ys[-2]) / dx if dx > 1e-12 else 0.0
        else:
            dx = xs[i + 1] - xs[i - 1]
            tangents[i] = (ys[i + 1] - ys[i - 1]) / dx if dx > 1e-12 else 0.0

    idx = np.searchsorted(xs, t, side="right") - 1
    idx = np.clip(idx, 0, m - 2)
    x0 = xs[idx]
    x1 = xs[idx + 1]
    y0 = ys[idx]
    y1 = ys[idx + 1]
    m0 = tangents[idx]
    m1 = tangents[idx + 1]
    dx = np.maximum(x1 - x0, 1e-12)
    u = (t - x0) / dx
    u2 = u * u
    u3 = u2 * u
    h00 = 2.0 * u3 - 3.0 * u2 + 1.0
    h10 = u3 - 2.0 * u2 + u
    h01 = -2.0 * u3 + 3.0 * u2
    h11 = u3 - u2
    return h00 * y0 + h10 * dx * m0 + h01 * y1 + h11 * dx * m1


def _quantize_levels(value: np.ndarray, levels: int, vmax: float) -> np.ndarray:
    """Snap continuous channel values in [0, vmax] to ``levels`` evenly spaced steps."""
    n = max(2, int(levels))
    if n == 2:
        return np.where(value >= vmax * 0.5, vmax, 0.0)
    t = np.clip(value / max(vmax, 1e-6), 0.0, 1.0)
    q = np.round(t * (n - 1)) / (n - 1)
    return q * vmax


def _floyd_steinberg(rgb: np.ndarray, n_levels: int, vmax: float) -> np.ndarray:
    """Left-to-right / top-to-bottom Floyd–Steinberg error diffusion.

    Pixel (y, x) depends only on (y, x-1), (y-1, x-1), (y-1, x) and
    (y-1, x+1), i.e. on pixels with a smaller ``x + 2y``. Every pixel on one
    anti-diagonal ``k = x + 2y`` is therefore independent, so the whole
    diagonal is quantised in one vectorised step (w + 2h steps total instead
    of w*h Python iterations). Error is scattered in the same order the
    sequential scan would add it, so results are bit-identical.
    """
    h, w = rgb.shape[:2]
    work = rgb.astype(np.float64, copy=True)
    out = np.empty_like(work)
    ys_all = np.arange(h, dtype=np.intp)
    for k in range(w + 2 * (h - 1)):
        y_lo = max(0, (k - (w - 1) + 1) // 2)
        y_hi = min(h - 1, k // 2)
        if y_lo > y_hi:
            continue
        ys = ys_all[y_lo : y_hi + 1]
        xs = k - 2 * ys
        old = work[ys, xs]
        new = _quantize_levels(old, n_levels, vmax)
        out[ys, xs] = new
        err = old - new

        below = ys + 1
        has_below = below < h
        if has_below.any():
            by = below[has_below]
            bx = xs[has_below]
            be = err[has_below]
            # Sequential scan adds the (y-1, x+1) share to a pixel before the
            # (y, x-1) share; keep that order (3/16 before 7/16).
            left = bx > 0
            if left.any():
                work[by[left], bx[left] - 1] += be[left] * (3.0 / 16.0)
            work[by, bx] += be * (5.0 / 16.0)
            right = bx + 1 < w
            if right.any():
                work[by[right], bx[right] + 1] += be[right] * (1.0 / 16.0)
        nxt = xs + 1 < w
        if nxt.any():
            work[ys[nxt], xs[nxt] + 1] += err[nxt] * (7.0 / 16.0)
    return out


def dither(
    pixels: np.ndarray,
    mode: str = "Floyd–Steinberg",
    levels: int = 2,
) -> np.ndarray:
    """Reduce color depth with dithering (RGB only; alpha preserved).

    ``levels`` is the number of quantization steps per channel (2 = binary).
    """
    h, w = pixels.shape[:2]
    if min(h, w) < 1:
        return np.ascontiguousarray(pixels.copy())

    mode_key = str(mode).strip()
    # Normalize en/em dash and case for matching.
    mode_norm = mode_key.replace("–", "-").replace("—", "-").title()
    if mode_norm.startswith("Floyd"):
        mode_norm = "Floyd-Steinberg"
    elif mode_norm.startswith("Order"):
        mode_norm = "Ordered"
    elif mode_norm.startswith("Thresh"):
        mode_norm = "Threshold"
    else:
        mode_norm = "Floyd-Steinberg"

    n_levels = max(2, min(64, int(levels)))
    vmax = float(np.iinfo(pixels.dtype).max) if np.issubdtype(pixels.dtype, np.integer) else 1.0
    src = pixels.astype(np.float64, copy=False)
    out = src.copy()
    rgb = src[..., :3].copy()

    if mode_norm == "Threshold":
        out[..., :3] = _quantize_levels(rgb, n_levels, vmax)
    elif mode_norm == "Ordered":
        # Tile Bayer threshold in [0, 1), scale into one quantization bin.
        yy = np.arange(h) % 8
        xx = np.arange(w) % 8
        thresh = (_BAYER_8[np.ix_(yy, xx)] + 0.5) / 64.0  # HxW
        if n_levels == 2:
            step = vmax
            biased = rgb + (thresh[..., None] - 0.5) * step
            out[..., :3] = _quantize_levels(biased, 2, vmax)
        else:
            # Spread threshold within each quantization bin.
            step = vmax / (n_levels - 1)
            biased = rgb + (thresh[..., None] - 0.5) * step
            out[..., :3] = _quantize_levels(biased, n_levels, vmax)
    else:
        out[..., :3] = _floyd_steinberg(rgb, n_levels, vmax)

    lo, hi = 0.0, vmax
    out[..., :3] = np.clip(out[..., :3], lo, hi)
    return out.astype(pixels.dtype, copy=False)


def _bilinear_sample(
    src: np.ndarray,
    y: np.ndarray,
    x: np.ndarray,
    *,
    wrap: bool = False,
) -> np.ndarray:
    """Sample HxWxC at float coords with edge clamping (or toroidal wrap)."""
    h, w, _c = src.shape
    if wrap and h > 0 and w > 0:
        y_cl = np.mod(y, h)
        x_cl = np.mod(x, w)
        y0 = np.floor(y_cl).astype(np.int32) % h
        x0 = np.floor(x_cl).astype(np.int32) % w
        y1 = (y0 + 1) % h
        x1 = (x0 + 1) % w
        wy = (y_cl - np.floor(y_cl))[..., None]
        wx = (x_cl - np.floor(x_cl))[..., None]
    else:
        y_cl = np.clip(y, 0.0, h - 1.0001)
        x_cl = np.clip(x, 0.0, w - 1.0001)
        y0 = np.floor(y_cl).astype(np.int32)
        x0 = np.floor(x_cl).astype(np.int32)
        y1 = np.minimum(y0 + 1, h - 1)
        x1 = np.minimum(x0 + 1, w - 1)
        wy = (y_cl - y0)[..., None]
        wx = (x_cl - x0)[..., None]
    # Gather first, cast after: converting only the sampled neighbours (not the
    # whole image) keeps small brush stamps O(stamp) instead of O(image).
    # Integer -> float64 conversion is exact, so results are unchanged.
    return (
        src[y0, x0].astype(np.float64) * (1 - wy) * (1 - wx)
        + src[y0, x1].astype(np.float64) * (1 - wy) * wx
        + src[y1, x0].astype(np.float64) * wy * (1 - wx)
        + src[y1, x1].astype(np.float64) * wy * wx
    )


def _liquify_origin_displacements(
    yy: np.ndarray,
    xx: np.ndarray,
    cy: float,
    cx: float,
    radius: float,
    mode_key: str,
    amt: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (disp_y, disp_x, falloff) for one warp origin."""
    dy = yy - cy
    dx = xx - cx
    dist = np.sqrt(dx * dx + dy * dy)
    t = np.clip(dist / max(radius, 1e-6), 0.0, 1.0)
    falloff = (1.0 - t * t) ** 2
    falloff = np.where(dist <= radius, falloff, 0.0)

    if mode_key == "Swirl":
        # Up to ~2π turns at full strength; alternate spin per origin via amt sign.
        angle = amt * falloff * (2.0 * np.pi)
        cos_a = np.cos(angle)
        sin_a = np.sin(angle)
        src_x = cx + dx * cos_a + dy * sin_a
        src_y = cy - dx * sin_a + dy * cos_a
    elif mode_key == "Pinch":
        scale = 1.0 - abs(amt) * falloff * 0.85
        scale = np.maximum(scale, 0.05)
        src_x = cx + dx / scale
        src_y = cy + dy / scale
    else:  # Bulge
        scale = 1.0 + abs(amt) * falloff * 1.25
        src_x = cx + dx / scale
        src_y = cy + dy / scale

    return src_y - yy, src_x - xx, falloff


def liquify(
    pixels: np.ndarray,
    mode: str = "Swirl",
    strength: float = 50.0,
    grid: int = 3,
    radius_pct: float = 100.0,
) -> np.ndarray:
    """Full-image liquify via a grid of warp origins (swirl / pinch / bulge).

    ``grid`` is the N in an N×N lattice of origins spanning the layer. Each
    origin's radius is sized from neighbor spacing so the warps cover the
    whole image; ``radius_pct`` scales that auto radius (100 = touch neighbors).
    """
    h, w = pixels.shape[:2]
    if min(h, w) < 2:
        return np.ascontiguousarray(pixels.copy())

    mode_key = str(mode).strip().title()
    if mode_key not in LIQUIFY_MODES:
        mode_key = "Swirl"

    amt = max(0.0, min(100.0, float(strength))) / 100.0
    if amt <= 1e-6:
        return np.ascontiguousarray(pixels.copy())

    g = max(1, min(8, int(grid)))
    radius_scale = max(20.0, min(150.0, float(radius_pct))) / 100.0

    if g == 1:
        centers = [((h - 1) * 0.5, (w - 1) * 0.5)]
        spacing = 0.5 * min(h, w)
    else:
        ys = np.linspace(0.0, h - 1.0, g)
        xs = np.linspace(0.0, w - 1.0, g)
        centers = [(float(y), float(x)) for y in ys for x in xs]
        spacing = min((h - 1) / (g - 1), (w - 1) / (g - 1))

    # Overlap neighbors so the whole canvas is warped, not island disks.
    radius = max(1.0, spacing * 0.72 * radius_scale)

    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    disp_y = np.zeros((h, w), dtype=np.float64)
    disp_x = np.zeros((h, w), dtype=np.float64)

    for i, (cy, cx) in enumerate(centers):
        # Alternate swirl direction across the lattice for richer full-image flow.
        local_amt = amt if (i % 2 == 0 or mode_key != "Swirl") else -amt
        dy, dx, _falloff = _liquify_origin_displacements(
            yy, xx, cy, cx, radius, mode_key, local_amt
        )
        # Displacements are already soft-edged; summing overlaps covers the full image.
        disp_y += dy
        disp_x += dx

    src_y = yy + disp_y
    src_x = xx + disp_x

    sampled = _bilinear_sample(pixels, src_y, src_x)
    lo, hi = 0, np.iinfo(pixels.dtype).max if np.issubdtype(pixels.dtype, np.integer) else 1
    return np.clip(sampled, lo, hi).astype(pixels.dtype, copy=False)


def liquify_brush_stamp(
    pixels: np.ndarray,
    x: float,
    y: float,
    radius: float,
    mode: str = "Push",
    strength: float = 0.45,
    *,
    dir_x: float = 0.0,
    dir_y: float = 0.0,
    mask: np.ndarray | None = None,
    wrap: bool = False,
) -> None:
    """Warp a soft circular neighborhood in-place (liquify brush stamp)."""
    r = max(0.5, float(radius))
    amt = float(np.clip(strength, 0.0, 1.0))
    if amt <= 1e-6:
        return

    mode_key = str(mode).strip().title()
    if mode_key not in LIQUIFY_BRUSH_MODES:
        mode_key = "Push"

    h, w = pixels.shape[:2]
    if wrap and w > 0 and h > 0:
        # Fold into the tile, then stamp edge replicas (same idea as paint.stamp_centers).
        cx = float(x) - float(w) * math.floor(float(x) / float(w))
        cy = float(y) - float(h) * math.floor(float(y) / float(h))
        xs = [cx]
        ys = [cy]
        ext = r + 1.5
        if cx - ext < 0:
            xs.append(cx + w)
        if cx + ext > w:
            xs.append(cx - w)
        if cy - ext < 0:
            ys.append(cy + h)
        if cy + ext > h:
            ys.append(cy - h)
        for py in ys:
            for px in xs:
                _liquify_brush_stamp_at(
                    pixels,
                    px,
                    py,
                    r,
                    mode_key,
                    amt,
                    dir_x=dir_x,
                    dir_y=dir_y,
                    mask=mask,
                    sample_wrap=True,
                )
        return

    _liquify_brush_stamp_at(
        pixels,
        x,
        y,
        r,
        mode_key,
        amt,
        dir_x=dir_x,
        dir_y=dir_y,
        mask=mask,
        sample_wrap=False,
    )


def _liquify_brush_stamp_at(
    pixels: np.ndarray,
    x: float,
    y: float,
    r: float,
    mode_key: str,
    amt: float,
    *,
    dir_x: float,
    dir_y: float,
    mask: np.ndarray | None,
    sample_wrap: bool,
) -> None:
    h, w = pixels.shape[:2]
    x0 = max(0, int(math.floor(x - r - 1)))
    y0 = max(0, int(math.floor(y - r - 1)))
    x1 = min(w, int(math.ceil(x + r + 2)))
    y1 = min(h, int(math.ceil(y + r + 2)))
    if x0 >= x1 or y0 >= y1:
        return

    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float64)
    dy = yy - y
    dx = xx - x
    dist = np.sqrt(dx * dx + dy * dy)
    t = np.clip(dist / r, 0.0, 1.0)
    falloff = (1.0 - t * t) ** 2
    falloff = np.where(dist <= r, falloff, 0.0)
    if mask is not None:
        falloff = falloff * (mask[y0:y1, x0:x1] > 0).astype(np.float64)
    if not np.any(falloff):
        return

    if mode_key == "Push":
        mag = math.hypot(dir_x, dir_y)
        if mag < 1e-6:
            return
        nx, ny = dir_x / mag, dir_y / mag
        # Sample from behind the stroke so pixels flow along the drag.
        push = amt * falloff * r * 0.9
        src_x = xx - nx * push
        src_y = yy - ny * push
    elif mode_key == "Swirl":
        angle = amt * falloff * (1.15 * math.pi)
        cos_a = np.cos(angle)
        sin_a = np.sin(angle)
        src_x = x + dx * cos_a + dy * sin_a
        src_y = y - dx * sin_a + dy * cos_a
    elif mode_key == "Pinch":
        scale = 1.0 - amt * falloff * 0.85
        scale = np.maximum(scale, 0.05)
        src_x = x + dx / scale
        src_y = y + dy / scale
    else:  # Bulge
        scale = 1.0 + amt * falloff * 1.25
        src_x = x + dx / scale
        src_y = y + dy / scale

    region = pixels[y0:y1, x0:x1]
    sampled = _bilinear_sample(pixels, src_y, src_x, wrap=sample_wrap)
    active = falloff > 1e-6
    out = region.astype(np.float64, copy=True)
    out[active] = sampled[active]
    lo, hi = 0, np.iinfo(pixels.dtype).max if np.issubdtype(pixels.dtype, np.integer) else 1
    pixels[y0:y1, x0:x1] = np.clip(out, lo, hi).astype(pixels.dtype, copy=False)


def _channel_vmax(pixels: np.ndarray) -> float:
    return float(np.iinfo(pixels.dtype).max) if np.issubdtype(pixels.dtype, np.integer) else 1.0


def _luma01(pixels: np.ndarray) -> np.ndarray:
    """Rec. 601 luma normalized to [0, 1]."""
    src = pixels.astype(np.float64, copy=False)
    vmax = max(_channel_vmax(pixels), 1e-6)
    return (0.299 * src[..., 0] + 0.587 * src[..., 1] + 0.114 * src[..., 2]) / vmax


def _sobel_gradients(height: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Sobel ∂h/∂x and ∂h/∂y with edge padding (image y increases downward)."""
    p = np.pad(height, 1, mode="edge")
    # Horizontal: [-1 0 1; -2 0 2; -1 0 1]
    gx = (
        -p[:-2, :-2]
        + p[:-2, 2:]
        - 2.0 * p[1:-1, :-2]
        + 2.0 * p[1:-1, 2:]
        - p[2:, :-2]
        + p[2:, 2:]
    )
    # Vertical: [-1 -2 -1; 0 0 0; 1 2 1]
    gy = (
        -p[:-2, :-2]
        - 2.0 * p[:-2, 1:-1]
        - p[:-2, 2:]
        + p[2:, :-2]
        + 2.0 * p[2:, 1:-1]
        + p[2:, 2:]
    )
    return gx, gy


def emboss(
    pixels: np.ndarray,
    direction: float = 135.0,
    depth: float = 100.0,
    height: int = 2,
    radius: int = 0,
) -> np.ndarray:
    """Classic directional emboss from luma-as-height (alpha preserved).

    Flat areas map to mid-gray; slopes facing the light go bright and the
    opposite side goes dark. ``direction`` is degrees in image space:
    0° = right, 90° = down (same convention as Drop Shadow). ``depth`` scales
    relief contrast (0–200). ``height`` is the sample offset in pixels.
    Optional ``radius`` pre-smooths the height field.
    """
    h, w = pixels.shape[:2]
    if min(h, w) < 2:
        return np.ascontiguousarray(pixels.copy())

    field = _luma01(pixels)
    r = max(0, int(radius))
    if r >= 1:
        stacked = field[..., None]
        field = _separable_convolve(stacked, _gaussian_kernel_1d(r))[..., 0]
        field = np.clip(field, 0.0, 1.0)

    # Sample height along the light ray; relief = local − opposite.
    dist = max(1, min(64, int(height)))
    ang = math.radians(float(direction))
    dx = dist * math.cos(ang)
    dy = dist * math.sin(ang)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    shifted = _bilinear_sample(field[..., None], yy + dy, xx + dx)[..., 0]
    relief = field - shifted

    # Unit step over ``height`` pixels → |relief| ≈ 1; depth 100 ≈ full swing.
    amt = max(0.0, min(200.0, float(depth))) / 100.0
    gray = np.clip(0.5 + relief * amt, 0.0, 1.0)

    vmax = _channel_vmax(pixels)
    src = pixels.astype(np.float64, copy=False)
    out = src.copy()
    tone = gray * vmax
    out[..., 0] = tone
    out[..., 1] = tone
    out[..., 2] = tone
    return out.astype(pixels.dtype, copy=False)


def edge_detect(
    pixels: np.ndarray,
    strength: float = 100.0,
    threshold: float = 0.0,
    radius: int = 0,
    style: str = "Light on dark",
) -> np.ndarray:
    """Sobel edge detection on luma (alpha preserved).

    ``strength`` scales the edge magnitude (0–200). ``threshold`` zeros weak
    edges (0–100% of full scale). Optional ``radius`` pre-blurs luma.
    """
    h, w = pixels.shape[:2]
    if min(h, w) < 2:
        return np.ascontiguousarray(pixels.copy())

    height = _luma01(pixels)
    r = max(0, int(radius))
    if r >= 1:
        # Blur luma only via a 1-channel separable pass.
        stacked = height[..., None]
        height = _separable_convolve(stacked, _gaussian_kernel_1d(r))[..., 0]
        height = np.clip(height, 0.0, 1.0)

    gx, gy = _sobel_gradients(height)
    # A unit step maps to Sobel magnitude ≈ 4; scale so strength 100 ≈ full white.
    mag = np.sqrt(gx * gx + gy * gy) / 4.0
    amt = max(0.0, min(200.0, float(strength))) / 100.0
    mag = np.clip(mag * amt, 0.0, 1.0)

    thr = max(0.0, min(100.0, float(threshold))) / 100.0
    if thr > 1e-6:
        mag = np.where(mag >= thr, mag, 0.0)

    style_key = str(style).strip()
    if style_key.lower().startswith("dark"):
        gray = 1.0 - mag
    else:
        gray = mag

    vmax = _channel_vmax(pixels)
    src = pixels.astype(np.float64, copy=False)
    out = src.copy()
    tone = gray * vmax
    out[..., 0] = tone
    out[..., 1] = tone
    out[..., 2] = tone
    return out.astype(pixels.dtype, copy=False)


def normal_map(
    pixels: np.ndarray,
    strength: float = 100.0,
    radius: int = 0,
    y_convention: str = "OpenGL",
) -> np.ndarray:
    """Bake a tangent-space normal map from luma-as-height (alpha preserved).

    RGB packs ``(nx, ny, nz)`` from [-1, 1] into [0, vmax]. ``strength`` scales
    the XY gradients. ``radius`` optionally smooths the height field first.
    ``y_convention`` flips the green channel for DirectX vs OpenGL tooling.
    """
    h, w = pixels.shape[:2]
    if min(h, w) < 2:
        return np.ascontiguousarray(pixels.copy())

    height = _luma01(pixels)
    r = max(0, int(radius))
    if r >= 1:
        stacked = height[..., None]
        height = _separable_convolve(stacked, _gaussian_kernel_1d(r))[..., 0]
        height = np.clip(height, 0.0, 1.0)

    gx, gy = _sobel_gradients(height)
    # Match common height→normal bake: stronger = steeper slopes.
    s = max(0.0, min(500.0, float(strength))) / 100.0
    nx = -gx * s
    # Image y grows downward; OpenGL normal maps treat +Y as up in texture space.
    ny = gy * s
    nz = np.ones_like(height)
    inv = 1.0 / np.maximum(1e-5, np.sqrt(nx * nx + ny * ny + nz * nz))
    nx, ny, nz = nx * inv, ny * inv, nz * inv

    y_key = str(y_convention).strip().lower()
    if y_key.startswith("direct"):
        ny = -ny

    vmax = _channel_vmax(pixels)
    src = pixels.astype(np.float64, copy=False)
    out = src.copy()
    out[..., 0] = (nx * 0.5 + 0.5) * vmax
    out[..., 1] = (ny * 0.5 + 0.5) * vmax
    out[..., 2] = (nz * 0.5 + 0.5) * vmax
    lo, hi = 0.0, vmax
    out[..., :3] = np.clip(out[..., :3], lo, hi)
    return out.astype(pixels.dtype, copy=False)


def _rgb_to_hsv(rgb: np.ndarray) -> np.ndarray:
    """Vectorized RGB→HSV; ``rgb`` last-dim size 3, values in [0, 1]."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    diff = mx - mn
    h = np.zeros_like(mx)
    mask = diff > 1e-8
    r_is = mask & (mx == r)
    g_is = mask & (mx == g) & ~r_is
    b_is = mask & ~(r_is | g_is)
    h[r_is] = ((g[r_is] - b[r_is]) / diff[r_is]) % 6.0
    h[g_is] = (b[g_is] - r[g_is]) / diff[g_is] + 2.0
    h[b_is] = (r[b_is] - g[b_is]) / diff[b_is] + 4.0
    h = h / 6.0
    s = np.where(mx > 1e-8, diff / np.maximum(mx, 1e-8), 0.0)
    return np.stack([h, s, mx], axis=-1)


def _hsv_to_rgb(hsv: np.ndarray) -> np.ndarray:
    """Vectorized HSV→RGB; ``hsv`` last-dim size 3, H in [0,1], S/V in [0,1]."""
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    i = np.floor(h * 6.0).astype(np.int32)
    f = h * 6.0 - i
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    i_mod = i % 6
    r = np.choose(i_mod, [v, q, p, p, t, v])
    g = np.choose(i_mod, [t, v, v, q, p, p])
    b = np.choose(i_mod, [p, p, t, v, v, q])
    return np.stack([r, g, b], axis=-1)


def _shift_hue_rgb(rgb: tuple[float, float, float], hue_deg: float) -> np.ndarray:
    """Rotate hue of a single RGB triple by ``hue_deg`` degrees; return (3,) array."""
    base = np.asarray(rgb, dtype=np.float64).reshape(1, 1, 3)
    hsv = _rgb_to_hsv(base)
    hsv[..., 0] = (hsv[..., 0] + float(hue_deg) / 360.0) % 1.0
    return _hsv_to_rgb(hsv).reshape(3)


def metal_relief(
    pixels: np.ndarray,
    metal: str = "Copper",
    depth: float = 70.0,
    glossiness: float = 55.0,
    shadow: float = 40.0,
    reflections: float = 50.0,
    exposure: float = 100.0,
    hue: float = 0.0,
    highlight_hue: float = 0.0,
    shadow_hue: float = 0.0,
    saturation: float = 100.0,
    radius: int = 1,
    light_dir: tuple[float, float, float] = DEFAULT_LIGHT_DIR,
) -> np.ndarray:
    """Render luma as embossed metal relief (copper / silver / gold / …).

    Height comes from image luma. Lighting is Blinn-style with metal albedo,
    contact shadows, and a fake environment reflection. Alpha is preserved.

    ``depth``          — relief contrast / light wrap (0–100)
    ``glossiness``     — specular sharpness (0–100)
    ``shadow``         — dark-side / contact shadow strength (0–100)
    ``reflections``    — environment / specular intensity (0–100)
    ``exposure``       — overall light level (0–200; 100 = neutral)
    ``hue``            — degrees shift of the metal base (−180…180)
    ``highlight_hue``  — extra hue shift on lit / specular areas (−180…180)
    ``shadow_hue``     — extra hue shift on dark / form-shadow areas (−180…180)
    ``saturation``     — metal chroma (0–200; 100 = preset default)
    ``radius``         — optional height-field pre-smooth
    """
    h, w = pixels.shape[:2]
    if min(h, w) < 2:
        return np.ascontiguousarray(pixels.copy())

    metal_key = str(metal).strip()
    base_rgb = _METAL_BASE.get(metal_key, _METAL_BASE["Copper"])
    albedo = _shift_hue_rgb(base_rgb, float(hue))
    # 100 → keep preset sat; 0 → grayscale metal; 200 → double chroma.
    sat_n = max(0.0, min(200.0, float(saturation))) / 100.0
    if abs(sat_n - 1.0) > 1e-6:
        hsv = _rgb_to_hsv(albedo.reshape(1, 1, 3))
        hsv[..., 1] = np.clip(hsv[..., 1] * sat_n, 0.0, 1.0)
        albedo = _hsv_to_rgb(hsv).reshape(3)
    albedo_hi = _shift_hue_rgb(tuple(float(c) for c in albedo), float(highlight_hue))
    albedo_lo = _shift_hue_rgb(tuple(float(c) for c in albedo), float(shadow_hue))

    depth_n = max(0.0, min(100.0, float(depth))) / 100.0
    gloss_n = max(0.0, min(100.0, float(glossiness))) / 100.0
    shadow_n = max(0.0, min(100.0, float(shadow))) / 100.0
    refl_n = max(0.0, min(100.0, float(reflections))) / 100.0
    # 100 → 1×, 0 → ~0.25×, 200 → ~2.5× (soft curve so mid stays usable).
    exp_n = max(0.0, min(200.0, float(exposure))) / 100.0
    exposure_mul = 0.25 + 0.75 * exp_n + 0.50 * max(0.0, exp_n - 1.0)

    height = _luma01(pixels)
    r = max(0, int(radius))
    if r >= 1:
        stacked = height[..., None]
        height = _separable_convolve(stacked, _gaussian_kernel_1d(r))[..., 0]
        height = np.clip(height, 0.0, 1.0)

    gx, gy = _sobel_gradients(height)
    # Steeper relief with depth; keep a floor so flat areas still catch light.
    slope = 0.35 + 2.4 * depth_n
    nx = -gx * slope
    ny = gy * slope  # image y down → flip for upward lighting frame
    nz = np.full_like(height, 0.45 + 0.35 * (1.0 - depth_n))
    inv = 1.0 / np.maximum(1e-5, np.sqrt(nx * nx + ny * ny + nz * nz))
    nx, ny, nz = nx * inv, ny * inv, nz * inv

    # Key light (canvas sun when enabled).
    lx, ly, lz = normalize_light(*light_dir)
    ndotl = np.clip(nx * lx + ny * ly + nz * lz, 0.0, 1.0)

    # Soft masks: lit vs dark sides for per-lobe hue tinting.
    lit_w = ndotl * ndotl
    dark_w = (1.0 - ndotl) ** 2

    # Blinn half-vector vs camera looking down +Z.
    hx, hy, hz = lx, ly, lz + 1.0
    invh = 1.0 / math.sqrt(hx * hx + hy * hy + hz * hz)
    ndoth = np.clip(nx * hx * invh + ny * hy * invh + nz * hz * invh, 0.0, 1.0)
    # Gloss: soft plastic → hard metal highlight.
    spec_pow = 4.0 + 96.0 * (gloss_n ** 1.4)
    spec = ndoth ** spec_pow

    # Fake environment: sky gradient along reflected view (R = 2(N·V)N − V, V≈(0,0,1)).
    # With V=(0,0,1): R = (2 nz nx, 2 nz ny, 2 nz² − 1).
    ry = 2.0 * nz * ny
    rz = 2.0 * nz * nz - 1.0
    # Map reflection to a cool→warm sky / floor gradient.
    env_t = np.clip(0.5 + 0.5 * ry + 0.25 * rz, 0.0, 1.0)
    # Local “reflection” from neighboring height (cheap chrome shimmer).
    local = np.clip(0.5 + 0.5 * (height - 0.5) + 0.35 * ndotl, 0.0, 1.0)
    env = 0.55 * env_t + 0.45 * local
    # Cool fill from above, warm bounce from below — then tint by metal.
    env_rgb = np.stack(
        [
            0.55 + 0.45 * env,
            0.62 + 0.30 * env,
            0.78 - 0.25 * env,
        ],
        axis=-1,
    )
    # Diffuse albedo lerps base → highlight hue on lit faces, → shadow hue in dark.
    diffuse_albedo = (
        albedo
        + (albedo_hi - albedo) * lit_w[..., None]
        + (albedo_lo - albedo) * dark_w[..., None]
    )
    # Specular / Fresnel use highlight-shifted metal; cool metals stay nearer white.
    metal_sat = float(np.clip(np.max(albedo_hi) - np.min(albedo_hi), 0.0, 1.0))
    spec_tint = (
        (1.0 - 0.65 * metal_sat) * np.array([1.0, 1.0, 1.0])
        + 0.65 * metal_sat * albedo_hi
    )

    ambient = 0.18 + 0.22 * (1.0 - depth_n)
    diffuse = (0.35 + 0.55 * depth_n) * ndotl
    # Soft rim so raised edges catch light.
    rim = np.clip(1.0 - height, 0.0, 1.0) * ndotl * (0.08 + 0.22 * depth_n)
    shade = ambient + diffuse + rim

    rgb = diffuse_albedo * shade[..., None]
    # Specular lobe + environment reflections (highlight-tinted).
    gloss_boost = 0.15 + 0.85 * gloss_n
    rgb = rgb + spec_tint * ((0.15 + 0.85 * refl_n) * gloss_boost * spec[..., None])
    rgb = rgb + diffuse_albedo * env_rgb * (
        (0.08 + 0.42 * refl_n) * (0.35 + 0.65 * gloss_n)
    )
    # Fresnel-ish edge lift for chrome look at grazing angles.
    fresnel = np.clip(1.0 - nz, 0.0, 1.0) ** 2
    rgb = rgb + spec_tint * (fresnel * (0.04 + 0.18 * refl_n))[..., None]

    # Contact / form shadow on the dark side — multiply with shadow-hue tint.
    form_shadow = (1.0 - ndotl) * (0.15 + 0.55 * shadow_n) * np.clip(
        1.0 - height * 0.55, 0.0, 1.0
    )
    shadow_mul = 1.0 - form_shadow
    # Lean residual shade toward shadow-hue metal instead of pure black crush.
    shadow_tint = 0.55 + 0.45 * (albedo_lo / np.maximum(np.max(albedo_lo), 1e-6))
    rgb = rgb * (
        shadow_mul[..., None] * (1.0 - dark_w[..., None])
        + (shadow_mul[..., None] * shadow_tint) * dark_w[..., None]
    )

    rgb = rgb * exposure_mul

    vmax = _channel_vmax(pixels)
    src = pixels.astype(np.float64, copy=False)
    out = src.copy()
    out[..., :3] = np.clip(rgb * vmax, 0.0, vmax)
    return out.astype(pixels.dtype, copy=False)


def milk(
    pixels: np.ndarray,
    depth: float = 55.0,
    glossiness: float = 75.0,
    thickness: float = 90.0,
    shadow: float = 30.0,
    exposure: float = 100.0,
    hue: float = 8.0,
    saturation: float = 35.0,
    radius: int = 8,
    bubbles: float = 45.0,
    bubble_size: float = 50.0,
    clear_spots: float = 40.0,
    edge_melt: float = 70.0,
    wetness: float = 45.0,
    expiration: float = 0.0,
    light_dir: tuple[float, float, float] = DEFAULT_MILK_LIGHT_DIR,
) -> np.ndarray:
    """Render luma as creamy viscous fluid relief (milky / seminal look).

    Same height-field emboss as metal relief, but shaded as soft pearlescent
    white fluid with wet sheen, liquid content-edge melt, air bubbles, and
    blurry translucent voids.

    ``depth``        — relief contrast / light wrap (0–100)
    ``glossiness``   — wet specular sheen (0–100)
    ``thickness``    — opacity vs translucent edge glow (0–100)
    ``shadow``       — form-shadow strength (0–100)
    ``exposure``     — overall light level (0–200; 100 = neutral)
    ``hue``          — degrees tint of the cream base (−180…180)
    ``saturation``   — cream chroma (0–200; lower = whiter)
    ``radius``       — height-field / liquid pre-smooth (blob feel)
    ``bubbles``      — brush-mode air bubbles on flat color (0–100)
    ``bubble_size``  — bubble diameter scale (0–100; 50 = default)
    ``clear_spots``  — blurry see-through voids (0–100)
    ``edge_melt``    — soft liquid falloff at content / silhouette edges (0–100)
    ``wetness``      — surface wetness: darker troughs, hotter specular (0–100)
    ``expiration``   — spoil the milk: yellow → mould → brown (0–100)
    """
    h, w = pixels.shape[:2]
    if min(h, w) < 2:
        return np.ascontiguousarray(pixels.copy())

    wet_n = max(0.0, min(100.0, float(wetness))) / 100.0
    spoil_n = max(0.0, min(100.0, float(expiration))) / 100.0

    # Ivory cream base — warms / yellows / browns as expiration rises.
    cream_fresh = (0.92, 0.89, 0.82)
    cream_sour = (0.86, 0.78, 0.48)   # yellowy whey
    cream_rot = (0.52, 0.36, 0.18)    # brown curd
    # 0→0.45 yellow, 0.45→1.0 brown.
    if spoil_n <= 0.45:
        t = spoil_n / 0.45
        cream0 = tuple(
            cream_fresh[i] * (1.0 - t) + cream_sour[i] * t for i in range(3)
        )
    else:
        t = (spoil_n - 0.45) / 0.55
        cream0 = tuple(
            cream_sour[i] * (1.0 - t) + cream_rot[i] * t for i in range(3)
        )
    cream = _shift_hue_rgb(cream0, float(hue))
    sat_n = max(0.0, min(200.0, float(saturation))) / 100.0
    # Spoilage pushes chroma up a bit even at low sat settings.
    sat_eff = sat_n * (1.0 + 0.55 * spoil_n)
    if abs(sat_eff - 1.0) > 1e-6:
        hsv = _rgb_to_hsv(cream.reshape(1, 1, 3))
        hsv[..., 1] = np.clip(hsv[..., 1] * sat_eff, 0.0, 1.0)
        cream = _hsv_to_rgb(hsv).reshape(3)
    undertone_fresh = (0.78, 0.82, 0.68)
    undertone_spoil = (0.55, 0.48, 0.22)
    ut0 = tuple(
        undertone_fresh[i] * (1.0 - spoil_n) + undertone_spoil[i] * spoil_n
        for i in range(3)
    )
    undertone = _shift_hue_rgb(ut0, float(hue) + 18.0)
    undertone_hsv = _rgb_to_hsv(undertone.reshape(1, 1, 3))
    undertone_hsv[..., 1] = np.clip(undertone_hsv[..., 1] * sat_eff * 0.85, 0.0, 1.0)
    undertone = _hsv_to_rgb(undertone_hsv).reshape(3)
    # Mould / rot accent colors.
    mould_rgb = np.array([0.42, 0.52, 0.28])   # dull green mould
    brown_stain = np.array([0.38, 0.22, 0.10])  # oxidized brown

    depth_n = max(0.0, min(100.0, float(depth))) / 100.0
    gloss_n = max(0.0, min(100.0, float(glossiness))) / 100.0
    # Wetness boosts effective gloss; spoil dulls it a little.
    gloss_eff = np.clip(gloss_n * (0.55 + 0.70 * wet_n) * (1.0 - 0.35 * spoil_n), 0.0, 1.35)
    thick_n = max(0.0, min(100.0, float(thickness))) / 100.0
    shadow_n = max(0.0, min(100.0, float(shadow))) / 100.0
    bub_n = max(0.0, min(100.0, float(bubbles))) / 100.0
    bub_size_n = max(0.0, min(100.0, float(bubble_size))) / 100.0
    # 0 → ~0.35×, 50 → 1×, 100 → ~2.2× diameter.
    bub_size_mul = 0.35 + 1.85 * bub_size_n
    clear_n = max(0.0, min(100.0, float(clear_spots))) / 100.0
    melt_n = max(0.0, min(100.0, float(edge_melt))) / 100.0
    exp_n = max(0.0, min(200.0, float(exposure))) / 100.0
    exposure_mul = 0.28 + 0.72 * exp_n + 0.40 * max(0.0, exp_n - 1.0)
    # Spoiled milk reads a bit darker / dirtier.
    exposure_mul *= 1.0 - 0.22 * spoil_n

    src = pixels.astype(np.float64, copy=False)
    vmax = _channel_vmax(pixels)
    src_a = src[..., 3] / max(vmax, 1e-6) if src.shape[2] >= 4 else np.ones((h, w), dtype=np.float64)

    height = _luma01(pixels)
    r = max(0, int(radius))
    # Extra gooey pre-smooth so mounds read as liquid, not emboss.
    smooth_r = r if r >= 1 else 0
    if smooth_r >= 1:
        stacked = height[..., None]
        height = _separable_convolve(stacked, _gaussian_kernel_1d(smooth_r))[..., 0]
        height = np.clip(height, 0.0, 1.0)

    # Deterministic UVs for voids / mould / post-smooth bubbles.
    yy, xx = np.mgrid[0:h, 0:w]
    u = (xx + 0.5) / w
    v = (yy + 0.5) / h
    span = float(min(h, w))

    # Spoilage mottling: soft mould colonies + brown stains (procedural).
    mould_mask = np.zeros((h, w), dtype=np.float64)
    stain_mask = np.zeros((h, w), dtype=np.float64)
    if spoil_n > 1e-4:
        # Multi-octave value noise from sines — cheap, seamless-ish.
        n1 = (
            0.50 * np.sin(u * 23.7 + v * 17.1 + 1.3)
            + 0.30 * np.sin(u * 41.2 - v * 29.4 + 4.1)
            + 0.20 * np.sin(u * 67.5 + v * 53.8 + 2.7)
        )
        n2 = (
            0.55 * np.sin(u * 19.3 - v * 31.6 + 0.8)
            + 0.45 * np.sin(u * 53.1 + v * 11.9 + 5.5)
        )
        mould_mask = np.clip((n1 * 0.5 + 0.5) ** 1.8, 0.0, 1.0)
        stain_mask = np.clip((n2 * 0.5 + 0.5) ** 1.4, 0.0, 1.0)
        # Prefer mould on mid/high body; brown in troughs / creases.
        mould_mask = mould_mask * (0.35 + 0.65 * height) * spoil_n
        stain_mask = stain_mask * (0.25 + 0.75 * (1.0 - height)) * spoil_n
        m_r = max(1, min(10, int(round(1 + 0.012 * span))))
        mould_mask = _separable_convolve(mould_mask[..., None], _gaussian_kernel_1d(m_r))[..., 0]
        stain_mask = _separable_convolve(stain_mask[..., None], _gaussian_kernel_1d(m_r))[..., 0]
        mould_mask = np.clip(mould_mask, 0.0, 1.0)
        stain_mask = np.clip(stain_mask, 0.0, 1.0)

    # Soft irregular clear spots (blurry see-through puddles).
    void_mask = np.zeros((h, w), dtype=np.float64)
    if clear_n > 1e-4:
        n_void = max(1, min(20, int(round(3 + 12 * clear_n * (span / 512.0) ** 0.35))))
        rng_v = np.random.default_rng(0x564F4944)  # "VOID"
        for _ in range(n_void):
            cx = float(rng_v.uniform(0.08, 0.92))
            cy = float(rng_v.uniform(0.08, 0.92))
            rad = float(rng_v.uniform(0.06, 0.18)) * (0.6 + 0.8 * clear_n)
            sx = float(rng_v.uniform(0.55, 1.55))
            sy = float(rng_v.uniform(0.55, 1.55))
            ang = float(rng_v.uniform(0.0, math.pi))
            ca, sa = math.cos(ang), math.sin(ang)
            rx = rad * max(sx, sy) * 1.25
            x0 = max(0, int((cx - rx) * w))
            x1 = min(w, int(math.ceil((cx + rx) * w)) + 1)
            y0 = max(0, int((cy - rx) * h))
            y1 = min(h, int(math.ceil((cy + rx) * h)) + 1)
            if x1 <= x0 or y1 <= y0:
                continue
            uu = u[y0:y1, x0:x1]
            vv = v[y0:y1, x0:x1]
            dx0 = (uu - cx) / max(rad * sx, 1e-6)
            dy0 = (vv - cy) / max(rad * sy, 1e-6)
            dx = ca * dx0 + sa * dy0
            dy = -sa * dx0 + ca * dy0
            d2 = dx * dx + dy * dy
            blob = np.clip(1.0 - d2, 0.0, 1.0) ** 2.2
            patch = void_mask[y0:y1, x0:x1]
            np.maximum(patch, blob * clear_n, out=patch)
        wobble = 0.55 + 0.45 * np.sin(u * 17.3 + v * 11.7) * np.sin(u * 7.1 - v * 13.9)
        void_mask = np.clip(void_mask * wobble, 0.0, 1.0)
        void_r = max(1, min(24, int(round(2 + 0.028 * span * clear_n))))
        void_mask = _separable_convolve(void_mask[..., None], _gaussian_kernel_1d(void_r))[..., 0]
        void_mask = np.clip(void_mask, 0.0, 1.0)

    gx, gy = _sobel_gradients(height)
    slope = 0.22 + 1.7 * depth_n
    nx = -gx * slope
    ny = gy * slope
    nz = np.full_like(height, 0.58 + 0.28 * (1.0 - depth_n))
    inv = 1.0 / np.maximum(1e-5, np.sqrt(nx * nx + ny * ny + nz * nz))
    nx, ny, nz = nx * inv, ny * inv, nz * inv

    lx, ly, lz = normalize_light(*light_dir)
    ndotl = np.clip(nx * lx + ny * ly + nz * lz, 0.0, 1.0)

    hx, hy, hz = lx, ly, lz + 1.0
    invh = 1.0 / math.sqrt(hx * hx + hy * hy + hz * hz)
    hx, hy, hz = hx * invh, hy * invh, hz * invh
    ndoth = np.clip(nx * hx + ny * hy + nz * hz, 0.0, 1.0)
    # Wet → tighter, hotter highlights; spoil → slightly softer/duller.
    spec_pow = 2.5 + 28.0 * (min(gloss_eff, 1.0) ** 1.2) + 36.0 * wet_n
    spec = ndoth ** spec_pow
    bloom = ndoth ** (1.2 + 5.0 * min(gloss_eff, 1.0) + 4.0 * wet_n)

    edge = np.clip(1.0 - nz, 0.0, 1.0)
    thin = np.clip((1.0 - height) * (0.35 + 0.65 * edge), 0.0, 1.0)
    thin = thin * (1.0 - 0.55 * thick_n)
    # Wet surfaces look a touch more translucent in thin areas.
    thin = np.clip(thin * (1.0 + 0.35 * wet_n), 0.0, 1.0)
    albedo = cream * (1.0 - thin[..., None]) + undertone * thin[..., None]
    if spoil_n > 1e-4:
        albedo = (
            albedo * (1.0 - 0.75 * mould_mask[..., None])
            + mould_rgb * (0.75 * mould_mask[..., None])
        )
        albedo = (
            albedo * (1.0 - 0.80 * stain_mask[..., None])
            + brown_stain * (0.80 * stain_mask[..., None])
        )

    wrap = np.clip((ndotl + 0.35) / 1.35, 0.0, 1.0)
    sss = (0.15 + 0.28 * (1.0 - thick_n)) * (1.0 - wrap) * height
    warm_sss = np.array([1.05, 0.95, 0.78]) * (1.0 - 0.35 * spoil_n) + np.array(
        [0.95, 0.75, 0.45]
    ) * (0.35 * spoil_n)

    ambient = 0.22 + 0.14 * thick_n
    diffuse = (0.38 + 0.42 * depth_n) * wrap
    rim = edge * ndotl * (0.05 + 0.14 * min(gloss_eff, 1.0) + 0.10 * wet_n)
    shade = ambient + diffuse + rim

    rgb = albedo * shade[..., None]
    rgb = rgb + albedo * warm_sss * sss[..., None]

    pearl = 0.82 * np.array([1.0, 1.0, 1.0]) + 0.18 * cream
    gloss_boost = 0.18 + 0.72 * min(gloss_eff, 1.0) + 0.35 * wet_n
    rgb = rgb + pearl * (gloss_boost * (0.38 * spec + 0.22 * bloom))[..., None]
    fresnel = edge * edge
    rgb = rgb + pearl * (fresnel * (0.04 + 0.12 * min(gloss_eff, 1.0) + 0.14 * wet_n))[..., None]

    form_shadow = (1.0 - wrap) * (0.14 + 0.52 * shadow_n) * np.clip(
        1.0 - height * 0.45, 0.0, 1.0
    )
    # Wet troughs go darker (pooled liquid).
    wet_trough = (1.0 - height) * wet_n * (0.12 + 0.28 * (1.0 - wrap))
    shadow_mul = 1.0 - form_shadow - wet_trough
    shadow_mul = np.clip(shadow_mul, 0.15, 1.0)
    cream_shadow = 0.55 + 0.45 * (undertone / np.maximum(np.max(undertone), 1e-6))
    rgb = rgb * (shadow_mul[..., None] * cream_shadow)
    rgb = rgb * exposure_mul

    # Soft whole-field blur for viscous smoothness (scales with Smooth).
    # Bubbles are composited after this so Smooth does not flatten them.
    goo_r = max(0, min(10, int(round(smooth_r * 0.40))))
    if goo_r >= 1:
        rgb = _separable_convolve(rgb, _gaussian_kernel_1d(goo_r))

    # Blurry see-through voids: heavily soften RGB and punch alpha later.
    if clear_n > 1e-4 and void_mask.max() > 1e-4:
        void_blur_r = max(2, min(12, int(round(2 + 0.030 * span * clear_n))))
        rgb_blur = _separable_convolve(rgb, _gaussian_kernel_1d(void_blur_r))
        glass = 0.55 * rgb_blur + 0.45 * cream * (0.75 + 0.25 * height[..., None])
        vm = void_mask[..., None]
        rgb = rgb * (1.0 - vm) + glass * vm

    # --- Bubble placement via brush bubble mode (before edge melt) ---
    placed_bubs: list[tuple[float, float, float, float]] = []
    bub_protect = np.zeros((h, w), dtype=np.float64)
    if bub_n > 1e-4:
        # Local color uniformity — luma variance only (faster than full RGB + Sobel).
        homo_r = max(1, min(8, int(round(1 + 0.012 * span))))
        src_luma = _luma01(pixels)
        k_homo = _gaussian_kernel_1d(homo_r)
        mean_l = _separable_convolve(src_luma[..., None], k_homo)[..., 0]
        mean2_l = _separable_convolve((src_luma * src_luma)[..., None], k_homo)[..., 0]
        var_l = np.maximum(mean2_l - mean_l * mean_l, 0.0)
        homo = (var_l < 0.0035) & (src_a > 0.35)
        margin = max(2, int(0.02 * span))
        homo[:margin, :] = False
        homo[-margin:, :] = False
        homo[:, :margin] = False
        homo[:, -margin:] = False
        cand_y, cand_x = np.nonzero(homo)

        if cand_y.size > 0:
            rng = np.random.default_rng(0x4D494C4B)  # "MILK"
            dens = bub_n * 100.0
            n_sites = max(1, min(20, int(round(3 + 14 * bub_n * (span / 512.0) ** 0.35))))
            picks = rng.choice(cand_y.size, size=min(n_sites * 3, cand_y.size), replace=False)
            max_bubbles = max(1, min(48, int(round(8 + 36 * bub_n * (span / 512.0) ** 0.35))))
            # Brush-mode cluster radius: same span-relative sizing as milk Bubble Size.
            site_rad = max(
                1.5,
                float(0.010 * span) * (0.75 + 0.50 * bub_n) * bub_size_mul,
            )

            def _homo_ok(cx_px: float, cy_px: float, rad_px: float) -> bool:
                ix = int(round(cx_px))
                iy = int(round(cy_px))
                if not (0 <= ix < w and 0 <= iy < h) or not homo[iy, ix]:
                    return False
                pad = int(math.ceil(rad_px * 1.15)) + 1
                x0 = max(0, ix - pad)
                x1 = min(w, ix + pad + 1)
                y0 = max(0, iy - pad)
                y1 = min(h, iy + pad + 1)
                if x1 <= x0 or y1 <= y0:
                    return False
                xs = xx[y0:y1, x0:x1].astype(np.float64) + 0.5
                ys = yy[y0:y1, x0:x1].astype(np.float64) + 0.5
                d2 = ((xs - cx_px) / rad_px) ** 2 + ((ys - cy_px) / rad_px) ** 2
                inside = d2 < 1.0
                if not np.any(inside):
                    return False
                if float(homo[y0:y1, x0:x1][inside].mean()) < 0.72:
                    return False
                if float(np.abs(src_luma[y0:y1, x0:x1][inside] - src_luma[iy, ix]).mean()) > 0.06:
                    return False
                return True

            for pi in picks:
                if len(placed_bubs) >= max_bubbles:
                    break
                sy = int(cand_y[pi])
                sx = int(cand_x[pi])
                # Same cluster layout as the Bubbles brush mode.
                cluster = place_bubble_cluster(
                    sx + 0.5,
                    sy + 0.5,
                    site_rad,
                    rng,
                    density=dens,
                    force=True,
                )
                for cx_px, cy_px, rad_px, strength in cluster:
                    if len(placed_bubs) >= max_bubbles:
                        break
                    if not _homo_ok(cx_px, cy_px, rad_px):
                        continue
                    placed_bubs.append(
                        (cx_px, cy_px, rad_px, float(strength) * (0.55 + 0.45 * bub_n))
                    )

        if placed_bubs:
            for cx_px, cy_px, rad_px, strength in placed_bubs:
                bx0, by0, bx1, by1 = bubble_influence_bbox(cx_px, cy_px, rad_px)
                x0, y0 = max(0, bx0), max(0, by0)
                x1, y1 = min(w, bx1), min(h, by1)
                if x1 <= x0 or y1 <= y0:
                    continue
                xs = xx[y0:y1, x0:x1].astype(np.float64) + 0.5
                ys = yy[y0:y1, x0:x1].astype(np.float64) + 0.5
                d2 = ((xs - cx_px) / max(rad_px, 1e-6)) ** 2 + (
                    (ys - cy_px) / max(rad_px, 1e-6)
                ) ** 2
                disk = np.clip(1.0 - d2, 0.0, 1.0) ** 0.85
                patch = bub_protect[y0:y1, x0:x1]
                np.maximum(patch, disk * min(1.0, strength + 0.15), out=patch)

    # Liquid edge melt — soften content silhouettes only (not canvas borders).
    edge_fade = np.ones((h, w), dtype=np.float64)
    if melt_n > 1e-4:
        sil_r = max(1, min(16, int(round(1 + 0.035 * span * melt_n))))
        if src_a.min() < 0.999:
            sil = src_a
        else:
            sil = np.clip(height * 1.2, 0.0, 1.0)
        edge_fade = _separable_convolve(sil[..., None], _gaussian_kernel_1d(sil_r))[..., 0]
        edge_fade = np.clip(edge_fade, 0.0, 1.0)
        melt_blur_r = max(1, min(10, int(round(1 + 0.025 * span * melt_n))))
        rgb_edge = _separable_convolve(rgb, _gaussian_kernel_1d(melt_blur_r))
        melt_w = (1.0 - edge_fade) * melt_n * (1.0 - bub_protect)
        rgb = rgb * (1.0 - melt_w[..., None]) + rgb_edge * melt_w[..., None]

    # Extra spoil pass after blur so mould stays blotchy on the gooey surface.
    if spoil_n > 1e-4:
        spoil_w = np.clip(0.55 * mould_mask + 0.70 * stain_mask, 0.0, 1.0)
        spoil_col = (
            mould_rgb * mould_mask[..., None] + brown_stain * stain_mask[..., None]
        )
        spoil_sum = np.maximum(
            mould_mask[..., None] + stain_mask[..., None], 1e-6
        )
        spoil_col = spoil_col / spoil_sum
        rgb = rgb * (1.0 - 0.65 * spoil_w[..., None]) + spoil_col * (
            0.65 * spoil_w[..., None]
        )
        # Yellow cast over the whole body as it turns.
        yellow_cast = np.array([1.06, 0.96, 0.72])
        rgb = rgb * (1.0 - 0.25 * spoil_n) + rgb * yellow_cast * (0.25 * spoil_n)

    # Alpha for the smoothed milk body (voids / melt). Bubbles reinforce opacity later.
    alpha = src_a.copy()
    alpha = alpha * (0.55 + 0.45 * thick_n + 0.45 * (1.0 - thick_n) * height)
    if clear_n > 1e-4:
        alpha = alpha * (1.0 - (0.55 + 0.40 * clear_n) * void_mask)
    if melt_n > 1e-4:
        melt_a = edge_fade ** (0.65 + 0.7 * melt_n)
        # Keep bubble disks fully opaque w.r.t. edge melt.
        alpha = alpha * (melt_a * (1.0 - bub_protect) + bub_protect)
    alpha = np.clip(alpha, 0.0, 1.0)

    a_blur_r = max(0, min(8, int(round(1 + 0.35 * smooth_r + 1.0 * melt_n + 1.0 * clear_n))))
    if a_blur_r >= 1:
        alpha_pre = alpha
        alpha = _separable_convolve(alpha[..., None], _gaussian_kernel_1d(a_blur_r))[..., 0]
        alpha = np.clip(alpha, 0.0, 1.0)
        # Do not let alpha blur soften bubble disks.
        if bub_protect.max() > 1e-4:
            alpha = alpha * (1.0 - bub_protect) + alpha_pre * bub_protect

    # --- Bubble shading: same renderer as the Bubbles brush ---
    out = src.copy()
    out[..., :3] = np.clip(rgb * vmax, 0.0, vmax)
    out[..., 3] = np.clip(alpha * vmax, 0.0, vmax)
    if placed_bubs:
        # Tint bubbles with the milk cream albedo (incl. spoil / hue).
        cr = [float(np.clip(c, 0.0, 1.0)) * vmax for c in cream]
        bub_color = (cr[0], cr[1], cr[2], float(vmax))
        # Slightly translucent over the milk body; wetness boosts presence.
        bub_op = float(np.clip(0.55 + 0.35 * bub_n + 0.15 * wet_n, 0.35, 1.0))
        render_bubbles_list(out, placed_bubs, bub_color, opacity=bub_op, light_dir=light_dir)
    return out.astype(pixels.dtype, copy=False)


def offset_wrap(pixels: np.ndarray, dx: int = 0, dy: int = 0) -> np.ndarray:
    """Wrap-around shift (Photoshop Offset / seamless tile move).

    Positive ``dx`` moves content right; positive ``dy`` moves content down.
    Pixels that leave one edge re-enter on the opposite edge.
    """
    ox = int(dx)
    oy = int(dy)
    if ox == 0 and oy == 0:
        return np.ascontiguousarray(pixels.copy())
    out = pixels
    if oy:
        out = np.roll(out, oy, axis=0)
    if ox:
        out = np.roll(out, ox, axis=1)
    return np.ascontiguousarray(out)


def blend_effect(
    source: np.ndarray,
    effected: np.ndarray,
    selection_mask: np.ndarray | None,
) -> np.ndarray:
    """Compose effected pixels over source; optional selection (nonzero = apply)."""
    if selection_mask is None or not selection_mask.any():
        return np.ascontiguousarray(effected)
    out = source.copy()
    m = selection_mask > 0
    out[m] = effected[m]
    return out
