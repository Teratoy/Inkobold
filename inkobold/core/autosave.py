"""Autosave and crash recovery, modelled on Blender's Auto Save.

Every few minutes each open document with unsaved changes is written to this
process's session folder (``state_dir()/autosave/<session>/``). The real file
is never touched. Saving a document, closing its tab or quitting normally
deletes its autosave, so whatever is left belongs to a session that crashed
(or was killed); File → Recover Autosave… lists those.

Each session holds an exclusive lock on ``session.lock`` while it runs. A
session folder whose lock can be taken is no longer running, which (unlike a
pid check) survives pid reuse and works the same on Linux and Windows.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Optional

from inkobold.core.document import Document, SaveJob
from inkobold.core.flavor import current
from inkobold.core.paths import state_dir

DEFAULT_MINUTES = 2  # Blender's default timer
MINUTES_MIN = 1
MINUTES_MAX = 60
# Blender retries this soon when a modal operator (here: a stroke) is running.
BUSY_RETRY_SECONDS = 10

LOCK_NAME = "session.lock"
SUFFIX = ".inkobold"
# document.json key with what recovery needs (ignored by Document.open)
META_KEY = "autosave"


def autosave_root() -> Path:
    return state_dir() / "autosave"


# ---- session lock ----


def _try_lock(fh: IO[bytes]) -> bool:
    try:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fh: IO[bytes]) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _session_is_live(folder: Path) -> bool:
    """True while the process that owns *folder* is still running."""
    lock = folder / LOCK_NAME
    if not lock.is_file():
        return False
    try:
        fh = open(lock, "r+b")
    except OSError:
        return True  # can't tell; leave it alone
    try:
        if _try_lock(fh):
            _unlock(fh)
            return False
        return True
    finally:
        fh.close()


class Session:
    """This process's autosave folder; holds its lock until :meth:`close`."""

    def __init__(self, folder: Path, lock: IO[bytes]) -> None:
        self.folder = folder
        self._lock = lock
        self._serial = 0

    @classmethod
    def start(cls, root: Optional[Path] = None) -> Session:
        root = Path(root) if root is not None else autosave_root()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        folder = root / f"{stamp}-{os.getpid()}"
        n = 1
        while folder.exists():
            n += 1
            folder = root / f"{stamp}-{os.getpid()}-{n}"
        folder.mkdir(parents=True)
        fh = open(folder / LOCK_NAME, "w+b")
        fh.write(b"\0")
        fh.flush()
        if not _try_lock(fh):
            fh.close()
            raise OSError(f"could not lock {folder / LOCK_NAME}")
        return cls(folder, fh)

    def new_path(self, title: str) -> Path:
        """A fresh file name in this session for a document called *title*."""
        self._serial += 1
        stem = "".join(c if c.isalnum() or c in "-_ " else "_" for c in Path(title).stem)
        return self.folder / f"{self._serial:03d}-{stem.strip() or 'untitled'}{SUFFIX}"

    def close(self, remove: bool = True) -> None:
        """Release the lock; *remove* deletes the folder (a normal quit)."""
        if self._lock is None:
            return
        _unlock(self._lock)
        self._lock.close()
        self._lock = None
        if remove:
            shutil.rmtree(self.folder, ignore_errors=True)


# ---- writing ----


def fingerprint(doc: Document) -> tuple:
    """Changes whenever anything an autosave would store changes."""
    layers = list(doc.layers)
    for fr in doc.frames:
        layers.extend(fr.layers)
    parts: list = [
        doc.width, doc.height, doc.dpi, doc.color_depth, doc.fps, doc.onion_skin,
        doc.onion_opacity, doc.current_frame_index, doc.active_layer_index,
        tuple(len(fr.layers) for fr in doc.frames),
    ]
    parts.extend(
        (ly.id, ly.revision, ly.name, ly.visible, ly.opacity, ly.offset_x, ly.offset_y)
        for ly in layers
    )
    rig = getattr(doc, "character", None)
    if rig is not None:
        parts.append(json.dumps(rig.to_dict(), sort_keys=True))
    return tuple(parts)


def prepare(doc: Document, target: Path, title: str) -> SaveJob:
    """Snapshot *doc* (main thread) into a job that writes its autosave to *target*."""
    job = doc.snapshot(target)
    info: dict = {
        "title": title,
        "source": str(doc.path) if doc.path else None,
        "saved_at": time.time(),
        "flavor": current().display_name,
    }
    rig = getattr(doc, "character", None)
    if rig is not None:
        info["character"] = rig.to_dict()
        info["character_folder"] = str(rig.folder) if rig.folder is not None else None
    job.meta[META_KEY] = info
    return job


def discard(path: Optional[Path]) -> None:
    if path is None:
        return
    try:
        Path(path).unlink()
    except OSError:
        pass


# ---- recovery ----


@dataclass
class Recoverable:
    """An autosave left behind by a session that is no longer running."""

    path: Path
    title: str
    source: Optional[Path]
    saved_at: float
    size: int
    info: dict


def _read_info(path: Path) -> Optional[dict]:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            meta = json.loads(zf.read("document.json"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None
    info = meta.get(META_KEY)
    return info if isinstance(info, dict) else {}


def _remove_if_empty(folder: Path) -> None:
    """Delete a dead session folder once no autosave is left in it."""
    try:
        if any(p.suffix == SUFFIX for p in folder.iterdir()):
            return
    except OSError:
        return
    shutil.rmtree(folder, ignore_errors=True)


def find_recoverable(root: Optional[Path] = None) -> list[Recoverable]:
    """Autosaves of dead sessions, newest first. Empty dead session folders are removed."""
    root = Path(root) if root is not None else autosave_root()
    found: list[Recoverable] = []
    try:
        folders = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return found
    for folder in folders:
        if _session_is_live(folder):
            continue
        files = sorted(folder.glob(f"*{SUFFIX}"))
        if not files:
            _remove_if_empty(folder)
            continue
        for path in files:
            info = _read_info(path)
            if info is None:
                continue  # half-written (crash during the write itself)
            try:
                st = path.stat()
            except OSError:
                continue
            source = info.get("source")
            found.append(
                Recoverable(
                    path=path,
                    title=str(info.get("title") or path.stem),
                    source=Path(source) if isinstance(source, str) and source else None,
                    saved_at=float(info.get("saved_at") or st.st_mtime),
                    size=int(st.st_size),
                    info=info,
                )
            )
    found.sort(key=lambda r: r.saved_at, reverse=True)
    return found


def delete_recoverable(item: Recoverable) -> None:
    discard(item.path)
    _remove_if_empty(item.path.parent)


def adopt(item: Recoverable, session: Optional[Session]) -> Path:
    """Move a recovered autosave into *session* so it stays safe until the user saves.

    Returns its new path (unchanged when there is no session to move it to).
    """
    if session is None:
        return item.path
    target = session.new_path(item.title)
    os.replace(item.path, target)
    _remove_if_empty(item.path.parent)
    return target


def open_recoverable(path: Path, info: dict) -> Document:
    """Load an autosave as an unsaved document that saves back to its original file."""
    doc = Document.open(path)
    source = info.get("source")
    doc.path = Path(source) if isinstance(source, str) and source else None
    rig_data = info.get("character")
    if isinstance(rig_data, dict):
        from inkobold.core.character import CharacterRig

        folder = info.get("character_folder")
        doc.character = CharacterRig.from_dict(
            rig_data, Path(folder) if isinstance(folder, str) and folder else None
        )
        doc.path = None
    doc.dirty = True
    return doc
