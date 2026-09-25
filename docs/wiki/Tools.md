---
tags: [inkobold, wiki]
---

# Tools

Registered in `inkobold/tools/` via `default_tools()`. Shared paint helpers live in `paint.py`. Selection masks clip paint; tools with `supports_mirror` respect stroke [[View|Mirror]] and Wrap Moves.

| Tool | id | Key | Behavior |
|------|-----|-----|----------|
| [[Pen\|Pen]] | `pen` | `P` | Constant-width freehand (no pressure) |
| [[Line\|Line]] | `line` | `L` | Straight segment; **Shift** → 45° snap |
| [[Rectangle\|Rectangle]] | `rectangle` | `O` | Outline; **Shift** → square; **Alt** → from center |
| [[Curve\|Curve]] | `curve` | `U` | Modes: freehand / arc / circle |
| [[Brush\|Brush]] | `brush` | `B` | Pressure; round / custom tip / bubbles |
| [[Weld Brush\|Weld Brush]] | `weld_brush` | `W` | Pressure + live morphological weld |
| [[Fill\|Fill]] | `fill` | `G` | Flood: color / pattern / maze / puzzle |
| [[Gradient\|Gradient]] | `gradient` | `A` | Drag axis → flood gradient |
| [[3D Pen\|3D Pen]] | `pen3d` | `D` | Lit tube stroke; uses [[View\|Sun]] |
| [[3D Fill\|3D Fill]] | `fill3d` | `F` | Flood then shade |
| [[Eraser\|Eraser]] | `eraser` | `E` | Freehand or line |
| [[Smear\|Smear]] | `smear` | `M` | Smudge |
| [[Liquify\|Liquify]] | `liquify` | `Y` | Push / Swirl / Pinch / Bulge brush |
| [[Replace\|Replace]] | `replace` | `C`/`R` | Replace or erase × brush / fill / all |
| [[Transform\|Transform]] | `move` | `V` | Move / scale / rotate |
| [[Lasso\|Lasso]] | `lasso` | `Q` | Selection; Shift add, Alt subtract |
| [[Type\|Type]] | `type` | `T` | Live text; Enter commits |

Visibility of individual tools can be toggled in **Settings → Tools**. See [[Shortcuts]].
