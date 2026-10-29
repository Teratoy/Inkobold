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
| `INKOBOLD_STATE_HOME` | State directory (autosaves) |
| `INKOBOLD_ICON` | Window icon path (set by launch scripts) |

## Default locations

| | Linux | Windows |
|--|-------|---------|
| Config | `$XDG_CONFIG_HOME/inkobold` or `~/.config/inkobold` | `%APPDATA%\Inkobold` |
| Data | `$XDG_DATA_HOME/inkobold` or `~/.local/share/inkobold` | `%LOCALAPPDATA%\Inkobold` |
| State | `$XDG_STATE_HOME/inkobold` or `~/.local/state/inkobold` | `%LOCALAPPDATA%\Inkobold` |
| Settings file | `…/settings.json` | same under config dir |
| Libraries | `{data}/libraries/{brushes,patterns,fonts}` | same |
| Autosaves | `{state}/autosave/<session>/` | same |

## Settings UI

| Menu | What it controls |
|------|------------------|
| Theme | Custom RGB theme tint |
| Memory | Undo steps (`history_steps`, default **124**, clamp 1–2000); Autosave… (on/off, interval) |
| Image | Crop dialog, default DPI, color depth |
| Tools | Color follows tools; which tools are visible |
| Shortcuts | View defaults and rebind keys |
| Debug Mode | Extra diagnostics / reload |

Continuous numeric tool/effect controls (size, opacity, amounts, sun elevation, fps, …) accept floats to **two decimal places** (e.g. `0.01`; locale comma `0,01` where GTK uses it). Tool **Size** ranges **0.1–256**. Discrete counts (pixel sizes, levels, axes, DPI, undo steps) stay integers.

## Persisted keys (`AppSettings`)

`theme_rgb` (default white), `use_custom_theme`, `checker_light`, `history_steps`, `autosave` (default on), `autosave_minutes` (default **2**, clamp 1–60), `default_dpi`, `default_color_depth`, `color_follows_tools`, `debug_mode`, `visible_tools`, `shortcut_overrides`, `recent_colors` (max 8), `sun_active`, `sun_visible`, `sun_x_norm`, `sun_y_norm`, `sun_elevation`, `active_palette` (file name in Libraries → Palettes).

Tool options, mirror/grid/wrap, and canvas view are saved per document in the `.inkobold` `workspace` blob — see [[File Format]].

Sun defaults: `active` true, glyph hidden (`sun_visible` false), `x_norm`/`y_norm` = **0.18**, `elevation` = **0.70**. See [[View]].

## Autosave

Modelled on Blender's Auto Save. Every `autosave_minutes`, each open document with unsaved changes is written to this session's folder under `{state}/autosave/`. Your own file is never touched, and a document that hasn't changed since its last autosave isn't written again. While a stroke or effect preview is in progress, autosave waits and tries again 10 s later.

- Saving a document or closing its tab deletes its autosave. A normal quit deletes the whole session folder, including documents you chose **Don't Save** for.
- After a crash or kill the folder stays. **File → Recover Autosave…**, and the startup prompt when there's something to recover, list documents from sessions that are no longer running. **Open** reopens one as an unsaved tab that saves back to its original file (untitled documents ask where to save). **Delete** removes the copy.
- Each running session holds a lock on `session.lock` in its folder, so another running window's autosaves are never offered for recovery.
- The state folder is used instead of the temp dir because `/tmp` is often cleared on reboot.

## Color depths

| Value | Meaning |
|-------|---------|
| 8 | Gray |
| 24 | RGB |
| 32 | RGBA 8 bpc (typical) |
| 64 | RGBA 16 bpc |

## Related

- [[Libraries]] · [[Shortcuts]] · [[File Format]]
