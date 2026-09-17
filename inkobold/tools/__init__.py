from inkobold.tools.base import ToolContext
from inkobold.tools.brush import BrushTool
from inkobold.tools.curve import CurveTool
from inkobold.tools.eraser import EraserTool
from inkobold.tools.fill import FillTool
from inkobold.tools.lasso import LassoTool
from inkobold.tools.line import LineTool
from inkobold.tools.liquify import LiquifyTool
from inkobold.tools.move import MoveTool, TransformTool
from inkobold.tools.pen import PenTool
from inkobold.tools.pen3d import Fill3DTool, Pen3DTool
from inkobold.tools.replace_color import ReplaceColorTool
from inkobold.tools.smear import SmearTool
from inkobold.tools.type_tool import TypeTool


def default_tools() -> dict:
    tools = [
        PenTool(),
        LineTool(),
        CurveTool(),
        BrushTool(),
        FillTool(),
        Pen3DTool(),
        Fill3DTool(),
        EraserTool(),
        SmearTool(),
        LiquifyTool(),
        ReplaceColorTool(),
        TransformTool(),
        LassoTool(),
        TypeTool(),
    ]
    return {t.id: t for t in tools}


__all__ = [
    "ToolContext",
    "default_tools",
    "PenTool",
    "LineTool",
    "CurveTool",
    "BrushTool",
    "FillTool",
    "Pen3DTool",
    "Fill3DTool",
    "EraserTool",
    "SmearTool",
    "LiquifyTool",
    "ReplaceColorTool",
    "TransformTool",
    "MoveTool",
    "LassoTool",
    "TypeTool",
]
