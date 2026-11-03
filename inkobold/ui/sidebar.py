"""Sidebar: the Frames timeline and Layers list to the right of the canvas.

``build_sidebar`` builds the column; ``SidebarMixin`` holds the ``MainWindow``
methods behind it: frame / layer list rebuilds, context menus, drag reorder,
inline rename, playback, the Layer menu actions and the Frames | Layers split
sizing helpers.
"""

from __future__ import annotations

from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from inkobold.ui.dialogs import configure_numeric_spin


def build_sidebar(win) -> Gtk.Box:
    """Frames | Layers column (right of the canvas); the window packs it into its paned."""
    side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, css_classes=["sidebar"])
    side.set_size_request(win._side_panel_w, -1)
    side.set_hexpand(False)
    side.set_vexpand(True)

    # Frames | Layers share height so section footers (+/−, FPS, …) stay on-screen.
    # Default both-collapsed: keep the start (Frames) size fixed so Layers sits
    # directly under it instead of mid-column after later layout passes.
    win._sidebar_split = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
    win._sidebar_split.set_wide_handle(True)
    win._sidebar_split.set_resize_start_child(False)
    win._sidebar_split.set_resize_end_child(True)
    win._sidebar_split.set_shrink_start_child(True)
    win._sidebar_split.set_shrink_end_child(True)
    side.append(win._sidebar_split)

    # --- Animation frames (collapsible) ---
    win.frames_expander = Gtk.Expander(label="Frames", expanded=False)
    win.frames_expander.set_hexpand(True)
    win.frames_expander.set_vexpand(False)
    # Collapsed: sit at the top of the paned slot. Expanded: fill so the list can grow.
    win.frames_expander.set_valign(Gtk.Align.START)
    frames_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    win.frames_expander.set_child(frames_body)
    win._sidebar_split.set_start_child(win.frames_expander)

    win.frame_list = Gtk.ListBox()
    win.frame_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
    win.frame_list.set_valign(Gtk.Align.START)
    win.frame_list.connect("row-selected", win._on_frame_selected)
    frame_drop = Gtk.DropTarget.new(GObject.TYPE_INT64, Gdk.DragAction.MOVE)
    frame_drop.connect("drop", win._on_frame_drop)
    win.frame_list.add_controller(frame_drop)
    win.frame_scroll = Gtk.ScrolledWindow(vexpand=False, hexpand=True)
    win.frame_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    win.frame_scroll.set_min_content_height(96)
    win.frame_scroll.set_child(win.frame_list)
    frames_body.append(win.frame_scroll)

    frame_btns = Gtk.Box(spacing=4)
    add_fr = Gtk.Button(label="+")
    add_fr.set_tooltip_text("Add blank frame after current")
    add_fr.connect("clicked", lambda *_: win.action_add_frame(None, None))
    del_fr = Gtk.Button(label="−")
    del_fr.set_tooltip_text("Delete current frame")
    del_fr.connect("clicked", lambda *_: win.action_delete_frame(None, None))
    frame_btns.append(add_fr)
    frame_btns.append(del_fr)
    frames_body.append(frame_btns)

    play_row = Gtk.Box(spacing=4)
    win.play_btn = Gtk.Button(label="▶")
    win.play_btn.set_tooltip_text("Play / pause animation (Space)")
    win.play_btn.connect("clicked", lambda *_: win.action_play_animation(None, None))
    win.stop_btn = Gtk.Button(label="■")
    win.stop_btn.set_tooltip_text("Stop playback")
    win.stop_btn.connect("clicked", lambda *_: win.action_stop_animation(None, None))
    play_row.append(win.play_btn)
    play_row.append(win.stop_btn)
    frames_body.append(play_row)

    fps_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    fps_row.append(Gtk.Label(label="FPS", xalign=0))
    win.fps_spin = Gtk.SpinButton.new_with_range(1, 60, 1)
    configure_numeric_spin(win.fps_spin)
    win.fps_spin.set_value(12)
    win.fps_spin.set_hexpand(True)
    win.fps_spin.set_tooltip_text("Playback speed in frames per second")
    win.fps_spin.connect("value-changed", win._on_fps_changed)
    fps_row.append(win.fps_spin)
    frames_body.append(fps_row)

    onion_row = Gtk.Box(spacing=6)
    win.onion_check = Gtk.CheckButton(label="Onion skin")
    win.onion_check.set_active(True)
    win.onion_check.set_tooltip_text("Show the previous frame faintly while drawing")
    win.onion_check.connect("toggled", win._on_onion_toggled)
    onion_row.append(win.onion_check)
    frames_body.append(onion_row)

    win.frames_expander.connect(
        "notify::expanded",
        win._on_sidebar_section_expanded,
        win.frame_scroll,
    )

    # --- Layers (collapsible) ---
    win.layers_expander = Gtk.Expander(label="Layers", expanded=False)
    win.layers_expander.set_hexpand(True)
    win.layers_expander.set_vexpand(False)
    win.layers_expander.set_valign(Gtk.Align.START)
    layers_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    win.layers_expander.set_child(layers_body)
    win._sidebar_split.set_end_child(win.layers_expander)

    win.layer_list = Gtk.ListBox()
    win.layer_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
    win.layer_list.set_valign(Gtk.Align.START)
    win.layer_list.connect("row-selected", win._on_layer_selected)
    # Drop target for layer reorder (document indices carried as int64)
    drop = Gtk.DropTarget.new(GObject.TYPE_INT64, Gdk.DragAction.MOVE)
    drop.connect("drop", win._on_layer_drop)
    win.layer_list.add_controller(drop)
    win.layer_scroll = Gtk.ScrolledWindow(vexpand=False, hexpand=True)
    win.layer_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    # Tall enough for one full opacity row; vexpand grows further when open.
    win.layer_scroll.set_min_content_height(96)
    win.layer_scroll.set_child(win.layer_list)
    layers_body.append(win.layer_scroll)
    side_btns = Gtk.Box(spacing=6)
    add_b = Gtk.Button(label="+")
    add_b.connect("clicked", lambda *_: win.action_add_layer(None, None))
    del_b = Gtk.Button(label="−")
    del_b.connect("clicked", lambda *_: win.action_delete_layer(None, None))
    side_btns.append(add_b)
    side_btns.append(del_b)
    layers_body.append(side_btns)

    win.layers_expander.connect(
        "notify::expanded",
        win._on_sidebar_section_expanded,
        win.layer_scroll,
    )
    return side


