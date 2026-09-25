---
tags: [inkobold, wiki]
---

# Effects

Menus in the main window. Every effect dialog offers **Apply to:** Active layer | All layers. When a selection exists, blending is limited to the mask.

| Category | Notes |
|----------|--------|
| [[Effects/Blur and Sharpen\|Blur & Sharpen]] | Gaussian, Sharpen, Kuwahara |
| [[Effects/Stylize\|Stylize]] | Pixelate, Drop Shadow, Dither, Posterize, **Flora** |
| [[Effects/Color\|Color]] | Basics, Grayscale, Invert, Solarize, Curves, Threshold |
| [[Effects/Distort\|Distort]] | Full-image Liquify |
| [[Effects/Edge and Depth\|Edge & Depth]] | Edge Detect, Emboss, Normal Map |
| [[Effects/Materials\|Materials]] | Metal Relief, Milk (sun-aware) |

Implementation: mostly CPU NumPy in `inkobold/core/effects.py`. Flora also has a GPU path in `inkobold/gpu/flora.py` (downscales long edge to ≤1280).
