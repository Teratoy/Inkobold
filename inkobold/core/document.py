from __future__ import annotations

import itertools
import json
import uuid
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from inkobold.core.animation import (
    DEFAULT_FPS,
    DEFAULT_ONION_OPACITY,
    AnimFrame,
    blank_frame,
)
from inkobold.core.image_meta import (
    COLOR_DEPTH_GRAY8,
    COLOR_DEPTH_RGB24,
    COLOR_DEPTH_RGBA32,
    DEFAULT_DPI,
    channel_max,
    constrain_pixels,
    convert_pixels,
    storage_dtype,
    to_display_u8,
    valid_color_depth,
)
from inkobold.core.layer import Layer


class Selection:
    """Binary mask matching document size; 255 = selected.

    ``revision`` changes whenever the mask is (re)assigned, so per-frame
    consumers (GPU upload, ``active`` scans) can skip work when nothing moved.
    Callers that mutate the mask array in place must call ``bump()``.
    """

    __slots__ = ("_mask", "revision", "_active_rev", "_active")

    # Process-unique so a revision identifies mask contents even across the
    # Selection objects that undo/redo swap in.
    _counter = itertools.count(1)

    def __init__(self, mask: Optional[np.ndarray] = None) -> None:
        self._mask: Optional[np.ndarray] = mask
        self.revision = next(Selection._counter)
        self._active_rev = -1
        self._active = False

    @property
    def mask(self) -> Optional[np.ndarray]:
        return self._mask

    @mask.setter
    def mask(self, value: Optional[np.ndarray]) -> None:
        self._mask = value
        self.revision = next(Selection._counter)

    def bump(self) -> None:
        """Mark the mask contents as changed (after in-place edits)."""
        self.revision = next(Selection._counter)

    def clear(self) -> None:
        self.mask = None

    def ensure(self, width: int, height: int) -> np.ndarray:
        if self._mask is None or self._mask.shape != (height, width):
            self.mask = np.zeros((height, width), dtype=np.uint8)
        else:
            self.bump()
        return self._mask

    @property
    def active(self) -> bool:
        if self._active_rev != self.revision:
            m = self._mask
            self._active = m is not None and bool(m.any())
            self._active_rev = self.revision
        return self._active

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        shape = None if self._mask is None else self._mask.shape
        return f"Selection(mask={shape}, revision={self.revision})"


