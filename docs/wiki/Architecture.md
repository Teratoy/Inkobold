---
tags: [inkobold, wiki]
---

# Architecture

## Packages

| Package | Role |
|---------|------|
| `inkobold/app.py`, `__main__.py` | GTK `Application` (`HANDLES_OPEN`); `python -m inkobold [files…]` |
| `inkobold/ui/` | `MainWindow` and its mixins (below), `Canvas` (GLArea), dialogs, theme, color picker, `popup_keys` |
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
| `core/mirror.py` | Stroke Symmetry (Mirror / Radial / Linear Array) |
| `core/autosave.py` | Autosave sessions (locked folder per process) and crash recovery |
| `core/damage.py` | Dirty-rect tracking: freehand tools report the rectangle they paint so only that part of the layer texture is re-uploaded (`INKOBOLD_VERIFY_DAMAGE=1` checks it) |
| `gpu/renderer.py` | OpenGL composite |
| `ui/canvas.py` | GLArea + input routing; sun / symmetry guide drag |
| `ui/main_window.py` | `MainWindow`: composition root that owns the document, tools, history and settings and wires the pieces below |
| `ui/actions.py` | `win.*` Gio action table, shortcut install, View toggles, Settings dialogs |
| `ui/menus.py` | Menu-bar `Gio.Menu` models, Reload / Exit |
| `ui/doc_tabs.py` | Document tab strip; open / switch / close tabs |
| `ui/tool_options.py` | Tools tab: tool buttons, per-tool options, colors / palette, Symmetry |
| `ui/sidebar.py` | Frames timeline and Layers list; Layer menu actions |
| `ui/effects_actions.py` | Mirror / Flip / Rotate and the Effects menu with live preview |
| `ui/file_ops.py` | New / Open / Save / Export / Import, startup prompt, quit, save worker, autosave wiring, workspace snapshot |

Related: [[Overview]] · [[File Format]] · [[Input]]
