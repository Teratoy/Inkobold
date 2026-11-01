---
tags: [inkobold, wiki]
---

# View

Canvas defaults: pan `(40, 40)`, zoom `1.0`. Zoom clamp **0.05–32**. App starts **borderless**.

| Feature | Control | Notes |
|---------|---------|-------|
| Pan | Middle-mouse drag | |
| Zoom | Scroll wheel | |
| Fit Canvas | `Ctrl+0`, `Ctrl+F` | |
| Show Grid | View menu | Visual only; default 8×8 interior guides (0–64), equal parts; no snap |
| Grid… | Settings | rows / columns, **Pixel tile** (Pixel Art tile grid, default 16, 0 = none); **Reset Offset** restores default centering |
| Move Grid | Alt-drag a grid line | Vertical line → shift X; horizontal → Y; crossing → both |
| Show Sun | View + drag glyph | Shows/drags the sun glyph; also turns Use Sun Light on |
| Use Sun Light | View | When off, classic fixed key light (pre-sun) |
| Deactivate Sun | View / Sun… | Turns Use Sun Light off and hides the glyph |
| Sun… | Elevation etc. | Persisted; elevation 0.15–1.5; Apply reactivates |
| Tile Preview | `Ctrl+Shift+T` | 3×3 tiled composite |
| Wrap Moves | `Ctrl+Shift+W` | Toroidal stamps/transforms/3D Fill emboss; follows Tile Preview by default |
| Pixel Art | `Ctrl+Shift+P`, toolbox **Pixel** | Hard-pixel painting; see [Pixel Art](#pixel-art) |
| Checker BG | View / settings | `checker_light` |
| Fullscreen / Borderless / Windowed | `F11` / menu | |
| Clear selection | `Esc`, `Ctrl+D` | |

## Pixel Art

Per-document toggle (saved in the document workspace) for pixel art. While on, the paint primitives that Pen, Brush (round tip), Eraser, Line, Rectangle, Curve and Replace build on change:

- Stamps are hard: alpha is all or nothing, with no soft edge. 1 px is a single pixel, 2 px a 2×2 block, 3 px a plus and wider tips are round.
- **Size** is the stroke width in whole pixels and pen pressure is ignored.
- Strokes step cell by cell along Bresenham lines, so there are no gaps or doubled pixels.
- 1 px freehand strokes are pixel-perfect: the elbow of an L-shaped corner is put back, leaving clean diagonals.
- Eraser and Replace colour matching is all-or-nothing within the threshold.

Custom brush tips, Bubbles, Gradient, Fill and the 3D tools are unchanged.

The view changes too:

- Scroll zoom steps through 1/8×, 1/4×, 1/2×, 1×, 2×, 3×, 4×, 6×, 8×, 12×, 16×, 24×, 32×, 48× and 64×, and the canvas pans in whole screen pixels, so every document pixel is an even square. Turning Pixel Art on settles the current zoom on that ladder; **Fit Canvas** picks the largest step that fits (small sprites can fit at up to 64×).
- From 8× up a one-pixel grid is drawn over the canvas.
- With **Show Grid** on, a blue tile grid is drawn every **Pixel tile** pixels (set in **Grid…**), alongside the row/column guides.
- The cells the current tool would paint are outlined under the pointer: the tip shape for Pen, Brush, Eraser, Line, Rectangle, Curve and Replace, a single pixel for other tools.

The **Inkimp** flavor (`python -m inkobold.imp`, `scripts/run-imp.sh`) starts with Pixel Art on, and files opened without a saved setting open with it on. Its New File sizes are sprite sizes, and its toolbox hides Lay, Weld Brush, Gradient, 3D Pen, 3D Fill, Smear, Liquify and Type until they are picked in **Visible Tools**. It keeps its own settings, like any flavor, and shares libraries and palettes with the others.

## Symmetry

Live stroke symmetry around a movable hub (defaults to canvas center). Toggle **Symmetry** in the toolbox. Applies to tools with `supports_mirror` (not Transform / Lasso).

| Mode | Behavior | Count |
|------|----------|-------|
| **Mirror** | Reflect across equally spaced axes (dihedral / kaleidoscope). Odd branches flip handedness. | **Axes** 1–16 |
| **Radial** | Rotate same-facing copies (cyclic / pinwheel). No flips. | **Copies** 1–16 |
| **Linear Array** | Repeat same-facing copies of the stroke at regular intervals along a free direction. No flips. | **Copies** 1–64 |

**Linear Array** replaces Orientation / Axes with **Copies** (instances, the drawn stroke included), **Spacing (px)** and **Direction (°)** (0° = right, 90° = up). Copies follow the pointer; the hub only anchors the guide. **Alt-drag** the square step handle (one interval from the hub) to set distance and direction together, or the guide line to rotate the heading.

**Orientation** (horizontal / vertical / diagonal) snaps the first-axis / first-ray angle. **Alt-drag** the hub to move the center; **Alt-drag** a guide line to rotate freely. **Reset Guides** restores hub to center and Horizontal. Hub position, angle and array settings persist in the document workspace.

## Sun

Defaults: `x_norm`/`y_norm` = **0.18**, `elevation` = **0.70**, `sun_active` = **true**, glyph hidden. Used by [[3D Pen|3D Pen]], [[3D Fill|3D Fill]], [[Materials|Materials]]. **Deactivate Sun** restores classic Metal/Milk light dirs; pose is kept for later.

Related: [[Paths and Settings]] · [[Architecture]]
