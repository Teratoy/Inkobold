"""Persistent app settings (XDG config)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from inkobold.core.history import DEFAULT_HISTORY_STEPS
from inkobold.core.image_meta import (
    COLOR_DEPTH_CHOICES,
    COLOR_DEPTH_RGBA32,
    DEFAULT_DPI,
)

# DaemonDomain default: white accent on black glass chrome
DEFAULT_THEME_RGB: tuple[int, int, int] = (255, 255, 255)

@dataclass
class AppSettings:
    theme_rgb: tuple[int, int, int] = DEFAULT_THEME_RGB
    use_custom_theme: bool = False
    checker_light: bool = False
    history_steps: int = DEFAULT_HISTORY_STEPS
    default_dpi: int = DEFAULT_DPI
    default_color_depth: int = COLOR_DEPTH_RGBA32
    # When True, one shared color follows you across tools; when False, each tool keeps its own.
    color_follows_tools: bool = False
    # When True, show a Reload button so the process can be restarted without quitting first.
    debug_mode: bool = False
    # Tool ids shown in the Tools column; empty means all known tools.
    visible_tools: list[str] = field(default_factory=list)
    # action -> accel list; only stores differences from defaults when saved
    shortcut_overrides: dict[str, list[str]] = field(default_factory=dict)
    # most-recent paint colors first (RGBA 0–255)
    recent_colors: list[tuple[int, int, int, int]] = field(default_factory=list)
    # Movable sun light for 3D-illusion tools / relief effects
    sun_enabled: bool = False
    sun_x_norm: float = 0.18
    sun_y_norm: float = 0.18
    sun_elevation: float = 0.70

    def clamp(self) -> None:
        r, g, b = self.theme_rgb
        self.theme_rgb = (
            max(0, min(255, int(r))),
            max(0, min(255, int(g))),
            max(0, min(255, int(b))),
        )
        self.history_steps = max(1, min(2000, int(self.history_steps)))
        self.default_dpi = max(1, min(1200, int(self.default_dpi)))
        if self.default_color_depth not in {d for d, _ in COLOR_DEPTH_CHOICES}:
            self.default_color_depth = COLOR_DEPTH_RGBA32
        self.color_follows_tools = bool(self.color_follows_tools)
        self.debug_mode = bool(self.debug_mode)
        cleaned: dict[str, list[str]] = {}
        for key, accels in self.shortcut_overrides.items():
            if not isinstance(key, str) or not isinstance(accels, list):
                continue
            cleaned[key] = [str(a) for a in accels if isinstance(a, str)]
        self.shortcut_overrides = cleaned
        recent: list[tuple[int, int, int, int]] = []
        for item in self.recent_colors:
            if not isinstance(item, (list, tuple)) or len(item) not in (3, 4):
                continue
            try:
                r, g, b = int(item[0]), int(item[1]), int(item[2])
                a = int(item[3]) if len(item) == 4 else 255
            except (TypeError, ValueError):
                continue
            recent.append(
                (
                    max(0, min(255, r)),
                    max(0, min(255, g)),
                    max(0, min(255, b)),
                    max(0, min(255, a)),
                )
            )
            if len(recent) >= 8:
                break
        self.recent_colors = recent
        if isinstance(self.visible_tools, list):
            self.visible_tools = [str(t) for t in self.visible_tools if isinstance(t, str)]
        else:
            self.visible_tools = []
        self.sun_enabled = bool(self.sun_enabled)
        self.sun_x_norm = max(0.0, min(1.0, float(self.sun_x_norm)))
        self.sun_y_norm = max(0.0, min(1.0, float(self.sun_y_norm)))
        self.sun_elevation = max(0.15, min(1.5, float(self.sun_elevation)))


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        root = Path(base) / "inkobold"
    else:
        root = Path.home() / ".config" / "inkobold"
    return root / "settings.json"


def load_settings() -> AppSettings:
    path = config_path()
    settings = AppSettings()
    if not path.is_file():
        return settings
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return settings
    rgb = data.get("theme_rgb")
    if isinstance(rgb, (list, tuple)) and len(rgb) == 3:
        try:
            settings.theme_rgb = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
        except (TypeError, ValueError):
            pass
    settings.use_custom_theme = bool(data.get("use_custom_theme", False))
    settings.checker_light = bool(data.get("checker_light", False))
    try:
        settings.history_steps = int(data.get("history_steps", DEFAULT_HISTORY_STEPS))
    except (TypeError, ValueError):
        settings.history_steps = DEFAULT_HISTORY_STEPS
    try:
        settings.default_dpi = int(data.get("default_dpi", DEFAULT_DPI))
    except (TypeError, ValueError):
        settings.default_dpi = DEFAULT_DPI
    try:
        settings.default_color_depth = int(data.get("default_color_depth", COLOR_DEPTH_RGBA32))
    except (TypeError, ValueError):
        settings.default_color_depth = COLOR_DEPTH_RGBA32
    raw_sc = data.get("shortcut_overrides")
    if isinstance(raw_sc, dict):
        overrides: dict[str, list[str]] = {}
        for k, v in raw_sc.items():
            if isinstance(k, str) and isinstance(v, list):
                overrides[k] = [str(a) for a in v if isinstance(a, str)]
        settings.shortcut_overrides = overrides
    raw_recent = data.get("recent_colors")
    if isinstance(raw_recent, list):
        settings.recent_colors = list(raw_recent)  # clamp() validates/normalizes
    if "color_follows_tools" in data:
        settings.color_follows_tools = bool(data.get("color_follows_tools"))
    if "debug_mode" in data:
        settings.debug_mode = bool(data.get("debug_mode"))
    raw_visible = data.get("visible_tools")
    if isinstance(raw_visible, list):
        settings.visible_tools = [str(t) for t in raw_visible if isinstance(t, str)]
    if "sun_enabled" in data:
        settings.sun_enabled = bool(data.get("sun_enabled"))
    try:
        settings.sun_x_norm = float(data.get("sun_x_norm", 0.18))
    except (TypeError, ValueError):
        settings.sun_x_norm = 0.18
    try:
        settings.sun_y_norm = float(data.get("sun_y_norm", 0.18))
    except (TypeError, ValueError):
        settings.sun_y_norm = 0.18
    try:
        settings.sun_elevation = float(data.get("sun_elevation", 0.70))
    except (TypeError, ValueError):
        settings.sun_elevation = 0.70
    settings.clamp()
    return settings


def save_settings(settings: AppSettings) -> None:
    settings.clamp()
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "theme_rgb": list(settings.theme_rgb),
            "use_custom_theme": bool(settings.use_custom_theme),
            "checker_light": bool(settings.checker_light),
            "history_steps": int(settings.history_steps),
            "default_dpi": int(settings.default_dpi),
            "default_color_depth": int(settings.default_color_depth),
            "color_follows_tools": bool(settings.color_follows_tools),
            "debug_mode": bool(settings.debug_mode),
            "visible_tools": list(settings.visible_tools),
            "shortcut_overrides": {
                k: list(v) for k, v in settings.shortcut_overrides.items()
            },
            "recent_colors": [list(c) for c in settings.recent_colors],
            "sun_enabled": bool(settings.sun_enabled),
            "sun_x_norm": float(settings.sun_x_norm),
            "sun_y_norm": float(settings.sun_y_norm),
            "sun_elevation": float(settings.sun_elevation),
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
