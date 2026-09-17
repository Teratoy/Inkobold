"""Document undo/redo history (zlib-compressed layer snapshots)."""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from inkobold.core.animation import DEFAULT_FPS, DEFAULT_ONION_OPACITY, AnimFrame, blank_frame
from inkobold.core.document import Document, Selection
from inkobold.core.layer import Layer

DEFAULT_HISTORY_STEPS = 124


@dataclass
class _LayerSnap:
    id: str
    name: str
    visible: bool
    opacity: float
    offset_x: int
    offset_y: int
    width: int
    height: int
    pixels_z: bytes
    dtype: str = "uint8"


@dataclass
class _FrameSnap:
    name: str
    active_layer_index: int
    layers: list[_LayerSnap]


@dataclass
class _DocSnap:
    width: int
    height: int
    active_layer_index: int
    layers: list[_LayerSnap]
    selection_z: Optional[bytes] = None
    color_depth: int = 32
    dpi: int = 72
    frames: list[_FrameSnap] = field(default_factory=list)
    current_frame_index: int = 0
    fps: float = DEFAULT_FPS
    onion_skin: bool = True
    onion_opacity: float = DEFAULT_ONION_OPACITY


@dataclass
class History:
    max_steps: int = DEFAULT_HISTORY_STEPS
    _undo: list[_DocSnap] = field(default_factory=list)
    _redo: list[_DocSnap] = field(default_factory=list)
    _busy: bool = False  # suppress pushes while restoring
    # (layer.id, layer.revision) -> compressed snapshot. Layer revisions are
    # process-unique (see Layer.bump), so a hit means the pixels are unchanged
    # since that snapshot was taken and the zlib work can be skipped. Pruned to
    # the layers referenced by the newest snapshot on every push.
    _layer_cache: dict[tuple[str, int], _LayerSnap] = field(default_factory=dict, repr=False)
    # (selection.revision, compressed mask) for the most recent snapshot.
    _sel_cache: Optional[tuple[int, bytes]] = field(default=None, repr=False)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._layer_cache.clear()
        self._sel_cache = None

    def set_max_steps(self, steps: int) -> None:
        self.max_steps = max(1, int(steps))
        if len(self._undo) > self.max_steps:
            del self._undo[: len(self._undo) - self.max_steps]
        if len(self._redo) > self.max_steps:
            del self._redo[: len(self._redo) - self.max_steps]

    def push(self, doc: Document) -> None:
        if self._busy or doc is None:
            return
        self._undo.append(self._snapshot(doc))
        if len(self._undo) > self.max_steps:
            del self._undo[: len(self._undo) - self.max_steps]
        self._redo.clear()

    def discard_last_push(self) -> bool:
        """Drop the newest undo snapshot without restoring (e.g. cancelled preview)."""
        if not self._undo:
            return False
        self._undo.pop()
        return True

    def undo(self, doc: Document) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._snapshot(doc))
        snap = self._undo.pop()
        self._restore(doc, snap)
        return True

    def redo(self, doc: Document) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._snapshot(doc))
        snap = self._redo.pop()
        self._restore(doc, snap)
        return True

    def _snap_layer(self, ly: Layer, fresh: dict[tuple[str, int], _LayerSnap]) -> _LayerSnap:
        key = (ly.id, ly.revision)
        cached = self._layer_cache.get(key)
        if cached is not None:
            # Pixels unchanged since the cached snapshot; only metadata may differ.
            if (
                cached.name != ly.name
                or cached.visible != ly.visible
                or cached.opacity != ly.opacity
                or cached.offset_x != ly.offset_x
                or cached.offset_y != ly.offset_y
            ):
                cached = _LayerSnap(
                    id=ly.id,
                    name=ly.name,
                    visible=ly.visible,
                    opacity=ly.opacity,
                    offset_x=ly.offset_x,
                    offset_y=ly.offset_y,
                    width=cached.width,
                    height=cached.height,
                    pixels_z=cached.pixels_z,
                    dtype=cached.dtype,
                )
            fresh[key] = cached
            return cached
        px = np.ascontiguousarray(ly.pixels)
        snap = _LayerSnap(
            id=ly.id,
            name=ly.name,
            visible=ly.visible,
            opacity=ly.opacity,
            offset_x=ly.offset_x,
            offset_y=ly.offset_y,
            width=ly.width,
            height=ly.height,
            pixels_z=zlib.compress(px.tobytes(), 1),
            dtype=str(px.dtype),
        )
        fresh[key] = snap
        return snap

    def _restore_layer(self, ls: _LayerSnap, color_depth: int) -> Layer:
        ly = Layer(
            name=ls.name,
            width=ls.width,
            height=ls.height,
            visible=ls.visible,
            opacity=ls.opacity,
            offset_x=ls.offset_x,
            offset_y=ls.offset_y,
            id=ls.id,
            color_depth=color_depth,
        )
        raw = zlib.decompress(ls.pixels_z)
        dtype = np.dtype(getattr(ls, "dtype", "uint8"))
        ly.pixels = np.frombuffer(raw, dtype=dtype).reshape(ls.height, ls.width, 4).copy()
        ly.bump()
        # The restored layer's pixels are exactly this snapshot: remember that so
        # the next push (e.g. redo bookkeeping) doesn't recompress them.
        self._layer_cache[(ly.id, ly.revision)] = ls
        return ly

    def _snapshot(self, doc: Document) -> _DocSnap:
        doc._sync_frame_from_layers()
        frames_src = doc.frames or [
            AnimFrame(
                name="Frame 1",
                layers=list(doc.layers),
                active_layer_index=doc.active_layer_index,
            )
        ]
        fresh: dict[tuple[str, int], _LayerSnap] = {}
        frame_snaps: list[_FrameSnap] = []
        for fr in frames_src:
            frame_snaps.append(
                _FrameSnap(
                    name=fr.name,
                    active_layer_index=fr.active_layer_index,
                    layers=[self._snap_layer(ly, fresh) for ly in fr.layers],
                )
            )
        # Keep only what the document currently references (bounded memory).
        self._layer_cache = fresh
        # Legacy flat layers = current frame (compat with older snap readers)
        cur = frame_snaps[doc.current_frame_index] if frame_snaps else None
        layers = list(cur.layers) if cur is not None else []
        sel_z = None
        sel = doc.selection
        if sel.mask is not None:
            if self._sel_cache is not None and self._sel_cache[0] == sel.revision:
                sel_z = self._sel_cache[1]
            else:
                sel_z = zlib.compress(np.ascontiguousarray(sel.mask).tobytes(), 1)
                self._sel_cache = (sel.revision, sel_z)
        return _DocSnap(
            width=doc.width,
            height=doc.height,
            active_layer_index=doc.active_layer_index,
            layers=layers,
            selection_z=sel_z,
            color_depth=int(doc.color_depth),
            dpi=int(doc.dpi),
            frames=frame_snaps,
            current_frame_index=int(doc.current_frame_index),
            fps=float(doc.fps),
            onion_skin=bool(doc.onion_skin),
            onion_opacity=float(doc.onion_opacity),
        )

    def _restore(self, doc: Document, snap: _DocSnap) -> None:
        self._busy = True
        try:
            doc.width = snap.width
            doc.height = snap.height
            doc.color_depth = int(getattr(snap, "color_depth", doc.color_depth))
            doc.dpi = int(getattr(snap, "dpi", doc.dpi))
            doc.fps = float(getattr(snap, "fps", DEFAULT_FPS))
            doc.onion_skin = bool(getattr(snap, "onion_skin", True))
            doc.onion_opacity = float(getattr(snap, "onion_opacity", DEFAULT_ONION_OPACITY))

            frame_snaps = getattr(snap, "frames", None) or []
            if not frame_snaps and snap.layers:
                frame_snaps = [
                    _FrameSnap(
                        name="Frame 1",
                        active_layer_index=snap.active_layer_index,
                        layers=list(snap.layers),
                    )
                ]

            doc.frames = []
            for fs in frame_snaps:
                fr = AnimFrame(
                    name=fs.name,
                    active_layer_index=fs.active_layer_index,
                    layers=[self._restore_layer(ls, doc.color_depth) for ls in fs.layers],
                )
                if not fr.layers:
                    fr = blank_frame(
                        doc.width,
                        doc.height,
                        name=fs.name,
                        color_depth=doc.color_depth,
                    )
                doc.frames.append(fr)

            if not doc.frames:
                doc.frames = [blank_frame(doc.width, doc.height, color_depth=doc.color_depth)]

            idx = int(getattr(snap, "current_frame_index", 0))
            doc._adopt_frame(idx)

            if snap.selection_z is None:
                doc.selection = Selection()
            else:
                raw = zlib.decompress(snap.selection_z)
                mask = np.frombuffer(raw, dtype=np.uint8).reshape(snap.height, snap.width).copy()
                doc.selection = Selection(mask=mask)
                self._sel_cache = (doc.selection.revision, snap.selection_z)
            doc.dirty = True
        finally:
            self._busy = False
