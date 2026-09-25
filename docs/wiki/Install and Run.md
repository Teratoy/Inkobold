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

Or manually:

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m inkobold
```

Console entry after install: `inkobold` → `inkobold.__main__:main`.

Tablet enrichment (libinput) usually needs membership in the `input` group so `/dev/input` is readable. See [[Input]].

## Windows

Prefer MSYS2 MINGW64. Full steps, packaging, and smoke checklist: [[Windows]] (same content as `docs/WINDOWS.md` in the repo).

Helper: `scripts/run.ps1`.

## Related

- [[Paths and Settings]] — where config and libraries live
- [[Overview]]
