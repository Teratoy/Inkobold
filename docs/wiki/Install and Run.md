---
tags: [inkobold, wiki]
---

# Install and Run

## Dependencies

**Python packages** (`requirements.txt` / `pyproject.toml`):

- `numpy>=2.0`
- `PyOpenGL>=3.1.7`
- `Pillow>=10.0`

**System** (not pip): PyGObject + GTK4 (+ working OpenGL). Use a venv with `--system-site-packages` so GTK imports resolve.

## Linux

```bash
./scripts/run.sh
```

Open a file from the shell (skips the startup Open/New dialog):

```bash
./scripts/run.sh path/to/drawing.inkobold
# or: python -m inkobold path/to/image.png
```

Or manually:

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m inkobold
```

Console entry after install: `inkobold` → `inkobold.__main__:main`.

### Desktop “Open with”

`scripts/inkobold.desktop` passes file paths (`%F`). Optional MIME for `.inkobold` / `.scribbler`:

```bash
xdg-mime install --novendor scripts/inkobold.xml
# install/update the .desktop into ~/.local/share/applications/ as usual
update-desktop-database ~/.local/share/applications
```

Tablet enrichment (libinput) usually needs membership in the `input` group so `/dev/input` is readable. This is optional and has a security cost (any program you run can then read your keyboard); see [[Input]].

## Windows

Prefer MSYS2 MINGW64. Full steps, packaging, and smoke checklist: [[Windows]] (same content as `docs/WINDOWS.md` in the repo).

Helper: `scripts/run.ps1`.

## Related

- [[Paths and Settings]] — where config and libraries live
- [[Overview]]
