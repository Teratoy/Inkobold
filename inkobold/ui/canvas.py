from __future__ import annotations

import copy
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from inkobold.core.document import Document
from inkobold.core.grid import GridOverlay
from inkobold.core.mirror import MirrorModifier
from inkobold.gpu.renderer import GpuRenderer
from inkobold.input import InputHub
from inkobold.tools.base import ToolContext


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
        on_type_editing: Callable[[bool], None] | None = None,
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
        self._on_type_editing = on_type_editing
        self.renderer = GpuRenderer()
        self._needs_fit = True
        self._panning = False
        self._pan_sx = 0.0
        self._pan_sy = 0.0
        self._pan_ox = 0.0
        self._pan_oy = 0.0
        self._drawing = False
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
        return ToolContext(
            document=doc,
            color=self._get_color(),
            brush_size=self._get_brush(),
            pressure=self._pressure,
            threshold=int(getattr(tool, "threshold", 28)),
            depth=float(getattr(tool, "depth", 70)),
            highlight=float(getattr(tool, "highlight", 55)),
            bevel=float(getattr(tool, "bevel", 40)),
            fill3d_type=str(getattr(tool, "fill3d_type", "classic")),
            frequency=float(getattr(tool, "frequency", 60)),
            fill_mode=str(getattr(tool, "fill_mode", "color")),
            tile_scale=float(getattr(tool, "tile_scale", 1.0)),
            replace_action=str(getattr(tool, "replace_action", "replace")),
            apply_mode=str(getattr(tool, "apply_mode", "all")),
            intensity=float(getattr(tool, "intensity", 50)),
            opacity=float(getattr(tool, "opacity", 100)),
        )

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
        if (ly.offset_x or ly.offset_y) and ly.apply_offset():
            self.renderer.invalidate(ly.id)
        return ctx, tool, dx - ly.offset_x, dy - ly.offset_y

    def _mirror_points(self, tool: object, x: float, y: float, doc: Document) -> list[tuple[float, float]]:
        if not getattr(tool, "supports_mirror", True):
            return [(x, y)]
        return self._get_mirror().transform_points(x, y, doc.width, doc.height)

    def _begin_stroke_tools(self, tool: object, points: list[tuple[float, float]]) -> list[object]:
        """Primary tool tracks the cursor; clones handle mirrored branches."""
        tools: list[object] = [tool]
        for _ in points[1:]:
            clone = copy.deepcopy(tool)
            clone.reset()
            tools.append(clone)
        self._stroke_tools = tools
        return tools

    def _stroke_tool_list(self, tool: object) -> list[object]:
        if self._stroke_tools:
            return self._stroke_tools
        return [tool]

    def _after_tool(self, ctx: ToolContext, tool: object) -> None:
        if getattr(tool, "modifies_pixels", True):
            from inkobold.core.image_meta import constrain_pixels, has_alpha, is_gray

            depth = ctx.document.color_depth
            if is_gray(depth) or not has_alpha(depth):
                constrain_pixels(ctx.document.active_layer.pixels, depth)
            self.renderer.invalidate(ctx.document.active_layer.id)
        self.queue_render()
        self.refresh_guides()
        self._on_changed()

    def _draw_guides(self, _area, cr, _width: int, _height: int) -> None:
        doc = self._get_document()
        if doc is None:
            return

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
                    sx0, sy0 = self.renderer.doc_to_screen(corners[0][0], corners[0][1])
                    cr.move_to(sx0, sy0)
                    for px, py in corners[1:]:
                        sx, sy = self.renderer.doc_to_screen(px, py)
                        cr.line_to(sx, sy)
                    cr.close_path()
                    cr.stroke()
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
        cr.restore()

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

    # --- primary button: click (tap) + drag (stroke) ---
    def _checkpoint(self) -> None:
        if self._push_history is not None:
            self._push_history()

    def _on_click_pressed(self, gesture: Gtk.GestureClick, _n: int, x: float, y: float) -> None:
        self._catcher.grab_focus()
        # GestureDrag is CAPTURE-phase, so drag-begin often starts the stroke
        # before click pressed. Don't apply / checkpoint twice.
        if self._drawing:
            self._btn1 = True
            return
        self._pressure = self._read_pressure(gesture)
        ctx, tool, lx, ly = self._tool_xy(x, y)
        if ctx is None:
            return
        self._prepare_transform_tool(tool, ctx)
        shift, alt = self._mods_from_gesture(gesture)
        if self._transform_idle_miss(tool, ctx, lx, ly, alt=alt):
            return
        editing = self._type_tool_editing(tool)
        if not editing:
            self._checkpoint()
        self._drawing = True
        self._btn1 = True
        points = self._mirror_points(tool, lx, ly, ctx.document)
        for t, (mx, my) in zip(self._begin_stroke_tools(tool, points), points):
            t.on_press(ctx, mx, my, shift=shift, alt=alt)
        self._after_tool(ctx, tool)
        self._bind_type_editing_callback()

    def _on_click_released(self, gesture: Gtk.GestureClick, _n: int, x: float, y: float) -> None:
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
        for t, (mx, my) in zip(self._stroke_tool_list(tool), points):
            t.on_release(ctx, mx, my)
        self._stroke_tools = None
        self._after_tool(ctx, tool)

    def _on_drag_begin(self, gesture: Gtk.GestureDrag, x: float, y: float) -> None:
        # If click already started the stroke, keep it; else start here.
        if not self._drawing:
            self._pressure = self._read_pressure(gesture)
            ctx, tool, lx, ly = self._tool_xy(x, y)
            if ctx is None:
                return
            self._prepare_transform_tool(tool, ctx)
            shift, alt = self._mods_from_gesture(gesture)
            if self._transform_idle_miss(tool, ctx, lx, ly, alt=alt):
                return
            editing = self._type_tool_editing(tool)
            if not editing:
                self._checkpoint()
            self._drawing = True
            points = self._mirror_points(tool, lx, ly, ctx.document)
            for t, (mx, my) in zip(self._begin_stroke_tools(tool, points), points):
                t.on_press(ctx, mx, my, shift=shift, alt=alt)
            self._after_tool(ctx, tool)
            self._bind_type_editing_callback()

    def _on_drag_update(self, gesture: Gtk.GestureDrag, offset_x: float, offset_y: float) -> None:
        ok, sx, sy = gesture.get_start_point()
        if not ok:
            return
        x, y = sx + offset_x, sy + offset_y
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
        points = self._mirror_points(tool, lx, ly, ctx.document)
        tools = self._stroke_tool_list(tool)
        if len(tools) != len(points):
            # Settings changed mid-stroke — stick to the primary branch.
            tools = tools[:1]
            points = points[:1]
        for t, (mx, my) in zip(tools, points):
            t.on_drag(ctx, mx, my, shift=shift, alt=alt)
        self._after_tool(ctx, tool)

    def _on_drag_end(self, gesture: Gtk.GestureDrag, offset_x: float, offset_y: float) -> None:
        ok, sx, sy = gesture.get_start_point()
        x = sx + offset_x if ok else 0.0
        y = sy + offset_y if ok else 0.0
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
        for t, (mx, my) in zip(self._stroke_tool_list(tool), points):
            t.on_release(ctx, mx, my)
        self._stroke_tools = None
        self._after_tool(ctx, tool)

    # --- middle-button pan ---
    def _start_pan(self, x: float, y: float) -> None:
        self._panning = True
        self._drawing = False
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

    def _on_pointer_motion(self, _c, x: float, y: float) -> None:
        self._pointer_x = x
        self._pointer_y = y

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
