from __future__ import annotations

import copy
import math
from typing import Callable, Optional

import gi
import numpy as np

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from inkobold.core.character import RigView
from inkobold.core.document import Document
from inkobold.core.grid import GridOverlay
from inkobold.core import damage
from inkobold.core.image_meta import constrain_pixels, is_gray
from inkobold.core.mirror import MirrorModifier, segment_distance_sq
from inkobold.core.sun import SunLight
from inkobold.gpu.renderer import GpuRenderer
from inkobold.input import InputHub
from inkobold.tools.base import ToolContext
from inkobold.tools.brush import MAX_STROKE_BUBBLES
from inkobold.tools.paint import render_bubbles_list, set_pixel_mode
from inkobold.ui import rig_overlay


class Canvas(Gtk.Overlay):
    """GL compositor under a transparent event surface (reliable GDK hit-testing)."""

    def __init__(
        self,
        get_document: Callable[[], Optional[Document]],
        get_tool: Callable,
        get_color: Callable[[], tuple[int, int, int, int]],
        get_brush: Callable[[], float],
        input_hub: InputHub,
        on_changed: Callable[[], None],
        push_history: Callable[[], None] | None = None,
        get_mirror: Callable[[], MirrorModifier] | None = None,
        get_grid: Callable[[], GridOverlay] | None = None,
        get_sun: Callable[[], SunLight] | None = None,
        get_tile_wrap: Callable[[], bool] | None = None,
        get_pixel_mode: Callable[[], bool] | None = None,
        on_type_editing: Callable[[bool], None] | None = None,
        on_sun_moved: Callable[[], None] | None = None,
        get_rig_view: Callable[[], RigView] | None = None,
        on_rig_changed: Callable[[], None] | None = None,
        on_mirror_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)

        self._get_document = get_document
        self._get_tool = get_tool
        self._get_color = get_color
        self._get_brush = get_brush
        self._input = input_hub
        self._on_changed = on_changed
        self._push_history = push_history
        self._get_mirror = get_mirror or (lambda: MirrorModifier())
        self._get_grid = get_grid or (lambda: GridOverlay())
        self._get_sun = get_sun or (lambda: SunLight())
        self._get_tile_wrap = get_tile_wrap or (lambda: False)
        self._get_pixel_mode = get_pixel_mode or (lambda: False)
        self._on_type_editing = on_type_editing
        self._on_sun_moved = on_sun_moved
        self._get_rig_view = get_rig_view or (lambda: RigView(show=False))
        self._on_rig_changed = on_rig_changed
        self._on_mirror_changed = on_mirror_changed
        self.renderer = GpuRenderer()
        self._needs_fit = True
        self._panning = False
        self._pan_sx = 0.0
        self._pan_sy = 0.0
        self._pan_ox = 0.0
        self._pan_oy = 0.0
        self._drawing = False
        self._dragging_sun = False
        self._dragging_mirror: str | None = None  # "center" | "rotate" | "step"
        self._mirror_angle0 = 0.0
        self._mirror_pointer_angle0 = 0.0
        self._dragging_grid: str | None = None  # "x" | "y" | "both"
        self._grid_ox0 = 0.0
        self._grid_oy0 = 0.0
        self._grid_doc_x0 = 0.0
        self._grid_doc_y0 = 0.0
        self._dragging_rig: str | None = None  # rig handle key
        self._rig_grab = (0.0, 0.0)  # handle minus pointer, document px
        self._btn1 = False
        self._pointer_x = 0.0
        self._pointer_y = 0.0
        self._pressure = 1.0
        self._stroke_tools: list[object] | None = None
        self.animation_playing = False

        self.gl = Gtk.GLArea()
        self.gl.set_hexpand(True)
        self.gl.set_vexpand(True)
        self.gl.set_auto_render(True)
        self.gl.set_has_depth_buffer(False)
        self.gl.set_has_stencil_buffer(False)
        # Prefer a usable 3.x context; Wayland often provides GLES.
        try:
            self.gl.set_required_version(3, 0)
        except Exception:
            pass
        try:
            self.gl.set_allowed_apis(Gdk.GLAPI.GL | Gdk.GLAPI.GLES)
        except Exception:
            pass
        self.gl.set_can_target(False)  # events go to the overlay catcher
        self.set_child(self.gl)

        # Transparent event catcher above GL — GLArea alone often drops pointer events.
        self._catcher = Gtk.DrawingArea()
        self._catcher.set_hexpand(True)
        self._catcher.set_vexpand(True)
        self._catcher.set_draw_func(self._draw_guides)
        self._catcher.set_focusable(True)
        self._catcher.set_can_focus(True)
        self.add_overlay(self._catcher)

        self.gl.connect("realize", self._on_realize)
        self.gl.connect("render", self._on_render)
        self.gl.connect("resize", self._on_gl_resize)
        self._catcher.connect("resize", self._on_catcher_resize)

        drag = Gtk.GestureDrag.new()
        drag.set_button(1)
        drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self._catcher.add_controller(drag)

        mid = Gtk.GestureDrag.new()
        mid.set_button(2)
        mid.connect("drag-begin", self._on_pan_begin)
        mid.connect("drag-update", self._on_pan_update)
        mid.connect("drag-end", self._on_pan_end)
        self._catcher.add_controller(mid)

        click = Gtk.GestureClick.new()
        click.set_button(1)
        click.connect("pressed", self._on_click_pressed)
        click.connect("released", self._on_click_released)
        self._catcher.add_controller(click)

        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.DISCRETE
        )
        scroll.connect("scroll", self._on_scroll)
        self._catcher.add_controller(scroll)

        keys = Gtk.EventControllerKey.new()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key_pressed)
        keys.connect("key-released", self._on_key_released)
        self._catcher.add_controller(keys)

        motion = Gtk.EventControllerMotion.new()
        motion.connect("enter", lambda *_: self._catcher.grab_focus())
        motion.connect("motion", self._on_pointer_motion)
        self._catcher.add_controller(motion)

        GLib.timeout_add(16, self._tick)

        self._bind_type_editing_callback()

    def _bind_type_editing_callback(self) -> None:
        tool = self._get_tool()
        if tool is not None and getattr(tool, "id", None) == "type":
            if hasattr(tool, "set_editing_changed_callback"):
                tool.set_editing_changed_callback(self._emit_type_editing)

    def _emit_type_editing(self, active: bool) -> None:
        if self._on_type_editing is not None:
            self._on_type_editing(active)

    def _type_tool_editing(self, tool: object | None = None) -> bool:
        t = tool if tool is not None else self._get_tool()
        return bool(
            t is not None
            and getattr(t, "id", None) == "type"
            and getattr(t, "editing", False)
        )

    def cancel_type_edit(self) -> bool:
        """Restore snapshot and end type edit. Returns True if an edit was active."""
        tool = self._get_tool()
        # Prefer the registered type tool instance even if another tool is active.
        if tool is None or getattr(tool, "id", None) != "type":
            return False
        if not getattr(tool, "editing", False):
            return False
        ctx = self._ctx()
        if ctx is None:
            tool.reset()
            return True
        if hasattr(tool, "cancel") and tool.cancel(ctx):
            self._after_tool(ctx, tool)
            return True
        return False

    def refresh_type_preview(self) -> None:
        """Re-rasterize live type after font/size/color changes."""
        tool = self._get_tool()
        if not self._type_tool_editing(tool):
            return
        ctx = self._ctx()
        if ctx is None or not hasattr(tool, "redraw"):
            return
        tool.redraw(ctx)
        self._after_tool(ctx, tool)

    def _layer_to_doc(self, tool: object, x: float, y: float) -> tuple[float, float]:
        """Convert layer-local coords to document space for overlays."""
        if getattr(tool, "uses_document_coords", False):
            return x, y
        doc = self._get_document()
        if doc is None:
            return x, y
        ly = doc.active_layer
        return x + ly.offset_x, y + ly.offset_y

    def _tick(self) -> bool:
        if self._input.libinput is None:
            # Nothing to poll (GDK-only input): stop the 60 Hz idle timer.
            return False
        doc = self._get_document()
        w = doc.width if doc else 1
        h = doc.height if doc else 1
        self._input.poll(w, h)
        return True

    def _on_realize(self, *_a) -> None:
        self.gl.make_current()
        err = self.gl.get_error()
        if err:
            print("GLArea error:", err)
        try:
            self.renderer.init_gl()
            print(
                "Inkobold GL ready:",
                "ES" if self.renderer.es else "GL",
                flush=True,
            )
        except Exception as exc:
            print("GL init failed:", exc, flush=True)
        self.queue_render()
        if self._needs_fit:
            GLib.idle_add(self.request_fit)

    def apply_flora(self, pixels, **params):
        """GPU Flora when GL is ready; returns ``None`` to trigger CPU fallback."""
        if not self.renderer.ready:
            return None
        self.gl.make_current()
        err = self.gl.get_error()
        if err:
            return None
        return self.renderer.apply_flora(pixels, **params)

    def _on_gl_resize(self, _area, width: int, height: int) -> None:
        self.renderer.set_view(width, height)
        if self._needs_fit and width > 32 and height > 32:
            self.fit()

    def _on_catcher_resize(self, _area, width: int, height: int) -> None:
        # Keep view in sync if GL resize lags
        if width > 0 and height > 0:
            self.renderer.set_view(width, height)
            if self._needs_fit and width > 32 and height > 32:
                self.fit()

    def _on_render(self, _area, _ctx) -> bool:
        doc = self._get_document()
        if doc is None or not self.renderer.ready:
            return True
        self.gl.make_current()
        playing = bool(getattr(self, "animation_playing", False))
        self.renderer.draw(doc, playing=playing)
        return True

    def queue_render(self) -> None:
        self.gl.queue_render()

    def _mods_from_gesture(self, gesture: Gtk.Gesture) -> tuple[bool, bool]:
        try:
            state = gesture.get_current_event_state()
        except Exception:
            state = Gdk.ModifierType(0)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        alt = bool(state & (Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.META_MASK))
        return shift, alt

    def _pointer_doc(self, x: float, y: float) -> tuple[float, float]:
        return self.renderer.screen_to_doc(x, y)

    def _read_pressure(self, gesture: Gtk.Gesture | None = None) -> float:
        """Prefer libinput tablet pressure; fall back to GDK axis, then 1.0 (mouse)."""
        doc = self._get_document()
        if doc is not None:
            self._input.poll(doc.width, doc.height)
        tablet = self._input.pressure(None)
        if tablet is not None:
            return float(tablet)
        if gesture is not None:
            try:
                event = gesture.get_current_event()
                if event is not None:
                    ok, val = event.get_axis(Gdk.AxisUse.PRESSURE)
                    if ok:
                        return max(0.0, min(1.0, float(val)))
            except Exception:
                pass
        return 1.0

    def _ctx(self) -> Optional[ToolContext]:
        doc = self._get_document()
        if doc is None:
            return None
        tool = self._get_tool()
        # Every tool call builds its context here, so this keeps the paint
        # primitives in step with the Pixel toggle.
        pixel = bool(self._get_pixel_mode())
        set_pixel_mode(pixel)
        return ToolContext(
            document=doc,
            color=self._get_color(),
            brush_size=self._get_brush(),
            # Pixel mode draws at exactly Size wide, so pressure is ignored.
            pressure=1.0 if pixel else self._pressure,
            threshold=int(getattr(tool, "threshold", 28)),
            depth=float(getattr(tool, "depth", 70)),
            highlight=float(getattr(tool, "highlight", 55)),
            bevel=float(getattr(tool, "bevel", 40)),
            fill3d_type=str(getattr(tool, "fill3d_type", "classic")),
            frequency=float(getattr(tool, "frequency", 60)),
            fill_mode=str(getattr(tool, "fill_mode", "color")),
            tile_scale=float(getattr(tool, "tile_scale", 1.0)),
            maze_cell_size=float(getattr(tool, "maze_cell_size", 8.0)),
            secondary_color=tuple(getattr(tool, "secondary_color", (255, 255, 255, 255))),
            lay_spacing=float(getattr(tool, "lay_spacing", 40.0)),
            replace_action=str(getattr(tool, "replace_action", "replace")),
            apply_mode=str(getattr(tool, "apply_mode", "all")),
            intensity=float(getattr(tool, "intensity", 50)),
            opacity=float(getattr(tool, "opacity", 100)),
            density=float(getattr(tool, "density", 50)),
            tile_wrap=bool(self._get_tile_wrap()),
            light_dir=self._light_dir_for_doc(doc),
        )

    def _light_dir_for_doc(self, doc: Document) -> tuple[float, float, float]:
        sun = self._get_sun()
        return sun.light_dir(float(doc.width), float(doc.height))

    def _sun_screen_pos(self, doc: Document) -> tuple[float, float]:
        sun = self._get_sun()
        dx, dy = sun.doc_xy(float(doc.width), float(doc.height))
        return self.renderer.doc_to_screen(dx, dy)

    def _hit_sun(self, screen_x: float, screen_y: float) -> bool:
        sun = self._get_sun()
        if not sun.enabled:
            return False
        doc = self._get_document()
        if doc is None:
            return False
        sx, sy = self._sun_screen_pos(doc)
        # ~22px hit radius so the glyph is easy to grab at any zoom.
        return (screen_x - sx) ** 2 + (screen_y - sy) ** 2 <= 22.0 ** 2

    def _move_sun_to_screen(self, screen_x: float, screen_y: float, *, commit: bool = False) -> None:
        doc = self._get_document()
        if doc is None:
            return
        sun = self._get_sun()
        dx, dy = self.renderer.screen_to_doc(screen_x, screen_y)
        sun.set_doc_xy(dx, dy, float(doc.width), float(doc.height))
        self.refresh_guides()
        if commit and self._on_sun_moved is not None:
            self._on_sun_moved()

    def _mirror_center_screen(self, doc: Document) -> tuple[float, float]:
        mod = self._get_mirror()
        cx, cy = mod.center(float(doc.width), float(doc.height))
        return self.renderer.doc_to_screen(cx, cy)

    def _hit_mirror_guides(self, screen_x: float, screen_y: float) -> str | None:
        """Return ``center``, ``rotate``, ``step``, or None for a symmetry-guide hit."""
        mod = self._get_mirror()
        if not mod.enabled:
            return None
        doc = self._get_document()
        if doc is None:
            return None
        if mod.mode == "linear":
            # Step handle wins over the hub so a tiny spacing stays editable.
            ax, ay = self.renderer.doc_to_screen(*mod.array_handle(doc.width, doc.height))
            if (screen_x - ax) ** 2 + (screen_y - ay) ** 2 <= 10.0 ** 2:
                return "step"
        hx, hy = self._mirror_center_screen(doc)
        if (screen_x - hx) ** 2 + (screen_y - hy) ** 2 <= 14.0 ** 2:
            return "center"
        hit_r2 = 8.0 ** 2
        for (x0, y0), (x1, y1) in mod.guide_segments(doc.width, doc.height):
            sx0, sy0 = self.renderer.doc_to_screen(x0, y0)
            sx1, sy1 = self.renderer.doc_to_screen(x1, y1)
            if segment_distance_sq(screen_x, screen_y, sx0, sy0, sx1, sy1) <= hit_r2:
                return "rotate"
        return None

    def _begin_mirror_drag(self, kind: str, screen_x: float, screen_y: float) -> None:
        doc = self._get_document()
        if doc is None:
            return
        mod = self._get_mirror()
        self._dragging_mirror = kind
        if kind == "center":
            self._move_mirror_center_to_screen(screen_x, screen_y)
            return
        if kind == "step":
            self._set_mirror_step_to_screen(screen_x, screen_y)
            return
        hx, hy = self._mirror_center_screen(doc)
        self._mirror_angle0 = mod.guide_angle
        self._mirror_pointer_angle0 = math.atan2(screen_y - hy, screen_x - hx)
        self.refresh_guides()

    def _move_mirror_center_to_screen(self, screen_x: float, screen_y: float) -> None:
        doc = self._get_document()
        if doc is None:
            return
        dx, dy = self.renderer.screen_to_doc(screen_x, screen_y)
        self._get_mirror().set_center(dx, dy, float(doc.width), float(doc.height))
        self.refresh_guides()

    def _rotate_mirror_to_screen(self, screen_x: float, screen_y: float) -> None:
        doc = self._get_document()
        if doc is None:
            return
        hx, hy = self._mirror_center_screen(doc)
        ang = math.atan2(screen_y - hy, screen_x - hx)
        delta = ang - self._mirror_pointer_angle0
        self._get_mirror().guide_angle = self._mirror_angle0 + delta
        self.refresh_guides()
        if self._on_mirror_changed is not None:
            self._on_mirror_changed()

    def _set_mirror_step_to_screen(self, screen_x: float, screen_y: float) -> None:
        """Linear Array: one interval ends under the pointer (free distance + direction)."""
        doc = self._get_document()
        if doc is None:
            return
        dx, dy = self.renderer.screen_to_doc(screen_x, screen_y)
        self._get_mirror().set_array_step_to(dx, dy, float(doc.width), float(doc.height))
        self.refresh_guides()
        if self._on_mirror_changed is not None:
            self._on_mirror_changed()

    def _update_mirror_drag(self, screen_x: float, screen_y: float) -> None:
        kind = self._dragging_mirror
        if kind == "center":
            self._move_mirror_center_to_screen(screen_x, screen_y)
        elif kind == "rotate":
            self._rotate_mirror_to_screen(screen_x, screen_y)
        elif kind == "step":
            self._set_mirror_step_to_screen(screen_x, screen_y)

    def _end_mirror_drag(self) -> None:
        self._dragging_mirror = None

    def _hit_grid_guides(self, screen_x: float, screen_y: float) -> str | None:
        """Return ``x``, ``y``, ``both``, or None for a grid-guide hit."""
        grid = self._get_grid()
        if not grid.enabled:
            return None
        doc = self._get_document()
        if doc is None:
            return None
        dx, dy = self.renderer.screen_to_doc(screen_x, screen_y)
        # ~6 screen px in document space so grab feel stays stable while zoomed.
        zoom = max(1e-3, float(self.renderer.zoom))
        tol = 6.0 / zoom
        return grid.hit_axis(dx, dy, float(doc.width), float(doc.height), tol=tol)

    def _begin_grid_drag(self, kind: str, screen_x: float, screen_y: float) -> None:
        doc = self._get_document()
        if doc is None:
            return
        grid = self._get_grid()
        self._dragging_grid = kind
        self._grid_ox0 = float(grid.ox_norm)
        self._grid_oy0 = float(grid.oy_norm)
        self._grid_doc_x0, self._grid_doc_y0 = self.renderer.screen_to_doc(screen_x, screen_y)
        self.refresh_guides()

    def _update_grid_drag(self, screen_x: float, screen_y: float) -> None:
        doc = self._get_document()
        if doc is None or self._dragging_grid is None:
            return
        grid = self._get_grid()
        dx, dy = self.renderer.screen_to_doc(screen_x, screen_y)
        w = max(1.0, float(doc.width))
        h = max(1.0, float(doc.height))
        kind = self._dragging_grid
        if kind in ("x", "both"):
            grid.ox_norm = self._grid_ox0 + (dx - self._grid_doc_x0) / w
        else:
            grid.ox_norm = self._grid_ox0
        if kind in ("y", "both"):
            grid.oy_norm = self._grid_oy0 + (dy - self._grid_doc_y0) / h
        else:
            grid.oy_norm = self._grid_oy0
        grid.clamp()
        self.refresh_guides()

    def _end_grid_drag(self) -> None:
        self._dragging_grid = None

    def _rig(self):
        """(document, its CharacterRig) while the rig overlay is shown, else (None, None)."""
        doc = self._get_document()
        rig = getattr(doc, "character", None) if doc is not None else None
        if rig is None or not self._get_rig_view().show:
            return None, None
        return doc, rig

    def _hit_rig(self, screen_x: float, screen_y: float, alt: bool) -> str | None:
        """Rig handle under the pointer; handles grab in Edit Joints mode or with Alt."""
        _doc, rig = self._rig()
        if rig is None or not (alt or self._get_rig_view().edit):
            return None
        return rig_overlay.hit_handle(rig, screen_x, screen_y, self.renderer.doc_to_screen)

    def _begin_rig_drag(self, key: str, screen_x: float, screen_y: float) -> None:
        _doc, rig = self._rig()
        if rig is None:
            return
        self._checkpoint()
        hx, hy = rig.point(key)
        px, py = self.renderer.screen_to_doc(screen_x, screen_y)
        self._rig_grab = (hx - px, hy - py)
        self._dragging_rig = key
        self._get_rig_view().active = key
        self.refresh_guides()

    def _update_rig_drag(self, screen_x: float, screen_y: float) -> None:
        doc, rig = self._rig()
        if rig is None or self._dragging_rig is None:
            return
        px, py = self.renderer.screen_to_doc(screen_x, screen_y)
        before = rig.point(self._dragging_rig)
        rig.move_handle(self._dragging_rig, px + self._rig_grab[0], py + self._rig_grab[1])
        if rig.point(self._dragging_rig) != before:
            doc.dirty = True
            if self._on_rig_changed is not None:
                self._on_rig_changed()
        self.refresh_guides()

    def _end_rig_drag(self) -> None:
        self._dragging_rig = None
        self._get_rig_view().active = None
        self.refresh_guides()

    def _tool_xy(self, x: float, y: float) -> tuple[Optional[ToolContext], object, float, float]:
        ctx = self._ctx()
        tool = self._get_tool()
        if ctx is None or tool is None:
            return None, None, 0.0, 0.0
        dx, dy = self._pointer_doc(x, y)
        if getattr(tool, "uses_document_coords", False):
            return ctx, tool, dx, dy
        ly = ctx.document.active_layer
        # Paint tools expect a document-aligned buffer. Bake any leftover
        # Transform offset (e.g. tool switch mid-drag) so the full canvas stays drawable.
        if (ly.offset_x or ly.offset_y) and ly.apply_offset(wrap=bool(self._get_tile_wrap())):
            self.renderer.invalidate(ly.id)
        return ctx, tool, dx - ly.offset_x, dy - ly.offset_y

    def _mirror_points(self, tool: object, x: float, y: float, doc: Document) -> list[tuple[float, float]]:
        return [(px, py) for px, py, _rev in self._mirror_branches(tool, x, y, doc)]

    def _mirror_branches(
        self, tool: object, x: float, y: float, doc: Document
    ) -> list[tuple[float, float, bool]]:
        if not getattr(tool, "supports_mirror", True):
            return [(x, y, False)]
        return self._get_mirror().transform_branches(x, y, doc.width, doc.height)

    @staticmethod
    def _branch_alt(tool: object, alt: bool, reverse: bool) -> bool:
        """Flip Alt for orientation-reversing mirror branches of chord-based arcs."""
        if reverse and getattr(tool, "curve_mode", None) == "arc":
            return not alt
        return alt

    def _begin_stroke_tools(self, tool: object, points: list[tuple[float, float]]) -> list[object]:
        """Primary tool tracks the cursor; clones handle mirrored branches."""
        tools: list[object] = [tool]
        for _ in points[1:]:
            clone = copy.deepcopy(tool)
            clone.reset()
            tools.append(clone)
        self._stroke_tools = tools
        return tools

    def _prepare_bubble_stroke_tools(self, ctx: ToolContext, tools: list[object]) -> None:
        """Share one pre-stroke layer snapshot across bubble brush branches."""
        if not tools:
            return
        primary = tools[0]
        if getattr(primary, "brush_mode", None) != "bubbles":
            return
        if not hasattr(primary, "begin_bubble_stroke"):
            return
        base = np.array(ctx.document.active_layer.pixels, copy=True)
        # One shared bubble list so each branch's live preview sees the others.
        shared: list[tuple[float, float, float, float]] = []
        for t in tools:
            t.begin_bubble_stroke(base, shared)

    def _release_stroke_tools(
        self, ctx: ToolContext, tool: object, points: list[tuple[float, float]]
    ) -> None:
        """End a stroke; bubble mode reblends every collected bubble together."""
        tools = self._stroke_tool_list(tool)
        if getattr(tool, "brush_mode", None) == "bubbles" and hasattr(tool, "take_bubble_stroke"):
            base = None
            placed: list[tuple[float, float, float, float]] = []
            seen_lists: list[list] = []
            color = (255, 255, 255, 255)
            opacity = 1.0
            for t in tools:
                t._drawing = False
                b, p, c, o = t.take_bubble_stroke()
                if b is not None:
                    base = b
                # Branches share one list object; don't add it twice.
                if not any(p is s for s in seen_lists):
                    seen_lists.append(p)
                    placed.extend(p)
                color, opacity = c, o
            if base is not None and placed:
                pixels = ctx.document.active_layer.pixels
                if pixels.shape == base.shape:
                    pixels[...] = base
                    if len(placed) > MAX_STROKE_BUBBLES:
                        placed = placed[:MAX_STROKE_BUBBLES]
                    mask = tool._mask(ctx) if hasattr(tool, "_mask") else None
                    render_bubbles_list(
                        pixels, placed, color, mask=mask, opacity=opacity,
                        light_dir=ctx.light_dir,
                    )
                    ctx.document.mark_dirty()
        else:
            for t, (mx, my) in zip(tools, points):
                t.on_release(ctx, mx, my)
        self._stroke_tools = None

    def _start_paint_stroke(
        self,
        ctx: ToolContext,
        tool: object,
        branches: list[tuple[float, float, bool]],
        *,
        shift: bool,
        alt: bool,
    ) -> None:
        points = [(mx, my) for mx, my, _rev in branches]
        tools = self._begin_stroke_tools(tool, points)
        self._prepare_bubble_stroke_tools(ctx, tools)
        with self._damage_scope(ctx, tools):
            for t, (mx, my, rev) in zip(tools, branches):
                t.on_press(ctx, mx, my, shift=shift, alt=self._branch_alt(t, alt, rev))

    @staticmethod
    def _damage_scope(ctx: ToolContext, tools: list[object]):
        """Track which rectangle these tool calls paint, for a partial GPU upload."""
        tracked = bool(tools) and all(
            callable(getattr(t, "damage_tracked", None)) and t.damage_tracked() for t in tools
        )
        return damage.track(ctx.document.active_layer, enabled=tracked)

    def _stroke_tool_list(self, tool: object) -> list[object]:
        if self._stroke_tools:
            return self._stroke_tools
        return [tool]

    def _after_tool(self, ctx: ToolContext, tool: object) -> None:
        if getattr(tool, "modifies_pixels", True):
            layer = ctx.document.active_layer
            dmg = layer.damage
            partial = isinstance(dmg, tuple)
            depth = ctx.document.color_depth
            if is_gray(depth):
                if partial:
                    x0, y0, x1, y1 = dmg
                    constrain_pixels(layer.pixels[y0:y1, x0:x1], depth)
                else:
                    constrain_pixels(layer.pixels, depth)
            if not partial:
                # Unknown extent: make sure the whole layer is re-sent.
                self.renderer.invalidate(layer.id)
        self.queue_render()
        self.refresh_guides()
        self._on_changed()

    def _draw_guides(self, _area, cr, _width: int, _height: int) -> None:
        doc = self._get_document()
        if doc is None:
            return

        if self.renderer.tile_preview:
            cr.save()
            sx0, sy0 = self.renderer.doc_to_screen(0.0, 0.0)
            sx1, sy1 = self.renderer.doc_to_screen(float(doc.width), float(doc.height))
            cr.set_source_rgba(0.95, 0.95, 1.0, 0.9)
            cr.set_line_width(1.5)
            cr.rectangle(sx0, sy0, sx1 - sx0, sy1 - sy0)
            cr.stroke()
            cr.restore()

        grid = self._get_grid()
        if grid.enabled:
            segs = grid.guide_segments(doc.width, doc.height)
            if segs:
                cr.save()
                cr.set_source_rgba(1.0, 1.0, 1.0, 0.5)
                cr.set_line_width(1.0)
                for (x0, y0), (x1, y1) in segs:
                    sx0, sy0 = self.renderer.doc_to_screen(x0, y0)
                    sx1, sy1 = self.renderer.doc_to_screen(x1, y1)
                    cr.move_to(sx0, sy0)
                    cr.line_to(sx1, sy1)
                cr.stroke()
                cr.restore()

        _rig_doc, rig = self._rig()
        if rig is not None:
            rig_overlay.draw(cr, rig, self._get_rig_view(), self.renderer.doc_to_screen)

        sun = self._get_sun()
        if sun.enabled:
            sx, sy = self._sun_screen_pos(doc)
            cr.save()
            # Soft glow
            cr.set_source_rgba(1.0, 0.85, 0.25, 0.22)
            cr.arc(sx, sy, 18.0, 0, math.tau)
            cr.fill()
            # Rays
            cr.set_source_rgba(1.0, 0.78, 0.15, 0.85)
            cr.set_line_width(2.0)
            cr.set_line_cap(1)  # CAIRO_LINE_CAP_ROUND
            for i in range(8):
                ang = i * (math.pi / 4.0)
                c = math.cos(ang)
                s = math.sin(ang)
                cr.move_to(sx + c * 10.0, sy + s * 10.0)
                cr.line_to(sx + c * 16.5, sy + s * 16.5)
            cr.stroke()
            # Disc
            cr.set_source_rgba(1.0, 0.92, 0.35, 0.95)
            cr.arc(sx, sy, 8.0, 0, math.tau)
            cr.fill()
            cr.set_source_rgba(1.0, 0.65, 0.05, 0.95)
            cr.set_line_width(1.5)
            cr.arc(sx, sy, 8.0, 0, math.tau)
            cr.stroke()
            cr.restore()

        tool = self._get_tool()
        points = getattr(tool, "points", None) if tool is not None else None
        if getattr(tool, "id", None) == "lasso" and points and len(points) >= 2:
            cr.save()
            cr.set_source_rgba(0.95, 0.95, 1.0, 0.9)
            cr.set_line_width(1.25)
            cr.set_dash([5.0, 4.0])
            sx0, sy0 = self.renderer.doc_to_screen(points[0][0], points[0][1])
            cr.move_to(sx0, sy0)
            for px, py in points[1:]:
                sx, sy = self.renderer.doc_to_screen(px, py)
                cr.line_to(sx, sy)
            cr.stroke()
            cr.restore()

        if tool is not None and getattr(tool, "id", None) == "move" and hasattr(tool, "guide_corners"):
            ctx = self._ctx()
            if ctx is not None:
                if hasattr(tool, "set_view_scale"):
                    tool.set_view_scale(self.renderer.zoom)
                if hasattr(tool, "sync_box"):
                    tool.sync_box(ctx)
                corners = tool.guide_corners()
                if len(corners) >= 4:
                    cr.save()
                    cr.set_source_rgba(0.95, 0.95, 1.0, 0.95)
                    cr.set_line_width(1.0)
                    if getattr(tool, "floating", False):
                        # Dashed while the original pixels are still floating.
                        cr.set_dash([5.0, 3.0])
                    sx0, sy0 = self.renderer.doc_to_screen(corners[0][0], corners[0][1])
                    cr.move_to(sx0, sy0)
                    for px, py in corners[1:]:
                        sx, sy = self.renderer.doc_to_screen(px, py)
                        cr.line_to(sx, sy)
                    cr.close_path()
                    cr.stroke()
                    cr.set_dash([])
                    handle = 3.5
                    for px, py in corners:
                        sx, sy = self.renderer.doc_to_screen(px, py)
                        cr.rectangle(sx - handle, sy - handle, handle * 2, handle * 2)
                        cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
                        cr.fill_preserve()
                        cr.set_source_rgba(0.2, 0.25, 0.35, 0.95)
                        cr.set_line_width(1.0)
                        cr.stroke()
                    cr.restore()

        if tool is not None and getattr(tool, "id", None) == "type" and getattr(tool, "editing", False):
            box = tool.guide_box() if hasattr(tool, "guide_box") else None
            caret = tool.caret_screen_hint() if hasattr(tool, "caret_screen_hint") else None
            cr.save()
            if box is not None:
                x0, y0, x1, y1 = box
                dx0, dy0 = self._layer_to_doc(tool, x0, y0)
                dx1, dy1 = self._layer_to_doc(tool, x1, y1)
                sx0, sy0 = self.renderer.doc_to_screen(dx0, dy0)
                sx1, sy1 = self.renderer.doc_to_screen(dx1, dy1)
                cr.set_source_rgba(0.95, 0.95, 1.0, 0.85)
                cr.set_line_width(1.0)
                cr.set_dash([4.0, 3.0])
                cr.rectangle(sx0, sy0, sx1 - sx0, sy1 - sy0)
                cr.stroke()
                cr.set_dash([])
            if caret is not None:
                cx0, cy0, cx1, cy1 = caret
                dx0, dy0 = self._layer_to_doc(tool, cx0, cy0)
                dx1, dy1 = self._layer_to_doc(tool, cx1, cy1)
                sx0, sy0 = self.renderer.doc_to_screen(dx0, dy0)
                sx1, sy1 = self.renderer.doc_to_screen(dx1, dy1)
                cr.set_source_rgba(0.2, 0.55, 1.0, 0.95)
                cr.set_line_width(1.25)
                cr.move_to(sx0, sy0)
                cr.line_to(sx1, sy1)
                cr.stroke()
            cr.restore()

        mod = self._get_mirror()
        if not mod.enabled:
            return
        segs = mod.guide_segments(doc.width, doc.height)
        if not segs:
            return
        cr.save()
        cr.set_source_rgba(0.75, 0.85, 1.0, 0.45)
        cr.set_line_width(1.0)
        cr.set_dash([4.0, 4.0])
        for (x0, y0), (x1, y1) in segs:
            sx0, sy0 = self.renderer.doc_to_screen(x0, y0)
            sx1, sy1 = self.renderer.doc_to_screen(x1, y1)
            cr.move_to(sx0, sy0)
            cr.line_to(sx1, sy1)
        cr.stroke()
        # Hub handle — Alt-drag to move the symmetry center.
        hx, hy = self._mirror_center_screen(doc)
        cr.set_dash([])
        if mod.mode == "linear":
            # Ticks at every copy offset; the first interval is the step handle.
            stx, sty = mod.array_step()
            cr.set_source_rgba(0.75, 0.85, 1.0, 0.7)
            for k in range(2, mod.array_count):
                tx, ty = self.renderer.doc_to_screen(
                    mod.cx_norm * doc.width + stx * k, mod.cy_norm * doc.height + sty * k
                )
                cr.arc(tx, ty, 2.5, 0, math.tau)
                cr.fill()
            ax, ay = self.renderer.doc_to_screen(*mod.array_handle(doc.width, doc.height))
            cr.set_source_rgba(0.85, 0.92, 1.0, 0.95)
            cr.set_line_width(1.5)
            cr.rectangle(ax - 4.5, ay - 4.5, 9.0, 9.0)
            cr.stroke()
        cr.set_source_rgba(0.75, 0.85, 1.0, 0.35)
        cr.arc(hx, hy, 7.0, 0, math.tau)
        cr.fill()
        cr.set_source_rgba(0.85, 0.92, 1.0, 0.95)
        cr.set_line_width(1.5)
        cr.arc(hx, hy, 5.0, 0, math.tau)
        cr.stroke()
        cr.restore()

    @property
    def stroke_active(self) -> bool:
        """A tool is between press and release (pixels mid-edit)."""
        return self._drawing

    def refresh_guides(self) -> None:
        self._catcher.queue_draw()

    def _prepare_transform_tool(self, tool: object, ctx: ToolContext) -> None:
        if getattr(tool, "id", None) != "move":
            return
        if hasattr(tool, "set_view_scale"):
            tool.set_view_scale(self.renderer.zoom)
        if hasattr(tool, "sync_box"):
            tool.sync_box(ctx)

    def _transform_idle_miss(
        self, tool: object, ctx: ToolContext, x: float, y: float, *, alt: bool
    ) -> bool:
        """True when Transform would ignore this press (outside the box)."""
        if getattr(tool, "id", None) != "move":
            return False
        would = getattr(tool, "would_begin", None)
        if would is None:
            return False
        return not bool(would(ctx, x, y, alt=alt))

    def commit_transform_float(self) -> bool:
        """Apply a live Transform float (Enter / click outside). True if one was live."""
        tool = self._get_tool()
        commit = getattr(tool, "commit", None)
        if getattr(tool, "id", None) != "move" or commit is None or not commit():
            return False
        self.refresh_guides()
        self._on_changed()
        return True

    # --- primary button: click (tap) + drag (stroke) ---
    def _checkpoint(self) -> None:
        if self._push_history is not None:
            self._push_history()

    def _on_click_pressed(self, gesture: Gtk.GestureClick, _n: int, x: float, y: float) -> None:
        self._catcher.grab_focus()
        # GestureDrag is CAPTURE-phase, so drag-begin often starts the stroke
        # before click pressed. Don't apply / checkpoint twice.
        if self._drawing or self._dragging_sun or self._dragging_mirror or self._dragging_grid or self._dragging_rig:
            self._btn1 = True
            return
        shift, alt = self._mods_from_gesture(gesture)
        rig_hit = self._hit_rig(x, y, alt)
        if rig_hit is not None:
            self._btn1 = True
            self._begin_rig_drag(rig_hit, x, y)
            return
        if self._hit_sun(x, y):
            self._dragging_sun = True
            self._btn1 = True
            self._move_sun_to_screen(x, y)
            return
        # Guides only grab when Alt is held so they don't block drawing.
        if alt:
            mirror_hit = self._hit_mirror_guides(x, y)
            if mirror_hit is not None:
                self._btn1 = True
                self._begin_mirror_drag(mirror_hit, x, y)
                return
            grid_hit = self._hit_grid_guides(x, y)
            if grid_hit is not None:
                self._btn1 = True
                self._begin_grid_drag(grid_hit, x, y)
                return
        self._pressure = self._read_pressure(gesture)
        ctx, tool, lx, ly = self._tool_xy(x, y)
        if ctx is None:
            return
        self._prepare_transform_tool(tool, ctx)
        if self._transform_idle_miss(tool, ctx, lx, ly, alt=alt):
            self.commit_transform_float()
            return
        editing = self._type_tool_editing(tool)
        if not editing:
            self._checkpoint()
        self._drawing = True
        self._btn1 = True
        points = self._mirror_branches(tool, lx, ly, ctx.document)
        self._start_paint_stroke(ctx, tool, points, shift=shift, alt=alt)
        self._after_tool(ctx, tool)
        self._bind_type_editing_callback()

    def _on_click_released(self, gesture: Gtk.GestureClick, _n: int, x: float, y: float) -> None:
        if self._dragging_rig:
            self._update_rig_drag(x, y)
            self._end_rig_drag()
            self._btn1 = False
            return
        if self._dragging_sun:
            self._dragging_sun = False
            self._btn1 = False
            self._move_sun_to_screen(x, y, commit=True)
            return
        if self._dragging_mirror:
            self._update_mirror_drag(x, y)
            self._end_mirror_drag()
            self._btn1 = False
            return
        if self._dragging_grid:
            self._update_grid_drag(x, y)
            self._end_grid_drag()
            self._btn1 = False
            return
        if self._panning and not self._drawing:
            self._panning = False
            return
        if not self._drawing:
            return
        self._pressure = self._read_pressure(gesture)
        ctx, tool, lx, ly = self._tool_xy(x, y)
        self._drawing = False
        self._btn1 = False
        if ctx is None:
            self._stroke_tools = None
            return
        points = self._mirror_points(tool, lx, ly, ctx.document)
        self._release_stroke_tools(ctx, tool, points)
        self._after_tool(ctx, tool)

    def _on_drag_begin(self, gesture: Gtk.GestureDrag, x: float, y: float) -> None:
        if self._dragging_sun or self._dragging_mirror or self._dragging_grid or self._dragging_rig:
            return
        shift, alt = self._mods_from_gesture(gesture)
        rig_hit = self._hit_rig(x, y, alt)
        if rig_hit is not None:
            self._begin_rig_drag(rig_hit, x, y)
            return
        if self._hit_sun(x, y):
            self._dragging_sun = True
            self._move_sun_to_screen(x, y)
            return
        # Guides only grab when Alt is held so they don't block drawing.
        if alt:
            mirror_hit = self._hit_mirror_guides(x, y)
            if mirror_hit is not None:
                self._begin_mirror_drag(mirror_hit, x, y)
                return
            grid_hit = self._hit_grid_guides(x, y)
            if grid_hit is not None:
                self._begin_grid_drag(grid_hit, x, y)
                return
        # If click already started the stroke, keep it; else start here.
        if not self._drawing:
            self._pressure = self._read_pressure(gesture)
            ctx, tool, lx, ly = self._tool_xy(x, y)
            if ctx is None:
                return
            self._prepare_transform_tool(tool, ctx)
            if self._transform_idle_miss(tool, ctx, lx, ly, alt=alt):
                self.commit_transform_float()
                return
            editing = self._type_tool_editing(tool)
            if not editing:
                self._checkpoint()
            self._drawing = True
            points = self._mirror_branches(tool, lx, ly, ctx.document)
            self._start_paint_stroke(ctx, tool, points, shift=shift, alt=alt)
            self._after_tool(ctx, tool)
            self._bind_type_editing_callback()

    def _on_drag_update(self, gesture: Gtk.GestureDrag, offset_x: float, offset_y: float) -> None:
        ok, sx, sy = gesture.get_start_point()
        if not ok:
            return
        x, y = sx + offset_x, sy + offset_y
        if self._dragging_rig:
            self._update_rig_drag(x, y)
            return
        if self._dragging_sun:
            self._move_sun_to_screen(x, y)
            return
        if self._dragging_mirror:
            self._update_mirror_drag(x, y)
            return
        if self._dragging_grid:
            self._update_grid_drag(x, y)
            return
        if self._panning:
            self.renderer.pan_x = self._pan_ox + (x - self._pan_sx)
            self.renderer.pan_y = self._pan_oy + (y - self._pan_sy)
            self.queue_render()
            self.refresh_guides()
            return
        if not self._drawing:
            return
        self._pressure = self._read_pressure(gesture)
        ctx, tool, lx, ly = self._tool_xy(x, y)
        if ctx is None:
            return
        shift, alt = self._mods_from_gesture(gesture)
        branches = self._mirror_branches(tool, lx, ly, ctx.document)
        tools = self._stroke_tool_list(tool)
        if len(tools) != len(branches):
            # Settings changed mid-stroke — stick to the primary branch.
            tools = tools[:1]
            branches = branches[:1]
        with self._damage_scope(ctx, tools):
            for t, (mx, my, rev) in zip(tools, branches):
                t.on_drag(ctx, mx, my, shift=shift, alt=self._branch_alt(t, alt, rev))
        self._after_tool(ctx, tool)

    def _on_drag_end(self, gesture: Gtk.GestureDrag, offset_x: float, offset_y: float) -> None:
        ok, sx, sy = gesture.get_start_point()
        x = sx + offset_x if ok else 0.0
        y = sy + offset_y if ok else 0.0
        if self._dragging_rig:
            self._update_rig_drag(x, y)
            self._end_rig_drag()
            self._btn1 = False
            return
        if self._dragging_sun:
            self._dragging_sun = False
            self._btn1 = False
            self._move_sun_to_screen(x, y, commit=True)
            return
        if self._dragging_mirror:
            self._update_mirror_drag(x, y)
            self._end_mirror_drag()
            self._btn1 = False
            return
        if self._dragging_grid:
            self._update_grid_drag(x, y)
            self._end_grid_drag()
            self._btn1 = False
            return
        if self._panning:
            self._panning = False
            self.refresh_guides()
            return
        if not self._drawing:
            return
        self._pressure = self._read_pressure(gesture)
        ctx, tool, lx, ly = self._tool_xy(x, y)
        self._drawing = False
        self._btn1 = False
        if ctx is None:
            self._stroke_tools = None
            return
        points = self._mirror_points(tool, lx, ly, ctx.document)
        self._release_stroke_tools(ctx, tool, points)
        self._after_tool(ctx, tool)

    # --- middle-button pan ---
    def _start_pan(self, x: float, y: float) -> None:
        self._panning = True
        self._drawing = False
        self._dragging_sun = False
        self._dragging_mirror = None
        self._dragging_grid = None
        self._stroke_tools = None
        self._pan_sx, self._pan_sy = x, y
        self._pan_ox, self._pan_oy = self.renderer.pan_x, self.renderer.pan_y

    def _on_pan_begin(self, _g: Gtk.GestureDrag, x: float, y: float) -> None:
        self._start_pan(x, y)

    def _on_pan_update(self, gesture: Gtk.GestureDrag, offset_x: float, offset_y: float) -> None:
        if not self._panning:
            return
        ok, sx, sy = gesture.get_start_point()
        if not ok:
            return
        x, y = sx + offset_x, sy + offset_y
        self.renderer.pan_x = self._pan_ox + (x - self._pan_sx)
        self.renderer.pan_y = self._pan_oy + (y - self._pan_sy)
        self.queue_render()
        self.refresh_guides()

    def _on_pan_end(self, *_a) -> None:
        self._panning = False

    def _on_pointer_motion(self, controller, x: float, y: float) -> None:
        self._pointer_x = x
        self._pointer_y = y
        _doc, rig = self._rig()
        if rig is None:
            return
        view = self._get_rig_view()
        hover = None
        if self._dragging_rig is None:
            try:
                state = controller.get_current_event_state()
            except Exception:
                state = Gdk.ModifierType(0)
            alt = bool(state & (Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.META_MASK))
            hover = self._hit_rig(x, y, alt)
        if hover != view.hover:
            view.hover = hover
            self.refresh_guides()

    def _on_scroll(self, controller, _dx: float, dy: float) -> bool:
        # Zoom toward cursor (fall back to view center if unknown)
        cx, cy = self._pointer_x, self._pointer_y
        try:
            event = controller.get_current_event()
            if event is not None:
                ok, ex, ey = event.get_position()
                if ok:
                    cx, cy = ex, ey
        except Exception:
            pass
        if cx <= 0 and cy <= 0:
            cx = self.renderer.view_w * 0.5
            cy = self.renderer.view_h * 0.5

        before = self.renderer.screen_to_doc(cx, cy)
        factor = 1.1 if dy < 0 else (1 / 1.1)
        self.renderer.zoom = max(0.05, min(32.0, self.renderer.zoom * factor))
        after = self.renderer.screen_to_doc(cx, cy)
        self.renderer.pan_x += (after[0] - before[0]) * self.renderer.zoom
        self.renderer.pan_y += (after[1] - before[1]) * self.renderer.zoom
        self.queue_render()
        self.refresh_guides()
        self._on_changed()
        return True

    def _on_key_pressed(self, _c, keyval, _keycode, state) -> bool:
        tool = self._get_tool()
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self.commit_transform_float():
            return True
        if self._type_tool_editing(tool) and hasattr(tool, "on_text_key"):
            ctx = self._ctx()
            if ctx is not None:
                shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
                # Ctrl/Alt combos stay for app shortcuts (save, etc.).
                ctrl = bool(state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK))
                ch = ""
                if not ctrl:
                    code = Gdk.keyval_to_unicode(keyval)
                    if code:
                        ch = chr(code)
                if tool.on_text_key(ctx, keyval, ch, shift=shift):
                    self._after_tool(ctx, tool)
                    return True
        return False

    def _on_key_released(self, _c, keyval, _keycode, _state) -> bool:
        return False

    def request_fit(self) -> bool:
        """Queue Fit Canvas; runs immediately if the view is already sized."""
        self._needs_fit = True
        w = self.renderer.view_w
        h = self.renderer.view_h
        if w > 32 and h > 32:
            self.fit()
        return False  # one-shot for GLib.idle_add

    def fit(self) -> None:
        doc = self._get_document()
        if not doc:
            return
        if self.renderer.view_w <= 32 or self.renderer.view_h <= 32:
            self._needs_fit = True
            return
        self.renderer.fit_canvas(doc)
        self._needs_fit = False
        self.queue_render()
        self.refresh_guides()
        self._on_changed()
