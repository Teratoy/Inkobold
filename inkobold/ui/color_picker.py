"""Color select: hue editor + recent colors (no preset palette)."""

from __future__ import annotations

from typing import Callable, Optional, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GObject, Gtk  # noqa: E402

MAX_RECENT_COLORS = 8
RgbaTuple = tuple[int, int, int, int]


def configure_hue_chooser(chooser: Gtk.ColorChooserWidget, *, use_alpha: bool) -> None:
    """Show the HSV editor (hue bar + plane); hide the preset/custom palette."""
    chooser.set_property("show-editor", True)
    chooser.set_property("use-alpha", use_alpha)


def rgba_to_tuple(rgba: Gdk.RGBA, *, use_alpha: bool = True) -> RgbaTuple:
    return (
        max(0, min(255, int(round(rgba.red * 255)))),
        max(0, min(255, int(round(rgba.green * 255)))),
        max(0, min(255, int(round(rgba.blue * 255)))),
        max(0, min(255, int(round(rgba.alpha * 255)))) if use_alpha else 255,
    )


def tuple_to_rgba(color: RgbaTuple) -> Gdk.RGBA:
    r, g, b, a = color
    return Gdk.RGBA(red=r / 255.0, green=g / 255.0, blue=b / 255.0, alpha=a / 255.0)


def push_recent(recent: Sequence[RgbaTuple], color: RgbaTuple, *, limit: int = MAX_RECENT_COLORS) -> list[RgbaTuple]:
    """Put color at the front of recent, deduped, capped."""
    out = [color]
    for c in recent:
        if c != color:
            out.append(c)
        if len(out) >= limit:
            break
    return out


def _draw_swatch(rgba: Gdk.RGBA, use_alpha: bool):
    def draw(_area: Gtk.DrawingArea, cr, width: int, height: int) -> None:
        if use_alpha and rgba.alpha < 0.999:
            tile = 5
            for y in range(0, height, tile):
                for x in range(0, width, tile):
                    light = ((x // tile) + (y // tile)) % 2 == 0
                    cr.set_source_rgb(0.85, 0.85, 0.85) if light else cr.set_source_rgb(0.55, 0.55, 0.55)
                    cr.rectangle(x, y, tile, tile)
                    cr.fill()
        cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, rgba.alpha if use_alpha else 1.0)
        cr.rectangle(0, 0, width, height)
        cr.fill()
        cr.set_source_rgba(0, 0, 0, 0.35)
        cr.set_line_width(1)
        cr.rectangle(0.5, 0.5, width - 1, height - 1)
        cr.stroke()

    return draw


class ColorPickerDialog(Gtk.Window):
    """Modal picker: recent swatches + hue/SV editor (no preset palette)."""

    def __init__(
        self,
        parent: Optional[Gtk.Window],
        rgba: Gdk.RGBA,
        *,
        use_alpha: bool = True,
        recent: Optional[Sequence[RgbaTuple]] = None,
        title: str = "Color",
    ) -> None:
        super().__init__(title=title, transient_for=parent, modal=True)
        self.set_default_size(360, 420)
        self.set_resizable(True)
        self._callback: Optional[Callable] = None
        self._use_alpha = use_alpha
        self._recent: list[RgbaTuple] = list(recent or ())

        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        self.set_child(root)

        root.append(Gtk.Label(label="Recent", xalign=0))
        self._recent_row = Gtk.Box(spacing=4)
        self._recent_row.set_halign(Gtk.Align.START)
        root.append(self._recent_row)
        self._rebuild_recent_row()

        self.chooser = Gtk.ColorChooserWidget()
        configure_hue_chooser(self.chooser, use_alpha=use_alpha)
        self.chooser.set_rgba(rgba)
        self.chooser.set_hexpand(True)
        self.chooser.set_vexpand(True)
        root.append(self.chooser)

        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        root.append(actions)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.CANCEL))
        select = Gtk.Button(label="Select")
        select.add_css_class("suggested-action")
        select.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.OK))
        actions.append(cancel)
        actions.append(select)
        self.connect("close-request", self._on_close)

    def rgba(self) -> Gdk.RGBA:
        return self.chooser.get_rgba()

    def recent_colors(self) -> list[RgbaTuple]:
        return list(self._recent)

    def connect_response(self, callback: Callable) -> None:
        self._callback = callback

    def _rebuild_recent_row(self) -> None:
        child = self._recent_row.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._recent_row.remove(child)
            child = nxt
        if not self._recent:
            empty = Gtk.Label(label="No recent colors yet", xalign=0)
            empty.add_css_class("dim-label")
            self._recent_row.append(empty)
            return
        for color in self._recent:
            btn = Gtk.Button()
            btn.set_size_request(28, 28)
            btn.set_tooltip_text(f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}")
            preview = Gtk.DrawingArea()
            preview.set_content_width(28)
            preview.set_content_height(28)
            preview.set_draw_func(_draw_swatch(tuple_to_rgba(color), self._use_alpha))
            btn.set_child(preview)
            btn.connect("clicked", self._on_recent_clicked, color)
            self._recent_row.append(btn)

    def _on_recent_clicked(self, _btn: Gtk.Button, color: RgbaTuple) -> None:
        self.chooser.set_rgba(tuple_to_rgba(color))

    def _emit(self, response: Gtk.ResponseType) -> None:
        if response == Gtk.ResponseType.OK:
            self._recent = push_recent(
                self._recent,
                rgba_to_tuple(self.chooser.get_rgba(), use_alpha=self._use_alpha),
            )
        if self._callback:
            self._callback(self, response)
        self.destroy()

    def _on_close(self, *_a) -> bool:
        self._emit(Gtk.ResponseType.CANCEL)
        return True


