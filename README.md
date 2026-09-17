# Inkobold

GPU-accelerated layered drawing for Linux, with **libinput** / **libwacom** tablet awareness.

## Features

- **File** — New File (pixel size prompt), Open, Save (`.inkobold` multilayer ZIP)
- **Layers** — RGBA8 layers, visibility, opacity-ready offsets, GPU composite
- **Animation** — Frames with FPS playback; onion-skin overlay of the previous frame while drawing; export as GIF or PNG sequence folder
- **Tools** — Pen, Line, Curve (freehand / arc / circle), Brush, Fill, Eraser, Replace (replace/erase × brush/fill/all), Transform (move / scale / rotate), Lasso
- **Import** — Image import as a new layer
- **Input** — GDK drawing path + libinput device poll + libwacom tablet identification
- **View** — Pan (middle mouse), scroll zoom, Fit Canvas (on load), Fullscreen / Borderless / Windowed
- **Edit** — Mirror / Flip / Rotate active layer; Crop Canvas (live size before apply; optional selection bounds)
- **Settings** — Theme, Memory, Image (crop / DPI / color depth), Shortcuts (view & rebind)

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

## Shortcuts

| Action | Keys |
|--------|------|
| New / Open / Save / Save As | `Ctrl+N` `Ctrl+O` `Ctrl+S` `Ctrl+Shift+S` |
| Import image | `Ctrl+I` |
| Fit Canvas | `Ctrl+0` or `Ctrl+F` |
| Fullscreen | `F11` |
| Clear selection | `Esc` or `Ctrl+D` |
| Add / delete layer | `Ctrl+Shift+N` / `Ctrl+Shift+Delete` |
| Add / duplicate / delete frame | `Ctrl+Alt+N` / `Ctrl+Alt+D` / `Ctrl+Alt+Delete` |
| Play / pause animation | `Space` or `F5` |
| Stop animation | `F6` |
| Brush size | `[` `]` |
| Tools | `P/L/U/B/G/E/R/C/V/Q` or `1`–`3`, `5`–`7`, `0` |
| Pan | Middle mouse drag |


## File format

`.inkobold` is a ZIP containing `document.json` and `layers/NNN.png` (RGBA).

## Author

[KTNplayground](https://github.com/Teratoy)
