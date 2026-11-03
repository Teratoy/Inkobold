"""Goblin-shaped characters (GreenInkobold): a folder of part PNGs plus ``rig.json``.

The Python twin of the game's ``RigCharacter`` (GoblinEngineering
``scripts/player/rig_character.gd``). Every part is drawn in its assembled place on
one shared canvas; ``rig.json`` holds the joint pixels (left side and centre: the
right side mirrors about the canvas centre), the art top / foot sole lines, the hat
anchor and per-character tuning overrides for the procedural rig. Opening a folder
gives a document with one layer per part; saving writes the changed parts and the
JSON back, so the game picks them up on its next import.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from inkobold.core.document import Document
from inkobold.core.layer import Layer

FILE_NAME = "rig.json"
VERSION = 1
PARTS = ("head", "torso", "armL", "armR", "thighL", "thighR", "shinL", "shinR")
# Default layer stack bottom -> top, as the game draws them: each shin behind its thigh,
# the legs behind the torso, the head (and hat) over it, arms on top. A character's own
# order is rig.json "draw_order" (RigCharacter.draw_order), written from the layer order.
DRAW_ORDER = ("shinL", "thighL", "shinR", "thighR", "torso", "head", "armL", "armR")
# Points listed under "joints" in rig.json (canvas px). *_l points mirror to the right.
JOINTS = ("pelvis", "neck", "shoulder_l", "hand_l", "hip_l", "knee_l", "ankle_l", "cheek_l")
MIRRORED = frozenset(j for j in JOINTS if j.endswith("_l"))
GOBLIN_JOINTS: dict[str, tuple[float, float]] = {
    "pelvis": (200.0, 340.0),
    "neck": (200.0, 232.0),
    "shoulder_l": (115.0, 213.0),
    "hand_l": (45.0, 437.0),
    "hip_l": (147.0, 302.0),
    "knee_l": (122.0, 368.0),
    "ankle_l": (127.0, 480.0),
    "cheek_l": (108.0, 182.0),
}
JOINT_LABELS = {
    "pelvis": "Pelvis",
    "neck": "Neck",
    "shoulder_l": "Shoulder",
    "hand_l": "Hand",
    "hip_l": "Hip",
    "knee_l": "Knee",
    "ankle_l": "Ankle",
    "cheek_l": "Cheek (kiss)",
}
# Handle keys that are not joints.
HAT_CROWN = "hat_crown"
HAT_BRIM = "hat_brim"
ART_TOP = "art_top_y"
FOOT_SOLE = "foot_sole_y"
RIGHT_SUFFIX = "@r"
_TMP_PREFIX = "."
_TMP_SUFFIX = ".inkobold-tmp"


class CharacterError(Exception):
    """A character folder or document that cannot be opened or saved as asked."""


@dataclass(frozen=True)
class RigHandle:
    """One draggable point of the rig overlay (document px)."""

    key: str  # joint name, "<joint>@r" for a mirrored twin, or HAT_* / ART_TOP / FOOT_SOLE
    x: float
    y: float
    kind: str  # "center" | "left" | "right" | "anchor" | "line"
    label: str


@dataclass
class RigView:
    """How the rig overlay shows on a character canvas (per window, not saved)."""

    show: bool = True
    # Drag handles without holding Alt (otherwise Alt-drag, like the guides).
    edit: bool = False
    # Live rig preview pane next to the canvas.
    preview: bool = True
    hover: Optional[str] = None
    active: Optional[str] = None


@dataclass
class CharacterRig:
    """rig.json in memory, plus which part pixels were last written to disk."""

    folder: Optional[Path] = None
    canvas_width: float = 400.0
    canvas_height: float = 500.0
    art_top_y: float = 3.0
    foot_sole_y: float = 495.0
    joints: dict[str, tuple[float, float]] = field(default_factory=lambda: dict(GOBLIN_JOINTS))
    hat_crown: tuple[float, float] = (200.0, 44.0)
    hat_sit: float = 46.0
    hat_scale: float = 3.0
    params: dict = field(default_factory=dict)
    # Part stack bottom -> top (the game's sprite order; the hat draws right after the head).
    draw_order: tuple[str, ...] = DRAW_ORDER
    # Top-level rig.json keys this version does not know, written back untouched.
    extra: dict = field(default_factory=dict)
    # part -> (layer id, revision, offset x, offset y) as last read from / written to disk
    saved: dict[str, tuple] = field(default_factory=dict, compare=False, repr=False)

    @property
    def name(self) -> str:
        return self.folder.name if self.folder is not None else "character"

    # ---- rig.json ----

    @classmethod
    def from_dict(cls, data: dict, folder: Optional[Path] = None) -> CharacterRig:
        rig = cls(folder=folder)
        rig.apply_dict(data)
        return rig

    def apply_dict(self, data: dict) -> None:
        """Fill from a parsed rig.json; missing keys keep their current value."""
        if not isinstance(data, dict):
            raise CharacterError("rig.json is not a JSON object")
        known = {"version", "canvas", "art_top_y", "foot_sole_y", "joints", "hat", "params", "draw_order"}
        self.extra = {k: v for k, v in data.items() if k not in known}
        canvas = data.get("canvas")
        if isinstance(canvas, list) and len(canvas) == 2:
            self.canvas_width = _num(canvas[0], self.canvas_width)
            self.canvas_height = _num(canvas[1], self.canvas_height)
        self.art_top_y = _num(data.get("art_top_y"), self.art_top_y)
        self.foot_sole_y = _num(data.get("foot_sole_y"), self.foot_sole_y)
        joints = data.get("joints")
        if isinstance(joints, dict):
            merged = dict(self.joints)
            for key in JOINTS:
                pt = _point(joints.get(key))
                if pt is not None:
                    merged[key] = pt
            self.joints = merged
        hat = data.get("hat")
        if isinstance(hat, dict):
            crown = _point(hat.get("crown"))
            if crown is not None:
                self.hat_crown = crown
            self.hat_sit = _num(hat.get("sit"), self.hat_sit)
            self.hat_scale = _num(hat.get("scale"), self.hat_scale)
        params = data.get("params")
        if isinstance(params, dict):
            self.params = dict(params)
        order = data.get("draw_order")
        if order is not None:
            # Like the game: every part exactly once, else the default stack.
            valid = isinstance(order, list) and sorted(map(str, order)) == sorted(PARTS)
            self.draw_order = tuple(str(p) for p in order) if valid else DRAW_ORDER
        elif "version" in data:
            # A whole rig.json (or history snapshot) without one means the default stack.
            self.draw_order = DRAW_ORDER

    def to_dict(self) -> dict:
        out: dict = {
            "version": VERSION,
            "canvas": [_clean(self.canvas_width), _clean(self.canvas_height)],
            "art_top_y": _clean(self.art_top_y),
            "foot_sole_y": _clean(self.foot_sole_y),
            "joints": {k: [_clean(self.joints[k][0]), _clean(self.joints[k][1])] for k in JOINTS},
            "hat": {
                "crown": [_clean(self.hat_crown[0]), _clean(self.hat_crown[1])],
                "sit": _clean(self.hat_sit),
                "scale": _clean(self.hat_scale),
            },
            "params": dict(self.params),
        }
        if tuple(self.draw_order) != DRAW_ORDER:
            out["draw_order"] = list(self.draw_order)
        out.update(self.extra)
        return out

    def to_json(self) -> str:
        """rig.json text in the game's style: tabs, one joint per line, hat on one line."""

        def inline(v) -> str:
            return json.dumps(v, ensure_ascii=False, separators=(", ", ": "))

        def block(d: dict, indent: str) -> str:
            if not d:
                return "{}"
            rows = [f'{indent}\t{json.dumps(str(k))}: {inline(v)}' for k, v in d.items()]
            return "{\n" + ",\n".join(rows) + "\n" + indent + "}"

        data = self.to_dict()
        rows = []
        for key, value in data.items():
            if key in ("joints", "params") and isinstance(value, dict):
                text = block(value, "\t")
            else:
                text = inline(value)
            rows.append(f"\t{json.dumps(key)}: {text}")
        return "{\n" + ",\n".join(rows) + "\n}\n"

    @classmethod
    def load(cls, folder: Path) -> CharacterRig:
        """Read ``folder/rig.json``; without one, the goblin's joints on the folder's art.

        A rig.json that does not parse raises (rather than silently falling back),
        so a save can never overwrite a file the user still needs to fix.
        """
        folder = Path(folder)
        path = folder / FILE_NAME
        if not path.is_file():
            return cls(folder=folder)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CharacterError(f"{path} could not be read: {exc}") from exc
        return cls.from_dict(data, folder)

    # ---- geometry ----

    def mirror(self, p: tuple[float, float]) -> tuple[float, float]:
        return (self.canvas_width - p[0], p[1])

    def point(self, key: str) -> tuple[float, float]:
        """Document position of a handle key (see RigHandle.key)."""
        if key.endswith(RIGHT_SUFFIX):
            return self.mirror(self.joints[key[: -len(RIGHT_SUFFIX)]])
        if key in self.joints:
            return self.joints[key]
        if key == HAT_CROWN:
            return self.hat_crown
        if key == HAT_BRIM:
            return (self.hat_crown[0], self.hat_crown[1] + self.hat_sit)
        if key == ART_TOP:
            return (self.canvas_width * 0.5, self.art_top_y)
        if key == FOOT_SOLE:
            return (self.canvas_width * 0.5, self.foot_sole_y)
        raise KeyError(key)

    def handles(self) -> list[RigHandle]:
        out: list[RigHandle] = []
        for key in JOINTS:
            x, y = self.joints[key]
            label = JOINT_LABELS[key]
            if key in MIRRORED:
                out.append(RigHandle(key, x, y, "left", f"{label} L"))
                rx, ry = self.mirror((x, y))
                out.append(RigHandle(key + RIGHT_SUFFIX, rx, ry, "right", f"{label} R"))
            else:
                out.append(RigHandle(key, x, y, "center", label))
        cx, cy = self.hat_crown
        out.append(RigHandle(HAT_CROWN, cx, cy, "anchor", "Hat crown"))
        out.append(RigHandle(HAT_BRIM, cx, cy + self.hat_sit, "anchor", "Hat brim"))
        out.append(RigHandle(ART_TOP, self.canvas_width * 0.5, self.art_top_y, "line", "Art top"))
        out.append(RigHandle(FOOT_SOLE, self.canvas_width * 0.5, self.foot_sole_y, "line", "Foot sole"))
        return out

    def move_handle(self, key: str, x: float, y: float) -> None:
        """Drag a handle to (x, y) document px (rounded to whole pixels)."""
        x, y = float(round(x)), float(round(y))
        if key.endswith(RIGHT_SUFFIX):
            self.joints = {**self.joints, key[: -len(RIGHT_SUFFIX)]: self.mirror((x, y))}
        elif key in JOINTS:
            self.joints = {**self.joints, key: (x, y)}
        elif key == HAT_CROWN:
            self.hat_crown = (x, y)
        elif key == HAT_BRIM:
            self.hat_sit = y - self.hat_crown[1]
        elif key == ART_TOP:
            self.art_top_y = min(y, self.foot_sole_y - 1.0)
        elif key == FOOT_SOLE:
            self.foot_sole_y = max(y, self.art_top_y + 1.0)
        else:
            raise KeyError(key)

    def bones(self) -> list[tuple[tuple[float, float], tuple[float, float], str]]:
        """Overlay segments (start, end, kind): "bone" for limbs, "link" for attachments."""
        j = self.joints
        segs: list[tuple[tuple[float, float], tuple[float, float], str]] = [
            (j["pelvis"], j["neck"], "bone"),
        ]
        for side in (lambda p: p, self.mirror):
            hip, knee, ankle = side(j["hip_l"]), side(j["knee_l"]), side(j["ankle_l"])
            shoulder, hand = side(j["shoulder_l"]), side(j["hand_l"])
            segs += [
                (j["pelvis"], hip, "link"),
                (hip, knee, "bone"),
                (knee, ankle, "bone"),
                (j["neck"], shoulder, "link"),
                (shoulder, hand, "bone"),
            ]
        return segs


