"""Shared pixel painting helpers (CPU, uploaded to GPU textures)."""

from __future__ import annotations

import functools
import math
import random

import numpy as np
from PIL import Image, ImageFilter

from inkobold.core.bubbles import (
    bubble_influence_bbox,
    place_bubble_cluster as _place_bubble_cluster,
    render_bubbles_list,
)

def _max_v(pixels: np.ndarray) -> float:
    return 65535.0 if pixels.dtype == np.uint16 else 255.0


# Direct ufunc calls: ``np.clip`` is a thin Python wrapper around exactly
# minimum(maximum(x, lo), hi) but costs several microseconds per call, which
# adds up at ~1000 stamps per fast stroke.
_maximum = np.maximum
_minimum = np.minimum


def _clip(a: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return _minimum(_maximum(a, lo), hi)


def _grid(y0: int, y1: int, x0: int, x1: int) -> tuple[np.ndarray, np.ndarray]:
    """Broadcastable (yy, xx) integer coordinate columns/rows (like ``np.ogrid``, cheaper)."""
    return np.arange(y0, y1)[:, None], np.arange(x0, x1)[None, :]


def _period(v: float, size: int) -> float:
    """Map ``v`` into ``[0, size)`` (works for negatives)."""
    if size <= 0:
        return v
    return float(v) - float(size) * math.floor(float(v) / float(size))


def wrap_pixel_coords(x: float, y: float, width: int, height: int) -> tuple[int, int]:
    """Integer pixel coords wrapped into the document."""
    w = max(1, int(width))
    h = max(1, int(height))
    return int(math.floor(_period(x, w))), int(math.floor(_period(y, h)))


def stamp_centers(
    x: float,
    y: float,
    extent: float,
    width: int,
    height: int,
    wrap: bool,
) -> list[tuple[float, float]]:
    """Stamp positions: primary (optionally folded) plus edge replicas when wrapping."""
    if not wrap or width <= 0 or height <= 0:
        return [(float(x), float(y))]
    cx = _period(x, width)
    cy = _period(y, height)
    xs = [cx]
    ys = [cy]
    ext = max(0.0, float(extent))
    if cx - ext < 0:
        xs.append(cx + width)
    if cx + ext > width:
        xs.append(cx - width)
    if cy - ext < 0:
        ys.append(cy + height)
    if cy + ext > height:
        ys.append(cy - height)
    return [(px, py) for py in ys for px in xs]


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
    wrap: bool = False,
) -> None:
    h, w = pixels.shape[:2]
    r = max(0.5, radius)
    if wrap:
        for px, py in stamp_centers(x, y, r + 1.5, w, h, True):
            stamp_disk(
                pixels,
                px,
                py,
                radius,
                color,
                erase=erase,
                mask=mask,
                key_color=key_color,
                threshold=threshold,
                replace=replace,
                opacity=opacity,
                wrap=False,
            )
        return
    x0 = max(0, int(x - r - 1))
    y0 = max(0, int(y - r - 1))
    x1 = min(w, int(x + r + 2))
    y1 = min(h, int(y + r + 2))
    if x0 >= x1 or y0 >= y1:
        return
    op = min(1.0, max(0.0, float(opacity)))
    if op <= 0.0:
        return
    yy, xx = _grid(y0, y1, x0, x1)
    dist = np.sqrt((xx + 0.5 - x) ** 2 + (yy + 0.5 - y) ** 2)
    cov = _clip(1.0 - (dist - (r - 0.5)), 0.0, 1.0)
    if mask is not None:
        cov = cov * (mask[y0:y1, x0:x1] > 0).astype(np.float32)
    if not cov.any():
        return
    max_v = _max_v(pixels)
    region = pixels[y0:y1, x0:x1]
    needs_rgb = not erase or (key_color is not None and threshold is not None)
    # Erasing only touches alpha; skip converting RGB unless a colour key needs it.
    patch = region.astype(np.float32) if needs_rgb else None

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
            match = _clip(1.0 - diff / float(tol), 0.0, 1.0)
        strength = cov * match * op
    else:
        strength = cov * op

    if erase:
        alpha = region[..., 3].astype(np.float32)
        alpha *= 1.0 - strength
        region[..., 3] = _clip(alpha, 0, max_v).astype(pixels.dtype)
        return
    elif replace:
        src = np.array(color, dtype=np.float32)
        a = (src[3] / max_v) * strength[..., None]
        patch[..., :3] = src[:3] * a + patch[..., :3] * (1.0 - a)
        patch[..., 3:4] = _maximum(patch[..., 3:4], src[3] * a)
    else:
        src = np.array(color, dtype=np.float32)
        a = (src[3] / max_v) * strength[..., None]
        patch[..., :3] = src[:3] * a + patch[..., :3] * (1.0 - a)
        patch[..., 3:4] = src[3] * a + patch[..., 3:4] * (1.0 - a)
    region[...] = _clip(patch, 0, max_v).astype(pixels.dtype)


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
    wrap: bool = False,
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
            wrap=wrap,
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
    wrap: bool = False,
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
            wrap=wrap,
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
    wrap: bool = False,
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
            threshold=threshold, replace=replace, opacity=opacity, wrap=wrap,
        )
        return

    deg = abs(float(degrees))
    if deg < 1e-3:
        stroke_segment(
            pixels, x0, y0, x1, y1, radius, color,
            erase=erase, mask=mask, key_color=key_color,
            threshold=threshold, replace=replace, opacity=opacity, wrap=wrap,
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
            threshold=threshold, replace=replace, opacity=opacity, wrap=wrap,
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
            wrap=wrap,
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
    wrap: bool = False,
) -> None:
    """Stamp a full circle centered at (cx, cy) with geometric radius `circle_radius`."""
    r_circ = max(0.0, float(circle_radius))
    if r_circ < 1e-6:
        stamp_disk(
            pixels, cx, cy, radius, color,
            erase=erase, mask=mask, key_color=key_color,
            threshold=threshold, replace=replace, opacity=opacity, wrap=wrap,
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
            wrap=wrap,
        )


_tip_scale_cache: dict[tuple[int, int, int, int], np.ndarray] = {}


def _scaled_brush_tip(tip: np.ndarray, diameter: int) -> np.ndarray:
    """Scale tip so its longest side equals *diameter* (RGBA uint8)."""
    d = max(1, int(diameter))
    tip_u8 = np.asarray(tip, dtype=np.uint8)
    th, tw = tip_u8.shape[:2]
    key = (id(tip), th, tw, d)
    cached = _tip_scale_cache.get(key)
    if cached is not None:
        return cached
    scale = d / float(max(th, tw, 1))
    nh = max(1, int(round(th * scale)))
    nw = max(1, int(round(tw * scale)))
    if nh == th and nw == tw:
        out = tip_u8
    else:
        img = Image.fromarray(tip_u8, mode="RGBA")
        out = np.asarray(img.resize((nw, nh), Image.Resampling.LANCZOS), dtype=np.uint8)
    if len(_tip_scale_cache) > 64:
        _tip_scale_cache.clear()
    _tip_scale_cache[key] = out
    return out


def stamp_brush_tip(
    pixels: np.ndarray,
    x: float,
    y: float,
    tip: np.ndarray,
    radius: float,
    color: tuple[int, int, int, int],
    mask: np.ndarray | None = None,
    opacity: float = 1.0,
    wrap: bool = False,
) -> None:
    """Stamp an RGBA brush tip tinted with *color* (tip alpha = coverage)."""
    if tip is None or tip.size == 0 or tip.ndim != 3 or tip.shape[2] < 4:
        return
    r = max(0.5, float(radius))
    op = min(1.0, max(0.0, float(opacity)))
    if op <= 0.0:
        return
    h, w = pixels.shape[:2]
    if wrap:
        for px, py in stamp_centers(x, y, r + 1.5, w, h, True):
            stamp_brush_tip(
                pixels, px, py, tip, radius, color, mask=mask, opacity=opacity, wrap=False,
            )
        return
    scaled = _scaled_brush_tip(tip, max(1, int(round(2.0 * r))))
    th, tw = scaled.shape[:2]
    # Tip centered on (x, y)
    x0 = int(math.floor(x - tw * 0.5))
    y0 = int(math.floor(y - th * 0.5))
    x1 = x0 + tw
    y1 = y0 + th
    cx0 = max(0, x0)
    cy0 = max(0, y0)
    cx1 = min(w, x1)
    cy1 = min(h, y1)
    if cx0 >= cx1 or cy0 >= cy1:
        return
    tx0 = cx0 - x0
    ty0 = cy0 - y0
    tx1 = tx0 + (cx1 - cx0)
    ty1 = ty0 + (cy1 - cy0)
    cov = scaled[ty0:ty1, tx0:tx1, 3].astype(np.float32) * (1.0 / 255.0) * op
    if mask is not None:
        cov = cov * (mask[cy0:cy1, cx0:cx1] > 0).astype(np.float32)
    if not cov.any():
        return
    max_v = _max_v(pixels)
    region = pixels[cy0:cy1, cx0:cx1]
    patch = region.astype(np.float32)
    src = np.array(color, dtype=np.float32)
    a = (src[3] / max_v) * cov[..., None]
    patch[..., :3] = src[:3] * a + patch[..., :3] * (1.0 - a)
    patch[..., 3:4] = src[3] * a + patch[..., 3:4] * (1.0 - a)
    region[...] = _clip(patch, 0, max_v).astype(pixels.dtype)


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
    tip: np.ndarray | None = None,
    opacity: float = 1.0,
    wrap: bool = False,
) -> None:
    """Stamp a segment while linearly interpolating stamp radius (pressure taper)."""
    r0 = max(0.5, float(radius0))
    r1 = max(0.5, float(radius1))
    dist = float(np.hypot(x1 - x0, y1 - y0))
    step = max(0.5, min(r0, r1) * 0.35)
    steps = max(1, int(dist / step))
    use_tip = tip is not None
    for i in range(steps + 1):
        t = i / steps
        rr = r0 + (r1 - r0) * t
        px = x0 + (x1 - x0) * t
        py = y0 + (y1 - y0) * t
        if use_tip:
            stamp_brush_tip(
                pixels, px, py, tip, rr, color, mask=mask, opacity=opacity, wrap=wrap,
            )
        else:
            stamp_disk(
                pixels, px, py, rr, color, mask=mask, opacity=opacity, wrap=wrap,
            )


