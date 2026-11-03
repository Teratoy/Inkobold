"""File flows for ``MainWindow``: new / open / save / export / import, the startup
prompt, quit and reload, the background save worker, autosave wiring and the
``.inkobold`` workspace snapshot (``_capture_workspace`` / ``_apply_workspace``).

``FileOpsMixin`` is mixed into ``MainWindow``; every method runs with the window
as ``self`` and keeps its state on the window.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from inkobold.core import autosave
from inkobold.core import character as ch
from inkobold.core.assets import export_folder
from inkobold.core.document import EXPORT_SCALES, Document
from inkobold.core.flavor import current as current_flavor
from inkobold.core.history import History
from inkobold.core.image_meta import COLOR_DEPTH_RGBA32, constrain_pixels, convert_pixels
from inkobold.core.libraries import open_in_file_manager
from inkobold.core.mirror import MODES, ORIENTATIONS
from inkobold.ui.doc_tabs import _DocTab
from inkobold.ui.dialogs import (
    AutosaveDialog,
    NewFileDialog,
    QuitConfirmDialog,
    RecoverAutosaveDialog,
    StartupDialog,
)


class FileOpsMixin:
    """Open / save / export / import, autosave and quit flows for ``MainWindow``."""

    def _capture_workspace(self) -> dict:
        """Snapshot tool options and document UI for .inkobold persistence."""
        self._stash_active_view()
        tools: dict = {}
        for tid, tool in self.tools.items():
            to_dict = getattr(tool, "to_settings_dict", None)
            if callable(to_dict):
                tools[tid] = to_dict()
        workspace: dict = {
            "tool_id": self.tool_id,
            "tools": tools,
            "mirror": {
                "enabled": bool(self.mirror.enabled),
                "mode": str(self.mirror.mode),
                "orientation": str(self.mirror.orientation),
                "axes": int(self.mirror.axes),
                "angle": float(self.mirror.angle),
                "cx_norm": float(self.mirror.cx_norm),
                "cy_norm": float(self.mirror.cy_norm),
                "array_count": int(self.mirror.array_count),
                "array_spacing": float(self.mirror.array_spacing),
                "array_direction": float(self.mirror.array_direction),
            },
            "grid": {
                "enabled": bool(self.grid.enabled),
                "rows": int(self.grid.rows),
                "columns": int(self.grid.columns),
                "tile": int(self.grid.tile),
                "ox_norm": float(self.grid.ox_norm),
                "oy_norm": float(self.grid.oy_norm),
            },
            "tile_wrap": bool(self.tile_wrap),
            "pixel": bool(self.pixel_mode),
            "export_scale": int(self.export_scale),
        }
        if 0 <= self._active_tab < len(self._tabs):
            tab = self._tabs[self._active_tab]
            workspace["view"] = {
                "zoom": float(tab.zoom),
                "pan_x": float(tab.pan_x),
                "pan_y": float(tab.pan_y),
            }
        return workspace

    def _apply_workspace(self, workspace: Optional[dict], *, restore_view: bool = True) -> None:
        """Restore tool options / UI previously saved into a document workspace."""
        if not isinstance(workspace, dict):
            return

        tools_data = workspace.get("tools")
        if isinstance(tools_data, dict):
            for tid, data in tools_data.items():
                tool = self.tools.get(str(tid))
                apply = getattr(tool, "apply_settings_dict", None) if tool is not None else None
                if callable(apply) and isinstance(data, dict):
                    apply(data)

        mirror = workspace.get("mirror")
        if isinstance(mirror, dict):
            self.mirror.enabled = bool(mirror.get("enabled", False))
            mode = str(mirror.get("mode", "mirror"))
            if mode in {k for k, _ in MODES}:
                self.mirror.mode = mode
            orient = str(mirror.get("orientation", "horizontal"))
            if orient in {k for k, _ in ORIENTATIONS}:
                self.mirror.orientation = orient
            try:
                self.mirror.axes = int(mirror.get("axes", 1))
            except (TypeError, ValueError):
                pass
            if "angle" in mirror:
                try:
                    self.mirror.angle = float(mirror["angle"])
                except (TypeError, ValueError):
                    self.mirror.apply_orientation_preset(self.mirror.orientation)
            else:
                self.mirror.apply_orientation_preset(self.mirror.orientation)
            try:
                if "cx_norm" in mirror:
                    self.mirror.cx_norm = float(mirror["cx_norm"])
                if "cy_norm" in mirror:
                    self.mirror.cy_norm = float(mirror["cy_norm"])
            except (TypeError, ValueError):
                pass
            for key in ("array_count", "array_spacing", "array_direction"):
                if key in mirror:
                    try:
                        setattr(self.mirror, key, float(mirror[key]))
                    except (TypeError, ValueError):
                        pass
            self.mirror.clamp()
            if hasattr(self, "mirror_toggle"):
                self.mirror_toggle.set_active(self.mirror.enabled)
            if hasattr(self, "mirror_opts"):
                self.mirror_opts.set_visible(self.mirror.enabled)
            if hasattr(self, "mirror_mode_dropdown"):
                idx = next(
                    (i for i, (k, _) in enumerate(MODES) if k == self.mirror.mode),
                    0,
                )
                self.mirror_mode_dropdown.set_selected(idx)
            if hasattr(self, "mirror_orient_dropdown"):
                idx = next(
                    (i for i, (k, _) in enumerate(ORIENTATIONS) if k == self.mirror.orientation),
                    0,
                )
                self.mirror_orient_dropdown.set_selected(idx)
            if hasattr(self, "mirror_axes_spin"):
                self.mirror_axes_spin.set_value(self.mirror.axes)
            self._sync_mirror_array_ui()
            self._update_mirror_mode_ui()
            if hasattr(self, "canvas"):
                self.canvas.refresh_guides()

        grid = workspace.get("grid")
        if isinstance(grid, dict):
            self.grid.enabled = bool(grid.get("enabled", False))
            try:
                self.grid.rows = int(grid.get("rows", self.grid.rows))
                self.grid.columns = int(grid.get("columns", self.grid.columns))
                self.grid.tile = int(grid.get("tile", self.grid.tile))
            except (TypeError, ValueError):
                pass
            try:
                if "ox_norm" in grid:
                    self.grid.ox_norm = float(grid["ox_norm"])
                if "oy_norm" in grid:
                    self.grid.oy_norm = float(grid["oy_norm"])
            except (TypeError, ValueError):
                pass
            self.grid.clamp()
            action = self.lookup_action("show_grid")
            if action is not None:
                action.change_state(GLib.Variant.new_boolean(self.grid.enabled))

        if "tile_wrap" in workspace:
            enabled = bool(workspace.get("tile_wrap"))
            self.tile_wrap = enabled
            action = self.lookup_action("tile_wrap")
            if action is not None:
                action.change_state(GLib.Variant.new_boolean(enabled))
            elif hasattr(self, "tile_wrap_toggle"):
                self.tile_wrap_toggle.set_active(enabled)

        action = self.lookup_action("pixel_mode")
        if action is not None:
            pixel = workspace.get("pixel", current_flavor().pixel_default)
            action.change_state(GLib.Variant.new_boolean(bool(pixel)))

        action = self.lookup_action("export_scale")
        if action is not None:
            try:
                scale = int(workspace.get("export_scale", 1))
            except (TypeError, ValueError):
                scale = 1
            action.change_state(GLib.Variant.new_string(str(scale if scale in EXPORT_SCALES else 1)))

        tid = workspace.get("tool_id")
        if isinstance(tid, str) and tid in self.tools:
            self.select_tool(tid)
        if self.app_settings.color_follows_tools:
            self._take_shared_colors_from_active_tool()
        self._load_active_tool_settings()

        if restore_view:
            view = workspace.get("view")
            if isinstance(view, dict) and 0 <= self._active_tab < len(self._tabs):
                tab = self._tabs[self._active_tab]
                try:
                    tab.zoom = float(view.get("zoom", tab.zoom))
                    tab.pan_x = float(view.get("pan_x", tab.pan_x))
                    tab.pan_y = float(view.get("pan_y", tab.pan_y))
                except (TypeError, ValueError):
                    pass
                r = self.canvas.renderer
                r.zoom = tab.zoom
                r.pan_x = tab.pan_x
                r.pan_y = tab.pan_y

        if hasattr(self, "canvas"):
            self.canvas.refresh_guides()
            self.canvas.queue_render()

    def _alert(self, message: str, detail: str = "") -> None:
        err = Gtk.AlertDialog()
        err.set_message(message)
        if detail:
            err.set_detail(detail)
        err.show(self)

    def _gfile_path(self, gfile, what: str = "file") -> Optional[Path]:
        """Local path of a Gio.File from a file dialog, or None (with an alert)."""
        raw = gfile.get_path() if gfile is not None else None
        if not raw:
            uri = gfile.get_uri() if gfile is not None else ""
            self._alert(
                f"Can't use that {what}",
                f"{current_flavor().display_name} can only read and write local files.\n{uri}",
            )
            return None
        return Path(raw)

    def _start_in_asset_folder(self, dialog: Gtk.FileDialog) -> None:
        """Point an export dialog at the visual-asset folder (flavors with an asset pipeline)."""
        browser = self.asset_browser
        folder = export_folder(
            browser.root() if browser is not None else None,
            self.document.path if self.document else None,
            browser.selected_path() if browser is not None else None,
        )
        if folder is not None:
            dialog.set_initial_folder(Gio.File.new_for_path(str(folder)))

    def _on_asset_root_changed(self, root: Path) -> None:
        self.app_settings.asset_root = str(root)
        self._persist_settings()

    def _save_document(self, path: Path | None = None, on_done=None) -> None:
        """Save the active document (plus workspace) without blocking the UI.

        Pixels are copied on the main thread, then compressed and written on a
        worker thread. The write is atomic, so a failure never damages the
        existing file. ``on_done(ok: bool)`` runs on the main thread afterwards.
        """
        doc = self.document
        if doc is None:
            if on_done is not None:
                on_done(False)
            return
        if doc.character is not None:
            self._save_character(doc, path, on_done)
            return
        doc.workspace = self._capture_workspace()
        old_path, old_dirty = doc.path, doc.dirty
        try:
            job = doc.prepare_save(path)
        except Exception as exc:
            doc.path, doc.dirty = old_path, old_dirty
            self._alert("Save failed", str(exc))
            if on_done is not None:
                on_done(False)
            return
        self._set_status()

        def finish(error: Optional[BaseException]) -> bool:
            self._save_futures = [f for f in self._save_futures if not f.done()]
            if error is not None:
                if doc.path == job.target:
                    doc.path = old_path
                doc.dirty = True
                self._alert("Save failed", f"{job.target}\n{error}\n\nThe existing file was not changed.")
            elif not doc.dirty:
                self._drop_autosave_for(doc)
            self._set_status()
            if on_done is not None:
                on_done(error is None)
            return False

        def work() -> None:
            error: Optional[BaseException] = None
            try:
                job.write()
            except Exception as exc:  # reported on the main thread
                error = exc
            GLib.idle_add(finish, error)

        self._save_futures.append(self._save_executor.submit(work))

    def _wait_for_saves(self) -> None:
        for fut in list(self._save_futures):
            try:
                fut.result()
            except Exception:
                pass
        self._save_futures.clear()

    def action_memory_autosave(self, *_a) -> None:
        dlg = AutosaveDialog(
            self,
            self.app_settings.autosave,
            self.app_settings.autosave_minutes,
            minutes_range=(autosave.MINUTES_MIN, autosave.MINUTES_MAX),
            folder=autosave.autosave_root(),
            on_open_folder=lambda: open_in_file_manager(autosave.autosave_root()),
        )
        dlg.connect_response(self._on_memory_autosave_response)
        dlg.present()

    def _on_memory_autosave_response(self, dlg: AutosaveDialog, response: int) -> None:
        if response != Gtk.ResponseType.OK:
            return
        self.app_settings.autosave = dlg.enabled()
        self.app_settings.autosave_minutes = dlg.minutes()
        self._persist_settings()
        self._schedule_autosave()

    # --- autosave (core.autosave; modelled on Blender's Auto Save) ---

    def _schedule_autosave(self, seconds: Optional[int] = None) -> None:
        """(Re)start the autosave timer: the set interval, or *seconds* for a retry."""
        if self._autosave_timer_id is not None:
            GLib.source_remove(self._autosave_timer_id)
            self._autosave_timer_id = None
        if self._autosave is None or not self.app_settings.autosave:
            return
        if seconds is None:
            seconds = int(self.app_settings.autosave_minutes) * 60
        self._autosave_timer_id = GLib.timeout_add_seconds(seconds, self._on_autosave_timer)

    def _autosave_busy(self) -> bool:
        """Mid-stroke or previewing an effect: the pixels are not a state worth keeping."""
        return self.canvas.stroke_active or self._effect_session is not None

    def _on_autosave_timer(self) -> bool:
        self._autosave_timer_id = None
        if self._autosave_busy():
            # Like Blender during a modal operator: try again shortly.
            self._schedule_autosave(autosave.BUSY_RETRY_SECONDS)
            return False
        self._autosave_now()
        self._schedule_autosave()
        return False

    def _autosave_now(self) -> None:
        """Queue an autosave of every tab whose document changed since its last one."""
        session = self._autosave
        if session is None:
            return
        for tab in list(self._tabs):
            doc = tab.document
            if not doc.dirty:
                self._drop_autosave(tab)  # saved since the last autosave
                continue
            mark = autosave.fingerprint(doc)
            if mark == tab.autosave_mark:
                continue
            title = self._tab_display_name(doc)
            if tab.autosave_path is None:
                tab.autosave_path = session.new_path(title)
            if doc is self.document:
                doc.workspace = self._capture_workspace()
            try:
                job = autosave.prepare(doc, tab.autosave_path, title)
            except Exception as exc:
                self._autosave_failed(exc)
                continue
            self._submit_autosave(tab, job, mark)

    def _submit_autosave(self, tab: _DocTab, job, mark: tuple) -> None:
        def finish(error: Optional[BaseException]) -> bool:
            self._save_futures = [f for f in self._save_futures if not f.done()]
            if not any(t is tab for t in self._tabs) or not tab.document.dirty:
                # Closed or saved while the copy was being written.
                self._drop_autosave(tab)
            elif error is not None:
                self._autosave_failed(error)
            else:
                tab.autosave_mark = mark
            return False

        def work() -> None:
            error: Optional[BaseException] = None
            try:
                job.write()
            except Exception as exc:  # reported on the main thread
                error = exc
            GLib.idle_add(finish, error)

        # Same single writer as real saves, so the two never interleave.
        self._save_futures.append(self._save_executor.submit(work))

    def _autosave_failed(self, error: BaseException) -> None:
        print(f"autosave failed: {error}", file=sys.stderr)
        if self._autosave_warned:
            return
        self._autosave_warned = True
        self._alert(
            "Autosave failed",
            f"{error}\n\nAutosave keeps trying. Your documents and files are not affected.",
        )

    def _drop_autosave(self, tab: _DocTab) -> None:
        autosave.discard(tab.autosave_path)
        tab.autosave_path = None
        tab.autosave_mark = None

    def _drop_autosave_for(self, doc: Document) -> None:
        idx = self._tab_index_of(doc)
        if idx is not None:
            self._drop_autosave(self._tabs[idx])

    def action_recover_autosave(self, *_a) -> None:
        self._show_recover_dialog()

    def _show_recover_dialog(self, from_startup: bool = False) -> None:
        def closed() -> None:
            if from_startup and not self._tabs:
                self._prompt_startup()

        dlg = RecoverAutosaveDialog(
            self,
            autosave.find_recoverable(),
            on_open=self._recover_autosave,
            on_delete=autosave.delete_recoverable,
            on_open_folder=lambda: open_in_file_manager(autosave.autosave_root()),
            on_closed=closed,
        )
        dlg.present()

    def _recover_autosave(self, item: autosave.Recoverable) -> bool:
        """Open a crashed session's autosave as an unsaved tab; True when it opened."""
        try:
            doc = autosave.open_recoverable(item.path, item.info)
        except Exception as exc:
            self._alert("Could not recover the autosave", f"{item.path}\n{exc}")
            return False
        try:
            # Keep the copy (now this session's) until the document is saved or closed.
            path: Optional[Path] = autosave.adopt(item, self._autosave)
        except OSError:
            path = None
        self._startup_pending = False
        self._open_at_startup = False
        if doc.character is not None:
            self.rig_view.show = True
        has_view = isinstance((doc.workspace or {}).get("view"), dict)
        self._add_tab(doc, History(max_steps=self.app_settings.history_steps), fit=not has_view)
        self._apply_workspace(doc.workspace, restore_view=has_view)
        if path is not None and self._autosave is not None and path.parent == self._autosave.folder:
            tab = self._tabs[self._active_tab]
            tab.autosave_path = path
            tab.autosave_mark = autosave.fingerprint(doc)
        return True

    def _maybe_prompt_startup(self) -> bool:
        if not self._startup_pending:
            return False
        if self._tabs:
            self._startup_pending = False
            return False
        self._prompt_startup()
        return False

    def request_startup_prompt(self) -> None:
        """Show Open/New if nothing was opened (used when CLI paths fail)."""
        if self._tabs:
            self._startup_pending = False
            return
        self._startup_pending = True
        GLib.idle_add(self._maybe_prompt_startup)

    def _prompt_startup(self) -> bool:
        self._startup_pending = False
        dlg = StartupDialog(self, recoverable=len(autosave.find_recoverable()))
        dlg.connect_response(self._on_startup_response)
        dlg.present()
        return False

    def _on_startup_response(self, _dlg: StartupDialog, response: int) -> None:
        if response == Gtk.ResponseType.YES:
            dlg = NewFileDialog(self)
            dlg.connect_response(self._on_startup_new_response)
            dlg.present()
            return
        if response == Gtk.ResponseType.NO:
            self._open_at_startup = True
            self.action_open()
            return
        if response == Gtk.ResponseType.APPLY:
            self._open_at_startup = True
            self.action_open_character()
            return
        if response == Gtk.ResponseType.ACCEPT:
            self._show_recover_dialog(from_startup=True)
            return
        self._do_quit()

    def _on_startup_new_response(self, dlg: NewFileDialog, response: int) -> None:
        if response == Gtk.ResponseType.OK:
            w, h = dlg.size()
            self._new_document(w, h)
            return
        GLib.idle_add(self._prompt_startup)

    # --- actions ---

    def action_new(self, *_a) -> None:
        dlg = NewFileDialog(self)
        dlg.connect_response(self._on_new_response)
        dlg.present()

    def _on_new_response(self, dlg: NewFileDialog, response: int) -> None:
        if response == Gtk.ResponseType.OK:
            w, h = dlg.size()
            self._new_document(w, h)

    def open_path(self, path: Path) -> bool:
        """Open *path* in a new tab. Returns False on failure."""
        if current_flavor().character_rig:
            # A character folder, or its rig.json, opens as a character
            path = Path(path)
            if path.is_dir() or path.name == ch.FILE_NAME:
                folder = ch.character_folder_for(path)
                if folder is not None:
                    return self.open_character_path(folder)
        self._startup_pending = False
        self._open_at_startup = False
        try:
            doc = Document.open(Path(path))
        except Exception as exc:
            err = Gtk.AlertDialog()
            err.set_message("Could not open file")
            err.set_detail(f"{path}\n{exc}")
            err.show(self)
            return False
        hist = History(max_steps=self.app_settings.history_steps)
        has_view = isinstance((doc.workspace or {}).get("view"), dict)
        self._add_tab(doc, hist, fit=not has_view)
        self._apply_workspace(doc.workspace, restore_view=has_view)
        return True

    def action_open(self, *_a) -> None:
        dialog = Gtk.FileDialog(title="Open File")
        filt = Gtk.FileFilter()
        filt.set_name("Inkobold / Images")
        filt.add_pattern("*.inkobold")
        filt.add_pattern("*.scribbler")
        filt.add_pattern("*.png")
        filt.add_pattern("*.jpg")
        filt.add_pattern("*.jpeg")
        filt.add_pattern("*.webp")
        filt.add_pattern("*.bmp")
        filt.add_pattern("*.gif")
        if current_flavor().character_rig:
            filt.set_name("Inkobold / Images / Characters")
            filt.add_pattern(ch.FILE_NAME)
        dialog.set_default_filter(filt)
        dialog.open(self, None, self._on_open_done)

    def _on_open_done(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            if self._open_at_startup:
                self._open_at_startup = False
                GLib.idle_add(self._prompt_startup)
            return
        if not file:
            if self._open_at_startup:
                self._open_at_startup = False
                GLib.idle_add(self._prompt_startup)
            return
        path = file.get_path()
        if not path:
            if self._open_at_startup:
                self._open_at_startup = False
                GLib.idle_add(self._prompt_startup)
            return
        self.open_path(Path(path))

    def action_save(self, *_a) -> None:
        if not self.document:
            return
        if self._saves_in_place(self.document):
            self._save_document()
            return
        self.action_save_as()

    def action_save_as(self, *_a) -> None:
        if self.document is not None and self.document.character is not None:
            self._save_character_as()
            return
        dialog = Gtk.FileDialog(title="Save File")
        dialog.set_initial_name("drawing.inkobold")
        dialog.save(self, None, self._on_save_done)

    def _on_save_done(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            self._quit_after_save = False
            return
        if not file or not self.document:
            self._quit_after_save = False
            return
        path = self._gfile_path(file)
        if path is None:
            self._quit_after_save = False
            return
        quitting = self._quit_after_save
        self._quit_after_save = False

        def done(ok: bool) -> None:
            if quitting and ok:
                # Continue quit flow for any remaining unsaved tabs.
                self._continue_quit()
            elif quitting:
                self._quit_discarded.clear()

        self._save_document(path, on_done=done)

    def request_quit(self, *_a) -> None:
        """Start a quit: ask about each unsaved tab in turn."""
        if self._quit_dialog_open or self._allow_close:
            return
        self._quit_discarded.clear()
        self._continue_quit()

    def _continue_quit(self) -> None:
        if self._quit_dialog_open or self._allow_close:
            return
        if not self._tabs:
            self._do_quit()
            return
        unsaved = self._first_unsaved_tab_index()
        if unsaved is None:
            self._do_quit()
            return
        self._activate_tab(unsaved, fit=False)
        self._quit_dialog_open = True
        name = self._doc_display_name()
        dlg = QuitConfirmDialog(self, name)
        dlg.connect_response(self._on_quit_response)
        dlg.present()

    def _on_quit_response(self, _dlg: QuitConfirmDialog, response: int) -> None:
        self._quit_dialog_open = False
        if response == Gtk.ResponseType.CANCEL:
            self._quit_discarded.clear()
            return
        if response == Gtk.ResponseType.NO:
            # Discard this document only, then ask about the next unsaved one.
            if self.document is not None:
                self._quit_discarded.add(id(self.document))
            self._continue_quit()
            return
        if response == Gtk.ResponseType.YES:
            self._save_then_quit()

    def _save_then_quit(self) -> None:
        if not self.document:
            self._do_quit()
            return
        if self._saves_in_place(self.document):
            def done(ok: bool) -> None:
                if ok:
                    self._continue_quit()  # quits, or asks about the next unsaved tab
                else:
                    self._quit_discarded.clear()

            self._save_document(on_done=done)
            return
        self._quit_after_save = True
        dialog = Gtk.FileDialog(title="Save File")
        dialog.set_initial_name("drawing.inkobold")
        dialog.save(self, None, self._on_save_done)

    def _do_quit(self) -> None:
        # Never exit with a save half-written (the atomic rename would be lost).
        self._wait_for_saves()
        if self._autosave is not None:
            # A normal quit: every document was saved or deliberately discarded.
            self._autosave.close(remove=True)
            self._autosave = None
        self._allow_close = True
        self.shutdown_input()
        app = self.get_application()
        if app is not None:
            app.quit()
        else:
            self.destroy()

    def request_reload(self, *_a) -> None:
        """Restart the process so code changes take effect; debug_mode stays in settings."""
        self._persist_settings()
        if self._autosave is not None:
            # Reload doesn't ask about unsaved work: autosave it now so the restarted
            # process can recover it.
            self._autosave_now()
            self._wait_for_saves()
            self._autosave.close(remove=not any(t.document.dirty for t in self._tabs))
            self._autosave = None
        self.shutdown_input()
        argv = [sys.executable, "-m", current_flavor().module, *sys.argv[1:]]
        if sys.platform == "win32":
            # os.execv is unreliable with Windows paths/spaces; spawn then quit.
            import subprocess

            subprocess.Popen(argv)
            self._do_quit()
            return
        os.execv(sys.executable, argv)

    def _export_title(self, title: str) -> str:
        """File dialog title, noting the Export Scale when it enlarges."""
        return f"{title} (×{self.export_scale})" if self.export_scale > 1 else title

    def action_export(self, *_a) -> None:
        if not self.document:
            return
        dialog = Gtk.FileDialog(title=self._export_title("Export Image"))
        filters = Gio.ListStore.new(Gtk.FileFilter)
        for name, patterns in (
            ("PNG image", ("*.png",)),
            ("JPEG image", ("*.jpg", "*.jpeg")),
            ("WebP image", ("*.webp",)),
        ):
            f = Gtk.FileFilter()
            f.set_name(name)
            for p in patterns:
                f.add_pattern(p)
            filters.append(f)
        dialog.set_filters(filters)
        dialog.set_default_filter(filters.get_item(0))
        stem = self.document.path.stem if self.document.path else "drawing"
        dialog.set_initial_name(f"{stem}.png")
        self._start_in_asset_folder(dialog)
        dialog.save(self, None, self._on_export_done)

    def _on_export_done(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            return
        if not file or not self.document:
            return
        path = self._gfile_path(file)
        if path is None:
            return
        # If the chosen filter implies a type but the name has no/unknown suffix, fix it
        filt = dialog.get_default_filter()
        if filt is not None and path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            name = (filt.get_name() or "").lower()
            if "jpeg" in name or "jpg" in name:
                path = path.with_suffix(".jpg")
            elif "webp" in name:
                path = path.with_suffix(".webp")
            else:
                path = path.with_suffix(".png")
        try:
            self.document.export_image(path, scale=self.export_scale)
        except Exception as exc:
            err = Gtk.AlertDialog()
            err.set_message("Export failed")
            err.set_detail(str(exc))
            err.show(self)
            return
        self._set_status()

    def action_export_anim_gif(self, *_a) -> None:
        if not self.document:
            return
        dialog = Gtk.FileDialog(title=self._export_title("Export Animation as GIF"))
        filt = Gtk.FileFilter()
        filt.set_name("GIF animation")
        filt.add_pattern("*.gif")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filt)
        dialog.set_filters(filters)
        dialog.set_default_filter(filt)
        stem = self.document.path.stem if self.document.path else "animation"
        dialog.set_initial_name(f"{stem}.gif")
        self._start_in_asset_folder(dialog)
        dialog.save(self, None, self._on_export_anim_gif_done)

    def _on_export_anim_gif_done(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            return
        if not file or not self.document:
            return
        path = self._gfile_path(file)
        if path is None:
            return
        if path.suffix.lower() != ".gif":
            path = path.with_suffix(".gif")
        try:
            self.document.export_animation_gif(path, scale=self.export_scale)
        except Exception as exc:
            err = Gtk.AlertDialog()
            err.set_message("GIF export failed")
            err.set_detail(str(exc))
            err.show(self)
            return
        self._set_status()

    def action_export_anim_png(self, *_a) -> None:
        if not self.document:
            return
        dialog = Gtk.FileDialog(title=self._export_title("Export Animation as PNG Folder"))
        self._start_in_asset_folder(dialog)
        dialog.select_folder(self, None, self._on_export_anim_png_done)

    def _on_export_anim_png_done(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if not folder or not self.document:
            return
        path = self._gfile_path(folder, "folder")
        if path is None:
            return
        try:
            self.document.export_animation_png_sequence(path, scale=self.export_scale)
        except Exception as exc:
            err = Gtk.AlertDialog()
            err.set_message("PNG sequence export failed")
            err.set_detail(str(exc))
            err.show(self)
            return
        self._set_status()

    def action_export_anim_sheet(self, *_a) -> None:
        if not self.document:
            return
        dialog = Gtk.FileDialog(title=self._export_title("Export Animation as Sprite Sheet"))
        filt = Gtk.FileFilter()
        filt.set_name("PNG image")
        filt.add_pattern("*.png")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filt)
        dialog.set_filters(filters)
        dialog.set_default_filter(filt)
        stem = self.document.path.stem if self.document.path else "animation"
        dialog.set_initial_name(f"{stem}_sheet.png")
        self._start_in_asset_folder(dialog)
        dialog.save(self, None, self._on_export_anim_sheet_done)

    def _on_export_anim_sheet_done(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            return
        if not file or not self.document:
            return
        path = self._gfile_path(file)
        if path is None:
            return
        try:
            self.document.export_sprite_sheet(path, scale=self.export_scale)
        except Exception as exc:
            err = Gtk.AlertDialog()
            err.set_message("Sprite sheet export failed")
            err.set_detail(str(exc))
            err.show(self)
            return
        self._set_status()

    def action_export_layer(self, *_a) -> None:
        if not self.document or not self.document.layers:
            return
        dialog = Gtk.FileDialog(title=self._export_title("Export Selected Layer"))
        filt = Gtk.FileFilter()
        filt.set_name("PNG image")
        filt.add_pattern("*.png")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filt)
        dialog.set_filters(filters)
        dialog.set_default_filter(filt)
        stem = self.document._sanitize_layer_filename(self.document.active_layer.name)
        dialog.set_initial_name(f"{stem}.png")
        self._start_in_asset_folder(dialog)
        dialog.save(self, None, self._on_export_layer_done)

    def _on_export_layer_done(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            return
        if not file or not self.document:
            return
        path = self._gfile_path(file)
        if path is None:
            return
        if path.suffix.lower() != ".png":
            path = path.with_suffix(".png")
        try:
            self.document.export_layer_png(path, scale=self.export_scale)
        except Exception as exc:
            err = Gtk.AlertDialog()
            err.set_message("Layer export failed")
            err.set_detail(str(exc))
            err.show(self)
            return
        self._set_status()

    def action_export_layers(self, *_a) -> None:
        if not self.document:
            return
        dialog = Gtk.FileDialog(title=self._export_title("Export Separated Layers"))
        self._start_in_asset_folder(dialog)
        dialog.select_folder(self, None, self._on_export_layers_done)

    def _on_export_layers_done(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if not folder or not self.document:
            return
        path = self._gfile_path(folder, "folder")
        if path is None:
            return
        try:
            self.document.export_layers_png(
                path, index_prefix=current_flavor().layer_export_index, scale=self.export_scale
            )
        except Exception as exc:
            err = Gtk.AlertDialog()
            err.set_message("Layer export failed")
            err.set_detail(str(exc))
            err.show(self)
            return
        self._set_status()

    def action_import_image(self, *_a) -> None:
        dialog = Gtk.FileDialog(title="Import Image as New Layer")
        filt = Gtk.FileFilter()
        filt.set_name("Images")
        for p in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp", "*.gif"):
            filt.add_pattern(p)
        dialog.set_default_filter(filt)
        dialog.open(self, None, self._on_import_done)

    def _on_import_done(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            return
        if not file or not self.document:
            return
        path = self._gfile_path(file)
        if path is None:
            return
        self.import_image_path(path)

    def import_image_path(self, path: Path) -> None:
        """Float *path* onto a new layer of the active document (Transform until Enter)."""
        if not self.document:
            return
        try:
            with Image.open(path) as img:
                arr = np.array(img.convert("RGBA"), dtype=np.uint8)
        except Exception as exc:
            self._alert("Could not import image", f"{path}\n{exc}")
            return
        doc = self.document
        self._cancel_type_edit_if_any()
        self._push_history()
        doc.add_layer(name=path.stem)
        # Float the full image at 1:1 with Transform so it can be scaled from
        # its original pixels (even past the canvas edge) until Enter.
        self.select_tool("move")
        tool = self.tools["move"]
        ctx = self.canvas._ctx()
        if ctx is not None:
            src = convert_pixels(arr, COLOR_DEPTH_RGBA32, doc.color_depth)
            constrain_pixels(src, doc.color_depth)
            tool.begin_placement(ctx, src)
        self.canvas.renderer.invalidate()
        self._rebuild_layers()
        self.canvas.queue_render()
        self.canvas.refresh_guides()
        self.canvas._catcher.grab_focus()
        self._set_status()
