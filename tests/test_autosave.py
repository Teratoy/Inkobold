"""Autosave / crash recovery tests (no GTK needed).

Run with ``python -m pytest tests`` or, without pytest, ``python tests/test_autosave.py``.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inkobold.core import Document  # noqa: E402
from inkobold.core import autosave  # noqa: E402


def _root() -> Path:
    return Path(tempfile.mkdtemp(prefix="inkobold-autosave-"))


def _painted_doc() -> Document:
    doc = Document.blank(8, 6)
    doc.active_layer.pixels[2, 3] = (10, 20, 30, 255)
    doc.mark_dirty()
    return doc


def _autosave(session: autosave.Session, doc: Document, title: str = "drawing") -> Path:
    path = session.new_path(title)
    autosave.prepare(doc, path, title).write()
    return path


def test_autosave_leaves_the_document_unsaved_and_its_path_alone():
    root = _root()
    session = autosave.Session.start(root)
    doc = _painted_doc()
    doc.path = root / "art.inkobold"
    path = _autosave(session, doc)
    assert path.is_file() and path.parent == session.folder
    assert doc.dirty and doc.path == root / "art.inkobold"
    assert not doc.path.exists()  # the real file is never written
    session.close()


def test_running_sessions_are_not_offered_for_recovery():
    root = _root()
    session = autosave.Session.start(root)
    _autosave(session, _painted_doc())
    assert autosave.find_recoverable(root) == []
    session.close()


def test_clean_quit_removes_the_session():
    root = _root()
    session = autosave.Session.start(root)
    _autosave(session, _painted_doc())
    session.close(remove=True)
    assert not session.folder.exists()
    assert autosave.find_recoverable(root) == []


def test_crashed_session_recovers_pixels_and_original_path():
    root = _root()
    crashed = autosave.Session.start(root)
    doc = _painted_doc()
    doc.path = root / "art.inkobold"
    doc.active_layer.name = "Ink"
    _autosave(crashed, doc, "art.inkobold")
    crashed.close(remove=False)  # lock released, folder kept: what a crash leaves

    found = autosave.find_recoverable(root)
    assert len(found) == 1
    item = found[0]
    assert item.title == "art.inkobold" and item.source == root / "art.inkobold"

    back = autosave.open_recoverable(item.path, item.info)
    assert back.dirty and back.path == root / "art.inkobold"
    assert back.active_layer.name == "Ink"
    assert np.array_equal(back.active_layer.pixels, doc.active_layer.pixels)


def test_untitled_document_recovers_without_a_path():
    root = _root()
    crashed = autosave.Session.start(root)
    _autosave(crashed, _painted_doc(), "untitled.inkobold")
    crashed.close(remove=False)
    item = autosave.find_recoverable(root)[0]
    assert item.source is None
    assert autosave.open_recoverable(item.path, item.info).path is None


def test_adopt_moves_the_file_into_the_running_session():
    root = _root()
    crashed = autosave.Session.start(root)
    _autosave(crashed, _painted_doc())
    crashed.close(remove=False)
    live = autosave.Session.start(root)
    item = autosave.find_recoverable(root)[0]
    moved = autosave.adopt(item, live)
    assert moved.parent == live.folder and moved.is_file()
    assert not crashed.folder.exists()  # emptied dead session is cleaned up
    assert autosave.find_recoverable(root) == []
    live.close()


def test_delete_and_broken_files():
    root = _root()
    crashed = autosave.Session.start(root)
    _autosave(crashed, _painted_doc(), "keep")
    broken = crashed.new_path("broken")
    broken.write_bytes(b"not a zip")  # a crash in the middle of a write
    crashed.close(remove=False)
    found = autosave.find_recoverable(root)
    assert [r.title for r in found] == ["keep"]
    autosave.delete_recoverable(found[0])
    assert autosave.find_recoverable(root) == []


def test_empty_dead_sessions_are_swept():
    root = _root()
    crashed = autosave.Session.start(root)
    crashed.close(remove=False)
    assert autosave.find_recoverable(root) == []
    assert not crashed.folder.exists()


def test_fingerprint_tracks_pixels_and_layer_settings():
    doc = _painted_doc()
    first = autosave.fingerprint(doc)
    assert autosave.fingerprint(doc) == first
    doc.active_layer.pixels[0, 0] = (1, 2, 3, 255)
    doc.mark_dirty()
    second = autosave.fingerprint(doc)
    assert second != first
    doc.active_layer.visible = False
    assert autosave.fingerprint(doc) != second


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok    {name}")
            except Exception as exc:  # pragma: no cover - manual runner
                failures += 1
                print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failures else 0)