# ---- folders ----


def is_character_folder(path: Path) -> bool:
    """A folder with a rig.json, or with every part PNG."""
    path = Path(path)
    if not path.is_dir():
        return False
    if (path / FILE_NAME).is_file():
        return True
    return all((path / f"{part}.png").is_file() for part in PARTS)


def character_folder_for(path: Optional[Path]) -> Optional[Path]:
    """The character folder a selected file or folder belongs to, if any."""
    if path is None:
        return None
    path = Path(path)
    folder = path if path.is_dir() else path.parent
    return folder if is_character_folder(folder) else None


def open_character(folder: Path) -> tuple[Document, list[str]]:
    """Load a character folder as a document (one layer per part, game draw order).

    Returns the document and human-readable notes (missing parts started blank,
    parts of another size padded / cropped to the canvas).
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise CharacterError(f"{folder} is not a folder")
    rig = CharacterRig.load(folder)
    has_json = (folder / FILE_NAME).is_file()
    images: dict[str, np.ndarray] = {}
    for part in PARTS:
        path = folder / f"{part}.png"
        if path.is_file():
            try:
                with Image.open(path) as img:
                    images[part] = np.array(img.convert("RGBA"), dtype=np.uint8)
            except OSError as exc:
                raise CharacterError(f"{path} could not be read: {exc}") from exc
    if not has_json and images:
        # No rig.json yet: the canvas is the art's size.
        first = images[next(p for p in PARTS if p in images)]
        rig.canvas_height, rig.canvas_width = float(first.shape[0]), float(first.shape[1])
    width, height = int(round(rig.canvas_width)), int(round(rig.canvas_height))
    if width < 1 or height < 1:
        raise CharacterError(f"{folder / FILE_NAME}: canvas {width}×{height} is empty")

    notes: list[str] = []
    doc = Document.blank(width, height, name=DRAW_ORDER[0])
    layers: list[Layer] = []
    for part in rig.draw_order:
        ly = Layer(name=part, width=width, height=height, color_depth=doc.color_depth)
        arr = images.get(part)
        if arr is None:
            notes.append(f"{part}.png is missing: started blank")
        else:
            h, w = arr.shape[:2]
            if (w, h) != (width, height):
                notes.append(f"{part}.png is {w}×{h}, not {width}×{height}: padded / cropped")
            ly.pixels[: min(h, height), : min(w, width)] = arr[:height, :width]
            ly.bump()
        layers.append(ly)
    doc.frames[0].layers = layers
    doc.layers = layers
    doc.active_layer_index = DRAW_ORDER.index("torso")
    doc.frames[0].active_layer_index = doc.active_layer_index
    doc.character = rig
    doc.path = None
    doc.dirty = False
    rig.saved = {
        ly.name: _layer_state(ly) for ly in layers if ly.name in images
    }
    return doc, notes


def part_layers(doc: Document) -> tuple[dict[str, Layer], list[str], list[str]]:
    """(part -> layer, missing part names, extra layer names) for the current frame."""
    found: dict[str, Layer] = {}
    extra: list[str] = []
    dupes: list[str] = []
    for ly in doc.layers:
        if ly.name in PARTS:
            if ly.name in found:
                dupes.append(ly.name)
            else:
                found[ly.name] = ly
        else:
            extra.append(ly.name)
    if dupes:
        raise CharacterError("More than one layer named " + ", ".join(sorted(set(dupes))))
    missing = [p for p in PARTS if p not in found]
    return found, missing, extra


def save_character(doc: Document, folder: Optional[Path] = None) -> list[str]:
    """Write the part layers that changed and rig.json into the character folder.

    ``folder`` defaults to the folder the character came from; another folder
    writes every part (Save As). Each file is replaced atomically. Returns notes
    (layers that are not parts and were skipped). Raises CharacterError when a part
    layer is missing, so a save never leaves the game with half a character.
    """
    rig = getattr(doc, "character", None)
    if rig is None:
        raise CharacterError("This canvas is not a character")
    target = Path(folder) if folder is not None else rig.folder
    if target is None:
        raise CharacterError("Choose a folder for the character")
    doc._sync_frame_from_layers()
    found, missing, extra = part_layers(doc)
    if missing:
        raise CharacterError(
            "Rename or add layers for the missing parts: " + ", ".join(missing)
        )
    # The part layers' stack (extras skipped) is the game's draw order.
    rig.draw_order = tuple(ly.name for ly in doc.layers if ly.name in PARTS)
    if (doc.width, doc.height) != (int(round(rig.canvas_width)), int(round(rig.canvas_height))):
        rig.canvas_width, rig.canvas_height = float(doc.width), float(doc.height)
    target.mkdir(parents=True, exist_ok=True)
    same_folder = rig.folder is not None and target.resolve() == rig.folder.resolve()
    written: dict[str, tuple] = {}
    for part in PARTS:
        ly = found[part]
        state = _layer_state(ly)
        path = target / f"{part}.png"
        if same_folder and path.is_file() and rig.saved.get(part) == state:
            continue
        rgba = doc._layer_rgba_u8(ly, bake_opacity=False)
        # Undo / redo rebuild layers, so a new revision may still hold the pixels on
        # disk: never re-encode an unchanged part (it would churn the game repo).
        if path.is_file() and _same_pixels(path, rgba):
            written[part] = state
            continue
        _atomic_write(path, lambda tmp, a=rgba: Image.fromarray(a, mode="RGBA").save(tmp, format="PNG", optimize=True))
        written[part] = state
    json_path = target / FILE_NAME
    text = rig.to_json()
    try:
        current = json_path.read_text(encoding="utf-8") if json_path.is_file() else None
    except OSError:
        current = None
    if current != text:
        _atomic_write(json_path, lambda tmp: Path(tmp).write_text(text, encoding="utf-8"))
    if same_folder:
        rig.saved.update(written)
    else:
        rig.folder = target
        rig.saved = written
    doc.dirty = False
    notes = []
    if extra:
        notes.append("Not saved (not a part): " + ", ".join(extra))
    return notes


def create_character(
    folder: Path,
    *,
    template: Optional[Path] = None,
    width: int = 400,
    height: int = 500,
) -> Path:
    """Make a new character folder: copies of ``template``'s parts and rig.json, or blank
    parts with the goblin's joints. The folder must not exist yet (or be empty)."""
    folder = Path(folder)
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise CharacterError(f"{folder} already exists")
    if template is not None:
        rig = CharacterRig.load(template)
        rig.params = dict(rig.params)
    else:
        rig = CharacterRig(canvas_width=float(width), canvas_height=float(height))
    folder.mkdir(parents=True, exist_ok=True)
    w, h = int(round(rig.canvas_width)), int(round(rig.canvas_height))
    for part in PARTS:
        src = template / f"{part}.png" if template is not None else None
        if src is not None and src.is_file():
            shutil.copyfile(src, folder / f"{part}.png")
        else:
            Image.new("RGBA", (w, h), (0, 0, 0, 0)).save(folder / f"{part}.png", format="PNG")
    rig.folder = folder
    (folder / FILE_NAME).write_text(rig.to_json(), encoding="utf-8")
    return folder


