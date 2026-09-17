from __future__ import annotations

import numpy as np

from inkobold.tools.base import BaseTool, ToolContext
from inkobold.tools.paint import fill_polygon_mask


class LassoTool(BaseTool):
    """Freehand lasso selection (Shift adds, Alt subtracts — replace by default)."""

    name = "Lasso"
    id = "lasso"
    uses_size = False
    uses_color = False
    supports_mirror = False
    modifies_pixels = False

    def __init__(self) -> None:
        super().__init__()
        self.points: list[tuple[float, float]] = []
        self._drawing = False
        self._mode = "replace"
        self._base_mask: np.ndarray | None = None

    def on_press(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        self._drawing = True
        self._mode = "add" if shift else ("subtract" if alt else "replace")
        self.points = [(x, y)]
        mask = ctx.document.selection.mask
        self._base_mask = None if mask is None else mask.copy()
        self._apply_selection(ctx)

    def on_drag(self, ctx: ToolContext, x: float, y: float, shift: bool = False, alt: bool = False) -> None:
        if not self._drawing:
            return
        if not self.points or (abs(self.points[-1][0] - x) + abs(self.points[-1][1] - y)) > 1.5:
            self.points.append((x, y))
            self._apply_selection(ctx)

    def on_release(self, ctx: ToolContext, x: float, y: float) -> None:
        if not self._drawing:
            return
        self._drawing = False
        self.points.append((x, y))
        self._apply_selection(ctx)
        self.points.clear()
        self._base_mask = None

    def reset(self) -> None:
        self._drawing = False
        self.points.clear()
        self._base_mask = None

    def _apply_selection(self, ctx: ToolContext) -> None:
        doc = ctx.document
        new_mask = np.zeros((doc.height, doc.width), dtype=np.uint8)
        fill_polygon_mask(new_mask, self.points)
        if self._mode == "replace" or self._base_mask is None:
            doc.selection.mask = new_mask
        elif self._mode == "add":
            base = self._base_mask.copy()
            base[:] = np.maximum(base, new_mask)
            doc.selection.mask = base
        else:
            base = self._base_mask.copy()
            base[new_mask > 0] = 0
            doc.selection.mask = base
        doc.mark_dirty(content=False)
