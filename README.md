# Inkobold

GPU-accelerated drawing tool for Linux, with **libinput** / **libwacom** tablet awareness.

## Features

- **File** — Multi-document tabs; New File (pixel size prompt), Open, Save (`.inkobold` multilayer ZIP), Export, Export Separated Layers, Import Image as Layer
- **Layers** — RGBA8 layers, visibility, opacity-ready offsets, GPU composite; add / duplicate / rename / delete / merge down / merge all
- **Animation** — Frames with FPS playback; onion-skin overlay of the previous frame while drawing; export as GIF or PNG sequence folder
- **Tools** — Pen, Line, Curve (freehand / arc / circle), Brush (round / custom tip / bubbles), Weld Brush, Fill, Gradient, 3D Pen, 3D Fill, Eraser, Smear, Liquify, Replace (replace/erase × brush/fill/all), Transform (move / scale / rotate), Lasso, Type
- **Effects** — Pixelate, Kuwahara, Gaussian Blur, Drop Shadow, Dither, Posterize, Curves, Threshold, Liquify, Edge Detect, Emboss, Normal Map, Metal Relief, Milk (apply to active layer or all layers)
- **Libraries** — User brushes, patterns, and fonts under `~/.local/share/inkobold/libraries/` (or `$XDG_DATA_HOME/inkobold/libraries/`)
- **Input** — GDK drawing path + libinput device poll + libwacom tablet identification
- **View** — Pan (middle mouse), scroll zoom, Fit Canvas, grid overlay, Show Sun (draggable light for 3D tools / Metal Relief / Milk), Tile Preview, Wrap Moves, checker background, Fullscreen / Borderless / Windowed
- **Edit** — Undo / Redo; stroke Mirror (1–16 axes, horizontal / vertical / diagonal); Flip / Rotate active layer; Crop Canvas (live size before apply; optional selection bounds)
- **Settings** — Theme, Memory (undo steps), Image (crop / DPI / color depth), Tools (color follows tools, visible tools), Shortcuts (view & rebind), Debug Mode

## Run

```bash
./scripts/run.sh
```

Or:

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m inkobold
```

Requires system **PyGObject** + **GTK4** (install via your distro; use a venv with `--system-site-packages`).

## Shortcuts

| Action | Keys |
|--------|------|
| New / Open / Save / Save As | `Ctrl+N` `Ctrl+O` `Ctrl+S` `Ctrl+Shift+S` |
| Import image / Export | `Ctrl+I` / `Ctrl+E` |
| Undo / Redo | `Ctrl+Z` / `Ctrl+Shift+Z` or `Ctrl+Y` |
| Fit Canvas | `Ctrl+0` or `Ctrl+F` |
| Fullscreen | `F11` |
| Clear selection | `Esc` or `Ctrl+D` |
| Tile Preview / Wrap Moves | `Ctrl+Shift+T` / `Ctrl+Shift+W` |
| Add / duplicate / delete layer | `Ctrl+Shift+N` / `Ctrl+J` / `Ctrl+Shift+Delete` |
| Merge layer down | `Ctrl+Shift+E` |
| Add / duplicate / delete frame | `Ctrl+Alt+N` / `Ctrl+Alt+D` / `Ctrl+Alt+Delete` |
| Play / pause animation | `Space` or `F5` |
| Stop animation | `F6` |
| Brush size | `[` `]` |
| Tools | `P` `L` `U` `B` `W` `G` `A` `D` `F` `E` `M` `Y` `C`/`R` `V` `Q` `T` |
| Pan | Middle mouse drag |

Shortcuts are rebindable under **Settings → Shortcuts**.

## File format

`.inkobold` is a ZIP containing `document.json` and `layers/NNN.png` (RGBA).

## Author

[KTNplayground](https://github.com/Teratoy)