# ---- helpers ----


def _layer_state(ly: Layer) -> tuple:
    return (ly.id, ly.revision, int(ly.offset_x), int(ly.offset_y))


def _same_pixels(path: Path, rgba: np.ndarray) -> bool:
    try:
        with Image.open(path) as img:
            disk = np.asarray(img.convert("RGBA"))
    except OSError:
        return False
    if disk.shape != rgba.shape:
        return False
    # Fully transparent pixels may differ in their hidden color; compare what shows.
    visible = (disk[..., 3] > 0) | (rgba[..., 3] > 0)
    return bool(np.array_equal(disk[visible], rgba[visible]))


def _atomic_write(path: Path, write) -> None:
    tmp = path.with_name(f"{_TMP_PREFIX}{path.name}{_TMP_SUFFIX}")
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _num(v, fallback: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return float(fallback)
    return float(v)


def _point(v) -> Optional[tuple[float, float]]:
    if isinstance(v, list) and len(v) == 2 and all(
        isinstance(c, (int, float)) and not isinstance(c, bool) for c in v
    ):
        return (float(v[0]), float(v[1]))
    return None


def _clean(v: float):
    """Whole numbers as ints, so rig.json keeps the game's ``[200, 340]`` style."""
    f = float(v)
    return int(f) if f.is_integer() else round(f, 4)
