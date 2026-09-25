---
tags: [inkobold, wiki]
---

# View

Canvas defaults: pan `(40, 40)`, zoom `1.0`. Zoom clamp **0.05–32**. App starts **borderless**.

| Feature | Control | Notes |
|---------|---------|-------|
| Pan | Middle-mouse drag | |
| Zoom | Scroll wheel | |
| Fit Canvas | `Ctrl+0`, `Ctrl+F` | |
| Show Grid | View menu | Visual only; default 8×8 bands (1–64); no snap |
| Grid… | Settings | rows / columns |
| Show Sun | View + drag glyph | Key light for 3D tools / Metal / Milk |
| Sun… | Elevation etc. | Persisted; elevation 0.15–1.5 |
| Tile Preview | `Ctrl+Shift+T` | 3×3 tiled composite |
| Wrap Moves | `Ctrl+Shift+W` | Toroidal stamps/transforms; follows Tile Preview by default |
| Checker BG | View / settings | `checker_light` |
| Fullscreen / Borderless / Windowed | `F11` / menu | |
| Clear selection | `Esc`, `Ctrl+D` | |

## Mirror

Stroke mirror: **1–16** axes; horizontal / vertical / diagonal. Applies to tools with `supports_mirror` (not Transform).

## Sun

Defaults: `x_norm`/`y_norm` = **0.18**, `elevation` = **0.70**. Used by [[Tools/3D Pen|3D Pen]], [[Tools/3D Fill|3D Fill]], [[Effects/Materials|Materials]].

Related: [[Paths and Settings]] · [[Architecture]]
