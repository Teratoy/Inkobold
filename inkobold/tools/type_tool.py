"""Click to place text; edit and move until Enter rasterizes it onto the layer."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import numpy as np

from inkobold.tools.base import BaseTool, ToolContext
from inkobold.tools.paint import blit_text, measure_text


class TypeTool(BaseTool):
    """Type tool: live editable text preview, committed on Enter."""

    name = "Type"
    id = "type"
    default_size = 32.0
    default_color = (0, 0, 0, 255)
    uses_size = True
    uses_color = True
    uses_opacity = True
    uses_type_options = True
    supports_mirror = False

    def __init__(self) -> None:
        super().__init__()
        self.font_source: str = "system"  # "system" | "library"
        self.font_path: Optional[Path] = None
        self._editing = False
        self._text = ""
        self._x = 0.0
        self._y = 0.0
        self._drag_dx = 0.0
        self._drag_dy = 0.0
        self._moving = False
        self._snapshot: np.ndarray | None = None
        self._bbox: tuple[float, float, float, float] | None = None
        self._created_layer_id: str | None = None
        self._on_editing_changed: Callable[[bool], None] | None = None

    @property
    def editing(self) -> bool:
        return self._editing

    def set_editing_changed_callback(self, cb: Callable[[bool], None] | None) -> None:
        self._on_editing_changed = cb

    def set_font_path(self, path: Optional[Path]) -> None:
        self.font_path = Path(path) if path is not None else None

    def set_font_source(self, source: str) -> None:
        self.font_source = "library" if source == "library" else "system"

    def _font_file(self) -> str | None:
        if self.font_path is None:
            return None
        try:
            if self.font_path.is_file():
                return str(self.font_path)
        except OSError:
            return None
        return None

    def _notify_editing(self, active: bool) -> None:
        if self._on_editing_changed is not None:
            self._on_editing_changed(active)

    def _discard_created_layer(self, ctx: ToolContext) -> None:
        layer_id = self._created_layer_id
        self._created_layer_id = None
        if not layer_id:
            return
        doc = ctx.document
        for i, ly in enumerate(doc.layers):
            if ly.id == layer_id:
                if len(doc.layers) > 1:
                    doc.delete_layer(i)
                else:
                    # Last layer: clear instead of deleting.
                    ly.clear()
                    doc.mark_dirty()
                return

    def _begin_edit(self, ctx: ToolContext, x: float, y: float) -> None:
        layer = ctx.document.add_layer(name="Text")
        ctx.document._sync_frame_from_layers()
        self._created_layer_id = layer.id
        self._snapshot = layer.pixels.copy()
        self._editing = True
        self._text = ""
        self._x, self._y = x, y
        self._bbox = None
        self._moving = False
        self._notify_editing(True)
        self.redraw(ctx)

    def _end_edit(self, *, commit: bool, ctx: ToolContext | None = None) -> None:
        if not self._editing:
            return
        if commit and ctx is not None and self._text:
            ly = ctx.document.active_layer
            label = self._text.strip().split("\n", 1)[0].strip()
            if label:
                ly.name = label[:48]
            self._created_layer_id = None
        elif ctx is not None:
            # Cancel or empty Enter: drop the text layer we created.
            self._discard_created_layer(ctx)
        self._editing = False
        self._text = ""
        self._snapshot = None
        self._bbox = None
        self._moving = False
        self._notify_editing(False)

    def commit(self, ctx: ToolContext) -> bool:
        """Finalize live text onto the layer. Returns True if anything changed."""
        if not self._editing:
            return False
        if not self._text:
            self._end_edit(commit=False, ctx=ctx)
            return True
        # Pixels already hold the rasterized preview — just drop the snapshot.
        self._end_edit(commit=True, ctx=ctx)
        return True

    def cancel(self, ctx: ToolContext) -> bool:
        """Discard the live edit and remove the temporary text layer."""
        if not self._editing:
            return False
        self._end_edit(commit=False, ctx=ctx)
        return True

    def redraw(self, ctx: ToolContext) -> None:
        if not self._editing or self._snapshot is None:
            return
        pixels = ctx.document.active_layer.pixels
        np.copyto(pixels, self._snapshot)
        if self._text:
            self._bbox = blit_text(
                pixels,
                self._text,
                self._x,
                self._y,
                font_path=self._font_file(),
                size=max(1.0, float(ctx.brush_size)),
                color=self._paint_color(ctx),
                mask=self._mask(ctx),
            )
        else:
            left, top, right, bottom = measure_text(
                "Mg",
                self._font_file(),
                max(1.0, float(ctx.brush_size)),
            )
            # Empty caret box uses em-box height at the click (top-left anchor).
            self._bbox = (
                self._x,
                self._y,
                self._x + max(1.0, right - left),
                self._y + max(1.0, bottom - top),
            )
        ctx.document.mark_dirty()

    def caret_screen_hint(self) -> tuple[float, float, float, float] | None:
        """Layer-local caret segment (x0, y0, x1, y1) for guide drawing."""
        if not self._editing:
            return None
        if self._bbox is not None and self._text:
            x1 = self._bbox[2]
            y0, y1 = self._bbox[1], self._bbox[3]
            return x1, y0, x1, y1
        size = max(1.0, float(self.brush_size))
        return self._x, self._y, self._x, self._y + size

    def guide_box(self) -> tuple[float, float, float, float] | None:
        if not self._editing or self._bbox is None:
            return None
        return self._bbox

    def on_press(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        if self._editing:
            self._moving = True
            self._drag_dx = x - self._x
            self._drag_dy = y - self._y
            return
        self._begin_edit(ctx, x, y)

    def on_drag(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        if not self._editing or not self._moving:
            return
        self._x = x - self._drag_dx
        self._y = y - self._drag_dy
        self.redraw(ctx)

    def on_release(self, ctx: ToolContext, x: float, y: float) -> None:
        self._moving = False

    def on_text_key(self, ctx: ToolContext, keyval: int, text: str, *, shift: bool = False) -> bool:
        """Handle a key while editing. Returns True if consumed."""
        if not self._editing:
            return False

        # Lazy import keeps tools free of GTK when used from tests.
        from gi.repository import Gdk

        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if shift:
                self._text += "\n"
                self.redraw(ctx)
                return True
            self.commit(ctx)
            return True
        if keyval == Gdk.KEY_Escape:
            self.cancel(ctx)
            return True
        if keyval in (Gdk.KEY_BackSpace,):
            if self._text:
                self._text = self._text[:-1]
                self.redraw(ctx)
            return True
        if keyval == Gdk.KEY_Delete:
            return True
        if not text:
            return False
        # Ignore pure control characters except tab.
        if text == "\t":
            self._text += "    "
            self.redraw(ctx)
            return True
        if text.isprintable() or text == "\n":
            self._text += text
            self.redraw(ctx)
            return True
        return False

    def reset(self) -> None:
        # Prefer cancel(ctx) so a created text layer is removed. reset() alone
        # only clears edit state (used after cancel, or when no pixels changed).
        was = self._editing
        self._editing = False
        self._text = ""
        self._snapshot = None
        self._bbox = None
        self._moving = False
        self._created_layer_id = None
        if was:
            self._notify_editing(False)
