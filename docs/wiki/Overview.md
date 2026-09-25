---
tags: [inkobold, wiki]
---

# Overview

Inkobold is a **GPU-composited** painting app: you draw into CPU layer buffers; an OpenGL renderer composites visible layers (plus onion skin, selection, checker, grid, sun glyph) into a GTK `GLArea`.

## What it is good at

- Multilayer documents with undo history
- Frame-based animation and onion skin
- Pressure-aware brushes (when the input path reports pressure)
- Lit 3D strokes / fills driven by a movable **Sun**
- Procedural / stylize effects (including Flora)
- Seamless tile painting (Tile Preview + Wrap Moves)

## Platforms

| Platform | UI / GL | Input |
|----------|---------|--------|
| Linux | GTK4 + OpenGL | GDK + optional libinput / libwacom |
| Windows | GTK4 + OpenGL (MSYS2 or gvsbuild) | GDK only |

## Mental model

```
pointer → tool → active layer pixels
              → history snapshot
              → GPU upload / composite → GLArea
```

Effects are mostly **CPU (NumPy)**; Flora tries GPU then falls back to CPU.

See [[Architecture]] for package layout.
