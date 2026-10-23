"""GreenInkobold characters: rig.json <-> CharacterRig, folder <-> document (no GTK / OpenGL).

Run with ``python -m pytest tests`` or ``python tests/test_character.py``.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inkobold.core import character as ch  # noqa: E402
from inkobold.core.history import History  # noqa: E402

W, H = 40, 50
GAME_GOBLIN = Path.home() / "Toyfactory" / "GoblinEngineering" / "visual assets" / "player"


def _folder(parts=ch.PARTS, rig: dict | None = None) -> Path:
    """Temp character folder: each part a solid square at its own spot, plus rig.json."""
    folder = Path(tempfile.mkdtemp(prefix="greeninkobold-char-")) / "Grub"
    folder.mkdir()
    for i, part in enumerate(parts):
        arr = np.zeros((H, W, 4), dtype=np.uint8)
        arr[i * 5 : i * 5 + 4, i * 4 : i * 4 + 4] = (10 * i, 200, 30, 255)
        Image.fromarray(arr, mode="RGBA").save(folder / f"{part}.png")
    data = ch.CharacterRig(canvas_width=W, canvas_height=H).to_dict()
    data.update(rig or {})
    (folder / ch.FILE_NAME).write_text(json.dumps(data), encoding="utf-8")
    return folder


def _png(path: Path) -> np.ndarray:
    with Image.open(path) as img:
        return np.array(img.convert("RGBA"))


def test_goblin_rig_json_round_trips_byte_for_byte():
    path = GAME_GOBLIN / ch.FILE_NAME
    if not path.is_file():
        return  # game checkout not here
    rig = ch.CharacterRig.load(GAME_GOBLIN)
    assert rig.to_json() == path.read_text(encoding="utf-8")
    assert rig.joints == ch.GOBLIN_JOINTS


def test_defaults_match_the_game_goblin():
    rig = ch.CharacterRig()
    d = rig.to_dict()
    assert d["canvas"] == [400, 500] and d["art_top_y"] == 3 and d["foot_sole_y"] == 495
    assert d["joints"]["neck"] == [200, 232] and d["joints"]["cheek_l"] == [108, 182]
    assert d["hat"] == {"crown": [200, 44], "sit": 46, "scale": 3}


def test_unknown_keys_and_params_survive():
    rig = ch.CharacterRig.from_dict({"joints": {"neck": [1, 2]}, "params": {"stride_length": 33.5}, "future": [1]})
    out = json.loads(rig.to_json())
    assert out["joints"]["neck"] == [1, 2]
    assert out["joints"]["pelvis"] == [200, 340]
    assert out["params"] == {"stride_length": 33.5}
    assert out["future"] == [1]


def test_broken_json_refuses_instead_of_falling_back():
    folder = _folder()
    (folder / ch.FILE_NAME).write_text("{ nope", encoding="utf-8")
    try:
        ch.CharacterRig.load(folder)
    except ch.CharacterError:
        return
    raise AssertionError("broken rig.json loaded")


def test_mirrored_handles_move_the_left_joint():
    rig = ch.CharacterRig()
    rig.move_handle("shoulder_l" + ch.RIGHT_SUFFIX, 290.4, 220.6)
    assert rig.joints["shoulder_l"] == (110.0, 221.0)
    assert rig.point("shoulder_l" + ch.RIGHT_SUFFIX) == (290.0, 221.0)
    rig.move_handle(ch.HAT_BRIM, 0, 100)
    assert rig.hat_sit == 100 - rig.hat_crown[1]
    rig.move_handle(ch.ART_TOP, 0, 999)
    assert rig.art_top_y < rig.foot_sole_y
    keys = {h.key for h in rig.handles()}
    assert {"pelvis", "hand_l", "hand_l@r", ch.HAT_CROWN, ch.FOOT_SOLE} <= keys
    assert "pelvis@r" not in keys


def test_open_stacks_parts_in_game_draw_order():
    folder = _folder()
    doc, notes = ch.open_character(folder)
    assert notes == []
    assert [ly.name for ly in doc.layers] == list(ch.DRAW_ORDER)
    assert (doc.width, doc.height) == (W, H)
    assert doc.character.folder == folder and doc.character.name == "Grub"
    assert not doc.dirty and doc.path is None
    head = next(ly for ly in doc.layers if ly.name == "head")
    assert np.array_equal(head.pixels, _png(folder / "head.png"))


def test_open_notes_missing_parts_and_sizes_from_art_without_json():
    folder = _folder(parts=[p for p in ch.PARTS if p != "armR"])
    (folder / ch.FILE_NAME).unlink()
    doc, notes = ch.open_character(folder)
    assert (doc.width, doc.height) == (W, H)
    assert any("armR" in n for n in notes)
    assert [ly.name for ly in doc.layers] == list(ch.DRAW_ORDER)


def test_save_writes_only_what_changed():
    folder = _folder()
    doc, _ = ch.open_character(folder)
    before = {p: (folder / f"{p}.png").stat().st_mtime_ns for p in ch.PARTS}
    json_before = (folder / ch.FILE_NAME).read_text()
    assert ch.save_character(doc) == []
    after = {p: (folder / f"{p}.png").stat().st_mtime_ns for p in ch.PARTS}
    assert after == before
    torso = next(ly for ly in doc.layers if ly.name == "torso")
    torso.pixels[0, 0] = (255, 0, 0, 255)
    torso.bump()
    doc.character.move_handle("knee_l", 5, 6)
    ch.save_character(doc)
    after = {p: (folder / f"{p}.png").stat().st_mtime_ns for p in ch.PARTS}
    assert [p for p in ch.PARTS if after[p] != before[p]] == ["torso"]
    assert tuple(_png(folder / "torso.png")[0, 0]) == (255, 0, 0, 255)
    assert json.loads((folder / ch.FILE_NAME).read_text())["joints"]["knee_l"] == [5, 6]
    assert (folder / ch.FILE_NAME).read_text() != json_before
    assert not list(folder.glob(".*" + ch._TMP_SUFFIX))


def test_save_keeps_layer_opacity_out_but_applies_offset():
    folder = _folder()
    doc, _ = ch.open_character(folder)
    head = next(ly for ly in doc.layers if ly.name == "head")
    head.opacity = 0.3
    head.offset_x = 2
    ch.save_character(doc)
    i = ch.PARTS.index("head")
    saved = _png(folder / "head.png")
    assert tuple(saved[i * 5, i * 4 + 2]) == (10 * i, 200, 30, 255)


def test_save_refuses_missing_parts_and_skips_extras():
    folder = _folder()
    doc, _ = ch.open_character(folder)
    doc.add_layer("sketch")
    notes = ch.save_character(doc)
    assert any("sketch" in n for n in notes)
    assert not (folder / "sketch.png").exists()
    next(ly for ly in doc.layers if ly.name == "armL").name = "arm"
    try:
        ch.save_character(doc)
    except ch.CharacterError as exc:
        assert "armL" in str(exc)
    else:
        raise AssertionError("saved without armL")


def test_save_as_another_folder_writes_everything_and_moves_there():
    folder = _folder()
    doc, _ = ch.open_character(folder)
    other = folder.parent / "Grub2"
    ch.save_character(doc, other)
    assert all((other / f"{p}.png").is_file() for p in ch.PARTS)
    assert doc.character.folder == other
    assert ch.is_character_folder(other)


def test_create_from_template_and_blank():
    src = _folder(rig={"joints": {"neck": [20, 21]}})
    made = ch.create_character(src.parent / "Copy", template=src)
    assert np.array_equal(_png(made / "armL.png"), _png(src / "armL.png"))
    assert ch.CharacterRig.load(made).joints["neck"] == (20.0, 21.0)
    blank = ch.create_character(src.parent / "Blank", width=30, height=60)
    img = _png(blank / "head.png")
    assert img.shape == (60, 30, 4) and not img[..., 3].any()
    assert ch.CharacterRig.load(blank).joints == ch.GOBLIN_JOINTS
    try:
        ch.create_character(made)
    except ch.CharacterError:
        pass
    else:
        raise AssertionError("overwrote an existing character")


def test_character_folder_detection():
    folder = _folder()
    assert ch.character_folder_for(folder / "head.png") == folder
    assert ch.character_folder_for(folder) == folder
    assert ch.character_folder_for(folder.parent) is None


def test_save_after_undo_leaves_untouched_parts_alone():
    folder = _folder()
    doc, _ = ch.open_character(folder)
    ch.save_character(doc)  # first save may reformat rig.json
    before = {p: (folder / f"{p}.png").read_bytes() for p in ch.PARTS}
    hist = History()
    hist.push(doc)
    doc.character.move_handle("neck", 3, 4)
    hist.undo(doc)
    hist.redo(doc)
    ch.save_character(doc)
    assert {p: (folder / f"{p}.png").read_bytes() for p in ch.PARTS} == before


def test_undo_restores_joints():
    doc, _ = ch.open_character(_folder())
    hist = History()
    hist.push(doc)
    doc.character.move_handle("neck", 7, 8)
    assert hist.undo(doc)
    assert doc.character.joints["neck"] == ch.GOBLIN_JOINTS["neck"]
    assert hist.redo(doc)
    assert doc.character.joints["neck"] == (7.0, 8.0)


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
