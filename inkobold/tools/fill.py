from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from inkobold.core.image_meta import prepare_paint_color
from inkobold.tools.base import BaseTool, ToolContext
from inkobold.tools.paint import flood_fill, flood_fill_maze, flood_fill_pattern, flood_fill_puzzle


class FillTool(BaseTool):
    name = "Fill"
    id = "fill"
    default_color = (0, 0, 0, 255)
    default_threshold = 200
    uses_size = False
    uses_threshold = True
    uses_fill_options = True
    uses_opacity = True
    default_tile_scale = 1.0
    default_maze_cell_size = 8.0
    default_corridor_color = (255, 255, 255, 255)

    def __init__(self) -> None:
        super().__init__()
        self.fill_mode: str = "color"  # "color" | "pattern" | "maze" | "puzzle"
        self.tile_scale: float = self.default_tile_scale
        self.maze_cell_size: float = self.default_maze_cell_size
        self.corridor_color: tuple[int, int, int, int] = self.default_corridor_color
        self.pattern_path: Optional[Path] = None
        self._pattern_cache_path: Optional[Path] = None
        self._pattern_cache_mtime: float = -1.0
        self._pattern_pixels: Optional[np.ndarray] = None

    def _corridor_paint_color(self, ctx: ToolContext) -> tuple[int, int, int, int]:
        r, g, b, a = getattr(ctx, "corridor_color", None) or self.corridor_color
        color = (r, g, b, int(round(a * self._opacity_factor(ctx))))
        return prepare_paint_color(color, ctx.document.color_depth)

    def set_pattern_path(self, path: Optional[Path]) -> None:
        self.pattern_path = Path(path) if path is not None else None
        self._pattern_pixels = None
        self._pattern_cache_path = None
        self._pattern_cache_mtime = -1.0

    def _load_pattern(self) -> Optional[np.ndarray]:
        path = self.pattern_path
        if path is None or not path.is_file():
            return None
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        if (
            self._pattern_pixels is not None
            and self._pattern_cache_path == path
            and self._pattern_cache_mtime == mtime
        ):
            return self._pattern_pixels
        try:
            arr = np.array(Image.open(path).convert("RGBA"), dtype=np.uint8)
        except OSError:
            return None
        self._pattern_pixels = arr
        self._pattern_cache_path = path
        self._pattern_cache_mtime = mtime
        return arr

    def on_press(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        mode = getattr(ctx, "fill_mode", None) or self.fill_mode
        wrap = ctx.tile_wrap
        if mode == "pattern":
            pattern = self._load_pattern()
            if pattern is None:
                return
            scale = float(getattr(ctx, "tile_scale", self.tile_scale))
            flood_fill_pattern(
                ctx.document.active_layer.pixels,
                int(x),
                int(y),
                pattern,
                scale=scale,
                tolerance=self._threshold(ctx),
                mask=self._mask(ctx),
                opacity=self._opacity_factor(ctx),
                wrap=wrap,
            )
        elif mode == "maze":
            flood_fill_maze(
                ctx.document.active_layer.pixels,
                int(x),
                int(y),
                self._paint_color(ctx),
                tolerance=self._threshold(ctx),
                mask=self._mask(ctx),
                cell_size=float(getattr(ctx, "maze_cell_size", self.maze_cell_size)),
                corridor_color=self._corridor_paint_color(ctx),
                wrap=wrap,
            )
        elif mode == "puzzle":
            flood_fill_puzzle(
                ctx.document.active_layer.pixels,
                int(x),
                int(y),
                self._paint_color(ctx),
                tolerance=self._threshold(ctx),
                mask=self._mask(ctx),
                cell_size=float(getattr(ctx, "maze_cell_size", self.maze_cell_size)),
                corridor_color=self._corridor_paint_color(ctx),
                wrap=wrap,
            )
        else:
            flood_fill(
                ctx.document.active_layer.pixels,
                int(x),
                int(y),
                self._paint_color(ctx),
                tolerance=self._threshold(ctx),
                mask=self._mask(ctx),
                wrap=wrap,
            )
        ctx.document.mark_dirty()