class ColorSelectButton(Gtk.Button):
    """Swatch button that opens ColorPickerDialog (hue editor + recent colors)."""

    __gsignals__ = {
        "color-set": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, *, use_alpha: bool = True) -> None:
        super().__init__()
        self._use_alpha = use_alpha
        self._rgba = Gdk.RGBA(red=0.0, green=0.0, blue=0.0, alpha=1.0)
        self._recent: list[RgbaTuple] = []
        self._picker: Optional[ColorPickerDialog] = None

        self._preview = Gtk.DrawingArea()
        self._preview.set_hexpand(True)
        self._preview.set_content_height(28)
        self._preview.set_draw_func(self._draw_preview)
        self.set_child(self._preview)
        self.connect("clicked", self._on_clicked)

    def get_rgba(self) -> Gdk.RGBA:
        return self._rgba.copy()

    def set_rgba(self, rgba: Gdk.RGBA) -> None:
        self._rgba.red = float(rgba.red)
        self._rgba.green = float(rgba.green)
        self._rgba.blue = float(rgba.blue)
        self._rgba.alpha = float(rgba.alpha) if self._use_alpha else 1.0
        self._preview.queue_draw()

    def set_use_alpha(self, use_alpha: bool) -> None:
        self._use_alpha = bool(use_alpha)
        if not self._use_alpha:
            self._rgba.alpha = 1.0
        self._preview.queue_draw()

    def get_use_alpha(self) -> bool:
        return self._use_alpha

    def get_recent_colors(self) -> list[RgbaTuple]:
        return list(self._recent)

    def set_recent_colors(self, colors: Sequence[RgbaTuple]) -> None:
        self._recent = list(colors)[:MAX_RECENT_COLORS]

    def _draw_preview(
        self,
        _area: Gtk.DrawingArea,
        cr,
        width: int,
        height: int,
    ) -> None:
        _draw_swatch(self._rgba, self._use_alpha)(_area, cr, width, height)

    def _on_clicked(self, *_a) -> None:
        if self._picker is not None:
            self._picker.present()
            return
        parent = self.get_root()
        if not isinstance(parent, Gtk.Window):
            parent = None
        dialog = ColorPickerDialog(
            parent,
            self.get_rgba(),
            use_alpha=self._use_alpha,
            recent=self._recent,
            title="Color",
        )
        self._picker = dialog

        def on_response(dlg: ColorPickerDialog, response: Gtk.ResponseType) -> None:
            self._picker = None
            if response == Gtk.ResponseType.OK:
                self.set_rgba(dlg.rgba())
                self._recent = dlg.recent_colors()
                self.emit("color-set")

        dialog.connect_response(on_response)
        dialog.present()
