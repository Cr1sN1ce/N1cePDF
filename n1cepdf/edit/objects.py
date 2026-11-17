"""Edit objects. Immutable, stored in PDF user space (points), never in screen pixels."""
from __future__ import annotations
import uuid
from dataclasses import dataclass, field, replace
from typing import Union

Color = tuple[float, float, float]  # 0..1 RGB
PageKey = tuple[int, int]           # (object number, generation) of the page: stable across reorder/insert/delete


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass(frozen=True)
class TextBox:
    page_key: PageKey
    x0: float
    y0: float
    x1: float
    y1: float                     # rect in PDF space: (x0, y0) bottom-left, (x1, y1) top-right
    text: str = ""
    family: str = "DejaVu Sans"
    size: float = 12.0
    bold: bool = False
    italic: bool = False
    color: Color = (0.0, 0.0, 0.0)
    align: str = "left"           # left | center | right
    line_spacing: float = 1.2
    opacity: float = 1.0
    rotation: float = 0.0         # degrees counter-clockwise about the box centre
    origin: str = "new"           # "new" | "replacement" (replaces removed original text)
    id: str = field(default_factory=new_id)
    kind: str = "text"
    ocr_source: tuple[float, float, float, float] | None = None
    ocr_original: str | None = None
    ocr_initial_rect: tuple[float, float, float, float] | None = None
    char_spacing: float = 0.0
    horizontal_scale: float = 100.0
    ocr_background: str = "auto"  # auto (solid only) | inpaint (explicit consent)
    ocr_allow_page_raster: bool = False
    ocr_words: tuple[tuple[str, tuple[float,float,float,float]], ...] = ()
    ocr_regions: tuple[tuple[float,float,float,float], ...] = ()


@dataclass(frozen=True)
class ImageBox:
    page_key: PageKey
    x0: float
    y0: float
    x1: float
    y1: float
    data: bytes = b""             # original file bytes (PNG / JPEG / WebP)
    rotation: float = 0.0
    lock_aspect: bool = True
    opacity: float = 1.0
    id: str = field(default_factory=new_id)
    kind: str = "image"


@dataclass(frozen=True)
class Shape:
    page_key: PageKey
    shape: str                    # rect | ellipse | line | arrow
    x0: float
    y0: float
    x1: float
    y1: float                     # rect/ellipse: bounding rect; line/arrow: (x0,y0) -> (x1,y1)
    stroke: Color = (0.0, 0.0, 0.0)
    fill: Color | None = None
    width: float = 1.5
    opacity: float = 1.0
    id: str = field(default_factory=new_id)
    kind: str = "shape"


@dataclass(frozen=True)
class Markup:
    page_key: PageKey
    markup: str                   # highlight | underline | strike
    rects: tuple[tuple[float, float, float, float], ...] = ()  # (l, b, r, t) per text line
    color: Color = (1.0, 0.9, 0.0)
    opacity: float = 0.4
    id: str = field(default_factory=new_id)
    kind: str = "markup"


@dataclass(frozen=True)
class Comment:
    page_key: PageKey
    x: float
    y: float                      # top-left anchor of the note icon
    text: str = ""
    author: str = "N1cePDF"
    color: Color = (1.0, 0.85, 0.0)
    id: str = field(default_factory=new_id)
    kind: str = "comment"


@dataclass(frozen=True)
class Ink:
    page_key: PageKey
    strokes: tuple[tuple[tuple[float, float], ...], ...] = ()
    color: Color = (0.9, 0.1, 0.1)
    width: float = 2.0
    opacity: float = 1.0
    id: str = field(default_factory=new_id)
    kind: str = "ink"


EditObject = Union[TextBox, ImageBox, Shape, Markup, Comment, Ink]


def bbox(o: EditObject) -> tuple[float, float, float, float]:
    """Axis-aligned bounds (l, b, r, t) in PDF space (rotation ignored for boxes)."""
    if isinstance(o, (TextBox, ImageBox, Shape)):
        pad = o.width / 2 if isinstance(o, Shape) else 0.0
        return (min(o.x0, o.x1) - pad, min(o.y0, o.y1) - pad, max(o.x0, o.x1) + pad, max(o.y0, o.y1) + pad)
    if isinstance(o, Markup):
        if not o.rects:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(r[0] for r in o.rects), min(r[1] for r in o.rects),
                max(r[2] for r in o.rects), max(r[3] for r in o.rects))
    if isinstance(o, Comment):
        return (o.x, o.y - 20.0, o.x + 20.0, o.y)
    pts = [p for s in o.strokes for p in s]
    if not pts:
        return (0.0, 0.0, 0.0, 0.0)
    pad = o.width / 2
    return (min(p[0] for p in pts) - pad, min(p[1] for p in pts) - pad,
            max(p[0] for p in pts) + pad, max(p[1] for p in pts) + pad)


def moved(o: EditObject, dx: float, dy: float) -> EditObject:
    if isinstance(o, (TextBox, ImageBox, Shape)):
        return replace(o, x0=o.x0 + dx, y0=o.y0 + dy, x1=o.x1 + dx, y1=o.y1 + dy)
    if isinstance(o, Markup):
        return replace(o, rects=tuple((r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy) for r in o.rects))
    if isinstance(o, Comment):
        return replace(o, x=o.x + dx, y=o.y + dy)
    return replace(o, strokes=tuple(tuple((p[0] + dx, p[1] + dy) for p in s) for s in o.strokes))


def with_rect(o: EditObject, rect: tuple[float, float, float, float]) -> EditObject:
    """Set the rectangle of a box-like object (text, image, shape)."""
    if not isinstance(o, (TextBox, ImageBox, Shape)):
        raise TypeError("object has no rectangle")
    if isinstance(o, Shape) and o.shape in ("line", "arrow"):
        # keep the line's direction: map both endpoints proportionally into the new rectangle
        l, b, r, t = min(o.x0, o.x1), min(o.y0, o.y1), max(o.x0, o.x1), max(o.y0, o.y1)
        w, h = (r - l) or 1e-9, (t - b) or 1e-9

        def mx(x): return rect[0] + (x - l) / w * (rect[2] - rect[0])
        def my(y): return rect[1] + (y - b) / h * (rect[3] - rect[1])
        return replace(o, x0=mx(o.x0), y0=my(o.y0), x1=mx(o.x1), y1=my(o.y1))
    return replace(o, x0=rect[0], y0=rect[1], x1=rect[2], y1=rect[3])
