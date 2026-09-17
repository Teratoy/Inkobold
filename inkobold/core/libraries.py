"""User library folders (brushes, patterns, fonts) under XDG data home."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

PATTERN_SUFFIXES = {".png", ".jpg", ".jpeg"}
FONT_SUFFIXES = {".ttf", ".otf", ".ttc", ".otc"}

CATEGORIES: tuple[tuple[str, str], ...] = (
    ("brushes", "Brushes"),
    ("patterns", "Patterns"),
    ("fonts", "Fonts"),
)

_system_fonts_cache: list["SystemFont"] | None = None


@dataclass(frozen=True)
class SystemFont:
    """A system font face resolved to a file path."""

    family: str
    path: Path
    style: str = "Regular"

    @property
    def label(self) -> str:
        style = (self.style or "Regular").strip()
        if not style or style.lower() in ("regular", "normal", "book"):
            return self.family
        return f"{self.family} ({style})"


def libraries_root() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        root = Path(base) / "inkobold" / "libraries"
    else:
        root = Path.home() / ".local" / "share" / "inkobold" / "libraries"
    return root


def category_dir(category: str) -> Path:
    return libraries_root() / category


def ensure_libraries() -> Path:
    """Create library root and category folders; return root."""
    root = libraries_root()
    for cat, _label in CATEGORIES:
        (root / cat).mkdir(parents=True, exist_ok=True)
    return root


def list_patterns() -> list[Path]:
    """Return pattern image paths sorted by name (case-insensitive)."""
    folder = category_dir("patterns")
    if not folder.is_dir():
        return []
    files = [
        p
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in PATTERN_SUFFIXES
    ]
    files.sort(key=lambda p: p.name.lower())
    return files


def list_fonts() -> list[Path]:
    """Return library font paths (including subfolders), sorted by relative path."""
    folder = category_dir("fonts")
    if not folder.is_dir():
        return []
    files = [
        p
        for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in FONT_SUFFIXES
    ]
    files.sort(key=lambda p: str(p.relative_to(folder)).lower())
    return files


def list_system_fonts(*, refresh: bool = False) -> list[SystemFont]:
    """Return system fonts via fontconfig (`fc-list`), cached until *refresh*."""
    global _system_fonts_cache
    if _system_fonts_cache is not None and not refresh:
        return _system_fonts_cache

    fonts: list[SystemFont] = []
    seen: set[tuple[str, str]] = set()
    try:
        proc = subprocess.run(
            ["fc-list", "-f", "%{file}\t%{family[0]}\t%{style[0]}\n"],
            capture_output=True,
            text=True,
            check=False,
            timeout=8.0,
        )
        raw = proc.stdout if proc.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        raw = ""

    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        path_s, family = parts[0].strip(), parts[1].strip()
        style = parts[2].strip() if len(parts) > 2 else "Regular"
        if not path_s or not family:
            continue
        path = Path(path_s)
        if path.suffix.lower() not in FONT_SUFFIXES or not path.is_file():
            continue
        key = (family.lower(), style.lower())
        if key in seen:
            continue
        seen.add(key)
        fonts.append(SystemFont(family=family, path=path, style=style or "Regular"))

    def _style_rank(style: str) -> int:
        s = style.lower()
        if s in ("regular", "normal", "book", "roman"):
            return 0
        if "bold" in s and ("italic" in s or "oblique" in s):
            return 3
        if "bold" in s:
            return 1
        if "italic" in s or "oblique" in s:
            return 2
        return 4

    fonts.sort(key=lambda f: (f.family.lower(), _style_rank(f.style), f.style.lower()))

    # One face per family for the picker (prefer Regular).
    by_family: dict[str, SystemFont] = {}
    for font in fonts:
        key = font.family.lower()
        prev = by_family.get(key)
        if prev is None or _style_rank(font.style) < _style_rank(prev.style):
            by_family[key] = font
    collapsed = sorted(by_family.values(), key=lambda f: f.family.lower())
    _system_fonts_cache = collapsed
    return collapsed


def open_in_file_manager(path: Path) -> bool:
    """Open *path* in the desktop file manager. Returns False on failure."""
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    uri = path.as_uri()
    try:
        from gi.repository import Gio

        return bool(Gio.AppInfo.launch_default_for_uri(uri, None))
    except Exception:
        pass
    try:
        subprocess.Popen(["xdg-open", str(path)], start_new_session=True)
        return True
    except Exception:
        return False
