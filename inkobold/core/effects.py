"""CPU image effects for the Effects menu (NumPy, no extra deps)."""

from __future__ import annotations

import math

import numpy as np

LIQUIFY_MODES = ("Swirl", "Pinch", "Bulge")
LIQUIFY_BRUSH_MODES = ("Push", "Swirl", "Pinch", "Bulge")
DITHER_MODES = ("Floyd–Steinberg", "Ordered", "Threshold")

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


def _bilinear_sample(src: np.ndarray, y: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Sample HxWxC at float coords with edge clamping."""
    h, w, _c = src.shape
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

    src_y = yy.copy()
    src_x = xx.copy()

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

    # Sample from a copy so in-place writes do not feed back within the stamp.
    region = pixels[y0:y1, x0:x1]
    # Map absolute sample coords into the local region, then use full-image sampling
    # for edge-correct bilinear (clamped) via the shared helper.
    sampled = _bilinear_sample(pixels, src_y, src_x)
    active = falloff > 1e-6
    out = region.astype(np.float64, copy=True)
    out[active] = sampled[active]
    lo, hi = 0, np.iinfo(pixels.dtype).max if np.issubdtype(pixels.dtype, np.integer) else 1
    pixels[y0:y1, x0:x1] = np.clip(out, lo, hi).astype(pixels.dtype, copy=False)


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
