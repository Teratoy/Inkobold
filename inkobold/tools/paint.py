"""Shared pixel painting helpers (CPU, uploaded to GPU textures)."""

from __future__ import annotations

import math

import numpy as np
from PIL import Image


def _max_v(pixels: np.ndarray) -> float:
    return 65535.0 if pixels.dtype == np.uint16 else 255.0


def stamp_disk(
    pixels: np.ndarray,
    x: float,
    y: float,
    radius: float,
    color: tuple[int, int, int, int],
    erase: bool = False,
    mask: np.ndarray | None = None,
    key_color: tuple[int, int, int, int] | None = None,
    threshold: int | None = None,
    replace: bool = False,
    opacity: float = 1.0,
) -> None:
    h, w = pixels.shape[:2]
    r = max(0.5, radius)
    x0 = max(0, int(x - r - 1))
    y0 = max(0, int(y - r - 1))
    x1 = min(w, int(x + r + 2))
    y1 = min(h, int(y + r + 2))
    if x0 >= x1 or y0 >= y1:
        return
    yy, xx = np.ogrid[y0:y1, x0:x1]
    dist = np.sqrt((xx + 0.5 - x) ** 2 + (yy + 0.5 - y) ** 2)
    cov = np.clip(1.0 - (dist - (r - 0.5)), 0.0, 1.0)
    if mask is not None:
        cov = cov * (mask[y0:y1, x0:x1] > 0).astype(np.float32)
    op = float(np.clip(opacity, 0.0, 1.0))
    if op <= 0.0 or not np.any(cov):
        return
    patch = pixels[y0:y1, x0:x1].astype(np.float32)
    max_v = _max_v(pixels)

    # Optional color match weight (0..1) for thresholded erase / replace
    if key_color is not None and threshold is not None:
        tol = max(0, int(threshold))
        key = np.array(key_color, dtype=np.float32)
        if tol >= max_v:
            match = np.ones(cov.shape, dtype=np.float32)
        elif tol <= 0:
            match = (np.max(np.abs(patch - key), axis=2) < 0.5).astype(np.float32)
        else:
            diff = np.max(np.abs(patch - key), axis=2)
            match = np.clip(1.0 - diff / float(tol), 0.0, 1.0)
        strength = cov * match * op
    else:
        strength = cov * op

    if erase:
        patch[..., 3] *= 1.0 - strength
    elif replace:
        src = np.array(color, dtype=np.float32)
        a = (src[3] / max_v) * strength[..., None]
        patch[..., :3] = src[:3] * a + patch[..., :3] * (1.0 - a)
        patch[..., 3:4] = np.maximum(patch[..., 3:4], src[3] * a)
    else:
        src = np.array(color, dtype=np.float32)
        a = (src[3] / max_v) * strength[..., None]
        patch[..., :3] = src[:3] * a + patch[..., :3] * (1.0 - a)
        patch[..., 3:4] = src[3] * a + patch[..., 3:4] * (1.0 - a)
    pixels[y0:y1, x0:x1] = np.clip(patch, 0, _max_v(pixels)).astype(pixels.dtype)


def stroke_segment(
    pixels: np.ndarray,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    radius: float,
    color: tuple[int, int, int, int],
    erase: bool = False,
    mask: np.ndarray | None = None,
    key_color: tuple[int, int, int, int] | None = None,
    threshold: int | None = None,
    replace: bool = False,
    opacity: float = 1.0,
) -> None:
    dist = float(np.hypot(x1 - x0, y1 - y0))
    steps = max(1, int(dist / max(0.5, radius * 0.35)))
    for i in range(steps + 1):
        t = i / steps
        stamp_disk(
            pixels,
            x0 + (x1 - x0) * t,
            y0 + (y1 - y0) * t,
            radius,
            color,
            erase=erase,
            mask=mask,
            key_color=key_color,
            threshold=threshold,
            replace=replace,
            opacity=opacity,
        )


def stroke_quadratic(
    pixels: np.ndarray,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: float,
    color: tuple[int, int, int, int],
    erase: bool = False,
    mask: np.ndarray | None = None,
    key_color: tuple[int, int, int, int] | None = None,
    threshold: int | None = None,
    replace: bool = False,
    opacity: float = 1.0,
) -> None:
    """Stamp along a quadratic Bézier (P0 → control P1 → P2)."""
    # Chord + control legs give a cheap upper bound on arc length.
    est = float(
        np.hypot(x1 - x0, y1 - y0)
        + np.hypot(x2 - x1, y2 - y1)
    )
    steps = max(1, int(est / max(0.5, radius * 0.35)))
    for i in range(steps + 1):
        t = i / steps
        u = 1.0 - t
        stamp_disk(
            pixels,
            u * u * x0 + 2.0 * u * t * x1 + t * t * x2,
            u * u * y0 + 2.0 * u * t * y1 + t * t * y2,
            radius,
            color,
            erase=erase,
            mask=mask,
            key_color=key_color,
            threshold=threshold,
            replace=replace,
            opacity=opacity,
        )


def stroke_circular_arc(
    pixels: np.ndarray,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    degrees: float,
    radius: float,
    color: tuple[int, int, int, int],
    erase: bool = False,
    mask: np.ndarray | None = None,
    key_color: tuple[int, int, int, int] | None = None,
    threshold: int | None = None,
    replace: bool = False,
    opacity: float = 1.0,
    flip: bool = False,
) -> None:
    """Stamp a circular arc whose chord is (x0,y0)→(x1,y1) and central angle is `degrees`.

    180° with the drag as diameter yields a semicircle. `flip` mirrors the bulge
    across the chord.
    """
    dx = x1 - x0
    dy = y1 - y0
    chord = math.hypot(dx, dy)
    if chord < 1e-6:
        stamp_disk(
            pixels, x0, y0, radius, color,
            erase=erase, mask=mask, key_color=key_color,
            threshold=threshold, replace=replace, opacity=opacity,
        )
        return

    deg = abs(float(degrees))
    if deg < 1e-3:
        stroke_segment(
            pixels, x0, y0, x1, y1, radius, color,
            erase=erase, mask=mask, key_color=key_color,
            threshold=threshold, replace=replace, opacity=opacity,
        )
        return
    # Full circle is ambiguous for a chord; clamp just under 360°.
    deg = min(deg, 359.0)
    theta = math.radians(deg)
    half = theta * 0.5
    sin_h = math.sin(half)
    if abs(sin_h) < 1e-8:
        stroke_segment(
            pixels, x0, y0, x1, y1, radius, color,
            erase=erase, mask=mask, key_color=key_color,
            threshold=threshold, replace=replace, opacity=opacity,
        )
        return

    r_circ = chord / (2.0 * sin_h)
    nx = -dy / chord
    ny = dx / chord
    if flip:
        nx, ny = -nx, -ny
    mid_x = (x0 + x1) * 0.5
    mid_y = (y0 + y1) * 0.5
    # Center sits opposite the bulge for minor arcs (θ < 180°).
    cx = mid_x - nx * r_circ * math.cos(half)
    cy = mid_y - ny * r_circ * math.cos(half)

    a0 = math.atan2(y0 - cy, x0 - cx)
    best_sweep = theta
    best_side = -1.0
    for sign in (1.0, -1.0):
        sweep = sign * theta
        am = a0 + sweep * 0.5
        mx = cx + r_circ * math.cos(am)
        my = cy + r_circ * math.sin(am)
        side = (mx - mid_x) * nx + (my - mid_y) * ny
        if side > best_side:
            best_side = side
            best_sweep = sweep

    arc_len = abs(best_sweep) * r_circ
    steps = max(1, int(arc_len / max(0.5, radius * 0.35)))
    for i in range(steps + 1):
        t = i / steps
        ang = a0 + best_sweep * t
        stamp_disk(
            pixels,
            cx + r_circ * math.cos(ang),
            cy + r_circ * math.sin(ang),
            radius,
            color,
            erase=erase,
            mask=mask,
            key_color=key_color,
            threshold=threshold,
            replace=replace,
            opacity=opacity,
        )


