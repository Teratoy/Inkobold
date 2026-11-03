from __future__ import annotations

import math
from typing import Optional

import numpy as np
from PIL import Image

from inkobold.core.image_meta import to_display_u8
from inkobold.tools.base import BaseTool, ToolContext

# Corner order: NW, NE, SE, SW
_CORNER_LOCAL = (
    (-0.5, -0.5),
    (0.5, -0.5),
    (0.5, 0.5),
    (-0.5, 0.5),
)


def _bool_bounds(m: np.ndarray) -> tuple[int, int, int, int] | None:
    """(x0, y0, x1_inclusive, y1_inclusive) of True cells, or None if empty.

    Uses row/column reductions instead of materialising every nonzero index.
    """
    rows = np.flatnonzero(m.any(axis=1))
    if rows.size == 0:
        return None
    cols = np.flatnonzero(m.any(axis=0))
    return int(cols[0]), int(rows[0]), int(cols[-1]), int(rows[-1])


def layer_content_box(ly) -> tuple[float, float, float, float, float]:
    """Axis-aligned content box in document space: (cx, cy, w, h, angle=0)."""
    ox = float(ly.offset_x)
    oy = float(ly.offset_y)
    bounds = _bool_bounds(ly.pixels[..., 3] != 0)
    if bounds is None:
        w = float(max(1, ly.width))
        h = float(max(1, ly.height))
        return ox + w * 0.5, oy + h * 0.5, w, h, 0.0
    bx0, by0, bx1, by1 = bounds
    x0 = float(bx0) + ox
    y0 = float(by0) + oy
    x1 = float(bx1) + ox + 1.0
    y1 = float(by1) + oy + 1.0
    w = max(1.0, x1 - x0)
    h = max(1.0, y1 - y0)
    return x0 + w * 0.5, y0 + h * 0.5, w, h, 0.0


def selection_content_box(
    ly,
    mask: np.ndarray,
) -> tuple[float, float, float, float, float]:
    """Axis-aligned box of selected layer content in document space."""
    ox = int(ly.offset_x)
    oy = int(ly.offset_y)
    m = mask > 0
    alpha = ly.pixels[..., 3] > 0
    if ox == 0 and oy == 0:
        usable = m & alpha
    else:
        # Map document selection into layer pixel space accounting for offset.
        h, w = ly.height, ly.width
        usable = np.zeros((h, w), dtype=bool)
        ys, xs = np.nonzero(m)
        lx = xs - ox
        ly_i = ys - oy
        inb = (lx >= 0) & (lx < w) & (ly_i >= 0) & (ly_i < h)
        if inb.any():
            usable[ly_i[inb], lx[inb]] = alpha[ly_i[inb], lx[inb]]
    bounds = _bool_bounds(usable)
    if bounds is None:
        # Fall back to selection AABB in document space.
        bounds = _bool_bounds(m)
        if bounds is None:
            return layer_content_box(ly)
        bx0, by0, bx1, by1 = bounds
        x0 = float(bx0)
        y0 = float(by0)
        x1 = float(bx1) + 1.0
        y1 = float(by1) + 1.0
    else:
        bx0, by0, bx1, by1 = bounds
        x0 = float(bx0) + float(ox)
        y0 = float(by0) + float(oy)
        x1 = float(bx1) + float(ox) + 1.0
        y1 = float(by1) + float(oy) + 1.0
    w = max(1.0, x1 - x0)
    h = max(1.0, y1 - y0)
    return x0 + w * 0.5, y0 + h * 0.5, w, h, 0.0


def box_corners(cx: float, cy: float, w: float, h: float, angle: float) -> list[tuple[float, float]]:
    ca, sa = math.cos(angle), math.sin(angle)
    out: list[tuple[float, float]] = []
    for lx, ly in _CORNER_LOCAL:
        x = lx * w
        y = ly * h
        out.append((cx + x * ca - y * sa, cy + x * sa + y * ca))
    return out


def _hit_corner(
    x: float,
    y: float,
    corners: list[tuple[float, float]],
    radius: float,
) -> Optional[int]:
    best: Optional[int] = None
    best_d = radius * radius
    for i, (cx, cy) in enumerate(corners):
        d = (x - cx) * (x - cx) + (y - cy) * (y - cy)
        if d <= best_d:
            best_d = d
            best = i
    return best


