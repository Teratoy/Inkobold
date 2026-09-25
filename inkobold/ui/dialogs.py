from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from inkobold.core.image_meta import COLOR_DEPTH_CHOICES, DEFAULT_DPI
from inkobold.core.shortcuts import (
    DEFAULT_SHORTCUTS,
    SHORTCUT_LABELS,
    SHORTCUT_ORDER,
    format_accels,
)
from inkobold.ui.color_picker import ColorSelectButton, configure_hue_chooser, rgba_to_tuple, tuple_to_rgba


@dataclass
class EffectOption:
    """One control in an EffectPreviewDialog."""

    key: str
    label: str
    kind: str  # "spin" | "choice" | "color"
    default: Any
    minimum: float = 0
    maximum: float = 100
    step: float = 1
    digits: int = 0
    choices: tuple[str, ...] = field(default_factory=tuple)
    use_alpha: bool = True


EFFECT_APPLY_TO_ACTIVE = "Active layer"
EFFECT_APPLY_TO_ALL = "All layers"
EFFECT_APPLY_TO_CHOICES = (EFFECT_APPLY_TO_ACTIVE, EFFECT_APPLY_TO_ALL)


def _attach_shortcut_focus_guard(parent: Gtk.Window, window: Gtk.Window) -> None:
    """Suspend main-window bare-key shortcuts while this dialog focuses a text field."""
    watch = getattr(parent, "_watch_focus_for_shortcuts", None)
    if callable(watch):
        watch(window)


class StartupDialog(Gtk.Window):
    """Ask Open or New at launch. YES=new, NO=open, CANCEL=quit."""

    def __init__(self, parent: Gtk.Window) -> None:
        super().__init__(title="Inkobold", transient_for=parent, modal=True)
        _attach_shortcut_focus_guard(parent, self)
        self.set_default_size(360, 140)
        self.set_resizable(False)
        self._callback = None

        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )
        self.set_child(root)
        root.append(Gtk.Label(
            label="Open an existing file, or create a new canvas.",
            xalign=0,
            wrap=True,
        ))

        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=8)
        root.append(actions)
        quit_btn = Gtk.Button(label="Quit")
        quit_btn.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.CANCEL))
        open_btn = Gtk.Button(label="Open File…")
        open_btn.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.NO))
        new_btn = Gtk.Button(label="New File…")
        new_btn.add_css_class("suggested-action")
        new_btn.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.YES))
        actions.append(quit_btn)
        actions.append(open_btn)
        actions.append(new_btn)
        self.connect("close-request", self._on_close)

    def connect_response(self, callback) -> None:
        self._callback = callback

    def _emit(self, response: Gtk.ResponseType) -> None:
        if self._callback:
            self._callback(self, response)
        self.destroy()

    def _on_close(self, *_a) -> bool:
        self._emit(Gtk.ResponseType.CANCEL)
        return True


class NewFileDialog(Gtk.Window):
    """New-document size prompt. Uses a plain Window so action buttons always show on GTK4."""

    def __init__(self, parent: Gtk.Window, default_w: int = 1920, default_h: int = 1080) -> None:
        super().__init__(title="New File", transient_for=parent, modal=True)
        _attach_shortcut_focus_guard(parent, self)
        self.set_default_size(380, 240)
        self.set_resizable(False)
        self._response = Gtk.ResponseType.CANCEL
        self._callback = None

        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )
        self.set_child(root)
        root.append(Gtk.Label(label="Canvas size (pixels)", xalign=0))

        grid = Gtk.Grid(column_spacing=8, row_spacing=8)
        root.append(grid)
        self.width_spin = Gtk.SpinButton.new_with_range(1, 16384, 1)
        self.width_spin.set_value(default_w)
        self.height_spin = Gtk.SpinButton.new_with_range(1, 16384, 1)
        self.height_spin.set_value(default_h)
        grid.attach(Gtk.Label(label="Width", xalign=0), 0, 0, 1, 1)
        grid.attach(self.width_spin, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Height", xalign=0), 0, 1, 1, 1)
        grid.attach(self.height_spin, 1, 1, 1, 1)

        presets = Gtk.Box(spacing=6)
        root.append(presets)
        for label, w, h in (
            ("HD", 1920, 1080),
            ("Square", 2048, 2048),
            ("A4@150", 1240, 1754),
            ("4K", 3840, 2160),
        ):
            btn = Gtk.Button(label=label)
            btn.connect("clicked", self._preset, w, h)
            presets.append(btn)

        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=8)
        root.append(actions)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", self._on_cancel)
        create = Gtk.Button(label="Create")
        create.add_css_class("suggested-action")
        create.connect("clicked", self._on_create)
        actions.append(cancel)
        actions.append(create)

        self.connect("close-request", self._on_close)

    def _preset(self, _btn: Gtk.Button, w: int, h: int) -> None:
        self.width_spin.set_value(w)
        self.height_spin.set_value(h)

    def size(self) -> tuple[int, int]:
        return int(self.width_spin.get_value()), int(self.height_spin.get_value())

    def connect_response(self, callback) -> None:
        self._callback = callback

    def _emit(self, response: Gtk.ResponseType) -> None:
        self._response = response
        if self._callback:
            self._callback(self, response)
        self.destroy()

    def _on_cancel(self, *_a) -> None:
        self._emit(Gtk.ResponseType.CANCEL)

    def _on_create(self, *_a) -> None:
        self._emit(Gtk.ResponseType.OK)

    def _on_close(self, *_a) -> bool:
        self._emit(Gtk.ResponseType.CANCEL)
        return True


