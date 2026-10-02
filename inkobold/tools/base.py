from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from inkobold.core.document import Document
from inkobold.core.image_meta import prepare_paint_color, scale_threshold

# Attributes written into .inkobold workspace (per tool). Paths are optional.
_SETTING_KEYS = (
    "color",
    "brush_size",
    "threshold",
    "depth",
    "highlight",
    "bevel",
    "fill3d_type",
    "frequency",
    "intensity",
    "opacity",
    "density",
    "liquify_mode",
    "fill_mode",
    "tile_scale",
    "maze_cell_size",
    "corridor_color",
    "end_color",
    "replace_action",
    "apply_mode",
    "brush_mode",
    "curve_mode",
    "eraser_mode",
    "lay_mode",
    "arc_degrees",
    "lay_spacing",
    "font_source",
)
_PATH_KEYS = ("brush_path", "pattern_path", "font_path")


def _encode_setting(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    return None


def _decode_color(value: Any, fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return fallback
    try:
        return (
            max(0, min(255, int(value[0]))),
            max(0, min(255, int(value[1]))),
            max(0, min(255, int(value[2]))),
            max(0, min(255, int(value[3]))),
        )
    except (TypeError, ValueError):
        return fallback


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
    # Unit key-light direction in image space (y down); from canvas sun
    light_dir: tuple[float, float, float] = (-0.45, -0.55, 0.70)
    # 3D fill style: "classic" | "addiction" | "wavy" | "drift" | "depression"
    fill3d_type: str = "classic"
    # 3D pen stamp density (0–100)
    frequency: float = 60.0
    # Fill: "color" | "pattern" | "maze" | "puzzle"
    fill_mode: str = "color"
    tile_scale: float = 1.0
    maze_cell_size: float = 8.0
    corridor_color: tuple[int, int, int, int] = (255, 255, 255, 255)
    # Lay tool: spacing between pattern elements along the path
    lay_spacing: float = 40.0
    # Gradient tool end color
    end_color: tuple[int, int, int, int] = (255, 255, 255, 255)
    # Replace tool: "replace" | "erase"
    replace_action: str = "replace"
    # Replace tool scope: "brush" | "fill" | "all"
    apply_mode: str = "all"
    # Smear strength (0–100)
    intensity: float = 50.0
    # Paint / erase strength (0–100)
    opacity: float = 100.0
    # Bubbles brush stamp density (0–100)
    density: float = 50.0
    # When True, stamps and transforms wrap across opposite tile edges.
    tile_wrap: bool = False


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
    uses_lay_options: bool = False
    uses_brush_options: bool = False
    uses_replace_modes: bool = False
    uses_intensity: bool = False
    uses_opacity: bool = False
    uses_end_color: bool = False
    uses_curve_modes: bool = False
    uses_eraser_modes: bool = False
    uses_liquify_modes: bool = False
    uses_type_options: bool = False
    # Pointer coords in document space (not layer-local). Needed when the tool
    # changes layer offset — subtracting offset from the pointer would feedback.
    uses_document_coords: bool = False
    # When False, canvas skips GPU texture invalidation (offset-only tools).
    modifies_pixels: bool = True
    # Live Symmetry modifier — Mirror or Radial (Transform / Lasso opt out).
    supports_mirror: bool = True
    default_depth: float = 70.0
    default_highlight: float = 55.0
    default_bevel: float = 40.0
    default_fill3d_type: str = "classic"
    default_frequency: float = 60.0
    default_intensity: float = 50.0
    default_opacity: float = 100.0
    default_density: float = 50.0
    default_curve_mode: str = "freehand"
    default_eraser_mode: str = "freehand"
    default_arc_degrees: float = 180.0
    default_lay_spacing: float = 40.0
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
        self.density: float = self.default_density
        self.liquify_mode: str = self.default_liquify_mode

    def to_settings_dict(self) -> dict[str, Any]:
        """Serialize persistent tool options for the document workspace."""
        out: dict[str, Any] = {}
        for key in _SETTING_KEYS:
            if not hasattr(self, key):
                continue
            encoded = _encode_setting(getattr(self, key))
            if encoded is not None:
                out[key] = encoded
        for key in _PATH_KEYS:
            if not hasattr(self, key):
                continue
            path = getattr(self, key)
            out[key] = str(path) if path else None
        return out

    def apply_settings_dict(self, data: dict[str, Any]) -> None:
        """Restore options previously produced by ``to_settings_dict``."""
        if not isinstance(data, dict):
            return
        for key in _SETTING_KEYS:
            if key not in data or not hasattr(self, key):
                continue
            raw = data[key]
            current = getattr(self, key)
            if isinstance(current, tuple) and len(current) == 4:
                setattr(self, key, _decode_color(raw, current))
            elif isinstance(current, bool):
                setattr(self, key, bool(raw))
            elif isinstance(current, int) and not isinstance(current, bool):
                try:
                    setattr(self, key, int(raw))
                except (TypeError, ValueError):
                    pass
            elif isinstance(current, float):
                try:
                    setattr(self, key, float(raw))
                except (TypeError, ValueError):
                    pass
            elif isinstance(current, str):
                if isinstance(raw, str):
                    setattr(self, key, raw)
        for key in _PATH_KEYS:
            if key not in data or not hasattr(self, key):
                continue
            raw = data[key]
            if raw is None or raw == "":
                setattr(self, key, None)
            elif isinstance(raw, str):
                setattr(self, key, Path(raw))

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
        return max(0.1, ctx.brush_size * (0.35 + 0.65 * ctx.pressure))

    def _opacity_factor(self, ctx: ToolContext) -> float:
        return max(0.0, min(100.0, float(ctx.opacity))) / 100.0

    def _paint_color(self, ctx: ToolContext) -> tuple[int, int, int, int]:
        """Tool color scaled to document depth, with opacity on alpha."""
        r, g, b, a = ctx.color
        color = (r, g, b, int(round(a * self._opacity_factor(ctx))))
        return prepare_paint_color(color, ctx.document.color_depth)

    def _threshold(self, ctx: ToolContext) -> int:
        return scale_threshold(int(ctx.threshold), ctx.document.color_depth)
