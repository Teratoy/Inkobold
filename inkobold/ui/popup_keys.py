"""Esc-to-close, Enter-to-confirm, and Tab-among-editables for popups and tool options."""

from __future__ import annotations

from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402


def collect_editables(root: Gtk.Widget) -> list[Gtk.Widget]:
    """Visible, sensitive, focusable Gtk.Editable widgets under root (document order)."""
    found: list[Gtk.Widget] = []

    def walk(w: Optional[Gtk.Widget]) -> None:
        if w is None or not w.get_visible():
            return
        # SpinButton/Entry/Text implement Editable; do not recurse into their internals.
        if isinstance(w, Gtk.Editable):
            if w.get_sensitive() and w.get_can_focus():
                found.append(w)
            return
        child = w.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(root)
    return found


def _suggested_action_button(root: Optional[Gtk.Widget]) -> Optional[Gtk.Button]:
    """First visible, sensitive suggested-action button under root (document order)."""
    found: Optional[Gtk.Button] = None

    def walk(w: Optional[Gtk.Widget]) -> None:
        nonlocal found
        if found is not None or w is None or not w.get_visible():
            return
        if (
            isinstance(w, Gtk.Button)
            and w.has_css_class("suggested-action")
            and w.get_sensitive()
        ):
            found = w
            return
        child = w.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(root)
    return found


def _editable_for_focus(focus: Optional[Gtk.Widget], editables: list[Gtk.Widget]) -> Optional[Gtk.Widget]:
    w = focus
    while w is not None:
        if w in editables:
            return w
        w = w.get_parent()
    return None


def focus_adjacent_editable(
    root: Gtk.Widget,
    current: Optional[Gtk.Widget],
    *,
    backward: bool = False,
) -> bool:
    """Move focus to the next/previous editable under root. True if focus moved."""
    editables = collect_editables(root)
    if len(editables) < 2:
        return False
    current_editable = _editable_for_focus(current, editables)
    if current_editable is None:
        return False
    idx = editables.index(current_editable)
    nxt = editables[(idx - 1) % len(editables)] if backward else editables[(idx + 1) % len(editables)]
    nxt.grab_focus()
    select = getattr(nxt, "select_region", None)
    if callable(select):
        try:
            select(0, -1)
        except Exception:
            pass
    return True


def _tab_is_backward(keyval: int, state: Gdk.ModifierType) -> bool:
    mods = state & Gtk.accelerator_get_default_mod_mask()
    return keyval == Gdk.KEY_ISO_Left_Tab or bool(mods & Gdk.ModifierType.SHIFT_MASK)


def attach_tab_among_editables(window: Gtk.Window, root: Optional[Gtk.Widget] = None) -> None:
    """Tab / Shift+Tab cycles editables under root when one is already focused."""
    scope = root if root is not None else window
    keys = Gtk.EventControllerKey.new()
    keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

    def _on_key(_c, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        if keyval not in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            return False
        mods = state & Gtk.accelerator_get_default_mod_mask()
        if mods & ~Gdk.ModifierType.SHIFT_MASK:
            return False
        return focus_adjacent_editable(
            scope,
            window.get_focus(),
            backward=_tab_is_backward(keyval, state),
        )

    keys.connect("key-pressed", _on_key)
    window.add_controller(keys)


def _flush_focused_spin(window: Gtk.Window) -> None:
    """Commit in-progress SpinButton text so Enter-to-confirm sees the typed value."""
    w = window.get_focus()
    while w is not None:
        if isinstance(w, Gtk.SpinButton):
            w.update()
            return
        w = w.get_parent()


def attach_popup_keys(window: Gtk.Window) -> None:
    """Esc closes; Enter activates suggested-action; Tab cycles editables while editing."""
    keys = Gtk.EventControllerKey.new()
    keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

    def _on_key(_c, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        if keyval == Gdk.KEY_Escape:
            window.close()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            mods = state & Gtk.accelerator_get_default_mod_mask()
            if mods:
                return False
            # Focused buttons keep their own Enter (Cancel, presets, …).
            focus = window.get_focus()
            if isinstance(focus, Gtk.Button):
                return False
            btn = _suggested_action_button(window)
            if btn is not None:
                _flush_focused_spin(window)
                btn.activate()
                return True
            return False
        if keyval not in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            return False
        mods = state & Gtk.accelerator_get_default_mod_mask()
        if mods & ~Gdk.ModifierType.SHIFT_MASK:
            return False
        return focus_adjacent_editable(
            window,
            window.get_focus(),
            backward=_tab_is_backward(keyval, state),
        )

    keys.connect("key-pressed", _on_key)
    window.add_controller(keys)