def stamp_bubbles(
    pixels: np.ndarray,
    x: float,
    y: float,
    radius: float,
    color: tuple[int, int, int, int],
    mask: np.ndarray | None = None,
    opacity: float = 1.0,
    wrap: bool = False,
    seed: int | None = None,
    density: float = 50.0,
    *,
    force: bool = False,
) -> None:
    """Stamp milk-style air bubbles (soft hemispheres + rim + shared membranes).

    ``force`` bypasses the low-density spawn skip (used for single clicks).
    """
    h, w = pixels.shape[:2]
    r = max(1.5, float(radius))
    op = min(1.0, max(0.0, float(opacity)))
    if op <= 0.0:
        return
    if wrap:
        for px, py in stamp_centers(x, y, r * 1.6 + 2.0, w, h, True):
            stamp_bubbles(
                pixels, px, py, radius, color,
                mask=mask, opacity=opacity, wrap=False, seed=seed,
                density=density, force=force,
            )
        return

    if seed is None:
        seed = (
            (int(round(x * 16.0)) * 73856093)
            ^ (int(round(y * 16.0)) * 19349663)
            ^ (int(round(r * 8.0)) * 83492791)
        ) & 0xFFFFFFFF
    rng = np.random.default_rng(seed)
    placed = _place_bubble_cluster(x, y, r, rng, density=density, force=force)
    if not placed:
        return
    render_bubbles_list(pixels, placed, color, mask=mask, opacity=op)


def collect_bubbles_along_segment(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    radius0: float,
    radius1: float,
    density: float = 50.0,
    *,
    force: bool = False,
) -> list[tuple[float, float, float, float]]:
    """Place bubble clusters along a segment without painting (for stroke accumulate)."""
    r0 = max(1.5, float(radius0))
    r1 = max(1.5, float(radius1))
    dens = max(0.0, min(100.0, float(density))) / 100.0
    dist = float(np.hypot(x1 - x0, y1 - y0))
    placed: list[tuple[float, float, float, float]] = []
    if dist <= 1e-6:
        seed = (
            (int(round(x0 * 16.0)) * 73856093)
            ^ (int(round(y0 * 16.0)) * 19349663)
            ^ (int(round(r0 * 8.0)) * 83492791)
        ) & 0xFFFFFFFF
        rng = np.random.default_rng(seed)
        return _place_bubble_cluster(x0, y0, r0, rng, density=density, force=True)
    # Spacing: dens=0 → ~16× radius, dens=10 → ~12×, dens=50 → ~3×, dens=100 → ~0.4×.
    spacing_frac = 0.40 + 15.5 * (1.0 - dens) ** 2.05
    step = max(1.0, min(r0, r1) * spacing_frac)
    steps = max(1, int(dist / step))
    for i in range(steps + 1):
        t = i / steps
        rr = r0 + (r1 - r0) * t
        px = x0 + (x1 - x0) * t
        py = y0 + (y1 - y0) * t
        seed = (
            (int(round(px * 16.0)) * 73856093)
            ^ (int(round(py * 16.0)) * 19349663)
            ^ (int(round(rr * 8.0)) * 83492791)
            ^ (i * 2654435761)
        ) & 0xFFFFFFFF
        rng = np.random.default_rng(seed)
        # Only a true press (force) guarantees a bubble; drag sites respect density.
        placed.extend(
            _place_bubble_cluster(px, py, rr, rng, density=density, force=False)
        )
    # If this segment was the stroke press (force) and nothing spawned, place one.
    if force and not placed:
        seed = (
            (int(round(x0 * 16.0)) * 73856093)
            ^ (int(round(y0 * 16.0)) * 19349663)
            ^ (int(round(r0 * 8.0)) * 83492791)
        ) & 0xFFFFFFFF
        rng = np.random.default_rng(seed)
        placed = _place_bubble_cluster(x0, y0, r0, rng, density=density, force=True)
    return placed


def stroke_segment_bubbles(
    pixels: np.ndarray,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    radius0: float,
    radius1: float,
    color: tuple[int, int, int, int],
    mask: np.ndarray | None = None,
    opacity: float = 1.0,
    wrap: bool = False,
    density: float = 50.0,
) -> None:
    """Stamp milk-style bubble clusters along a pressure-tapered segment.

    ``density`` (0–100) controls stamp spacing, spawn chance, and cluster
    richness: low = sparse solitary bubbles, high = packed foam.
    """
    placed = collect_bubbles_along_segment(
        x0, y0, x1, y1, radius0, radius1, density=density, force=True,
    )
    if not placed:
        return
    if wrap:
        h, w = pixels.shape[:2]
        # Re-stamp via per-cluster calls so edge wrap still works.
        for cx, cy, rad, strength in placed:
            render_bubbles_list(
                pixels, [(cx, cy, rad, strength)], color,
                mask=mask, opacity=opacity,
            )
            for px, py in stamp_centers(cx, cy, rad * 1.6 + 2.0, w, h, True):
                if abs(px - cx) < 1e-6 and abs(py - cy) < 1e-6:
                    continue
                render_bubbles_list(
                    pixels, [(px, py, rad, strength)], color,
                    mask=mask, opacity=opacity,
                )
        return
    render_bubbles_list(pixels, placed, color, mask=mask, opacity=opacity)


def _same_color_coverage(
    region: np.ndarray,
    color: np.ndarray,
    max_v: float,
) -> np.ndarray:
    """Soft 0..1 mask of pixels that already hold *color* (encoding-aware).

    Brush stamps over a transparent layer leave ``rgb == color * alpha``;
    fills / opaque layers hold ``rgb == color``. Accept either. Only visible
    pixels are examined, so sparse windows cost little.
    """
    dest_a = region[..., 3]
    cover = np.zeros(dest_a.shape, dtype=np.float32)
    cand = dest_a > (0.05 * max_v)
    if not cand.any():
        return cover
    pix = region[cand].astype(np.float32)  # (n, 4)
    a = pix[:, 3] / max_v
    rgb = pix[:, :3]
    tol = max_v * (20.0 / 255.0)
    diff = np.max(np.abs(rgb - color[:3] * a[:, None]), axis=1)
    diff_straight = np.max(np.abs(rgb - color[:3]), axis=1)
    match = (diff <= tol) | (diff_straight <= tol)
    cover[cand] = np.where(match, _minimum(a, 1.0), 0.0).astype(np.float32)
    return cover


# Hard cap for the structuring element (300 weld on huge brushes).
WELD_MAX_R = 96
# Pixels with at least this much same-color coverage count as "ink" for the
# morphology (soft AA fringes below it are left alone).
_WELD_INK = 0.45


@functools.lru_cache(maxsize=128)
def _disk_chords(radius: int) -> tuple[tuple[int, int], ...]:
    """(dy, half_chord) rows of the digital disk of the given radius."""
    r = max(0, int(radius))
    return tuple(
        (dy, int(math.floor(math.sqrt(r * r - dy * dy) + 1e-9)))
        for dy in range(-r, r + 1)
    )


