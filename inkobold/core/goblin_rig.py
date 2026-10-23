"""The game's procedural goblin rig, ported from GDScript for GreenInkobold's live preview.

A line-for-line port of GoblinEngineering ``scripts/player/player_rig.gd`` (``PlayerRig``)
driven by a :class:`PuppetDriver` (the twin of ``PuppetRigDriver``): the same blend
weights, cycles, two-bone leg IK and arm / head layers, so a character previewed here
moves as it will in the game. ``tests/test_goblin_rig.py`` replays the game's own puppet
recording (``rig_character_test -- --fixture``) and fails when the two drift apart; port
any change to ``player_rig.gd`` here too.

Vectors are complex numbers (x + y·i, y down); transforms are Godot ``Transform2D``s.
Tuning defaults are the rig's ``@export`` values, read from the game script when the
project is found (:func:`game_params`), then the character's ``rig.json`` overrides.
"""

from __future__ import annotations

import cmath
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Optional

from inkobold.core.character import CharacterRig

TAU = math.tau
INF = complex(math.inf, math.inf)
FEET_Y = 12.0  # PlayerRig.FEET_Y: the controller capsule bottom
AIRBORNE_GRACE = 0.06
TELEPORT_SNAP_DIST = 32.0
SQUASH_MAX = 7.0  # PlayerController.SQUASH_MAX
CAPSULE_RADIUS = 7.0
CAPSULE_HEIGHT = 14.0
CAPSULE_OFFSET = complex(0.0, -2.0)
SLEEP_BODY_MID = complex(0.0, -2.0)  # GoblinSleep.BODY_MID
SWIM_JUMP_IMMERSION = 0.35  # PhysicsTuning.PLAYER_SWIM_JUMP_IMMERSION
JUMP_SNAP_CLEAR = 181.0  # PhysicsTuning.PLAYER_JUMP_SNAP_CLEAR
RIG_SCRIPT = Path("scripts") / "player" / "player_rig.gd"

# PlayerRig @export defaults (player_rig.gd); game_params() refreshes them from the script.
DEFAULT_PARAMS: dict = {
    "character_height": 28.0, "stride_length": 30.0, "stride_half": 3.0, "step_lift": 2.2,
    "body_bob": 0.7, "arm_swing_deg": 26.0, "torso_lean_deg": 6.0, "breath_hz": 0.35,
    "breath_amp": 0.25, "idle_arm_sway_deg": 2.5, "head_bob": 0.45, "head_tilt_deg": 3.0,
    "head_look_shift": 0.6, "head_look_tilt_deg": 8.0, "air_tuck_rise": 0.35,
    "air_tuck_fall": 0.08, "air_arm_raise_deg": 45.0, "land_dip": 3.0,
    "land_recover_speed": 9.0, "fall_delay": 0.5, "fall_ramp": 0.35, "fall_min_speed": 380.0,
    "fall_arm_raise_deg": 135.0, "fall_arm_flap_deg": 32.0, "fall_flap_hz": 4.5,
    "fall_pedal_amp": 2.8, "fall_pedal_hz": 3.2, "fall_tuck": 0.2, "fall_wobble_deg": 7.0,
    "fall_wobble_hz": 1.6, "struggle_shake_deg": 14.0, "struggle_shake_hz": 9.0,
    "struggle_shake_px": 2.5, "fall_land_extra": 0.7, "burn_arm_raise_deg": 150.0,
    "burn_arm_flail_deg": 40.0, "burn_flail_hz": 7.0, "burn_head_shake_deg": 12.0,
    "burn_head_shake_hz": 5.0, "soot_tint": (0.4, 0.36, 0.33), "sit_drop": 6.0,
    "sit_leg_splay": 0.75, "getup_sec": 0.45, "sit_bounce": 1.4, "sit_arm_deg": 28.0,
    "sit_fling_deg": 110.0, "daze_hz": 1.3, "daze_tilt_deg": 11.0, "dizzy_star_count": 3,
    "dizzy_orbit": (8.0, 2.0), "dizzy_orbit_hz": 1.1, "dizzy_star_size": 2.4,
    "float_gravity_none": 0.35, "float_gravity_full": 0.08, "float_hz": 0.45,
    "float_arm_deg": 62.0, "float_arm_scull_deg": 30.0, "float_leg_amp": 3.0,
    "float_rock_deg": 9.0, "float_bob": 0.8, "stun_float_arm_deg": 75.0,
    "stun_float_leg_splay": 0.4, "stun_float_drift_hz": 0.35, "swim_kick_amp": 3.5,
    "swim_tuck": 0.18, "swim_arm_deg": 70.0, "swim_stroke_arm_deg": 55.0,
    "swim_stroke_hz": 1.6, "swim_lean_deg": 14.0, "swim_bob": 0.9,
    "swim_fall_arm_raise": 0.45, "max_hip_drop": 5.0, "hip_drop_speed": 14.0,
    "probe_radius": 1.5, "squash_torso": 0.08, "squash_max_sink": 7.0,
    "strain_shake_px": 0.15, "strain_shake_hz": 9.0, "climb_stride": 18.0,
    "climb_foot_amp": 2.5, "climb_arm_deg": 150.0, "hang_hand_spread": 6.0,
    "hang_leg_sway": 1.2, "hang_sway_hz": 0.7, "sleep_arm_deg": 8.0,
    "sleep_breath_hz": 0.22, "sleep_breath_px": 0.6, "sleep_blend_rate": 9.0,
    "aim_blend_speed": 14.0, "aim_linger": 0.5, "bubble_fade_sec": 0.35,
    "bubble_stretch_max": 1.0675, "bubble_stretch_speed": 850.0, "bubble_squash_max": 0.665,
    "bubble_squash_min": 0.25, "bubble_squash_speed": 900.0, "bubble_jelly_hz": 3.5,
    "bubble_jelly_damping": 0.28, "bubble_shine_period": 1.4, "bubble_shine_sweep_sec": 0.45,
    "bubble_shine_strength": 0.8, "debug_draw": False,
}
# The rig's @export_group sections, in inspector order (refreshed by project_tuning()).
DEFAULT_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("General", ("character_height",)),
    ("Walk", ("stride_length", "stride_half", "step_lift", "body_bob", "arm_swing_deg", "torso_lean_deg",)),
    ("Idle", ("breath_hz", "breath_amp", "idle_arm_sway_deg",)),
    ("Head", ("head_bob", "head_tilt_deg", "head_look_shift", "head_look_tilt_deg",)),
    ("Air", ("air_tuck_rise", "air_tuck_fall", "air_arm_raise_deg", "land_dip", "land_recover_speed",)),
    ("Fall", (
        "fall_delay", "fall_ramp", "fall_min_speed", "fall_arm_raise_deg", "fall_arm_flap_deg",
        "fall_flap_hz", "fall_pedal_amp", "fall_pedal_hz", "fall_tuck", "fall_wobble_deg",
        "fall_wobble_hz", "struggle_shake_deg", "struggle_shake_hz", "struggle_shake_px",
        "fall_land_extra",
    )),
    ("On fire", (
        "burn_arm_raise_deg", "burn_arm_flail_deg", "burn_flail_hz", "burn_head_shake_deg",
        "burn_head_shake_hz", "soot_tint",
    )),
    ("Knockdown", (
        "sit_drop", "sit_leg_splay", "getup_sec", "sit_bounce", "sit_arm_deg", "sit_fling_deg", "daze_hz",
        "daze_tilt_deg", "dizzy_star_count", "dizzy_orbit", "dizzy_orbit_hz", "dizzy_star_size",
    )),
    ("Swim", (
        "float_gravity_none", "float_gravity_full", "float_hz", "float_arm_deg", "float_arm_scull_deg",
        "float_leg_amp", "float_rock_deg", "float_bob", "stun_float_arm_deg", "stun_float_leg_splay",
        "stun_float_drift_hz", "swim_kick_amp", "swim_tuck", "swim_arm_deg", "swim_stroke_arm_deg",
        "swim_stroke_hz", "swim_lean_deg", "swim_bob", "swim_fall_arm_raise",
    )),
    ("Terrain", ("max_hip_drop", "hip_drop_speed", "probe_radius",)),
    ("Squash", ("squash_torso", "squash_max_sink", "strain_shake_px", "strain_shake_hz",)),
    ("Climb", ("climb_stride", "climb_foot_amp", "climb_arm_deg",)),
    ("Hang", ("hang_hand_spread", "hang_leg_sway", "hang_sway_hz",)),
    ("Sleep", ("sleep_arm_deg", "sleep_breath_hz", "sleep_breath_px", "sleep_blend_rate",)),
    ("Aim", ("aim_blend_speed", "aim_linger",)),
    ("Bubble", (
        "bubble_fade_sec", "bubble_stretch_max", "bubble_stretch_speed", "bubble_squash_max",
        "bubble_squash_min", "bubble_squash_speed", "bubble_jelly_hz", "bubble_jelly_damping",
        "bubble_shine_period", "bubble_shine_sweep_sec", "bubble_shine_strength",
    )),
    ("Debug", ("debug_draw",)),
)
# Tuning the host owns, never taken from rig.json (RigCharacter.HOST_PARAMS).
HOST_PARAMS = frozenset({"character_height", "debug_draw"})
_EXPORT_RE = re.compile(r"^@export var (\w+): (float|int|bool|Vector2|Color) = (.+?)\s*$", re.M)
_GROUP_RE = re.compile(r'^@export_group\("([^"]*)"\)')