def _point_in_box(x: float, y: float, cx: float, cy: float, w: float, h: float, angle: float) -> bool:
    ca, sa = math.cos(-angle), math.sin(-angle)
    dx, dy = x - cx, y - cy
    lx = dx * ca - dy * sa
    ly = dx * sa + dy * ca
    return abs(lx) <= w * 0.5 and abs(ly) <= h * 0.5


def _affine_coeffs(
    cx0: float,
    cy0: float,
    w0: float,
    h0: float,
    cx: float,
    cy: float,
    w: float,
    h: float,
    angle: float,
) -> tuple[float, float, float, float, float, float]:
    """PIL AFFINE coeffs mapping output doc pixels → source pixels."""
    ca, sa = math.cos(angle), math.sin(angle)
    sx = w0 / max(w, 1e-6)
    sy = h0 / max(h, 1e-6)
    a = sx * ca
    b = sx * sa
    c = cx0 - sx * (ca * cx + sa * cy)
    d = sy * (-sa)
    e = sy * ca
    f = cy0 - sy * (-sa * cx + ca * cy)
    return a, b, c, d, e, f


def _identity_shift(coeffs: tuple[float, ...]) -> tuple[int, int] | None:
    """(dx, dy) when *coeffs* are a pure integer translation, else None."""
    a, b, c, d, e, f = coeffs
    eps = 1e-6
    if abs(a - 1.0) > eps or abs(b) > eps or abs(d) > eps or abs(e - 1.0) > eps:
        return None
    if abs(c - round(c)) > 1e-4 or abs(f - round(f)) > 1e-4:
        return None
    # Output (x, y) samples source (x + c, y + f): content moves by (-c, -f).
    return -int(round(c)), -int(round(f))


def _shift_paste(src: np.ndarray, dx: int, dy: int, size: tuple[int, int]) -> np.ndarray:
    """Copy *src* into a zeroed buffer of *size* with its origin at (dx, dy)."""
    h, w = size
    out = np.zeros((h, w) + src.shape[2:], dtype=src.dtype)
    sh, sw = src.shape[:2]
    x0, y0 = max(0, dx), max(0, dy)
    x1, y1 = min(w, dx + sw), min(h, dy + sh)
    if x1 > x0 and y1 > y0:
        out[y0:y1, x0:x1] = src[y0 - dy : y1 - dy, x0 - dx : x1 - dx]
    return out


def _fold_wrap(region: np.ndarray, x0: int, y0: int, size: tuple[int, int]) -> np.ndarray:
    """Wrap a doc-space *region* whose top-left sits at (x0, y0) onto a tile of *size*."""
    h, w = size
    out = np.zeros((h, w) + region.shape[2:], dtype=region.dtype)
    rh, rw = region.shape[:2]
    for ty in range(math.floor(y0 / h), math.floor((y0 + rh - 1) / h) + 1):
        for tx in range(math.floor(x0 / w), math.floor((x0 + rw - 1) / w) + 1):
            # Intersection of this tile with the region, in doc space.
            ax0, ay0 = max(x0, tx * w), max(y0, ty * h)
            ax1, ay1 = min(x0 + rw, (tx + 1) * w), min(y0 + rh, (ty + 1) * h)
            if ax1 <= ax0 or ay1 <= ay0:
                continue
            piece = region[ay0 - y0 : ay1 - y0, ax0 - x0 : ax1 - x0]
            dst = out[ay0 - ty * h : ay1 - ty * h, ax0 - tx * w : ax1 - tx * w]
            m = piece[..., 3] > 0
            dst[m] = piece[m]
    return out