@dataclass
class Document:
    width: int
    height: int
    layers: list[Layer] = field(default_factory=list)
    active_layer_index: int = 0
    path: Optional[Path] = None
    selection: Selection = field(default_factory=Selection)
    dirty: bool = False
    dpi: int = DEFAULT_DPI
    color_depth: int = COLOR_DEPTH_RGBA32
    # Animation
    frames: list[AnimFrame] = field(default_factory=list)
    current_frame_index: int = 0
    fps: float = DEFAULT_FPS
    onion_skin: bool = True
    onion_opacity: float = DEFAULT_ONION_OPACITY

    @classmethod
    def blank(
        cls,
        width: int,
        height: int,
        name: str = "Layer 1",
        dpi: int = DEFAULT_DPI,
        color_depth: int = COLOR_DEPTH_RGBA32,
    ) -> Document:
        depth = valid_color_depth(color_depth)
        doc = cls(width=width, height=height, dpi=dpi, color_depth=depth)
        frame = blank_frame(width, height, name="Frame 1", layer_name=name, color_depth=depth)
        doc.frames = [frame]
        doc.current_frame_index = 0
        doc.layers = frame.layers
        doc.active_layer_index = frame.active_layer_index
        return doc

    @property
    def active_layer(self) -> Layer:
        return self.layers[self.active_layer_index]

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    @property
    def current_frame(self) -> AnimFrame:
        return self.frames[self.current_frame_index]

    def _sync_frame_from_layers(self) -> None:
        """Write active-layer index back into the current AnimFrame."""
        if not self.frames:
            return
        fr = self.frames[self.current_frame_index]
        fr.layers = self.layers
        fr.active_layer_index = self.active_layer_index

    def _adopt_frame(self, index: int) -> None:
        """Point document.layers at frames[index]."""
        index = max(0, min(index, len(self.frames) - 1))
        self.current_frame_index = index
        fr = self.frames[index]
        self.layers = fr.layers
        self.active_layer_index = min(fr.active_layer_index, max(0, len(fr.layers) - 1))

    def set_current_frame(self, index: int) -> None:
        """Switch the editable cel."""
        if not self.frames or index == self.current_frame_index:
            return
        if not (0 <= index < len(self.frames)):
            return
        self._sync_frame_from_layers()
        self._adopt_frame(index)

    def add_frame(self, *, duplicate: bool = False) -> AnimFrame:
        """Insert a new frame after the current one (blank or clone of current)."""
        self._sync_frame_from_layers()
        n = len(self.frames)
        name = f"Frame {n + 1}"
        if duplicate and self.frames:
            frame = self.frames[self.current_frame_index].clone(name=name)
        else:
            frame = blank_frame(
                self.width,
                self.height,
                name=name,
                color_depth=self.color_depth,
            )
        insert_at = self.current_frame_index + 1 if self.frames else 0
        self.frames.insert(insert_at, frame)
        self._adopt_frame(insert_at)
        self.dirty = True
        return frame

    def delete_frame(self, index: int | None = None) -> None:
        """Remove a frame (keeps at least one)."""
        if len(self.frames) <= 1:
            return
        self._sync_frame_from_layers()
        idx = self.current_frame_index if index is None else int(index)
        if not (0 <= idx < len(self.frames)):
            return
        del self.frames[idx]
        new_idx = min(idx, len(self.frames) - 1)
        if idx < self.current_frame_index:
            new_idx = self.current_frame_index - 1
        elif idx == self.current_frame_index:
            new_idx = min(idx, len(self.frames) - 1)
        else:
            new_idx = self.current_frame_index
        self._adopt_frame(new_idx)
        self.dirty = True

    def move_frame(self, from_index: int, to_index: int) -> None:
        """Reorder frames in the timeline. Updates current_frame_index."""
        self._sync_frame_from_layers()
        n = len(self.frames)
        if not (0 <= from_index < n) or not (0 <= to_index < n) or from_index == to_index:
            return
        frame = self.frames.pop(from_index)
        self.frames.insert(to_index, frame)
        active = self.current_frame_index
        if active == from_index:
            self.current_frame_index = to_index
        elif from_index < active and to_index >= active:
            self.current_frame_index = active - 1
        elif from_index > active and to_index <= active:
            self.current_frame_index = active + 1
        self._adopt_frame(self.current_frame_index)
        self.dirty = True

    def previous_frame(self) -> AnimFrame | None:
        """Frame before the current one (for onion skin), or None."""
        if self.current_frame_index <= 0:
            return None
        return self.frames[self.current_frame_index - 1]

    def add_layer(self, name: str | None = None, fill: np.ndarray | None = None) -> Layer:
        layer = Layer(
            name=name or f"Layer {len(self.layers) + 1}",
            width=self.width,
            height=self.height,
            color_depth=self.color_depth,
        )
        if fill is not None:
            src = np.asarray(fill)
            if src.dtype != layer.pixels.dtype or src.ndim != 3:
                # Treat incoming buffers as 8-bit RGBA unless already matching
                if src.dtype != np.uint8:
                    src = to_display_u8(src)
                src = convert_pixels(src, COLOR_DEPTH_RGBA32, self.color_depth)
            h, w = src.shape[:2]
            layer.pixels[:h, :w] = src[:h, :w]
            constrain_pixels(layer.pixels, self.color_depth)
            layer.bump()
        self.layers.append(layer)
        self.active_layer_index = len(self.layers) - 1
        self.dirty = True
        return layer

    def delete_layer(self, index: int) -> None:
        if len(self.layers) <= 1:
            return
        del self.layers[index]
        self.active_layer_index = min(self.active_layer_index, len(self.layers) - 1)
        self._sync_frame_from_layers()
        self.dirty = True

    def merge_down(self, index: int | None = None) -> bool:
        """Merge ``layers[index]`` into the layer immediately below it.

        Composites with the same straight-alpha rules as ``flat_rgba`` (opacity
        and visibility baked in), writes into the lower layer, then removes the
        upper. Returns False if there is nothing to merge.
        """
        if index is None:
            index = self.active_layer_index
        if index <= 0 or index >= len(self.layers):
            return False
        below = self.layers[index - 1]
        above = self.layers[index]
        self._apply_composite_to_layer(below, [below, above])
        del self.layers[index]
        self.active_layer_index = index - 1
        self._sync_frame_from_layers()
        self.dirty = True
        return True

    def merge_all(self) -> bool:
        """Flatten the entire layer stack into a single layer. Returns False if already flat."""
        if len(self.layers) <= 1:
            return False
        target = self.layers[0]
        self._apply_composite_to_layer(target, list(self.layers))
        target.name = "Layer 1"
        del self.layers[1:]
        self.active_layer_index = 0
        self._sync_frame_from_layers()
        self.dirty = True
        return True

    def _apply_composite_to_layer(self, target: Layer, layers: list[Layer]) -> None:
        """Bake a transparent composite of ``layers`` into ``target`` at native depth."""
        out = self._composite_float(layers)
        # Compositor accumulates associated RGB; store straight RGBA so a later
        # opacity=1 draw matches the pre-merge flat result.
        alpha = out[..., 3:4]
        rgb = np.zeros_like(out[..., :3])
        np.divide(out[..., :3], alpha, out=rgb, where=alpha > 1e-6)
        straight = np.concatenate([rgb, alpha], axis=-1)
        max_v = float(channel_max(self.color_depth))
        target.pixels = np.clip(np.rint(straight * max_v), 0, max_v).astype(
            storage_dtype(self.color_depth)
        )
        constrain_pixels(target.pixels, self.color_depth)
        target.opacity = 1.0
        target.visible = True
        target.offset_x = 0
        target.offset_y = 0
        target.bump()

    def move_layer(self, from_index: int, to_index: int) -> None:
        """Move a layer in the stack (0 = bottom). Updates active_layer_index."""
        n = len(self.layers)
        if not (0 <= from_index < n) or not (0 <= to_index < n) or from_index == to_index:
            return
        layer = self.layers.pop(from_index)
        self.layers.insert(to_index, layer)
        active = self.active_layer_index
        if active == from_index:
            self.active_layer_index = to_index
        elif from_index < active and to_index >= active:
            self.active_layer_index = active - 1
        elif from_index > active and to_index <= active:
            self.active_layer_index = active + 1
        self._sync_frame_from_layers()
        self.dirty = True

    def mark_dirty(self, content: bool = True) -> None:
        self.dirty = True
        if content:
            self.active_layer.bump()

    def _transform_selection_mask(self, transform) -> None:
        """Apply a numpy image transform to the selection mask, if present."""
        mask = self.selection.mask
        if mask is None:
            return
        out = transform(mask)
        if out.shape != (self.height, self.width):
            # Keep document-sized: center crop / pad after 90° turns
            placed = np.zeros((self.height, self.width), dtype=mask.dtype)
            self._center_blit(out, placed)
            self.selection.mask = placed
        else:
            self.selection.mask = np.ascontiguousarray(out)

    @staticmethod
    def _center_blit(src: np.ndarray, dest: np.ndarray) -> None:
        """Copy src into dest centered; clips when src is larger."""
        sh, sw = src.shape[:2]
        dh, dw = dest.shape[:2]
        dy = (dh - sh) // 2
        dx = (dw - sw) // 2
        sy0 = max(0, -dy)
        sx0 = max(0, -dx)
        dy0 = max(0, dy)
        dx0 = max(0, dx)
        sy1 = sy0 + min(sh - sy0, dh - dy0)
        sx1 = sx0 + min(sw - sx0, dw - dx0)
        if sy1 > sy0 and sx1 > sx0:
            dest[dy0 : dy0 + (sy1 - sy0), dx0 : dx0 + (sx1 - sx0)] = src[sy0:sy1, sx0:sx1]

    def mirror_active_layer(self, *, horizontal: bool) -> None:
        """Reflect the active layer across a vertical (horizontal=True) or horizontal axis."""
        self._flip_active_layer(horizontal=horizontal)

    def flip_active_layer(self, *, horizontal: bool) -> None:
        """Flip the active layer left-right or top-bottom."""
        self._flip_active_layer(horizontal=horizontal)

    def _flip_active_layer(self, *, horizontal: bool) -> None:
        ly = self.active_layer
        ly.apply_offset()
        transform = np.fliplr if horizontal else np.flipud
        sel = self.selection
        if sel.active and sel.mask is not None:
            m = sel.mask > 0
            base = np.array(ly.pixels, copy=True)
            flo = np.array(ly.pixels, copy=True)
            base[m] = 0
            flo[~m] = 0
            flo = np.ascontiguousarray(transform(flo))
            out = base
            hit = flo[..., 3] > 0
            out[hit] = flo[hit]
            ly.pixels = out
            self._transform_selection_mask(transform)
        else:
            ly.pixels = np.ascontiguousarray(transform(ly.pixels))
            self._transform_selection_mask(transform)
        ly.bump()
        self.dirty = True

    def rotate_active_layer(self, turns_ccw: int) -> None:
        """Rotate the active layer by 90° × turns_ccw (1=CCW, 2=180°, 3=CW).

        Result stays document-sized: non-square 90° turns are centered and clipped.
        With an active selection, only selected pixels (and the mask) are rotated.
        """
        k = int(turns_ccw) % 4
        if k == 0:
            return
        ly = self.active_layer
        ly.apply_offset()

        def _place_rgba(src: np.ndarray) -> np.ndarray:
            rotated = np.rot90(src, k=k)
            if rotated.shape[:2] == (ly.height, ly.width):
                return np.ascontiguousarray(rotated)
            dest = np.zeros_like(src)
            self._center_blit(rotated, dest)
            return dest

        sel = self.selection
        if sel.active and sel.mask is not None:
            m = sel.mask > 0
            base = np.array(ly.pixels, copy=True)
            flo = np.array(ly.pixels, copy=True)
            base[m] = 0
            flo[~m] = 0
            flo = _place_rgba(flo)
            out = base
            hit = flo[..., 3] > 0
            out[hit] = flo[hit]
            ly.pixels = out
        else:
            ly.pixels = _place_rgba(ly.pixels)

        def _rot_mask(mask: np.ndarray) -> np.ndarray:
            return np.rot90(mask, k=k)

        self._transform_selection_mask(_rot_mask)
        ly.bump()
        self.dirty = True

    def selection_bounds(self) -> Optional[tuple[int, int, int, int]]:
        """Axis-aligned bounds of the selection as (x, y, width, height), or None."""
        if not self.selection.active or self.selection.mask is None:
            return None
        mask = self.selection.mask
        rows = np.flatnonzero(mask.any(axis=1))
        if rows.size == 0:
            return None
        cols = np.flatnonzero(mask.any(axis=0))
        x0, x1 = int(cols[0]), int(cols[-1])
        y0, y1 = int(rows[0]), int(rows[-1])
        return x0, y0, x1 - x0 + 1, y1 - y0 + 1

    def crop(self, x: int, y: int, width: int, height: int) -> None:
        """Crop every frame's layers (and selection) to a document rectangle.

        The rectangle is clamped to the current canvas. Offsets are baked first.
        """
        x = int(x)
        y = int(y)
        width = int(width)
        height = int(height)
        if self.width < 1 or self.height < 1:
            return
        x = max(0, min(x, self.width - 1))
        y = max(0, min(y, self.height - 1))
        width = max(1, min(width, self.width - x))
        height = max(1, min(height, self.height - y))
        if x == 0 and y == 0 and width == self.width and height == self.height:
            return

        self._sync_frame_from_layers()
        frames = self.frames or [AnimFrame(name="Frame 1", layers=list(self.layers))]
        for fr in frames:
            for ly in fr.layers:
                ly.apply_offset()
                ly.crop(x, y, width, height)

        mask = self.selection.mask
        if mask is not None:
            cropped = np.ascontiguousarray(mask[y : y + height, x : x + width])
            if cropped.any():
                self.selection.mask = cropped
            else:
                self.selection.clear()

        self.width = width
        self.height = height
        self.dirty = True

    def set_color_depth(self, depth: int) -> None:
        """Convert all layers on all frames to a new editing color depth."""
        new_depth = valid_color_depth(depth)
        old_depth = valid_color_depth(self.color_depth)
        self._sync_frame_from_layers()
        frames = self.frames or [AnimFrame(name="Frame 1", layers=list(self.layers))]

        def _convert_layer(ly: Layer) -> None:
            if new_depth == old_depth:
                constrain_pixels(ly.pixels, new_depth)
                ly.color_depth = new_depth
                ly.bump()
                return
            ly.pixels = convert_pixels(ly.pixels, old_depth, new_depth)
            ly.color_depth = new_depth
            constrain_pixels(ly.pixels, new_depth)
            ly.bump()

        for fr in frames:
            for ly in fr.layers:
                _convert_layer(ly)
        self.color_depth = new_depth
        self.dirty = True

    def _load_layer_entry(self, zf: zipfile.ZipFile, entry: dict, depth: int) -> Layer:
        ly = Layer(
            name=entry["name"],
            width=self.width,
            height=self.height,
            visible=bool(entry.get("visible", True)),
            opacity=float(entry.get("opacity", 1.0)),
            offset_x=int(entry.get("offset_x", 0)),
            offset_y=int(entry.get("offset_y", 0)),
            id=entry.get("id") or uuid.uuid4().hex[:10],
            color_depth=depth,
        )
        raw = zf.read(entry["file"])
        fname = str(entry["file"])
        if fname.endswith(".npy"):
            arr = np.load(BytesIO(raw))
            if arr.shape[-1] != 4:
                raise ValueError(f"bad layer array shape: {arr.shape}")
            if arr.dtype != ly.pixels.dtype:
                arr = convert_pixels(
                    arr if arr.dtype == np.uint8 else to_display_u8(arr),
                    COLOR_DEPTH_RGBA32 if arr.dtype == np.uint8 else depth,
                    depth,
                )
            h, w = arr.shape[:2]
            ly.pixels[:h, :w] = arr[:h, :w]
        else:
            img = Image.open(BytesIO(raw)).convert("RGBA")
            arr = np.array(img, dtype=np.uint8)
            if depth != COLOR_DEPTH_RGBA32:
                arr = convert_pixels(arr, COLOR_DEPTH_RGBA32, depth)
            h, w = arr.shape[:2]
            ly.pixels[:h, :w] = arr[:h, :w]
        constrain_pixels(ly.pixels, depth)
        # Normalize legacy saves that left a non-zero offset after Transform
        # so the full document stays drawable.
        ly.apply_offset()
        return ly

    @staticmethod
    def _layer_meta_entry(ly: Layer, fname: str) -> dict:
        return {
            "id": ly.id,
            "name": ly.name,
            "visible": ly.visible,
            "opacity": ly.opacity,
            "offset_x": ly.offset_x,
            "offset_y": ly.offset_y,
            "file": fname,
            "dtype": str(ly.pixels.dtype),
        }

    def _write_layer_pixels(self, zf: zipfile.ZipFile, ly: Layer, fname: str, use_u16: bool) -> None:
        if use_u16:
            buf = BytesIO()
            np.save(buf, np.ascontiguousarray(ly.pixels))
            zf.writestr(fname, buf.getvalue())
        else:
            buf = BytesIO()
            Image.fromarray(to_display_u8(ly.pixels), mode="RGBA").save(buf, format="PNG")
            zf.writestr(fname, buf.getvalue())

    def save(self, path: Path | None = None) -> Path:
        target = Path(path or self.path or "untitled.inkobold")
        if target.suffix.lower() not in {".inkobold", ".scribbler", ".zip"}:
            target = target.with_suffix(".inkobold")
        self._sync_frame_from_layers()
        use_u16 = storage_dtype(self.color_depth) == np.dtype(np.uint16)
        frames = self.frames or [
            AnimFrame(
                name="Frame 1",
                layers=list(self.layers),
                active_layer_index=self.active_layer_index,
            )
        ]
        meta = {
            "version": 3,
            "width": self.width,
            "height": self.height,
            "dpi": int(self.dpi),
            "color_depth": int(self.color_depth),
            "fps": float(self.fps),
            "onion_skin": bool(self.onion_skin),
            "onion_opacity": float(self.onion_opacity),
            "current_frame_index": int(self.current_frame_index),
            "active_layer_index": self.active_layer_index,
            "frames": [],
            # Legacy single-frame mirror (current frame) for older readers
            "layers": [],
        }
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for fi, fr in enumerate(frames):
                frame_meta = {
                    "name": fr.name,
                    "active_layer_index": int(fr.active_layer_index),
                    "layers": [],
                }
                for li, ly in enumerate(fr.layers):
                    ext = "npy" if use_u16 else "png"
                    fname = f"frames/{fi:03d}/layers/{li:03d}.{ext}"
                    self._write_layer_pixels(zf, ly, fname, use_u16)
                    frame_meta["layers"].append(self._layer_meta_entry(ly, fname))
                meta["frames"].append(frame_meta)
            # Also write current frame under legacy layers/ paths
            cur = frames[self.current_frame_index] if frames else None
            if cur is not None:
                for li, ly in enumerate(cur.layers):
                    ext = "npy" if use_u16 else "png"
                    fname = f"layers/{li:03d}.{ext}"
                    self._write_layer_pixels(zf, ly, fname, use_u16)
                    meta["layers"].append(self._layer_meta_entry(ly, fname))
            zf.writestr("document.json", json.dumps(meta, indent=2))
        self.path = target
        self.dirty = False
        return target

    @classmethod
    def _open_animated_gif(cls, path: Path, img: Image.Image) -> Document:
        """Load every GIF frame as an animation cel (Pillow composites disposal)."""
        width, height = img.size
        n_frames = int(getattr(img, "n_frames", 1))
        frames: list[AnimFrame] = []
        durations_ms: list[int] = []

        for i in range(n_frames):
            img.seek(i)
            duration = int(img.info.get("duration") or 0)
            durations_ms.append(duration if duration > 0 else 100)
            rgba = np.asarray(img.convert("RGBA"), dtype=np.uint8)
            fr = blank_frame(
                width,
                height,
                name=f"Frame {i + 1}",
                color_depth=COLOR_DEPTH_RGBA32,
            )
            if rgba.shape[0] != height or rgba.shape[1] != width:
                canvas = np.zeros((height, width, 4), dtype=np.uint8)
                hh = min(height, rgba.shape[0])
                ww = min(width, rgba.shape[1])
                canvas[:hh, :ww] = rgba[:hh, :ww]
                rgba = canvas
            fr.layers[0].pixels[:] = rgba
            fr.layers[0].bump()
            frames.append(fr)

        doc = cls(width=width, height=height, color_depth=COLOR_DEPTH_RGBA32)
        doc.frames = frames
        mean_ms = sum(durations_ms) / len(durations_ms)
        doc.fps = float(max(1, min(60, round(1000.0 / mean_ms))))
        doc._adopt_frame(0)
        doc.path = path
        doc.dirty = False
        return doc

    @classmethod
    def open(cls, path: Path) -> Document:
        path = Path(path)
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
            img = Image.open(path)
            if path.suffix.lower() == ".gif" and getattr(img, "n_frames", 1) > 1:
                return cls._open_animated_gif(path, img)
            img = img.convert("RGBA")
            arr = np.array(img, dtype=np.uint8)
            doc = cls.blank(img.width, img.height, name=path.stem)
            doc.layers[0].pixels[:] = arr
            doc.layers[0].bump()
            doc.path = path
            doc.dirty = False
            return doc

        with zipfile.ZipFile(path, "r") as zf:
            meta = json.loads(zf.read("document.json"))
            depth = valid_color_depth(int(meta.get("color_depth", COLOR_DEPTH_RGBA32)))
            doc = cls(
                width=int(meta["width"]),
                height=int(meta["height"]),
                dpi=int(meta.get("dpi", DEFAULT_DPI)),
                color_depth=depth,
            )
            doc.fps = float(meta.get("fps", DEFAULT_FPS))
            doc.onion_skin = bool(meta.get("onion_skin", True))
            doc.onion_opacity = float(meta.get("onion_opacity", DEFAULT_ONION_OPACITY))
            doc.frames = []

            frame_entries = meta.get("frames")
            if frame_entries:
                for i, fentry in enumerate(frame_entries):
                    fr = AnimFrame(
                        name=str(fentry.get("name", f"Frame {i + 1}")),
                        active_layer_index=int(fentry.get("active_layer_index", 0)),
                    )
                    for entry in fentry.get("layers", []):
                        fr.layers.append(doc._load_layer_entry(zf, entry, depth))
                    if not fr.layers:
                        fr = blank_frame(
                            doc.width,
                            doc.height,
                            name=fr.name,
                            color_depth=depth,
                        )
                    doc.frames.append(fr)
            else:
                # Legacy single-frame documents
                fr = AnimFrame(name="Frame 1")
                for entry in meta.get("layers", []):
                    fr.layers.append(doc._load_layer_entry(zf, entry, depth))
                if not fr.layers:
                    fr = blank_frame(doc.width, doc.height, color_depth=depth)
                fr.active_layer_index = int(meta.get("active_layer_index", 0))
                doc.frames.append(fr)

            idx = int(meta.get("current_frame_index", 0))
            doc._adopt_frame(idx)
            doc.path = path
            doc.dirty = False
            return doc

    def _composite_float(
        self,
        layers: list[Layer],
        background: tuple[int, int, int, int] | None = None,
    ) -> np.ndarray:
        """Straight-alpha composite → float32 RGBA in 0..1."""
        if background is not None:
            out = np.empty((self.height, self.width, 4), dtype=np.float32)
            out[..., 0] = background[0] / 255.0
            out[..., 1] = background[1] / 255.0
            out[..., 2] = background[2] / 255.0
            out[..., 3] = background[3] / 255.0
        else:
            out = np.zeros((self.height, self.width, 4), dtype=np.float32)

        for ly in layers:
            if not ly.visible or ly.opacity <= 0:
                continue
            if ly.pixels.dtype == np.uint16:
                max_v = 65535.0
            elif ly.pixels.dtype == np.uint8:
                max_v = 255.0
            else:
                max_v = float(np.iinfo(ly.pixels.dtype).max)
            ox, oy = int(ly.offset_x), int(ly.offset_y)
            sx0 = max(0, -ox)
            sy0 = max(0, -oy)
            dx0 = max(0, ox)
            dy0 = max(0, oy)
            sx1 = min(ly.width, self.width - ox)
            sy1 = min(ly.height, self.height - oy)
            if sx1 <= sx0 or sy1 <= sy0:
                continue
            # Convert only the visible tile (not the whole layer) to float.
            tile = ly.pixels[sy0:sy1, sx0:sx1].astype(np.float32) / max_v
            a = tile[..., 3:4] * ly.opacity
            dest = out[dy0 : dy0 + (sy1 - sy0), dx0 : dx0 + (sx1 - sx0)]
            dest[..., :3] = tile[..., :3] * a + dest[..., :3] * (1.0 - a)
            dest[..., 3:4] = a + dest[..., 3:4] * (1.0 - a)
        return out

    def flat_rgba(
        self,
        background: tuple[int, int, int, int] | None = None,
        *,
        layers: list[Layer] | None = None,
    ) -> np.ndarray:
        """CPU composite → uint8 RGBA (for preview / common export)."""
        src_layers = self.layers if layers is None else layers
        out = self._composite_float(src_layers, background)
        return np.clip(np.rint(out * 255.0), 0, 255).astype(np.uint8)

    def _animation_frames(self) -> list[AnimFrame]:
        self._sync_frame_from_layers()
        if self.frames:
            return list(self.frames)
        return [
            AnimFrame(
                name="Frame 1",
                layers=list(self.layers),
                active_layer_index=self.active_layer_index,
            )
        ]

    def iter_frame_rgba(
        self,
        background: tuple[int, int, int, int] | None = None,
    ):
        """Yield flattened uint8 RGBA arrays for each animation frame."""
        for fr in self._animation_frames():
            yield self.flat_rgba(background, layers=fr.layers)

    def export_animation_gif(self, path: Path) -> Path:
        """Flatten all frames into an animated GIF at document fps.

        Keeps transparent pixels (GIF binary transparency). Leave frames as
        RGBA so Pillow can assign a transparency index when quantizing.
        """
        path = Path(path)
        if path.suffix.lower() != ".gif":
            path = path.with_suffix(".gif")
        fps = max(1.0, float(self.fps))
        duration_ms = max(1, int(round(1000.0 / fps)))
        pil_frames: list[Image.Image] = [
            Image.fromarray(rgba, mode="RGBA") for rgba in self.iter_frame_rgba()
        ]
        if not pil_frames:
            raise ValueError("no frames to export")
        pil_frames[0].save(
            path,
            format="GIF",
            save_all=True,
            append_images=pil_frames[1:],
            duration=duration_ms,
            loop=0,
            optimize=False,
            disposal=2,
        )
        return path

    def export_animation_png_sequence(self, folder: Path) -> Path:
        """Write each frame as a separate PNG into ``folder`` (created if needed)."""
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        dpi = (max(1, int(self.dpi)), max(1, int(self.dpi)))
        frames = self._animation_frames()
        width = max(1, len(str(len(frames))))
        for i, fr in enumerate(frames):
            rgba = self.flat_rgba(layers=fr.layers)
            name = f"frame_{i + 1:0{width}d}.png"
            Image.fromarray(rgba, mode="RGBA").save(
                folder / name,
                format="PNG",
                optimize=True,
                dpi=dpi,
            )
        return folder

    def _layer_rgba_u8(self, ly: Layer) -> np.ndarray:
        """Document-sized RGBA uint8 for one layer (offset applied, opacity baked)."""
        out = np.zeros((self.height, self.width, 4), dtype=np.uint8)
        src = to_display_u8(ly.pixels)
        ox, oy = int(ly.offset_x), int(ly.offset_y)
        sx0 = max(0, -ox)
        sy0 = max(0, -oy)
        dx0 = max(0, ox)
        dy0 = max(0, oy)
        sx1 = min(ly.width, self.width - ox)
        sy1 = min(ly.height, self.height - oy)
        if sx1 <= sx0 or sy1 <= sy0:
            return out
        tile = src[sy0:sy1, sx0:sx1]
        opacity = float(ly.opacity)
        if opacity <= 0.0:
            return out
        if opacity < 1.0:
            tile = tile.copy()
            tile[..., 3] = np.clip(
                tile[..., 3].astype(np.float32) * opacity,
                0,
                255,
            ).astype(np.uint8)
        out[dy0 : dy0 + (sy1 - sy0), dx0 : dx0 + (sx1 - sx0)] = tile
        return out

    @staticmethod
    def _sanitize_layer_filename(name: str) -> str:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in (name or "").strip())
        safe = safe.strip("._")
        return safe or "layer"

    def export_layers_png(self, folder: Path) -> Path:
        """Write each layer of the current frame as a document-sized PNG into ``folder``."""
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        dpi = (max(1, int(self.dpi)), max(1, int(self.dpi)))
        layers = list(self.layers)
        width = max(1, len(str(max(len(layers) - 1, 0))))
        used: set[str] = set()
        for i, ly in enumerate(layers):
            stem = self._sanitize_layer_filename(ly.name)
            name = f"{i:0{width}d}_{stem}.png"
            if name in used:
                name = f"{i:0{width}d}_{stem}_{ly.id}.png"
            used.add(name)
            rgba = self._layer_rgba_u8(ly)
            Image.fromarray(rgba, mode="RGBA").save(
                folder / name,
                format="PNG",
                optimize=True,
                dpi=dpi,
            )
        return folder

    def export_image(self, path: Path) -> Path:
        """Flatten and write PNG / JPEG / WebP, honoring dpi and color_depth."""
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            path = path.with_suffix(".png")
            suffix = ".png"

        dpi = (max(1, int(self.dpi)), max(1, int(self.dpi)))
        depth = valid_color_depth(self.color_depth)

        if depth == COLOR_DEPTH_GRAY8:
            rgba = self.flat_rgba(background=(255, 255, 255, 255))
            img = Image.fromarray(rgba, mode="RGBA").convert("L")
            if suffix in {".jpg", ".jpeg"}:
                img.save(path, format="JPEG", quality=92, optimize=True, dpi=dpi)
            elif suffix == ".webp":
                img.save(path, format="WEBP", quality=90, method=4)
            else:
                img.save(path, format="PNG", optimize=True, dpi=dpi)
            return path

        if depth == COLOR_DEPTH_RGB24 or suffix in {".jpg", ".jpeg"}:
            rgba = self.flat_rgba(background=(255, 255, 255, 255))
            img = Image.fromarray(rgba, mode="RGBA").convert("RGB")
            if suffix in {".jpg", ".jpeg"}:
                img.save(path, format="JPEG", quality=92, optimize=True, dpi=dpi)
            elif suffix == ".webp":
                img.save(path, format="WEBP", quality=90, method=4)
            else:
                img.save(path, format="PNG", optimize=True, dpi=dpi)
            return path

        # RGBA32 and RGBA16 — external formats use 8-bit preview composite
        rgba = self.flat_rgba()
        img = Image.fromarray(rgba, mode="RGBA")
        if suffix == ".webp":
            img.save(path, format="WEBP", quality=90, method=4)
        else:
            img.save(path, format="PNG", optimize=True, dpi=dpi)
        return path