def binary_disk_filter(
    src: np.ndarray,
    radius: int,
    erode: bool,
    y0: int,
    y1: int,
    x0: int,
    x1: int,
) -> np.ndarray:
    """Disk dilation (or erosion) of a boolean field over ``src[y0:y1, x0:x1]``.

    Reads up to *radius* beyond the output crop; everything outside *src*
    counts as empty. Decomposes the disk into horizontal chords evaluated with
    row prefix sums, so it costs O(radius) vectorised passes instead of the
    O(radius²) shifted copies of a naive structuring-element loop.
    """
    h, w = src.shape
    oh, ow = y1 - y0, x1 - x0
    r = max(0, int(radius))
    if oh <= 0 or ow <= 0:
        return np.zeros((max(0, oh), max(0, ow)), dtype=bool)
    if r <= 0:
        return src[y0:y1, x0:x1].copy()
    ry0, ry1 = max(0, y0 - r), min(h, y1 + r)
    rx0, rx1 = max(0, x0 - r), min(w, x1 + r)
    rows = src[ry0:ry1, rx0:rx1]
    rh, rw = rows.shape
    # Horizontal prefix sums with r zero columns of padding each side:
    # cs[y, i] == count of True in padded_row[:i].
    cs = np.zeros((rh, rw + 2 * r + 1), dtype=np.int32)
    np.cumsum(rows, axis=1, dtype=np.int32, out=cs[:, r + 1 : r + 1 + rw])
    cs[:, r + 1 + rw :] = cs[:, r + rw : r + rw + 1]
    cx0 = x0 - rx0  # output column 0 → padded index cx0 + r
    out = np.ones((oh, ow), dtype=bool) if erode else np.zeros((oh, ow), dtype=bool)
    lines: dict[int, np.ndarray] = {}
    for dy, k in _disk_chords(r):
        line = lines.get(k)
        if line is None:
            lo = cs[:, cx0 + r - k : cx0 + r - k + ow]
            hi = cs[:, cx0 + r + k + 1 : cx0 + r + k + 1 + ow]
            cnt = hi - lo
            line = (cnt == 2 * k + 1) if erode else (cnt > 0)
            lines[k] = line
        base = y0 - ry0 + dy  # local source row for output row 0
        ya = max(0, -base)
        yb = min(oh, rh - base)
        if erode:
            if ya > 0:
                out[:ya] = False
            if yb < oh:
                out[yb:] = False
            if yb > ya:
                out[ya:yb] &= line[base + ya : base + yb]
        elif yb > ya:
            out[ya:yb] |= line[base + ya : base + yb]
    return out


def _interior3(b: np.ndarray) -> np.ndarray:
    """Pixels whose full 3×3 neighbourhood is set (outside counts as unset)."""
    h, w = b.shape
    p = np.zeros((h + 2, w + 2), dtype=bool)
    p[1:-1, 1:-1] = b
    out = b.copy()
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            if dy == 1 and dx == 1:
                continue
            out &= p[dy : dy + h, dx : dx + w]
    return out


def _box3_mean(b: np.ndarray) -> np.ndarray:
    """3×3 box mean of a bool or float field (cheap edge anti-aliasing)."""
    h, w = b.shape
    p = np.zeros((h + 2, w + 2), dtype=np.float32)
    p[1:-1, 1:-1] = b
    acc = np.zeros((h, w), dtype=np.float32)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            acc += p[dy : dy + h, dx : dx + w]
    acc *= 1.0 / 9.0
    return acc


def _weld_closings_exact(
    before_b: np.ndarray,
    after_b: np.ndarray,
    r: int,
    roi: tuple[int, int, int, int],
    dbox: tuple[int, int, int, int],
) -> tuple[np.ndarray, np.ndarray]:
    """Boolean disk closings of *before* / *after* over *roi* (window coords).

    *dbox* is the crop the dilations need (roi grown by r).
    """
    ry0, ry1, rx0, rx1 = roi
    dy0, dy1, dx0, dx1 = dbox
    d_before = binary_disk_filter(before_b, r, False, dy0, dy1, dx0, dx1)
    d_after = binary_disk_filter(after_b, r, False, dy0, dy1, dx0, dx1)
    sub = (ry0 - dy0, ry1 - dy0, rx0 - dx0, rx1 - dx0)
    c_before = binary_disk_filter(d_before, r, True, *sub)
    c_after = binary_disk_filter(d_after, r, True, *sub)
    return c_before, c_after


def _weld_apply(
    sub: np.ndarray,
    target: np.ndarray,
    strength: np.ndarray,
    cov_roi: np.ndarray,
    after_roi: np.ndarray,
    src: np.ndarray,
    max_v: float,
) -> bool:
    """Lift *target* pixels of *sub* (a live view) to ``strength × body`` same-color paint.

    *body* is the stroke's own alpha (fraction of max_v, capped by the paint
    alpha), so low-opacity strokes weld at their opacity rather than to solid.
    Pixels already at or above their level are left alone; the rest are
    composited "over" with the effective stamp coverage that reaches the
    level, exactly like stamp_disk, so fillets match brush paint.
    """
    if not target.any():
        return False
    sigma = float(src[3]) / max_v
    if sigma <= 0.0:
        return False
    ink = cov_roi[after_roi]
    body = min(sigma, float(ink.max())) if ink.size else sigma
    level = strength[target] * body
    cur = cov_roi[target]
    grow = level > cur + 1e-4
    if not grow.any():
        return False
    apply = np.zeros_like(target)
    apply[target] = grow
    pix = sub[apply].astype(np.float32)  # (n, 4)
    level = level[grow]
    cur = cur[grow]
    den = _maximum(sigma - cur, 1e-6)
    a = _clip((level - cur) / den, 0.0, 1.0)[:, None]
    pix[:, :3] = src[:3] * a + pix[:, :3] * (1.0 - a)
    pix[:, 3:4] = src[3] * a + pix[:, 3:4] * (1.0 - a)
    sub[apply] = _clip(pix, 0, max_v).astype(sub.dtype)
    return True


def _weld_window(
    pixels: np.ndarray,
    wx0: int,
    wy0: int,
    wx1: int,
    wy1: int,
    sx0: int,
    sy0: int,
    sx1: int,
    sy1: int,
    before: np.ndarray,
    color: tuple[int, int, int, int],
    weld_r: int,
    mask: np.ndarray | None,
) -> bool:
    """Weld the paint just added inside segment box [sx0,sx1)×[sy0,sy1).

    *before* is a copy of the segment box (clipped to the layer) taken before
    the segment was stamped — nothing outside it changed. The window must
    reach ≥ 4·weld_r + 2 past the segment box (or the layer edge). Adds
    exactly the closing coverage the new paint causes —
    ``close(after) \\ close(before)`` — so concavities elsewhere and the
    stroke's own anti-aliased edge are left untouched.
    Returns True if any pixel changed.
    """
    r = int(weld_r)
    if r <= 0:
        return False
    region = pixels[wy0:wy1, wx0:wx1]
    wh, ww = region.shape[:2]
    lx0, ly0, lx1, ly1 = sx0 - wx0, sy0 - wy0, sx1 - wx0, sy1 - wy0
    cy0, cy1, cx0, cx1 = max(0, ly0), min(wh, ly1), max(0, lx0), min(ww, lx1)
    if cy0 >= cy1 or cx0 >= cx1:
        return False
    max_v = _max_v(pixels)
    src = np.array(color, dtype=np.float32)
    m_win = mask[wy0:wy1, wx0:wx1] > 0 if mask is not None else None

    def coverage(px: np.ndarray, y0: int, y1: int, x0: int, x1: int) -> np.ndarray:
        cov = _same_color_coverage(px, src, max_v)
        if m_win is not None:
            cov *= m_win[y0:y1, x0:x1]
        return cov

    cover_after = coverage(region, 0, wh, 0, ww)
    cover_before = cover_after.copy()
    cover_before[cy0:cy1, cx0:cx1] = coverage(before, cy0, cy1, cx0, cx1)
    after_b = cover_after >= _WELD_INK
    before_b = cover_before >= _WELD_INK
    if not np.any(after_b[cy0:cy1, cx0:cx1] & ~before_b[cy0:cy1, cx0:cx1]):
        return False

    pr = 2 * r + 2  # new coverage lies within 2r of new paint (+ 3×3 interior margin)
    roi = (max(0, ly0 - pr), min(wh, ly1 + pr), max(0, lx0 - pr), min(ww, lx1 + pr))
    ry0, ry1, rx0, rx1 = roi
    if ry0 >= ry1 or rx0 >= rx1:
        return False
    sub = region[ry0:ry1, rx0:rx1]
    m_roi = m_win[ry0:ry1, rx0:rx1] if m_win is not None else None
    # Full-resolution disk close. The chord-prefix morphology is fast enough
    # on the local window even at WELD_MAX_R (~4–16 ms); the old coarse-grid
    # path left scaly faceted fillet arcs that no amount of post-blur could
    # turn into a true circular weld.
    dbox = (max(0, ry0 - r), min(wh, ry1 + r), max(0, rx0 - r), min(ww, rx1 + r))
    c_before, c_after = _weld_closings_exact(before_b, after_b, r, roi, dbox)
    after_roi = after_b[ry0:ry1, rx0:rx1]
    # New fillet pixels (never the stroke's own AA edge) …
    fill = c_after & ~c_before & ~after_roi
    # … plus soft pixels the new fillet just enclosed (stroke / ink edges at
    # the junction), so the welded blob has no seams inside.
    newly_interior = _interior3(c_after) & ~_interior3(c_before)
    target = fill | newly_interior
    if m_roi is not None:
        target &= m_roi
    if not target.any():
        return False
    strength = _box3_mean(c_after)  # cheap AA on the fillet boundary
    strength[newly_interior] = 1.0
    return _weld_apply(sub, target, strength, cover_after[ry0:ry1, rx0:rx1], after_roi, src, max_v)


