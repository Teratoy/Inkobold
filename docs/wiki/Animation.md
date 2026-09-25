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
- Flattened image / separated layers — see File menu

Multi-frame GIF open becomes an animation. See [[File Format]].