def stroke_circle(
    pixels: np.ndarray,
    cx: float,
    cy: float,
    circle_radius: float,
    radius: float,
    color: tuple[int, int, int, int],
    erase: bool = False,
    mask: np.ndarray | None = None,
    key_color: tuple[int, int, int, int] | None = None,
    threshold: int | None = None,
    replace: bool = False,
    opacity: float = 1.0,
) -> None:
    """Stamp a full circle centered at (cx, cy) with geometric radius `circle_radius`."""
    r_circ = max(0.0, float(circle_radius))
    if r_circ < 1e-6:
        stamp_disk(
            pixels, cx, cy, radius, color,
            erase=erase, mask=mask, key_color=key_color,
            threshold=threshold, replace=replace, opacity=opacity,
        )
        return
    circ = 2.0 * math.pi * r_circ
    steps = max(1, int(circ / max(0.5, radius * 0.35)))
    for i in range(steps + 1):
        ang = (2.0 * math.pi * i) / steps
        stamp_disk(
            pixels,
            cx + r_circ * math.cos(ang),
            cy + r_circ * math.sin(ang),
            radius,
            color,
            erase=erase,
            mask=mask,
            key_color=key_color,
            threshold=threshold,
            replace=replace,
            opacity=opacity,
        )


def stroke_segment_tapered(
    pixels: np.ndarray,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    radius0: float,
    radius1: float,
    color: tuple[int, int, int, int],
    mask: np.ndarray | None = None,
) -> None:
    """Stamp a segment while linearly interpolating stamp radius (pressure taper)."""
    r0 = max(0.5, float(radius0))
    r1 = max(0.5, float(radius1))
    dist = float(np.hypot(x1 - x0, y1 - y0))
    step = max(0.5, min(r0, r1) * 0.35)
    steps = max(1, int(dist / step))
    for i in range(steps + 1):
        t = i / steps
        stamp_disk(
            pixels,
            x0 + (x1 - x0) * t,
            y0 + (y1 - y0) * t,
            r0 + (r1 - r0) * t,
            color,
            mask=mask,
        )


def _span_around(row: np.ndarray, x: int) -> tuple[int, int] | None:
    """Inclusive [left, right] of the contiguous True run containing x."""
    if not row[x]:
        return None
    left_hits = np.flatnonzero(~row[:x])
    left = 0 if left_hits.size == 0 else int(left_hits[-1]) + 1
    right_hits = np.flatnonzero(~row[x + 1 :])
    right = row.size - 1 if right_hits.size == 0 else int(x + 1 + right_hits[0]) - 1
    return left, right


def flood_fill_region(
    pixels: np.ndarray,
    sx: int,
    sy: int,
    tolerance: int = 32,
    mask: np.ndarray | None = None,
    key_color: tuple[int, int, int, int] | None = None,
    rgb_only: bool = False,
) -> np.ndarray | None:
    """Return a boolean mask of the connected fill region, or None if empty.

    When *key_color* is set, matching uses that color instead of the seed pixel.
    With *rgb_only*, alpha is ignored (Chebyshev distance on RGB).
    """
    h, w = pixels.shape[:2]
    if not (0 <= sx < w and 0 <= sy < h):
        return None
    if mask is not None and mask[sy, sx] == 0:
        return None

    tol = int(max(0, tolerance))
    max_v = int(_max_v(pixels))
    if key_color is not None:
        target = np.asarray(key_color[:4], dtype=np.int32)
    else:
        target = pixels[sy, sx].astype(np.int32)

    if rgb_only:
        key = target[:3]
        diff = np.max(np.abs(pixels[..., :3].astype(np.int32) - key), axis=2)
        can = diff <= tol
    else:
        lo = np.clip(target - tol, 0, max_v)
        hi = np.clip(target + tol, 0, max_v)
        can = (
            (pixels[..., 0] >= lo[0])
            & (pixels[..., 0] <= hi[0])
            & (pixels[..., 1] >= lo[1])
            & (pixels[..., 1] <= hi[1])
            & (pixels[..., 2] >= lo[2])
            & (pixels[..., 2] <= hi[2])
            & (pixels[..., 3] >= lo[3])
            & (pixels[..., 3] <= hi[3])
        )
    if mask is not None:
        can &= mask.astype(bool, copy=False)
    if not can[sy, sx]:
        return None

    if can.all():
        return np.ones((h, w), dtype=bool)

    region = np.zeros((h, w), dtype=bool)
    work = can.copy()
    stack: list[tuple[int, int]] = [(sy, sx)]
    while stack:
        y, x = stack.pop()
        span = _span_around(work[y], x)
        if span is None:
            continue
        left, right = span
        work[y, left : right + 1] = False
        region[y, left : right + 1] = True

        for ny in (y - 1, y + 1):
            if ny < 0 or ny >= h:
                continue
            nrow = work[ny]
            segment = nrow[left : right + 1]
            if not segment.any():
                continue
            padded = np.empty(segment.size + 2, dtype=bool)
            padded[0] = False
            padded[-1] = False
            padded[1:-1] = segment
            starts = np.flatnonzero(~padded[:-1] & padded[1:])
            for s in starts:
                stack.append((ny, left + int(s)))
    return region


def flood_fill(
    pixels: np.ndarray,
    sx: int,
    sy: int,
    color: tuple[int, int, int, int],
    tolerance: int = 32,
    mask: np.ndarray | None = None,
) -> None:
    """Scanline flood fill; blends when color alpha is below opaque."""
    region = flood_fill_region(pixels, sx, sy, tolerance=tolerance, mask=mask)
    if region is None:
        return
    max_v = _max_v(pixels)
    src = np.asarray(color, dtype=np.float32)
    a = float(src[3]) / max_v
    if a >= 0.999:
        pixels[region] = np.clip(src, 0, max_v).astype(pixels.dtype)
        return
    if a <= 0.0:
        return
    dest = pixels[region].astype(np.float32)
    out = dest.copy()
    out[..., :3] = src[:3] * a + dest[..., :3] * (1.0 - a)
    out[..., 3] = src[3] + dest[..., 3] * (1.0 - a)
    pixels[region] = np.clip(out, 0, _max_v(pixels)).astype(pixels.dtype)


def replace_matching_colors(
    pixels: np.ndarray,
    sx: int,
    sy: int,
    color: tuple[int, int, int, int],
    tolerance: int = 32,
    mask: np.ndarray | None = None,
) -> None:
    """Replace every pixel matching the seed (within *tolerance*) with *color*.

    Unlike flood fill, this is global — all matching pixels on the layer, not
    only the connected region. Respects an optional selection *mask*.
    """
    h, w = pixels.shape[:2]
    if not (0 <= sx < w and 0 <= sy < h):
        return
    if mask is not None and mask[sy, sx] == 0:
        return

    tol = int(max(0, tolerance))
    max_v = int(_max_v(pixels))
    target = pixels[sy, sx].astype(np.int32)
    lo = np.clip(target - tol, 0, max_v)
    hi = np.clip(target + tol, 0, max_v)
    region = (
        (pixels[..., 0] >= lo[0])
        & (pixels[..., 0] <= hi[0])
        & (pixels[..., 1] >= lo[1])
        & (pixels[..., 1] <= hi[1])
        & (pixels[..., 2] >= lo[2])
        & (pixels[..., 2] <= hi[2])
        & (pixels[..., 3] >= lo[3])
        & (pixels[..., 3] <= hi[3])
    )
    if mask is not None:
        region &= mask.astype(bool, copy=False)
    if not np.any(region):
        return

    src = np.asarray(color, dtype=np.float32)
    a = float(src[3]) / float(max_v)
    if a >= 0.999:
        pixels[region] = np.clip(src, 0, max_v).astype(pixels.dtype)
        return
    if a <= 0.0:
        return
    dest = pixels[region].astype(np.float32)
    out = dest.copy()
    out[..., :3] = src[:3] * a + dest[..., :3] * (1.0 - a)
    out[..., 3] = src[3] + dest[..., 3] * (1.0 - a)
    pixels[region] = np.clip(out, 0, max_v).astype(pixels.dtype)


