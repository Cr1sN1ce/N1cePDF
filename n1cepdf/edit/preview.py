"""Exact previews: render one text/image object through the same writer + PDFium, as a transparent
bitmap cropped to the object's bounds. The viewer moves/scales this bitmap while dragging and
re-renders only on release, so mouse moves never trigger a page re-render."""
from __future__ import annotations
import io
import math
import pikepdf
import pypdfium2 as pdfium
from ..core.pdfium_lock import PDFIUM_LOCK
from .fonts import FontCatalog, default_catalog
from .objects import EditObject, ImageBox, TextBox, bbox
from .writer import build_overlay_pdf


def display_bounds(o: EditObject, pad: float = 1.0) -> tuple[float, float, float, float]:
    """PDF-space bounds of the drawn object including rotation and a small pad."""
    l, b, r, t = bbox(o)
    rot = getattr(o, "rotation", 0.0)
    if rot and isinstance(o, (TextBox, ImageBox)):
        cx, cy = (l + r) / 2, (b + t) / 2
        a = math.radians(rot)
        ca, sa = math.cos(a), math.sin(a)
        xs, ys = [], []
        for x, y in ((l, b), (r, b), (r, t), (l, t)):
            dx, dy = x - cx, y - cy
            xs.append(cx + dx * ca - dy * sa)
            ys.append(cy + dx * sa + dy * ca)
        l, r, b, t = min(xs), max(xs), min(ys), max(ys)
    return l - pad, b - pad, r + pad, t + pad


def render_preview(o: EditObject, page_rotation: int, scale: float,
                   catalog: FontCatalog | None = None) -> tuple[int, int, bytes, list[str]]:
    """Returns (width_px, height_px, RGBA bytes, warnings). The bitmap covers display_bounds(o)."""
    cat = catalog or default_catalog()
    l, b, r, t = display_bounds(o)
    data, warnings = build_overlay_pdf((0.0, 0.0, max(r, 1.0), max(t, 1.0)), [o], cat)
    with pikepdf.open(io.BytesIO(data)) as pdf:
        pg = pdf.pages[0].obj
        pg.MediaBox = [l, b, r, t]
        pg.CropBox = [l, b, r, t]
        pg.Rotate = int(page_rotation) % 360
        out = io.BytesIO()
        pdf.save(out)
    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(out.getvalue())
        try:
            page = doc[0]
            try:
                img = page.render(scale=scale, fill_color=(255, 255, 255, 0)).to_pil().convert("RGBA")
            finally:
                page.close()
        finally:
            doc.close()
    return img.width, img.height, img.tobytes(), warnings
