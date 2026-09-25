---
tags: [inkobold, wiki, effects]
---

# Stylize

| Effect | Notes |
|--------|-------|
| Pixelate | Block average |
| Drop Shadow | From alpha; distance / direction / blur / spread / color |
| Dither | Floyd–Steinberg, Ordered, Threshold; levels |
| Posterize | RGB levels |
| **Flora** | Local HDR contrast, grit, neon, bleach, Orton bloom; GPU→CPU fallback; max long edge **1280** (`_FLORA_MAX_SIDE`) |

Flora lives in `effects.flora` + `gpu/flora.py`. See [[Changelog Notes]].

Parent: [[Effects]]
