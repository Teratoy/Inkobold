# Inkobold

GPU-accelerated layered drawing for **Linux** and **Windows** (GTK4 + OpenGL). On Linux it can enrich tablets via **libinput** / **libwacom**; on Windows it uses **GDK** pointer/tablet axes.

Version **0.1.0** · Python **≥3.11** · App id `trinkets.inkobold`

## Features

- **File** — Multi-document tabs; New File (pixel size), Open, Save (`.inkobold` multilayer ZIP), Export, Export Separated Layers, Import Image as Layer
- **Layers** — RGBA layers (8 or 16 bpc), visibility, opacity, offsets, GPU composite; add / duplicate / rename / delete / merge down / merge all
- **Animation** — Frames with FPS playback; onion-skin of the previous frame; export GIF or PNG sequence
- **Tools** — Pen, Line, Rectangle, Curve (freehand / arc / circle), Brush (round / custom tip / bubbles), Weld Brush, Fill, Gradient, 3D Pen, 3D Fill, Eraser, Smear, Liquify, Replace (replace/erase × brush/fill/all), Transform (move / scale / rotate), Lasso, Type
- **Effects** — Blur & Sharpen, Stylize (Pixelate, Drop Shadow, Dither, Posterize, **Flora**), Color, Distort, Edge & Depth, Materials (Metal Relief, Milk); apply to active layer or all layers
- **Libraries** — User brushes, patterns, and fonts under the platform data dir (see Paths below)
- **Input** — GDK drawing path; on Linux, optional libinput poll + libwacom tablet identification
- **View** — Pan (middle mouse), scroll zoom, Fit Canvas, grid, Show Sun (draggable light for 3D / Metal / Milk), Tile Preview, Wrap Moves, checker background, Fullscreen / Borderless / Windowed
- **Edit** — Undo / Redo; stroke Mirror (1–16 axes, H / V / diagonal); Flip / Rotate layer; Crop Canvas (live size; optional selection bounds)
- **Settings** — Theme, Memory (undo steps), Image (crop / DPI / color depth), Tools (color follows tools, visible tools), Shortcuts (rebind), Debug Mode

## Documentation

- **[docs/WINDOWS.md](docs/WINDOWS.md)** — Windows setup, packaging, known gaps
- **[docs/wiki/](docs/wiki/)** — Full wiki (Obsidian-friendly `[[wikilinks]]`; also mirrored in the Stele vault)

## Run (Linux)

```bash
./scripts/run.sh
```

Or:

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m inkobold
```

Requires system **PyGObject** + **GTK4** (distro packages; use a venv with `--system-site-packages`).

## Run / package (Windows)

See **[docs/WINDOWS.md](docs/WINDOWS.md)** (MSYS2 MINGW64 recommended) and `scripts/run.ps1`.

## Paths

| | Linux | Windows |
|--|-------|---------|
| Config / settings | `~/.config/inkobold/settings.json` | `%APPDATA%\Inkobold\settings.json` |
| Libraries | `~/.local/share/inkobold/libraries/` | `%LOCALAPPDATA%\Inkobold\libraries\` |

Overrides: `INKOBOLD_CONFIG_HOME`, `INKOBOLD_DATA_HOME`, `INKOBOLD_ICON`.

## Shortcuts

| Action | Keys |
|--------|------|
| New / Open / Save / Save As | `Ctrl+N` `Ctrl+O` `Ctrl+S` `Ctrl+Shift+S` |
| Import image / Export | `Ctrl+I` / `Ctrl+E` |
| Undo / Redo | `Ctrl+Z` `Ctrl+Shift+Z` or `Ctrl+Y` |
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
| Tools | Pen `P` · Line `L` · Rect `O` · Curve `U` · Brush `B` · Weld `W` · Fill `G` · Gradient `A` · 3D Pen `D` · 3D Fill `F` · Eraser `E` · Smear `M` · Liquify `Y` · Replace `C`/`R` · Transform `V` · Lasso `Q` · Type `T` |
| Pan | Middle mouse drag |

Shortcuts are rebindable under **Settings → Shortcuts**.

## File format

`.inkobold` is a ZIP (`document.json` **version 3**) with per-frame layers:

```
document.json
frames/FFF/layers/LLL.png   # or .npy for 16 bpc
layers/LLL.png              # legacy mirror of the current frame
```

Also opens PNG / JPEG / WebP / BMP / GIF (multi-frame GIF → animation).

## Author

[KTNplayground](https://github.com/Teratoy)