class ThemeColorDialog(Gtk.Window):
    """Pick a seed color; the UI is recolored to shades of it."""

    def __init__(
        self,
        parent: Gtk.Window,
        rgb: tuple[int, int, int],
        on_preview: Optional[Callable[[tuple[int, int, int]], None]] = None,
        *,
        debounce_ms: int = 40,
    ) -> None:
        # Non-modal so the parent chrome stays undimmed for live preview.
        super().__init__(title="Theme Color", transient_for=parent, modal=False)
        _attach_shortcut_focus_guard(parent, self)
        self.set_default_size(380, 480)
        self.set_resizable(True)
        self._callback = None
        self._on_preview = on_preview
        self._debounce_ms = max(0, int(debounce_ms))
        self._preview_source: Optional[int] = None
        self._closed = False

        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )
        self.set_child(root)
        root.append(Gtk.Label(
            label="Accent tint for the glass chrome (white keeps the DaemonDomain look).",
            xalign=0,
            wrap=True,
        ))
        self.chooser = Gtk.ColorChooserWidget()
        configure_hue_chooser(self.chooser, use_alpha=False)
        rgba = Gdk.RGBA()
        rgba.red, rgba.green, rgba.blue, rgba.alpha = rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0, 1.0
        self.chooser.set_rgba(rgba)
        self.chooser.set_hexpand(True)
        self.chooser.set_vexpand(True)
        self.chooser.connect("notify::rgba", self._schedule_preview)
        root.append(self.chooser)

        hint = Gtk.Label(label="Chrome updates as you pick.", xalign=0)
        hint.add_css_class("dim-label")
        root.append(hint)

        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=8)
        root.append(actions)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.CANCEL))
        apply = Gtk.Button(label="Apply")
        apply.add_css_class("suggested-action")
        apply.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.OK))
        actions.append(cancel)
        actions.append(apply)
        self.connect("close-request", self._on_close)

        if self._on_preview is not None:
            GLib.idle_add(self._fire_preview)

    def rgb(self) -> tuple[int, int, int]:
        c = self.chooser.get_rgba()
        return int(c.red * 255), int(c.green * 255), int(c.blue * 255)

    def connect_response(self, callback) -> None:
        self._callback = callback

    def _schedule_preview(self, *_a) -> None:
        if self._closed or self._on_preview is None:
            return
        if self._preview_source is not None:
            GLib.source_remove(self._preview_source)
            self._preview_source = None
        if self._debounce_ms <= 0:
            self._fire_preview()
            return
        self._preview_source = GLib.timeout_add(self._debounce_ms, self._fire_preview)

    def _fire_preview(self) -> bool:
        self._preview_source = None
        if self._closed or self._on_preview is None:
            return False
        try:
            self._on_preview(self.rgb())
        except Exception:
            pass
        return False

    def _cancel_pending(self) -> None:
        if self._preview_source is not None:
            GLib.source_remove(self._preview_source)
            self._preview_source = None

    def _emit(self, response: Gtk.ResponseType) -> None:
        if self._closed:
            return
        self._closed = True
        self._cancel_pending()
        if self._callback:
            self._callback(self, response)
        self.destroy()

    def _on_close(self, *_a) -> bool:
        self._emit(Gtk.ResponseType.CANCEL)
        return True


class HistoryStepsDialog(Gtk.Window):
    """Choose how many undo/redo snapshots to keep."""

    def __init__(self, parent: Gtk.Window, steps: int) -> None:
        super().__init__(title="Memory", transient_for=parent, modal=True)
        _attach_shortcut_focus_guard(parent, self)
        self.set_default_size(340, 180)
        self.set_resizable(False)
        self._callback = None

        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )
        self.set_child(root)
        root.append(Gtk.Label(
            label="Undo / redo history depth. Higher uses more RAM.",
            xalign=0,
            wrap=True,
        ))
        row = Gtk.Box(spacing=8)
        root.append(row)
        row.append(Gtk.Label(label="Steps", xalign=0, hexpand=True))
        self.steps_spin = Gtk.SpinButton.new_with_range(1, 2000, 1)
        self.steps_spin.set_value(steps)
        self.steps_spin.set_hexpand(True)
        row.append(self.steps_spin)

        presets = Gtk.Box(spacing=6)
        root.append(presets)
        for label, n in (("32", 32), ("64", 64), ("124", 124), ("256", 256), ("512", 512)):
            btn = Gtk.Button(label=label)
            btn.connect("clicked", lambda _b, v=n: self.steps_spin.set_value(v))
            presets.append(btn)

        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=8)
        root.append(actions)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.CANCEL))
        apply = Gtk.Button(label="Apply")
        apply.add_css_class("suggested-action")
        apply.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.OK))
        actions.append(cancel)
        actions.append(apply)
        self.connect("close-request", self._on_close)

    def steps(self) -> int:
        return int(self.steps_spin.get_value())

    def connect_response(self, callback) -> None:
        self._callback = callback

    def _emit(self, response: Gtk.ResponseType) -> None:
        if self._callback:
            self._callback(self, response)
        self.destroy()

    def _on_close(self, *_a) -> bool:
        self._emit(Gtk.ResponseType.CANCEL)
        return True


class VisibleToolsDialog(Gtk.Window):
    """Choose which tools appear in the Tools column."""

    def __init__(
        self,
        parent: Gtk.Window,
        tools: list[tuple[str, str]],
        visible_ids: list[str],
    ) -> None:
        super().__init__(title="Visible Tools", transient_for=parent, modal=True)
        _attach_shortcut_focus_guard(parent, self)
        self.set_default_size(320, 420)
        self.set_resizable(True)
        self._callback = None
        self._checks: dict[str, Gtk.CheckButton] = {}
        visible = set(visible_ids)

        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )
        self.set_child(root)
        root.append(Gtk.Label(
            label="Show these tools in the Tools column. Hidden tools stay available via shortcuts.",
            xalign=0,
            wrap=True,
        ))

        scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(260)
        root.append(scroll)

        list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scroll.set_child(list_box)
        for tid, label in tools:
            row = Gtk.CheckButton(label=label)
            row.set_active(tid in visible)
            row.connect("toggled", self._on_toggled)
            self._checks[tid] = row
            list_box.append(row)

        presets = Gtk.Box(spacing=6)
        root.append(presets)
        all_btn = Gtk.Button(label="All")
        all_btn.connect("clicked", lambda *_: self._set_all(True))
        none_btn = Gtk.Button(label="None")
        none_btn.connect("clicked", lambda *_: self._set_all(False))
        presets.append(all_btn)
        presets.append(none_btn)

        self._hint = Gtk.Label(label="", xalign=0)
        self._hint.add_css_class("dim-label")
        root.append(self._hint)

        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=4)
        root.append(actions)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.CANCEL))
        self._apply = Gtk.Button(label="Apply")
        self._apply.add_css_class("suggested-action")
        self._apply.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.OK))
        actions.append(cancel)
        actions.append(self._apply)
        self.connect("close-request", self._on_close)
        self._on_toggled()

    def _set_all(self, active: bool) -> None:
        for check in self._checks.values():
            check.set_active(active)

    def _on_toggled(self, *_a) -> None:
        n = sum(1 for c in self._checks.values() if c.get_active())
        ok = n > 0
        self._apply.set_sensitive(ok)
        self._hint.set_label("" if ok else "Select at least one tool.")

    def visible_ids(self) -> list[str]:
        return [tid for tid, check in self._checks.items() if check.get_active()]

    def connect_response(self, callback) -> None:
        self._callback = callback

    def _emit(self, response: Gtk.ResponseType) -> None:
        if response == Gtk.ResponseType.OK and not self.visible_ids():
            return
        if self._callback:
            self._callback(self, response)
        self.destroy()

    def _on_close(self, *_a) -> bool:
        self._emit(Gtk.ResponseType.CANCEL)
        return True


