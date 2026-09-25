# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for a Windows folder build (run from MINGW64).

  pyinstaller scripts/windows/inkobold.spec

Expect to copy extra GTK runtime files into dist/Inkobold/ after the first build.
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parents[1]
ICON = ROOT / "scripts" / "inkobold.png"

a = Analysis(
    [str(ROOT / "inkobold" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ICON), "scripts"),
    ],
    hiddenimports=[
        "gi",
        "gi.repository.Gtk",
        "gi.repository.Gdk",
        "gi.repository.GdkPixbuf",
        "gi.repository.Gio",
        "gi.repository.GLib",
        "gi.repository.GObject",
        "gi.repository.Pango",
        "OpenGL",
        "OpenGL.GL",
        "numpy",
        "PIL",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Inkobold",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Inkobold",
)