def flood_erase(
    pixels: np.ndarray,
    sx: int,
    sy: int,
    key_color: tuple[int, int, int, int],
    tolerance: int = 32,
    mask: np.ndarray | None = None,
    opacity: float = 1.0,
) -> None:
    """Flood-erase connected pixels matching *key_color* (RGB + threshold)."""
    region = flood_fill_region(
        pixels,
        sx,
        sy,
        tolerance=tolerance,
        mask=mask,
        key_color=key_color,
        rgb_only=True,
    )
    if region is None:
        return
    op = float(np.clip(opacity, 0.0, 1.0))
    if op <= 0.0:
        return
    if op >= 0.999:
        pixels[..., 3] = np.where(region, 0, pixels[..., 3])
        return
    alpha = pixels[..., 3].astype(np.float32)
    alpha[region] *= 1.0 - op
    pixels[..., 3] = np.clip(alpha, 0, _max_v(pixels)).astype(pixels.dtype)


def erase_matching_colors(
    pixels: np.ndarray,
    key_color: tuple[int, int, int, int],
    tolerance: int = 32,
    mask: np.ndarray | None = None,
    opacity: float = 1.0,
) -> None:
    """Erase every pixel matching *key_color* (RGB + threshold) on the layer.

    Global counterpart to :func:`flood_erase` — all matches, not only a
    connected region. Respects an optional selection *mask*.
    """
    op = float(np.clip(opacity, 0.0, 1.0))
    if op <= 0.0:
        return
    tol = int(max(0, tolerance))
    max_v = int(_max_v(pixels))
    key = np.asarray(key_color[:3], dtype=np.int32)
    lo = np.clip(key - tol, 0, max_v)
    hi = np.clip(key + tol, 0, max_v)
    region = (
        (pixels[..., 0] >= lo[0])
        & (pixels[..., 0] <= hi[0])
        & (pixels[..., 1] >= lo[1])
        & (pixels[..., 1] <= hi[1])
        & (pixels[..., 2] >= lo[2])
        & (pixels[..., 2] <= hi[2])
    )
    if mask is not None:
        region &= mask.astype(bool, copy=False)
    if not np.any(region):
        return
    if op >= 0.999:
        pixels[..., 3] = np.where(region, 0, pixels[..., 3])
        return
    alpha = pixels[..., 3].astype(np.float32)
    alpha[region] *= 1.0 - op
    pixels[..., 3] = np.clip(alpha, 0, max_v).astype(pixels.dtype)


def _scaled_pattern(pattern: np.ndarray, scale: float) -> np.ndarray:
    """Resize pattern for tiling. *scale* is tile size multiplier (1 = native)."""
    s = max(0.05, float(scale))
    if abs(s - 1.0) < 1e-6:
        return np.asarray(pattern, dtype=np.uint8)
    h, w = pattern.shape[:2]
    nh = max(1, int(round(h * s)))
    nw = max(1, int(round(w * s)))
    img = Image.fromarray(np.asarray(pattern, dtype=np.uint8), mode="RGBA")
    resized = img.resize((nw, nh), Image.Resampling.LANCZOS)
    return np.asarray(resized, dtype=np.uint8)


def flood_fill_pattern(
    pixels: np.ndarray,
    sx: int,
    sy: int,
    pattern: np.ndarray,
    scale: float = 1.0,
    tolerance: int = 32,
    mask: np.ndarray | None = None,
    opacity: float = 1.0,
) -> None:
    """Flood-fill a region with a tiled RGBA pattern."""
    if pattern is None or pattern.size == 0 or pattern.ndim != 3 or pattern.shape[2] < 4:
        return
    region = flood_fill_region(pixels, sx, sy, tolerance=tolerance, mask=mask)
    if region is None:
        return
    tile = _scaled_pattern(pattern, scale)
    th, tw = tile.shape[:2]
    if th < 1 or tw < 1:
        return
    op = float(np.clip(opacity, 0.0, 1.0))
    if op <= 0.0:
        return
    ys, xs = np.nonzero(region)
    max_v = _max_v(pixels)
    src = tile[ys % th, xs % tw].astype(np.float32)
    if max_v > 255.0 and float(np.max(src)) <= 255.0:
        src = src * (max_v / 255.0)
    if op >= 0.999 and np.all(src[..., 3] >= max_v * 0.996):
        pixels[ys, xs] = np.clip(src, 0, max_v).astype(pixels.dtype)
        return
    dest = pixels[ys, xs].astype(np.float32)
    a = (src[..., 3] / max_v) * op
    out = dest.copy()
    out[..., :3] = src[..., :3] * a[..., None] + dest[..., :3] * (1.0 - a[..., None])
    out[..., 3] = src[..., 3] * op + dest[..., 3] * (1.0 - a)
    pixels[ys, xs] = np.clip(out, 0, _max_v(pixels)).astype(pixels.dtype)


def stamp_disk_3d(
    pixels: np.ndarray,
    x: float,
    y: float,
    radius: float,
    color: tuple[int, int, int, int],
    mask: np.ndarray | None = None,
    depth: float = 70.0,
    highlight: float = 55.0,
    bevel: float = 40.0,
) -> None:
    """Soft disc shaded as a lit sphere (bevel + specular highlight)."""
    h, w = pixels.shape[:2]
    r = max(1.0, radius)
    x0 = max(0, int(x - r - 1))
    y0 = max(0, int(y - r - 1))
    x1 = min(w, int(x + r + 2))
    y1 = min(h, int(y + r + 2))
    if x0 >= x1 or y0 >= y1:
        return

    yy, xx = np.mgrid[y0:y1, x0:x1]
    fx = xx + 0.5 - x
    fy = yy + 0.5 - y
    dist = np.sqrt(fx * fx + fy * fy)
    # Bevel softens the edge falloff (higher = softer rim)
    edge_soft = 0.35 + 0.9 * (max(0.0, min(100.0, bevel)) / 100.0)
    cov = np.clip(1.0 - (dist - (r - edge_soft)) / max(0.35, edge_soft), 0.0, 1.0)
    if mask is not None:
        cov = cov * (mask[y0:y1, x0:x1] > 0).astype(np.float32)
    inside = dist < r
    if not np.any(cov > 0):
        return

    # Roundness from bevel: lower bevel → flatter top (higher nz bias)
    roundness = 0.35 + 0.65 * (max(0.0, min(100.0, bevel)) / 100.0)
    nx = np.clip(fx / r, -1.0, 1.0) * roundness
    ny = np.clip(fy / r, -1.0, 1.0) * roundness
    nz = np.sqrt(np.clip(1.0 - nx * nx - ny * ny, 0.0, 1.0))

    lx, ly, lz = -0.45, -0.55, 0.70
    inv = 1.0 / np.sqrt(lx * lx + ly * ly + lz * lz)
    lx, ly, lz = lx * inv, ly * inv, lz * inv
    ndotl = np.clip(nx * lx + ny * ly + nz * lz, 0.0, 1.0)

    hx, hy, hz = lx * 0.5, ly * 0.5, (lz + 1.0) * 0.5
    invh = 1.0 / np.sqrt(hx * hx + hy * hy + hz * hz)
    spec_pow = 8.0 + 24.0 * (1.0 - max(0.0, min(100.0, bevel)) / 100.0)
    spec = np.clip(nx * hx * invh + ny * hy * invh + nz * hz * invh, 0.0, 1.0) ** spec_pow

    depth_n = max(0.0, min(100.0, depth)) / 100.0
    high_n = max(0.0, min(100.0, highlight)) / 100.0
    ambient = 1.0 - 0.75 * depth_n
    diffuse = 0.75 * depth_n
    shade = ambient + diffuse * ndotl
    max_v = _max_v(pixels)
    base = np.array(color[:3], dtype=np.float32)
    rgb = base * shade[..., None]
    rgb = rgb + (max_v - rgb) * ((0.15 + 0.75 * high_n) * spec[..., None])
    rgb = np.clip(rgb, 0, max_v)
    alpha = float(color[3]) * cov * inside.astype(np.float32)

    patch = pixels[y0:y1, x0:x1].astype(np.float32)
    a = (alpha / max_v)[..., None]
    patch[..., :3] = rgb * a + patch[..., :3] * (1.0 - a)
    patch[..., 3:4] = np.maximum(patch[..., 3:4], alpha[..., None])
    pixels[y0:y1, x0:x1] = np.clip(patch, 0, _max_v(pixels)).astype(pixels.dtype)