class GridOverlayDialog(Gtk.Window):
    """Choose how many row/column cells the canvas grid overlay uses."""

    def __init__(self, parent: Gtk.Window, rows: int = 8, columns: int = 8) -> None:
        super().__init__(title="Grid Overlay", transient_for=parent, modal=True)
        _attach_shortcut_focus_guard(parent, self)
        self.set_default_size(340, 200)
        self.set_resizable(False)
        self._callback = None

        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )
        self.set_child(root)
        root.append(Gtk.Label(
            label="Visual guide only — does not snap or change drawing. "
            "1×1 is a centered crosshair; higher values add evenly spaced guides.",
            xalign=0,
            wrap=True,
        ))

        grid = Gtk.Grid(column_spacing=8, row_spacing=8)
        root.append(grid)
        self.rows_spin = Gtk.SpinButton.new_with_range(1, 64, 1)
        self.rows_spin.set_value(rows)
        self.rows_spin.set_hexpand(True)
        self.columns_spin = Gtk.SpinButton.new_with_range(1, 64, 1)
        self.columns_spin.set_value(columns)
        self.columns_spin.set_hexpand(True)
        grid.attach(Gtk.Label(label="Rows", xalign=0), 0, 0, 1, 1)
        grid.attach(self.rows_spin, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Columns", xalign=0), 0, 1, 1, 1)
        grid.attach(self.columns_spin, 1, 1, 1, 1)

        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=8)
        root.append(actions)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.CANCEL))
        apply = Gtk.Button(label="Apply")
        apply.add_css_class("suggested-action")
        apply.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.OK))
        actions.append(cancel)
        actions.append(apply)
        self.connect("close-request", self._on_close)

    def rows(self) -> int:
        return int(self.rows_spin.get_value())

    def columns(self) -> int:
        return int(self.columns_spin.get_value())

    def connect_response(self, callback) -> None:
        self._callback = callback

    def _emit(self, response: Gtk.ResponseType) -> None:
        if self._callback:
            self._callback(self, response)
        self.destroy()

    def _on_close(self, *_a) -> bool:
        self._emit(Gtk.ResponseType.CANCEL)
        return True


class SunSettingsDialog(Gtk.Window):
    """Elevation for the canvas sun that drives 3D lighting."""

    def __init__(self, parent: Gtk.Window, elevation: float = 0.70) -> None:
        super().__init__(title="Sun Light", transient_for=parent, modal=True)
        _attach_shortcut_focus_guard(parent, self)
        self.set_default_size(360, 200)
        self.set_resizable(False)
        self._callback = None

        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )
        self.set_child(root)
        root.append(Gtk.Label(
            label="When Show Sun is on, drag the sun on the canvas to aim "
            "shadows and highlights for 3D Pen, 3D Fill, Bubbles, Metal Relief, "
            "and Milk. Elevation controls how frontal vs. raking the light is.",
            xalign=0,
            wrap=True,
        ))

        grid = Gtk.Grid(column_spacing=8, row_spacing=8)
        root.append(grid)
        self.elevation_spin = Gtk.SpinButton.new_with_range(0.15, 1.50, 0.05)
        self.elevation_spin.set_digits(2)
        self.elevation_spin.set_value(elevation)
        self.elevation_spin.set_hexpand(True)
        grid.attach(Gtk.Label(label="Elevation", xalign=0), 0, 0, 1, 1)
        grid.attach(self.elevation_spin, 1, 0, 1, 1)

        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=8)
        root.append(actions)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.CANCEL))
        apply = Gtk.Button(label="Apply")
        apply.add_css_class("suggested-action")
        apply.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.OK))
        actions.append(cancel)
        actions.append(apply)
        self.connect("close-request", self._on_close)

    def elevation(self) -> float:
        return float(self.elevation_spin.get_value())

    def connect_response(self, callback) -> None:
        self._callback = callback

    def _emit(self, response: Gtk.ResponseType) -> None:
        if self._callback:
            self._callback(self, response)
        self.destroy()

    def _on_close(self, *_a) -> bool:
        self._emit(Gtk.ResponseType.CANCEL)
        return True


class QuitConfirmDialog(Gtk.Window):
    """Ask whether to save before quitting. YES=save, NO=discard, CANCEL=stay."""

    def __init__(self, parent: Gtk.Window, filename: str = "untitled") -> None:
        super().__init__(title="Exit Inkobold", transient_for=parent, modal=True)
        _attach_shortcut_focus_guard(parent, self)
        self.set_default_size(420, 140)
        self.set_resizable(False)
        self._callback = None

        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )
        self.set_child(root)
        root.append(Gtk.Label(
            label=f"Save changes to “{filename}” before exiting?",
            xalign=0,
            wrap=True,
        ))

        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=8)
        root.append(actions)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.CANCEL))
        discard = Gtk.Button(label="Don't Save")
        discard.add_css_class("destructive-action")
        discard.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.NO))
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.YES))
        actions.append(cancel)
        actions.append(discard)
        actions.append(save)
        self.connect("close-request", self._on_close)

    def connect_response(self, callback) -> None:
        self._callback = callback

    def _emit(self, response: Gtk.ResponseType) -> None:
        if self._callback:
            self._callback(self, response)
        self.destroy()

    def _on_close(self, *_a) -> bool:
        self._emit(Gtk.ResponseType.CANCEL)
        return True


class DpiDialog(Gtk.Window):
    """Document / default DPI for export metadata."""

    def __init__(self, parent: Gtk.Window, dpi: int = DEFAULT_DPI) -> None:
        super().__init__(title="DPI", transient_for=parent, modal=True)
        _attach_shortcut_focus_guard(parent, self)
        self.set_default_size(340, 180)
        self.set_resizable(False)
        self._callback = None

        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )
        self.set_child(root)
        root.append(Gtk.Label(
            label="Dots per inch used when exporting images.",
            xalign=0,
            wrap=True,
        ))
        row = Gtk.Box(spacing=8)
        root.append(row)
        row.append(Gtk.Label(label="DPI", xalign=0, hexpand=True))
        self.dpi_spin = Gtk.SpinButton.new_with_range(1, 1200, 1)
        self.dpi_spin.set_value(dpi)
        self.dpi_spin.set_hexpand(True)
        row.append(self.dpi_spin)

        presets = Gtk.Box(spacing=6)
        root.append(presets)
        for label, n in (("72", 72), ("96", 96), ("150", 150), ("300", 300), ("600", 600)):
            btn = Gtk.Button(label=label)
            btn.connect("clicked", lambda _b, v=n: self.dpi_spin.set_value(v))
            presets.append(btn)

        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=8)
        root.append(actions)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.CANCEL))
        apply = Gtk.Button(label="Apply")
        apply.add_css_class("suggested-action")
        apply.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.OK))
        actions.append(cancel)
        actions.append(apply)
        self.connect("close-request", self._on_close)

    def dpi(self) -> int:
        return int(self.dpi_spin.get_value())

    def connect_response(self, callback) -> None:
        self._callback = callback

    def _emit(self, response: Gtk.ResponseType) -> None:
        if self._callback:
            self._callback(self, response)
        self.destroy()

    def _on_close(self, *_a) -> bool:
        self._emit(Gtk.ResponseType.CANCEL)
        return True


