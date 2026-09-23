from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from inkobold.tools.base import BaseTool, ToolContext
from inkobold.core.sun import DEFAULT_MILK_LIGHT_DIR
from inkobold.tools.paint import (
    bubble_influence_bbox,
    collect_bubbles_along_segment,
    render_bubbles_list,
    stroke_segment_tapered,
)

Bubble = tuple[float, float, float, float]

# Upper bound on bubbles re-blended per stroke (render cost ∝ total bubble area).
MAX_STROKE_BUBBLES = 4096


def bubbles_region(bubs: list[Bubble]) -> tuple[int, int, int, int]:
    """Union of influence bboxes (``x0, y0, x1, y1``, exclusive, unclipped)."""
    x0 = y0 = 1 << 30
    x1 = y1 = -(1 << 30)
    for bx, by, rad, _ in bubs:
        bx0, by0, bx1, by1 = bubble_influence_bbox(bx, by, rad)
        x0, y0 = min(x0, bx0), min(y0, by0)
        x1, y1 = max(x1, bx1), max(y1, by1)
    return x0, y0, x1, y1


def bubbles_touching(
    bubs: list[Bubble], region: tuple[int, int, int, int]
) -> list[Bubble]:
    """Subset of ``bubs`` whose influence bbox intersects ``region``."""
    rx0, ry0, rx1, ry1 = region
    out: list[Bubble] = []
    for b in bubs:
        bx0, by0, bx1, by1 = bubble_influence_bbox(b[0], b[1], b[2])
        if bx0 < rx1 and bx1 > rx0 and by0 < ry1 and by1 > ry0:
            out.append(b)
    return out


def render_bubbles_incremental(
    pixels: np.ndarray,
    base: np.ndarray,
    placed: list[Bubble],
    new_bubs: list[Bubble],
    color: tuple[int, int, int, int],
    mask: np.ndarray | None,
    opacity: float,
    light_dir: tuple[float, float, float] = DEFAULT_MILK_LIGHT_DIR,
) -> None:
    """Exact live update: only the region ``new_bubs`` can influence is
    restored from ``base`` and re-rendered with every bubble touching it."""
    h, w = pixels.shape[:2]
    x0, y0, x1, y1 = bubbles_region(new_bubs)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x0 >= x1 or y0 >= y1:
        return
    pixels[y0:y1, x0:x1] = base[y0:y1, x0:x1]
    render_bubbles_list(
        pixels,
        bubbles_touching(placed, (x0, y0, x1, y1)),
        color,
        mask=mask,
        opacity=opacity,
        clip=(x0, y0, x1, y1),
        light_dir=light_dir,
    )