def stroke_segment_weld(
    pixels: np.ndarray,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    radius0: float,
    radius1: float,
    color: tuple[int, int, int, int],
    mask: np.ndarray | None = None,
    opacity: float = 1.0,
    wrap: bool = False,
    weld: float = 0.65,
) -> None:
    """Pressure-tapered round stroke segment with live welding.

    Stamps the segment, then evaluates a disk morphological close of radius
    ``max(radius0, radius1) * weld`` around it and adds only the coverage the
    new paint causes (fillets between it and same-color paint it touches).
    Closing is idempotent and monotone, so applying it per segment yields the
    same welded shape as one pass over the finished stroke — without the
    end-of-stroke pop, and without touching unrelated paint in the stroke's
    bounding box or hardening its anti-aliased edges.
    """
    h, w = pixels.shape[:2]
    r0 = max(0.5, float(radius0))
    r1 = max(0.5, float(radius1))
    rmax = max(r0, r1)
    weld_r = min(WELD_MAX_R, int(round(rmax * max(0.0, float(weld)))))
    if wrap and w > 0 and h > 0:
        fx, fy = _period(x0, w), _period(y0, h)
        x1 += fx - x0
        y1 += fy - y0
        x0, y0 = fx, fy

    windows: list[tuple] = []
    if weld_r > 0:
        ext = rmax + 1.5
        bx0 = int(math.floor(min(x0, x1) - ext))
        by0 = int(math.floor(min(y0, y1) - ext))
        bx1 = int(math.ceil(max(x0, x1) + ext)) + 1
        by1 = int(math.ceil(max(y0, y1) + ext)) + 1
        offsets = [(0, 0)]
        if wrap:
            xs = [0] + ([w] if bx0 < 0 else []) + ([-w] if bx1 > w else [])
            ys = [0] + ([h] if by0 < 0 else []) + ([-h] if by1 > h else [])
            offsets = [(ox, oy) for oy in ys for ox in xs]
        pad = 4 * weld_r + 2
        for ox, oy in offsets:
            sx0, sy0, sx1, sy1 = bx0 + ox, by0 + oy, bx1 + ox, by1 + oy
            cx0, cy0 = max(0, sx0), max(0, sy0)
            cx1, cy1 = min(w, sx1), min(h, sy1)
            if cx0 >= cx1 or cy0 >= cy1:
                continue
            wx0, wy0 = max(0, sx0 - pad), max(0, sy0 - pad)
            wx1, wy1 = min(w, sx1 + pad), min(h, sy1 + pad)
            snapshot = pixels[cy0:cy1, cx0:cx1].copy()
            windows.append((wx0, wy0, wx1, wy1, sx0, sy0, sx1, sy1, snapshot))

    dist = float(np.hypot(x1 - x0, y1 - y0))
    step = max(0.5, min(r0, r1) * 0.35)
    steps = max(1, int(dist / step))
    for i in range(steps + 1):
        t = i / steps
        rr = r0 + (r1 - r0) * t
        stamp_disk(
            pixels,
            x0 + (x1 - x0) * t,
            y0 + (y1 - y0) * t,
            rr,
            color,
            mask=mask,
            opacity=opacity,
            wrap=wrap,
        )

    for win in windows:
        _weld_window(pixels, *win, color, weld_r, mask)


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
    wrap: bool = False,
) -> np.ndarray | None:
    """Return a boolean mask of the connected fill region, or None if empty.

    When *key_color* is set, matching uses that color instead of the seed pixel.
    With *rgb_only*, alpha is ignored (Chebyshev distance on RGB).
    With *wrap*, the seed folds into the canvas and flood crosses opposite edges.
    """
    h, w = pixels.shape[:2]
    if wrap:
        sx, sy = wrap_pixel_coords(float(sx), float(sy), w, h)
    elif not (0 <= sx < w and 0 <= sy < h):
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
            if wrap:
                ny %= h
            elif ny < 0 or ny >= h:
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
        if wrap:
            # Bridge left↔right edges of the same row.
            if left == 0 and work[y, w - 1]:
                stack.append((y, w - 1))
            if right == w - 1 and work[y, 0]:
                stack.append((y, 0))
    return region


def flood_fill(
    pixels: np.ndarray,
    sx: int,
    sy: int,
    color: tuple[int, int, int, int],
    tolerance: int = 32,
    mask: np.ndarray | None = None,
    wrap: bool = False,
) -> None:
    """Scanline flood fill; blends when color alpha is below opaque."""
    region = flood_fill_region(
        pixels, sx, sy, tolerance=tolerance, mask=mask, wrap=wrap,
    )
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
    wrap: bool = False,
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
        wrap=wrap,
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
    wrap: bool = False,
) -> None:
    """Flood-fill a region with a tiled RGBA pattern."""
    if pattern is None or pattern.size == 0 or pattern.ndim != 3 or pattern.shape[2] < 4:
        return
    region = flood_fill_region(pixels, sx, sy, tolerance=tolerance, mask=mask, wrap=wrap)
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


_MAZE_N, _MAZE_S, _MAZE_E, _MAZE_W = 1, 2, 4, 8
_MAZE_DX = {_MAZE_E: 1, _MAZE_W: -1, _MAZE_N: 0, _MAZE_S: 0}
_MAZE_DY = {_MAZE_E: 0, _MAZE_W: 0, _MAZE_N: -1, _MAZE_S: 1}
_MAZE_OPPOSITE = {_MAZE_E: _MAZE_W, _MAZE_W: _MAZE_E, _MAZE_N: _MAZE_S, _MAZE_S: _MAZE_N}


def _region_principal_axes(
    ys: np.ndarray, xs: np.ndarray
) -> tuple[float, float, float, float, float]:
    """Centroid, major-axis angle, and extents along major/minor axes.

    Returns ``(cy, cx, theta, span_u, span_v)`` where *theta* rotates image
    ``(+x, +y)`` so *u* follows the region's long axis.
    """
    cy = float(np.mean(ys))
    cx = float(np.mean(xs))
    dy = ys.astype(np.float64) - cy
    dx = xs.astype(np.float64) - cx
    n = float(ys.size)
    cov_xx = float(np.dot(dx, dx) / n)
    cov_yy = float(np.dot(dy, dy) / n)
    cov_xy = float(np.dot(dx, dy) / n)
    if cov_xx + cov_yy < 1e-6:
        theta = 0.0
    else:
        theta = 0.5 * math.atan2(2.0 * cov_xy, cov_xx - cov_yy)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    u = dx * cos_t + dy * sin_t
    v = -dx * sin_t + dy * cos_t
    span_u = float(u.max() - u.min()) + 1.0
    span_v = float(v.max() - v.min()) + 1.0
    if span_v > span_u:
        theta += math.pi * 0.5
        span_u, span_v = span_v, span_u
    return cy, cx, theta, span_u, span_v


def _region_is_round(
    ys: np.ndarray,
    xs: np.ndarray,
    cy: float,
    cx: float,
    span_u: float,
    span_v: float,
) -> tuple[bool, float]:
    """Whether the fill looks disk-like; also returns circumradius from centroid."""
    dx = xs.astype(np.float64) - cx
    dy = ys.astype(np.float64) - cy
    radii = np.hypot(dx, dy)
    r_max = float(radii.max()) if radii.size else 0.0
    if r_max < 4.0:
        return False, r_max
    aspect = span_u / max(span_v, 1.0)
    # Filled disk ≈ 1.0 vs circumcircle; filled square ≈ 2/π ≈ 0.64.
    roundness = float(ys.size) / max(math.pi * r_max * r_max, 1.0)
    return aspect < 1.35 and roundness > 0.72, r_max


def _carve_maze(
    cols: int,
    rows: int,
    rng: random.Random,
    *,
    prefer_long_axis: float = 0.65,
) -> list[list[int]]:
    """Perfect maze via recursive backtracker; cells hold carved passage bitflags."""
    grid = [[0 for _ in range(cols)] for _ in range(rows)]
    visited = [[False] * cols for _ in range(rows)]
    sy = rng.randrange(rows)
    sx = rng.randrange(cols)
    stack: list[tuple[int, int]] = [(sy, sx)]
    visited[sy][sx] = True
    bias = max(0.0, min(1.0, float(prefer_long_axis)))
    while stack:
        cy, cx = stack[-1]
        neighbors: list[tuple[int, int, int]] = []
        for direction in (_MAZE_N, _MAZE_S, _MAZE_E, _MAZE_W):
            ny = cy + _MAZE_DY[direction]
            nx = cx + _MAZE_DX[direction]
            if 0 <= ny < rows and 0 <= nx < cols and not visited[ny][nx]:
                neighbors.append((direction, ny, nx))
        if not neighbors:
            stack.pop()
            continue
        if len(neighbors) == 1 or bias <= 0.0 or abs(bias - 0.5) < 1e-6:
            direction, ny, nx = neighbors[rng.randrange(len(neighbors))]
        else:
            weights = [
                bias if d in (_MAZE_E, _MAZE_W) else (1.0 - bias)
                for d, _, _ in neighbors
            ]
            total = sum(weights)
            pick = rng.random() * total
            acc = 0.0
            direction, ny, nx = neighbors[-1]
            for (d, nny, nnx), w in zip(neighbors, weights):
                acc += w
                if pick <= acc:
                    direction, ny, nx = d, nny, nnx
                    break
        grid[cy][cx] |= direction
        grid[ny][nx] |= _MAZE_OPPOSITE[direction]
        visited[ny][nx] = True
        stack.append((ny, nx))
    return grid


