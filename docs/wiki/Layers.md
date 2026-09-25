---
tags: [inkobold, wiki]
---

# Layers

## Document model

- Canvas `width` × `height`
- Per-frame layer stack; `active_layer_index`
- Binary selection mask (255 = selected)
- Color depth: see [[Paths and Settings]]

## Layer fields

`name`, `visible`, `opacity`, `offset_x` / `offset_y`, `id`, straight RGBA `pixels` (uint8 or uint16).

## Operations

| Op | Shortcut (default) |
|----|--------------------|
| Add | `Ctrl+Shift+N` |
| Duplicate | `Ctrl+J` |
| Delete | `Ctrl+Shift+Delete` |
| Merge down | `Ctrl+Shift+E` |
| Merge all | menu only |
| Rename / reorder | UI |
| Flip H / V | Edit menu |
| Rotate 90 / 180 / CCW | Edit menu |
| Crop canvas | Settings / Image (optional selection bounds) |

Composite is GPU-side; see [[Architecture]]. Persistence: [[File Format]].