class ColorDepthDialog(Gtk.Window):
    """Document color depth preference (affects export)."""

    def __init__(self, parent: Gtk.Window, color_depth: int = 32) -> None:
        super().__init__(title="Color Depth", transient_for=parent, modal=True)
        _attach_shortcut_focus_guard(parent, self)
        self.set_default_size(360, 200)
        self.set_resizable(False)
        self._callback = None
        self._depths = [d for d, _ in COLOR_DEPTH_CHOICES]

        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )
        self.set_child(root)
        root.append(Gtk.Label(
            label="Editing and export color depth. 16 bpc uses uint16 layers; GPU preview is 8-bit.",
            xalign=0,
            wrap=True,
        ))
        self.depth_dropdown = Gtk.DropDown.new_from_strings([label for _, label in COLOR_DEPTH_CHOICES])
        idx = self._depths.index(color_depth) if color_depth in self._depths else 0
        self.depth_dropdown.set_selected(idx)
        root.append(self.depth_dropdown)

        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=8)
        root.append(actions)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.CANCEL))
        apply = Gtk.Button(label="Apply")
        apply.add_css_class("suggested-action")
        apply.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.OK))
        actions.append(cancel)
        actions.append(apply)
        self.connect("close-request", self._on_close)

    def color_depth(self) -> int:
        i = int(self.depth_dropdown.get_selected())
        return self._depths[max(0, min(i, len(self._depths) - 1))]

    def connect_response(self, callback) -> None:
        self._callback = callback

    def _emit(self, response: Gtk.ResponseType) -> None:
        if self._callback:
            self._callback(self, response)
        self.destroy()

    def _on_close(self, *_a) -> bool:
        self._emit(Gtk.ResponseType.CANCEL)
        return True


class CropDialog(Gtk.Window):
    """Crop the canvas to a rectangle; live size readout before commit."""

    def __init__(
        self,
        parent: Gtk.Window,
        width: int,
        height: int,
        *,
        selection_bounds: Optional[tuple[int, int, int, int]] = None,
    ) -> None:
        super().__init__(title="Crop Canvas", transient_for=parent, modal=True)
        _attach_shortcut_focus_guard(parent, self)
        self.set_default_size(420, 320)
        self.set_resizable(False)
        self._callback = None
        self._doc_w = max(1, int(width))
        self._doc_h = max(1, int(height))
        self._selection_bounds = selection_bounds
        self._syncing = False

        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )
        self.set_child(root)
        root.append(Gtk.Label(
            label="Trim the canvas. New size updates as you adjust the crop.",
            xalign=0,
            wrap=True,
        ))
        self.current_label = Gtk.Label(
            label=f"Current size: {self._doc_w} × {self._doc_h} px",
            xalign=0,
        )
        self.current_label.add_css_class("dim-label")
        root.append(self.current_label)

        grid = Gtk.Grid(column_spacing=8, row_spacing=8)
        root.append(grid)

        self.left_spin = Gtk.SpinButton.new_with_range(0, self._doc_w - 1, 1)
        self.top_spin = Gtk.SpinButton.new_with_range(0, self._doc_h - 1, 1)
        self.right_spin = Gtk.SpinButton.new_with_range(0, self._doc_w - 1, 1)
        self.bottom_spin = Gtk.SpinButton.new_with_range(0, self._doc_h - 1, 1)
        self.width_spin = Gtk.SpinButton.new_with_range(1, self._doc_w, 1)
        self.height_spin = Gtk.SpinButton.new_with_range(1, self._doc_h, 1)
        self.width_spin.set_value(self._doc_w)
        self.height_spin.set_value(self._doc_h)

        grid.attach(Gtk.Label(label="Left", xalign=0), 0, 0, 1, 1)
        grid.attach(self.left_spin, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Right", xalign=0), 2, 0, 1, 1)
        grid.attach(self.right_spin, 3, 0, 1, 1)
        grid.attach(Gtk.Label(label="Top", xalign=0), 0, 1, 1, 1)
        grid.attach(self.top_spin, 1, 1, 1, 1)
        grid.attach(Gtk.Label(label="Bottom", xalign=0), 2, 1, 1, 1)
        grid.attach(self.bottom_spin, 3, 1, 1, 1)
        grid.attach(Gtk.Label(label="Width", xalign=0), 0, 2, 1, 1)
        grid.attach(self.width_spin, 1, 2, 1, 1)
        grid.attach(Gtk.Label(label="Height", xalign=0), 2, 2, 1, 1)
        grid.attach(self.height_spin, 3, 2, 1, 1)

        for spin in (
            self.left_spin,
            self.top_spin,
            self.right_spin,
            self.bottom_spin,
        ):
            spin.connect("value-changed", self._on_margin_changed)
        self.width_spin.connect("value-changed", self._on_size_changed)
        self.height_spin.connect("value-changed", self._on_size_changed)

        if selection_bounds is not None:
            use_sel = Gtk.Button(label="Use Selection Bounds")
            use_sel.set_tooltip_text("Crop to the axis-aligned bounds of the current selection.")
            use_sel.connect("clicked", self._on_use_selection)
            root.append(use_sel)

        self.size_label = Gtk.Label(xalign=0)
        self.size_label.add_css_class("title-4")
        root.append(self.size_label)
        self._refresh_size_label()

        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=8)
        root.append(actions)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.CANCEL))
        apply = Gtk.Button(label="Crop")
        apply.add_css_class("suggested-action")
        apply.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.OK))
        actions.append(cancel)
        actions.append(apply)
        self.connect("close-request", self._on_close)

    def rect(self) -> tuple[int, int, int, int]:
        """Return (x, y, width, height) of the crop rectangle."""
        left = int(self.left_spin.get_value())
        top = int(self.top_spin.get_value())
        width = int(self.width_spin.get_value())
        height = int(self.height_spin.get_value())
        return left, top, width, height

    def connect_response(self, callback) -> None:
        self._callback = callback

    def _emit(self, response: Gtk.ResponseType) -> None:
        if self._callback:
            self._callback(self, response)
        self.destroy()

    def _on_close(self, *_a) -> bool:
        self._emit(Gtk.ResponseType.CANCEL)
        return True

    def _on_use_selection(self, *_a) -> None:
        if self._selection_bounds is None:
            return
        x, y, w, h = self._selection_bounds
        self._apply_rect(x, y, w, h)

    def _apply_rect(self, x: int, y: int, w: int, h: int) -> None:
        x = max(0, min(int(x), self._doc_w - 1))
        y = max(0, min(int(y), self._doc_h - 1))
        w = max(1, min(int(w), self._doc_w - x))
        h = max(1, min(int(h), self._doc_h - y))
        self._syncing = True
        try:
            self.left_spin.set_value(x)
            self.top_spin.set_value(y)
            self.right_spin.set_value(self._doc_w - x - w)
            self.bottom_spin.set_value(self._doc_h - y - h)
            self.width_spin.set_value(w)
            self.height_spin.set_value(h)
        finally:
            self._syncing = False
        self._refresh_size_label()

    def _on_margin_changed(self, *_a) -> None:
        if self._syncing:
            return
        left = int(self.left_spin.get_value())
        top = int(self.top_spin.get_value())
        right = int(self.right_spin.get_value())
        bottom = int(self.bottom_spin.get_value())
        # Keep at least 1×1 by shrinking the opposite margin if needed.
        if left + right >= self._doc_w:
            right = max(0, self._doc_w - left - 1)
        if top + bottom >= self._doc_h:
            bottom = max(0, self._doc_h - top - 1)
        width = self._doc_w - left - right
        height = self._doc_h - top - bottom
        self._syncing = True
        try:
            self.right_spin.set_value(right)
            self.bottom_spin.set_value(bottom)
            self.width_spin.set_value(width)
            self.height_spin.set_value(height)
        finally:
            self._syncing = False
        self._refresh_size_label()

    def _on_size_changed(self, *_a) -> None:
        if self._syncing:
            return
        left = int(self.left_spin.get_value())
        top = int(self.top_spin.get_value())
        width = int(self.width_spin.get_value())
        height = int(self.height_spin.get_value())
        width = max(1, min(width, self._doc_w - left))
        height = max(1, min(height, self._doc_h - top))
        right = self._doc_w - left - width
        bottom = self._doc_h - top - height
        self._syncing = True
        try:
            self.width_spin.set_value(width)
            self.height_spin.set_value(height)
            self.right_spin.set_value(right)
            self.bottom_spin.set_value(bottom)
        finally:
            self._syncing = False
        self._refresh_size_label()

    def _refresh_size_label(self) -> None:
        _x, _y, w, h = self.rect()
        self.size_label.set_text(f"New canvas size: {w} × {h} px")


