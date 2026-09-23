"""3D-illusion pen and fill tools (gradient shading + specular highlights)."""

from __future__ import annotations

from inkobold.tools.base import BaseTool, ToolContext
from inkobold.tools.paint import (
    flood_fill_region,
    shade_region_3d,
    shade_region_addiction,
    shade_region_drift,
    shade_region_wavy,
    stroke_segment_3d,
)

_FILL3D_SHADERS = {
    "classic": shade_region_3d,
    "addiction": shade_region_addiction,
    "wavy": shade_region_wavy,
    "drift": shade_region_drift,
}


class Pen3DTool(BaseTool):
    """Stroke with spherical lighting — raised tube / bead look."""

    name = "3D Pen"
    id = "pen3d"
    default_size = 16.0
    default_color = (0, 0, 0, 255)
    uses_3d_settings = True
    uses_frequency = True
    uses_opacity = True
    default_depth = 72.0
    default_highlight = 60.0
    default_bevel = 45.0
    default_frequency = 80.0

    def __init__(self) -> None:
        super().__init__()
        self._drawing = False
        self._lx = 0.0
        self._ly = 0.0

    def on_press(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        self._drawing = True
        self._lx, self._ly = x, y
        stroke_segment_3d(
            ctx.document.active_layer.pixels,
            x, y, x, y,
            self._radius(ctx),
            self._paint_color(ctx),
            mask=self._mask(ctx),
            depth=ctx.depth,
            highlight=ctx.highlight,
            bevel=ctx.bevel,
            frequency=ctx.frequency,
            wrap=ctx.tile_wrap,
            light_dir=ctx.light_dir,
        )
        ctx.document.mark_dirty()

    def on_drag(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        if not self._drawing:
            return
        stroke_segment_3d(
            ctx.document.active_layer.pixels,
            self._lx, self._ly, x, y,
            self._radius(ctx),
            self._paint_color(ctx),
            mask=self._mask(ctx),
            depth=ctx.depth,
            highlight=ctx.highlight,
            bevel=ctx.bevel,
            frequency=ctx.frequency,
            wrap=ctx.tile_wrap,
            light_dir=ctx.light_dir,
        )
        self._lx, self._ly = x, y
        ctx.document.mark_dirty()

    def on_release(self, ctx: ToolContext, x: float, y: float) -> None:
        self._drawing = False

    def reset(self) -> None:
        self._drawing = False


class Fill3DTool(BaseTool):
    """Flood-fill then emboss with edge bevel, light gradient, and highlight."""

    name = "3D Fill"
    id = "fill3d"
    default_color = (0, 0, 0, 255)
    default_threshold = 200
    uses_size = False
    uses_threshold = True
    uses_3d_settings = True
    uses_fill3d_types = True
    uses_opacity = True
    default_depth = 95.0
    default_highlight = 82.0
    default_bevel = 38.0
    default_fill3d_type = "classic"

    def on_press(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        pixels = ctx.document.active_layer.pixels
        region = flood_fill_region(
            pixels,
            int(x),
            int(y),
            tolerance=self._threshold(ctx),
            mask=self._mask(ctx),
            wrap=ctx.tile_wrap,
        )
        if region is None or not region.any():
            return
        shade = _FILL3D_SHADERS.get(ctx.fill3d_type, shade_region_3d)
        shade(
            pixels,
            region,
            self._paint_color(ctx),
            depth=ctx.depth,
            highlight=ctx.highlight,
            bevel=ctx.bevel,
            light_dir=ctx.light_dir,
        )
        ctx.document.mark_dirty()
