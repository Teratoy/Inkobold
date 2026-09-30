---
tags: [inkobold, wiki]
---

# File Format

`.inkobold` is a ZIP. Current schema **version: 4**.

## Layout

```
document.json
frames/FFF/layers/LLL.png   # or .npy if 16 bpc
layers/LLL.png              # legacy mirror of current frame
```

Save aliases also accept `.scribbler` and `.zip`.

## Important `document.json` fields

```json
{
  "version": 4,
  "width": 0,
  "height": 0,
  "dpi": 72,
  "color_depth": 32,
  "fps": 12.0,
  "onion_skin": true,
  "onion_opacity": 0.35,
  "current_frame_index": 0,
  "active_layer_index": 0,
  "frames": [
    {
      "name": "",
      "active_layer_index": 0,
      "layers": [
        {
          "id": "",
          "name": "",
          "visible": true,
          "opacity": 1.0,
          "offset_x": 0,
          "offset_y": 0,
          "file": "frames/000/layers/000.png",
          "dtype": "uint8"
        }
      ]
    }
  ],
  "layers": [],
  "workspace": {
    "tool_id": "pen",
    "tools": {},
    "mirror": { "enabled": false, "orientation": "horizontal", "axes": 1 },
    "grid": { "enabled": false, "rows": 8, "columns": 8 },
    "tile_wrap": false,
    "view": { "zoom": 1.0, "pan_x": 40.0, "pan_y": 40.0 }
  }
}
```

`layers` duplicates the current frame for older readers.

`workspace` (v4) stores document-scoped tool options and view UI. Older files without it open normally; app-wide prefs (theme, shortcuts, sun, …) stay in [[Paths and Settings]].

## Also opens

`.png` `.jpg` `.jpeg` `.webp` `.bmp` `.gif` (multi-frame GIF → [[Animation]]).

Related: [[Layers]] · [[Architecture]] · [[Tools]]
