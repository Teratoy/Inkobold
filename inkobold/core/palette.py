"""Color palettes: GIMP ``.gpl`` files in Libraries → Palettes.

Swatches are opaque RGB (``.gpl`` has no alpha); picking one keeps the tool's
current alpha. ``.hex`` (one ``RRGGBB`` per line, as exported by Lospec) and
images (each distinct opaque color, in reading order) can be imported and are
saved back as ``.gpl``.
"""

from __future__ import annotations

import colorsys
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

PALETTE_SUFFIX = ".gpl"
IMPORT_SUFFIXES = {".gpl", ".hex", ".txt", ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
# Image imports with more distinct colors than this are refused (not a palette image)
MAX_IMAGE_COLORS = 256
# A layer with more distinct colors than this is reduced (median cut) to this many
MAX_LAYER_COLORS = 64
DEFAULT_PALETTE_NAME = "Default"

Rgb = tuple[int, int, int]

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})(?:[0-9a-fA-F]{2})?$")
_UNSAFE_NAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class PaletteError(ValueError):
    """A palette file could not be read."""


@dataclass
class Swatch:
    rgb: Rgb
    name: str = ""

    @property
    def hex(self) -> str:
        r, g, b = self.rgb
        return f"#{r:02x}{g:02x}{b:02x}"


@dataclass
class Palette:
    name: str
    swatches: list[Swatch] = field(default_factory=list)
    # GIMP's preferred grid width; 0 = let the viewer decide
    columns: int = 0

    def add(self, rgb: Rgb, name: str = "") -> bool:
        """Append *rgb* unless it is already in the palette. Returns True if added."""
        rgb = _clamp_rgb(rgb)
        if any(s.rgb == rgb for s in self.swatches):
            return False
        self.swatches.append(Swatch(rgb, name))
        return True

    def move(self, src: int, dst: int) -> None:
        """Move the swatch at *src* so it ends up at index *dst*."""
        if not (0 <= src < len(self.swatches)):
            return
        dst = max(0, min(len(self.swatches) - 1, dst))
        swatch = self.swatches.pop(src)
        self.swatches.insert(dst, swatch)


def _clamp_rgb(rgb) -> Rgb:
    return (
        max(0, min(255, int(rgb[0]))),
        max(0, min(255, int(rgb[1]))),
        max(0, min(255, int(rgb[2]))),
    )


def parse_gpl(text: str, *, fallback_name: str = "Palette") -> Palette:
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "GIMP Palette":
        raise PaletteError("not a GIMP palette (missing 'GIMP Palette' header)")
    palette = Palette(fallback_name)
    for raw in lines[1:]:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() in ("name", "columns"):
            if key.strip().lower() == "name":
                palette.name = value.strip() or fallback_name
            else:
                try:
                    palette.columns = max(0, min(256, int(value.strip())))
                except ValueError:
                    pass
            continue
        parts = line.split(None, 3)
        if len(parts) < 3:
            continue
        try:
            rgb = _clamp_rgb((int(parts[0]), int(parts[1]), int(parts[2])))
        except ValueError:
            continue
        palette.swatches.append(Swatch(rgb, parts[3].strip() if len(parts) > 3 else ""))
    return palette


def format_gpl(palette: Palette) -> str:
    out = ["GIMP Palette", f"Name: {palette.name}"]
    if palette.columns:
        out.append(f"Columns: {palette.columns}")
    out.append("#")
    for swatch in palette.swatches:
        r, g, b = swatch.rgb
        line = f"{r:3d} {g:3d} {b:3d}"
        if swatch.name:
            line += f"\t{swatch.name}"
        out.append(line)
    return "\n".join(out) + "\n"


def parse_hex(text: str, *, name: str = "Palette") -> Palette:
    palette = Palette(name)
    for raw in text.lstrip("﻿").splitlines():
        m = _HEX_RE.match(raw.strip())
        if m is None:
            continue
        v = m.group(1)
        palette.swatches.append(Swatch((int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))))
    if not palette.swatches:
        raise PaletteError("no RRGGBB colors found")
    return palette


def palette_from_image(path: Path, *, name: str | None = None) -> Palette:
    """Distinct opaque colors of an image, in reading order (left→right, top→bottom)."""
    try:
        with Image.open(path) as im:
            rgba = np.asarray(im.convert("RGBA"))
    except (OSError, ValueError) as exc:
        raise PaletteError(f"cannot read image: {exc}") from exc
    pixels = _opaque_rgb(rgba, "image")
    uniq, first = _distinct(pixels)
    if len(uniq) > MAX_IMAGE_COLORS:
        raise PaletteError(
            f"image has {len(uniq)} colors; a palette image may have at most {MAX_IMAGE_COLORS}"
        )
    return _palette_from_packed(name or path.stem, uniq[np.argsort(first)])


