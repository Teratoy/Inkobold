"""Simple frame-based animation (cels + playback timing)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from inkobold.core.layer import Layer


DEFAULT_FPS = 12.0
DEFAULT_ONION_OPACITY = 0.35


@dataclass
class AnimFrame:
    """One animation cel: its own layer stack."""

    name: str = "Frame 1"
    layers: list[Layer] = field(default_factory=list)
    active_layer_index: int = 0

    def clone(self, *, name: str | None = None) -> AnimFrame:
        layers = [clone_layer(ly) for ly in self.layers]
        return AnimFrame(
            name=name or self.name,
            layers=layers,
            active_layer_index=min(self.active_layer_index, max(0, len(layers) - 1)),
        )


def clone_layer(ly: Layer) -> Layer:
    """Deep-copy a layer (new id, copied pixels)."""
    out = Layer(
        name=ly.name,
        width=ly.width,
        height=ly.height,
        visible=ly.visible,
        opacity=ly.opacity,
        offset_x=ly.offset_x,
        offset_y=ly.offset_y,
        color_depth=ly.color_depth,
    )
    out.pixels = np.ascontiguousarray(ly.pixels.copy())
    out.bump()
    return out


def blank_frame(
    width: int,
    height: int,
    *,
    name: str = "Frame 1",
    layer_name: str = "Layer 1",
    color_depth: int,
) -> AnimFrame:
    frame = AnimFrame(name=name)
    frame.layers.append(
        Layer(name=layer_name, width=width, height=height, color_depth=color_depth)
    )
    return frame
