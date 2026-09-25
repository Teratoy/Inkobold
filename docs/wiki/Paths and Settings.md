---
tags: [inkobold, wiki]
---

# Paths and Settings

Resolved by `inkobold/core/paths.py`.

## Environment overrides

| Env | Purpose |
|-----|---------|
| `INKOBOLD_CONFIG_HOME` | Settings directory |
| `INKOBOLD_DATA_HOME` | Libraries / data root |
| `INKOBOLD_ICON` | Window icon path (set by launch scripts) |

## Default locations

| | Linux | Windows |
|--|-------|---------|
| Config | `$XDG_CONFIG_HOME/inkobold` or `~/.config/inkobold` | `%APPDATA%\Inkobold` |
| Data | `$XDG_DATA_HOME/inkobold` or `~/.local/share/inkobold` | `%LOCALAPPDATA%\Inkobold` |
| Settings file | `…/settings.json` | same under config dir |
| Libraries | `{data}/libraries/{brushes,patterns,fonts}` | same |

## Settings UI

| Menu | What it controls |
|------|------------------|
| Theme | Custom RGB theme tint |
| Memory | Undo steps (`history_steps`, default **124**, clamp 1–2000) |
| Image | Crop dialog, default DPI, color depth |
| Tools | Color follows tools; which tools are visible |
| Shortcuts | View defaults and rebind keys |
| Debug Mode | Extra diagnostics / reload |

## Persisted keys (`AppSettings`)

`theme_rgb` (default white), `use_custom_theme`, `checker_light`, `history_steps`, `default_dpi`, `default_color_depth`, `color_follows_tools`, `debug_mode`, `visible_tools`, `shortcut_overrides`, `recent_colors` (max 8), `sun_enabled`, `sun_x_norm`, `sun_y_norm`, `sun_elevation`.

Sun defaults: `x_norm`/`y_norm` = **0.18**, `elevation` = **0.70**. See [[View]].

## Color depths

| Value | Meaning |
|-------|---------|
| 8 | Gray |
| 24 | RGB |
| 32 | RGBA 8 bpc (typical) |
| 64 | RGBA 16 bpc |

## Related

- [[Libraries]] · [[Shortcuts]] · [[File Format]]