class SidebarMixin:
    """Frames and Layers panel behaviour for ``MainWindow``."""

    def _on_sidebar_height_notify(self, *_a) -> None:
        if not self._sidebar_sized:
            self._apply_default_sidebar_split()
            return
        # Later layout (e.g. horizontal pane sizing) can redistribute a vertical
        # paned with resize_start_child=True — keep collapsed headers pinned.
        if (
            not self.frames_expander.get_expanded()
            and not self.layers_expander.get_expanded()
        ):
            self._pack_sidebar_headers_top()

    def _apply_default_sidebar_split(self) -> bool:
        """Give Frames a smaller share so Layers (and +/−) stay usable."""
        if self._sidebar_sized:
            return False
        split = self._sidebar_split
        h = split.get_height()
        if h < 160:
            return False
        if not self.frames_expander.get_expanded() and not self.layers_expander.get_expanded():
            # Both collapsed — stack headers at the top instead of mid-column.
            _min_h, nat_h, *_ = self.frames_expander.measure(Gtk.Orientation.VERTICAL, -1)
            if nat_h <= 0:
                return False
            split.set_resize_start_child(False)
            split.set_resize_end_child(True)
            split.set_position(nat_h)
            if abs(split.get_position() - nat_h) <= 2:
                self._sidebar_sized = True
            return False
        # ~38% frames / 62% layers — layers need room for opacity rows + buttons
        split.set_resize_start_child(True)
        split.set_resize_end_child(True)
        split.set_position(max(120, int(h * 0.38)))
        self._sidebar_sized = True
        return False

    def _pack_sidebar_headers_top(self) -> bool:
        """Place both Frames and Layers headers at the top of the sidebar column."""
        split = self._sidebar_split
        _min_h, nat_h, *_ = self.frames_expander.measure(Gtk.Orientation.VERTICAL, -1)
        if nat_h <= 0:
            return True  # not measured yet — retry next idle
        # Freeze start size so window/pane resizes don't push Layers mid-column.
        split.set_resize_start_child(False)
        split.set_resize_end_child(True)
        split.set_position(nat_h)
        return False

    def _sidebar_give_space_to_frames(self) -> bool:
        """Push the Layers header to the bottom so Frames can use the column."""
        split = self._sidebar_split
        h = split.get_height()
        if h <= 0:
            return False
        _min_h, nat_h, *_ = self.layers_expander.measure(Gtk.Orientation.VERTICAL, -1)
        if nat_h <= 0:
            return True
        handle = 8
        split.set_resize_start_child(True)
        split.set_resize_end_child(False)
        split.set_position(max(0, h - nat_h - handle))
        return False

    def _sync_anim_controls_from_doc(self) -> None:
        if not self.document:
            return
        self._syncing_frames = True
        try:
            self.fps_spin.set_value(float(self.document.fps))
            self.onion_check.set_active(bool(self.document.onion_skin))
        finally:
            self._syncing_frames = False

    def _on_sidebar_section_expanded(self, expander: Gtk.Expander, _pspec, scroll: Gtk.ScrolledWindow) -> None:
        """Collapse frees vertical space in the Frames|Layers paned."""
        expanded = bool(expander.get_expanded())
        expander.set_vexpand(expanded)
        # FILL when open so the expander uses the paned allocation (list can grow).
        # START when collapsed so the header stays at the top of a tall slot.
        expander.set_valign(Gtk.Align.FILL if expanded else Gtk.Align.START)
        scroll.set_vexpand(expanded)
        # Let the other section claim the freed space.
        other = self.layers_expander if expander is self.frames_expander else self.frames_expander
        if expanded and other.get_expanded():
            # Both open — keep sharing; re-apply a balanced split once laid out.
            self._sidebar_sized = False
            GLib.idle_add(self._apply_default_sidebar_split)
        elif expanded and not other.get_expanded():
            # One open — give it the column; keep the collapsed header at the edge.
            if expander is self.frames_expander:
                GLib.idle_add(self._sidebar_give_space_to_frames)
            else:
                GLib.idle_add(self._pack_sidebar_headers_top)
        elif not expanded and other.get_expanded():
            other.set_vexpand(True)
            other.set_valign(Gtk.Align.FILL)
            if other is self.frames_expander:
                GLib.idle_add(self._sidebar_give_space_to_frames)
            else:
                GLib.idle_add(self._pack_sidebar_headers_top)
        elif not expanded and not other.get_expanded():
            # Both collapsed — keep headers stacked at the top of the column.
            GLib.idle_add(self._pack_sidebar_headers_top)

    def _rebuild_frames(self) -> None:
        self._dismiss_frame_context_menu()
        self._syncing_frames = True
        try:
            while (child := self.frame_list.get_row_at_index(0)) is not None:
                self.frame_list.remove(child)
            if not self.document:
                return
            for i, fr in enumerate(self.document.frames):
                row = Gtk.ListBoxRow()
                row.set_activatable(True)
                row.set_vexpand(False)
                top = Gtk.Box(spacing=6, margin_top=2, margin_bottom=2, margin_start=4, margin_end=4)
                grip = Gtk.Label(label="≡", xalign=0.5)
                grip.add_css_class("dim-label")
                grip.set_tooltip_text("Drag to reorder")
                top.append(grip)
                label = Gtk.Label(label=f"{i + 1}. {fr.name}", xalign=0, hexpand=True)
                label.set_tooltip_text("Drag to reorder")
                top.append(label)
                row.set_child(top)
                row._frame_index = i  # type: ignore[attr-defined]
                self._attach_frame_drag(top, i)
                self._attach_frame_context_menu(row)
                self.frame_list.append(row)
                if i == self.document.current_frame_index:
                    self.frame_list.select_row(row)
        finally:
            self._syncing_frames = False

    def _frame_context_menu_model(self) -> Gio.Menu:
        menu = getattr(self, "_frame_context_menu", None)
        if menu is None:
            menu = Gio.Menu()
            menu.append("Duplicate Frame", "win.duplicate_frame")
            menu.append("Delete Frame", "win.delete_frame")
            self._frame_context_menu = menu
        return menu

    def _dismiss_frame_context_menu(self) -> None:
        popover = getattr(self, "_frame_context_popover", None)
        if popover is None:
            return
        popover.popdown()
        popover.unparent()
        self._frame_context_popover = None

    def _attach_frame_context_menu(self, row: Gtk.ListBoxRow) -> None:
        click = Gtk.GestureClick.new()
        click.set_button(Gdk.BUTTON_SECONDARY)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def _pressed(gesture: Gtk.GestureClick, n_press: int, x: float, y: float, r=row) -> None:
            if n_press != 1:
                return
            self.frame_list.select_row(r)
            self._dismiss_frame_context_menu()
            popover = Gtk.PopoverMenu.new_from_model(self._frame_context_menu_model())
            popover.set_parent(r)
            popover.set_has_arrow(False)
            rect = Gdk.Rectangle()
            rect.x = int(x)
            rect.y = int(y)
            rect.width = 1
            rect.height = 1
            popover.set_pointing_to(rect)
            self._frame_context_popover = popover
            popover.popup()
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

        click.connect("pressed", _pressed)
        row.add_controller(click)

    def _attach_frame_drag(self, widget: Gtk.Widget, frame_index: int) -> None:
        source = Gtk.DragSource.new()
        source.set_actions(Gdk.DragAction.MOVE)

        def _prepare(_src, _x, _y, idx=frame_index):
            self._drag_frame_from = idx
            val = GObject.Value(GObject.TYPE_INT64, idx)
            return Gdk.ContentProvider.new_for_value(val)

        def _end(_src, _drag, _delete, *_a):
            self._drag_frame_from = None

        source.connect("prepare", _prepare)
        source.connect("drag-end", _end)
        widget.add_controller(source)

    def _on_frame_drop(self, _target, value, x, y) -> bool:
        if not self.document or self._anim_playing:
            return False
        # Only accept drags that started on a frame row (not a layer).
        from_idx = self._drag_frame_from
        if from_idx is None:
            return False
        try:
            if value is not None:
                from_idx = int(value)
        except (TypeError, ValueError):
            pass
        dest_row = None
        picked = self.frame_list.pick(x, y, Gtk.PickFlags.DEFAULT)
        w = picked
        while w is not None:
            if isinstance(w, Gtk.ListBoxRow):
                dest_row = w
                break
            w = w.get_parent()
        if dest_row is None:
            dest_row = self.frame_list.get_row_at_y(int(y))
        if dest_row is None:
            return False
        to_idx = getattr(dest_row, "_frame_index", None)
        if to_idx is None or to_idx == from_idx:
            return False
        self._push_history()
        self.document.move_frame(int(from_idx), int(to_idx))
        self.canvas.renderer.invalidate()
        self._rebuild_frames()
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()
        return True

    def _on_frame_selected(self, _lb, row: Optional[Gtk.ListBoxRow]) -> None:
        if self._syncing_frames or row is None or not self.document:
            return
        if self._anim_playing:
            return
        idx = getattr(row, "_frame_index", 0)
        if idx == self.document.current_frame_index:
            return
        self.document.set_current_frame(int(idx))
        self.canvas.renderer.invalidate()
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()

    def _on_fps_changed(self, spin: Gtk.SpinButton) -> None:
        if self._syncing_frames or not self.document:
            return
        self.document.fps = float(spin.get_value())
        self.document.dirty = True
        if self._anim_playing:
            self._restart_anim_timer()

    def _on_onion_toggled(self, btn: Gtk.CheckButton) -> None:
        if self._syncing_frames or not self.document:
            return
        self.document.onion_skin = bool(btn.get_active())
        self.document.dirty = True
        self.canvas.queue_render()

    def _goto_frame(self, index: int, *, rebuild_list: bool = True) -> None:
        if not self.document:
            return
        self.document.set_current_frame(index)
        if rebuild_list:
            self.canvas.renderer.invalidate()
            self._rebuild_frames()
            self._rebuild_layers()
        else:
            # Playback tick: textures are keyed by layer id + unique revision, so
            # the other frame's layers are already resident — no invalidation.
            self._syncing_frames = True
            try:
                row = self.frame_list.get_row_at_index(index)
                if row is not None:
                    self.frame_list.select_row(row)
            finally:
                self._syncing_frames = False
            self._rebuild_layers(only_if_changed=True)
        self.canvas.queue_render()
        self._set_status()

    def action_add_frame(self, *_a) -> None:
        if not self.document:
            return
        self.action_stop_animation()
        self._push_history()
        self.document.add_frame(duplicate=False)
        self.canvas.renderer.invalidate()
        self._rebuild_frames()
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()

    def action_duplicate_frame(self, *_a) -> None:
        if not self.document:
            return
        self.action_stop_animation()
        self._push_history()
        self.document.add_frame(duplicate=True)
        self.canvas.renderer.invalidate()
        self._rebuild_frames()
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()

    def action_delete_frame(self, *_a) -> None:
        if not self.document or self.document.frame_count <= 1:
            return
        self.action_stop_animation()
        self._push_history()
        self.document.delete_frame()
        self.canvas.renderer.invalidate()
        self._rebuild_frames()
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()

    def action_play_animation(self, *_a) -> None:
        """Start playback, or pause (stop) if already playing."""
        if not self.document or self.document.frame_count < 1:
            return
        if self._anim_playing:
            self.action_stop_animation()
            return
        self._anim_playing = True
        self.canvas.animation_playing = True
        self._restart_anim_timer()
        self.canvas.queue_render()
        self._set_status()

    def action_stop_animation(self, *_a) -> None:
        self._anim_playing = False
        self.canvas.animation_playing = False
        if self._anim_timer_id is not None:
            GLib.source_remove(self._anim_timer_id)
            self._anim_timer_id = None
        self.canvas.queue_render()
        self._set_status()

    def _restart_anim_timer(self) -> None:
        if self._anim_timer_id is not None:
            GLib.source_remove(self._anim_timer_id)
            self._anim_timer_id = None
        if not self._anim_playing or not self.document:
            return
        fps = max(1.0, float(self.document.fps))
        interval_ms = max(1, int(round(1000.0 / fps)))
        self._anim_timer_id = GLib.timeout_add(interval_ms, self._anim_tick)

    def _anim_tick(self) -> bool:
        if not self._anim_playing or not self.document:
            self._anim_timer_id = None
            return False
        n = self.document.frame_count
        if n <= 0:
            return True
        nxt = (self.document.current_frame_index + 1) % n
        self._goto_frame(nxt, rebuild_list=False)
        return True

    def _layers_signature(self) -> Optional[tuple]:
        """Cheap identity of what the layer panel shows (for playback ticks).

        Rows bind callbacks by index, not layer id, so identical-looking
        stacks on different frames can share the same widgets.
        """
        if not self.document:
            return None
        return (
            self.document.active_layer_index,
            tuple((ly.name, ly.visible, round(ly.opacity, 4)) for ly in self.document.layers),
        )

    def _rebuild_layers(self, *, only_if_changed: bool = False) -> None:
        sig = self._layers_signature()
        if (
            only_if_changed
            and sig is not None
            and sig == self._layers_sig
            and getattr(self, "_layer_rename_entry", None) is None
        ):
            return
        self._layers_sig = sig
        self._sync_rig_panel()
        self._dismiss_layer_context_menu()
        self._layer_rename_entry = None
        self._layer_rename_done = True
        self._syncing_layers = True
        try:
            while (child := self.layer_list.get_row_at_index(0)) is not None:
                self.layer_list.remove(child)
            if not self.document:
                return
            # Show top layer first
            for i, ly in enumerate(reversed(self.document.layers)):
                idx = len(self.document.layers) - 1 - i
                row = Gtk.ListBoxRow()
                row.set_activatable(True)
                row.set_vexpand(False)
                col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=4, margin_bottom=4, margin_start=6, margin_end=6)
                col.set_vexpand(False)

                top = Gtk.Box(spacing=6)
                grip = Gtk.Label(label="≡", xalign=0.5)
                grip.add_css_class("dim-label")
                grip.set_tooltip_text("Drag to reorder")
                top.append(grip)
                vis = Gtk.CheckButton()
                vis.set_active(ly.visible)
                vis.set_tooltip_text("Visible")
                vis.connect("toggled", self._toggle_vis, idx)
                top.append(vis)
                name_slot = Gtk.Box(hexpand=True)
                name = Gtk.Label(label=ly.name, xalign=0, hexpand=True)
                name.set_tooltip_text("Double-click to rename")
                name_slot.append(name)
                top.append(name_slot)
                col.append(top)

                opac = Gtk.Box(spacing=4)
                opac.append(Gtk.Label(label="Opacity", xalign=0))
                scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 0.01)
                scale.set_value(max(0, min(100, ly.opacity * 100.0)))
                scale.set_draw_value(True)
                scale.set_value_pos(Gtk.PositionType.RIGHT)
                scale.set_hexpand(True)
                scale.set_vexpand(False)
                scale.set_valign(Gtk.Align.CENTER)
                scale.set_digits(2)
                scale.set_tooltip_text("Layer transparency")
                scale.connect("value-changed", self._on_layer_opacity, idx)
                # Arm history once per slider interaction (mouse or keyboard)
                scale.connect("change-value", self._on_opacity_change_value)
                opac.append(scale)
                col.append(opac)

                row.set_child(col)
                row._layer_index = idx  # type: ignore[attr-defined]
                row._name_slot = name_slot  # type: ignore[attr-defined]
                row._name_label = name  # type: ignore[attr-defined]
                # Drag from name row only so the opacity slider stays usable
                self._attach_layer_drag(top, idx)
                self._attach_layer_rename(name, row)
                self._attach_layer_context_menu(row)
                self.layer_list.append(row)
                if idx == self.document.active_layer_index:
                    self.layer_list.select_row(row)
        finally:
            self._syncing_layers = False
            self._opacity_hist_armed = False

    def _layer_context_menu_model(self) -> Gio.Menu:
        menu = getattr(self, "_layer_context_menu", None)
        if menu is None:
            menu = Gio.Menu()
            menu.append("Duplicate Layer", "win.duplicate_layer")
            menu.append("Rename Layer…", "win.rename_layer")
            menu.append("Move to Top", "win.move_layer_to_top")
            menu.append("Move to Bottom", "win.move_layer_to_bottom")
            menu.append("Delete Layer", "win.delete_layer")
            menu.append("Merge with Layer Below", "win.merge_layer_down")
            menu.append("Merge Visible", "win.merge_visible_layers")
            menu.append("Merge All", "win.merge_all_layers")
            menu.append("Export Selected Layer…", "win.export_layer")
            self._layer_context_menu = menu
        return menu

    def _dismiss_layer_context_menu(self) -> None:
        popover = getattr(self, "_layer_context_popover", None)
        if popover is None:
            return
        popover.popdown()
        popover.unparent()
        self._layer_context_popover = None

    def _attach_layer_context_menu(self, row: Gtk.ListBoxRow) -> None:
        click = Gtk.GestureClick.new()
        click.set_button(Gdk.BUTTON_SECONDARY)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def _pressed(gesture: Gtk.GestureClick, n_press: int, x: float, y: float, r=row) -> None:
            if n_press != 1:
                return
            self.layer_list.select_row(r)
            self._dismiss_layer_context_menu()
            popover = Gtk.PopoverMenu.new_from_model(self._layer_context_menu_model())
            popover.set_parent(r)
            popover.set_has_arrow(False)
            rect = Gdk.Rectangle()
            rect.x = int(x)
            rect.y = int(y)
            rect.width = 1
            rect.height = 1
            popover.set_pointing_to(rect)
            self._layer_context_popover = popover
            popover.popup()
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

        click.connect("pressed", _pressed)
        row.add_controller(click)

    def _attach_layer_rename(self, label: Gtk.Label, row: Gtk.ListBoxRow) -> None:
        click = Gtk.GestureClick.new()
        click.set_button(Gdk.BUTTON_PRIMARY)

        def _released(gesture: Gtk.GestureClick, n_press: int, _x: float, _y: float, r=row) -> None:
            if n_press != 2:
                return
            self.layer_list.select_row(r)
            self._begin_layer_rename(r)
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

        click.connect("released", _released)
        label.add_controller(click)

    def _begin_layer_rename(self, row: Gtk.ListBoxRow) -> None:
        if not self.document or getattr(self, "_layer_rename_entry", None) is not None:
            return
        idx = getattr(row, "_layer_index", None)
        slot = getattr(row, "_name_slot", None)
        if idx is None or slot is None or not (0 <= idx < len(self.document.layers)):
            return
        self._dismiss_layer_context_menu()
        while (child := slot.get_first_child()) is not None:
            slot.remove(child)
        entry = Gtk.Entry()
        entry.set_text(self.document.layers[idx].name)
        entry.set_hexpand(True)
        entry.set_width_chars(8)
        self._layer_rename_entry = entry
        self._layer_rename_index = idx
        self._layer_rename_done = False

        def _finish(cancelled: bool = False) -> None:
            if self._layer_rename_done:
                return
            self._layer_rename_done = True
            self._layer_rename_entry = None
            rename_idx = self._layer_rename_index
            text = entry.get_text().strip()
            if (
                not cancelled
                and self.document
                and 0 <= rename_idx < len(self.document.layers)
                and text
                and text != self.document.layers[rename_idx].name
            ):
                self._push_history()
                self.document.layers[rename_idx].name = text
                self.document.dirty = True
                self.document._sync_frame_from_layers()
            self._rebuild_layers()
            self._set_status()

        entry.connect("activate", lambda *_: _finish(False))
        key = Gtk.EventControllerKey.new()

        def _on_key(_ctrl, keyval, _keycode, _state):
            if keyval == Gdk.KEY_Escape:
                _finish(True)
                return True
            return False

        key.connect("key-pressed", _on_key)
        entry.add_controller(key)

        def _on_focus_leave(_ctrl):
            GLib.idle_add(lambda: (_finish(False), False)[1])

        focus = Gtk.EventControllerFocus.new()
        focus.connect("leave", _on_focus_leave)
        entry.add_controller(focus)

        slot.append(entry)
        entry.grab_focus()
        entry.select_region(0, -1)

    def action_rename_layer(self, *_a) -> None:
        if not self.document:
            return
        row = self.layer_list.get_selected_row()
        if row is None:
            return
        self._begin_layer_rename(row)

    def _attach_layer_drag(self, widget: Gtk.Widget, layer_index: int) -> None:
        source = Gtk.DragSource.new()
        source.set_actions(Gdk.DragAction.MOVE)

        def _prepare(_src, _x, _y, idx=layer_index):
            self._drag_layer_from = idx
            val = GObject.Value(GObject.TYPE_INT64, idx)
            return Gdk.ContentProvider.new_for_value(val)

        def _end(_src, _drag, _delete, *_a):
            self._drag_layer_from = None

        source.connect("prepare", _prepare)
        source.connect("drag-end", _end)
        widget.add_controller(source)

    def _on_layer_drop(self, _target, value, x, y) -> bool:
        if not self.document:
            return False
        # Only accept drags that started on a layer row (not a frame).
        from_idx = self._drag_layer_from
        if from_idx is None:
            return False
        try:
            if value is not None:
                from_idx = int(value)
        except (TypeError, ValueError):
            pass
        # Find destination row under pointer
        dest_row = None
        picked = self.layer_list.pick(x, y, Gtk.PickFlags.DEFAULT)
        w = picked
        while w is not None:
            if isinstance(w, Gtk.ListBoxRow):
                dest_row = w
                break
            w = w.get_parent()
        if dest_row is None:
            dest_row = self.layer_list.get_row_at_y(int(y))
        if dest_row is None:
            return False
        to_idx = getattr(dest_row, "_layer_index", None)
        if to_idx is None or to_idx == from_idx:
            return False
        self._push_history()
        self.document.move_layer(int(from_idx), int(to_idx))
        self.canvas.renderer.invalidate()
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()
        return True

    def _on_opacity_change_value(self, _scale, _scroll, _value) -> bool:
        self._opacity_hist_armed = True
        return False

    def _on_layer_opacity(self, scale: Gtk.Scale, index: int) -> None:
        if self._syncing_layers or not self.document:
            return
        if not (0 <= index < len(self.document.layers)):
            return
        layer = self.document.layers[index]
        new_op = max(0.0, min(1.0, scale.get_value() / 100.0))
        if abs(layer.opacity - new_op) < 0.0005:
            return
        if self._opacity_hist_armed:
            self._push_history()
            self._opacity_hist_armed = False
        layer.opacity = new_op
        self.document.dirty = True
        self.canvas.queue_render()
        self._set_status()

    def _toggle_vis(self, btn: Gtk.CheckButton, index: int) -> None:
        if self._syncing_layers or not self.document:
            return
        layer = self.document.layers[index]
        if layer.visible == btn.get_active():
            return
        self._push_history()
        layer.visible = btn.get_active()
        self.canvas.renderer.invalidate(layer.id)
        self.canvas.queue_render()
        self._set_status()

    def _on_layer_selected(self, _lb, row: Optional[Gtk.ListBoxRow]) -> None:
        if row is None or not self.document:
            return
        self.document.active_layer_index = getattr(row, "_layer_index", 0)
        self.document._sync_frame_from_layers()
        self._set_status()

    def action_add_layer(self, *_a) -> None:
        if not self.document:
            return
        self._push_history()
        self.document.add_layer()
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()

    def action_duplicate_layer(self, *_a) -> None:
        if not self.document:
            return
        self._push_history()
        if self.document.duplicate_layer() is None:
            return
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()

    def action_delete_layer(self, *_a) -> None:
        if not self.document or len(self.document.layers) <= 1:
            return
        self._push_history()
        self.document.delete_layer(self.document.active_layer_index)
        self.canvas.renderer.invalidate()
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()

    def action_move_layer_to_top(self, *_a) -> None:
        if not self.document:
            return
        from_idx = self.document.active_layer_index
        to_idx = len(self.document.layers) - 1
        if from_idx >= to_idx:
            return
        self._push_history()
        self.document.move_layer(from_idx, to_idx)
        self.canvas.renderer.invalidate()
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()

    def action_move_layer_to_bottom(self, *_a) -> None:
        if not self.document:
            return
        from_idx = self.document.active_layer_index
        if from_idx <= 0:
            return
        self._push_history()
        self.document.move_layer(from_idx, 0)
        self.canvas.renderer.invalidate()
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()

    def action_merge_layer_down(self, *_a) -> None:
        if not self.document or self.document.active_layer_index <= 0:
            return
        idx = self.document.active_layer_index
        if not (self.document.layers[idx].visible and self.document.layers[idx - 1].visible):
            self._alert(
                "Can't merge a hidden layer",
                "Show both layers before merging down, or the hidden one's pixels would be lost.",
            )
            return
        self._push_history()
        if not self.document.merge_down():
            self.history.discard_last_push()
            return
        self.canvas.renderer.invalidate()
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()

    def action_merge_visible_layers(self, *_a) -> None:
        if not self.document:
            return
        visible = sum(1 for ly in self.document.layers if ly.visible)
        if visible < 2:
            return
        self._push_history()
        if not self.document.merge_visible():
            self.history.discard_last_push()
            return
        self.canvas.renderer.invalidate()
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()

    def action_merge_all_layers(self, *_a) -> None:
        if not self.document or len(self.document.layers) <= 1:
            return
        hidden = self.document.hidden_layer_count()
        if hidden:
            doc = self.document
            dlg = Gtk.AlertDialog()
            dlg.set_message("Discard hidden layers?")
            noun = "layer is" if hidden == 1 else "layers are"
            dlg.set_detail(
                f"{hidden} hidden {noun} not part of the flattened image and will be "
                "deleted. You can undo this afterwards."
            )
            dlg.set_buttons(["Cancel", "Merge All"])
            dlg.set_cancel_button(0)
            dlg.set_default_button(0)

            def on_choice(d: Gtk.AlertDialog, result) -> None:
                try:
                    choice = d.choose_finish(result)
                except GLib.Error:
                    return
                if choice == 1 and self.document is doc:
                    self._merge_all_now()

            dlg.choose(self, None, on_choice)
            return
        self._merge_all_now()

    def _merge_all_now(self) -> None:
        if not self.document:
            return
        self._push_history()
        if not self.document.merge_all():
            self.history.discard_last_push()
            return
        self.canvas.renderer.invalidate()
        self._rebuild_layers()
        self.canvas.queue_render()
        self._set_status()
