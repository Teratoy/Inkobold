---
tags: [inkobold, wiki]
---

# Animation

Implemented in `inkobold/core/animation.py`.

| Constant | Default |
|----------|---------|
| `DEFAULT_FPS` | `12.0` |
| `onion_skin` | `True` |
| `DEFAULT_ONION_OPACITY` | `0.35` |

## Model

Each `AnimFrame` is a cel with its own layer stack. Documents store `fps`, onion settings, and `current_frame_index`.

## Controls

| Action | Default |
|--------|---------|
| Add / Dup / Delete frame | `Ctrl+Alt+N` / `Ctrl+Alt+D` / `Ctrl+Alt+Delete` |
| Play / pause | `Space` or `F5` |
| Stop | `F6` |

## Onion skin

While drawing, the previous frame composites under the current at `onion_opacity`.

## Export

- **GIF** — fps → frame duration ms, loop 0, disposal 2
- **PNG sequence** — `frame_NNN.png` in a folder
- **Sprite sheet** — every frame left to right in one PNG row (Godot `hframes` = frame count), plus `<name>.json` beside it with `frame_width`, `frame_height`, `columns`, `rows`, `scale`, `fps` and each frame's `name`, `x`, `y`, `w`, `h`, `duration_ms`
- Flattened image / separated layers — see File menu

**File → Export Scale** (1×, 2×, 3×, 4×, 6×, 8×) enlarges every export (image, selected layer, separated layers, GIF, PNG sequence, sprite sheet) by a whole factor, each pixel becoming a hard block with no smoothing. It is saved in the document workspace, and export dialog titles show it when it is above 1×.

Multi-frame GIF open becomes an animation. See [[File Format]].
