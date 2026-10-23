"""Live rig preview (GreenInkobold): the game's procedural goblin animation on the parts being painted.

Runs :class:`inkobold.core.goblin_rig.GoblinRig` (the port of the game's PlayerRig) every
frame on the active character document and draws its part layers with Cairo, so strokes,
joint moves and tuning show up animated straight away. Tuning comes from the game
project's ``player_rig.gd`` plus the character's ``rig.json`` params.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Callable, Optional

import cairo
import gi
import numpy as np

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from inkobold.core import goblin_rig as gr
from inkobold.core.assets import godot_project_dir
from PIL import Image
from inkobold.core.character import PARTS, CharacterRig
from inkobold.core.document import Document
from inkobold.ui.dialogs import configure_numeric_spin

# Part images re-upload at most this often while a stroke keeps changing them (s).
REUPLOAD_INTERVAL = 0.08
MAX_STEP = 0.1  # longest frame the rig is stepped by (s), so a stall does not jump
_BG = (0.17, 0.19, 0.16)
_GROUND = (0.11, 0.12, 0.10)
_GROUND_LINE = (0.47, 0.78, 0.27, 0.8)
HATS_DIR = Path("visual assets") / "player" / "hats"  # PlayerRig.HATS_DIR, under the project
GROUNDS = ("Flat floor", "Slope", "Step")
# (label, world px -> screen px; 0 = fit the widget)
VIEW_SIZES = (("Fit", 0.0), ("Game 1×", 1.0), ("Game 2×", 2.0), ("Game 4×", 4.0))


def _surface_from_rgba(rgba: np.ndarray) -> tuple[cairo.ImageSurface, np.ndarray]:
    """Premultiplied Cairo surface (and the buffer it borrows) from straight RGBA uint8."""
    a = rgba[..., 3].astype(np.uint16)
    h, w = rgba.shape[:2]
    stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_ARGB32, w)
    buf = np.zeros((h, stride), dtype=np.uint8)
    view = buf[:, : w * 4].reshape(h, w, 4)
    # Cairo ARGB32 is native-endian premultiplied: B, G, R, A bytes on little-endian.
    view[..., 0] = (rgba[..., 2].astype(np.uint16) * a // 255).astype(np.uint8)
    view[..., 1] = (rgba[..., 1].astype(np.uint16) * a // 255).astype(np.uint8)
    view[..., 2] = (rgba[..., 0].astype(np.uint16) * a // 255).astype(np.uint8)
    view[..., 3] = rgba[..., 3]
    surf = cairo.ImageSurface.create_for_data(memoryview(buf), cairo.FORMAT_ARGB32, w, h, stride)
    return surf, buf


class _PartImage:
    """One part layer as a premultiplied Cairo surface, tracked by layer id + revision."""

    def __init__(self) -> None:
        self.key: tuple = ()
        self.surface: Optional[cairo.ImageSurface] = None
        self.uploaded_at = 0.0
        self._buf: Optional[np.ndarray] = None

    def refresh(self, doc: Document, layer, now: float, force: bool = False) -> None:
        key = (layer.id, layer.revision, layer.offset_x, layer.offset_y, doc.width, doc.height)
        if key == self.key:
            return
        if not force and self.surface is not None and now - self.uploaded_at < REUPLOAD_INTERVAL:
            return
        self.surface, self._buf = _surface_from_rgba(doc._layer_rgba_u8(layer, bake_opacity=False))
        self.key = key
        self.uploaded_at = now


class RigPreview(Gtk.Box):
    """Animated preview pane; call :meth:`set_active` when it is shown or hidden."""

    def __init__(self, get_document: Callable[[], Optional[Document]]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["toolbox"])
        self.set_size_request(220, -1)
        self._get_document = get_document
        self._rig: Optional[gr.GoblinRig] = None
        self._rig_doc: Optional[Document] = None
        self._char_sig = ""
        self._hat_cache: dict[str, tuple[float, cairo.ImageSurface, np.ndarray]] = {}
        self._hat_ids: list[str] = []
        self._ground_values = {"Slope": 15.0, "Step": 3.0}
        self._ground_mode = GROUNDS[0]
        self._images: dict[str, _PartImage] = {}
        self._tick_id: Optional[int] = None
        self._last_frame: Optional[int] = None
        self._t = 0.0
        self._playing = True
        self._scenario_name = "Walk"
        self._pointer: Optional[tuple[float, float]] = None
        self._view = (1.0, 0.0, 0.0)  # scale, offset x, offset y (world -> widget)

        title = Gtk.Label(label="Live Rig", xalign=0, css_classes=["heading"])
        self.append(title)
        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True)
        self.area.set_content_height(260)
        self.area.set_draw_func(self._draw)
        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", lambda _c, x, y: self._set_pointer((x, y)))
        motion.connect("leave", lambda *_: self._set_pointer(None))
        self.area.add_controller(motion)
        self.append(self.area)

        row = Gtk.Box(spacing=4)
        self.motion_drop = Gtk.DropDown.new_from_strings(list(gr.SCENARIOS))
        self.motion_drop.set_selected(gr.SCENARIOS.index(self._scenario_name))
        self.motion_drop.set_hexpand(True)
        self.motion_drop.set_tooltip_text("What the goblin is doing (the game's procedural states)")
        self.motion_drop.connect("notify::selected", self._on_motion_changed)
        row.append(self.motion_drop)
        self.play_btn = Gtk.ToggleButton(icon_name="media-playback-pause-symbolic", active=True)
        self.play_btn.set_tooltip_text("Play / pause")
        self.play_btn.connect("toggled", self._on_play_toggled)
        row.append(self.play_btn)
        self.append(row)

        row2 = Gtk.Box(spacing=6)
        row2.append(Gtk.Label(label="Speed", xalign=0))
        self.speed = Gtk.SpinButton.new_with_range(0.25, 2.0, 0.05)
        configure_numeric_spin(self.speed)
        self.speed.set_value(1.0)
        self.speed.set_hexpand(True)
        self.speed.set_tooltip_text("Walk / run / climb pace (also playback rate when not walking)")
        row2.append(self.speed)
        self.facing_btn = Gtk.ToggleButton(label="◀")
        self.facing_btn.set_tooltip_text("Face left (walk, run, jump and swim go the other way)")
        row2.append(self.facing_btn)
        self.append(row2)

        self.aim_check = Gtk.CheckButton(label="Aim at the pointer")
        self.aim_check.set_tooltip_text("Raise the tool arm toward the mouse over the preview")
        self.append(self.aim_check)

        row3 = Gtk.Box(spacing=6)
        self.ground_drop = Gtk.DropDown.new_from_strings(list(GROUNDS))
        self.ground_drop.set_tooltip_text("What the feet stand on (leg IK reaches for it)")
        self.ground_drop.connect("notify::selected", lambda *_: self._update_ground_ui())
        row3.append(self.ground_drop)
        self.ground_amount = Gtk.SpinButton.new_with_range(-45.0, 45.0, 1.0)
        configure_numeric_spin(self.ground_amount, digits=0)
        self.ground_amount.set_hexpand(True)
        self.ground_amount.connect("value-changed", self._on_ground_amount)
        row3.append(self.ground_amount)
        self.append(row3)

        row4 = Gtk.Box(spacing=6)
        self.hat_drop = Gtk.DropDown.new_from_strings(["No hat"])
        self.hat_drop.set_hexpand(True)
        self.hat_drop.set_tooltip_text("Try a hat from visual assets/player/hats (crown / sit / scale in the Rig tab)")
        row4.append(self.hat_drop)
        self.size_drop = Gtk.DropDown.new_from_strings([label for label, _ in VIEW_SIZES])
        self.size_drop.set_tooltip_text("Fit the pane, or show the goblin at its in-game size (world px × zoom)")
        row4.append(self.size_drop)
        self.append(row4)
        self._update_ground_ui()

    # ---- lifecycle ----

    def set_active(self, active: bool) -> None:
        """Run the animation only while the pane is visible."""
        if active and self._tick_id is None:
            self._last_frame = None
            self._tick_id = self.area.add_tick_callback(self._on_tick)
        elif not active and self._tick_id is not None:
            self.area.remove_tick_callback(self._tick_id)
            self._tick_id = None

    def _on_motion_changed(self, drop: Gtk.DropDown, _pspec) -> None:
        i = drop.get_selected()
        if 0 <= i < len(gr.SCENARIOS):
            self._scenario_name = gr.SCENARIOS[i]

    def _on_play_toggled(self, btn: Gtk.ToggleButton) -> None:
        self._playing = btn.get_active()
        btn.set_icon_name("media-playback-pause-symbolic" if self._playing else "media-playback-start-symbolic")

    def _set_pointer(self, p: Optional[tuple[float, float]]) -> None:
        self._pointer = p

    def _update_ground_ui(self) -> None:
        mode = GROUNDS[self.ground_drop.get_selected()]
        # Range changes clamp the value; ignore those until the mode's own value is set.
        self._ground_mode = ""
        self.ground_amount.set_visible(mode != "Flat floor")
        if mode == "Slope":
            self.ground_amount.set_range(-45.0, 45.0)
            self.ground_amount.set_tooltip_text("Floor angle (°), downhill to the right when positive")
        elif mode == "Step":
            self.ground_amount.set_range(-8.0, 8.0)
            self.ground_amount.set_tooltip_text("Step under the right foot (world px, positive = lower)")
        if mode in self._ground_values:
            self.ground_amount.set_value(self._ground_values[mode])
        self._ground_mode = mode

    def _on_ground_amount(self, spin: Gtk.SpinButton) -> None:
        if self._ground_mode in self._ground_values:
            self._ground_values[self._ground_mode] = spin.get_value()

    def _ground(self) -> tuple[float, float]:
        """(slope dy/dx, step px) for the scenario."""
        mode = GROUNDS[self.ground_drop.get_selected()]
        v = self.ground_amount.get_value()
        if mode == "Slope":
            return math.tan(math.radians(v)), 0.0
        if mode == "Step":
            return 0.0, v
        return 0.0, 0.0

    # ---- hats ----

    def _hats_folder(self, char: CharacterRig) -> Optional[Path]:
        project = godot_project_dir(char.folder) if char.folder is not None else None
        return project / HATS_DIR if project is not None else None

    def _refresh_hat_list(self, char: CharacterRig) -> None:
        folder = self._hats_folder(char)
        ids = sorted(p.stem for p in folder.glob("*.png")) if folder is not None and folder.is_dir() else []
        if ids == self._hat_ids:
            return
        current = self._hat_id()
        self._hat_ids = ids
        model = Gtk.StringList.new(["No hat", *[i.replace("_", " ").capitalize() for i in ids]])
        self.hat_drop.set_model(model)
        self.hat_drop.set_selected(ids.index(current) + 1 if current in ids else 0)

    def _hat_id(self) -> str:
        i = self.hat_drop.get_selected()
        return self._hat_ids[i - 1] if 0 < i <= len(self._hat_ids) else ""

    def _hat_surface(self, char: CharacterRig) -> Optional[cairo.ImageSurface]:
        hat = self._hat_id()
        folder = self._hats_folder(char)
        if not hat or folder is None:
            return None
        path = folder / f"{hat}.png"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        cached = self._hat_cache.get(str(path))
        if cached is None or cached[0] != mtime:
            try:
                with Image.open(path) as img:
                    rgba = np.array(img.convert("RGBA"), dtype=np.uint8)
            except OSError:
                return None
            surf, buf = _surface_from_rgba(rgba)
            cached = (mtime, surf, buf)
            self._hat_cache[str(path)] = cached
        return cached[1]

    # ---- rig ----

    def _base_params(self, char: CharacterRig) -> dict:
        return gr.project_tuning(char.folder).params

    def _sync_rig(self, doc: Document) -> Optional[gr.GoblinRig]:
        char = getattr(doc, "character", None)
        if char is None:
            return None
        base = self._base_params(char)
        sig = json.dumps(char.to_dict(), sort_keys=True) + str(id(base))
        if self._rig is None:
            self._rig = gr.GoblinRig(char, gr.resolve_params(base, char))
        elif doc is not self._rig_doc or sig != self._char_sig:
            self._rig.rebuild(char, gr.resolve_params(base, char))
        if doc is not self._rig_doc:
            self._images.clear()
            self._refresh_hat_list(char)
        self._rig_doc = doc
        self._char_sig = sig
        return self._rig

    def _on_tick(self, _widget, clock) -> bool:
        doc = self._get_document()
        rig = self._sync_rig(doc) if doc is not None else None
        frame = clock.get_frame_time()
        dt = 0.0 if self._last_frame is None else (frame - self._last_frame) / 1e6
        self._last_frame = frame
        if rig is not None and self._playing:
            dt = min(max(dt, 0.0), MAX_STEP)
            speed = self.speed.get_value()
            moves = self._scenario_name in ("Walk", "Run", "Climb")
            step = dt if moves else dt * speed
            self._t += step
            slope, step_px = self._ground()
            fn = gr.scenario(
                self._scenario_name,
                speed=speed if moves else 1.0,
                facing=-1.0 if self.facing_btn.get_active() else 1.0,
                slope=slope,
                step=step_px if not moves else 0.0,
            )
            fn(rig.driver, self._t, step)
            if self.aim_check.get_active() and self._pointer is not None:
                rig.driver.aim_point = self._widget_to_world(*self._pointer)
            if step > 0.0:
                rig.step(step)
        self.area.queue_draw()
        return True

    # ---- drawing ----

    @staticmethod
    def _draw_hat(cr, rig: gr.GoblinRig, hat: cairo.ImageSurface) -> None:
        c = rig.char
        w, h = hat.get_width(), hat.get_height()
        pos = complex(*c.hat_crown) - complex(*c.joints["neck"]) + complex(0.0, c.hat_sit)
        xf = rig.head_xf() * gr.Xf(complex(c.hat_scale, 0), complex(0, c.hat_scale), pos) * gr.Xf(o=complex(-w * 0.5, -h))
        cr.save()
        cr.transform(cairo.Matrix(xf.x.real, xf.x.imag, xf.y.real, xf.y.imag, xf.o.real, xf.o.imag))
        cr.set_source_surface(hat, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD)
        cr.paint()
        cr.restore()

    def _widget_to_world(self, x: float, y: float) -> complex:
        s, ox, oy = self._view
        return complex((x - ox) / s, (y - oy) / s)

    def _draw(self, _area, cr, width: int, height: int) -> None:
        cr.set_source_rgb(*_BG)
        cr.paint()
        doc = self._get_document()
        rig = self._rig if doc is not None and doc is self._rig_doc else None
        if rig is None:
            cr.set_source_rgba(1, 1, 1, 0.5)
            cr.select_font_face("Sans")
            cr.set_font_size(12)
            cr.move_to(12, 24)
            cr.show_text("Open a character to preview its rig.")
            return
        h = rig.p.character_height
        zoom = VIEW_SIZES[self.size_drop.get_selected()][1]
        s = zoom if zoom > 0 else min(width, height) / (h * 1.7)
        ox = width * 0.5
        oy = height * 0.5 + (h * 0.5 - gr.FEET_Y) * s
        self._view = (s, ox, oy)

        # Ground (the puppet's floor, tilted by the slope; none while airborne / swimming).
        d = rig.driver
        if not math.isnan(d.ground_y):
            left, right = -ox / s, (width - ox) / s
            xs = [left, d.ground_x, d.ground_x, right] if d.ground_step else [left, right]
            pts = []
            for i, x in enumerate(xs):
                # At the step edge, the first point is the upper floor and the second the lower.
                y = d.ground_y + d.ground_slope * (x - d.ground_x)
                if d.ground_step and (i >= 2):
                    y += d.ground_step
                pts.append((ox + x * s, oy + y * s))
            cr.move_to(*pts[0])
            for pt in pts[1:]:
                cr.line_to(*pt)
            cr.line_to(width, height)
            cr.line_to(0, height)
            cr.close_path()
            cr.set_source_rgb(*_GROUND)
            cr.fill()
            cr.move_to(*pts[0])
            for pt in pts[1:]:
                cr.line_to(*pt)
            cr.set_source_rgba(*_GROUND_LINE)
            cr.set_line_width(1.5)
            cr.stroke()
        elif d.immersion > 0.0:
            cr.set_source_rgba(0.2, 0.45, 0.7, 0.35)
            cr.paint()

        now = time.monotonic()
        layers = {ly.name: ly for ly in doc.layers if ly.name in PARTS}
        cr.save()
        cr.translate(ox, oy)
        cr.scale(s, s)
        hat = self._hat_surface(doc.character)
        for part, xf in rig.part_transforms():
            if part == "armL" and hat is not None:
                # PlayerRig hangs the hat off the head bone, drawn after it (before the arms).
                self._draw_hat(cr, rig, hat)
                hat = None
            layer = layers.get(part)
            if layer is None:
                continue
            img = self._images.setdefault(part, _PartImage())
            img.refresh(doc, layer, now)
            if img.surface is None:
                continue
            cr.save()
            cr.transform(cairo.Matrix(xf.x.real, xf.x.imag, xf.y.real, xf.y.imag, xf.o.real, xf.o.imag))
            cr.set_source_surface(img.surface, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_GOOD)
            cr.paint()
            cr.restore()
        cr.restore()

        if self.aim_check.get_active() and self._pointer is not None:
            px, py = self._pointer
            cr.set_source_rgba(1.0, 0.85, 0.3, 0.9)
            cr.set_line_width(1.5)
            cr.arc(px, py, 5.0, 0, math.tau)
            cr.stroke()
