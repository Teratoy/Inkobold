"""Side-panel palette strip: pick the active Libraries → Palettes file and use its swatches.

Click a swatch for the primary color, right-click for the secondary; Ctrl+click
(or middle-click) renames / removes it; drag swatches to reorder. Every edit is
written straight back to the palette's ``.gpl`` file.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

if TYPE_CHECKING:
    import numpy as np

from inkobold.core import libraries
from inkobold.core.palette import (
    IMPORT_SUFFIXES,
    Palette,
    PaletteError,
    Rgb,
    Swatch,
    import_palette,
    load_palette,
    palette_from_pixels,
    save_palette,
    unique_palette_path,
)

SWATCH_PX = 18
# A palette's Columns value is honored up to this many swatches per row
MAX_FIXED_COLUMNS = 16


class PalettePanel(Gtk.Box):
    def __init__(
        self,
        *,
        active_name: str,
        get_primary: Callable[[], Rgb],
        on_pick: Callable[[Rgb, bool], None],
        on_active_changed: Callable[[str], None],
        on_colors_changed: Callable[[list[Rgb]], None],
        on_error: Callable[[str, str], None],
        get_layer: Callable[[], Optional[tuple[str, "np.ndarray"]]] = lambda: None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        self._get_primary = get_primary
        self._on_pick = on_pick
        self._on_active_changed = on_active_changed
        self._on_colors_changed = on_colors_changed
        self._on_error = on_error
        self._get_layer = get_layer
        self._paths: list[Path] = []
        self._path: Optional[Path] = None
        self._palette: Optional[Palette] = None
        self._syncing = False
        self._drag_from: Optional[int] = None

        head = Gtk.Box(spacing=4)
        self.append(head)
        head.append(Gtk.Label(label="Palette", xalign=0))
        self._dropdown = Gtk.DropDown(model=Gtk.StringList.new([]))
        self._dropdown.set_hexpand(True)
        self._dropdown.set_tooltip_text("Active palette (Libraries → Palettes)")
        self._dropdown.connect("notify::selected", self._on_selected)
        head.append(self._dropdown)
        add = Gtk.Button(label="+")
        add.set_tooltip_text("Add the primary color to this palette")
        add.connect("clicked", self._on_add_clicked)
        head.append(add)
        self._add_btn = add
        menu = Gtk.MenuButton()
        menu.set_child(Gtk.Label(label="⋯"))
        menu.set_tooltip_text("New, rename, delete or import palettes")
        menu.set_popover(self._build_menu())
        head.append(menu)

        self._flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=True,
            column_spacing=2,
            row_spacing=2,
            min_children_per_line=6,
            max_children_per_line=24,
        )
        self.append(self._flow)
        self._empty = Gtk.Label(xalign=0, wrap=True)
        self._empty.add_css_class("dim-label")
        self.append(self._empty)

        self.reload(select_name=active_name)

    # ----- palette files ---------------------------------------------------

    def colors(self) -> list[Rgb]:
        return [s.rgb for s in self._palette.swatches] if self._palette else []

    def reload(self, *, select_name: Optional[str] = None) -> None:
        """Re-scan the Palettes folder and reload the active file."""
        if select_name is None and self._path is not None:
            select_name = self._path.name
        self._paths = libraries.list_palettes()
        names = [p.stem for p in self._paths]
        index = next((i for i, p in enumerate(self._paths) if p.name == select_name), 0)
        self._syncing = True
        try:
            self._dropdown.set_model(Gtk.StringList.new(names or ["(no palettes)"]))
            self._dropdown.set_sensitive(bool(names))
            self._dropdown.set_selected(index if names else 0)
        finally:
            self._syncing = False
        self._open(self._paths[index] if self._paths else None)

    def _open(self, path: Optional[Path]) -> None:
        self._path = path
        self._palette = None
        if path is not None:
            try:
                self._palette = load_palette(path)
            except PaletteError as exc:
                self._on_error(f"Could not read palette {path.name}", str(exc))
        self._add_btn.set_sensitive(self._palette is not None)
        self._on_active_changed(path.name if path is not None else "")
        self._rebuild()

    def _on_selected(self, dropdown: Gtk.DropDown, *_a) -> None:
        if self._syncing:
            return
        i = dropdown.get_selected()
        if 0 <= i < len(self._paths) and self._paths[i] != self._path:
            self._open(self._paths[i])

    def _save(self) -> None:
        if self._palette is None or self._path is None:
            return
        try:
            save_palette(self._palette, self._path)
        except OSError as exc:
            self._on_error(f"Could not save palette {self._path.name}", str(exc))
        self._rebuild()

    # ----- swatch grid -----------------------------------------------------

    def _rebuild(self) -> None:
        child = self._flow.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._flow.remove(child)
            child = nxt
        swatches = self._palette.swatches if self._palette else []
        columns = self._palette.columns if self._palette else 0
        fixed = 0 < columns <= MAX_FIXED_COLUMNS
        self._flow.set_min_children_per_line(columns if fixed else 6)
        self._flow.set_max_children_per_line(columns if fixed else 24)
        for i, swatch in enumerate(swatches):
            self._flow.append(self._swatch_widget(i, swatch.rgb, swatch.name, stretch=fixed))
        if self._palette is None:
            self._empty.set_label("No palette — use ⋯ → New Palette or drop .gpl files in Libraries → Palettes.")
        elif not swatches:
            self._empty.set_label("Empty palette — + adds the primary color.")
        self._empty.set_visible(not swatches)
        self._flow.set_visible(bool(swatches))
        self._on_colors_changed(self.colors())

    def _swatch_widget(self, index: int, rgb: Rgb, name: str, *, stretch: bool) -> Gtk.Widget:
        area = Gtk.DrawingArea()
        # Fixed column count: narrow minimum so the row fits, then stretch to fill
        area.set_content_width(SWATCH_PX // 2 if stretch else SWATCH_PX)
        area.set_hexpand(stretch)
        area.set_content_height(SWATCH_PX)
        r, g, b = (c / 255.0 for c in rgb)

        def draw(_a, cr, w, h) -> None:
            cr.set_source_rgb(r, g, b)
            cr.rectangle(0, 0, w, h)
            cr.fill()
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.6)
            cr.set_line_width(1)
            cr.rectangle(0.5, 0.5, w - 1, h - 1)
            cr.stroke()

        area.set_draw_func(draw)
        hex_s = "#{:02x}{:02x}{:02x}".format(*rgb)
        area.set_tooltip_text(
            f"{name + '  ' if name else ''}{hex_s}\n"
            "Click: primary · Right-click: secondary · Ctrl+click: edit · Drag: reorder"
        )
        area.set_cursor(Gdk.Cursor.new_from_name("pointer"))

        click = Gtk.GestureClick(button=0)
        click.connect("released", self._on_swatch_click, index, area)
        area.add_controller(click)

        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)

        def prepare(_src, _x, _y, idx=index):
            self._drag_from = idx
            return Gdk.ContentProvider.new_for_value(GObject.Value(GObject.TYPE_INT64, idx))

        def drag_end(*_a) -> None:
            self._drag_from = None

        source.connect("prepare", prepare)
        source.connect("drag-end", drag_end)
        area.add_controller(source)

        target = Gtk.DropTarget.new(GObject.TYPE_INT64, Gdk.DragAction.MOVE)
        target.connect("drop", self._on_swatch_drop, index)
        area.add_controller(target)
        return area

    def _on_swatch_click(self, gesture: Gtk.GestureClick, _n, _x, _y, index: int, area: Gtk.Widget) -> None:
        if self._palette is None or not (0 <= index < len(self._palette.swatches)):
            return
        button = gesture.get_current_button()
        ctrl = bool(gesture.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK)
        if button == 2 or (button == 1 and ctrl):
            self._edit_swatch(index, area)
        elif button in (1, 3):
            self._on_pick(self._palette.swatches[index].rgb, button == 1)

    def _on_swatch_drop(self, _target, value, _x, _y, dest: int) -> bool:
        src = self._drag_from
        try:
            src = int(value)
        except (TypeError, ValueError):
            pass
        if self._palette is None or src is None or src == dest:
            return False
        self._palette.move(src, dest)
        # Rebuild after the drop finishes; the source widget is still in use
        GLib.idle_add(lambda: (self._save(), False)[1])
        return True

    def _on_add_clicked(self, *_a) -> None:
        if self._palette is None:
            return
        if self._palette.add(self._get_primary()):
            self._save()

    def _edit_swatch(self, index: int, area: Gtk.Widget) -> None:
        swatch = self._palette.swatches[index]
        pop = Gtk.Popover()
        pop.set_parent(area)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        pop.set_child(box)
        entry = Gtk.Entry(text=swatch.name, placeholder_text="Color name")
        box.append(entry)
        row = Gtk.Box(spacing=6, homogeneous=True)
        box.append(row)
        remove = Gtk.Button(label="Remove")
        rename = Gtk.Button(label="Rename")
        rename.add_css_class("suggested-action")
        row.append(remove)
        row.append(rename)

        def do_rename(*_a) -> None:
            swatch.name = entry.get_text().strip()
            pop.popdown()
            self._save()

        def do_remove(*_a) -> None:
            if self._palette is not None and swatch in self._palette.swatches:
                self._palette.swatches.remove(swatch)
            pop.popdown()
            self._save()

        entry.connect("activate", do_rename)
        rename.connect("clicked", do_rename)
        remove.connect("clicked", do_remove)
        pop.connect("closed", lambda p: GLib.idle_add(lambda: (p.unparent(), False)[1]))
        pop.popup()
        entry.grab_focus()

    # ----- palette menu ----------------------------------------------------

    def _build_menu(self) -> Gtk.Popover:
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                      margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        pop.set_child(box)
        self._menu_stack = Gtk.Stack()
        box.append(self._menu_stack)

        items = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._menu_stack.add_named(items, "items")
        # Items that act on the open palette; greyed out when there is none
        needs_palette: list[Gtk.Button] = []
        for label, cb in (
            ("New Palette…", lambda: self._ask_name("new", "New palette name", "New Palette")),
            ("From Selected Layer…", self._ask_from_layer),
            ("Duplicate…", lambda: self._ask_name("duplicate", "Copy name", self._copy_name())),
            ("Rename…", lambda: self._ask_name("rename", "Palette name", self._palette.name if self._palette else "")),
            ("Delete…", self._ask_delete),
            ("Import…", self._import),
            ("Reload", self.reload),
            ("Open Folder", lambda: libraries.open_in_file_manager(libraries.category_dir("palettes"))),
        ):
            btn = Gtk.Button(label=label)
            btn.add_css_class("flat")
            btn.get_child().set_xalign(0)
            btn.connect("clicked", lambda _b, f=cb: f())
            items.append(btn)
            if label in ("Duplicate…", "Rename…", "Delete…"):
                needs_palette.append(btn)
            elif label == "From Selected Layer…":
                from_layer = btn

        ask = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._menu_stack.add_named(ask, "ask")
        self._ask_label = Gtk.Label(xalign=0, wrap=True)
        ask.append(self._ask_label)
        self._ask_entry = Gtk.Entry()
        self._ask_entry.connect("activate", lambda *_: self._ask_confirm())
        ask.append(self._ask_entry)
        row = Gtk.Box(spacing=6, homogeneous=True)
        ask.append(row)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: pop.popdown())
        self._ask_ok = Gtk.Button(label="OK")
        self._ask_ok.add_css_class("suggested-action")
        self._ask_ok.connect("clicked", lambda *_: self._ask_confirm())
        row.append(cancel)
        row.append(self._ask_ok)
        self._ask_mode = ""

        def on_show(*_a) -> None:
            self._menu_stack.set_visible_child_name("items")
            for btn in needs_palette:
                btn.set_sensitive(self._palette is not None)
            from_layer.set_sensitive(self._get_layer() is not None)

        pop.connect("show", on_show)
        self._menu = pop
        return pop

    def _copy_name(self) -> str:
        return f"{self._palette.name} copy" if self._palette else ""

    def _ask_name(self, mode: str, prompt: str, initial: str) -> None:
        self._ask_mode = mode
        self._ask_label.set_label(prompt)
        self._ask_entry.set_visible(True)
        self._ask_entry.set_text(initial)
        self._ask_ok.set_label("Rename" if mode == "rename" else "Create")
        self._ask_ok.remove_css_class("destructive-action")
        self._ask_ok.add_css_class("suggested-action")
        self._menu_stack.set_visible_child_name("ask")
        self._ask_entry.grab_focus()
        self._ask_entry.select_region(0, -1)

    def _ask_from_layer(self) -> None:
        layer = self._get_layer()
        if layer is not None:
            self._ask_name("layer", "Palette name (colors of the selected layer)", layer[0])

    def _ask_delete(self) -> None:
        if self._path is None:
            return
        self._ask_mode = "delete"
        self._ask_label.set_label(f"Delete palette “{self._path.stem}”? The file is moved to the trash.")
        self._ask_entry.set_visible(False)
        self._ask_ok.set_label("Delete")
        self._ask_ok.remove_css_class("suggested-action")
        self._ask_ok.add_css_class("destructive-action")
        self._menu_stack.set_visible_child_name("ask")

    def _ask_confirm(self) -> None:
        mode = self._ask_mode
        name = self._ask_entry.get_text().strip()
        self._menu.popdown()
        folder = libraries.category_dir("palettes")
        try:
            if mode == "new" and name:
                path = unique_palette_path(folder, name)
                save_palette(Palette(name), path)
                self.reload(select_name=path.name)
            elif mode == "duplicate" and name and self._palette is not None:
                path = unique_palette_path(folder, name)
                copy = Palette(name, [Swatch(s.rgb, s.name) for s in self._palette.swatches], self._palette.columns)
                save_palette(copy, path)
                self.reload(select_name=path.name)
            elif mode == "layer" and name:
                layer = self._get_layer()
                if layer is None:
                    return
                pal = palette_from_pixels(layer[1], name=name)
                path = unique_palette_path(folder, name)
                save_palette(pal, path)
                self.reload(select_name=path.name)
            elif mode == "rename" and name and self._palette is not None and self._path is not None:
                path = unique_palette_path(folder, name, ignore=self._path)
                self._palette.name = name
                save_palette(self._palette, path)
                if path != self._path:
                    self._path.unlink(missing_ok=True)
                self.reload(select_name=path.name)
            elif mode == "delete" and self._path is not None:
                self._trash(self._path)
                self._path = None
                self.reload(select_name="")
        except (PaletteError, OSError) as exc:
            self._on_error("Palette operation failed", str(exc))

    @staticmethod
    def _trash(path: Path) -> None:
        try:
            Gio.File.new_for_path(str(path)).trash(None)
        except GLib.Error:
            # No trash on this volume: keep a backup next to the palettes instead
            path.replace(path.with_name(path.name + ".deleted"))

    def _import(self) -> None:
        self._menu.popdown()
        dialog = Gtk.FileDialog(title="Import Palette")
        filt = Gtk.FileFilter()
        filt.set_name("Palettes (.gpl, .hex, images)")
        for suffix in sorted(IMPORT_SUFFIXES):
            filt.add_pattern(f"*{suffix}")
            filt.add_pattern(f"*{suffix.upper()}")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filt)
        dialog.set_filters(filters)
        dialog.set_default_filter(filt)
        root = self.get_root()
        dialog.open(root if isinstance(root, Gtk.Window) else None, None, self._on_import_done)

    def _on_import_done(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        path = gfile.get_path() if gfile is not None else None
        if not path:
            return
        try:
            dest = import_palette(Path(path), libraries.category_dir("palettes"))
        except (PaletteError, OSError) as exc:
            self._on_error(f"Could not import {Path(path).name}", str(exc))
            return
        self.reload(select_name=dest.name)
