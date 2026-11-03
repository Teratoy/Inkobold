---
tags: [inkobold, wiki]
---

# Libraries

Root: `{data_dir}/libraries/` — see [[Paths and Settings]].

| Category | Suffixes | Used by |
|----------|----------|---------|
| brushes | `.png` `.jpg` `.jpeg` | [[Brush\|Brush]] custom tip |
| patterns | same | [[Fill\|Fill]] pattern mode · [[Lay\|Lay]] |
| fonts | `.ttf` `.otf` `.ttc` `.otc` | [[Type\|Type]] library source |
| palettes | `.gpl` (GIMP) | Palette panel and color picker — see [[Tools#Colors]] |

The left notebook **Libraries** tab opens these folders in the system file manager.

A new `palettes` folder is seeded once with `Default.gpl` (gray ramp + twelve hues, light and dark).
The palette panel's **⋯ → Import…** converts `.hex` / `.txt` (one `RRGGBB` per line) and palette
images (distinct opaque colors in reading order, at most 256) into `.gpl` files here.

System fonts: prefer `fc-list`; on Windows fall back to `%WINDIR%\Fonts` and the user fonts folder ([[Windows]]).
