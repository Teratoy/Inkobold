---
tags: [inkobold, wiki]
aliases: [What's new]
---

# Changelog Notes

Notes for recent waves (around 2026-09). Not a full changelog.

| Feature | Where | Notes |
|---------|-------|-------|
| **Lay tool** | `tools/lay.py` | id `lay`; shortcut **`K`**; stamps pattern-library images along line / freehand / arc / circle |
| **Workspace in `.inkobold`** | `document.py` meta **v4** | Per-document tool options, mirror/grid/wrap, zoom/pan — see [[File Format]] |
| **3D Fill wrap emboss** | `paint.shade_region_*` | With Wrap Moves, edge-straddling fills unroll + toroidal blur/normals |
| **3D Fill `depression`** | Fill3D modes | Extra shade mode alongside classic / addiction / wavy / drift |
| **Layer order actions** | Shortcuts (unbound) | Move Layer to Top / Bottom (menu; rebindable) |
| **Rectangle tool** | `tools/rectangle.py` | id `rectangle`; shortcut **`O`**; Shift square, Alt from center |
| **Flora effect** | `effects.flora`, `gpu/flora.py` | Stylize menu; long edge ≤1280 |
| **Windows support** | `docs/WINDOWS.md`, `scripts/run.ps1`, `scripts/windows/inkobold.spec` | GDK-only input |
| **paths module** | `core/paths.py` | XDG vs APPDATA/LOCALAPPDATA; `INKOBOLD_*_HOME` |
| **Show Sun** | View + settings | Glyph for aiming sun light |
| **Use / Deactivate Sun** | View + Sun… | Classic fixed light when off |
| **Gradient tool** | `tools/gradient.py` | key `A` |
| **Categorized effects** | Main window menus | Blur, Stylize, Color, Distort, Edge, Materials |
| **Float settings** | Tool options + effects | Continuous spins accept hundredths (`0.01` / `0,01`) |
| **Subpixel brush size** | Size spin / `[` `]` | Minimum size **0.1** (was 1) |
| **Merge visible / export layer** | Layers menu | Merge visible keeps hidden layers; export active layer as document-sized PNG |
| **Popup keys** | `ui/popup_keys.py` | Esc close, Enter confirm, Tab among editables |
| **Theme complement** | `ui/theme.py` | GTK selection / focus chrome uses complementary hue |

See also [[Lay]] · [[File Format]] · [[Rectangle]] · [[Stylize]] · [[Windows]] · [[Paths and Settings]] · [[View]]
