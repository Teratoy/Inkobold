"""Document tabs: the open-document strip in the top bar and tab switching / closing.

``DocTabsMixin`` holds the ``MainWindow`` methods that manage ``_DocTab`` entries;
``build_doc_tabs`` builds the tab strip widget. Both take the window as ``self`` /
a parameter and keep their state on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from inkobold.core.document import Document
from inkobold.core.flavor import current as current_flavor
from inkobold.core.history import History
from inkobold.ui.dialogs import QuitConfirmDialog


@dataclass
class _DocTab:
    """One open document and its undo/view state."""

    document: Document
    history: History
    zoom: float = 1.0
    pan_x: float = 40.0
    pan_y: float = 40.0
    # Pixel Art mode for this tab; the window follows it on tab switch
    pixel: bool = field(default_factory=lambda: current_flavor().pixel_default)
    root: Optional[Gtk.Widget] = field(default=None, repr=False)
    label: Optional[Gtk.Label] = field(default=None, repr=False)
    # Last (text, tooltip) pushed to the tab widget and whether it carried the
    # "active" CSS class — avoids redundant GTK property sets on every event.
    shown: Optional[tuple[str, str]] = field(default=None, repr=False)
    shown_active: Optional[bool] = field(default=None, repr=False)
    # This tab's crash-recovery copy (core.autosave) and the document state it holds
    autosave_path: Optional[Path] = field(default=None, repr=False)
    autosave_mark: Optional[tuple] = field(default=None, repr=False)


def build_doc_tabs(win) -> Gtk.Box:
    """Tab strip + "new tab" button, packed into the top bar by the window."""
    tabs_host = Gtk.Box(spacing=4, hexpand=True, halign=Gtk.Align.FILL)
    tabs_host.add_css_class("doc-tabs-host")

    tabs_scroll = Gtk.ScrolledWindow()
    tabs_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
    tabs_scroll.set_hexpand(True)
    tabs_scroll.set_vexpand(False)
    tabs_scroll.set_overlay_scrolling(True)
    tabs_host.append(tabs_scroll)

    win.doc_tabs = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    win.doc_tabs.add_css_class("doc-tabs")
    tabs_scroll.set_child(win.doc_tabs)

    new_tab_btn = Gtk.Button(label="+")
    new_tab_btn.set_tooltip_text("New canvas tab")
    new_tab_btn.add_css_class("doc-tab-new")
    new_tab_btn.set_valign(Gtk.Align.CENTER)
    new_tab_btn.connect("clicked", lambda *_: win.action_new())
    tabs_host.append(new_tab_btn)
    return tabs_host


class DocTabsMixin:
    """Open-document tabs for ``MainWindow`` (mixed into the window class)."""

    def _tab_index_of(self, doc: Optional[Document]) -> Optional[int]:
        for i, tab in enumerate(self._tabs):
            if tab.document is doc:
                return i
        return None

    def _new_document(self, w: int, h: int) -> None:
        self.action_stop_animation()
        doc = Document.blank(
            w,
            h,
            dpi=self.app_settings.default_dpi,
            color_depth=self.app_settings.default_color_depth,
        )
        hist = History(max_steps=self.app_settings.history_steps)
        self._add_tab(doc, hist, fit=True)

    def _tab_display_name(self, doc: Optional[Document]) -> str:
        if doc and doc.character is not None:
            return f"{doc.character.name} (character)"
        if doc and doc.path:
            return doc.path.name
        return "untitled.inkobold"

    def _tab_is_unsaved(self, doc: Optional[Document]) -> bool:
        if not doc:
            return False
        if doc.character is not None:
            return doc.character.folder is None or bool(doc.dirty)
        return (not doc.path) or bool(doc.dirty)

    def _tab_title_text(self, doc: Document) -> tuple[str, str, str]:
        name = self._tab_display_name(doc)
        unsaved = self._tab_is_unsaved(doc)
        state = "Unsaved" if unsaved else "Saved"
        mark = "*" if unsaved else ""
        label = f"{name}{mark}  ·  {state}"
        tip_path = str(doc.path) if doc.path else name
        tip = f"{tip_path}\n{state}"
        return label, tip, f"{name}{mark}"

    def _stash_active_view(self) -> None:
        if not (0 <= self._active_tab < len(self._tabs)):
            return
        tab = self._tabs[self._active_tab]
        r = self.canvas.renderer
        tab.zoom = float(r.zoom)
        tab.pan_x = float(r.pan_x)
        tab.pan_y = float(r.pan_y)

    def _add_tab(self, document: Document, history: History, *, fit: bool = True) -> None:
        self.action_stop_animation()
        self._stash_active_view()
        tab = _DocTab(document=document, history=history)
        self._tabs.append(tab)
        self._build_tab_widget(tab, len(self._tabs) - 1)
        self._activate_tab(len(self._tabs) - 1, fit=fit)

    def _build_tab_widget(self, tab: _DocTab, index: int) -> None:
        root = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER)
        root.add_css_class("doc-tab")
        root.set_overflow(Gtk.Overflow.HIDDEN)

        label = Gtk.Label(xalign=0.5, hexpand=True)
        label.add_css_class("doc-title")
        label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        label.set_max_width_chars(28)
        label.set_selectable(False)
        # Click target covers the title area; close button is separate.
        hit = Gtk.Box(hexpand=True)
        hit.append(label)
        root.append(hit)

        close = Gtk.Button(label="×")
        close.set_tooltip_text("Close canvas")
        close.add_css_class("doc-tab-close")
        close.set_has_frame(False)
        close.set_focus_on_click(False)
        close.connect("clicked", lambda *_a, i=index: self._request_close_tab(i))
        root.append(close)

        click = Gtk.GestureClick.new()
        click.set_button(1)
        click.connect("released", lambda *_a, i=index: self._on_tab_clicked(i))
        hit.add_controller(click)

        tab.root = root
        tab.label = label
        tab.shown = None
        tab.shown_active = None
        self.doc_tabs.append(root)
        self._refresh_tab_label(tab)

    def _reindex_tab_widgets(self) -> None:
        """Rebuild click/close callbacks after tabs are inserted/removed."""
        while (child := self.doc_tabs.get_first_child()) is not None:
            self.doc_tabs.remove(child)
        for i, tab in enumerate(self._tabs):
            self._build_tab_widget(tab, i)
        self._refresh_tab_styles()

    def _on_tab_clicked(self, index: int) -> None:
        if index == self._active_tab:
            return
        self._activate_tab(index, fit=False)

    def _activate_tab(self, index: int, *, fit: bool = False) -> None:
        if not (0 <= index < len(self._tabs)):
            return
        if self._effect_session is not None and index != self._active_tab:
            return
        self.action_stop_animation()
        if index != self._active_tab:
            self._stash_active_view()
        self._active_tab = index
        tab = self._tabs[index]
        self.document = tab.document
        self.history = tab.history
        self.canvas.renderer.invalidate()
        # Before the view below, so Fit and the restored zoom use this tab's mode
        if tab.pixel != self.pixel_mode:
            action = self.lookup_action("pixel_mode")
            if action is not None:
                action.change_state(GLib.Variant.new_boolean(tab.pixel))
        if fit:
            self.canvas.request_fit()
        else:
            r = self.canvas.renderer
            r.zoom = tab.zoom
            r.pan_x = tab.pan_x
            r.pan_y = tab.pan_y
        self._sync_anim_controls_from_doc()
        self._rebuild_frames()
        self._rebuild_layers()
        self._refresh_tab_styles()
        self._set_status()
        self.canvas.queue_render()
        self._sync_rig_panel()

    def _refresh_tab_label(self, tab: _DocTab) -> None:
        if tab.label is None:
            return
        text, tip, _ = self._tab_title_text(tab.document)
        if (text, tip) == tab.shown:
            return  # runs on every stroke event; skip redundant GTK property sets
        tab.shown = (text, tip)
        tab.label.set_text(text)
        tab.label.set_tooltip_text(tip)
        if tab.root is not None:
            tab.root.set_tooltip_text(tip)

    def _refresh_tab_styles(self) -> None:
        for i, tab in enumerate(self._tabs):
            if tab.root is None:
                continue
            active = i == self._active_tab
            if tab.shown_active is not active:
                tab.shown_active = active
                if active:
                    tab.root.add_css_class("active")
                else:
                    tab.root.remove_css_class("active")
            self._refresh_tab_label(tab)

    def _request_close_tab(self, index: int) -> None:
        if not (0 <= index < len(self._tabs)):
            return
        if self._effect_session is not None:
            return
        # Resolve by current list order: close buttons capture index at build time,
        # so reindex after mutations. Prefer matching via widget if stale.
        if index >= len(self._tabs):
            return
        tab = self._tabs[index]
        if self._tab_is_unsaved(tab.document):
            self._activate_tab(index, fit=False)
            self._closing_tab_index = index
            self._quit_dialog_open = True
            name = self._tab_display_name(tab.document)
            dlg = QuitConfirmDialog(self, name)
            # Retitle for close-tab context
            dlg.set_title("Close Canvas")
            dlg.connect_response(self._on_close_tab_response)
            dlg.present()
            return
        self._close_tab(index)

    def _on_close_tab_response(self, _dlg: QuitConfirmDialog, response: int) -> None:
        self._quit_dialog_open = False
        index = self._closing_tab_index
        self._closing_tab_index = None
        if index is None or not (0 <= index < len(self._tabs)):
            return
        if response == Gtk.ResponseType.CANCEL:
            return
        if response == Gtk.ResponseType.NO:
            self._close_tab(index)
            return
        if response == Gtk.ResponseType.YES:
            self._activate_tab(index, fit=False)
            self._closing_tab_index = index
            self._quit_after_save = False
            if self._saves_in_place(self.document):
                self._closing_tab_index = None
                self._save_document(on_done=self._close_tab_after_save(self.document))
                return
            dialog = Gtk.FileDialog(title="Save File")
            dialog.set_initial_name("drawing.inkobold")
            dialog.save(self, None, self._on_save_done_close_tab)

    def _on_save_done_close_tab(self, dialog: Gtk.FileDialog, result) -> None:
        index = self._closing_tab_index
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            self._closing_tab_index = None
            return
        self._closing_tab_index = None
        if not file or not self.document or index is None:
            return
        path = self._gfile_path(file)
        if path is None:
            return
        self._save_document(path, on_done=self._close_tab_after_save(self.document))

    def _close_tab_after_save(self, doc: Optional[Document]):
        def done(ok: bool) -> None:
            if not ok:
                return  # keep the tab open; the user already saw the error
            idx = self._tab_index_of(doc)
            if idx is not None:
                self._close_tab(idx)
        return done

    def _close_tab(self, index: int) -> None:
        if not (0 <= index < len(self._tabs)):
            return
        self.action_stop_animation()
        was_active = index == self._active_tab
        if was_active:
            self._stash_active_view()
        self._drop_autosave(self._tabs[index])
        self._tabs.pop(index)
        if not self._tabs:
            self._active_tab = -1
            self.document = None
            self.history = History(max_steps=self.app_settings.history_steps)
            self.canvas.renderer.invalidate()
            self._rebuild_frames()
            self._rebuild_layers()
            self._reindex_tab_widgets()
            self._set_status()
            self.canvas.queue_render()
            return
        next_index = self._active_tab
        if index < self._active_tab:
            next_index = self._active_tab - 1
        elif was_active:
            next_index = min(index, len(self._tabs) - 1)
        self._reindex_tab_widgets()
        if was_active:
            self._active_tab = -1
            self._activate_tab(next_index, fit=False)
        else:
            self._active_tab = next_index
            self._refresh_tab_styles()
            self._set_status()

    def _first_unsaved_tab_index(self) -> Optional[int]:
        for i, tab in enumerate(self._tabs):
            if id(tab.document) in self._quit_discarded:
                continue
            if self._tab_is_unsaved(tab.document):
                return i
        return None