def parse_exports(source: str) -> dict:
    """``@export var name: type = literal`` defaults from GDScript source."""
    out: dict = {}
    for name, typ, raw in _EXPORT_RE.findall(source):
        try:
            if typ == "bool":
                out[name] = raw == "true"
            elif typ == "int":
                out[name] = int(raw)
            elif typ == "float":
                out[name] = float(raw)
            else:
                inner = raw[raw.index("(") + 1 : raw.rindex(")")]
                out[name] = tuple(float(v) for v in inner.split(","))
        except ValueError:
            continue
    return out


def game_params(project: Optional[Path]) -> dict:
    """DEFAULT_PARAMS refreshed from the game's player_rig.gd when ``project`` has one."""
    params = dict(DEFAULT_PARAMS)
    if project is not None:
        try:
            params.update(parse_exports((Path(project) / RIG_SCRIPT).read_text(encoding="utf-8")))
        except OSError:
            pass
    return params


def parse_layout(source: str) -> tuple[tuple[tuple[str, tuple[str, ...]], ...], dict[str, str]]:
    """(@export groups in order, name -> its ## doc comment) from GDScript source."""
    groups: list[tuple[str, tuple[str, ...]]] = []
    docs: dict[str, str] = {}
    group, names, comment = "General", [], []
    for line in source.splitlines():
        stripped = line.strip()
        m = _GROUP_RE.match(stripped)
        if m:
            if names:
                groups.append((group, tuple(names)))
            group, names, comment = m.group(1) or "General", [], []
            continue
        if stripped.startswith("##"):
            comment.append(stripped[2:].strip())
            continue
        m = _EXPORT_RE.match(stripped)
        if m:
            names.append(m.group(1))
            if comment:
                docs[m.group(1)] = " ".join(comment)
        comment = []
    if names:
        groups.append((group, tuple(names)))
    return tuple(groups), docs


@dataclass(frozen=True)
class Tuning:
    """A game project's rig tuning: defaults, inspector groups and doc comments."""

    params: dict
    groups: tuple[tuple[str, tuple[str, ...]], ...]
    docs: dict


_TUNING_CACHE: dict[str, tuple[float, Tuning]] = {}


def project_tuning(folder: Optional[Path]) -> Tuning:
    """Tuning of the Godot project holding folder (re-read when player_rig.gd changes);
    the built-in defaults outside a project."""
    from inkobold.core.assets import godot_project_dir

    project = godot_project_dir(Path(folder)) if folder is not None else None
    script = project / RIG_SCRIPT if project is not None else None
    try:
        mtime = script.stat().st_mtime if script is not None else -1.0
    except OSError:
        mtime = -1.0
    key = str(script) if script is not None and mtime >= 0 else ""
    cached = _TUNING_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    tuning = Tuning(dict(DEFAULT_PARAMS), DEFAULT_GROUPS, {})
    if key:
        try:
            source = script.read_text(encoding="utf-8")
        except OSError:
            source = ""
        if source:
            params = dict(DEFAULT_PARAMS)
            params.update(parse_exports(source))
            groups, docs = parse_layout(source)
            tuning = Tuning(params, groups or DEFAULT_GROUPS, docs)
    _TUNING_CACHE[key] = (mtime, tuning)
    return tuning


def resolve_params(base: dict, character: Optional[CharacterRig]) -> dict:
    """``base`` with the character's rig.json params on top (type-checked like the game)."""
    params = dict(base)
    for key, value in (character.params if character is not None else {}).items():
        if key in HOST_PARAMS or key not in params:
            continue
        current = params[key]
        if isinstance(current, bool):
            if isinstance(value, bool):
                params[key] = value
        elif isinstance(current, (int, float)):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                params[key] = type(current)(value)
        elif isinstance(current, tuple) and isinstance(value, list) and len(value) in (2, 3, 4):
            params[key] = tuple(float(v) for v in value)
    return params


# ---- Godot math ----


def lerp(a: float, b: float, w: float) -> float:
    return a + (b - a) * w


def approach(current: float, target: float, rate: float, delta: float) -> float:
    return lerp(current, target, 1.0 - math.exp(-rate * delta))


def move_toward(a: float, b: float, d: float) -> float:
    return b if abs(b - a) <= d else a + math.copysign(d, b - a)


def smoothstep(a: float, b: float, x: float) -> float:
    if math.isclose(a, b, rel_tol=1e-5, abs_tol=1e-5):
        return (0.0 if x <= a else 1.0) if a <= b else (1.0 if x <= b else 0.0)
    s = min(max((x - a) / (b - a), 0.0), 1.0)
    return s * s * (3.0 - 2.0 * s)


