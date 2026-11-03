"""Rig tab (GreenInkobold): the open character's joints, overlay toggles and parts check."""

from __future__ import annotations

from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

import math

from inkobold.core import character as ch
from inkobold.core import goblin_rig as gr
from inkobold.core.document import Document
from inkobold.core.libraries import open_in_file_manager
from inkobold.ui.dialogs import configure_numeric_spin

# (label, key, axis) rows below the joints: axis "xy" = two spins, "y" / "v" = one.
_EXTRA_ROWS = (
    ("Hat crown", ch.HAT_CROWN, "xy"),
    ("Hat sit", "hat_sit", "v"),
    ("Hat scale", "hat_scale", "v"),
    ("Art top", ch.ART_TOP, "y"),
    ("Foot sole", ch.FOOT_SOLE, "y"),
)


class RigPanel(Gtk.Box):
    """Edits ``document.character``; every change goes through ``push_history`` first."""

    def __init__(
        self,
        get_document: Callable[[], Optional[Document]],
        view: ch.RigView,
        push_history: Callable[[], None],
        on_changed: Callable[[], None],
        on_new: Callable[[], None],
        on_open: Callable[[], None],
        on_save: Callable[[], None],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["toolbox"])
        self._get_document = get_document
        self._view = view
        self._push_history = push_history
        self._on_changed = on_changed
        self._syncing = False
        self._spins: dict[tuple[str, str], Gtk.SpinButton] = {}

        self._stack = Gtk.Stack(vexpand=True)
        self.append(self._stack)

        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        empty.append(Gtk.Label(
            label="No character on this canvas.\n\nOpen a character folder (part PNGs + rig.json) "
            "from the Assets tab, or make a new one.",
            xalign=0,
            wrap=True,
        ))
        new_btn = Gtk.Button(label="New Character…")
        new_btn.connect("clicked", lambda *_: on_new())
        open_btn = Gtk.Button(label="Open Character…")
        open_btn.connect("clicked", lambda *_: on_open())
        empty.append(new_btn)
        empty.append(open_btn)
        self._stack.add_named(empty, "empty")

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._name = Gtk.Label(xalign=0, css_classes=["heading"], ellipsize=Pango.EllipsizeMode.END)
        page.append(self._name)
        self._folder = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.START)
        self._folder.add_css_class("dim-label")
        page.append(self._folder)
        buttons = Gtk.Box(spacing=4, homogeneous=True)
        save = Gtk.Button(label="Save")
        save.set_tooltip_text("Write the changed parts and rig.json into the character folder")
        save.connect("clicked", lambda *_: on_save())
        reveal = Gtk.Button(label="Show in Files")
        reveal.connect("clicked", lambda *_: self._reveal())
        buttons.append(save)
        buttons.append(reveal)
        page.append(buttons)

        self._show = Gtk.CheckButton(label="Show joints")
        self._show.set_tooltip_text("Bones, joints, hat anchor and the art-top / foot-sole lines")
        self._show.connect("toggled", self._on_show_toggled)
        page.append(self._show)
        self._edit = Gtk.CheckButton(label="Drag joints without Alt")
        self._edit.set_tooltip_text(
            "Off: Alt-drag a joint (painting near joints stays safe). "
            "On: plain drag on a handle moves it. Right-side joints mirror the left ones."
        )
        self._edit.connect("toggled", self._on_edit_toggled)
        page.append(self._edit)
        self._preview = Gtk.CheckButton(label="Live preview")
        self._preview.set_tooltip_text("The game's procedural animation on these parts, beside the canvas")
        self._preview.connect("toggled", self._on_preview_toggled)
        page.append(self._preview)

        grid = Gtk.Grid(column_spacing=6, row_spacing=4)
        grid.attach(Gtk.Label(label="Joint", xalign=0, css_classes=["dim-label"]), 0, 0, 1, 1)
        grid.attach(Gtk.Label(label="X", css_classes=["dim-label"]), 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="Y", css_classes=["dim-label"]), 2, 0, 1, 1)
        row = 1
        for key in ch.JOINTS:
            label = ch.JOINT_LABELS[key] + (" L" if key in ch.MIRRORED else "")
            self._add_row(grid, row, label, key, "xy")
            row += 1
        for label, key, axis in _EXTRA_ROWS:
            self._add_row(grid, row, label, key, axis)
            row += 1
        page.append(grid)

        self._parts = Gtk.Label(xalign=0, wrap=True)
        page.append(self._parts)
        reset = Gtk.Button(label="Reset Joints to Goblin")
        reset.set_tooltip_text("Put every joint, the hat anchor and the lines back to the player goblin's")
        reset.connect("clicked", self._on_reset)
        page.append(reset)

        self._tuning = TuningSection(self._rig, push_history, self._changed)
        page.append(self._tuning)

        scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(page)
        self._stack.add_named(scroll, "rig")
        self.sync()

    # ---- widgets ----

    def _add_row(self, grid: Gtk.Grid, row: int, label: str, key: str, axis: str) -> None:
        grid.attach(Gtk.Label(label=label, xalign=0, hexpand=True), 0, row, 1, 1)
        cols = {"xy": ("x", "y"), "y": ("y",), "v": ("v",)}[axis]
        for col, which in enumerate(cols, start=1):
            if axis == "v" and key == "hat_scale":
                spin = Gtk.SpinButton.new_with_range(0.01, 100.0, 0.1)
                configure_numeric_spin(spin)
            else:
                spin = Gtk.SpinButton.new_with_range(-4096, 8192, 1)
                configure_numeric_spin(spin, digits=0)
            spin.set_width_chars(5)
            spin.connect("value-changed", self._on_spin, key, which)
            grid.attach(spin, 2 if which == "y" and axis == "y" else col, row, 1, 1)
            self._spins[(key, which)] = spin

    def _rig(self) -> Optional[ch.CharacterRig]:
        doc = self._get_document()
        return getattr(doc, "character", None) if doc is not None else None

    def sync(self) -> None:
        """Show the active document's character (call after any change from elsewhere)."""
        rig = self._rig()
        self._stack.set_visible_child_name("rig" if rig is not None else "empty")
        if rig is None:
            return
        self._syncing = True
        try:
            self._name.set_text(rig.name)
            folder = str(rig.folder) if rig.folder is not None else "not saved yet"
            self._folder.set_text(folder)
            self._folder.set_tooltip_text(folder)
            self._show.set_active(self._view.show)
            self._edit.set_active(self._view.edit)
            self._preview.set_active(self._view.preview)
            for (key, which), spin in self._spins.items():
                if key == "hat_sit":
                    value = rig.hat_sit
                elif key == "hat_scale":
                    value = rig.hat_scale
                else:
                    x, y = rig.point(key)
                    value = x if which == "x" else y
                if abs(spin.get_value() - value) > 1e-6:
                    spin.set_value(value)
            self._parts.set_text(self._parts_text())
        finally:
            self._syncing = False
        self._tuning.sync()

    def _parts_text(self) -> str:
        doc = self._get_document()
        if doc is None:
            return ""
        try:
            _found, missing, extra = ch.part_layers(doc)
        except ch.CharacterError as exc:
            return f"⚠ {exc}"
        lines = []
        if missing:
            lines.append("⚠ Missing part layers: " + ", ".join(missing))
        else:
            lines.append("All eight part layers present.")
        if extra:
            lines.append("Not saved (not a part): " + ", ".join(extra))
        return "\n".join(lines)

    # ---- edits ----

    def _on_spin(self, spin: Gtk.SpinButton, key: str, which: str) -> None:
        rig = self._rig()
        if self._syncing or rig is None:
            return
        value = float(spin.get_value())
        self._push_history()
        if key == "hat_sit":
            rig.hat_sit = value
        elif key == "hat_scale":
            rig.hat_scale = value
        else:
            x, y = rig.point(key)
            rig.move_handle(key, value if which == "x" else x, value if which != "x" else y)
        self._changed()

    def _on_reset(self, *_a) -> None:
        rig = self._rig()
        if rig is None:
            return
        self._push_history()
        goblin = ch.CharacterRig()
        rig.joints = dict(goblin.joints)
        rig.hat_crown, rig.hat_sit, rig.hat_scale = goblin.hat_crown, goblin.hat_sit, goblin.hat_scale
        rig.art_top_y, rig.foot_sole_y = goblin.art_top_y, goblin.foot_sole_y
        self._changed()

    def _changed(self) -> None:
        doc = self._get_document()
        if doc is not None:
            doc.dirty = True
        self._on_changed()
        self.sync()

    def _on_show_toggled(self, btn: Gtk.CheckButton) -> None:
        if self._syncing:
            return
        self._view.show = btn.get_active()
        self._on_changed()

    def _on_edit_toggled(self, btn: Gtk.CheckButton) -> None:
        if self._syncing:
            return
        self._view.edit = btn.get_active()
        if self._view.edit and not self._view.show:
            self._view.show = True
            self.sync()
        self._on_changed()

    def _on_preview_toggled(self, btn: Gtk.CheckButton) -> None:
        if self._syncing:
            return
        self._view.preview = btn.get_active()
        self._on_changed()

    def _reveal(self) -> None:
        rig = self._rig()
        if rig is not None and rig.folder is not None:
            open_in_file_manager(rig.folder)


