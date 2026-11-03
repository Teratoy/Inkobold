"""Platform-aware config / data directories (XDG on Unix, APPDATA on Windows)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from inkobold.core.flavor import current


def is_windows() -> bool:
    return sys.platform == "win32"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def config_dir() -> Path:
    """Directory for settings.json (created by callers as needed); one per flavor."""
    flavor = current()
    override = os.environ.get(f"{flavor.env_prefix}_CONFIG_HOME")
    if override:
        return Path(override)
    if is_windows():
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / flavor.win_config_name
        return Path.home() / "AppData" / "Roaming" / flavor.win_config_name
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / flavor.config_name
    return Path.home() / ".config" / flavor.config_name


def state_dir() -> Path:
    """Directory for machine-local state such as autosaves; one per flavor.

    Not the temp dir (Blender's choice): /tmp is often wiped on reboot, which
    is exactly when a crash autosave is needed.
    """
    flavor = current()
    override = os.environ.get(f"{flavor.env_prefix}_STATE_HOME")
    if override:
        return Path(override)
    if is_windows():
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / flavor.win_config_name
        return Path.home() / "AppData" / "Local" / flavor.win_config_name
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / flavor.config_name
    return Path.home() / ".local" / "state" / flavor.config_name


def data_dir() -> Path:
    """Directory for libraries and other user data (shared by all flavors)."""
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