def stroke_segment_3d(
    pixels: np.ndarray,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    radius: float,
    color: tuple[int, int, int, int],
    mask: np.ndarray | None = None,
    depth: float = 70.0,
    highlight: float = 55.0,
    bevel: float = 40.0,
    frequency: float = 60.0,
) -> None:
    """Stamp lit spheres along a segment.

    ``frequency`` (0–100) controls stamp spacing: low = spaced beads,
    high = continuous tube. ~60 matches the former fixed spacing.
    """
    dist = float(np.hypot(x1 - x0, y1 - y0))
    freq_n = max(0.0, min(100.0, frequency)) / 100.0
    # Spacing as a fraction of radius (1.5 → sparse, 0.06 → dense)
    spacing_frac = 0.06 + 1.44 * (1.0 - freq_n) ** 2
    steps = max(1, int(dist / max(0.5, radius * spacing_frac)))
    for i in range(steps + 1):
        t = i / steps
        stamp_disk_3d(
            pixels,
            x0 + (x1 - x0) * t,
            y0 + (y1 - y0) * t,
            radius,
            color,
            mask=mask,
            depth=depth,
            highlight=highlight,
            bevel=bevel,
        )


def shade_region_3d(
    pixels: np.ndarray,
    region: np.ndarray,
    color: tuple[int, int, int, int],
    depth: float = 70.0,
    highlight: float = 55.0,
    bevel: float = 40.0,
) -> None:
    """Emboss a filled region: soft height from blur, directional light + highlight."""
    from PIL import Image, ImageFilter

    if not region.any():
        return
    h, w = region.shape
    ys, xs = np.where(region)
    y0, y1 = max(0, int(ys.min()) - 2), min(h, int(ys.max()) + 3)
    x0, x1 = max(0, int(xs.min()) - 2), min(w, int(xs.max()) + 3)
    sub = region[y0:y1, x0:x1]
    sh, sw = sub.shape

    bevel_n = max(0.0, min(100.0, bevel)) / 100.0
    depth_n = max(0.0, min(100.0, depth)) / 100.0
    high_n = max(0.0, min(100.0, highlight)) / 100.0

    # Bevel controls emboss softness (blur of height field)
    blur_r = max(1, int((0.015 + 0.08 * bevel_n) * max(sh, sw)))
    img = Image.fromarray((sub.astype(np.uint8) * 255), mode="L")
    height = np.asarray(img.filter(ImageFilter.GaussianBlur(radius=blur_r)), dtype=np.float32) / 255.0
    height = height * sub.astype(np.float32)

    gx = np.zeros_like(height)
    gy = np.zeros_like(height)
    gx[:, 1:-1] = height[:, 2:] - height[:, :-2]
    gy[1:-1, :] = height[2:, :] - height[:-2, :]
    nx = -gx
    ny = -gy
    nz = np.full_like(height, 0.35 + 0.45 * bevel_n)
    inv = 1.0 / np.maximum(1e-5, np.sqrt(nx * nx + ny * ny + nz * nz))
    nx, ny, nz = nx * inv, ny * inv, nz * inv

    lx, ly, lz = -0.50, -0.60, 0.65
    invl = 1.0 / np.sqrt(lx * lx + ly * ly + lz * lz)
    lx, ly, lz = lx * invl, ly * invl, lz * invl
    ndotl = np.clip(nx * lx + ny * ly + nz * lz, 0.0, 1.0)

    hx, hy, hz = lx, ly, lz + 1.0
    invh = 1.0 / np.sqrt(hx * hx + hy * hy + hz * hz)
    spec_pow = 10.0 + 28.0 * (1.0 - bevel_n)
    spec = np.clip(nx * hx * invh + ny * hy * invh + nz * hz * invh, 0.0, 1.0) ** spec_pow
    rim = np.clip(1.0 - height, 0.0, 1.0) * ndotl

    ambient = 1.0 - 0.88 * depth_n
    diffuse = 0.88 * depth_n
    shade = ambient + diffuse * ndotl + (0.12 + 0.32 * depth_n) * rim
    max_v = _max_v(pixels)
    base = np.array(color[:3], dtype=np.float32)
    rgb = base * shade[..., None]
    rgb = rgb + (max_v - rgb) * ((0.22 + 0.78 * high_n) * spec[..., None])
    # Soft contact shadow on the dark rim so fills read as raised
    shadow = (1.0 - ndotl) * (0.12 + 0.28 * depth_n) * np.clip(1.0 - height * 0.65, 0.0, 1.0)
    rgb = rgb * (1.0 - shadow[..., None])
    rgb = np.clip(rgb, 0, max_v)

    alpha = float(color[3])
    dest = pixels[y0:y1, x0:x1].astype(np.float32)
    m = sub
    a = alpha / max_v
    if a >= 0.999:
        dest[..., 0][m] = rgb[..., 0][m]
        dest[..., 1][m] = rgb[..., 1][m]
        dest[..., 2][m] = rgb[..., 2][m]
        dest[..., 3][m] = alpha
    elif a > 0.0:
        dest[..., 0][m] = rgb[..., 0][m] * a + dest[..., 0][m] * (1.0 - a)
        dest[..., 1][m] = rgb[..., 1][m] * a + dest[..., 1][m] * (1.0 - a)
        dest[..., 2][m] = rgb[..., 2][m] * a + dest[..., 2][m] * (1.0 - a)
        dest[..., 3][m] = alpha + dest[..., 3][m] * (1.0 - a)
    pixels[y0:y1, x0:x1] = np.clip(dest, 0, _max_v(pixels)).astype(pixels.dtype)