def _rasterize_maze_walls(
    grid: list[list[int]],
    rows: int,
    cols: int,
    bh: int,
    bw: int,
    pitch: int,
    wall_t: int,
) -> np.ndarray:
    """Boolean wall mask for a *bh*×*bw* bbox; True = wall."""
    walls = np.ones((bh, bw), dtype=bool)
    room = max(1, pitch - wall_t)
    for r in range(rows):
        for c in range(cols):
            y = wall_t + r * pitch
            x = wall_t + c * pitch
            if y >= bh or x >= bw:
                continue
            y2 = min(bh, y + room)
            x2 = min(bw, x + room)
            walls[y:y2, x:x2] = False
            cell = grid[r][c]
            if cell & _MAZE_E and c + 1 < cols:
                walls[y:y2, x + room : min(bw, x + pitch)] = False
            if cell & _MAZE_S and r + 1 < rows:
                walls[y + room : min(bh, y + pitch), x:x2] = False
    return walls


def _polar_ring_counts(num_rings: int, ring_width: float, cell_size: float) -> list[int]:
    """Angular cell counts per ring; outer rings get more cells for even corridor width.

    Counts are chosen independently per ring (not forced to share spokes) so
    radial walls stagger instead of forming continuous diameter lines.
    """
    counts: list[int] = []
    for r in range(num_rings):
        circ = 2.0 * math.pi * (r + 0.5) * ring_width
        n = max(6, int(round(circ / max(cell_size, 1.0))))
        counts.append(n)
    return counts