def _resample_rgba(
    src: np.ndarray,
    coeffs: tuple[float, ...],
    size: tuple[int, int],
    *,
    wrap: bool = False,
    scale: tuple[float, float] = (1.0, 1.0),
    hq: bool = False,
    bounds: tuple[int, int, int, int] | None = None,
) -> np.ndarray:
    """Affine-resample an HxWx4 buffer into a document-sized buffer.

    *scale* is output px per source px along the box axes; with *hq* a
    downscale is first reduced with Lanczos so detail averages instead of
    aliasing, then placed with bicubic. Without *hq* it is a quick bilinear
    preview. *src* may be any size: when it is not document-sized, *wrap*
    renders the doc-space *bounds* (x0, y0, x1, y1) and folds them onto the tile.
    """
    h, w = size
    tiled = wrap and src.shape[:2] == (h, w)
    fold = wrap and not tiled and bounds is not None
    shift = _identity_shift(coeffs)
    if shift is not None:
        # Exact 1:1 placement — no filtering, full bit depth kept.
        dx, dy = shift
        if tiled:
            return np.ascontiguousarray(np.roll(src, (dy, dx), axis=(0, 1)))
        if fold:
            bx0, by0 = max(bounds[0], -w), max(bounds[1], -h)
            bx1, by1 = min(bounds[2], 2 * w), min(bounds[3], 2 * h)
            region = _shift_paste(src, dx - bx0, dy - by0, (by1 - by0, bx1 - bx0))
            return _fold_wrap(region, bx0, by0, size)
        return _shift_paste(src, dx, dy, size)

    u8 = to_display_u8(src)
    a, b, c, d, e, f = coeffs
    img = Image.fromarray(u8, mode="RGBA")
    if hq:
        sh, sw = u8.shape[:2]
        fx = min(1.0, abs(scale[0]))
        fy = min(1.0, abs(scale[1]))
        if fx < 0.999 or fy < 0.999:
            nw = max(1, int(round(sw * fx)))
            nh = max(1, int(round(sh * fy)))
            img = img.resize((nw, nh), Image.Resampling.LANCZOS)
            fx, fy = nw / sw, nh / sh
            a, b, c = a * fx, b * fx, c * fx
            d, e, f = d * fy, e * fy, f * fy
    resample = Image.Resampling.BICUBIC if hq else Image.Resampling.BILINEAR
    if tiled:
        # 3×3 tiled source so AFFINE can sample across opposite edges.
        tw, th = img.size
        img = Image.fromarray(np.tile(np.asarray(img), (3, 3, 1)), mode="RGBA")
        out = img.transform(
            (w, h), Image.Transform.AFFINE, data=(a, b, c + tw, d, e, f + th),
            resample=resample, fillcolor=(0, 0, 0, 0),
        )
        result = np.array(out, dtype=np.uint8, copy=True)
    elif fold:
        bx0, by0 = max(bounds[0], -w), max(bounds[1], -h)
        bx1, by1 = min(bounds[2], 2 * w), min(bounds[3], 2 * h)
        if bx1 <= bx0 or by1 <= by0:
            result = np.zeros((h, w, 4), dtype=np.uint8)
        else:
            out = img.transform(
                (bx1 - bx0, by1 - by0), Image.Transform.AFFINE,
                data=(a, b, c + a * bx0 + b * by0, d, e, f + d * bx0 + e * by0),
                resample=resample, fillcolor=(0, 0, 0, 0),
            )
            result = _fold_wrap(np.asarray(out, dtype=np.uint8), bx0, by0, size)
    else:
        out = img.transform(
            (w, h), Image.Transform.AFFINE, data=(a, b, c, d, e, f),
            resample=resample, fillcolor=(0, 0, 0, 0),
        )
        result = np.array(out, dtype=np.uint8, copy=True)
    if src.dtype == np.uint8:
        return result
    # Restore high bit depth approximately from 8-bit preview
    max_v = float(np.iinfo(src.dtype).max)
    return np.clip(np.rint(result.astype(np.float64) * (max_v / 255.0)), 0, max_v).astype(src.dtype)


def _resample_mask(
    mask: np.ndarray,
    coeffs: tuple[float, ...],
    size: tuple[int, int],
    *,
    wrap: bool = False,
) -> np.ndarray:
    h, w = size
    a, b, c, d, e, f = coeffs
    src = np.asarray(mask, dtype=np.uint8)
    if wrap:
        tiled = np.tile(src, (3, 3))
        img = Image.fromarray(tiled, mode="L")
        data = (a, b, c + w, d, e, f + h)
    else:
        img = Image.fromarray(src, mode="L")
        data = (a, b, c, d, e, f)
    out = img.transform(
        (w, h),
        Image.Transform.AFFINE,
        data=data,
        resample=Image.Resampling.NEAREST,
        fillcolor=0,
    )
    return np.array(out, dtype=np.uint8, copy=True)