def palette_from_pixels(rgba: np.ndarray, *, name: str, max_colors: int = MAX_LAYER_COLORS) -> Palette:
    """Palette of a uint8 RGBA buffer (e.g. a layer).

    Up to ``max_colors`` distinct opaque colors are kept as-is, in reading
    order; a busier buffer (anti-aliasing, soft brushes) is reduced by median
    cut to ``max_colors`` swatches, most used first.
    """
    pixels = _opaque_rgb(rgba, "layer")
    uniq, first = _distinct(pixels)
    if len(uniq) <= max_colors:
        return _palette_from_packed(name, uniq[np.argsort(first)])
    strip = Image.fromarray(np.ascontiguousarray(pixels.reshape(1, -1, 3)), "RGB")
    quant = strip.quantize(colors=max_colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    flat = quant.getpalette() or []
    palette = Palette(name)
    for _count, idx in sorted(quant.getcolors(max_colors) or [], key=lambda c: -c[0]):
        palette.add(tuple(flat[idx * 3: idx * 3 + 3]))
    return palette


def _opaque_rgb(rgba: np.ndarray, what: str) -> np.ndarray:
    pixels = rgba.reshape(-1, 4)
    pixels = pixels[pixels[:, 3] > 0][:, :3]
    if pixels.size == 0:
        raise PaletteError(f"{what} has no opaque pixels")
    return pixels


def _distinct(pixels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Packed 0xRRGGBB distinct colors and the index of each one's first use."""
    packed = (pixels[:, 0].astype(np.uint32) << 16) | (pixels[:, 1].astype(np.uint32) << 8) | pixels[:, 2]
    return np.unique(packed, return_index=True)


def _palette_from_packed(name: str, packed: np.ndarray) -> Palette:
    palette = Palette(name)
    for v in packed:
        v = int(v)
        palette.swatches.append(Swatch(((v >> 16) & 255, (v >> 8) & 255, v & 255)))
    return palette


def load_palette(path: Path) -> Palette:
    """Read a ``.gpl``, ``.hex`` / ``.txt`` or image palette."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in IMPORT_SUFFIXES:
        raise PaletteError(f"unsupported palette type '{suffix}'")
    if suffix in (".gpl", ".hex", ".txt"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise PaletteError(str(exc)) from exc
        if suffix == ".gpl":
            return parse_gpl(text, fallback_name=path.stem)
        return parse_hex(text, name=path.stem)
    return palette_from_image(path)


def save_palette(palette: Palette, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(format_gpl(palette), encoding="utf-8")
    tmp.replace(path)


def safe_file_stem(name: str) -> str:
    stem = _UNSAFE_NAME_RE.sub("_", name).strip(" .")
    return stem or "Palette"


def unique_palette_path(folder: Path, name: str, *, ignore: Path | None = None) -> Path:
    """``<folder>/<name>.gpl``, numbered (``name 2.gpl`` …) if that file exists."""
    stem = safe_file_stem(name)
    candidate = folder / f"{stem}{PALETTE_SUFFIX}"
    n = 2
    while candidate.exists() and candidate != ignore:
        candidate = folder / f"{stem} {n}{PALETTE_SUFFIX}"
        n += 1
    return candidate


def import_palette(src: Path, folder: Path) -> Path:
    """Copy any supported palette into *folder* as ``.gpl``; returns the new file."""
    palette = load_palette(src)
    dest = unique_palette_path(folder, palette.name or Path(src).stem)
    save_palette(palette, dest)
    return dest


def default_palette() -> Palette:
    """Starter palette: a gray ramp plus twelve hues, light and dark."""
    palette = Palette(DEFAULT_PALETTE_NAME, columns=12)
    for i in range(12):
        v = round(255 * i / 11)
        palette.swatches.append(Swatch((v, v, v), "Black" if i == 0 else "White" if i == 11 else f"Gray {i}"))
    hues = (
        "Red", "Orange", "Yellow", "Lime", "Green", "Mint",
        "Cyan", "Azure", "Blue", "Violet", "Magenta", "Rose",
    )
    for label, (sat, val) in (("", (0.80, 0.95)), ("Dark ", (0.85, 0.55))):
        for i, hue in enumerate(hues):
            r, g, b = colorsys.hsv_to_rgb(i / len(hues), sat, val)
            palette.swatches.append(Swatch((round(r * 255), round(g * 255), round(b * 255)), label + hue))
    return palette