def _carve_polar_maze(
    counts: list[int], rng: random.Random
) -> tuple[list[list[bool]], list[list[bool]], list[bool]]:
    """Perfect polar maze.

    Returns ``(angular, inward, outer_rim)``:
    - ``angular[r][i]``: wall between cell ``i`` and ``i+1`` on ring ``r``
    - ``inward[r][i]``: wall on the inner edge of cell ``(r, i)`` (toward hub / parent)
    - ``outer_rim[i]``: outer boundary wall on the last ring
    """
    num_rings = len(counts)
    angular = [[True] * counts[r] for r in range(num_rings)]
    inward = [[True] * counts[r] for r in range(num_rings)]
    outer_rim = [True] * counts[-1]

    def parent_of(ring: int, idx: int) -> int:
        return min(counts[ring - 1] - 1, idx * counts[ring - 1] // counts[ring])

    def neighbors(ring: int, idx: int) -> list[tuple[int, int, str, int, int]]:
        """(nring, nidx, kind, wall_ring, wall_idx). kind: ang|in|out."""
        out: list[tuple[int, int, str, int, int]] = []
        n = counts[ring]
        out.append((ring, (idx + 1) % n, "ang", ring, idx))
        out.append((ring, (idx - 1) % n, "ang", ring, (idx - 1) % n))
        if ring > 0:
            out.append((ring - 1, parent_of(ring, idx), "in", ring, idx))
        if ring + 1 < num_rings:
            n_out = counts[ring + 1]
            for child in range(n_out):
                if parent_of(ring + 1, child) == idx:
                    out.append((ring + 1, child, "in", ring + 1, child))
        return out

    visited = [[False] * counts[r] for r in range(num_rings)]
    start_r, start_i = num_rings - 1, rng.randrange(counts[-1])
    stack: list[tuple[int, int]] = [(start_r, start_i)]
    visited[start_r][start_i] = True
    while stack:
        ring, idx = stack[-1]
        opts: list[tuple[int, int, str, int, int]] = []
        seen: set[tuple[int, int]] = set()
        for item in neighbors(ring, idx):
            key = (item[0], item[1])
            if key in seen or visited[item[0]][item[1]]:
                continue
            seen.add(key)
            opts.append(item)
        if not opts:
            stack.pop()
            continue
        nr, ni, kind, wr, wi = opts[rng.randrange(len(opts))]
        if kind == "ang":
            angular[wr][wi] = False
        else:
            inward[wr][wi] = False
        visited[nr][ni] = True
        stack.append((nr, ni))

    # Open hub: carve a few inward walls on the innermost ring.
    inner_n = counts[0]
    openings = max(1, inner_n // 4)
    for i in rng.sample(range(inner_n), openings):
        inward[0][i] = False
    # Entrance on the outer rim.
    outer_rim[rng.randrange(counts[-1])] = False
    return angular, inward, outer_rim


def _sample_polar_walls(
    ys: np.ndarray,
    xs: np.ndarray,
    cy: float,
    cx: float,
    radius: float,
    hub: float,
    counts: list[int],
    angular: list[list[bool]],
    inward: list[list[bool]],
    outer_rim: list[bool],
    wall_t: float,
) -> np.ndarray:
    """True where region pixels land on polar maze walls."""
    num_rings = len(counts)
    ring_width = (radius - hub) / max(num_rings, 1)
    dx = xs.astype(np.float64) - cx
    dy = ys.astype(np.float64) - cy
    rr = np.hypot(dx, dy)
    theta = np.arctan2(dy, dx)
    theta = np.where(theta < 0.0, theta + 2.0 * math.pi, theta)

    walls = np.zeros(ys.shape, dtype=bool)
    in_maze = rr >= hub
    maze_r = np.maximum(0.0, rr - hub)
    ring_f = maze_r / max(ring_width, 1e-6)
    ring_i = np.minimum(num_rings - 1, np.floor(ring_f).astype(np.int32))
    local_r = maze_r - ring_i.astype(np.float64) * ring_width

    for r in range(num_rings):
        mask_r = in_maze & (ring_i == r)
        if not np.any(mask_r):
            continue
        n = counts[r]
        ang_arr = np.asarray(angular[r], dtype=bool)
        in_arr = np.asarray(inward[r], dtype=bool)
        th = theta[mask_r]
        ang_f = th / (2.0 * math.pi) * n
        ang_i = np.floor(ang_f).astype(np.int32) % n
        ang_pos = ang_f - np.floor(ang_f)

        arc = 2.0 * math.pi * np.maximum(rr[mask_r], 1.0) / max(n, 1)
        ang_frac = np.clip(wall_t / np.maximum(arc, 1e-6), 0.015, 0.22)

        hit = np.zeros(ang_i.shape, dtype=bool)
        low = ang_pos < ang_frac
        high = ang_pos > 1.0 - ang_frac
        if np.any(low):
            hit[low] |= ang_arr[(ang_i[low] - 1) % n]
        if np.any(high):
            hit[high] |= ang_arr[ang_i[high]]

        near_in = local_r[mask_r] < wall_t
        if np.any(near_in):
            hit[near_in] |= in_arr[ang_i[near_in]]

        if r == num_rings - 1:
            near_out = local_r[mask_r] >= (ring_width - wall_t)
            if np.any(near_out):
                rim = np.asarray(outer_rim, dtype=bool)
                hit[near_out] |= rim[ang_i[near_out]]

        walls[mask_r] = hit

    return walls


def _maze_walls_rect(
    ys: np.ndarray,
    xs: np.ndarray,
    cy: float,
    cx: float,
    theta: float,
    span_u: float,
    span_v: float,
    cell_size: float,
    rng: random.Random,
) -> np.ndarray:
    """Oriented rectangular maze wall mask for region pixels."""
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    dx = xs.astype(np.float64) - cx
    dy = ys.astype(np.float64) - cy
    u = dx * cos_t + dy * sin_t
    v = -dx * sin_t + dy * cos_t
    u0 = float(u.min())
    v0 = float(v.min())
    lu = u - u0
    lv = v - v0

    pitch = max(3, int(round(cell_size)))
    wall_t = max(1, min(pitch // 4, pitch - 2))
    mw = max(pitch + wall_t, int(math.ceil(span_u)))
    mh = max(pitch + wall_t, int(math.ceil(span_v)))
    cols = max(1, (mw - wall_t) // pitch)
    rows = max(1, (mh - wall_t) // pitch)
    aspect = span_u / max(span_v, 1.0)
    prefer = 0.5 + 0.35 * min(1.0, max(0.0, (aspect - 1.0) / 2.0))
    grid = _carve_maze(cols, rows, rng, prefer_long_axis=prefer)
    walls_local = _rasterize_maze_walls(grid, rows, cols, mh, mw, pitch, wall_t)
    iu = np.clip(np.floor(lu + 0.5).astype(np.int32), 0, mw - 1)
    iv = np.clip(np.floor(lv + 0.5).astype(np.int32), 0, mh - 1)
    return walls_local[iv, iu]


def _maze_walls_polar(
    ys: np.ndarray,
    xs: np.ndarray,
    cy: float,
    cx: float,
    radius: float,
    cell_size: float,
    rng: random.Random,
) -> np.ndarray:
    """Circular (polar) maze wall mask for region pixels."""
    pitch = max(3.0, float(cell_size))
    wall_t = float(max(1, min(int(pitch // 4), int(pitch) - 2)))
    hub = max(wall_t * 2.0, pitch * 0.55)
    usable = max(pitch * 2.0, radius - hub)
    num_rings = max(2, int(usable / pitch))
    ring_width = usable / num_rings
    maze_r = hub + usable
    counts = _polar_ring_counts(num_rings, ring_width, pitch)
    angular, inward, outer_rim = _carve_polar_maze(counts, rng)
    return _sample_polar_walls(
        ys, xs, cy, cx, maze_r, hub, counts, angular, inward, outer_rim, wall_t
    )


def flood_fill_maze(
    pixels: np.ndarray,
    sx: int,
    sy: int,
    color: tuple[int, int, int, int],
    tolerance: int = 32,
    mask: np.ndarray | None = None,
    cell_size: float = 8.0,
    corridor_color: tuple[int, int, int, int] | None = None,
    wrap: bool = False,
) -> None:
    """Flood-fill a region with a perfect (solvable) maze.

    Round regions get a circular (polar) maze of concentric rings and radial
    walls; elongated regions get an oriented rectangular maze. Walls use
    *color*; corridors use *corridor_color* (default white).
    """
    region = flood_fill_region(pixels, sx, sy, tolerance=tolerance, mask=mask, wrap=wrap)
    if region is None:
        return
    ys, xs = np.nonzero(region)
    if ys.size == 0:
        return

    cy, cx, theta, span_u, span_v = _region_principal_axes(ys, xs)
    roundish, radius = _region_is_round(ys, xs, cy, cx, span_u, span_v)
    rng = random.Random()
    if roundish:
        local = _maze_walls_polar(ys, xs, cy, cx, radius, cell_size, rng)
    else:
        local = _maze_walls_rect(
            ys, xs, cy, cx, theta, span_u, span_v, cell_size, rng
        )

    max_v = _max_v(pixels)
    wall_src = np.asarray(color, dtype=np.float32)
    if corridor_color is None:
        path_src = np.array([max_v, max_v, max_v, wall_src[3]], dtype=np.float32)
    else:
        path_src = np.asarray(corridor_color, dtype=np.float32)
    src = np.where(local[:, None], wall_src[None, :], path_src[None, :])

    a = src[..., 3] / max_v
    if float(np.min(a)) >= 0.999:
        pixels[ys, xs] = np.clip(src, 0, max_v).astype(pixels.dtype)
        return
    if float(np.max(a)) <= 0.0:
        return
    dest = pixels[ys, xs].astype(np.float32)
    out = dest.copy()
    out[..., :3] = src[..., :3] * a[..., None] + dest[..., :3] * (1.0 - a[..., None])
    out[..., 3] = src[..., 3] + dest[..., 3] * (1.0 - a)
    pixels[ys, xs] = np.clip(out, 0, max_v).astype(pixels.dtype)


def _puzzle_edge_params(
    rows: int, cols: int, rng: random.Random
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Random interlocking tab direction (±1) and center along each shared edge."""
    v_dir = np.empty((rows, max(cols - 1, 0)), dtype=np.int8)
    h_dir = np.empty((max(rows - 1, 0), cols), dtype=np.int8)
    v_ctr = np.empty((rows, max(cols - 1, 0)), dtype=np.float64)
    h_ctr = np.empty((max(rows - 1, 0), cols), dtype=np.float64)
    for r in range(rows):
        for c in range(cols - 1):
            v_dir[r, c] = -1 if rng.random() < 0.5 else 1
            v_ctr[r, c] = rng.uniform(0.46, 0.54)
    for r in range(rows - 1):
        for c in range(cols):
            h_dir[r, c] = -1 if rng.random() < 0.5 else 1
            h_ctr[r, c] = rng.uniform(0.46, 0.54)
    return v_dir, h_dir, v_ctr, h_ctr


def _stroke_poly(mask: np.ndarray, xs: list[float], ys: list[float], radius: float) -> None:
    """Stamp a thick polyline into a boolean mask."""
    h, w = mask.shape
    r = max(0.75, float(radius))
    r2 = r * r
    ri = max(1, int(math.ceil(r)))
    for i in range(len(xs) - 1):
        x0, y0 = xs[i], ys[i]
        x1, y1 = xs[i + 1], ys[i + 1]
        seg = math.hypot(x1 - x0, y1 - y0)
        steps = max(1, int(seg * 2.5))
        for s in range(steps + 1):
            t = s / steps
            cx = x0 + (x1 - x0) * t
            cy = y0 + (y1 - y0) * t
            ix = int(round(cx))
            iy = int(round(cy))
            for dy in range(-ri, ri + 1):
                yy = iy + dy
                if yy < 0 or yy >= h:
                    continue
                for dx in range(-ri, ri + 1):
                    xx = ix + dx
                    if xx < 0 or xx >= w:
                        continue
                    if dx * dx + dy * dy <= r2:
                        mask[yy, xx] = True


def _jigsaw_edge_poly(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    flip: int,
    center: float,
    amp: float,
    n_samples: int,
    *,
    clearance: float = 2.0,
) -> tuple[list[float], list[float]]:
    """Polyline for one jigsaw seam from A→B with a necked circular knob.

    The whole knob (including the bulb's along-edge extent) stays strictly
    inside the open segment so seams never cross at corners.
    """
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return [ax, bx], [ay, by]
    ux, uy = dx / length, dy / length
    nx, ny = -uy * flip, ux * flip

    # Undercut knob. Along-edge the circle spans ±R from its center, so the
    # tab must be inset by at least R from both corners.
    max_r = max(0.0, (length - 2.0 * clearance) * 0.5)
    R = min(float(amp), length * 0.17, max_r)
    if R < 2.0:
        return [ax, bx], [ay, by]

    b = R * 0.70
    a = math.sqrt(max(1e-6, R * R - b * b))
    # Inset so the full bulb (extent R, not just neck a) clears both corners.
    margin = R + clearance
    ct = float(np.clip(center, 0.42, 0.58)) * length
    ct = min(max(ct, margin), length - margin)
    # If clamping pushed the bulb into a corner, drop the tab.
    if ct - R < clearance or ct + R > length - clearance:
        return [ax, bx], [ay, by]

    def pt(along: float, perp: float = 0.0) -> tuple[float, float]:
        return ax + ux * along + nx * perp, ay + uy * along + ny * perp

    xs: list[float] = []
    ys: list[float] = []

    n0 = max(4, int(n_samples * max(ct - a, 0.0) / max(length, 1e-6)))
    for i in range(n0 + 1):
        along = (ct - a) * (i / max(n0, 1))
        x, y = pt(along)
        xs.append(x)
        ys.append(y)

    a0 = math.atan2(-b, -a)
    a1 = math.atan2(-b, a)
    d_ccw = (a1 - a0) % (2.0 * math.pi)
    d_cw = d_ccw - 2.0 * math.pi
    tip = 0.5 * math.pi

    def mid_dist(delta: float) -> float:
        mid = a0 + delta * 0.5
        return abs((mid - tip + math.pi) % (2.0 * math.pi) - math.pi)

    delta = d_ccw if mid_dist(d_ccw) <= mid_dist(d_cw) else d_cw

    n_arc = max(20, int(n_samples * 0.55))
    for i in range(1, n_arc):
        u = i / n_arc
        ang = a0 + delta * u
        along = ct + math.cos(ang) * R
        perp = b + math.sin(ang) * R
        # Keep every sample inside the edge slab — never spill past corners.
        along = min(max(along, clearance), length - clearance)
        if perp < 0.0:
            continue
        x, y = pt(along, perp)
        xs.append(x)
        ys.append(y)

    n1 = max(4, int(n_samples * max(length - (ct + a), 0.0) / max(length, 1e-6)))
    for i in range(n1 + 1):
        along = (ct + a) + (length - (ct + a)) * (i / max(n1, 1))
        x, y = pt(along)
        xs.append(x)
        ys.append(y)

    xs[0], ys[0] = ax, ay
    xs[-1], ys[-1] = bx, by
    return xs, ys


def _rasterize_jigsaw(
    bh: int,
    bw: int,
    rows: int,
    cols: int,
    piece_x: float,
    piece_y: float,
    v_dir: np.ndarray,
    h_dir: np.ndarray,
    v_ctr: np.ndarray,
    h_ctr: np.ndarray,
    line_w: float,
) -> np.ndarray:
    """Boolean outline mask for a full jigsaw grid covering *bh*×*bw*."""
    mask = np.zeros((bh, bw), dtype=bool)
    # Tip reaches ~1.7R; keep tabs from neighbouring edges from meeting.
    amp = min(0.18 * piece_x, 0.18 * piece_y)
    clearance = max(line_w * 2.0, 2.5)
    samples_v = max(36, int(piece_y * 4))
    samples_h = max(36, int(piece_x * 4))

    for r in range(rows):
        y0 = r * piece_y
        y1 = (r + 1) * piece_y
        for c in range(cols - 1):
            x = (c + 1) * piece_x
            xs, ys = _jigsaw_edge_poly(
                x,
                y0,
                x,
                y1,
                int(v_dir[r, c]),
                float(v_ctr[r, c]),
                amp,
                samples_v,
                clearance=clearance,
            )
            _stroke_poly(mask, xs, ys, line_w)

    for r in range(rows - 1):
        y = (r + 1) * piece_y
        for c in range(cols):
            x0 = c * piece_x
            x1 = (c + 1) * piece_x
            xs, ys = _jigsaw_edge_poly(
                x0,
                y,
                x1,
                y,
                int(h_dir[r, c]),
                float(h_ctr[r, c]),
                amp,
                samples_h,
                clearance=clearance,
            )
            _stroke_poly(mask, xs, ys, line_w)

    _stroke_poly(mask, [0.0, float(max(bw - 1, 0))], [0.0, 0.0], line_w)
    _stroke_poly(
        mask,
        [0.0, float(max(bw - 1, 0))],
        [float(max(bh - 1, 0)), float(max(bh - 1, 0))],
        line_w,
    )
    _stroke_poly(mask, [0.0, 0.0], [0.0, float(max(bh - 1, 0))], line_w)
    _stroke_poly(
        mask,
        [float(max(bw - 1, 0)), float(max(bw - 1, 0))],
        [0.0, float(max(bh - 1, 0))],
        line_w,
    )
    return mask


def _jigsaw_arc_poly(
    cx: float,
    cy: float,
    radius: float,
    theta0: float,
    theta1: float,
    flip: int,
    center: float,
    amp: float,
    n_samples: int,
    *,
    clearance: float = 2.0,
) -> tuple[list[float], list[float]]:
    """Jigsaw seam along a circular arc; *flip* +1 = tab outward (larger r)."""
    dtheta = theta1 - theta0
    if dtheta <= 1e-9:
        dtheta += 2.0 * math.pi
    if radius < 2.0 or dtheta < 1e-6:
        return [], []
    arc_len = radius * dtheta
    max_r = max(0.0, (arc_len - 2.0 * clearance) * 0.5)
    R = min(float(amp), arc_len * 0.17, max_r, radius * 0.35)
    if R < 2.0:
        # Flat arc.
        n = max(12, int(n_samples))
        xs = [cx + radius * math.cos(theta0 + dtheta * (i / n)) for i in range(n + 1)]
        ys = [cy + radius * math.sin(theta0 + dtheta * (i / n)) for i in range(n + 1)]
        return xs, ys

    b = R * 0.70
    a = math.sqrt(max(1e-6, R * R - b * b))
    margin = R + clearance
    # Honour the caller's tab center; only clamp so the full bulb clears corners.
    ct = float(center) * arc_len
    ct = min(max(ct, margin), arc_len - margin)
    if ct - R < clearance or ct + R > arc_len - clearance:
        n = max(12, int(n_samples))
        xs = [cx + radius * math.cos(theta0 + dtheta * (i / n)) for i in range(n + 1)]
        ys = [cy + radius * math.sin(theta0 + dtheta * (i / n)) for i in range(n + 1)]
        return xs, ys

    def pt(along: float, perp: float = 0.0) -> tuple[float, float]:
        th = theta0 + (along / radius)
        rr = radius + flip * perp
        return cx + rr * math.cos(th), cy + rr * math.sin(th)

    xs: list[float] = []
    ys: list[float] = []

    n0 = max(4, int(n_samples * max(ct - a, 0.0) / max(arc_len, 1e-6)))
    for i in range(n0 + 1):
        along = (ct - a) * (i / max(n0, 1))
        x, y = pt(along)
        xs.append(x)
        ys.append(y)

    a0 = math.atan2(-b, -a)
    a1 = math.atan2(-b, a)
    d_ccw = (a1 - a0) % (2.0 * math.pi)
    d_cw = d_ccw - 2.0 * math.pi
    tip = 0.5 * math.pi

    def mid_dist(delta: float) -> float:
        mid = a0 + delta * 0.5
        return abs((mid - tip + math.pi) % (2.0 * math.pi) - math.pi)

    delta = d_ccw if mid_dist(d_ccw) <= mid_dist(d_cw) else d_cw
    n_arc = max(20, int(n_samples * 0.55))
    for i in range(1, n_arc):
        u = i / n_arc
        ang = a0 + delta * u
        along = ct + math.cos(ang) * R
        perp = b + math.sin(ang) * R
        along = min(max(along, clearance), arc_len - clearance)
        if perp < 0.0:
            continue
        x, y = pt(along, perp)
        xs.append(x)
        ys.append(y)

    n1 = max(4, int(n_samples * max(arc_len - (ct + a), 0.0) / max(arc_len, 1e-6)))
    for i in range(n1 + 1):
        along = (ct + a) + (arc_len - (ct + a)) * (i / max(n1, 1))
        x, y = pt(along)
        xs.append(x)
        ys.append(y)

    xs[0], ys[0] = pt(0.0)
    xs[-1], ys[-1] = pt(arc_len)
    return xs, ys


def _puzzle_ring_counts(
    num_rings: int, hub: float, ring_width: float, cell_size: float
) -> list[int]:
    """Piece counts per ring; outer rings get more slices for even piece size."""
    counts: list[int] = []
    # Leave room on each concentric arc for a knob plus corner clearances.
    min_arc = max(cell_size * 0.85, 0.45 * ring_width + 10.0)
    for r in range(num_rings):
        mid_r = hub + (r + 0.5) * ring_width
        circ = 2.0 * math.pi * mid_r
        n = max(4 if r == 0 else 6, int(round(circ / max(cell_size, 1.0))))
        if n % 2:
            n += 1
        max_n = max(6, int(circ / min_arc))
        if max_n % 2:
            max_n -= 1
        n = min(n, max(6, max_n))
        if counts:
            prev = counts[-1]
            n = max(n, prev)
            # Prefer an integer multiple of the inner ring when it still fits.
            mult = max(1, int(round(n / prev)))
            cand = prev * mult
            if cand <= max(6, max_n):
                n = cand
            elif prev <= max(6, max_n):
                n = prev
        counts.append(n)
    return counts


def _angle_diff(a: float, b: float) -> float:
    """Smallest absolute difference between two angles."""
    return abs((a - b + math.pi) % (2.0 * math.pi) - math.pi)


def _rasterize_jigsaw_polar(
    cy: float,
    cx: float,
    radius: float,
    cell_size: float,
    rng: random.Random,
    line_w: float,
) -> tuple[np.ndarray, int, int]:
    """Polar jigsaw outline mask and its top-left origin in image coords.

    Rings stay angularly staggered. Concentric seams follow the *outer* ring's
    divisions; tabs are placed only on arc spans that clear every T-junction
    from the inner ring, so radials never cut through a knob.
    """
    pad = max(2, int(math.ceil(line_w)) + 2)
    x0 = int(math.floor(cx - radius)) - pad
    y0 = int(math.floor(cy - radius)) - pad
    x1 = int(math.ceil(cx + radius)) + pad
    y1 = int(math.ceil(cy + radius)) + pad
    bw = max(1, x1 - x0)
    bh = max(1, y1 - y0)
    mask = np.zeros((bh, bw), dtype=bool)

    pitch = max(18.0, float(cell_size))
    hub = max(line_w * 3.0, pitch * 0.28)
    usable = max(pitch * 2.0, radius - hub)
    num_rings = max(2, int(usable / pitch))
    ring_width = usable / num_rings
    outer_r = hub + num_rings * ring_width
    counts = _puzzle_ring_counts(num_rings, hub, ring_width, pitch)
    phases = [
        (math.pi / counts[r]) if (r % 2) else 0.0 for r in range(num_rings)
    ]
    amp = min(0.15 * ring_width, 0.13 * pitch)
    clearance = max(line_w * 2.0, 2.5)

    def stroke(xs: list[float], ys: list[float]) -> None:
        if not xs:
            return
        _stroke_poly(mask, [x - x0 for x in xs], [y - y0 for y in ys], line_w)

    def ring_angles(ring: int) -> list[float]:
        n = counts[ring]
        phase = phases[ring]
        return [phase + 2.0 * math.pi * j / n for j in range(n)]

    def flat_arc(r: float, t0: float, t1: float) -> None:
        dtheta = t1 - t0
        if dtheta <= 1e-9:
            dtheta += 2.0 * math.pi
        n = max(12, int(r * dtheta * 3))
        stroke(
            [cx + r * math.cos(t0 + dtheta * (i / n)) for i in range(n + 1)],
            [cy + r * math.sin(t0 + dtheta * (i / n)) for i in range(n + 1)],
        )

    # Concentric seams owned by the outer ring. Skip / shift tabs that would
    # land on an inner-ring radial so every T-junction meets a flat arc.
    for b in range(1, num_rings):
        r_seam = hub + b * ring_width
        n = counts[b]
        phase = phases[b]
        inner_angles = ring_angles(b - 1)
        # Bulb spans ±~R along the arc; keep junctions outside that slab.
        tab_half = (amp * 1.35 + line_w) / max(r_seam, 1.0)
        corner_pad = (amp + clearance) / max(r_seam, 1.0)

        for j in range(n):
            t0 = phase + 2.0 * math.pi * j / n
            t1 = phase + 2.0 * math.pi * (j + 1) / n
            dtheta = t1 - t0
            if dtheta <= 1e-9:
                dtheta += 2.0 * math.pi
            lo = t0 + corner_pad
            hi = t0 + dtheta - corner_pad
            flip = -1 if rng.random() < 0.5 else 1

            def clear_of_radials(theta: float) -> bool:
                return all(
                    _angle_diff(theta, a) >= tab_half for a in inner_angles
                )

            # Prefer a near-mid tab; search the open span if mid conflicts.
            ctr_theta: float | None = None
            mid = t0 + 0.5 * dtheta
            candidates = [mid]
            if hi > lo:
                steps = max(6, int(dtheta / max(tab_half * 0.5, 0.02)))
                for i in range(steps + 1):
                    candidates.append(lo + (hi - lo) * (i / max(steps, 1)))
            candidates.sort(key=lambda t: abs(t - mid))
            for cand in candidates:
                if lo <= cand <= hi and clear_of_radials(cand):
                    ctr_theta = cand
                    break

            samples = max(28, int(r_seam * dtheta * 3))
            if ctr_theta is None:
                flat_arc(r_seam, t0, t1)
            else:
                ctr = (ctr_theta - t0) / dtheta
                # Re-check after the same corner clamp _jigsaw_arc_poly applies.
                arc_len = r_seam * dtheta
                R_est = min(
                    float(amp),
                    arc_len * 0.17,
                    max(0.0, (arc_len - 2.0 * clearance) * 0.5),
                    r_seam * 0.35,
                )
                if R_est >= 2.0:
                    margin = R_est + clearance
                    ct = min(max(ctr * arc_len, margin), arc_len - margin)
                    final = t0 + ct / r_seam
                    if not clear_of_radials(final):
                        flat_arc(r_seam, t0, t1)
                        continue
                    ctr = ct / arc_len
                xs, ys = _jigsaw_arc_poly(
                    cx,
                    cy,
                    r_seam,
                    t0,
                    t1,
                    flip,
                    ctr,
                    amp,
                    samples,
                    clearance=clearance,
                )
                stroke(xs, ys)

    # Radial seams — full ring span; tabs above never sit on these angles.
    for k in range(num_rings):
        r_in = hub + k * ring_width
        r_out = hub + (k + 1) * ring_width
        for theta in ring_angles(k):
            ax = cx + r_in * math.cos(theta)
            ay = cy + r_in * math.sin(theta)
            bx = cx + r_out * math.cos(theta)
            by = cy + r_out * math.sin(theta)
            flip = -1 if rng.random() < 0.5 else 1
            ctr = rng.uniform(0.47, 0.53)
            samples = max(24, int((r_out - r_in) * 4))
            xs, ys = _jigsaw_edge_poly(
                ax,
                ay,
                bx,
                by,
                flip,
                ctr,
                amp,
                samples,
                clearance=clearance,
            )
            stroke(xs, ys)

    # Flat hub and outer rim.
    hub_samples = max(48, int(2.0 * math.pi * hub))
    stroke(
        [cx + hub * math.cos(2.0 * math.pi * i / hub_samples) for i in range(hub_samples + 1)],
        [cy + hub * math.sin(2.0 * math.pi * i / hub_samples) for i in range(hub_samples + 1)],
    )
    rim_samples = max(64, int(2.0 * math.pi * outer_r))
    stroke(
        [cx + outer_r * math.cos(2.0 * math.pi * i / rim_samples) for i in range(rim_samples + 1)],
        [cy + outer_r * math.sin(2.0 * math.pi * i / rim_samples) for i in range(rim_samples + 1)],
    )
    return mask, x0, y0


def flood_fill_puzzle(
    pixels: np.ndarray,
    sx: int,
    sy: int,
    color: tuple[int, int, int, int],
    tolerance: int = 32,
    mask: np.ndarray | None = None,
    cell_size: float = 24.0,
    corridor_color: tuple[int, int, int, int] | None = None,
    wrap: bool = False,
) -> None:
    """Flood-fill a region with interlocking procedural jigsaw pieces.

    Round regions get a circular (polar) puzzle of concentric rings; other
    shapes get a rectangular grid. Outlines use *color*; fills use
    *corridor_color* (default white). *cell_size* is approximate piece size.
    """
    region = flood_fill_region(pixels, sx, sy, tolerance=tolerance, mask=mask, wrap=wrap)
    if region is None:
        return
    ys, xs = np.nonzero(region)
    if ys.size == 0:
        return

    rng = random.Random()
    cy, cx, _theta, span_u, span_v = _region_principal_axes(ys, xs)
    roundish, radius = _region_is_round(ys, xs, cy, cx, span_u, span_v)

    if roundish:
        pitch = max(18.0, float(cell_size))
        line_w = max(1.0, pitch * 0.045)
        outline, ox, oy = _rasterize_jigsaw_polar(
            cy, cx, radius, pitch, rng, line_w
        )
        # Clip sample indices to the polar mask.
        ly = ys - oy
        lx = xs - ox
        h, w = outline.shape
        valid = (ly >= 0) & (ly < h) & (lx >= 0) & (lx < w)
        local = np.zeros(ys.shape, dtype=bool)
        local[valid] = outline[ly[valid], lx[valid]]
    else:
        y0i = int(ys.min())
        x0i = int(xs.min())
        bh = int(ys.max()) - y0i + 1
        bw = int(xs.max()) - x0i + 1

        target = max(22.0, float(cell_size))
        cols = max(1, int(round(bw / target)))
        rows = max(1, int(round(bh / target)))
        piece_x = bw / cols
        piece_y = bh / rows

        v_dir, h_dir, v_ctr, h_ctr = _puzzle_edge_params(rows, cols, rng)
        line_w = max(1.0, min(piece_x, piece_y) * 0.045)
        outline = _rasterize_jigsaw(
            bh, bw, rows, cols, piece_x, piece_y, v_dir, h_dir, v_ctr, h_ctr, line_w
        )
        local = outline[ys - y0i, xs - x0i]

    max_v = _max_v(pixels)
    wall_src = np.asarray(color, dtype=np.float32)
    if corridor_color is None:
        path_src = np.array([max_v, max_v, max_v, wall_src[3]], dtype=np.float32)
    else:
        path_src = np.asarray(corridor_color, dtype=np.float32)
    src = np.where(local[:, None], wall_src[None, :], path_src[None, :])

    a = src[..., 3] / max_v
    if float(np.min(a)) >= 0.999:
        pixels[ys, xs] = np.clip(src, 0, max_v).astype(pixels.dtype)
        return
    if float(np.max(a)) <= 0.0:
        return
    dest = pixels[ys, xs].astype(np.float32)
    out = dest.copy()
    out[..., :3] = src[..., :3] * a[..., None] + dest[..., :3] * (1.0 - a[..., None])
    out[..., 3] = src[..., 3] + dest[..., 3] * (1.0 - a)
    pixels[ys, xs] = np.clip(out, 0, max_v).astype(pixels.dtype)


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
    wrap: bool = False,
) -> None:
    """Soft disc shaded as a lit sphere (bevel + specular highlight)."""
    h, w = pixels.shape[:2]
    r = max(1.0, radius)
    if wrap:
        for px, py in stamp_centers(x, y, r + 1.5, w, h, True):
            stamp_disk_3d(
                pixels,
                px,
                py,
                radius,
                color,
                mask=mask,
                depth=depth,
                highlight=highlight,
                bevel=bevel,
                wrap=False,
            )
        return
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
    wrap: bool = False,
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
            wrap=wrap,
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
    wrap: bool = False,
) -> np.ndarray:
    """Capture a square float tip centered on (x, y) for smudging."""
    r = max(1, int(np.ceil(radius)))
    size = 2 * r + 1
    tip = np.zeros((size, size, 4), dtype=np.float32)
    h, w = pixels.shape[:2]
    if wrap and w > 0 and h > 0:
        cx = int(round(_period(x, w)))
        cy = int(round(_period(y, h)))
        iy = (np.arange(size, dtype=np.int32) + cy - r) % h
        ix = (np.arange(size, dtype=np.int32) + cx - r) % w
        tip[:, :] = pixels[np.ix_(iy, ix)].astype(np.float32)
        return tip
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
    wrap: bool = False,
    *,
    pickup: bool = True,
) -> None:
    """Smudge: blend *tip* into the canvas, then pick canvas color back into *tip* (in-place)."""
    r = max(0.5, float(radius))
    strength = float(np.clip(strength, 0.0, 1.0))
    if strength <= 0.0 or tip.size == 0:
        return
    h, w = pixels.shape[:2]
    if wrap:
        centers = stamp_centers(x, y, r + 1.5, w, h, True)
        for i, (px, py) in enumerate(centers):
            smear_stamp(
                pixels,
                px,
                py,
                radius,
                tip,
                strength=strength,
                mask=mask,
                wrap=False,
                pickup=(pickup and i == 0),
            )
        return
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

    yy, xx = _grid(y0, y1, x0, x1)
    dist = np.sqrt((xx + 0.5 - x) ** 2 + (yy + 0.5 - y) ** 2)
    falloff = _clip(1.0 - dist / r, 0.0, 1.0).astype(np.float32)
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
    if not pickup:
        return
    # Pick up canvas into tip (slightly stronger pickup keeps the smear wet)
    pickup_w = np.clip(strength * 1.15, 0.0, 1.0) * falloff[..., None]
    tip[tip_y0:tip_y1, tip_x0:tip_x1] = src * (1.0 - pickup_w) + dest * pickup_w


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
    wrap: bool = False,
) -> None:
    dist = float(np.hypot(x1 - x0, y1 - y0))
    steps = max(1, int(dist / max(0.5, radius * 0.4)))
    op = float(np.clip(opacity, 0.0, 1.0))
    if op <= 0.0:
        return
    key = (int(key_color[0]), int(key_color[1]), int(key_color[2]), 255)
    for s in range(steps + 1):
        t = s / steps
        stamp_disk(
            pixels,
            x0 + (x1 - x0) * t,
            y0 + (y1 - y0) * t,
            radius,
            (0, 0, 0, 0),
            erase=True,
            mask=mask,
            key_color=key,
            threshold=tolerance,
            opacity=op,
            wrap=wrap,
        )


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


@functools.lru_cache(maxsize=32)
def _load_truetype(font_path: str | None, size: int):
    """Load (and cache) a font face; the Type tool calls this on every keystroke."""
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

