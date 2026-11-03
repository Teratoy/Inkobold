"""Symmetry modifier tests (no GTK needed).

Run with ``python -m pytest tests`` or, without pytest, ``python tests/test_mirror.py``.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inkobold.core.mirror import ARRAY_COUNT_MAX, MirrorModifier  # noqa: E402


def _linear(**kw) -> MirrorModifier:
    return MirrorModifier(enabled=True, mode="linear", **kw)


def _close(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return math.isclose(a[0], b[0], abs_tol=1e-6) and math.isclose(a[1], b[1], abs_tol=1e-6)


def test_linear_array_steps_from_the_pointer_not_the_hub():
    mod = _linear(array_count=3, array_spacing=10.0, array_direction=math.pi / 2)
    pts = mod.transform_points(5.0, 5.0, 100, 100)
    assert len(pts) == 3
    for got, want in zip(pts, [(5.0, 5.0), (5.0, 15.0), (5.0, 25.0)]):
        assert _close(got, want), (got, want)


def test_linear_array_never_reverses_orientation():
    mod = _linear(array_count=5, array_spacing=7.0, array_direction=1.0)
    assert not any(rev for _x, _y, rev in mod.transform_branches(1.0, 2.0, 50, 50))


def test_linear_array_zero_spacing_collapses_to_one_branch():
    mod = _linear(array_count=8, array_spacing=0.0)
    assert len(mod.transform_points(3.0, 4.0, 50, 50)) == 1


def test_step_handle_sets_free_distance_and_direction():
    mod = _linear(array_count=4)
    mod.set_array_step_to(50.0 + 30.0, 50.0 - 40.0, 100, 100)
    assert math.isclose(mod.array_spacing, 50.0)
    assert _close(mod.array_handle(100, 100), (80.0, 10.0))
    seg = mod.guide_segments(100, 100)
    assert len(seg) == 1 and _close(seg[0][1], (50.0 + 90.0, 50.0 - 120.0))


def test_guide_angle_edits_array_heading_only_in_linear_mode():
    mod = _linear()
    before = mod.angle
    mod.guide_angle = math.pi
    assert math.isclose(mod.array_direction, math.pi) and mod.angle == before
    mod.mode = "mirror"
    mod.guide_angle = 0.25
    assert math.isclose(mod.angle, 0.25) and math.isclose(mod.array_direction, math.pi)


def test_array_count_is_clamped():
    mod = _linear(array_count=999, array_spacing=-5.0)
    mod.clamp()
    assert mod.array_count == ARRAY_COUNT_MAX and mod.array_spacing == 0.0


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
