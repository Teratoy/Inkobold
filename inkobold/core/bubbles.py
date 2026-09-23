"""Shared air-bubble placement and shading (brush Bubbles mode + Milk effect)."""

from __future__ import annotations

import math

import numpy as np

from inkobold.core.sun import DEFAULT_MILK_LIGHT_DIR, normalize_light


def _max_v(pixels: np.ndarray) -> float:
    return 65535.0 if pixels.dtype == np.uint16 else 255.0


def _clip(a: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return np.minimum(np.maximum(a, lo), hi)


def _grid(y0: int, y1: int, x0: int, x1: int) -> tuple[np.ndarray, np.ndarray]:
    """Broadcastable (yy, xx) integer coordinate columns/rows."""
    return np.arange(y0, y1)[:, None], np.arange(x0, x1)[None, :]


def place_bubble_cluster(
    cx: float,
    cy: float,
    radius: float,
    rng: np.random.Generator,
    density: float = 50.0,
    *,
    force: bool = False,
) -> list[tuple[float, float, float, float]]:
    """Place one bubble or a small cluster (milk-effect layout).

    ``density`` (0–100) biases toward solitary bubbles when low and denser
    multi-bubble clusters when high. Returns an empty list when the stamp
    is skipped (low density spawn chance), unless ``force`` is set.
    """
    dens = max(0.0, min(100.0, float(density))) / 100.0
    # Low density rarely spawns; high density almost always does.
    # dens=0 → 0%, dens=10 → ~7%, dens=50 → ~42%, dens=100 → 100%.
    if not force:
        spawn_p = dens ** 1.25
        if dens < 0.015:
            spawn_p = 0.0
        if rng.random() > spawn_p:
            return []

    r = max(1.5, float(radius))
    # Low density → solitary only; clusters only appear as density rises.
    alone_p = 0.98 - 0.78 * dens
    alone = bool(rng.random() < alone_p)
    if alone or dens < 0.18:
        group_n = 1
    else:
        # 2…(3–8) members as density rises.
        hi = max(3, min(8, int(round(3 + 5 * dens))))
        group_n = int(rng.integers(2, hi + 1))
    cluster_r = r * float(rng.uniform(0.35, 0.95)) * (0.75 + 0.35 * dens)
    placed: list[tuple[float, float, float, float]] = []
    for gi in range(group_n):
        if gi == 0:
            bx, by = cx, cy
        else:
            ang = float(rng.uniform(0.0, 2.0 * math.pi))
            dist = float(rng.uniform(0.25, 1.0) * cluster_r)
            bx = cx + math.cos(ang) * dist
            by = cy + math.sin(ang) * dist
        rad = r * float(rng.uniform(0.28, 0.72))
        if not alone and gi > 0:
            rad *= float(rng.uniform(0.55, 1.05))
        rad = max(1.5, rad)
        strength = float(rng.uniform(0.55, 1.0))
        placed.append((bx, by, rad, strength))
    return placed


# Smooth-union blend width as a fraction of the bubble radius (see
# ``render_bubbles_list``). A bubble's rendering influence reaches this far
# outside its own disk, so bboxes must include it for seamless results.
_BUBBLE_SMOOTH_K = 0.12


def bubble_influence_bbox(cx: float, cy: float, rad: float) -> tuple[int, int, int, int]:
    """Integer pixel bbox (``x0, y0, x1, y1``, exclusive) a bubble can affect.

    Unclipped; callers intersect with the image / clip region.
    """
    p = int(math.ceil(float(rad) * (1.0 + _BUBBLE_SMOOTH_K))) + 3
    return (
        int(math.floor(cx - p)),
        int(math.floor(cy - p)),
        int(math.ceil(cx + p)) + 1,
        int(math.ceil(cy + p)) + 1,
    )


def render_bubbles_list(
    pixels: np.ndarray,
    placed: list[tuple[float, float, float, float]],
    color: tuple[int, int, int, int],
    mask: np.ndarray | None = None,
    opacity: float = 1.0,
    clip: tuple[int, int, int, int] | None = None,
    light_dir: tuple[float, float, float] = DEFAULT_MILK_LIGHT_DIR,
) -> None:
    """Shade and composite a list of bubbles as one seamless translucent body.

    ``placed`` entries are ``(cx, cy, radius, strength)``. All bubbles are
    considered together: overlapping bubbles merge into a single union whose
    membrane density, rim and lighting are continuous fields over the whole
    union, so a lens of overlap looks exactly like the rest of the body (no
    brighter/denser patch, no wall seam, no internal arcs).

    Each bubble is evaluated only over its own sub-bbox in a single pass, so
    the cost is proportional to the sum of bubble areas rather than
    ``len(placed) * bbox_area``.

    ``clip`` (``x0, y0, x1, y1``, exclusive) restricts output to a region.
    Pixels inside the region are rendered exactly as they would be without
    the clip, provided ``placed`` includes every bubble whose influence
    (see :func:`bubble_influence_bbox`) reaches the region.
    """
    if not placed:
        return
    op = min(1.0, max(0.0, float(opacity)))
    if op <= 0.0:
        return
    h, w = pixels.shape[:2]
    cx0, cy0, cx1, cy1 = 0, 0, w, h
    if clip is not None:
        cx0 = max(0, int(clip[0]))
        cy0 = max(0, int(clip[1]))
        cx1 = min(w, int(clip[2]))
        cy1 = min(h, int(clip[3]))
        if cx0 >= cx1 or cy0 >= cy1:
            return

    # Per-bubble influence bboxes (clipped) + union bbox.
    boxes: list[tuple[int, int, int, int]] = []
    x0, y0, x1, y1 = cx1, cy1, cx0, cy0
    for bx, by, rad, _ in placed:
        bx0, by0, bx1, by1 = bubble_influence_bbox(bx, by, rad)
        bx0, by0 = max(cx0, bx0), max(cy0, by0)
        bx1, by1 = min(cx1, bx1), min(cy1, by1)
        boxes.append((bx0, by0, bx1, by1))
        if bx0 < bx1 and by0 < by1:
            x0, y0 = min(x0, bx0), min(y0, by0)
            x1, y1 = max(x1, bx1), max(y1, by1)
    if x0 >= x1 or y0 >= y1:
        return

    ph, pw = y1 - y0, x1 - x0

    max_v = _max_v(pixels)
    cream = np.array(color[:3], dtype=np.float64) / max(max_v, 1e-6)
    # Specular stays a bit brighter than the fill; outline tracks the brush color.
    pearl = 0.35 * np.array([1.0, 1.0, 1.0]) + 0.65 * cream
    outline = np.clip(cream * 1.08, 0.0, 1.0)
    # Match milk bubble lighting defaults (glossy / slightly wet).
    gloss_eff = 0.85
    wet_n = 0.45
    bub_spec_pow = 18.0 + 70.0 * (min(gloss_eff, 1.0) ** 1.1) + 40.0 * wet_n
    lx, ly, lz = normalize_light(*light_dir)
    hx, hy, hz = lx, ly, lz + 1.0
    invh = 1.0 / math.sqrt(hx * hx + hy * hy + hz * hz)
    hx, hy, hz = hx * invh, hy * invh, hz * invh
    lxy = max(math.hypot(lx, ly), 1e-6)
    glint_ox, glint_oy = -0.28 * (lx / lxy), -0.28 * (ly / lxy)

    # Union accumulators (all continuous across bubble boundaries).
    # sd: signed pixel distance to the union silhouette (max of per-disk SDFs).
    sd = np.full((ph, pw), -1e9, dtype=np.float64)
    # Radius/strength blend weights: positive slightly outside each disk so the
    # AA fringe of the union also gets a well-defined local scale.
    wr_sum = np.zeros((ph, pw), dtype=np.float64)
    wr_rad = np.zeros((ph, pw), dtype=np.float64)
    wr_str = np.zeros((ph, pw), dtype=np.float64)
    # Hemisphere shading blend (weights → 0 at each disk edge, so mixing is seamless).
    shade_w = np.zeros((ph, pw), dtype=np.float64)
    shade_rgb = np.zeros((ph, pw, 3), dtype=np.float64)
    shade_spec = np.zeros((ph, pw), dtype=np.float64)
    shade_fres = np.zeros((ph, pw), dtype=np.float64)
    mid_glint = np.zeros((ph, pw), dtype=np.float64)

    b_amb = 0.38
    any_inside = False
    for (cx_px, cy_px, rad_px, strength), (bx0, by0, bx1, by1) in zip(placed, boxes):
        if bx0 >= bx1 or by0 >= by1:
            continue
        ly0, ly1 = by0 - y0, by1 - y0
        lx0, lx1 = bx0 - x0, bx1 - x0
        yy, xx = _grid(by0, by1, bx0, bx1)
        dx = (xx + (0.5 - cx_px)) / rad_px  # (1, bw)
        dy = (yy + (0.5 - cy_px)) / rad_px  # (bh, 1)
        d2 = dx * dx + dy * dy
        # Note: no early-out on "no interior pixel" — with ``clip`` a bubble
        # whose disk lies outside the region can still shape the union edge.
        inside = d2 < 1.0
        any_inside = True
        rn = np.sqrt(np.maximum(d2, 1e-8))

        # Smooth-union SDF (pixels, positive inside). A plain ``max`` is only
        # C0 and leaves a visible gradient crease along the radical axis; the
        # polynomial smooth-max keeps the gradient continuous and gently
        # fillets the neck where two silhouettes meet.
        sd_i = (1.0 - rn) * rad_px
        sd_v = sd[ly0:ly1, lx0:lx1]
        k = _BUBBLE_SMOOTH_K * rad_px
        hh = np.maximum(k - np.abs(sd_v - sd_i), 0.0) * (1.0 / k)
        np.maximum(sd_v, sd_i, out=sd_v)
        sd_v += hh * hh * (0.25 * k)

        # Local scale / strength weights: C1-continuous, positive out past the
        # AA fringe (2px + max smooth-union bump) and zero before the bbox edge.
        wr = np.maximum((1.0 + (2.0 + 0.25 * k) / rad_px) - rn, 0.0)
        wr = wr * wr
        wr_sum[ly0:ly1, lx0:lx1] += wr
        wr_rad[ly0:ly1, lx0:lx1] += wr * rad_px
        wr_str[ly0:ly1, lx0:lx1] += wr * strength

        # Hemisphere normal + lighting for this bubble.
        z = np.sqrt(np.maximum(1e-6, 1.0 - np.minimum(d2, 1.0)))
        # (dx, -dy, z) is already unit length on the hemisphere.
        b_ndotl = np.clip(dx * lx - dy * ly + z * lz, 0.0, 1.0)
        b_ndoth = np.clip(dx * hx - dy * hy + z * hz, 0.0, 1.0)
        b_spec = b_ndoth ** bub_spec_pow
        b_spec_broad = b_ndoth ** (6.0 + 10.0 * wet_n)
        b_diff = 0.52 * b_ndotl + 0.16 * (b_ndotl * b_ndotl)
        b_fres = np.clip(1.0 - z, 0.0, 1.0) ** 1.35
        # Broad sheen fades out smoothly toward the edge (no hard cutoff).
        center_w = np.clip((0.9 - rn) / 0.3, 0.0, 1.0)

        # Soft weights so overlapping hemispheres mix instead of cutting at the
        # seam. (1 - r²)^1.5 reaches zero at the disk edge with zero slope, so
        # a neighbour's shading fades in without a visible arc.
        wgt = np.maximum(1.0 - d2, 0.0) ** 1.5 * strength
        shade_w[ly0:ly1, lx0:lx1] += wgt
        shade_rgb[ly0:ly1, lx0:lx1] += (
            cream[None, None, :] * ((b_amb + b_diff) * wgt)[..., None]
        )
        shade_spec[ly0:ly1, lx0:lx1] += (
            strength * (0.35 * b_spec + 0.55 * b_spec_broad * center_w) * wgt
        )
        shade_fres[ly0:ly1, lx0:lx1] += strength * b_fres * wgt

        # Soft highlight blob toward the light (per bubble; Gaussian, seamless).
        gs = 1.0 / max(rad_px * 0.34, 1.0)
        gdx = (xx + (0.5 - (cx_px + glint_ox * rad_px))) * gs
        gdy = (yy + (0.5 - (cy_px + glint_oy * rad_px))) * gs
        glint = np.exp(-(gdx * gdx + gdy * gdy) * 2.2) * inside
        mg = mid_glint[ly0:ly1, lx0:lx1]
        np.maximum(mg, glint * (0.85 + 0.15 * strength), out=mg)

    if not any_inside:
        return

    # ~1px anti-aliased union silhouette.
    disk_aa = np.clip(sd + 0.5, 0.0, 1.0)
    if disk_aa.max() < 1e-4:
        return

    # Local radius / strength of the union (continuous blends).
    wr_safe = np.maximum(wr_sum, 1e-9)
    r_loc = np.where(wr_sum > 0.0, wr_rad / wr_safe, 1.0)
    s_loc = np.where(wr_sum > 0.0, wr_str / wr_safe, 0.75)
    # Normalized radial coordinate of the *union*: 0 deep inside, 1 at the
    # silhouette. Identical formula everywhere, so a lens of overlap gets the
    # same membrane density as a solitary bubble at the same depth.
    rn_u = np.clip(1.0 - sd / np.maximum(r_loc, 1e-6), 0.0, 1.0)

    # Membrane fill: thin at the centre, denser toward the outer silhouette.
    membrane = np.clip(rn_u / 0.98, 0.0, 1.0) ** 1.45
    soft_body = (0.30 + 0.12 * s_loc) * membrane
    # Translucent outline on the union silhouette only.
    rim = np.clip((rn_u - 0.97) / 0.025, 0.0, 1.0) * np.clip((1.01 - rn_u) / 0.025, 0.0, 1.0)
    rim_alpha = np.clip(rim * (0.36 + 0.08 * s_loc) * disk_aa, 0.0, 0.45)

    w_safe = np.maximum(shade_w, 1e-6)
    has_shade = shade_w > 1e-4
    bub_rgb = shade_rgb / w_safe[..., None]
    bub_spec = shade_spec / w_safe
    fres = shade_fres / w_safe
    # Fresnel shell and darker lip on the union silhouette (continuous bands).
    edge_w = np.clip((rn_u - 0.94) / 0.04, 0.0, 1.0)
    lip_w = np.clip((rn_u - 0.975) / 0.02, 0.0, 1.0)
    bub_rim = fres * edge_w
    bub_spec = bub_spec + 0.18 * bub_rim

    rgb = np.where(
        has_shade[..., None], bub_rgb, cream[None, None, :] * np.ones((ph, pw, 3))
    )
    rgb = rgb + outline * (
        (0.45 + 0.35 * min(gloss_eff, 1.0) + 0.15 * wet_n) * bub_rim
    )[..., None]
    rgb = rgb * (1.0 - 0.12 * lip_w)[..., None]
    gloss_w = 0.55 + 0.55 * min(gloss_eff, 1.0) + 0.35 * wet_n
    rgb = rgb + pearl * (gloss_w * bub_spec)[..., None]
    rgb = rgb + outline * ((0.55 + 0.25 * wet_n) * bub_rim)[..., None]
    rgb = rgb + (0.45 * outline + 0.55 * pearl) * (1.45 * mid_glint)[..., None]
    # Mild tint toward brush color on the rim; keep RGB soft so underlay shows.
    rim_w = np.clip(rim_alpha[..., None] * 0.65, 0.0, 1.0)
    rgb = rgb * (1.0 - rim_w) + outline * rim_w
    rgb = np.clip(rgb, 0.0, 1.0)

    # Soft-OR fill + rim so they meet continuously (no transparent gap).
    interior_boost = np.clip(soft_body * 1.25, 0.0, 1.0)
    alpha = disk_aa * (1.0 - (1.0 - interior_boost) * (1.0 - rim_alpha))
    alpha = np.maximum(alpha, disk_aa * mid_glint * 0.55)
    alpha = np.clip(alpha, 0.0, 1.0) * op
    if mask is not None:
        m = (mask[y0:y1, x0:x1] > 0).astype(np.float64)
        alpha = alpha * m

    if not np.any(alpha > 1e-4):
        return

    a_src = (float(color[3]) / max_v) * alpha
    patch = pixels[y0:y1, x0:x1].astype(np.float64)
    out_rgb = rgb * max_v
    # Proper "over" composite so overlapping stamps merge translucently.
    dst_a = patch[..., 3] / max_v
    src_a = a_src
    out_a = np.clip(src_a + dst_a * (1.0 - src_a), 0.0, 1.0)
    sa = src_a[..., None]
    da = (dst_a * (1.0 - src_a))[..., None]
    denom = np.maximum(out_a[..., None], 1e-6)
    mixed = (out_rgb * sa + patch[..., :3] * da) / denom
    # Leave pixels this stamp doesn't touch alone (incl. RGB of transparent ones).
    touched = (src_a > 1e-6)[..., None]
    patch[..., :3] = np.where(touched, mixed, patch[..., :3])
    patch[..., 3] = np.where(touched[..., 0], out_a * max_v, patch[..., 3])
    pixels[y0:y1, x0:x1] = _clip(patch, 0, max_v).astype(pixels.dtype)