def _humanize(name: str) -> str:
    text = name.replace("_deg", " (°)").replace("_hz", " (Hz)").replace("_px", " (px)")
    text = text.replace("_sec", " (s)").replace("_", " ")
    return text[:1].upper() + text[1:]


def _spin_for(default) -> Gtk.SpinButton:
    if isinstance(default, int):
        spin = Gtk.SpinButton.new_with_range(-100000, 100000, 1)
        configure_numeric_spin(spin, digits=0)
        return spin
    mag = abs(float(default))
    step = 10.0 ** (math.floor(math.log10(mag)) - 1) if mag > 0 else 0.01
    step = min(max(step, 0.001), 10.0)
    digits = max(0, min(4, -math.floor(math.log10(step))))
    spin = Gtk.SpinButton.new_with_range(-100000.0, 100000.0, step)
    configure_numeric_spin(spin, digits=max(digits, 2))
    return spin


class TuningSection(Gtk.Expander):
    """The rig's @export tuning, per character: changes land in rig.json ``params``."""

    def __init__(
        self,
        get_rig: Callable[[], Optional[ch.CharacterRig]],
        push_history: Callable[[], None],
        on_changed: Callable[[], None],
    ) -> None:
        super().__init__(label="Tuning", expanded=False)
        self.set_tooltip_text(
            "The game rig's tuning (player_rig.gd). Values you change are saved in this "
            "character's rig.json and override the game's defaults; bold = overridden."
        )
        self._get_rig = get_rig
        self._push_history = push_history
        self._on_changed = on_changed
        self._syncing = False
        self._tuning: Optional[gr.Tuning] = None
        self._rows: dict[str, tuple[Gtk.Widget, Gtk.Label, Gtk.Button, Gtk.Widget]] = {}
        self._groups: list[tuple[Gtk.Expander, list[str]]] = []
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.search = Gtk.SearchEntry(placeholder_text="Filter tuning")
        self.search.connect("search-changed", lambda *_: self._filter())
        body.append(self.search)
        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        body.append(self._list)
        reset_all = Gtk.Button(label="Use Game Tuning")
        reset_all.set_tooltip_text("Drop every override in this character's rig.json")
        reset_all.connect("clicked", self._on_reset_all)
        body.append(reset_all)
        self.set_child(body)

    def _build(self, tuning: gr.Tuning) -> None:
        while (child := self._list.get_first_child()) is not None:
            self._list.remove(child)
        self._rows.clear()
        self._groups.clear()
        for group, names in tuning.groups:
            shown = [
                n for n in names
                if n not in gr.HOST_PARAMS and isinstance(tuning.params.get(n), (int, float))
            ]
            if not shown:
                continue
            exp = Gtk.Expander(label=group, expanded=False)
            grid = Gtk.Grid(column_spacing=6, row_spacing=2, margin_start=8)
            for row, name in enumerate(shown):
                default = tuning.params[name]
                label = Gtk.Label(label=_humanize(name), xalign=0, hexpand=True, ellipsize=3)
                tip = tuning.docs.get(name, "")
                label.set_tooltip_text(f"{name}\n{tip}\nGame default: {default}".replace("\n\n", "\n"))
                if isinstance(default, bool):
                    widget: Gtk.Widget = Gtk.CheckButton()
                    widget.connect("toggled", self._on_value, name)
                else:
                    widget = _spin_for(default)
                    widget.set_width_chars(6)
                    widget.connect("value-changed", self._on_value, name)
                reset = Gtk.Button(icon_name="edit-undo-symbolic")
                reset.add_css_class("flat")
                reset.set_tooltip_text(f"Back to the game's {default}")
                reset.connect("clicked", self._on_reset_one, name)
                grid.attach(label, 0, row, 1, 1)
                grid.attach(widget, 1, row, 1, 1)
                grid.attach(reset, 2, row, 1, 1)
                self._rows[name] = (widget, label, reset, grid)
            exp.set_child(grid)
            self._list.append(exp)
            self._groups.append((exp, shown))
        self._tuning = tuning
        self._filter()

    def sync(self) -> None:
        rig = self._get_rig()
        if rig is None:
            return
        tuning = gr.project_tuning(rig.folder)
        if tuning is not self._tuning:
            self._build(tuning)
        self._syncing = True
        try:
            for name, (widget, label, reset, _grid) in self._rows.items():
                default = tuning.params[name]
                overridden = name in rig.params
                value = rig.params.get(name, default)
                if isinstance(widget, Gtk.CheckButton):
                    widget.set_active(bool(value))
                elif isinstance(value, (int, float)) and abs(widget.get_value() - float(value)) > 1e-9:
                    widget.set_value(float(value))
                text = _humanize(name)
                label.set_markup(f"<b>{text}</b>" if overridden else text)
                reset.set_sensitive(overridden)
        finally:
            self._syncing = False

    def _filter(self) -> None:
        needle = self.search.get_text().strip().lower().replace(" ", "_")
        for exp, names in self._groups:
            any_shown = False
            for name in names:
                widget, label, reset, _grid = self._rows[name]
                hit = not needle or needle in name or needle in label.get_text().lower()
                for w in (widget, label, reset):
                    w.set_visible(hit)
                any_shown = any_shown or hit
            exp.set_visible(any_shown)
            if needle:
                exp.set_expanded(any_shown)

    def _set(self, name: str, value) -> None:
        rig = self._get_rig()
        if rig is None or self._tuning is None:
            return
        default = self._tuning.params[name]
        current = rig.params.get(name, default)
        if value == current:
            return
        self._push_history()
        params = dict(rig.params)
        if value == default:
            params.pop(name, None)
        else:
            params[name] = value
        rig.params = params
        self._on_changed()

    def _on_value(self, widget: Gtk.Widget, name: str) -> None:
        if self._syncing or self._tuning is None:
            return
        default = self._tuning.params[name]
        if isinstance(widget, Gtk.CheckButton):
            value = widget.get_active()
        elif isinstance(default, int):
            value = int(round(widget.get_value()))
        else:
            value = round(float(widget.get_value()), 6)
        self._set(name, value)

    def _on_reset_one(self, _btn: Gtk.Button, name: str) -> None:
        if self._tuning is not None:
            self._set(name, self._tuning.params[name])

    def _on_reset_all(self, *_a) -> None:
        rig = self._get_rig()
        if rig is None or not rig.params:
            return
        self._push_history()
        rig.params = {}
        self._on_changed()