class CaptureShortcutDialog(Gtk.Window):
    """Modal key-capture dialog for rebinding a single shortcut."""

    def __init__(self, parent: Gtk.Window, action: str, label: str) -> None:
        super().__init__(title="Rebind Shortcut", transient_for=parent, modal=True)
        _attach_shortcut_focus_guard(parent, self)
        self.set_default_size(380, 140)
        self.set_resizable(False)
        self._callback: Optional[Callable] = None
        self.action = action
        self._accel: Optional[str] = None

        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )
        self.set_child(root)
        root.append(Gtk.Label(
            label=f"Press a new shortcut for “{label}”.\nEsc cancels · Backspace clears",
            xalign=0,
            wrap=True,
        ))
        self.hint = Gtk.Label(label="Waiting for key…", xalign=0)
        self.hint.add_css_class("dim-label")
        root.append(self.hint)

        keys = Gtk.EventControllerKey.new()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self.connect("close-request", self._on_close)
        self.connect("map", lambda *_: self.grab_focus())

    def connect_response(self, callback) -> None:
        self._callback = callback

    def _emit(self, response: Gtk.ResponseType) -> None:
        if self._callback:
            self._callback(self, response)
        self.destroy()

    def _on_key(self, _c, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        if keyval in (Gdk.KEY_Escape,):
            self._emit(Gtk.ResponseType.CANCEL)
            return True
        if keyval in (Gdk.KEY_BackSpace, Gdk.KEY_Delete):
            self._accel = ""
            self._emit(Gtk.ResponseType.OK)
            return True
        if keyval in (
            Gdk.KEY_Shift_L, Gdk.KEY_Shift_R,
            Gdk.KEY_Control_L, Gdk.KEY_Control_R,
            Gdk.KEY_Alt_L, Gdk.KEY_Alt_R,
            Gdk.KEY_Meta_L, Gdk.KEY_Meta_R,
            Gdk.KEY_Super_L, Gdk.KEY_Super_R,
        ):
            return True
        mods = state & Gtk.accelerator_get_default_mod_mask()
        name = Gtk.accelerator_name(keyval, mods)
        if not name:
            return True
        self._accel = name
        self._emit(Gtk.ResponseType.OK)
        return True

    def accel(self) -> Optional[str]:
        return self._accel

    def _on_close(self, *_a) -> bool:
        self._emit(Gtk.ResponseType.CANCEL)
        return True


class ShortcutsDialog(Gtk.Window):
    """List all shortcuts with per-row rebind and reset."""

    def __init__(self, parent: Gtk.Window, shortcuts: dict[str, list[str]]) -> None:
        super().__init__(title="Shortcuts", transient_for=parent, modal=True)
        _attach_shortcut_focus_guard(parent, self)
        self.set_default_size(520, 480)
        self._callback = None
        self.shortcuts = {k: list(v) for k, v in shortcuts.items()}
        self._rows: dict[str, Gtk.Label] = {}

        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        self.set_child(root)
        root.append(Gtk.Label(
            label="Click Rebind, then press a key. Backspace clears a binding.",
            xalign=0,
            wrap=True,
        ))

        scrolled = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        root.append(scrolled)
        list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scrolled.set_child(list_box)

        for action in SHORTCUT_ORDER:
            if action not in self.shortcuts:
                self.shortcuts[action] = list(DEFAULT_SHORTCUTS.get(action, []))
            row = Gtk.Box(spacing=8, margin_top=2, margin_bottom=2)
            label = SHORTCUT_LABELS.get(action, action)
            name_lbl = Gtk.Label(label=label, xalign=0, hexpand=True)
            name_lbl.set_width_chars(18)
            row.append(name_lbl)
            keys_lbl = Gtk.Label(label=format_accels(self.shortcuts[action]), xalign=1)
            keys_lbl.set_width_chars(18)
            self._rows[action] = keys_lbl
            row.append(keys_lbl)
            rebind = Gtk.Button(label="Rebind")
            rebind.connect("clicked", self._on_rebind_clicked, action)
            row.append(rebind)
            list_box.append(row)

        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=8)
        root.append(actions)
        reset = Gtk.Button(label="Reset All")
        reset.connect("clicked", self._on_reset)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.CANCEL))
        apply = Gtk.Button(label="Apply")
        apply.add_css_class("suggested-action")
        apply.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.OK))
        actions.append(reset)
        actions.append(cancel)
        actions.append(apply)
        self.connect("close-request", self._on_close)

    def _refresh_row(self, action: str) -> None:
        lbl = self._rows.get(action)
        if lbl is not None:
            lbl.set_text(format_accels(self.shortcuts.get(action, [])))

    def _on_rebind_clicked(self, _btn: Gtk.Button, action: str) -> None:
        label = SHORTCUT_LABELS.get(action, action)
        dlg = CaptureShortcutDialog(self, action, label)
        dlg.connect_response(self._on_capture_done)
        dlg.present()

    def _on_capture_done(self, dlg: CaptureShortcutDialog, response: int) -> None:
        if response != Gtk.ResponseType.OK:
            return
        accel = dlg.accel()
        if accel is None:
            return
        action = dlg.action
        if accel == "":
            self.shortcuts[action] = []
            self._refresh_row(action)
            return
        for other, accels in self.shortcuts.items():
            if other == action:
                continue
            if accel in accels:
                self.shortcuts[other] = [a for a in accels if a != accel]
                self._refresh_row(other)
        self.shortcuts[action] = [accel]
        self._refresh_row(action)

    def _on_reset(self, *_a) -> None:
        self.shortcuts = {k: list(v) for k, v in DEFAULT_SHORTCUTS.items()}
        for action in self._rows:
            self._refresh_row(action)

    def result_shortcuts(self) -> dict[str, list[str]]:
        return {k: list(v) for k, v in self.shortcuts.items()}

    def connect_response(self, callback) -> None:
        self._callback = callback

    def _emit(self, response: Gtk.ResponseType) -> None:
        if self._callback:
            self._callback(self, response)
        self.destroy()

    def _on_close(self, *_a) -> bool:
        self._emit(Gtk.ResponseType.CANCEL)
        return True


