"""The Python goblin rig matches the game's PlayerRig frame for frame (no GTK / OpenGL).

``fixtures/goblin_rig_puppet.json.gz`` is the game's own recording: GoblinEngineering
``godot --headless --fixed-fps 60 --path . -s res://scripts/debug/rig_character_test.gd --
--fixture out.json`` (a PuppetRigDriver walking, falling, sitting, aiming, swimming, ...;
every bone's local pose per frame). Re-record it after changing player_rig.gd, then port
the change into core/goblin_rig.py until this passes.

Run with ``python -m pytest tests`` or ``python tests/test_goblin_rig.py``.
"""

from __future__ import annotations

import gzip
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inkobold.core import goblin_rig as gr  # noqa: E402
from inkobold.core.character import CharacterRig  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "goblin_rig_puppet.json.gz"
# Godot keeps node transforms in 32-bit floats; the port runs in 64-bit.
POS_TOL = 2e-3  # canvas px
ROT_TOL = 2e-4  # radians
XF_TOL = 2e-4


def _fixture() -> dict:
    with gzip.open(FIXTURE, "rt", encoding="utf-8") as f:
        return json.load(f)


def _params(raw: dict) -> dict:
    out = dict(gr.DEFAULT_PARAMS)
    for key, value in raw.items():
        if key in out:
            out[key] = tuple(value) if isinstance(value, list) else value
    return out


def _apply(driver: gr.PuppetDriver, fields: dict) -> None:
    for key, value in fields.items():
        if key in ("vel", "rail_grip", "aim_point"):
            value = gr.INF if value is None else complex(value[0], value[1])
        elif key == "ground_y" and value is None:
            value = math.nan
        setattr(driver, key, value)


def _angle_diff(a: float, b: float) -> float:
    return abs(math.remainder(a - b, math.tau))


def replay() -> list[str]:
    """Run the fixture through the port; returns mismatch descriptions (empty = parity)."""
    fx = _fixture()
    char = CharacterRig.from_dict(fx["character"])
    driver = gr.PuppetDriver()
    rig = gr.GoblinRig(char, _params(fx["params"]), driver)
    # Godot's JSON rounds 1/60 to 0.0166666666666667; replay at the exact rate it ran at.
    dt = 1.0 / round(1.0 / fx["dt"])
    bones = fx["bones"]
    assert list(bones) == list(gr.GoblinRig.BONES)
    frames = iter(fx["frames"])
    problems: list[str] = []
    for seg in fx["segments"]:
        _apply(driver, seg["driver"])
        event = seg.get("event") or ""
        if event.startswith("knock_down:"):
            driver.knock_down(float(event.split(":", 1)[1]))
        for i in range(int(seg["frames"])):
            want = next(frames)
            rig.step(dt)
            got = rig.snapshot()
            where = f"{seg['name']}[{i}]"
            for k, (a, b) in enumerate(zip(got["rig"], want["rig"])):
                if abs(a - b) > (XF_TOL if k < 4 else POS_TOL):
                    problems.append(f"{where} rig[{k}] {a:.6f} != {b:.6f}")
            for name, g, w in zip(bones, got["bones"], want["bones"]):
                for label, a, b, tol in (
                    ("x", g[0], w[0], POS_TOL),
                    ("y", g[1], w[1], POS_TOL),
                    ("sx", g[3], w[3], XF_TOL),
                    ("sy", g[4], w[4], XF_TOL),
                ):
                    if abs(a - b) > tol:
                        problems.append(f"{where} {name}.{label} {a:.6f} != {b:.6f}")
                if _angle_diff(g[2], w[2]) > ROT_TOL:
                    problems.append(f"{where} {name}.rot {g[2]:.6f} != {w[2]:.6f}")
            if len(problems) > 20:
                return problems
    return problems


def test_port_matches_game_recording():
    problems = replay()
    assert not problems, "\n".join(problems[:20])


def test_tuning_parses_from_gdscript():
    src = (
        "@export var stride_length: float = 30.0\n"
        "@export var dizzy_star_count: int = 3\n"
        "@export var debug_draw: bool = false\n"
        "@export var dizzy_orbit: Vector2 = Vector2(8.0, 2.0)\n"
        '@export_dir var character_dir: String = ""\n'
    )
    assert gr.parse_exports(src) == {
        "stride_length": 30.0,
        "dizzy_star_count": 3,
        "debug_draw": False,
        "dizzy_orbit": (8.0, 2.0),
    }


