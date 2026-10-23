from __future__ import annotations

import os
import traceback
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from inkobold.core.flavor import INKOBOLD, Flavor, current, set_flavor


def _default_icon_path() -> Path:
    flavor = current()
    env = os.environ.get(f"{flavor.env_prefix}_ICON")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1] / "scripts" / flavor.icon_file


def _apply_icon(win: Gtk.Window) -> None:
    try:
        Gtk.Window.set_default_icon_name(current().prgname)
        win.set_icon_name(current().prgname)
    except Exception:
        pass
    path = _default_icon_path()
    if not path.is_file():
        return
    try:
        # Ensure window managers that read pixbufs still get the art
        pix = GdkPixbuf.Pixbuf.new_from_file_at_size(str(path), 256, 256)
        # Keep a strong reference on the window
        win._inkobold_icon_pixbuf = pix  # type: ignore[attr-defined]
        if hasattr(Gtk.Window, "set_icon"):
            # Some GTK4 builds still expose set_icon(Pixbuf) via overrides
            try:
                win.set_icon(pix)
            except Exception:
                pass
    except Exception as exc:
        print("icon load failed:", path, exc)


class InkoboldApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=current().app_id,
            flags=Gio.ApplicationFlags.HANDLES_OPEN,
        )
        self.win = None

    def _ensure_window(self):
        if self.win is not None:
            return self.win
        try:
            Gtk.Window.set_default_icon_name(current().prgname)
        except Exception:
            pass
        # Imported late so the flavor is set before settings/paths are first read.
        from inkobold.ui.main_window import MainWindow

        try:
            self.win = MainWindow(self)
        except Exception:
            # Avoid a windowless primary instance that blocks later launches
            # (Gtk.Application single-instance + failed __init__).
            traceback.print_exc()
            self.quit()
            return None
        _apply_icon(self.win)
        self.win.connect("close-request", self._on_close)
        return self.win

    def _present_window(self) -> None:
        if self.win is None:
            return
        self.win.apply_window_mode("borderless")
        self.win.present()
        # Fit Canvas once the window has a real allocation
        GLib.idle_add(self.win.canvas.request_fit)

    def do_activate(self) -> None:  # noqa: N802
        if self._ensure_window() is None:
            return
        self._present_window()

    def do_open(self, files: list, n_files: int, hint: str) -> None:  # noqa: N802
        del n_files, hint  # GApplication.open(files, n_files, hint)
        win = self._ensure_window()
        if win is None:
            return
        opened = False
        for gf in files:
            path = gf.get_path() if gf is not None else None
            if not path:
                continue
            if win.open_path(Path(path)):
                opened = True
        if not opened:
            # No usable paths — fall back to the normal empty-window startup.
            win.request_startup_prompt()
        self._present_window()

    def _on_close(self, *_a) -> bool:
        if self.win is None:
            return False
        if self.win._allow_close:
            self.win.shutdown_input()
            return False
        self.win.request_quit()
        return True


def run(argv: list[str] | None = None, flavor: Flavor = INKOBOLD) -> int:
    set_flavor(flavor)
    GLib.set_prgname(flavor.prgname)
    GLib.set_application_name(flavor.display_name)
    app = InkoboldApp()
    return app.run(argv)
