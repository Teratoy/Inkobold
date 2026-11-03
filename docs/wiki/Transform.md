---
tags: [inkobold, wiki, tools]
---

# Transform

**id** `move` · **key** `V`

Move / scale / rotate box. Selection-aware. Does not use stroke mirror.

- Drag inside to move, drag a corner to scale (Shift: uniform), Alt+drag a corner to rotate (Shift: 15° steps).
- Once you scale or rotate (or move a selection), the original pixels **float**: every later drag resamples from them, so shrinking and re-enlarging loses nothing, and content pushed past the canvas edge comes back intact. The box is drawn dashed while a float is live.
- **Enter** or a click outside the box applies the float, as does switching tools or editing the layer any other way. Each drag is its own undo step.
- Downscaling uses Lanczos pre-reduction plus bicubic placement; whole-pixel 1:1 moves copy pixels exactly (full bit depth).

**Import Image as Layer** (`Ctrl+I`) opens the image as such a float on a new layer, at actual size and centered, even when it is larger than the canvas. Scale it to taste, then press Enter.

Parent: [[Tools]]