def _extract_selection_float(
    pixels: np.ndarray,
    mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Split layer into (base without selection, floating selected pixels)."""
    m = mask > 0
    base = np.array(pixels, copy=True)
    flo = np.array(pixels, copy=True)
    base[m] = 0
    flo[~m] = 0
    return base, flo


class TransformTool(BaseTool):
    """Move, scale, and rotate the active layer (or selection) via a transform box.

    - Drag inside the box to translate (offset preview, baked on release).
    - Drag a corner to scale (opposite corner anchored).
    - Alt+drag a corner to rotate around the box center.
    - Shift constrains move to an axis, scale to uniform, rotate to 15°.
    - With an active lasso/selection, only selected pixels are transformed.

    Once a scale/rotate (or selection move) starts, the original pixels stay
    floating: every later gesture resamples from them, so repeated edits do
    not compound blur and content parked off-canvas comes back intact. The
    layer always holds the current result; Enter, clicking outside the box,
    switching tools, or any outside edit to the layer drops the float.
    """

    name = "Transform"
    id = "move"
    uses_size = False
    uses_color = False
    uses_document_coords = True
    modifies_pixels = False
    supports_mirror = False

    def __init__(self) -> None:
        super().__init__()
        self._view_scale = 1.0
        self._dragging = False
        self._mode: Optional[str] = None  # "move" | "scale" | "rotate"
        self._corner: Optional[int] = None
        self._start_x = 0.0
        self._start_y = 0.0
        self._origin_ox = 0
        self._origin_oy = 0
        # Live / idle box in document space
        self._cx = 0.0
        self._cy = 0.0
        self._w = 1.0
        self._h = 1.0
        self._angle = 0.0
        # Box at gesture start
        self._cx0 = 0.0
        self._cy0 = 0.0
        self._w0 = 1.0
        self._h0 = 1.0
        self._angle0 = 0.0
        self._anchor: tuple[float, float] = (0.0, 0.0)
        self._pointer_angle0 = 0.0
        # Floating source (kept across gestures) and the axis-aligned box it
        # occupies in its own pixel space.
        self._source: Optional[np.ndarray] = None
        self._source_mask: Optional[np.ndarray] = None
        self._base: Optional[np.ndarray] = None  # unselected pixels when clipping
        self._src_box = (0.0, 0.0, 1.0, 1.0)
        self._rolling = False  # wrap-roll move: _source is a scratch copy, not a float
        self._box_valid = False
        # Identity of the (layer, selection) state the idle box was computed
        # from; lets guide redraws skip the full-layer content scan. While a
        # float is live it is the state the float last wrote.
        self._box_key: Optional[tuple] = None

    def set_view_scale(self, zoom: float) -> None:
        self._view_scale = max(1e-6, float(zoom))

    def handle_radius_doc(self) -> float:
        # ~9 screen pixels, expressed in document space
        return 9.0 / self._view_scale

    @staticmethod
    def _state_key(ctx: ToolContext) -> tuple:
        ly = ctx.document.active_layer
        sel = ctx.document.selection
        use_sel = sel.active and sel.mask is not None
        return (ly.id, ly.revision, ly.offset_x, ly.offset_y, sel.revision if use_sel else None)

    @property
    def floating(self) -> bool:
        """True while an uncommitted float holds the original pixels."""
        return self._source is not None and not self._rolling

    def sync_box(self, ctx: ToolContext) -> None:
        """Refresh the idle transform box from selection or active layer."""
        if self._dragging:
            return
        key = self._state_key(ctx)
        if self._box_valid and key == self._box_key:
            return
        # Something else touched the layer/selection: the float is stale.
        self._drop_float()
        ly = ctx.document.active_layer
        sel = ctx.document.selection
        if key[-1] is not None:
            self._cx, self._cy, self._w, self._h, self._angle = selection_content_box(ly, sel.mask)
        else:
            self._cx, self._cy, self._w, self._h, self._angle = layer_content_box(ly)
        self._box_valid = True
        self._box_key = key

    def guide_box(self) -> Optional[tuple[float, float, float, float, float]]:
        if not self._box_valid:
            return None
        return self._cx, self._cy, self._w, self._h, self._angle

    def guide_corners(self) -> list[tuple[float, float]]:
        if not self._box_valid:
            return []
        return box_corners(self._cx, self._cy, self._w, self._h, self._angle)

    def _ensure_box(self, ctx: ToolContext) -> None:
        self.sync_box(ctx)

    def _drop_float(self) -> None:
        self._source = None
        self._source_mask = None
        self._base = None
        self._rolling = False

    def commit(self) -> bool:
        """Apply the live float (the layer already shows it). True if one was live."""
        if not self.floating or self._dragging:
            return False
        self._drop_float()
        # Re-fit the box to the committed content on the next sync.
        self._box_valid = False
        self._box_key = None
        return True

    def _begin_float(self, ctx: ToolContext) -> None:
        """Capture source pixels; if a selection is active, float only that region."""
        ly = ctx.document.active_layer
        sel = ctx.document.selection
        if sel.active and sel.mask is not None:
            self._source_mask = np.array(sel.mask, copy=True)
            self._base, self._source = _extract_selection_float(ly.pixels, self._source_mask)
        else:
            self._source_mask = None
            self._base = None
            self._source = np.array(ly.pixels, copy=True)
        self._rolling = False
        self._src_box = (self._cx, self._cy, self._w, self._h)

    def begin_placement(self, ctx: ToolContext, image: np.ndarray) -> None:
        """Float *image* (any size, layer dtype) at 1:1, centered on the canvas.

        The active layer is replaced by the placement; the image stays
        re-scalable from its original pixels until committed.
        """
        doc = ctx.document
        ih, iw = image.shape[:2]
        self.reset()
        self._source = np.ascontiguousarray(image)
        self._source_mask = None
        self._base = None
        self._src_box = (iw * 0.5, ih * 0.5, float(iw), float(ih))
        # Whole-pixel top-left so the 1:1 placement stays unfiltered.
        self._cx = (doc.width - iw) // 2 + iw * 0.5
        self._cy = (doc.height - ih) // 2 + ih * 0.5
        self._w, self._h, self._angle = float(iw), float(ih), 0.0
        self._render(ctx, hq=True)

    def would_begin(self, ctx: ToolContext, x: float, y: float, *, alt: bool = False) -> bool:
        """True if a press at (x, y) would start move/scale/rotate."""
        self._ensure_box(ctx)
        corners = box_corners(self._cx, self._cy, self._w, self._h, self._angle)
        radius = self.handle_radius_doc()
        if _hit_corner(x, y, corners, radius) is not None:
            return True
        return _point_in_box(x, y, self._cx, self._cy, self._w, self._h, self._angle)

    def _mark_gesture_start(self) -> None:
        self._cx0, self._cy0, self._w0, self._h0, self._angle0 = (
            self._cx,
            self._cy,
            self._w,
            self._h,
            self._angle,
        )

    def on_press(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        self.modifies_pixels = False
        self._ensure_box(ctx)
        ly = ctx.document.active_layer
        wrap = bool(getattr(ctx, "tile_wrap", False))
        corners = box_corners(self._cx, self._cy, self._w, self._h, self._angle)
        radius = self.handle_radius_doc()
        corner = _hit_corner(x, y, corners, radius)
        sel_active = ctx.document.selection.active
        floating = self.floating

        if corner is not None:
            if not floating:
                # Bake any pending translate so scale/rotate sample a stable buffer.
                if (ly.offset_x or ly.offset_y) and ly.apply_offset(wrap=wrap):
                    self.modifies_pixels = True
                    self.sync_box(ctx)
                    corners = box_corners(self._cx, self._cy, self._w, self._h, self._angle)
                self._begin_float(ctx)
            self._mode = "rotate" if alt else "scale"
            self._corner = corner
            self._mark_gesture_start()
            opp = corners[(corner + 2) % 4]
            self._anchor = opp
            self._pointer_angle0 = math.atan2(y - self._cy0, x - self._cx0)
        elif _point_in_box(x, y, self._cx, self._cy, self._w, self._h, self._angle):
            self._mode = "move"
            self._corner = None
            if floating:
                self._mark_gesture_start()
            elif sel_active:
                # Selection move must rewrite pixels (cannot use whole-layer offset).
                if (ly.offset_x or ly.offset_y) and ly.apply_offset(wrap=wrap):
                    self.modifies_pixels = True
                    self.sync_box(ctx)
                self._begin_float(ctx)
                self._mark_gesture_start()
            elif wrap:
                # Live wrap-roll: bake any pending offset, then shift with np.roll.
                if (ly.offset_x or ly.offset_y) and ly.apply_offset(wrap=True):
                    self.modifies_pixels = True
                    self.sync_box(ctx)
                self._source_mask = None
                self._base = None
                self._source = np.array(ly.pixels, copy=True)
                self._rolling = True
                self._origin_ox = 0
                self._origin_oy = 0
                self._cx0, self._cy0 = self._cx, self._cy
            else:
                self._drop_float()
                self._origin_ox = int(ly.offset_x)
                self._origin_oy = int(ly.offset_y)
                self._cx0, self._cy0 = self._cx, self._cy
        else:
            self._mode = None
            self._dragging = False
            return

        self._dragging = True
        self._start_x = float(x)
        self._start_y = float(y)

    def on_drag(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        if not self._dragging or self._mode is None:
            return
        if self._mode == "move":
            self._drag_move(ctx, x, y, shift=shift)
        elif self._mode == "scale":
            self._drag_scale(ctx, x, y, shift=shift)
        elif self._mode == "rotate":
            self._drag_rotate(ctx, x, y, shift=shift)

    def _drag_move(self, ctx: ToolContext, x: float, y: float, *, shift: bool) -> None:
        dx = float(x) - self._start_x
        dy = float(y) - self._start_y
        if shift:
            if abs(dx) >= abs(dy):
                dy = 0.0
            else:
                dx = 0.0
        if self.floating:
            # Floating content: whole-pixel steps keep a 1:1 float unfiltered.
            self._cx = self._cx0 + round(dx)
            self._cy = self._cy0 + round(dy)
            self._w, self._h, self._angle = self._w0, self._h0, self._angle0
            self._render(ctx, hq=False)
            return
        ly = ctx.document.active_layer
        if self._rolling and self._source is not None:
            # Seamless tile move: wrap content across opposite edges.
            ox = int(round(dx))
            oy = int(round(dy))
            out = self._source
            if oy:
                out = np.roll(out, oy, axis=0)
            if ox:
                out = np.roll(out, ox, axis=1)
            ly.pixels = np.ascontiguousarray(out)
            ly.offset_x = 0
            ly.offset_y = 0
            ly.bump()
            self._cx = self._cx0 + ox
            self._cy = self._cy0 + oy
            self.modifies_pixels = True
            ctx.document.mark_dirty(content=True)
            return
        ly.offset_x = self._origin_ox + int(round(dx))
        ly.offset_y = self._origin_oy + int(round(dy))
        self._cx = self._cx0 + (ly.offset_x - self._origin_ox)
        self._cy = self._cy0 + (ly.offset_y - self._origin_oy)
        ctx.document.mark_dirty(content=False)

    def _drag_scale(self, ctx: ToolContext, x: float, y: float, *, shift: bool) -> None:
        ax, ay = self._anchor
        angle = self._angle0
        ca, sa = math.cos(angle), math.sin(angle)
        # Vector from anchored opposite corner to pointer, in box-local axes
        rel_x = float(x) - ax
        rel_y = float(y) - ay
        lx = rel_x * ca + rel_y * sa
        ly = -rel_x * sa + rel_y * ca
        new_w = max(1.0, abs(lx))
        new_h = max(1.0, abs(ly))
        if shift:
            # Uniform scale from the dominant axis relative to the start size
            sx = new_w / max(self._w0, 1e-6)
            sy = new_h / max(self._h0, 1e-6)
            s = sx if abs(math.log(max(sx, 1e-6))) >= abs(math.log(max(sy, 1e-6))) else sy
            new_w = max(1.0, self._w0 * s)
            new_h = max(1.0, self._h0 * s)
            # Keep the anchor fixed: center = anchor + local_offset_of_dragged_corner
            assert self._corner is not None
            # Opposite local of corner i is -_CORNER_LOCAL[i]
            # Anchor is at center + R * (opp_local * size)
            # center = anchor - R * (opp_local_x * new_w, opp_local_y * new_h)
            olx, oly = _CORNER_LOCAL[(self._corner + 2) % 4]
            ox = olx * new_w
            oy = oly * new_h
            cx = ax - (ox * ca - oy * sa)
            cy = ay - (ox * sa + oy * ca)
        else:
            cx = (float(x) + ax) * 0.5
            cy = (float(y) + ay) * 0.5
        self._cx, self._cy, self._w, self._h, self._angle = cx, cy, new_w, new_h, angle
        self._render(ctx, hq=False)

    def _drag_rotate(self, ctx: ToolContext, x: float, y: float, *, shift: bool) -> None:
        ang = math.atan2(float(y) - self._cy0, float(x) - self._cx0) - self._pointer_angle0 + self._angle0
        if shift:
            step = math.radians(15.0)
            ang = round(ang / step) * step
        self._cx, self._cy, self._w, self._h = self._cx0, self._cy0, self._w0, self._h0
        self._angle = ang
        self._render(ctx, hq=False)

    def _render(self, ctx: ToolContext, *, hq: bool) -> None:
        """Write the float, mapped from its source box into the live box, to the layer."""
        if self._source is None:
            return
        ly = ctx.document.active_layer
        wrap = bool(getattr(ctx, "tile_wrap", False))
        scx, scy, sw, sh = self._src_box
        coeffs = _affine_coeffs(
            scx,
            scy,
            sw,
            sh,
            self._cx,
            self._cy,
            self._w,
            self._h,
            self._angle,
        )
        size = (ly.height, ly.width)
        xs, ys = zip(*box_corners(self._cx, self._cy, self._w, self._h, self._angle))
        bounds = (
            math.floor(min(xs)) - 1,
            math.floor(min(ys)) - 1,
            math.ceil(max(xs)) + 1,
            math.ceil(max(ys)) + 1,
        )
        transformed = _resample_rgba(
            self._source,
            coeffs,
            size,
            wrap=wrap,
            scale=(self._w / max(sw, 1e-6), self._h / max(sh, 1e-6)),
            hq=hq,
            bounds=bounds,
        )
        if transformed.dtype != ly.pixels.dtype:
            transformed = transformed.astype(ly.pixels.dtype)
        if self._base is not None:
            out = np.array(self._base, copy=True)
            # Place float over punched base (opaque float wins; keeps filter fringes)
            m = transformed[..., 3] > 0
            out[m] = transformed[m]
            ly.pixels = out
        else:
            ly.pixels = transformed
        ly.offset_x = 0
        ly.offset_y = 0
        ly.bump()
        if self._source_mask is not None:
            ctx.document.selection.mask = _resample_mask(
                self._source_mask,
                coeffs,
                (ctx.document.height, ctx.document.width),
                wrap=wrap,
            )
        self.modifies_pixels = True
        ctx.document.mark_dirty(content=True)
        # The layer now matches the float; keep it live across sync_box.
        self._box_valid = True
        self._box_key = self._state_key(ctx)

    def on_release(self, ctx: ToolContext, x: float, y: float) -> None:
        if self._dragging and self._mode is not None:
            self.on_drag(ctx, x, y)
            ly = ctx.document.active_layer
            wrap = bool(getattr(ctx, "tile_wrap", False))
            if self.floating:
                # Final pass at full quality; the float stays live.
                self._render(ctx, hq=True)
            elif self._mode == "move":
                if self._rolling:
                    # Live wrap-roll already wrote pixels.
                    self.modifies_pixels = True
                    ctx.document.mark_dirty(content=True)
                elif ly.apply_offset(wrap=wrap):
                    self.modifies_pixels = True
                    ctx.document.mark_dirty(content=True)
        self._dragging = False
        self._mode = None
        self._corner = None
        if self._rolling:
            self._drop_float()
        # Snap box to selection or layer content after bake (kept while floating)
        self.sync_box(ctx)

    def reset(self) -> None:
        self._dragging = False
        self._mode = None
        self._corner = None
        self._drop_float()
        self._box_valid = False
        self._box_key = None
        self.modifies_pixels = False


# Backwards-compatible alias
MoveTool = TransformTool
