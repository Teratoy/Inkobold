"""Assets tab (GreenInkobold): a live file tree of the project's visual-asset folder."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

from inkobold.core.assets import (  # noqa: E402
    IMAGE_SUFFIXES,
    OPENABLE_SUFFIXES,
    godot_res_path,
    is_listed,
    sort_key,
)
from inkobold.core.libraries import open_in_file_manager  # noqa: E402

_ATTRS = ",".join((
    "standard::name",
    "standard::display-name",
    "standard::type",
    "standard::symbolic-icon",
    "time::modified",
))
THUMB_PX = 24
PREVIEW_PX = 180


def _info_path(info: Gio.FileInfo) -> Optional[Path]:
    gfile = info.get_attribute_object("standard::file")
    raw = gfile.get_path() if gfile is not None else None
    return Path(raw) if raw else None


def _is_dir(info: Gio.FileInfo) -> bool:
    return info.get_file_type() == Gio.FileType.DIRECTORY


class AssetBrowser(Gtk.Box):
    """Folder tree with thumbnails, a preview, and Open / Import / res:// actions.

    The tree is backed by monitored Gtk.DirectoryLists, so files exported into
    the asset folder show up without a manual refresh.
    """

    def __init__(
        self,
        root: Optional[Path],
        on_open: Callable[[Path], None],
        on_import: Callable[[Path], None],
        on_root_changed: Callable[[Path], None],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["toolbox"])
        self._root = root
        self._on_open = on_open
        self._on_import = on_import
        self._on_root_changed = on_root_changed
        self._thumbs: dict[tuple[str, int], Gdk.Texture] = {}
        self._selected: Optional[Path] = None

        head = Gtk.Box(spacing=4)
        self._root_label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.START)
        head.append(self._root_label)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.set_tooltip_text("Reload the folder tree")
        refresh.connect("clicked", lambda *_: self.set_root(self._root))
        head.append(refresh)
        change = Gtk.Button(label="Change…")
        change.set_tooltip_text("Pick a different visual-asset folder")
        change.connect("clicked", self._on_change_root)
        head.append(change)
        self.append(head)

        self._stack = Gtk.Stack(vexpand=True)
        self._missing = Gtk.Label(wrap=True, xalign=0, valign=Gtk.Align.START)
        self._stack.add_named(self._missing, "missing")

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_item_setup)
        factory.connect("bind", self._on_item_bind)
        self._selection = Gtk.SingleSelection(autoselect=False, can_unselect=True)
        self._selection.connect("notify::selected-item", self._on_selection_changed)
        self._list = Gtk.ListView(model=self._selection, factory=factory)
        self._list.connect("activate", self._on_activate)
        scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(self._list)
        self._stack.add_named(scroll, "tree")
        self.append(self._stack)

        # Gtk.Image keeps a square natural size; a Picture would grow with the column width
        self._preview = Gtk.Image(pixel_size=PREVIEW_PX)
        self.append(self._preview)
        self._info = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self._info.add_css_class("dim-label")
        self.append(self._info)

        row1 = Gtk.Box(spacing=4, homogeneous=True)
        self._open_btn = Gtk.Button(label="Open")
        self._open_btn.set_tooltip_text("Open in a new canvas tab (double-click / Enter)")
        self._open_btn.connect("clicked", lambda *_: self._open_selected())
        self._import_btn = Gtk.Button(label="Import as Layer")
        self._import_btn.set_tooltip_text("Add to the current canvas as a new layer")
        self._import_btn.connect("clicked", lambda *_: self._import_selected())
        row1.append(self._open_btn)
        row1.append(self._import_btn)
        self.append(row1)
        row2 = Gtk.Box(spacing=4, homogeneous=True)
        self._res_btn = Gtk.Button(label="Copy res:// Path")
        self._res_btn.set_tooltip_text("Copy the Godot resource path to the clipboard")
        self._res_btn.connect("clicked", lambda *_: self._copy_res_path())
        reveal = Gtk.Button(label="Show in Files")
        reveal.set_tooltip_text("Open the selected folder in the file manager")
        reveal.connect("clicked", lambda *_: self._reveal())
        row2.append(self._res_btn)
        row2.append(reveal)
        self.append(row2)

        self.set_root(root)

    # ---- model ----

    def _dir_model(self, gfile: Gio.File) -> Gio.ListModel:
        listing = Gtk.DirectoryList.new(_ATTRS, gfile)
        listing.set_monitored(True)
        filt = Gtk.CustomFilter.new(lambda info, *_: is_listed(info.get_name() or ""))
        filtered = Gtk.FilterListModel.new(listing, filt)
        sorter = Gtk.CustomSorter.new(self._compare, None)
        return Gtk.SortListModel.new(filtered, sorter)

    @staticmethod
    def _compare(a: Gio.FileInfo, b: Gio.FileInfo, *_a) -> Gtk.Ordering:
        ka = sort_key(a.get_name() or "", _is_dir(a))
        kb = sort_key(b.get_name() or "", _is_dir(b))
        if ka < kb:
            return Gtk.Ordering.SMALLER
        if ka > kb:
            return Gtk.Ordering.LARGER
        return Gtk.Ordering.EQUAL

    def _children(self, info: Gio.FileInfo, *_a) -> Optional[Gio.ListModel]:
        if not _is_dir(info):
            return None
        gfile = info.get_attribute_object("standard::file")
        return self._dir_model(gfile) if gfile is not None else None

    def root(self) -> Optional[Path]:
        return self._root

    def set_root(self, root: Optional[Path]) -> None:
        self._root = root
        self._thumbs.clear()
        self._show_selection(None)
        if root is None or not root.is_dir():
            self._root_label.set_text("No asset folder")
            self._root_label.set_tooltip_text(None)
            where = f"\n{root}" if root is not None else ""
            self._missing.set_text(f"Asset folder not found.{where}\n\nUse Change… to pick one.")
            self._selection.set_model(None)
            self._stack.set_visible_child_name("missing")
            return
        self._root_label.set_text(root.name)
        self._root_label.set_tooltip_text(str(root))
        tree = Gtk.TreeListModel.new(
            self._dir_model(Gio.File.new_for_path(str(root))), False, False, self._children
        )
        self._selection.set_model(tree)
        self._stack.set_visible_child_name("tree")

    # ---- rows ----

    def _on_item_setup(self, _f, item: Gtk.ListItem) -> None:
        expander = Gtk.TreeExpander()
        row = Gtk.Box(spacing=6)
        icon = Gtk.Image(pixel_size=THUMB_PX)
        label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        row.append(icon)
        row.append(label)
        expander.set_child(row)
        item.set_child(expander)

    def _on_item_bind(self, _f, item: Gtk.ListItem) -> None:
        tree_row: Gtk.TreeListRow = item.get_item()
        expander: Gtk.TreeExpander = item.get_child()
        expander.set_list_row(tree_row)
        row = expander.get_child()
        icon: Gtk.Image = row.get_first_child()
        label: Gtk.Label = icon.get_next_sibling()
        info: Gio.FileInfo = tree_row.get_item()
        label.set_text(info.get_display_name() or info.get_name() or "")
        path = _info_path(info)
        if _is_dir(info):
            icon.set_from_icon_name("folder-symbolic")
            return
        tex = self._thumbnail(path, info) if path is not None else None
        if tex is not None:
            icon.set_from_paintable(tex)
        else:
            gicon = info.get_symbolic_icon()
            if gicon is not None:
                icon.set_from_gicon(gicon)
            else:
                icon.set_from_icon_name("text-x-generic-symbolic")

    def _thumbnail(self, path: Path, info: Gio.FileInfo) -> Optional[Gdk.Texture]:
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            return None
        mtime = info.get_modification_date_time()
        key = (str(path), mtime.to_unix() if mtime is not None else 0)
        tex = self._thumbs.get(key)
        if tex is None:
            try:
                pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), THUMB_PX, THUMB_PX, True)
            except GLib.Error:
                return None
            tex = Gdk.Texture.new_for_pixbuf(pix)
            self._thumbs[key] = tex
        return tex

    # ---- selection / actions ----

    def selected_path(self) -> Optional[Path]:
        return self._selected

    def _on_selection_changed(self, sel: Gtk.SingleSelection, _pspec) -> None:
        tree_row = sel.get_selected_item()
        info = tree_row.get_item() if tree_row is not None else None
        self._show_selection(_info_path(info) if info is not None else None)

    def _show_selection(self, path: Optional[Path]) -> None:
        self._selected = path
        is_file = path is not None and path.is_file()
        openable = is_file and path.suffix.lower() in OPENABLE_SUFFIXES
        importable = is_file and path.suffix.lower() in IMAGE_SUFFIXES
        self._open_btn.set_sensitive(openable)
        self._import_btn.set_sensitive(importable)
        res = godot_res_path(path) if path is not None else None
        self._res_btn.set_sensitive(res is not None)
        self._preview.clear()
        if path is None:
            self._info.set_text("Double-click an image to open it. Exports start in the selected folder.")
            return
        lines = []
        if importable:
            try:
                fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(str(path))
                if fmt is not None:
                    lines.append(f"{w}×{h} px")
                pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), PREVIEW_PX, PREVIEW_PX, True)
                self._preview.set_from_paintable(Gdk.Texture.new_for_pixbuf(pix))
            except GLib.Error:
                pass
        lines.append(res or str(path))
        self._info.set_text("\n".join(lines))

    def _on_activate(self, _lv: Gtk.ListView, position: int) -> None:
        model = self._selection.get_model()
        tree_row = model.get_item(position) if model is not None else None
        if tree_row is None:
            return
        if tree_row.is_expandable():
            tree_row.set_expanded(not tree_row.get_expanded())
            return
        path = _info_path(tree_row.get_item())
        if path is not None and path.suffix.lower() in OPENABLE_SUFFIXES:
            self._on_open(path)

    def _open_selected(self) -> None:
        if self._selected is not None and self._selected.is_file():
            self._on_open(self._selected)

    def _import_selected(self) -> None:
        if self._selected is not None and self._selected.is_file():
            self._on_import(self._selected)

    def _copy_res_path(self) -> None:
        res = godot_res_path(self._selected) if self._selected is not None else None
        if res:
            self.get_clipboard().set_content(Gdk.ContentProvider.new_for_value(res))

    def _reveal(self) -> None:
        target = self._selected or self._root
        if target is None:
            return
        open_in_file_manager(target if target.is_dir() else target.parent)

    def _on_change_root(self, _btn: Gtk.Button) -> None:
        dialog = Gtk.FileDialog(title="Choose Visual Assets Folder")
        if self._root is not None and self._root.is_dir():
            dialog.set_initial_folder(Gio.File.new_for_path(str(self._root)))
        dialog.select_folder(self.get_root(), None, self._on_change_root_done)

    def _on_change_root_done(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        raw = folder.get_path() if folder is not None else None
        if not raw:
            return
        self.set_root(Path(raw))
        self._on_root_changed(Path(raw))