def clampf(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def signf(v: float) -> float:
    return 1.0 if v > 0 else -1.0 if v < 0 else 0.0


def wrapf(v: float, lo: float, hi: float) -> float:
    rng = hi - lo
    if rng == 0.0:
        return lo
    return v - rng * math.floor((v - lo) / rng)


def lerp_angle(a: float, b: float, w: float) -> float:
    diff = math.fmod(b - a, TAU)
    dist = math.fmod(2.0 * diff, TAU) - diff
    return a + dist * w


def fposmod(x: float, y: float) -> float:
    v = math.fmod(x, y)
    if (v < 0 < y) or (v > 0 > y):
        v += y
    return v


def angle(v: complex) -> float:
    return math.atan2(v.imag, v.real)


def normalized(v: complex) -> complex:
    n = abs(v)
    return v / n if n > 0 else 0j


def slerp(a: complex, b: complex, w: float) -> complex:
    la, lb = abs(a) ** 2, abs(b) ** 2
    if la == 0.0 or lb == 0.0:
        return a + (b - a) * w
    start = math.sqrt(la)
    result = lerp(start, math.sqrt(lb), w)
    ang = math.atan2(a.real * b.imag - a.imag * b.real, a.real * b.real + a.imag * b.imag)
    return a * cmath.rect(1.0, ang * w) * (result / start)


@dataclass(frozen=True)
class Xf:
    """Godot Transform2D: x / y basis columns and origin, as complex numbers."""

    x: complex = 1 + 0j
    y: complex = 1j
    o: complex = 0j

    @staticmethod
    def from_node(rotation: float, scale: complex, position: complex) -> Xf:
        r = cmath.rect(1.0, rotation)
        return Xf(r * scale.real, 1j * r * scale.imag, position)

    def basis(self, v: complex) -> complex:
        return self.x * v.real + self.y * v.imag

    def xform(self, v: complex) -> complex:
        return self.x * v.real + self.y * v.imag + self.o

    def __mul__(self, other: Xf) -> Xf:
        return Xf(self.basis(other.x), self.basis(other.y), self.xform(other.o))

    def inverse(self) -> Xf:
        a, b, c, d = self.x.real, self.x.imag, self.y.real, self.y.imag
        det = a * d - c * b
        inv = 1.0 / det if det != 0 else 0.0
        ix = complex(d * inv, -b * inv)
        iy = complex(-c * inv, a * inv)
        o = -(ix * self.o.real + iy * self.o.imag)
        return Xf(ix, iy, o)

    def as_list(self) -> list[float]:
        return [self.x.real, self.x.imag, self.y.real, self.y.imag, self.o.real, self.o.imag]


def _axis_scale(direction: complex, along: float, across: float, pivot: complex) -> Xf:
    n = complex(direction.imag, -direction.real)  # Vector2.orthogonal()
    x = direction * (along * direction.real) + n * (across * n.real)
    y = direction * (along * direction.imag) + n * (across * n.imag)
    m = Xf(x, y, 0j)
    return Xf(x, y, pivot - m.basis(pivot))


@dataclass
class Bone:
    name: str
    parent: Optional[Bone]
    joint: complex  # canvas px of its joint (sprite offset = -joint)
    part: str = ""
    behind_parent: bool = False
    position: complex = 0j
    rotation: float = 0.0
    scale: complex = 1 + 1j

    def local(self) -> Xf:
        return Xf.from_node(self.rotation, self.scale, self.position)


# ---- driver ----


@dataclass
class PuppetDriver:
    """PuppetRigDriver in Python: the host sets the state, the rig poses to it."""

    position: complex = 0j
    vel: complex = 0j
    speed: float = 220.0
    axis: float = 0.0
    on_floor: bool = True
    climbing: bool = False
    held: bool = False
    hanging: bool = False
    asleep: bool = False
    sleep_rotation: float = 0.0
    immersion: float = 0.0
    burning: bool = False
    squash_px: float = 0.0
    gravity_scale: float = 1.0
    struggle: float = 0.0
    rail_grip: complex = INF
    aim_point: complex = INF
    ground_y: float = math.nan
    ground_slope: float = 0.0
    ground_x: float = 0.0
    # Preview only (not in PuppetRigDriver): the floor right of ground_x is this much lower.
    ground_step: float = 0.0
    integrate: bool = True
    bubble_left: float = 0.0
    _knocked: bool = False
    _knock_elapsed: float = 0.0
    _knock_duration: float = 0.0
    _bounce_listeners: list = field(default_factory=list, repr=False)

    def advance(self, delta: float) -> None:
        if self.integrate:
            self.position += self.vel * delta
        if self._knocked:
            self._knock_elapsed += delta
            if self._knock_elapsed >= self._knock_duration:
                self._knocked = False
        if self.bubble_left > 0.0:
            self.bubble_left = max(self.bubble_left - delta, 0.0)

    def knock_down(self, duration: float) -> None:
        self._knocked = True
        self._knock_elapsed = 0.0
        self._knock_duration = max(duration, 0.0)

    def bounce(self, impact_speed: float, normal: complex) -> None:
        for fn in list(self._bounce_listeners):
            fn(impact_speed, normal)

    def is_knocked_down(self) -> bool:
        return self._knocked

    def knockdown_elapsed(self) -> float:
        return self._knock_elapsed

    def knockdown_duration(self) -> float:
        return self._knock_duration

    def wants_aim(self) -> bool:
        return _finite(self.aim_point) and not self._knocked

    def floor_y(self, x: float) -> float:
        """Floor height at world x (NaN = no floor)."""
        y = self.ground_y + self.ground_slope * (x - self.ground_x)
        return y + self.ground_step if x > self.ground_x else y

    def probe_ground(self, origin: complex, radius: float, motion: complex) -> Optional[tuple]:
        if math.isnan(self.ground_y) or motion.imag <= 0.000001:
            return None
        floor_y = self.floor_y(origin.real)
        t = (floor_y - radius - origin.imag) / motion.imag
        if t <= 0.0 or t > 1.0:
            return None
        return (t, t, 1.0)


def _finite(v: complex) -> bool:
    return math.isfinite(v.real) and math.isfinite(v.imag)


# ---- the rig ----


class GoblinRig:
    """PlayerRig with a PuppetDriver. ``step(delta)`` = one ``_process`` frame."""

    BONES = ("Pelvis", "ThighL", "ShinL", "ThighR", "ShinR", "Torso", "Head", "ArmL", "ArmR")

    def __init__(
        self,
        character: Optional[CharacterRig] = None,
        params: Optional[dict] = None,
        driver: Optional[PuppetDriver] = None,
        host: Xf = Xf(),
    ) -> None:
        self.host = host  # the rig node's parent transform (world)
        self.driver = driver or PuppetDriver()
        self.driver._bounce_listeners.append(self._on_bubble_bounced)
        # state (PlayerRig member defaults)
        self._time = 0.0
        self._phase = 0.0
        self._climb_phase = 0.0
        self._swim_phase = 0.0
        self._facing = 1.0
        self._walk_w = self._air_w = self._climb_w = self._hang_w = 0.0
        self._sleep_w = 0.0
        self._sleep_angle = 0.0
        self._swim_w = self._swim_arm_w = self._swim_stroke = 0.0
        self._float_w = self._float_phase = 0.0
        self._limp_w = 0.0
        self._burn_w = 0.0
        self._kd_ground_w = 1.0
        self._aim_w = self._aim_hold = 0.0
        self._aim_left = False
        self._airborne_time = 0.0
        self._fall_time = self._fall_w = 0.0
        self._sit = self._kd_elapsed = self._getup = self._daze = 0.0
        self._was_grounded = True
        self._prev_vy = 0.0
        self._land = 0.0
        self._hip_drop = 0.0
        self._squash = self._squash_w = 0.0
        self._pelvis_sink = 0.0
        self._lean = 0.0
        self._last_global = INF
        self._bubble_w = self._bubble_sit = 0.0
        self._jelly = self._jelly_v = 0.0
        self._jelly_axis = complex(0.0, -1.0)
        self._jelly_pivot = complex(0.0, FEET_Y)
        self._stretch = 0.0
        self._stretch_dir = complex(0.0, 1.0)
        self._deformed = False
        self.debug_targets: list[complex] = []
        self.rebuild(character or CharacterRig(), params)

    # ---- setup ----

    def rebuild(self, character: CharacterRig, params: Optional[dict] = None) -> None:
        """(Re)build the skeleton from joints and tuning; the motion state carries on.

        ``params`` are the final tuning values (see :func:`resolve_params`); None keeps
        the current ones (DEFAULT_PARAMS with the character's overrides at first).
        """
        if params is None:
            params = getattr(self, "_params", None) or resolve_params(DEFAULT_PARAMS, character)
        self._params = dict(params)
        self.p = SimpleNamespace(**self._params)
        self.char = character
        c = character
        art = c.foot_sole_y - c.art_top_y
        self._scale = self.p.character_height / art if art else 1.0
        s = self._scale
        self._base_position = complex(0.0, FEET_Y) - complex(c.canvas_width * 0.5, c.foot_sole_y) * s
        self._body_xform = Xf(complex(s, 0.0), complex(0.0, s), self._base_position)
        if not self._deformed:
            self.transform = self._body_xform
        j = {k: complex(*v) for k, v in c.joints.items()}
        self._j = j
        hip_r, knee_r = self._mirror(j["hip_l"]), self._mirror(j["knee_l"])
        ankle_r, shoulder_r = self._mirror(j["ankle_l"]), self._mirror(j["shoulder_l"])
        old = getattr(self, "bones", {})
        pelvis = self._bone("Pelvis", None, j["pelvis"], 0j, "", old)
        thigh_l = self._bone("ThighL", pelvis, j["hip_l"], j["pelvis"], "thighL", old)
        shin_l = self._bone("ShinL", thigh_l, j["knee_l"], j["hip_l"], "shinL", old, True)
        thigh_r = self._bone("ThighR", pelvis, hip_r, j["pelvis"], "thighR", old)
        shin_r = self._bone("ShinR", thigh_r, knee_r, hip_r, "shinR", old, True)
        torso = self._bone("Torso", pelvis, j["pelvis"], j["pelvis"], "torso", old)
        head = self._bone("Head", torso, j["neck"], j["pelvis"], "head", old)
        arm_l = self._bone("ArmL", torso, j["shoulder_l"], j["pelvis"], "armL", old)
        arm_r = self._bone("ArmR", torso, shoulder_r, j["pelvis"], "armR", old)
        self.bones = {b.name: b for b in (pelvis, thigh_l, shin_l, thigh_r, shin_r, torso, head, arm_l, arm_r)}
        self._thigh_len = abs(j["knee_l"] - j["hip_l"])
        self._shin_len = abs(j["ankle_l"] - j["knee_l"])
        self._rest_thigh_l = angle(j["knee_l"] - j["hip_l"])
        self._rest_thigh_r = angle(knee_r - hip_r)
        self._rest_shin_l = angle(j["ankle_l"] - j["knee_l"])
        self._rest_shin_r = angle(ankle_r - knee_r)
        self._rest_arm_l = angle(j["hand_l"] - j["shoulder_l"])
        self._rest_arm_r = angle(self._mirror(j["hand_l"]) - shoulder_r)
        self._arm_len = abs(j["hand_l"] - j["shoulder_l"])

    @staticmethod
    def _bone(name, parent, joint, parent_joint, part, old, behind=False) -> Bone:
        bone = Bone(name, parent, joint, part, behind, position=joint - parent_joint)
        prev = old.get(name)
        if prev is not None:  # keep the pose across a rebuild
            bone.rotation, bone.scale = prev.rotation, prev.scale
        return bone

    def _mirror(self, p: complex) -> complex:
        return complex(self.char.canvas_width - p.real, p.imag)

    # ---- transforms ----

    def global_xf(self, bone: Bone) -> Xf:
        chain: list[Bone] = []
        b: Optional[Bone] = bone
        while b is not None:
            chain.append(b)
            b = b.parent
        xf = self.host * self.transform
        for b in reversed(chain):
            xf = xf * b.local()
        return xf

    @property
    def global_position(self) -> complex:
        return (self.host * self.transform).o

    def to_global(self, bone: Bone, p: complex) -> complex:
        return self.global_xf(bone).xform(p)

    def to_local(self, bone: Bone, p: complex) -> complex:
        return self.global_xf(bone).inverse().xform(p)

    def part_transforms(self) -> list[tuple[str, Xf]]:
        """(part, world transform of its canvas-sized image), in the game's draw order."""
        out: list[tuple[str, Xf]] = []

        def visit(bone: Bone) -> None:
            kids = [b for b in self.bones.values() if b.parent is bone]
            for k in kids:
                if k.behind_parent:
                    visit(k)
            if bone.part:
                out.append((bone.part, self.global_xf(bone) * Xf(o=-bone.joint)))
            for k in kids:
                if not k.behind_parent:
                    visit(k)

        visit(self.bones["Pelvis"])
        return out

    def head_xf(self) -> Xf:
        """Head bone world transform (hats hang off it)."""
        return self.global_xf(self.bones["Head"])

    # ---- frame ----

    def step(self, delta: float) -> None:
        d = self.driver
        self._time += delta
        d.advance(delta)
        expected = self._last_global + d.vel * delta
        body = d.position
        dist = abs(expected - body) if _finite(expected) else math.inf
        if dist > TELEPORT_SNAP_DIST:
            self._snap_state()
        self._last_global = body
        self._update_state(delta)
        self._pose_body(delta)
        self._pose_legs(delta)
        self._pose_arms_and_head()
        self._update_bubble(delta)
        self._apply_lying_turn()

    def _snap_state(self) -> None:
        d = self.driver
        self._land = 0.0
        self._hip_drop = 0.0
        self._squash = d.squash_px
        self._swim_stroke = 0.0
        self._was_grounded = d.on_floor
        self._air_w = 0.0 if self._was_grounded else 1.0
        self._swim_w = 0.0
        self._swim_arm_w = 0.0
        self._fall_time = 0.0
        self._fall_w = 0.0
        self._float_w = 0.0
        self._limp_w = 0.0
        self._kd_ground_w = 1.0 if self._was_grounded else 0.0

    def _update_state(self, delta: float) -> None:
        d, p = self.driver, self.p
        held, hanging, sleeping = d.held, d.hanging, d.asleep
        self._sleep_w = approach(self._sleep_w, 1.0 if sleeping else 0.0, p.sleep_blend_rate, delta)
        if sleeping:
            self._sleep_angle = d.sleep_rotation
        grounded = d.on_floor and not d.climbing and not held and not hanging and not sleeping
        vel = d.vel
        rel_vx = vel.real
        immersion = d.immersion
        if grounded:
            self._airborne_time = 0.0
        else:
            self._airborne_time += delta
        airborne = self._airborne_time > AIRBORNE_GRACE and not d.climbing and not hanging and not sleeping

        if grounded and not self._was_grounded:
            self._land = max(self._land, clampf(self._prev_vy / 520.0, 0.0, 1.0 + p.fall_land_extra * self._fall_w))
        if immersion >= SWIM_JUMP_IMMERSION and vel.imag < -JUMP_SNAP_CLEAR and self._prev_vy > vel.imag + 80.0:
            self._swim_stroke = 1.0
        self._was_grounded = grounded
        self._prev_vy = vel.imag
        self._land = move_toward(self._land, 0.0, p.land_recover_speed * delta * max(self._land, 0.15))
        self._swim_stroke = move_toward(self._swim_stroke, 0.0, 2.8 * delta)

        if abs(d.axis) > 0.01:
            self._facing = signf(d.axis)

        self._update_knockdown(delta, grounded or d.climbing)
        on_fire = d.burning
        self._burn_w = approach(self._burn_w, 1.0 if on_fire else 0.0, 9.0 if on_fire else 4.0, delta)
        self._squash = approach(self._squash, d.squash_px, 25.0, delta)
        self._squash_w = clampf(self._squash / SQUASH_MAX, 0.0, 1.0)
        speed = abs(rel_vx) if grounded and not d.is_knocked_down() and self._sit < 0.5 else 0.0
        walk_target = clampf(speed / max(d.speed * 0.5, 1.0), 0.0, 1.0)
        self._walk_w = approach(self._walk_w, walk_target, 10.0, delta)
        self._air_w = approach(self._air_w, 1.0 if airborne else 0.0, 14.0, delta)
        self._climb_w = approach(self._climb_w, 1.0 if d.climbing else 0.0, 10.0, delta)
        self._hang_w = approach(self._hang_w, 1.0 if hanging else 0.0, 14.0, delta)
        falling = airborne and vel.imag > 0.0 and immersion < 0.15
        self._fall_time = self._fall_time + delta if falling else 0.0
        fall_target = 0.0
        if held:
            fall_target = 1.0
        elif falling:
            fall_target = smoothstep(p.fall_delay, p.fall_delay + p.fall_ramp, self._fall_time) * smoothstep(
                p.fall_min_speed * 0.6, p.fall_min_speed, vel.imag
            )
        self._fall_w = approach(self._fall_w, fall_target, 7.0 if fall_target > self._fall_w else 18.0, delta)
        swim_target = 0.0
        if immersion >= 0.25 and airborne and not d.climbing:
            swim_target = clampf((immersion - 0.15) / 0.55, 0.0, 1.0)
        self._swim_w = approach(self._swim_w, swim_target, 10.0, delta)
        swim_arm_target = 0.0
        if immersion >= 0.35 and grounded and not d.climbing and walk_target < 0.12:
            swim_arm_target = clampf(immersion * 0.35, 0.0, 0.45)
        elif swim_target > 0.0 and self._swim_stroke > 0.05:
            swim_arm_target = swim_target
        self._swim_arm_w = approach(self._swim_arm_w, swim_arm_target, 14.0, delta)
        float_target = 0.0
        if airborne and not held and immersion < 0.15 and not d.is_knocked_down():
            float_target = 1.0 - smoothstep(p.float_gravity_full, p.float_gravity_none, d.gravity_scale)
        self._float_w = approach(self._float_w, float_target, 3.0 if float_target > self._float_w else 9.0, delta)
        self._float_phase = math.fmod(self._float_phase + p.float_hz * TAU * delta, TAU)
        stride_advance = speed * delta / max(p.stride_length, 1.0) * TAU
        self._phase = math.fmod(self._phase + stride_advance, TAU)
        if d.climbing:
            self._climb_phase = math.fmod(
                self._climb_phase + abs(vel.imag) * delta / max(p.climb_stride, 1.0) * TAU, TAU
            )
        if self._swim_w > 0.05 or (grounded and self._swim_arm_w > 0.05):
            swim_speed = abs(complex(vel.real, vel.imag * 0.65))
            stroke_rate = p.swim_stroke_hz * (0.55 + 0.45 * clampf(swim_speed / 90.0, 0.0, 1.5))
            if grounded:
                stroke_rate *= 0.5
            self._swim_phase = math.fmod(self._swim_phase + stroke_rate * TAU * delta, TAU)

        lean_target = 0.0
        if grounded and self._swim_w < 0.4:
            lean_target = clampf(rel_vx / max(d.speed, 1.0), -1.0, 1.0) * p.torso_lean_deg
        elif airborne or self._swim_w > 0.2:
            lean_target = clampf(vel.real / max(d.speed, 1.0), -1.0, 1.0) * p.torso_lean_deg * 0.5
            lean_target += clampf(-vel.imag / 220.0, -1.0, 1.0) * p.swim_lean_deg * self._swim_w
        lean_target += math.sin(self._float_phase * 0.5 + 0.4) * p.float_rock_deg * self._float_w
        lean_target *= 1.0 - self._sit
        self._lean = approach(self._lean, math.radians(lean_target), 8.0, delta)

        self._update_aim_weight(delta)

    def _update_knockdown(self, delta: float, supported: bool) -> None:
        d, p = self.driver, self.p
        knocked = d.is_knocked_down()
        self._kd_ground_w = 1.0 if supported else approach(self._kd_ground_w, 0.0, 10.0, delta)
        self._limp_w = approach(
            self._limp_w, 1.0 if knocked and not supported else 0.0, 8.0 if not supported else 16.0, delta
        )
        self._bubble_sit = approach(self._bubble_sit, 0.0, 14.0, delta)  # puppets never bubble-bounce
        if not d.is_knocked_down():
            self._sit = self._bubble_sit
            self._kd_elapsed = 0.0
            self._getup = 0.0
            self._daze = 0.0
            return
        self._kd_elapsed = d.knockdown_elapsed()
        total = d.knockdown_duration()
        getup_start = max(total - p.getup_sec, 0.0)
        self._getup = clampf((self._kd_elapsed - getup_start) / max(total - getup_start, 0.001), 0.0, 1.0)
        down = smoothstep(0.0, 0.07, self._kd_elapsed) * (1.0 - smoothstep(0.1, 0.85, self._getup))
        self._sit = down * self._kd_ground_w
        self._daze = smoothstep(0.08, 0.3, self._kd_elapsed) * (1.0 - smoothstep(0.0, 0.5, self._getup))

    def _update_aim_weight(self, delta: float) -> None:
        p = self.p
        if self.driver.wants_aim():
            self._aim_hold = p.aim_linger
        else:
            self._aim_hold = max(self._aim_hold - delta, 0.0)
        self._aim_w = approach(self._aim_w, 1.0 if self._aim_hold > 0.0 else 0.0, p.aim_blend_speed, delta)
        if self._aim_w > 0.001:
            dx = self._aim_global().real - self.global_position.real
            if dx < -2.0:
                self._aim_left = True
            elif dx > 2.0:
                self._aim_left = False

    def _aim_global(self) -> complex:
        a = self.driver.aim_point
        return a if _finite(a) else self.global_position

    def _body_velocity(self) -> complex:
        return self.driver.vel

    def _pose_body(self, _delta: float) -> None:
        p, b = self.p, self.bones
        px = 1.0 / self._scale
        bob = self._walk_w * p.body_bob * (0.5 - 0.5 * math.cos(2.0 * self._phase))
        breath = math.sin(self._time * TAU * p.breath_hz) * p.breath_amp * (1.0 - self._walk_w) * (1.0 - self._air_w)
        breath = lerp(breath, math.sin(self._time * TAU * p.sleep_breath_hz) * p.sleep_breath_px, self._sleep_w)
        stroke_bob = self._swim_w * p.swim_bob * (0.5 - 0.5 * math.cos(self._swim_phase * 2.0))
        stroke_bob += self._swim_w * self._swim_stroke * p.swim_bob * 0.85 * math.sin(self._swim_phase)
        stroke_bob += self._float_w * p.float_bob * math.sin(self._float_phase * 2.0)
        torso_k = p.squash_torso * self._squash_w
        head_top = (self._j["pelvis"].imag - self.char.art_top_y) * self._scale
        squash_sink = max(self._squash - torso_k * head_top, 0.0)
        self._pelvis_sink = min(max(self._hip_drop, squash_sink), max(p.squash_max_sink, self._hip_drop))
        drop = bob + max(self._land * p.land_dip, p.sit_drop * self._sit) + self._pelvis_sink
        drop *= 1.0 - max(self._air_w, self._swim_w * 0.85)
        bounce = abs(math.sin(self._kd_elapsed * 14.0)) * math.exp(-6.0 * self._kd_elapsed) * p.sit_bounce * self._sit
        bounce += math.sin(self._getup * math.pi) * p.sit_bounce * 0.6
        b["Pelvis"].position = self._j["pelvis"] + complex(0.0, (drop - stroke_bob - bounce) * px)
        strain = math.sin(self._time * TAU * p.strain_shake_hz) * p.strain_shake_px * self._squash_w * self._squash_w
        torso = b["Torso"]
        torso.position = complex(strain * px, breath * px)
        torso.scale = complex(1.0 + torso_k * 0.6, 1.0 - torso_k)
        wobble = math.sin(self._time * TAU * p.fall_wobble_hz) * math.radians(p.fall_wobble_deg) * self._fall_w
        jerk = self.driver.struggle if self.driver.held else 0.0
        if jerk > 0.0:
            wobble += math.sin(self._time * TAU * p.struggle_shake_hz) * math.radians(p.struggle_shake_deg) * jerk
            torso.position += complex(
                math.sin(self._time * TAU * p.struggle_shake_hz * 1.3) * p.struggle_shake_px * jerk * px, 0.0
            )
        wobble += math.sin(self._time * TAU * p.stun_float_drift_hz) * math.radians(8.0) * self._limp_w
        torso.rotation = self._lean + wobble

    def _pose_legs(self, delta: float) -> None:
        p, b, d = self.p, self.bones, self.driver
        px = 1.0 / self._scale
        reach = self._thigh_len + self._shin_len
        targets: list[complex] = []
        needed_drop = 0.0
        pelvis = b["Pelvis"]
        ankle_l = self._j["ankle_l"]
        for side in (0, 1):
            hip = (b["ThighL"] if side == 0 else b["ThighR"]).position
            ankle_rest = (ankle_l if side == 0 else self._mirror(ankle_l)) - self._j["pelvis"]
            leg_phase = self._phase + math.pi * side
            stride = math.sin(leg_phase) * p.stride_half * self._walk_w * self._facing
            lift = max(math.cos(leg_phase), 0.0) * p.step_lift * self._walk_w
            ground_local = ankle_rest + complex(stride * px, 0.0)
            probe_hit = False
            if self._sleep_w < 0.5:
                pelvis_xf = self.global_xf(pelvis)
                hip_world = pelvis_xf.xform(hip)
                foot_world_x = pelvis_xf.xform(ground_local).real
                probe_len = reach * self._scale + p.max_hip_drop + 6.0
                origin = complex(foot_world_x, hip_world.imag)
                cast = d.probe_ground(origin, p.probe_radius, complex(0.0, probe_len))
                if cast is not None and cast[2] > 0.5 and cast[0] > 0.001:
                    hit_t = cast[0]
                    window = probe_len * (cast[1] - cast[0])
                    fine = d.probe_ground(origin + complex(0.0, probe_len * cast[0]), p.probe_radius, complex(0.0, window))
                    if fine is not None and fine[2] > 0.5:
                        hit_t += (cast[1] - cast[0]) * fine[0]
                    ground_y = origin.imag + probe_len * hit_t + p.probe_radius
                    ankle_y = ground_y - (self.char.foot_sole_y - ankle_l.imag) * self._scale
                    ground_local = pelvis_xf.inverse().xform(complex(foot_world_x, ankle_y))
                    probe_hit = True
            if probe_hit:
                dx = ground_local.real - hip.real
                max_dy = math.sqrt(max(reach * reach * 0.995 - dx * dx, 0.0))
                residual = (ground_local.imag - hip.imag - max_dy) * self._scale
                needed_drop = max(needed_drop, residual + self._pelvis_sink * (1.0 - self._air_w))
            ground_local -= complex(0.0, lift * px)

            tuck = p.air_tuck_rise if self._body_velocity().imag < 0.0 else p.air_tuck_fall
            spread = (-0.05 if side == 0 else 0.05) * reach
            air_local = ankle_rest + complex(spread, -tuck * reach)
            kick = math.sin(self._swim_phase + math.pi * side) * p.swim_kick_amp
            frog_out = max(math.sin(self._swim_phase), 0.0) * self._swim_stroke * 2.2
            swim_spread = spread * (1.0 + 2.5 * self._swim_stroke + frog_out * 0.35)
            swim_local = ankle_rest + complex(
                swim_spread + frog_out * px * (-1.0 if side == 0 else 1.0),
                (-p.swim_tuck * reach) + (kick - frog_out * 0.6) * px,
            )
            climb_local = ankle_rest + complex(0.0, math.sin(self._climb_phase + math.pi * side) * p.climb_foot_amp * px)
            pedal = self._time * TAU * p.fall_pedal_hz + math.pi * side
            fall_local = ankle_rest + complex(
                spread * 1.6 + math.cos(pedal) * p.fall_pedal_amp * px * self._facing,
                -p.fall_tuck * reach + math.sin(pedal) * p.fall_pedal_amp * px,
            )
            tread = self._float_phase + math.pi * side
            float_local = ankle_rest + complex(
                spread * 2.2 + math.cos(tread) * p.float_leg_amp * 0.6 * px * self._facing,
                -0.22 * reach + math.sin(tread) * p.float_leg_amp * px,
            )
            drift = math.sin(self._time * TAU * p.stun_float_drift_hz + 1.7 * side)
            limp_local = ankle_rest + complex(
                (-1.0 if side == 0 else 1.0) * p.stun_float_leg_splay * reach + drift * 1.2 * px,
                -0.12 * reach + drift * 0.8 * px,
            )
            air_pose = _vlerp(_vlerp(air_local, float_local, self._float_w), limp_local, self._limp_w)
            air_or_swim = _vlerp(_vlerp(air_pose, fall_local, self._fall_w), swim_local, self._swim_w)
            target = _vlerp(_vlerp(ground_local, air_or_swim, self._air_w), climb_local, self._climb_w)
            hang_local = ankle_rest + complex(
                math.sin(self._time * TAU * p.hang_sway_hz + 0.8 * side) * p.hang_leg_sway * px, 0.0
            )
            target = _vlerp(target, hang_local, self._hang_w)
            if self._sit > 0.001:
                kick_up = max(math.sin(self._kd_elapsed * 12.0), 0.0) * math.exp(-4.0 * self._kd_elapsed) * 2.0
                sit_local = ankle_rest + complex(
                    (-1.0 if side == 0 else 1.0) * p.sit_leg_splay * reach,
                    -(p.sit_drop * self._sit + kick_up) * px,
                )
                target = _vlerp(target, sit_local, self._sit)
            target = _vlerp(target, ankle_rest, self._sleep_w)
            targets.append(target)
        self.debug_targets = targets

        if (
            self._air_w < 0.5 and self._climb_w < 0.5 and self._hang_w < 0.5 and self._swim_w < 0.55
            and self._sit < 0.05 and self._limp_w < 0.5 and self._sleep_w < 0.5
        ):
            self._hip_drop = approach(self._hip_drop, clampf(needed_drop, 0.0, p.max_hip_drop), p.hip_drop_speed, delta)
        else:
            self._hip_drop = approach(self._hip_drop, 0.0, p.hip_drop_speed, delta)

        self._solve_leg(b["ThighL"], b["ShinL"], targets[0], self._rest_thigh_l, self._rest_shin_l, 1.0)
        self._solve_leg(b["ThighR"], b["ShinR"], targets[1], self._rest_thigh_r, self._rest_shin_r, -1.0)

    def _solve_leg(self, thigh: Bone, shin: Bone, target: complex, rest_thigh: float, rest_shin: float, bend: float) -> None:
        hip = thigh.position
        to = target - hip
        if abs(to) ** 2 < 0.0001:
            to = 1j
        tl, sl = self._thigh_len, self._shin_len
        dist = clampf(abs(to), abs(tl - sl) + 0.01, tl + sl - 0.01)
        base = angle(to)
        c = clampf((tl * tl + dist * dist - sl * sl) / (2.0 * tl * dist), -1.0, 1.0)
        thigh_angle = base + bend * math.acos(c)
        knee = hip + cmath.rect(tl, thigh_angle)
        foot = hip + normalized(to) * dist
        thigh.rotation = thigh_angle - rest_thigh
        shin.rotation = angle(foot - knee) - rest_shin - thigh.rotation

    def _pose_arms_and_head(self) -> None:
        p, b, d = self.p, self.bones, self.driver
        px = 1.0 / self._scale
        rad = math.radians
        swing = rad(p.arm_swing_deg) * self._walk_w * self._facing
        sway = math.sin(self._time * TAU * p.breath_hz) * rad(p.idle_arm_sway_deg) * (1.0 - self._walk_w)
        rising = self._body_velocity().imag < 0.0
        raise_ = (
            rad(p.air_arm_raise_deg) * (1.0 if rising else 0.6) * self._air_w * (1.0 - self._swim_w)
            * (1.0 - max(self._float_w, self._limp_w))
        )
        if not rising and self._swim_w > 0.05:
            raise_ += rad(p.air_arm_raise_deg) * p.swim_fall_arm_raise * self._swim_w
        climb = rad(p.climb_arm_deg)
        swim_l = swim_r = 0.0
        lakebed_paddle = d.on_floor and not d.climbing and self._swim_w < 0.2
        if lakebed_paddle and self._swim_arm_w > 0.05:
            crawl = rad(p.swim_arm_deg) * self._swim_arm_w
            swim_l = math.sin(self._swim_phase) * crawl
            swim_r = math.sin(self._swim_phase + math.pi) * crawl
        elif self._swim_stroke > 0.05:
            pull_w = math.sin((1.0 - self._swim_stroke) * math.pi) * self._swim_arm_w
            pull = rad(p.swim_stroke_arm_deg) * pull_w
            sweep = rad(p.swim_arm_deg) * 0.45 * pull_w
            swim_l = sweep + pull
            swim_r = -sweep - pull

        base_l = swing * math.sin(self._phase) + sway + raise_
        base_r = swing * math.sin(self._phase + math.pi) - sway - raise_
        if self._float_w > 0.001:
            reach_out, scull = rad(p.float_arm_deg), rad(p.float_arm_scull_deg)
            base_l = lerp(base_l, reach_out + math.sin(self._float_phase) * scull, self._float_w)
            base_r = lerp(base_r, -reach_out - math.sin(self._float_phase + math.pi * 0.7) * scull, self._float_w)
        if self._limp_w > 0.001:
            lift, loose = rad(p.stun_float_arm_deg), rad(10.0)
            t = self._time * TAU * p.stun_float_drift_hz
            base_l = lerp(base_l, lift + math.sin(t) * loose, self._limp_w)
            base_r = lerp(base_r, -lift - math.sin(t + 1.3) * loose, self._limp_w)
        if self._fall_w > 0.001:
            flap = self._time * TAU * p.fall_flap_hz
            up, amp = rad(p.fall_arm_raise_deg), rad(p.fall_arm_flap_deg)
            base_l = lerp(base_l, up + math.sin(flap) * amp, self._fall_w)
            base_r = lerp(base_r, -up - math.sin(flap + math.pi * 0.8) * amp, self._fall_w)
        if self._burn_w > 0.001:
            flail = self._time * TAU * p.burn_flail_hz
            up_burn, amp = rad(p.burn_arm_raise_deg), rad(p.burn_arm_flail_deg)
            base_l = lerp(base_l, up_burn + math.sin(flail) * amp, self._burn_w)
            base_r = lerp(base_r, -up_burn - math.sin(flail + math.pi * 0.6) * amp, self._burn_w)
        if self._sit > 0.001:
            fling = rad(p.sit_fling_deg) * math.exp(-7.0 * self._kd_elapsed)
            prop = rad(p.sit_arm_deg) + math.sin(self._time * TAU * p.daze_hz) * rad(4.0) * self._daze
            base_l = lerp(base_l, prop + fling, self._sit)
            base_r = lerp(base_r, -prop - fling, self._sit)
        base_l = lerp(base_l, swim_l, self._swim_arm_w)
        base_r = lerp(base_r, swim_r, self._swim_arm_w)
        base_l = lerp(base_l, climb * (0.85 + 0.15 * math.sin(self._climb_phase)), self._climb_w)
        base_r = lerp(base_r, -climb * (0.85 + 0.15 * math.sin(self._climb_phase + math.pi)), self._climb_w)
        torso, arm_l, arm_r, head = b["Torso"], b["ArmL"], b["ArmR"], b["Head"]
        grip = d.rail_grip
        if self._hang_w > 0.001 and grip != INF:
            spread = complex(p.hang_hand_spread, 0.0)
            reach_l = self.to_local(torso, grip - spread) - arm_l.position
            reach_r = self.to_local(torso, grip + spread) - arm_r.position
            base_l = lerp_angle(base_l, wrapf(angle(reach_l) - self._rest_arm_l, -math.pi, math.pi), self._hang_w)
            base_r = lerp_angle(base_r, wrapf(angle(reach_r) - self._rest_arm_r, -math.pi, math.pi), self._hang_w)
        brace_w = smoothstep(0.1, 0.5, self._squash_w) * (1.0 - max(max(self._climb_w, self._hang_w), self._sit))
        if brace_w > 0.001:
            tremble = math.sin(self._time * TAU * p.strain_shake_hz + 0.7) * rad(3.0) * self._squash_w
            base_l = lerp_angle(base_l, self._brace_arm_angle(arm_l, -1.0, self._rest_arm_l) + tremble, brace_w)
            base_r = lerp_angle(base_r, self._brace_arm_angle(arm_r, 1.0, self._rest_arm_r) - tremble, brace_w)
        if self._sleep_w > 0.001:
            rest = rad(p.sleep_arm_deg)
            base_l = lerp_angle(base_l, -rest, self._sleep_w)
            base_r = lerp_angle(base_r, rest, self._sleep_w)

        aim_local = self.to_local(torso, self._aim_global())
        rot_l, rot_r = base_l, base_r
        if self._aim_w > 0.001:
            if self._aim_left:
                aim_l = wrapf(angle(aim_local - arm_l.position) - self._rest_arm_l, -math.pi, math.pi)
                rot_l = lerp_angle(base_l, aim_l, self._aim_w)
            else:
                aim_r = wrapf(angle(aim_local - arm_r.position) - self._rest_arm_r, -math.pi, math.pi)
                rot_r = lerp_angle(base_r, aim_r, self._aim_w)
        arm_l.rotation = rot_l
        arm_r.rotation = rot_r

        head_y = self._walk_w * p.head_bob * (0.5 - 0.5 * math.cos(2.0 * self._phase - 0.6))
        float_tilt = -math.sin(self._float_phase * 0.5 + 0.4) * rad(p.float_rock_deg) * 0.6 * self._float_w
        head_y += max(self._swim_w, self._swim_arm_w) * p.swim_bob * 0.35 * math.sin(self._swim_phase * 2.0)
        tilt = math.sin(self._phase) * rad(p.head_tilt_deg) * self._walk_w
        tilt += math.sin(self._swim_phase) * rad(p.head_tilt_deg) * 0.6 * max(self._swim_w, self._swim_arm_w * 0.5)
        tilt += float_tilt
        look = 0.0
        if self._aim_w > 0.001:
            to_aim = self._aim_global() - self.global_xf(head).o
            look = clampf(to_aim.real / 60.0, -1.0, 1.0) * self._aim_w
            tilt += clampf(to_aim.imag / 120.0, -1.0, 1.0) * rad(p.head_look_tilt_deg) * signf(to_aim.real) * self._aim_w
        daze_t = self._time * TAU * p.daze_hz
        tilt += math.sin(daze_t) * rad(p.daze_tilt_deg) * self._daze
        look += math.cos(daze_t) * 0.6 * self._daze
        tilt += math.sin(self._time * TAU * p.fall_wobble_hz * 2.3 + 1.1) * rad(p.head_tilt_deg * 2.5) * self._fall_w
        tilt += math.sin(self._time * TAU * p.burn_head_shake_hz) * rad(p.burn_head_shake_deg) * self._burn_w
        tilt *= 1.0 - self._sleep_w
        look *= 1.0 - self._sleep_w
        head.position = self._j["neck"] - self._j["pelvis"] + complex(look * p.head_look_shift * px, head_y * px)
        head.rotation = tilt - self._lean * 0.5

    def _brace_arm_angle(self, arm: Bone, outward: float, rest: float) -> float:
        rise = (self.char.art_top_y - self._j["pelvis"].imag) - arm.position.imag
        side = math.sqrt(max(self._arm_len * self._arm_len - rise * rise, 0.0)) * outward
        return wrapf(angle(complex(side, rise)) - rest, -math.pi, math.pi)

    # ---- bubble (squash / stretch only; the pink is a game shader) ----

    def _update_bubble(self, delta: float) -> None:
        p, d = self.p, self.driver
        left = d.bubble_left
        pink_target = 1.0 if left > 0.0 else 0.0
        if 0.0 < left < 2.0:
            pink_target = 0.55 + 0.45 * signf(math.sin(left * TAU * (2.0 + (2.0 - left) * 2.0)))
        fade = p.bubble_fade_sec * (0.25 if 0.0 < left < 2.0 else 1.0)
        self._bubble_w = move_toward(self._bubble_w, pink_target, delta / max(fade, 0.01))
        omega = TAU * p.bubble_jelly_hz
        steps = int(min(max(math.ceil(delta * omega / 0.25), 1), 8))
        h = delta / steps
        for _ in range(steps):
            self._jelly_v += (-omega * omega * self._jelly - 2.0 * p.bubble_jelly_damping * omega * self._jelly_v) * h
            self._jelly += self._jelly_v * h
        self._jelly = clampf(self._jelly, -0.75, 1.2)
        vel = d.vel
        speed = abs(vel)
        stretch_target = 0.0
        if left > 0.0 and speed > 1.0:
            stretch_target = p.bubble_stretch_max * clampf(speed / p.bubble_stretch_speed, 0.0, 1.0)
            self._stretch_dir = normalized(slerp(self._stretch_dir, vel / speed, 1.0 - math.exp(-20.0 * delta)))
        self._stretch = approach(self._stretch, stretch_target, 18.0, delta)
        active = abs(self._jelly) > 0.002 or abs(self._jelly_v) > 0.01 or self._stretch > 0.002
        s = self._scale
        base = Xf(complex(s, 0.0), complex(0.0, s), self._base_position)
        if not active:
            self._jelly = 0.0
            self._jelly_v = 0.0
            if self._deformed:
                self._deformed = False
                self._body_xform = base
            return
        self._deformed = True
        jelly_along = 1.0 + self._jelly
        squish = _axis_scale(self._jelly_axis, jelly_along, 1.0 / jelly_along, self._jelly_pivot)
        along = 1.0 + self._stretch
        stretch = _axis_scale(self._stretch_dir, along, 1.0 / math.sqrt(along), complex(0.0, -2.0))
        self._body_xform = stretch * squish * base

    def _on_bubble_bounced(self, impact_speed: float, normal: complex) -> None:
        p = self.p
        self._jelly_axis = normalized(normal) if abs(normal) ** 2 > 0.01 else complex(0.0, -1.0)
        reach = CAPSULE_RADIUS + CAPSULE_HEIGHT * 0.5 * abs(self._jelly_axis.imag)
        self._jelly_pivot = CAPSULE_OFFSET - self._jelly_axis * reach
        q = lerp(p.bubble_squash_min, p.bubble_squash_max, clampf(impact_speed / p.bubble_squash_speed, 0.0, 1.0))
        self._jelly_v = min(self._jelly_v, 0.0) - q * TAU * p.bubble_jelly_hz / 0.75
        self._stretch *= 0.2

    def _apply_lying_turn(self) -> None:
        if self._sleep_w <= 0.001:
            self.transform = self._body_xform
            return
        a = lerp_angle(0.0, self._sleep_angle, self._sleep_w)
        pivot = SLEEP_BODY_MID
        turn = Xf.from_node(a, 1 + 1j, pivot) * Xf(o=-pivot)
        self.transform = turn * self._body_xform

    # ---- inspection ----

    def snapshot(self) -> dict:
        """Rig transform and every bone's local pose, laid out like the game fixture."""
        return {
            "rig": self.transform.as_list(),
            "bones": [
                [b.position.real, b.position.imag, b.rotation, b.scale.real, b.scale.imag]
                for b in (self.bones[n] for n in self.BONES)
            ],
        }


def _vlerp(a: complex, b: complex, w: float) -> complex:
    return a + (b - a) * w


# ---- preview scenarios ----


Scenario = Callable[[PuppetDriver, float, float], None]


def _set(d: PuppetDriver, **fields) -> None:
    base = dict(
        vel=0j, axis=0.0, on_floor=True, climbing=False, held=False, hanging=False, asleep=False,
        sleep_rotation=0.0, immersion=0.0, burning=False, squash_px=0.0, gravity_scale=1.0,
        struggle=0.0, rail_grip=INF, aim_point=INF, ground_y=FEET_Y, ground_slope=0.0,
        ground_step=0.0, integrate=True,
    )
    base.update(fields)
    for k, v in base.items():
        setattr(d, k, v)


def scenario(
    name: str, *, speed: float = 1.0, facing: float = 1.0, slope: float = 0.0, step: float = 0.0
) -> Scenario:
    """A looping puppet routine for the preview: sets the driver each frame from its time.

    ``speed`` scales the walk / run pace, ``facing`` is +1 right / -1 left, ``slope`` tilts
    the floor (dy per dx) and ``step`` drops it right of the body (world px; standing
    states only, walking on a step would need a moving body). Returns ``fn(driver, t, dt)``.
    """
    f = 1.0 if facing >= 0 else -1.0

    def idle(d, _t, _dt):
        _set(d, ground_slope=slope, ground_step=step)

    def walk(d, _t, _dt):
        _set(d, vel=complex(110.0 * speed * f, 0.0), axis=f, ground_slope=slope, ground_step=step)

    def run(d, _t, _dt):
        _set(d, vel=complex(220.0 * speed * f, 0.0), axis=f, ground_slope=slope, ground_step=step)

    def jump(d, t, _dt):
        # 0.5 s standing, then a 1.0 s hop arc (up then down), landing back on the floor.
        c = math.fmod(t, 1.5)
        if c < 0.5:
            _set(d, ground_slope=slope, ground_step=step)
        else:
            u = c - 0.5
            _set(d, on_floor=False, ground_y=math.nan, vel=complex(60.0 * f, -420.0 + 840.0 * u), axis=f)

    def long_fall(d, _t, _dt):
        _set(d, on_floor=False, ground_y=math.nan, vel=complex(0.0, 700.0))

    def knockdown(d, t, dt):
        _set(d, ground_slope=slope, ground_step=step)
        if math.fmod(t, 3.0) < dt:
            d.knock_down(1.6)

    def swim(d, t, _dt):
        _set(d, on_floor=False, ground_y=math.nan, immersion=0.8, vel=complex(30.0 * f, -10.0), axis=f)

    def float_(d, _t, _dt):
        _set(d, on_floor=False, ground_y=math.nan, gravity_scale=0.0, vel=complex(20.0 * f, 0.0), axis=f)

    def climb(d, _t, _dt):
        _set(d, on_floor=False, climbing=True, vel=complex(0.0, -40.0 * speed))

    def hang(d, _t, _dt):
        _set(d, on_floor=False, ground_y=math.nan, hanging=True, rail_grip=complex(0.0, -30.0))

    def burn(d, _t, _dt):
        _set(d, burning=True, ground_slope=slope, ground_step=step)

    def squash(d, _t, _dt):
        _set(d, squash_px=4.0, ground_slope=slope, ground_step=step)

    def sleep(d, _t, _dt):
        _set(d, asleep=True, sleep_rotation=-math.pi * 0.5)

    def held(d, t, _dt):
        pulse = max(math.sin(t * 2.5), 0.0) ** 4
        _set(d, on_floor=False, ground_y=math.nan, held=True, struggle=pulse)

    table: dict[str, Scenario] = {
        "Idle": idle, "Walk": walk, "Run": run, "Jump": jump, "Long fall": long_fall,
        "Knockdown": knockdown, "Swim": swim, "Float": float_, "Climb": climb, "Hang": hang,
        "Burn": burn, "Squash": squash, "Sleep": sleep, "Held": held,
    }
    return table.get(name, idle)


SCENARIOS = (
    "Idle", "Walk", "Run", "Jump", "Long fall", "Knockdown", "Swim", "Float", "Climb", "Hang",
    "Burn", "Squash", "Sleep", "Held",
)
