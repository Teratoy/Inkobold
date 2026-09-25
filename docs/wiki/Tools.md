---
tags: [inkobold, wiki]
---

# Tools

Registered in `inkobold/tools/` via `default_tools()`. Shared paint helpers live in `paint.py`. Selection masks clip paint; tools with `supports_mirror` respect stroke [[View|Mirror]] and Wrap Moves.

| Tool | id | Key | Behavior |
|------|-----|-----|----------|
| [[Tools/Pen\|Pen]] | `pen` | `P` | Constant-width freehand (no pressure) |
| [[Tools/Line\|Line]] | `line` | `L` | Straight segment; **Shift** → 45° snap |
| [[Tools/Rectangle\|Rectangle]] | `rectangle` | `O` | Outline; **Shift** → square; **Alt** → from center |
| [[Tools/Curve\|Curve]] | `curve` | `U` | Modes: freehand / arc / circle |
| [[Tools/Brush\|Brush]] | `brush` | `B` | Pressure; round / custom tip / bubbles |
| [[Tools/Weld Brush\|Weld Brush]] | `weld_brush` | `W` | Pressure + live morphological weld |
| [[Tools/Fill\|Fill]] | `fill` | `G` | Flood: color / pattern / maze / puzzle |
| [[Tools/Gradient\|Gradient]] | `gradient` | `A` | Drag axis → flood gradient |
| [[Tools/3D Pen\|3D Pen]] | `pen3d` | `D` | Lit tube stroke; uses [[View\|Sun]] |
| [[Tools/3D Fill\|3D Fill]] | `fill3d` | `F` | Flood then shade |
| [[Tools/Eraser\|Eraser]] | `eraser` | `E` | Freehand or line |
| [[Tools/Smear\|Smear]] | `smear` | `M` | Smudge |
| [[Tools/Liquify\|Liquify]] | `liquify` | `Y` | Push / Swirl / Pinch / Bulge brush |
| [[Tools/Replace\|Replace]] | `replace` | `C`/`R` | Replace or erase × brush / fill / all |
| [[Tools/Transform\|Transform]] | `move` | `V` | Move / scale / rotate |
| [[Tools/Lasso\|Lasso]] | `lasso` | `Q` | Selection; Shift add, Alt subtract |
| [[Tools/Type\|Type]] | `type` | `T` | Live text; Enter commits |

Visibility of individual tools can be toggled in **Settings → Tools**. See [[Shortcuts]].
