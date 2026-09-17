from __future__ import annotations

import os
import traceback
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk  # noqa: E402

from inkobold.ui.main_window import MainWindow


def _default_icon_path() -> Path:
    env = os.environ.get("INKOBOLD_ICON")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1] / "scripts" / "inkobold.png"


def _apply_icon(win: Gtk.Window) -> None:
    try:
        Gtk.Window.set_default_icon_name("trinkets-inkobold")
        win.set_icon_name("trinkets-inkobold")
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
        super().__init__(application_id="trinkets.inkobold")
        self.win: MainWindow | None = None

    def do_activate(self) -> None:  # noqa: N802
        if self.win is None:
            try:
                Gtk.Window.set_default_icon_name("trinkets-inkobold")
            except Exception:
                pass
            try:
                self.win = MainWindow(self)
            except Exception:
                # Avoid a windowless primary instance that blocks later launches
                # (Gtk.Application single-instance + failed __init__).
                traceback.print_exc()
                self.quit()
                return
            _apply_icon(self.win)
            self.win.connect("close-request", self._on_close)
        self.win.apply_window_mode("borderless")
        self.win.present()
        # Fit Canvas once the window has a real allocation
        GLib.idle_add(self.win.canvas.request_fit)

    def _on_close(self, *_a) -> bool:
        if self.win is None:
            return False
        if self.win._allow_close:
            self.win.shutdown_input()
            return False
        self.win.request_quit()
        return True


def run(argv: list[str] | None = None) -> int:
    GLib.set_prgname("trinkets-inkobold")
    GLib.set_application_name("Inkobold")
    app = InkoboldApp()
    return app.run(argv)
