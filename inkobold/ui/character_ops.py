"""GreenInkobold character flows: New / Open / Save Character and rig-panel sync.

``CharacterOpsMixin`` is mixed into ``MainWindow``; every method runs with the
window as ``self``. Only flavors with ``character_rig`` reach these paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from inkobold.core import character as ch
from inkobold.core.document import Document
from inkobold.core.history import History
from inkobold.ui.dialogs import NewCharacterDialog


class CharacterOpsMixin:
    """Character (goblin rig) document flows for ``MainWindow``."""

    @staticmethod
    def _saves_in_place(doc: Optional[Document]) -> bool:
        """Ctrl+S writes without asking: .inkobold files and character folders."""
        if doc is None:
            return False
        if doc.character is not None:
            return doc.character.folder is not None
        return bool(doc.path) and doc.path.suffix.lower() in {".inkobold", ".scribbler"}

    def _sync_rig_panel(self) -> None:
        """Rig tab and live preview follow the active document."""
        if self.rig_panel is not None:
            self.rig_panel.sync()
        if self.rig_preview is not None:
            show = (
                self.document is not None
                and self.document.character is not None
                and self.rig_view.preview
            )
            if show != self.rig_preview.get_visible():
                self.rig_preview.set_visible(show)
            self.rig_preview.set_active(show)

    def _on_rig_changed(self) -> None:
        self.canvas.refresh_guides()
        self._set_status()
        self._sync_rig_panel()

    def _save_character(self, doc: Document, folder: Optional[Path], on_done=None) -> None:
        try:
            notes = ch.save_character(doc, folder)
        except (ch.CharacterError, OSError) as exc:
            self._alert("Character not saved", str(exc))
            if on_done is not None:
                on_done(False)
            return
        if notes:
            self._alert(f"Saved {doc.character.name}", "\n".join(notes))
        self._drop_autosave_for(doc)
        self._set_status()
        self._sync_rig_panel()
        if on_done is not None:
            on_done(True)

    def _save_character_as(self) -> None:
        doc = self.document
        if doc is None or doc.character is None:
            return
        dialog = Gtk.FileDialog(title="Save Character As (new folder)")
        dialog.set_initial_name(f"{doc.character.name} copy")
        if doc.character.folder is not None:
            dialog.set_initial_folder(Gio.File.new_for_path(str(doc.character.folder.parent)))
        dialog.save(self, None, self._on_save_character_as_done)

    def _on_save_character_as_done(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            return
        path = self._gfile_path(file, "folder") if file is not None else None
        if path is None or self.document is None:
            return
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            self._alert("Character not saved", f"{path} already exists. Pick a new folder name.")
            return
        self._save_character(self.document, path)

    def _characters_location(self) -> Optional[Path]:
        """Where New Character puts folders: the selected plain folder, else characters/."""
        browser = self.asset_browser
        selected = browser.selected_path() if browser is not None else None
        if selected is not None and selected.is_dir() and not ch.is_character_folder(selected):
            return selected
        root = browser.root() if browser is not None else None
        if root is None:
            return None
        chars = root / "characters"
        return chars if chars.is_dir() else root

    def _character_templates(self) -> list[tuple[str, Optional[Path]]]:
        out: list[tuple[str, Optional[Path]]] = []
        browser = self.asset_browser
        root = browser.root() if browser is not None else None
        goblin = root / "player" if root is not None else None
        if goblin is not None and ch.is_character_folder(goblin):
            out.append(("Copy of the player goblin", goblin))
        selected = browser.selected_character() if browser is not None else None
        if selected is not None and selected != goblin:
            out.append((f"Copy of {selected.name}", selected))
        out.append(("Blank parts (goblin joints)", None))
        return out

    def action_new_character(self, *_a) -> None:
        dlg = NewCharacterDialog(self, self._characters_location(), self._character_templates())
        dlg.connect_response(self._on_new_character_response)
        dlg.present()

    def _on_new_character_response(self, dlg: NewCharacterDialog, response: int) -> None:
        if response != Gtk.ResponseType.OK:
            return
        folder = dlg.folder()
        if folder is None:
            return
        try:
            ch.create_character(folder, template=dlg.template())
        except (ch.CharacterError, OSError) as exc:
            self._alert("Could not create the character", str(exc))
            return
        self.open_character_path(folder)

    def action_open_character(self, *_a) -> None:
        dialog = Gtk.FileDialog(title="Open Character Folder")
        location = self._characters_location()
        if location is not None and location.is_dir():
            dialog.set_initial_folder(Gio.File.new_for_path(str(location)))
        dialog.select_folder(self, None, self._on_open_character_done)

    def _on_open_character_done(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            folder = None
        path = self._gfile_path(folder, "folder") if folder is not None else None
        if path is None or not self.open_character_path(path):
            if self._open_at_startup and not self._tabs:
                # Cancelled (or failed) from the launch prompt: ask again
                self._open_at_startup = False
                GLib.idle_add(self._prompt_startup)

    def open_character_path(self, folder: Path) -> bool:
        """Open a character folder in a new tab (or switch to it if already open)."""
        folder = Path(folder)
        for i, tab in enumerate(self._tabs):
            rig = tab.document.character
            if rig is not None and rig.folder is not None and rig.folder.resolve() == folder.resolve():
                self._activate_tab(i, fit=False)
                return True
        self._startup_pending = False
        self._open_at_startup = False
        try:
            doc, notes = ch.open_character(folder)
        except (ch.CharacterError, OSError) as exc:
            self._alert("Could not open the character", f"{folder}\n{exc}")
            return False
        self.rig_view.show = True
        self._add_tab(doc, History(max_steps=self.app_settings.history_steps), fit=True)
        if self._rig_page_index is not None:
            self._left_notebook.set_current_page(self._rig_page_index)
        if notes:
            self._alert(f"Opened {doc.character.name}", "\n".join(notes))
        return True
