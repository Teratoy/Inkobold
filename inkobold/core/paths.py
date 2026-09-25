"""Platform-aware config / data directories (XDG on Unix, APPDATA on Windows)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_windows() -> bool:
    return sys.platform == "win32"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def config_dir() -> Path:
    """Directory for settings.json (created by callers as needed)."""
    override = os.environ.get("INKOBOLD_CONFIG_HOME")
    if override:
        return Path(override)
    if is_windows():
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "Inkobold"
        return Path.home() / "AppData" / "Roaming" / "Inkobold"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "inkobold"
    return Path.home() / ".config" / "inkobold"


def data_dir() -> Path:
    """Directory for libraries and other user data."""
    override = os.environ.get("INKOBOLD_DATA_HOME")
    if override:
        return Path(override)
    if is_windows():
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "Inkobold"
        return Path.home() / "AppData" / "Local" / "Inkobold"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "inkobold"
    return Path.home() / ".local" / "share" / "inkobold"