class BrushTool(BaseTool):
    """Freehand stroke whose width tracks pen pressure (Size = max width).

    Modes:
    - ``round``: soft disk stamps (default)
    - ``custom``: stamp a PNG tip from Libraries → Brushes, tinted with Color
    - ``bubbles``: milk-style air-bubble clusters tinted with Color
    """

    name = "Brush"
    id = "brush"
    default_size = 12.0
    default_color = (0, 0, 0, 255)
    default_density = 10.0
    uses_opacity = True
    uses_brush_options = True

    def __init__(self) -> None:
        super().__init__()
        self.brush_mode: str = "round"  # "round" | "custom" | "bubbles"
        self.brush_path: Optional[Path] = None
        self._brush_cache_path: Optional[Path] = None
        self._brush_cache_mtime: float = -1.0
        self._brush_pixels: Optional[np.ndarray] = None
        self._drawing = False
        self._lx = 0.0
        self._ly = 0.0
        self._lr = 0.5
        # Bubbles stroke accumulate — blended together on release.
        self._bubble_placed: list[tuple[float, float, float, float]] = []
        self._bubble_base: Optional[np.ndarray] = None
        self._bubble_color: tuple[int, int, int, int] = (255, 255, 255, 255)
        self._bubble_opacity: float = 1.0
        self._bubble_wrap: bool = False
        self._bubble_light_dir: tuple[float, float, float] = DEFAULT_MILK_LIGHT_DIR

    def set_brush_path(self, path: Optional[Path]) -> None:
        self.brush_path = Path(path) if path is not None else None
        self._brush_pixels = None
        self._brush_cache_path = None
        self._brush_cache_mtime = -1.0

    def begin_bubble_stroke(
        self,
        base_pixels: np.ndarray,
        shared_placed: Optional[list[Bubble]] = None,
    ) -> None:
        """Share a pre-stroke layer snapshot (so mirror clones restore the same base).

        ``shared_placed`` lets every mirror branch append to one list, so the
        incremental live preview of one branch accounts for bubbles painted
        by the others instead of erasing them.
        """
        self._bubble_base = base_pixels
        self._bubble_placed = shared_placed if shared_placed is not None else []

    def _load_brush(self) -> Optional[np.ndarray]:
        path = self.brush_path
        if path is None or not path.is_file():
            return None
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        if (
            self._brush_pixels is not None
            and self._brush_cache_path == path
            and self._brush_cache_mtime == mtime
        ):
            return self._brush_pixels
        try:
            arr = np.array(Image.open(path).convert("RGBA"), dtype=np.uint8)
        except OSError:
            return None
        self._brush_pixels = arr
        self._brush_cache_path = path
        self._brush_cache_mtime = mtime
        return arr

    def _pressure_radius(self, ctx: ToolContext) -> float:
        # Size is the maximum radius; light pressure → thin, full press → Size.
        p = max(0.0, min(1.0, float(ctx.pressure)))
        # Mild power curve feels more natural on tablets than pure linear.
        shaped = p**0.75
        return max(0.5, ctx.brush_size * (0.06 + 0.94 * shaped))

    def _stamp_kwargs(self, ctx: ToolContext) -> dict:
        mode = getattr(self, "brush_mode", "round")
        kwargs: dict = {"opacity": self._opacity_factor(ctx), "wrap": ctx.tile_wrap}
        if mode == "custom":
            tip = self._load_brush()
            if tip is not None:
                kwargs["tip"] = tip
        return kwargs

    def _density(self, ctx: ToolContext) -> float:
        return float(getattr(ctx, "density", getattr(self, "density", 50.0)))

    def _paint_bubbles_segment(
        self,
        ctx: ToolContext,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        r0: float,
        r1: float,
        *,
        force: bool,
    ) -> None:
        pixels = ctx.document.active_layer.pixels
        color = self._paint_color(ctx)
        mask = self._mask(ctx)
        opacity = self._opacity_factor(ctx)
        density = self._density(ctx)
        self._bubble_color = color
        self._bubble_opacity = opacity
        self._bubble_wrap = bool(ctx.tile_wrap)
        self._bubble_light_dir = tuple(ctx.light_dir)

        if self._bubble_base is None or self._bubble_base.shape != pixels.shape:
            self._bubble_base = np.array(pixels, copy=True)
            self._bubble_placed = []

        new_bubs = collect_bubbles_along_segment(
            x0, y0, x1, y1, r0, r1, density=density, force=force,
        )
        if not new_bubs:
            return
        self._bubble_placed.extend(new_bubs)
        # Exact live preview: re-render only the region the new bubbles touch,
        # together with every earlier bubble overlapping it, so overlaps blend
        # seamlessly while dragging (not just after release).
        render_bubbles_incremental(
            pixels, self._bubble_base, self._bubble_placed, new_bubs,
            color, mask, opacity, light_dir=self._bubble_light_dir,
        )

    def _finalize_bubbles(self, ctx: ToolContext) -> None:
        """Restore pre-stroke pixels and reblend every bubble from this stroke."""
        placed = self._bubble_placed
        base = self._bubble_base
        self._bubble_placed = []
        self._bubble_base = None
        if not placed or base is None:
            return
        pixels = ctx.document.active_layer.pixels
        if pixels.shape != base.shape:
            return
        pixels[...] = base
        # Guard against pathological strokes (render cost ∝ total bubble area).
        if len(placed) > MAX_STROKE_BUBBLES:
            placed = placed[:MAX_STROKE_BUBBLES]
        render_bubbles_list(
            pixels,
            placed,
            self._bubble_color,
            mask=self._mask(ctx),
            opacity=self._bubble_opacity,
            light_dir=self._bubble_light_dir,
        )

    def take_bubble_stroke(
        self,
    ) -> tuple[Optional[np.ndarray], list[Bubble], tuple[int, int, int, int], float]:
        """Hand off accumulated bubbles for a coordinated multi-branch finalize.

        Returns the accumulated list object itself (not a copy): mirror
        branches share one list, so callers should dedupe by identity.
        """
        base = self._bubble_base
        placed = self._bubble_placed
        color = self._bubble_color
        opacity = self._bubble_opacity
        self._bubble_base = None
        self._bubble_placed = []
        return base, placed, color, opacity

    def _paint_segment(
        self,
        ctx: ToolContext,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        r0: float,
        r1: float,
        *,
        force: bool = False,
    ) -> None:
        mode = getattr(self, "brush_mode", "round")
        if mode == "bubbles":
            self._paint_bubbles_segment(ctx, x0, y0, x1, y1, r0, r1, force=force)
            return
        pixels = ctx.document.active_layer.pixels
        color = self._paint_color(ctx)
        mask = self._mask(ctx)
        stroke_segment_tapered(
            pixels, x0, y0, x1, y1, r0, r1, color,
            mask=mask,
            **self._stamp_kwargs(ctx),
        )

    def on_press(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        self._drawing = True
        self._lx, self._ly = x, y
        self._lr = self._pressure_radius(ctx)
        self._paint_segment(ctx, x, y, x, y, self._lr, self._lr, force=True)
        ctx.document.mark_dirty()

    def on_drag(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        if not self._drawing:
            return
        r1 = self._pressure_radius(ctx)
        self._paint_segment(ctx, self._lx, self._ly, x, y, self._lr, r1, force=False)
        self._lx, self._ly = x, y
        self._lr = r1
        ctx.document.mark_dirty()

    def on_release(self, ctx: ToolContext, x: float, y: float) -> None:
        self._drawing = False
        if getattr(self, "brush_mode", "round") == "bubbles":
            self._finalize_bubbles(ctx)
            ctx.document.mark_dirty()

    def reset(self) -> None:
        self._drawing = False
        self._bubble_placed = []
        self._bubble_base = None