def test_defaults_match_the_game_script_when_present():
    project = Path.home() / "Toyfactory" / "GoblinEngineering"
    if not (project / gr.RIG_SCRIPT).is_file():
        return
    parsed = gr.parse_exports((project / gr.RIG_SCRIPT).read_text(encoding="utf-8"))
    assert set(parsed) == set(gr.DEFAULT_PARAMS)


def test_layout_parses_groups_and_doc_comments():
    src = (
        "@export var character_height: float = 28.0\n"
        '@export_group("Walk")\n'
        "## World px travelled per full two-step cycle.\n"
        "@export var stride_length: float = 30.0\n"
        "@export var stride_half: float = 3.0\n"
        '@export_group("Idle")\n'
        "## Breaths\n## per second.\n"
        "@export var breath_hz: float = 0.35\n"
    )
    groups, docs = gr.parse_layout(src)
    assert groups == (
        ("General", ("character_height",)),
        ("Walk", ("stride_length", "stride_half")),
        ("Idle", ("breath_hz",)),
    )
    assert docs == {"stride_length": "World px travelled per full two-step cycle.", "breath_hz": "Breaths per second."}


def test_project_tuning_reads_the_game_and_caches():
    project = Path.home() / "Toyfactory" / "GoblinEngineering"
    if not (project / gr.RIG_SCRIPT).is_file():
        return
    t1 = gr.project_tuning(project / "visual assets" / "player")
    assert t1 is gr.project_tuning(project / "visual assets" / "player")
    assert [g for g, _ in t1.groups][:3] == ["General", "Walk", "Idle"]
    assert t1.docs.get("stride_length")
    outside = gr.project_tuning(None)
    assert outside.params == gr.DEFAULT_PARAMS and outside.groups == gr.DEFAULT_GROUPS


def test_step_ground_bends_the_lower_leg():
    flat, step = gr.GoblinRig(CharacterRig()), gr.GoblinRig(CharacterRig())
    for rig, drop in ((flat, 0.0), (step, 3.0)):
        fn = gr.scenario("Idle", step=drop)
        for i in range(90):
            fn(rig.driver, i / 60.0, 1 / 60.0)
            rig.step(1 / 60.0)
    assert step.driver.floor_y(5.0) == gr.FEET_Y + 3.0 and step.driver.floor_y(-5.0) == gr.FEET_Y
    # The right foot reaches down onto the lower floor; the left leg barely changes.
    assert abs(step.bones["ThighR"].rotation - flat.bones["ThighR"].rotation) > 0.02
    assert step._hip_drop > flat._hip_drop


def test_character_params_override_but_not_host_ones():
    char = CharacterRig(params={"stride_length": 40, "character_height": 99, "nope": 1, "dizzy_orbit": [1, 2]})
    p = gr.resolve_params(gr.DEFAULT_PARAMS, char)
    assert p["stride_length"] == 40.0 and isinstance(p["stride_length"], float)
    assert p["character_height"] == gr.DEFAULT_PARAMS["character_height"]
    assert p["dizzy_orbit"] == (1.0, 2.0) and "nope" not in p


def test_parts_draw_in_game_order_and_rest_pose_lines_up():
    rig = gr.GoblinRig(CharacterRig())
    parts = [name for name, _xf in rig.part_transforms()]
    assert parts == ["shinL", "thighL", "shinR", "thighR", "torso", "head", "armL", "armR"]
    # Before any step every bone sits at rest: each part maps canvas px onto the same spot.
    root = rig.transform
    for _name, xf in rig.part_transforms():
        for p in (0j, complex(123, 456)):
            assert abs(xf.xform(p) - root.xform(p)) < 1e-9


def test_rebuild_keeps_motion_going():
    rig = gr.GoblinRig(CharacterRig())
    run = gr.scenario("Run")
    for i in range(60):
        run(rig.driver, i / 60.0, 1 / 60.0)
        rig.step(1 / 60.0)
    walk_w = rig._walk_w
    moved = CharacterRig()
    moved.move_handle("knee_l", 110, 370)
    rig.rebuild(moved)
    rig.step(1 / 60.0)
    assert rig._walk_w >= walk_w * 0.9
    assert rig.bones["ShinL"].joint == complex(110, 370)


def test_every_scenario_runs():
    for name in gr.SCENARIOS:
        rig = gr.GoblinRig(CharacterRig())
        fn = gr.scenario(name, speed=1.5, facing=-1.0, slope=0.2)
        for i in range(240):
            fn(rig.driver, i / 60.0, 1 / 60.0)
            rig.step(1 / 60.0)
        for _part, xf in rig.part_transforms():
            assert all(math.isfinite(v) for v in xf.as_list()), name


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
