---
tags: [inkobold, wiki]
---

# Tools

Registered in `inkobold/tools/` via `default_tools()`. Shared paint helpers live in `paint.py`. Selection masks clip paint; tools with `supports_mirror` respect stroke [[View|Symmetry]] (Mirror / Radial) and Wrap Moves.

| Tool | id | Key | Behavior |
|------|-----|-----|----------|
| [[Pen\|Pen]] | `pen` | `P` | Constant-width freehand (no pressure) |
| [[Line\|Line]] | `line` | `L` | Straight segment; **Shift** → 45° snap |
| [[Rectangle\|Rectangle]] | `rectangle` | `O` | Outline; **Shift** → square; **Alt** → from center |
| [[Curve\|Curve]] | `curve` | `U` | Modes: freehand / arc / circle |
| [[Lay\|Lay]] | `lay` | `K` | Pattern stamps along line / curve path |
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

## Colors

Color tools show a **primary / secondary** pair; **⇄** or `X` swaps them. With **Settings → Tools →
Color follows tools** on, the pair is shared by every tool; off, each tool keeps its own pair.
The secondary color is the [[Gradient]] end, the maze corridor and the puzzle piece in [[Fill]].

The **Palette** strip below shows the active palette from [[Libraries]] → `palettes`:

| Action | Effect |
|--------|--------|
| Click / right-click a swatch | Set primary / secondary (keeps that color's alpha) |
| **+** | Add the primary color |
| Ctrl+click or middle-click | Rename / remove the swatch |
| Drag | Reorder |
| **⋯** | New, Duplicate, Rename, Delete (to trash), Import, Reload, Open Folder |

Edits are saved to the `.gpl` immediately. The color picker lists the same swatches under **Recent**.