class EffectPreviewDialog(Gtk.Window):
    """Effect options with live canvas preview via on_preview callback.

    Non-modal so GTK does not dim the parent — the preview is the canvas itself.
    """

    def __init__(
        self,
        parent: Gtk.Window,
        title: str,
        blurb: str,
        options: list[EffectOption],
        on_preview: Callable[[dict[str, Any]], None],
        *,
        debounce_ms: int = 60,
    ) -> None:
        super().__init__(title=title, transient_for=parent, modal=False)
        _attach_shortcut_focus_guard(parent, self)
        self.set_default_size(360, 220)
        self.set_resizable(False)
        self._callback: Optional[Callable] = None
        self._on_preview = on_preview
        self._debounce_ms = max(0, int(debounce_ms))
        self._preview_source: Optional[int] = None
        # Every effect gets layer scope; strip any caller duplicate of the same key.
        scope = EffectOption(
            "apply_to",
            "Apply to",
            "choice",
            EFFECT_APPLY_TO_ACTIVE,
            choices=EFFECT_APPLY_TO_CHOICES,
        )
        self._options = [scope] + [o for o in options if o.key != "apply_to"]
        self._widgets: dict[str, Gtk.Widget] = {}
        self._closed = False

        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )
        self.set_child(root)
        if blurb:
            root.append(Gtk.Label(label=blurb, xalign=0, wrap=True))

        for opt in self._options:
            row = Gtk.Box(spacing=8)
            root.append(row)
            row.append(Gtk.Label(label=opt.label, xalign=0, hexpand=True))
            if opt.kind == "choice":
                dd = Gtk.DropDown.new_from_strings(list(opt.choices))
                try:
                    idx = list(opt.choices).index(str(opt.default))
                except ValueError:
                    idx = 0
                dd.set_selected(idx)
                dd.connect("notify::selected", self._schedule_preview)
                dd.set_hexpand(True)
                row.append(dd)
                self._widgets[opt.key] = dd
            elif opt.kind == "color":
                btn = ColorSelectButton(use_alpha=opt.use_alpha)
                default = opt.default
                if isinstance(default, (tuple, list)) and len(default) >= 3:
                    r, g, b = int(default[0]), int(default[1]), int(default[2])
                    a = int(default[3]) if len(default) > 3 else 255
                    btn.set_rgba(tuple_to_rgba((r, g, b, a)))
                btn.set_hexpand(True)
                btn.set_size_request(120, -1)
                btn.connect("color-set", self._schedule_preview)
                row.append(btn)
                self._widgets[opt.key] = btn
            else:
                spin = Gtk.SpinButton.new_with_range(opt.minimum, opt.maximum, opt.step)
                spin.set_digits(opt.digits)
                spin.set_value(float(opt.default))
                spin.set_hexpand(True)
                spin.connect("value-changed", self._schedule_preview)
                row.append(spin)
                self._widgets[opt.key] = spin

        hint = Gtk.Label(label="Preview updates on the canvas.", xalign=0)
        hint.add_css_class("dim-label")
        root.append(hint)

        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=8)
        root.append(actions)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.CANCEL))
        apply = Gtk.Button(label="Apply")
        apply.add_css_class("suggested-action")
        apply.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.OK))
        actions.append(cancel)
        actions.append(apply)
        self.connect("close-request", self._on_close)

        # Immediate first preview with defaults
        GLib.idle_add(self._fire_preview)

    def params(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for opt in self._options:
            w = self._widgets[opt.key]
            if opt.kind == "choice":
                i = int(w.get_selected())  # type: ignore[attr-defined]
                choices = opt.choices
                out[opt.key] = choices[max(0, min(i, len(choices) - 1))]
            elif opt.kind == "color":
                out[opt.key] = rgba_to_tuple(w.get_rgba(), use_alpha=opt.use_alpha)  # type: ignore[attr-defined]
            else:
                val = float(w.get_value())  # type: ignore[attr-defined]
                out[opt.key] = int(round(val)) if opt.digits == 0 else val
        return out

    def connect_response(self, callback) -> None:
        self._callback = callback

    def _schedule_preview(self, *_a) -> None:
        if self._closed:
            return
        if self._preview_source is not None:
            GLib.source_remove(self._preview_source)
            self._preview_source = None
        if self._debounce_ms <= 0:
            self._fire_preview()
            return
        self._preview_source = GLib.timeout_add(self._debounce_ms, self._fire_preview)

    def _fire_preview(self) -> bool:
        self._preview_source = None
        if self._closed:
            return False
        try:
            self._on_preview(self.params())
        except Exception:
            pass
        return False

    def _cancel_pending(self) -> None:
        if self._preview_source is not None:
            GLib.source_remove(self._preview_source)
            self._preview_source = None

    def _emit(self, response: Gtk.ResponseType) -> None:
        if self._closed:
            return
        self._closed = True
        self._cancel_pending()
        if self._callback:
            self._callback(self, response)
        self.destroy()

    def _on_close(self, *_a) -> bool:
        self._emit(Gtk.ResponseType.CANCEL)
        return True


_CURVE_EDITOR_PAD = 14
_CURVE_HIT_RADIUS = 10.0
_CURVE_MAX_POINTS = 16
_CURVE_CHANNEL_COLORS = {
    "RGB": (0.92, 0.92, 0.92),
    "Red": (0.92, 0.32, 0.28),
    "Green": (0.35, 0.82, 0.38),
    "Blue": (0.35, 0.55, 0.95),
    "Value": (0.85, 0.85, 0.55),
}


class CurvesDialog(Gtk.Window):
    """Tone-curve editor with live canvas preview (same session pattern as EffectPreviewDialog)."""

    def __init__(
        self,
        parent: Gtk.Window,
        on_preview: Callable[[dict[str, Any]], None],
        *,
        debounce_ms: int = 16,
    ) -> None:
        from inkobold.core.effects import CURVES_CHANNELS, _curve_lut, _normalize_curve_points

        super().__init__(title="Curves", transient_for=parent, modal=False)
        _attach_shortcut_focus_guard(parent, self)
        self.set_default_size(380, 460)
        self.set_resizable(False)
        self._callback: Optional[Callable] = None
        self._on_preview = on_preview
        self._debounce_ms = max(0, int(debounce_ms))
        self._preview_source: Optional[int] = None
        self._closed = False
        self._channels = tuple(CURVES_CHANNELS)
        self._normalize = _normalize_curve_points
        self._curve_lut = _curve_lut
        self._curves: dict[str, list[tuple[float, float]]] = {
            ch: [(0.0, 0.0), (1.0, 1.0)] for ch in self._channels
        }
        self._channel = "RGB"
        self._drag_index: Optional[int] = None
        self._selected: Optional[int] = None
        self._draw_w = 320.0
        self._draw_h = 320.0

        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )
        self.set_child(root)
        root.append(
            Gtk.Label(
                label=(
                    "Drag the curve to reshape tones. Click to place a point; "
                    "double-click a point to remove it."
                ),
                xalign=0,
                wrap=True,
            )
        )

        scope_row = Gtk.Box(spacing=8)
        root.append(scope_row)
        scope_row.append(Gtk.Label(label="Apply to", xalign=0, hexpand=True))
        self._apply_dd = Gtk.DropDown.new_from_strings(list(EFFECT_APPLY_TO_CHOICES))
        self._apply_dd.set_selected(0)
        self._apply_dd.set_hexpand(True)
        self._apply_dd.connect("notify::selected", self._schedule_preview)
        scope_row.append(self._apply_dd)

        ch_row = Gtk.Box(spacing=8)
        root.append(ch_row)
        ch_row.append(Gtk.Label(label="Channel", xalign=0, hexpand=True))
        self._channel_dd = Gtk.DropDown.new_from_strings(list(self._channels))
        self._channel_dd.set_selected(0)
        self._channel_dd.set_hexpand(True)
        self._channel_dd.connect("notify::selected", self._on_channel_changed)
        ch_row.append(self._channel_dd)

        self._area = Gtk.DrawingArea()
        self._area.set_content_width(320)
        self._area.set_content_height(320)
        self._area.set_hexpand(True)
        self._area.set_draw_func(self._draw_curve)
        cursor = Gdk.Cursor.new_from_name("crosshair")
        if cursor is not None:
            self._area.set_cursor(cursor)
        root.append(self._area)

        # Click before drag in controller order (last added = first to receive):
        # press selects/inserts, then drag moves that point once the threshold is met.
        click = Gtk.GestureClick.new()
        click.set_button(1)
        click.connect("pressed", self._on_click_pressed)
        self._area.add_controller(click)

        drag = Gtk.GestureDrag.new()
        drag.set_button(1)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self._area.add_controller(drag)

        btn_row = Gtk.Box(spacing=8)
        root.append(btn_row)
        reset = Gtk.Button(label="Reset channel")
        reset.connect("clicked", self._on_reset_channel)
        btn_row.append(reset)
        reset_all = Gtk.Button(label="Reset all")
        reset_all.connect("clicked", self._on_reset_all)
        btn_row.append(reset_all)

        hint = Gtk.Label(label="Preview updates on the canvas.", xalign=0)
        hint.add_css_class("dim-label")
        root.append(hint)

        actions = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=4)
        root.append(actions)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.CANCEL))
        apply = Gtk.Button(label="Apply")
        apply.add_css_class("suggested-action")
        apply.connect("clicked", lambda *_: self._emit(Gtk.ResponseType.OK))
        actions.append(cancel)
        actions.append(apply)
        self.connect("close-request", self._on_close)

        GLib.idle_add(self._fire_preview)

    def params(self) -> dict[str, Any]:
        i = int(self._apply_dd.get_selected())
        apply_to = EFFECT_APPLY_TO_CHOICES[max(0, min(i, len(EFFECT_APPLY_TO_CHOICES) - 1))]
        curves = {ch: [tuple(p) for p in pts] for ch, pts in self._curves.items()}
        return {"apply_to": apply_to, "curves": curves, "channel": self._channel}

    def connect_response(self, callback) -> None:
        self._callback = callback

    def _current_points(self) -> list[tuple[float, float]]:
        return self._curves[self._channel]

    def _set_current_points(self, points: list[tuple[float, float]]) -> None:
        self._curves[self._channel] = self._normalize(points)

    def _on_channel_changed(self, *_a) -> None:
        i = int(self._channel_dd.get_selected())
        self._channel = self._channels[max(0, min(i, len(self._channels) - 1))]
        self._drag_index = None
        self._selected = None
        self._area.queue_draw()
        self._schedule_preview()

    def _on_reset_channel(self, *_a) -> None:
        self._curves[self._channel] = [(0.0, 0.0), (1.0, 1.0)]
        self._selected = None
        self._area.queue_draw()
        self._schedule_preview()

    def _on_reset_all(self, *_a) -> None:
        for ch in self._channels:
            self._curves[ch] = [(0.0, 0.0), (1.0, 1.0)]
        self._selected = None
        self._area.queue_draw()
        self._schedule_preview()

    def _plot_rect(self, width: float, height: float) -> tuple[float, float, float, float]:
        pad = _CURVE_EDITOR_PAD
        return pad, pad, max(1.0, width - 2 * pad), max(1.0, height - 2 * pad)

    def _event_to_draw(self, x: float, y: float) -> tuple[float, float]:
        """Map widget event coords onto the draw_func pixel space (HiDPI-safe)."""
        aw = float(max(self._area.get_width(), 1))
        ah = float(max(self._area.get_height(), 1))
        return x * (self._draw_w / aw), y * (self._draw_h / ah)

    def _norm_to_px(self, x: float, y: float, width: float, height: float) -> tuple[float, float]:
        ox, oy, w, h = self._plot_rect(width, height)
        return ox + x * w, oy + (1.0 - y) * h

    def _px_to_norm(self, px: float, py: float, width: float, height: float) -> tuple[float, float]:
        ox, oy, w, h = self._plot_rect(width, height)
        x = (px - ox) / w
        y = 1.0 - (py - oy) / h
        return max(0.0, min(1.0, x)), max(0.0, min(1.0, y))

    def _hit_test(self, px: float, py: float, width: float, height: float) -> Optional[int]:
        pts = self._current_points()
        best: Optional[int] = None
        best_d = _CURVE_HIT_RADIUS * _CURVE_HIT_RADIUS
        for i, (x, y) in enumerate(pts):
            sx, sy = self._norm_to_px(x, y, width, height)
            d = (sx - px) * (sx - px) + (sy - py) * (sy - py)
            if d <= best_d:
                best_d = d
                best = i
        return best

    def _insert_point_at(self, px: float, py: float) -> Optional[int]:
        """Insert a control point at draw-space (px, py). Returns its index."""
        pts = list(self._current_points())
        if len(pts) >= _CURVE_MAX_POINTS:
            return None
        nx, ny = self._px_to_norm(px, py, self._draw_w, self._draw_h)
        nx = max(0.02, min(0.98, nx))
        pts.append((nx, ny))
        self._set_current_points(pts)
        normed = self._current_points()
        idx = min(range(len(normed)), key=lambda i: abs(normed[i][0] - nx))
        if idx in (0, len(normed) - 1) and len(normed) > 2:
            idx = min(range(1, len(normed) - 1), key=lambda i: abs(normed[i][0] - nx))
        return idx

    def _move_point(self, index: int, px: float, py: float) -> None:
        nx, ny = self._px_to_norm(px, py, self._draw_w, self._draw_h)
        pts = list(self._current_points())
        if index <= 0:
            pts[0] = (0.0, ny)
        elif index >= len(pts) - 1:
            pts[-1] = (1.0, ny)
        else:
            lo = pts[index - 1][0] + 0.01
            hi = pts[index + 1][0] - 0.01
            if lo >= hi:
                nx = pts[index][0]
            else:
                nx = max(lo, min(hi, nx))
            pts[index] = (nx, ny)
        self._curves[self._channel] = pts

    def _draw_curve(self, _area: Gtk.DrawingArea, cr, width: int, height: int) -> None:
        self._draw_w = float(max(width, 1))
        self._draw_h = float(max(height, 1))

        cr.set_source_rgb(0.14, 0.14, 0.16)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        ox, oy, w, h = self._plot_rect(self._draw_w, self._draw_h)
        cr.set_source_rgb(0.20, 0.20, 0.23)
        cr.rectangle(ox, oy, w, h)
        cr.fill()

        cr.set_source_rgba(1, 1, 1, 0.12)
        cr.set_line_width(1)
        for i in range(5):
            t = i / 4.0
            x = ox + t * w
            y = oy + t * h
            cr.move_to(x, oy)
            cr.line_to(x, oy + h)
            cr.move_to(ox, y)
            cr.line_to(ox + w, y)
        cr.stroke()

        cr.set_source_rgba(1, 1, 1, 0.25)
        cr.set_dash([4, 4])
        cr.move_to(ox, oy + h)
        cr.line_to(ox + w, oy)
        cr.stroke()
        cr.set_dash([])

        pts = self._normalize(self._current_points())
        lut = self._curve_lut(pts, max(64, int(w)))
        color = _CURVE_CHANNEL_COLORS.get(self._channel, (0.9, 0.9, 0.9))
        cr.set_source_rgb(*color)
        cr.set_line_width(2.0)
        for i, y01 in enumerate(lut):
            x01 = i / max(1, len(lut) - 1)
            px, py = self._norm_to_px(x01, float(y01), self._draw_w, self._draw_h)
            if i == 0:
                cr.move_to(px, py)
            else:
                cr.line_to(px, py)
        cr.stroke()

        for i, (x, y) in enumerate(pts):
            px, py = self._norm_to_px(x, y, self._draw_w, self._draw_h)
            r = 5.5 if i == self._selected or i == self._drag_index else 4.5
            cr.set_source_rgb(0.08, 0.08, 0.1)
            cr.arc(px, py, r + 1.5, 0, 6.2832)
            cr.fill()
            cr.set_source_rgb(*color)
            cr.arc(px, py, r, 0, 6.2832)
            cr.fill()

        cr.set_source_rgba(1, 1, 1, 0.35)
        cr.set_line_width(1)
        cr.rectangle(ox + 0.5, oy + 0.5, w - 1, h - 1)
        cr.stroke()

    def _on_click_pressed(self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float) -> None:
        dx, dy = self._event_to_draw(x, y)
        hit = self._hit_test(dx, dy, self._draw_w, self._draw_h)

        if n_press >= 2 and hit is not None:
            pts = list(self._current_points())
            if hit not in (0, len(pts) - 1):
                del pts[hit]
                self._set_current_points(pts)
                self._selected = None
                self._drag_index = None
                self._area.queue_draw()
                self._schedule_preview()
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            return

        if hit is not None:
            self._selected = hit
            self._drag_index = hit
            self._area.queue_draw()
            return

        idx = self._insert_point_at(dx, dy)
        if idx is None:
            return
        self._selected = idx
        self._drag_index = idx
        self._area.queue_draw()
        self._schedule_preview()

    def _on_drag_begin(self, gesture: Gtk.GestureDrag, x: float, y: float) -> None:
        dx, dy = self._event_to_draw(x, y)
        # Prefer the point chosen/created on press so the drag threshold
        # does not leave a brand-new point stranded on the identity line.
        if self._drag_index is None:
            hit = self._hit_test(dx, dy, self._draw_w, self._draw_h)
            if hit is None:
                hit = self._insert_point_at(dx, dy)
                if hit is not None:
                    self._area.queue_draw()
                    self._schedule_preview()
            self._drag_index = hit
        if self._drag_index is None:
            return
        self._selected = self._drag_index
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._area.queue_draw()

    def _on_drag_update(self, gesture: Gtk.GestureDrag, offset_x: float, offset_y: float) -> None:
        if self._drag_index is None:
            return
        ok, x0, y0 = gesture.get_start_point()
        if not ok:
            return
        dx0, dy0 = self._event_to_draw(x0, y0)
        # Offsets are in widget space; scale like the start point.
        aw = float(max(self._area.get_width(), 1))
        ah = float(max(self._area.get_height(), 1))
        dx = dx0 + offset_x * (self._draw_w / aw)
        dy = dy0 + offset_y * (self._draw_h / ah)
        self._move_point(self._drag_index, dx, dy)
        self._area.queue_draw()
        self._schedule_preview()

    def _on_drag_end(self, *_a) -> None:
        if self._drag_index is not None:
            self._set_current_points(self._current_points())
            self._drag_index = None
            self._area.queue_draw()
            self._schedule_preview()

    def _schedule_preview(self, *_a) -> None:
        if self._closed:
            return
        if self._preview_source is not None:
            GLib.source_remove(self._preview_source)
            self._preview_source = None
        if self._debounce_ms <= 0:
            self._fire_preview()
            return
        self._preview_source = GLib.timeout_add(self._debounce_ms, self._fire_preview)

    def _fire_preview(self) -> bool:
        self._preview_source = None
        if self._closed:
            return False
        try:
            self._on_preview(self.params())
        except Exception:
            import traceback

            traceback.print_exc()
        return False

    def _cancel_pending(self) -> None:
        if self._preview_source is not None:
            GLib.source_remove(self._preview_source)
            self._preview_source = None

    def _emit(self, response: Gtk.ResponseType) -> None:
        if self._closed:
            return
        self._closed = True
        self._cancel_pending()
        if self._callback:
            self._callback(self, response)
        self.destroy()

    def _on_close(self, *_a) -> bool:
        self._emit(Gtk.ResponseType.CANCEL)
        return True