def shade_region_addiction(
    pixels: np.ndarray,
    region: np.ndarray,
    color: tuple[int, int, int, int],
    depth: float = 70.0,
    highlight: float = 55.0,
    bevel: float = 40.0,
) -> None:
    """Deep emboss with multi-scale height, lobed ridges, and dual specular."""
    from PIL import Image, ImageFilter

    if not region.any():
        return
    h, w = region.shape
    ys, xs = np.where(region)
    pad = 4
    y0, y1 = max(0, int(ys.min()) - pad), min(h, int(ys.max()) + pad + 1)
    x0, x1 = max(0, int(xs.min()) - pad), min(w, int(xs.max()) + pad + 1)
    sub = region[y0:y1, x0:x1]
    sh, sw = sub.shape
    mask_f = sub.astype(np.float32)

    bevel_n = max(0.0, min(100.0, bevel)) / 100.0
    # Addiction pushes depth/highlight harder than classic for the same dial
    depth_n = min(1.0, (max(0.0, min(100.0, depth)) / 100.0) * 1.18)
    high_n = min(1.0, (max(0.0, min(100.0, highlight)) / 100.0) * 1.22)

    extent = max(sh, sw)
    blur_coarse = max(2, int((0.05 + 0.14 * bevel_n) * extent))
    blur_mid = max(1, int((0.02 + 0.07 * bevel_n) * extent))
    blur_fine = max(1, int((0.008 + 0.03 * bevel_n) * extent))

    img = Image.fromarray((sub.astype(np.uint8) * 255), mode="L")
    coarse = np.asarray(img.filter(ImageFilter.GaussianBlur(radius=blur_coarse)), dtype=np.float32) / 255.0
    mid = np.asarray(img.filter(ImageFilter.GaussianBlur(radius=blur_mid)), dtype=np.float32) / 255.0
    fine = np.asarray(img.filter(ImageFilter.GaussianBlur(radius=blur_fine)), dtype=np.float32) / 255.0

    # Soft inward distance from exterior blur (no scipy)
    inv = Image.fromarray(((~sub).astype(np.uint8) * 255), mode="L")
    exterior = np.asarray(inv.filter(ImageFilter.GaussianBlur(radius=blur_mid)), dtype=np.float32) / 255.0
    soft_dist = np.clip(1.0 - exterior, 0.0, 1.0) * mask_f

    # Complex interior: multi-lobe ridges around the region centroid
    cy = float(np.mean(ys - y0))
    cx = float(np.mean(xs - x0))
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    dx = xx - cx
    dy = yy - cy
    rad = np.sqrt(dx * dx + dy * dy) + 1e-5
    ang = np.arctan2(dy, dx)
    rad_n = rad / max(8.0, 0.45 * extent)
    lobes = (
        0.55
        + 0.28 * np.cos(3.0 * ang + 0.4)
        + 0.18 * np.cos(5.0 * ang - 1.1)
        + 0.12 * np.sin(2.0 * ang + rad_n * 2.4)
    )
    ring = np.sin(np.pi * np.clip(rad_n * (1.15 + 0.55 * bevel_n), 0.0, 1.0)) ** 2
    detail = soft_dist * lobes * (0.35 + 0.65 * ring)
    # Secondary micro-bumps for richer surface
    micro = soft_dist * (0.5 + 0.5 * np.sin(xx * 0.31 + yy * 0.27) * np.cos(xx * 0.19 - yy * 0.23))

    height = (
        0.42 * coarse
        + 0.28 * mid
        + 0.12 * fine
        + 0.22 * detail
        + 0.08 * micro
    ) * mask_f
    # Sharpen the plateau so deep fills read more sculpted
    height = np.clip(height * (0.85 + 0.55 * depth_n), 0.0, 1.0) * mask_f

    gx = np.zeros_like(height)
    gy = np.zeros_like(height)
    gx[:, 1:-1] = height[:, 2:] - height[:, :-2]
    gy[1:-1, :] = height[2:, :] - height[:-2, :]
    # Extra mid-frequency normals from detail alone for complex facets
    dgx = np.zeros_like(height)
    dgy = np.zeros_like(height)
    dgx[:, 1:-1] = detail[:, 2:] - detail[:, :-2]
    dgy[1:-1, :] = detail[2:, :] - detail[:-2, :]
    nx = -(gx + 0.55 * dgx)
    ny = -(gy + 0.55 * dgy)
    nz = np.full_like(height, 0.22 + 0.38 * bevel_n)
    invn = 1.0 / np.maximum(1e-5, np.sqrt(nx * nx + ny * ny + nz * nz))
    nx, ny, nz = nx * invn, ny * invn, nz * invn

    # Key light + cooler fill light for more dimensional shading
    lx, ly, lz = -0.55, -0.62, 0.58
    invl = 1.0 / np.sqrt(lx * lx + ly * ly + lz * lz)
    lx, ly, lz = lx * invl, ly * invl, lz * invl
    ndotl = np.clip(nx * lx + ny * ly + nz * lz, 0.0, 1.0)

    fx, fy, fz = 0.35, -0.15, 0.92
    invf = 1.0 / np.sqrt(fx * fx + fy * fy + fz * fz)
    fx, fy, fz = fx * invf, fy * invf, fz * invf
    fill_l = np.clip(nx * fx + ny * fy + nz * fz, 0.0, 1.0)

    hx, hy, hz = lx, ly, lz + 1.0
    invh = 1.0 / np.sqrt(hx * hx + hy * hy + hz * hz)
    hx, hy, hz = hx * invh, hy * invh, hz * invh
    half = np.clip(nx * hx + ny * hy + nz * hz, 0.0, 1.0)
    spec_broad = half ** (6.0 + 10.0 * (1.0 - bevel_n))
    spec_hot = half ** (22.0 + 40.0 * (1.0 - bevel_n))
    rim = np.clip(1.0 - height, 0.0, 1.0) * ndotl

    ambient = 1.0 - 0.94 * depth_n
    diffuse = 0.72 * depth_n * ndotl + 0.28 * depth_n * fill_l
    shade = ambient + diffuse + (0.16 + 0.38 * depth_n) * rim
    max_v = _max_v(pixels)
    base = np.array(color[:3], dtype=np.float32)
    rgb = base * shade[..., None]
    spec_amt = (0.18 + 0.55 * high_n) * spec_broad + (0.20 + 0.72 * high_n) * spec_hot
    rgb = rgb + (max_v - rgb) * spec_amt[..., None]
    # Deep contact shadow in valleys / dark rims
    valley = np.clip(1.0 - height * 0.55, 0.0, 1.0)
    shadow = (1.0 - ndotl) * (0.18 + 0.42 * depth_n) * valley
    rgb = rgb * (1.0 - shadow[..., None])
    rgb = np.clip(rgb, 0, max_v)

    alpha = float(color[3])
    dest = pixels[y0:y1, x0:x1].astype(np.float32)
    m = sub
    a = alpha / max_v
    if a >= 0.999:
        dest[..., 0][m] = rgb[..., 0][m]
        dest[..., 1][m] = rgb[..., 1][m]
        dest[..., 2][m] = rgb[..., 2][m]
        dest[..., 3][m] = alpha
    elif a > 0.0:
        dest[..., 0][m] = rgb[..., 0][m] * a + dest[..., 0][m] * (1.0 - a)
        dest[..., 1][m] = rgb[..., 1][m] * a + dest[..., 1][m] * (1.0 - a)
        dest[..., 2][m] = rgb[..., 2][m] * a + dest[..., 2][m] * (1.0 - a)
        dest[..., 3][m] = alpha + dest[..., 3][m] * (1.0 - a)
    pixels[y0:y1, x0:x1] = np.clip(dest, 0, _max_v(pixels)).astype(pixels.dtype)


