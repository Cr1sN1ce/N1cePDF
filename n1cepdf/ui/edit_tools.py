"""Editing tool definitions (one primary tool active at a time)."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Tool:
    id: str
    label: str
    icon: str
    shortcut: str
    cursor: str      # name of a Qt.CursorShape member
    hint: str


TOOLS: tuple[Tool, ...] = (
    Tool("browse", "Browse / Select Text", "cursor", "V", "ArrowCursor",
         "Select and copy text. Nothing is modified."),
    Tool("select", "Select / Move Objects", "select", "S", "ArrowCursor",
         "Click an added object to select it; drag to move, drag handles to resize."),
    Tool("edit_text", "Edit Text", "text_edit", "T", "IBeamCursor",
         "Click existing text to edit it. Esc cancels."),
    Tool("add_text", "Add Text", "text_add", "Shift+T", "CrossCursor",
         "Click on the page to create a text box, then type."),
    Tool("edit_images", "Edit Images", "image_edit", "I", "ArrowCursor",
         "Click an existing image to select it; drag to move or resize; Delete removes it."),
    Tool("add_image", "Add Image", "image_add", "Shift+I", "CrossCursor",
         "Click where the image should go, then choose a PNG, JPEG or WebP file."),
    Tool("highlight", "Highlight", "highlight", "H", "IBeamCursor", "Drag across text to highlight it."),
    Tool("underline", "Underline", "underline", "U", "IBeamCursor", "Drag across text to underline it."),
    Tool("strike", "Strikethrough", "strike", "K", "IBeamCursor", "Drag across text to strike it through."),
    Tool("draw", "Draw", "draw", "D", "CrossCursor", "Draw freehand (saved as an Ink annotation)."),
    Tool("shapes", "Shapes", "shapes", "R", "CrossCursor",
         "Drag to draw the shape chosen in the Properties panel."),
    Tool("comment", "Comment", "comment", "C", "CrossCursor", "Click to place a sticky note."),
    Tool("erase", "Erase / Remove Object", "erase", "X", "PointingHandCursor",
         "Click an added object, an existing image or an existing text line to remove it."),
)
TOOL_BY_ID = {t.id: t for t in TOOLS}
MARKUP_TOOLS = ("highlight", "underline", "strike")
SHAPE_KINDS = ("rect", "ellipse", "line", "arrow")
