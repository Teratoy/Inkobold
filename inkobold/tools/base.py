from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from inkobold.core.document import Document


@dataclass
class ToolContext:
    document: Document
    color: tuple[int, int, int, int] = (0, 0, 0, 255)
    brush_size: float = 6.0
    pressure: float = 1.0
    threshold: int = 28
    # 3D illusion (0–100)
    depth: float = 70.0
    highlight: float = 55.0
    bevel: float = 40.0
    # 3D fill style: "classic" | "addiction" | "wavy" | "drift"
    fill3d_type: str = "classic"
    # 3D pen stamp density (0–100)
    frequency: float = 60.0
    # Fill: solid color vs tiled pattern
    fill_mode: str = "color"
    tile_scale: float = 1.0
    # Replace tool: "replace" | "erase"
    replace_action: str = "replace"
    # Replace tool scope: "brush" | "fill" | "all"
    apply_mode: str = "all"
    # Smear strength (0–100)
    intensity: float = 50.0
    # Paint / erase strength (0–100)
    opacity: float = 100.0


class Tool(Protocol):
    name: str
    id: str
    color: tuple[int, int, int, int]
    brush_size: float
    threshold: int
    uses_threshold: bool

    def on_press(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None: ...
    def on_drag(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None: ...
    def on_release(self, ctx: ToolContext, x: float, y: float) -> None: ...
    def reset(self) -> None: ...


class BaseTool:
    name = "Tool"
    id = "tool"
    default_color: tuple[int, int, int, int] = (0, 0, 0, 255)
    default_size: float = 8.0
    default_threshold: int = 28
    uses_size: bool = True
    uses_color: bool = True
    uses_threshold: bool = False
    uses_3d_settings: bool = False
    uses_fill3d_types: bool = False
    uses_frequency: bool = False
    uses_fill_options: bool = False
    uses_replace_modes: bool = False
    uses_intensity: bool = False
    uses_opacity: bool = False
    uses_curve_modes: bool = False
    uses_liquify_modes: bool = False
    uses_type_options: bool = False
    # Pointer coords in document space (not layer-local). Needed when the tool
    # changes layer offset — subtracting offset from the pointer would feedback.
    uses_document_coords: bool = False
    # When False, canvas skips GPU texture invalidation (offset-only tools).
    modifies_pixels: bool = True
    # Live mirror / radial symmetry (Transform / Lasso opt out).
    supports_mirror: bool = True
    default_depth: float = 70.0
    default_highlight: float = 55.0
    default_bevel: float = 40.0
    default_fill3d_type: str = "classic"
    default_frequency: float = 60.0
    default_intensity: float = 50.0
    default_opacity: float = 100.0
    default_curve_mode: str = "freehand"
    default_arc_degrees: float = 180.0
    default_liquify_mode: str = "Push"

    def __init__(self) -> None:
        self.color: tuple[int, int, int, int] = self.default_color
        self.brush_size: float = self.default_size
        self.threshold: int = self.default_threshold
        self.depth: float = self.default_depth
        self.highlight: float = self.default_highlight
        self.bevel: float = self.default_bevel
        self.fill3d_type: str = self.default_fill3d_type
        self.frequency: float = self.default_frequency
        self.intensity: float = self.default_intensity
        self.opacity: float = self.default_opacity
        self.liquify_mode: str = self.default_liquify_mode

    def on_press(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        pass

    def on_drag(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        pass

    def on_release(self, ctx: ToolContext, x: float, y: float) -> None:
        pass

    def reset(self) -> None:
        pass

    def _mask(self, ctx: ToolContext):
        sel = ctx.document.selection
        return sel.mask if sel.active else None

    def _radius(self, ctx: ToolContext) -> float:
        return max(0.5, ctx.brush_size * (0.35 + 0.65 * ctx.pressure))

    def _opacity_factor(self, ctx: ToolContext) -> float:
        return max(0.0, min(100.0, float(ctx.opacity))) / 100.0

    def _paint_color(self, ctx: ToolContext) -> tuple[int, int, int, int]:
        """Tool color scaled to document depth, with opacity on alpha."""
        from inkobold.core.image_meta import prepare_paint_color

        r, g, b, a = ctx.color
        color = (r, g, b, int(round(a * self._opacity_factor(ctx))))
        return prepare_paint_color(color, ctx.document.color_depth)

    def _threshold(self, ctx: ToolContext) -> int:
        from inkobold.core.image_meta import scale_threshold

        return scale_threshold(int(ctx.threshold), ctx.document.color_depth)