def shade_region_drift(
    pixels: np.ndarray,
    region: np.ndarray,
    color: tuple[int, int, int, int],
    depth: float = 70.0,
    highlight: float = 55.0,
    bevel: float = 40.0,
) -> None:
    """Addiction-style emboss with silhouette contours and directional ridges (no center pinch)."""
    from PIL import Image, ImageFilter

    if not region.any():
        return
    h, w = region.shape
    ys, xs = np.where(region)
    pad = 4
    y0, y1 = max(0, int(ys.min()) - pad), min(h, int(ys.max()) + pad + 1)
    x0, x1 = max(0, int(xs.min()) - pad), min(w, int(xs.max()) + pad + 1)
    sub = region[y0:y1, x0:x1]
    sh, sw = sub.shape
    mask_f = sub.astype(np.float32)

    bevel_n = max(0.0, min(100.0, bevel)) / 100.0
    depth_n = min(1.0, (max(0.0, min(100.0, depth)) / 100.0) * 1.18)
    high_n = min(1.0, (max(0.0, min(100.0, highlight)) / 100.0) * 1.22)

    extent = max(sh, sw)
    blur_coarse = max(2, int((0.05 + 0.14 * bevel_n) * extent))
    blur_mid = max(1, int((0.02 + 0.07 * bevel_n) * extent))
    blur_fine = max(1, int((0.008 + 0.03 * bevel_n) * extent))

    img = Image.fromarray((sub.astype(np.uint8) * 255), mode="L")
    coarse = np.asarray(img.filter(ImageFilter.GaussianBlur(radius=blur_coarse)), dtype=np.float32) / 255.0
    mid = np.asarray(img.filter(ImageFilter.GaussianBlur(radius=blur_mid)), dtype=np.float32) / 255.0
    fine = np.asarray(img.filter(ImageFilter.GaussianBlur(radius=blur_fine)), dtype=np.float32) / 255.0

    inv = Image.fromarray(((~sub).astype(np.uint8) * 255), mode="L")
    exterior = np.asarray(inv.filter(ImageFilter.GaussianBlur(radius=blur_mid)), dtype=np.float32) / 255.0
    soft_dist = np.clip(1.0 - exterior, 0.0, 1.0) * mask_f

    # Interior detail without polar math: silhouette-parallel bands + crossed planar waves.
    # Contours follow soft_dist (never meet at a point); flow is directional across the crop.
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    xn = xx / max(8.0, float(sw))
    yn = yy / max(8.0, float(sh))
    contour = np.sin(np.pi * soft_dist * (1.8 + 2.8 * bevel_n)) ** 2
    flow = (
        0.55
        + 0.28 * np.sin((xn * 3.2 + yn * 2.1) * (2.0 + 3.5 * bevel_n) * np.pi + 0.3)
        + 0.18 * np.sin((xn * -1.7 + yn * 3.8) * (1.6 + 2.8 * bevel_n) * np.pi - 0.8)
        + 0.12 * np.cos((xn * 4.5 - yn * 1.2) * (1.2 + 2.0 * bevel_n) * np.pi)
    )
    detail = soft_dist * flow * (0.40 + 0.60 * contour)
    micro = soft_dist * (0.5 + 0.5 * np.sin(xx * 0.31 + yy * 0.27) * np.cos(xx * 0.19 - yy * 0.23))

    height = (
        0.42 * coarse
        + 0.28 * mid
        + 0.12 * fine
        + 0.22 * detail
        + 0.08 * micro
    ) * mask_f
    height = np.clip(height * (0.85 + 0.55 * depth_n), 0.0, 1.0) * mask_f

    gx = np.zeros_like(height)
    gy = np.zeros_like(height)
    gx[:, 1:-1] = height[:, 2:] - height[:, :-2]
    gy[1:-1, :] = height[2:, :] - height[:-2, :]
    dgx = np.zeros_like(height)
    dgy = np.zeros_like(height)
    dgx[:, 1:-1] = detail[:, 2:] - detail[:, :-2]
    dgy[1:-1, :] = detail[2:, :] - detail[:-2, :]
    nx = -(gx + 0.55 * dgx)
    ny = -(gy + 0.55 * dgy)
    nz = np.full_like(height, 0.22 + 0.38 * bevel_n)
    invn = 1.0 / np.maximum(1e-5, np.sqrt(nx * nx + ny * ny + nz * nz))
    nx, ny, nz = nx * invn, ny * invn, nz * invn

    lx, ly, lz = -0.55, -0.62, 0.58
    invl = 1.0 / np.sqrt(lx * lx + ly * ly + lz * lz)
    lx, ly, lz = lx * invl, ly * invl, lz * invl
    ndotl = np.clip(nx * lx + ny * ly + nz * lz, 0.0, 1.0)

    fx, fy, fz = 0.35, -0.15, 0.92
    invf = 1.0 / np.sqrt(fx * fx + fy * fy + fz * fz)
    fx, fy, fz = fx * invf, fy * invf, fz * invf
    fill_l = np.clip(nx * fx + ny * fy + nz * fz, 0.0, 1.0)

    hx, hy, hz = lx, ly, lz + 1.0
    invh = 1.0 / np.sqrt(hx * hx + hy * hy + hz * hz)
    hx, hy, hz = hx * invh, hy * invh, hz * invh
    half = np.clip(nx * hx + ny * hy + nz * hz, 0.0, 1.0)
    spec_broad = half ** (6.0 + 10.0 * (1.0 - bevel_n))
    spec_hot = half ** (22.0 + 40.0 * (1.0 - bevel_n))
    rim = np.clip(1.0 - height, 0.0, 1.0) * ndotl

    ambient = 1.0 - 0.94 * depth_n
    diffuse = 0.72 * depth_n * ndotl + 0.28 * depth_n * fill_l
    shade = ambient + diffuse + (0.16 + 0.38 * depth_n) * rim
    max_v = _max_v(pixels)
    base = np.array(color[:3], dtype=np.float32)
    rgb = base * shade[..., None]
    spec_amt = (0.18 + 0.55 * high_n) * spec_broad + (0.20 + 0.72 * high_n) * spec_hot
    rgb = rgb + (max_v - rgb) * spec_amt[..., None]
    valley = np.clip(1.0 - height * 0.55, 0.0, 1.0)
    shadow = (1.0 - ndotl) * (0.18 + 0.42 * depth_n) * valley
    rgb = rgb * (1.0 - shadow[..., None])
    rgb = np.clip(rgb, 0, max_v)

    alpha = float(color[3])
    dest = pixels[y0:y1, x0:x1].astype(np.float32)
    m = sub
    a = alpha / max_v
    if a >= 0.999:
        dest[..., 0][m] = rgb[..., 0][m]
        dest[..., 1][m] = rgb[..., 1][m]
        dest[..., 2][m] = rgb[..., 2][m]
        dest[..., 3][m] = alpha
    elif a > 0.0:
        dest[..., 0][m] = rgb[..., 0][m] * a + dest[..., 0][m] * (1.0 - a)
        dest[..., 1][m] = rgb[..., 1][m] * a + dest[..., 1][m] * (1.0 - a)
        dest[..., 2][m] = rgb[..., 2][m] * a + dest[..., 2][m] * (1.0 - a)
        dest[..., 3][m] = alpha + dest[..., 3][m] * (1.0 - a)
    pixels[y0:y1, x0:x1] = np.clip(dest, 0, _max_v(pixels)).astype(pixels.dtype)


