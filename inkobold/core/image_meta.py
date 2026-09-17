"""Shared image metadata defaults (DPI / color depth)."""

from __future__ import annotations

import numpy as np

DEFAULT_DPI = 72
COLOR_DEPTH_GRAY8 = 8
COLOR_DEPTH_RGB24 = 24
COLOR_DEPTH_RGBA32 = 32
COLOR_DEPTH_RGBA16 = 64  # 16 bits per channel RGBA

COLOR_DEPTH_CHOICES: tuple[tuple[int, str], ...] = (
    (COLOR_DEPTH_RGBA16, "64-bit RGBA (16 bpc)"),
    (COLOR_DEPTH_RGBA32, "32-bit RGBA (8 bpc)"),
    (COLOR_DEPTH_RGB24, "24-bit RGB (8 bpc)"),
    (COLOR_DEPTH_GRAY8, "8-bit Grayscale"),
)


def channel_max(depth: int) -> int:
    return 65535 if int(depth) == COLOR_DEPTH_RGBA16 else 255


def storage_dtype(depth: int) -> np.dtype:
    return np.dtype(np.uint16 if int(depth) == COLOR_DEPTH_RGBA16 else np.uint8)


def has_alpha(depth: int) -> bool:
    d = int(depth)
    return d in (COLOR_DEPTH_RGBA32, COLOR_DEPTH_RGBA16)


def is_gray(depth: int) -> bool:
    return int(depth) == COLOR_DEPTH_GRAY8


def valid_color_depth(depth: int) -> int:
    allowed = {d for d, _ in COLOR_DEPTH_CHOICES}
    d = int(depth)
    return d if d in allowed else COLOR_DEPTH_RGBA32


def scale_u8_color(color: tuple[int, int, int, int], depth: int) -> tuple[int, int, int, int]:
    """Map an 8-bit UI color into the document channel range."""
    max_v = channel_max(depth)
    if max_v == 255:
        return tuple(int(max(0, min(255, c))) for c in color)  # type: ignore[return-value]
    return tuple(int(round(max(0, min(255, c)) * max_v / 255.0)) for c in color)  # type: ignore[return-value]


def prepare_paint_color(color: tuple[int, int, int, int], depth: int) -> tuple[int, int, int, int]:
    """Scale UI color and apply gray / no-alpha constraints for editing."""
    depth = valid_color_depth(depth)
    r, g, b, a = scale_u8_color(color, depth)
    max_v = channel_max(depth)
    if is_gray(depth):
        y = int(round(0.299 * r + 0.587 * g + 0.114 * b))
        return (y, y, y, max_v if not has_alpha(depth) else a)
    if not has_alpha(depth):
        return (r, g, b, max_v)
    return (r, g, b, a)


def scale_threshold(tol_u8: int, depth: int) -> int:
    max_v = channel_max(depth)
    tol = max(0, int(tol_u8))
    if max_v == 255:
        return min(255, tol)
    return int(round(tol * max_v / 255.0))


def constrain_pixels(pixels: np.ndarray, depth: int) -> None:
    """Force gray / opaque alpha in-place for the active color depth."""
    depth = valid_color_depth(depth)
    max_v = channel_max(depth)
    if is_gray(depth):
        # Rec. 601 luma in channel units
        y = (
            0.299 * pixels[..., 0].astype(np.float64)
            + 0.587 * pixels[..., 1].astype(np.float64)
            + 0.114 * pixels[..., 2].astype(np.float64)
        )
        y = np.clip(np.rint(y), 0, max_v).astype(pixels.dtype)
        pixels[..., 0] = y
        pixels[..., 1] = y
        pixels[..., 2] = y
        pixels[..., 3] = max_v
    elif not has_alpha(depth):
        pixels[..., 3] = max_v


def convert_pixels(pixels: np.ndarray, old_depth: int, new_depth: int) -> np.ndarray:
    """Convert a layer buffer between color depths (always returns contiguous HxWx4)."""
    old_depth = valid_color_depth(old_depth)
    new_depth = valid_color_depth(new_depth)
    old_max = float(channel_max(old_depth))
    new_max = float(channel_max(new_depth))
    new_dt = storage_dtype(new_depth)
    f = pixels.astype(np.float64) / max(old_max, 1.0)
    if is_gray(new_depth):
        y = 0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]
        out = np.empty(pixels.shape, dtype=np.float64)
        out[..., 0] = y
        out[..., 1] = y
        out[..., 2] = y
        out[..., 3] = 1.0
        f = out
    elif not has_alpha(new_depth):
        # Composite on white, then lock opaque
        a = f[..., 3:4]
        rgb = f[..., :3] * a + (1.0 - a)
        f = np.concatenate([rgb, np.ones_like(a)], axis=-1)
    return np.clip(np.rint(f * new_max), 0, new_max).astype(new_dt)


def to_display_u8(pixels: np.ndarray) -> np.ndarray:
    """Downconvert any layer buffer to uint8 RGBA for GPU preview / 8-bit export helpers."""
    if pixels.dtype == np.uint8:
        return np.ascontiguousarray(pixels)
    max_v = float(np.iinfo(pixels.dtype).max)
    return np.clip(np.rint(pixels.astype(np.float64) * (255.0 / max_v)), 0, 255).astype(np.uint8)
