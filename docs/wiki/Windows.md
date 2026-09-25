---
tags: [inkobold, wiki, windows]
aliases: [WINDOWS]
---

# Windows

Inkobold is a **GTK4 + PyGObject + OpenGL** app. On Windows it draws via **GDK** (no libinput/libwacom). Pressure/tilt depend on what your GTK build exposes for tablets.

## Dev run (MSYS2 — recommended)

1. Install [MSYS2](https://www.msys2.org/), then in an **MINGW64** shell:

```bash
pacman -S --needed \
  mingw-w64-x86_64-python \
  mingw-w64-x86_64-python-pip \
  mingw-w64-x86_64-python-gobject \
  mingw-w64-x86_64-gtk4 \
  mingw-w64-x86_64-python-numpy \
  mingw-w64-x86_64-python-pillow \
  mingw-w64-x86_64-python-opengl
```

2. From the repo (still MINGW64):

```bash
cd /c/path/to/Inkobold
python -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m inkobold
```

Or from PowerShell after MSYS2 Python is on `PATH`, use `scripts/run.ps1`.

## Config / libraries on Windows

| Data | Location |
|------|----------|
| Settings | `%APPDATA%\Inkobold\settings.json` |
| Libraries | `%LOCALAPPDATA%\Inkobold\libraries\{brushes,patterns,fonts}` |

Overrides: `INKOBOLD_CONFIG_HOME`, `INKOBOLD_DATA_HOME`. See [[Paths and Settings]].

## Packaging

GTK apps on Windows ship best as a **directory tree** (all DLLs) plus an optional Inno Setup / NSIS wrapper—not a tiny one-file `.exe`.

### PyInstaller (MINGW64)

```bash
pacman -S mingw-w64-x86_64-python-pyinstaller   # or: pip install pyinstaller
pyinstaller scripts/windows/inkobold.spec
```

Output: `dist/Inkobold/` with `Inkobold.exe`. Copy missing GTK/GLib DLLs from `C:\msys64\mingw64\bin` if needed, and ship `share/glib-2.0`, `share/icons`, `lib/gdk-pixbuf-2.0` as required.

### Briefcase / gvsbuild

Use [gvsbuild](https://github.com/wingtk/gvsbuild) for MSVC-built GTK instead of MSYS2.

## Known Windows gaps

- **libinput / libwacom**: Linux only; status line reports GDK-only input
- **Tablet**: GDK axes; quality varies by driver / GTK build
- **GLArea**: needs working WGL/OpenGL
- **Debug Reload**: spawns a new process then quits (no `execv`)
- **Fonts**: `fc-list` when present; else `%WINDIR%\Fonts` + user fonts folder

## Smoke checklist

- [ ] Window opens (borderless / windowed / fullscreen)
- [ ] New document, draw with mouse, undo/redo
- [ ] Open / save `.inkobold`, export PNG
- [ ] Layers + one effect
- [ ] Libraries folder opens in Explorer
- [ ] Type tool lists some system fonts
- [ ] Optional: stylus pressure on a Wacom / Windows Ink device

## Related

- [[Install and Run]] · [[Input]] · [[Libraries]]