def shade_region_wavy(
    pixels: np.ndarray,
    region: np.ndarray,
    color: tuple[int, int, int, int],
    depth: float = 70.0,
    highlight: float = 55.0,
    bevel: float = 40.0,
) -> None:
    """Emboss with corner-distance waves: highlights and shadows radiate from bbox corners."""
    from PIL import Image, ImageFilter

    if not region.any():
        return
    h, w = region.shape
    ys, xs = np.where(region)
    pad = 3
    y0, y1 = max(0, int(ys.min()) - pad), min(h, int(ys.max()) + pad + 1)
    x0, x1 = max(0, int(xs.min()) - pad), min(w, int(xs.max()) + pad + 1)
    sub = region[y0:y1, x0:x1]
    sh, sw = sub.shape
    mask_f = sub.astype(np.float32)

    bevel_n = max(0.0, min(100.0, bevel)) / 100.0
    depth_n = max(0.0, min(100.0, depth)) / 100.0
    high_n = max(0.0, min(100.0, highlight)) / 100.0

    # Soft edge height so the fill still reads raised at the silhouette.
    blur_r = max(1, int((0.012 + 0.07 * bevel_n) * max(sh, sw)))
    img = Image.fromarray((sub.astype(np.uint8) * 255), mode="L")
    edge = np.asarray(img.filter(ImageFilter.GaussianBlur(radius=blur_r)), dtype=np.float32) / 255.0
    edge = edge * mask_f

    # Distances from the four region-bbox corners (in crop-local coords).
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    # Use the filled region's own extremes so irregular shapes still get clear corners.
    ry0, ry1 = float(ys.min() - y0), float(ys.max() - y0)
    rx0, rx1 = float(xs.min() - x0), float(xs.max() - x0)
    d_tl = np.sqrt((xx - rx0) ** 2 + (yy - ry0) ** 2)
    d_tr = np.sqrt((xx - rx1) ** 2 + (yy - ry0) ** 2)
    d_bl = np.sqrt((xx - rx0) ** 2 + (yy - ry1) ** 2)
    d_br = np.sqrt((xx - rx1) ** 2 + (yy - ry1) ** 2)
    diag = max(8.0, float(np.hypot(rx1 - rx0, ry1 - ry0)))
    n_tl, n_tr = d_tl / diag, d_tr / diag
    n_bl, n_br = d_bl / diag, d_br / diag

    # Wave frequency: higher bevel → tighter ripples from each corner.
    freq = (2.2 + 5.5 * bevel_n) * np.pi
    wave = (
        np.sin(freq * n_tl)
        + 0.85 * np.sin(freq * n_tr + 0.9)
        + 0.75 * np.sin(freq * n_bl + 1.7)
        + 0.95 * np.sin(freq * n_br + 2.6)
    ) * 0.25
    # Soft falloff so waves settle toward the interior center.
    fall = np.clip(1.0 - 0.55 * np.minimum(np.minimum(n_tl, n_tr), np.minimum(n_bl, n_br)), 0.0, 1.0)
    wave = wave * fall * mask_f

    # Inverse-distance corner weights: TL/TR = light, BL/BR = shadow.
    eps = 1.5
    w_tl = 1.0 / (d_tl + eps)
    w_tr = 1.0 / (d_tr + eps)
    w_bl = 1.0 / (d_bl + eps)
    w_br = 1.0 / (d_br + eps)
    w_sum = w_tl + w_tr + w_bl + w_br
    light_c = (w_tl + 0.7 * w_tr) / w_sum
    dark_c = (w_br + 0.7 * w_bl) / w_sum
    corner_shade = (light_c - dark_c) * mask_f

    height = np.clip(0.55 * edge + 0.45 * (0.5 + 0.5 * wave) + 0.25 * corner_shade, 0.0, 1.0) * mask_f

    gx = np.zeros_like(height)
    gy = np.zeros_like(height)
    gx[:, 1:-1] = height[:, 2:] - height[:, :-2]
    gy[1:-1, :] = height[2:, :] - height[:-2, :]
    # Bias normals with corner-distance gradients so light tracks the wave sources.
    nx = -(gx + 0.40 * (w_tr - w_tl) / max(w_sum.max(), 1e-5))
    ny = -(gy + 0.40 * (w_bl - w_tl) / max(w_sum.max(), 1e-5))
    nz = np.full_like(height, 0.30 + 0.40 * bevel_n)
    invn = 1.0 / np.maximum(1e-5, np.sqrt(nx * nx + ny * ny + nz * nz))
    nx, ny, nz = nx * invn, ny * invn, nz * invn

    lx, ly, lz = -0.52, -0.55, 0.65
    invl = 1.0 / np.sqrt(lx * lx + ly * ly + lz * lz)
    lx, ly, lz = lx * invl, ly * invl, lz * invl
    ndotl = np.clip(nx * lx + ny * ly + nz * lz, 0.0, 1.0)

    hx, hy, hz = lx, ly, lz + 1.0
    invh = 1.0 / np.sqrt(hx * hx + hy * hy + hz * hz)
    hx, hy, hz = hx * invh, hy * invh, hz * invh
    half = np.clip(nx * hx + ny * hy + nz * hz, 0.0, 1.0)
    spec_pow = 9.0 + 24.0 * (1.0 - bevel_n)
    # Specular pools near light corners and on wave crests.
    crest = np.clip(0.5 + 0.5 * wave, 0.0, 1.0)
    spec = (half ** spec_pow) * (0.35 + 0.65 * light_c) * (0.45 + 0.55 * crest)

    ambient = 1.0 - 0.86 * depth_n
    diffuse = 0.70 * depth_n * ndotl + 0.30 * depth_n * np.clip(0.5 + corner_shade, 0.0, 1.0)
    shade = ambient + diffuse + (0.10 + 0.28 * depth_n) * crest * ndotl
    max_v = _max_v(pixels)
    base = np.array(color[:3], dtype=np.float32)
    rgb = base * shade[..., None]
    rgb = rgb + (max_v - rgb) * ((0.20 + 0.80 * high_n) * spec[..., None])
    shadow = dark_c * (0.16 + 0.40 * depth_n) * np.clip(1.0 - crest * 0.5, 0.0, 1.0)
    rgb = rgb * (1.0 - shadow[..., None])
    rgb = np.clip(rgb, 0, max_v)

    alpha = float(color[3])
    dest = pixels[y0:y1, x0:x1].astype(np.float32)
    m = sub
    a = alpha / max_v
    if a >= 0.999:
        dest[..., 0][m] = rgb[..., 0][m]
        dest[..., 1][m] = rgb[..., 1][m]
        dest[..., 2][m] = rgb[..., 2][m]
        dest[..., 3][m] = alpha
    elif a > 0.0:
        dest[..., 0][m] = rgb[..., 0][m] * a + dest[..., 0][m] * (1.0 - a)
        dest[..., 1][m] = rgb[..., 1][m] * a + dest[..., 1][m] * (1.0 - a)
        dest[..., 2][m] = rgb[..., 2][m] * a + dest[..., 2][m] * (1.0 - a)
        dest[..., 3][m] = alpha + dest[..., 3][m] * (1.0 - a)
    pixels[y0:y1, x0:x1] = np.clip(dest, 0, _max_v(pixels)).astype(pixels.dtype)


def smear_sample_tip(
    pixels: np.ndarray,
    x: float,
    y: float,
    radius: float,
) -> np.ndarray:
    """Capture a square float tip centered on (x, y) for smudging."""
    r = max(1, int(np.ceil(radius)))
    size = 2 * r + 1
    tip = np.zeros((size, size, 4), dtype=np.float32)
    h, w = pixels.shape[:2]
    # Tip local (0..size) maps to image (x-r .. x+r)
    iy = np.arange(size, dtype=np.int32) + int(round(y)) - r
    ix = np.arange(size, dtype=np.int32) + int(round(x)) - r
    valid_y = (iy >= 0) & (iy < h)
    valid_x = (ix >= 0) & (ix < w)
    if not valid_y.any() or not valid_x.any():
        return tip
    yy = iy[valid_y]
    xx = ix[valid_x]
    # fancy index into tip rows/cols that are in-bounds
    tip_y = np.flatnonzero(valid_y)
    tip_x = np.flatnonzero(valid_x)
    tip[np.ix_(tip_y, tip_x)] = pixels[np.ix_(yy, xx)].astype(np.float32)
    return tip


