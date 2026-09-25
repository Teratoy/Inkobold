---
tags: [inkobold, wiki]
---

# Architecture

## Packages

| Package | Role |
|---------|------|
| `inkobold/app.py`, `__main__.py` | GTK `Application`; `python -m inkobold` |
| `inkobold/ui/` | `MainWindow`, `Canvas` (GLArea), dialogs, theme, color picker |
| `inkobold/tools/` | Drawing tools (`default_tools()`), shared ops in `paint.py` |
| `inkobold/gpu/` | `GpuRenderer` (composite, pan/zoom, checker, tile); `flora.py` |
| `inkobold/core/` | Document, layers, animation, effects, settings, paths, libraries, history, grid, sun, mirror |
| `inkobold/input/` | `InputHub`: GDK always; libinput + libwacom on Linux |

## Data flow

```
pointer events
  → tool
  → active layer pixels
  → history snapshot
  → GPU upload / composite
  → GLArea
```

Effects: CPU (NumPy). Flora: GPU attempt then CPU.

## Key modules

| Module | Responsibility |
|--------|----------------|
| `core/document.py` | `.inkobold` I/O |
| `core/layer.py` | RGBA buffers + offsets |
| `core/animation.py` | Cels / FPS / onion |
| `core/effects.py` | Menu effects |
| `core/shortcuts.py` | Defaults + rebinds |
| `core/settings.py` | Persistence |
| `core/paths.py` | XDG / APPDATA resolution |
| `gpu/renderer.py` | OpenGL composite |
| `ui/canvas.py` | GLArea + input routing |

Related: [[Overview]] · [[File Format]] · [[Input]]