def smear_stamp(
    pixels: np.ndarray,
    x: float,
    y: float,
    radius: float,
    tip: np.ndarray,
    strength: float = 0.45,
    mask: np.ndarray | None = None,
) -> None:
    """Smudge: blend *tip* into the canvas, then pick canvas color back into *tip* (in-place)."""
    r = max(0.5, float(radius))
    strength = float(np.clip(strength, 0.0, 1.0))
    if strength <= 0.0 or tip.size == 0:
        return
    h, w = pixels.shape[:2]
    half = tip.shape[0] // 2
    cx = int(round(x))
    cy = int(round(y))
    # Overlap of tip with canvas
    tip_y0 = max(0, half - cy)
    tip_x0 = max(0, half - cx)
    tip_y1 = tip.shape[0] - max(0, (cy + half + 1) - h)
    tip_x1 = tip.shape[1] - max(0, (cx + half + 1) - w)
    if tip_x0 >= tip_x1 or tip_y0 >= tip_y1:
        return
    y0 = cy - half + tip_y0
    x0 = cx - half + tip_x0
    y1 = y0 + (tip_y1 - tip_y0)
    x1 = x0 + (tip_x1 - tip_x0)

    yy, xx = np.ogrid[y0:y1, x0:x1]
    dist = np.sqrt((xx + 0.5 - x) ** 2 + (yy + 0.5 - y) ** 2)
    falloff = np.clip(1.0 - dist / r, 0.0, 1.0).astype(np.float32)
    if mask is not None:
        falloff *= (mask[y0:y1, x0:x1] > 0).astype(np.float32)
    if not np.any(falloff):
        return

    blend = (strength * falloff)[..., None]
    dest = pixels[y0:y1, x0:x1].astype(np.float32)
    src = tip[tip_y0:tip_y1, tip_x0:tip_x1]
    # Paint tip onto canvas
    out = dest * (1.0 - blend) + src * blend
    pixels[y0:y1, x0:x1] = np.clip(out, 0, _max_v(pixels)).astype(pixels.dtype)
    # Pick up canvas into tip (slightly stronger pickup keeps the smear wet)
    pickup = np.clip(strength * 1.15, 0.0, 1.0) * falloff[..., None]
    tip[tip_y0:tip_y1, tip_x0:tip_x1] = src * (1.0 - pickup) + dest * pickup


def background_erase_stroke(
    pixels: np.ndarray,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    radius: float,
    key_color: tuple[int, int, int, int],
    tolerance: int = 48,
    mask: np.ndarray | None = None,
    opacity: float = 1.0,
) -> None:
    h, w = pixels.shape[:2]
    dist = float(np.hypot(x1 - x0, y1 - y0))
    steps = max(1, int(dist / max(0.5, radius * 0.4)))
    key = np.array(key_color[:3], dtype=np.int32)
    op = float(np.clip(opacity, 0.0, 1.0))
    if op <= 0.0:
        return
    for s in range(steps + 1):
        t = s / steps
        x = x0 + (x1 - x0) * t
        y = y0 + (y1 - y0) * t
        r = radius
        xa = max(0, int(x - r - 1))
        ya = max(0, int(y - r - 1))
        xb = min(w, int(x + r + 2))
        yb = min(h, int(y + r + 2))
        if xa >= xb or ya >= yb:
            continue
        yy, xx = np.ogrid[ya:yb, xa:xb]
        disk = np.sqrt((xx + 0.5 - x) ** 2 + (yy + 0.5 - y) ** 2) <= r
        patch = pixels[ya:yb, xa:xb]
        diff = np.max(np.abs(patch[..., :3].astype(np.int32) - key), axis=2)
        hit = disk & (diff <= tolerance)
        if mask is not None:
            hit &= mask[ya:yb, xa:xb] > 0
        if op >= 0.999:
            patch[..., 3] = np.where(hit, 0, patch[..., 3])
        else:
            alpha = patch[..., 3].astype(np.float32)
            alpha[hit] *= 1.0 - op
            patch[..., 3] = np.clip(alpha, 0, _max_v(pixels)).astype(pixels.dtype)


def fill_polygon_mask(mask: np.ndarray, points: list[tuple[float, float]]) -> None:
    """Rasterize a closed polygon into mask (255)."""
    if len(points) < 3:
        return
    from PIL import Image, ImageDraw

    h, w = mask.shape
    img = Image.fromarray(mask, mode="L")
    draw = ImageDraw.Draw(img)
    draw.polygon([(p[0], p[1]) for p in points], outline=255, fill=255)
    mask[:] = np.array(img, dtype=np.uint8)


def _load_truetype(font_path: str | None, size: int):
    from PIL import ImageFont

    size = max(1, int(size))
    if font_path:
        try:
            return ImageFont.truetype(font_path, size=size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def measure_text(
    text: str,
    font_path: str | None,
    size: float,
) -> tuple[float, float, float, float]:
    """Return (left, top, right, bottom) relative to the draw origin (0, 0)."""
    from PIL import Image, ImageDraw

    if not text:
        # Empty caret box roughly the em-height.
        h = max(1.0, float(size))
        return 0.0, 0.0, max(1.0, h * 0.35), h
    font = _load_truetype(font_path, int(round(size)))
    img = Image.new("L", (1, 1), 0)
    draw = ImageDraw.Draw(img)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return float(left), float(top), float(right), float(bottom)


def blit_text(
    pixels: np.ndarray,
    text: str,
    x: float,
    y: float,
    *,
    font_path: str | None,
    size: float,
    color: tuple[int, int, int, int],
    mask: np.ndarray | None = None,
) -> tuple[float, float, float, float]:
    """Alpha-composite *text* onto *pixels* with top-left at (x, y). Returns bbox."""
    from PIL import Image, ImageDraw

    if not text:
        return float(x), float(y), float(x), float(y)

    font = _load_truetype(font_path, int(round(size)))
    probe = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    draw = ImageDraw.Draw(probe)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    pad = 2
    ink_w = max(1, int(math.ceil(right - left)))
    ink_h = max(1, int(math.ceil(bottom - top)))
    tw = ink_w + pad * 2
    th = ink_h + pad * 2
    glyph = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glyph)
    max_v = _max_v(pixels)
    scale = 255.0 / max_v if max_v > 255.0 else 1.0
    r, g, b, a = color
    fill = (
        int(round(r * scale)),
        int(round(g * scale)),
        int(round(b * scale)),
        int(round(a * scale)),
    )
    # Shift so glyph ink starts at (pad, pad); document anchor is top-left (x, y).
    gdraw.text((-left + pad, -top + pad), text, font=font, fill=fill)

    dest_x = int(math.floor(x - pad))
    dest_y = int(math.floor(y - pad))
    h, w = pixels.shape[:2]
    x0 = max(0, dest_x)
    y0 = max(0, dest_y)
    x1 = min(w, dest_x + tw)
    y1 = min(h, dest_y + th)
    bbox = (float(x), float(y), float(x + ink_w), float(y + ink_h))
    if x0 >= x1 or y0 >= y1:
        return bbox

    sx0 = x0 - dest_x
    sy0 = y0 - dest_y
    sx1 = sx0 + (x1 - x0)
    sy1 = sy0 + (y1 - y0)
    src = np.array(glyph.crop((sx0, sy0, sx1, sy1)), dtype=np.float32)
    if scale != 1.0:
        src *= max_v / 255.0

    dst = pixels[y0:y1, x0:x1].astype(np.float32)
    src_a = src[..., 3:4] / max_v
    if mask is not None:
        m = (mask[y0:y1, x0:x1] > 0).astype(np.float32)[..., None]
        src_a = src_a * m
        src = src.copy()
        src[..., 3:4] = src_a * max_v

    out = dst.copy()
    out[..., :3] = src[..., :3] * src_a + dst[..., :3] * (1.0 - src_a)
    out[..., 3:4] = src[..., 3:4] * src_a + dst[..., 3:4] * (1.0 - src_a)
    pixels[y0:y1, x0:x1] = np.clip(out, 0, max_v).astype(pixels.dtype)
    return bbox

