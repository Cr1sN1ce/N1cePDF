"""Exact previews: render one text/image object through the same writer + PDFium, as a transparent
bitmap cropped to the object's bounds. OCR uses the same source-image reconstruction as export.
Bitmaps are rendered at the current device-pixel scale; no size-fitting resampling is used."""
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
    if isinstance(o, TextBox) and o.ocr_source is not None:
        sl, sb, sr, st = o.ocr_source
        l, b, r, t = min(l, sl), min(b, sb), max(r, sr), max(t, st)
    return l - pad, b - pad, r + pad, t + pad


def render_preview(o: EditObject, page_rotation: int, scale: float,
                   catalog: FontCatalog | None = None, source_pdf: bytes | None = None,
                   page_index: int = 0, scan_objects=None, allow_page_raster=None) -> tuple[int, int, bytes, list[str]]:
    """Returns (width_px, height_px, RGBA bytes, warnings). The bitmap covers display_bounds(o)."""
    cat = catalog or default_catalog()
    l, b, r, t = display_bounds(o)
    from .scan_rebuild import changed, rebuild_page, ReconstructionError
    if isinstance(o, TextBox) and changed(o):
        if source_pdf is None:
            raise ReconstructionError("OCR reconstruction preview requires its source PDF.")
        with pikepdf.open(io.BytesIO(source_pdf)) as pdf:
            warnings = rebuild_page(pdf,page_index,scan_objects or [o],cat,allow_page_raster=allow_page_raster)
            pg = pdf.pages[page_index].obj
            pg.MediaBox = [l,b,r,t]
            pg.CropBox = [l,b,r,t]
            pg.Rotate = int(page_rotation)%360
            out = io.BytesIO()
            pdf.save(out)
        return _bitmap(out.getvalue(),page_index,scale,warnings)
    data, warnings = build_overlay_pdf((0.0, 0.0, max(r, 1.0), max(t, 1.0)), [o], cat)
    # OCR guides are only a preview aid and are never written into saved PDFs.
    if isinstance(o, TextBox) and o.ocr_original == o.text and o.ocr_initial_rect == (o.x0, o.y0, o.x1, o.y1):
        from .objects import Shape
        guide = Shape(o.page_key, "rect", o.x0, o.y0, o.x1, o.y1,
                      stroke=(0.2, 0.45, 0.9), width=0.5)
        data, warnings = build_overlay_pdf((0, 0, max(r, 1), max(t, 1)), [guide], cat)
    with pikepdf.open(io.BytesIO(data)) as pdf:
        pg = pdf.pages[0].obj
        pg.MediaBox = [l, b, r, t]
        pg.CropBox = [l, b, r, t]
        pg.Rotate = int(page_rotation) % 360
        out = io.BytesIO()
        pdf.save(out)
    return _bitmap(out.getvalue(),0,scale,warnings)


def _bitmap(data, index, scale, warnings):
    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(data)
        try:
            page = doc[index]
            try:
                bitmap = page.render(scale=scale, fill_color=(255, 255, 255, 0))
                try:
                    img = bitmap.to_pil().convert("RGBA").copy()
                finally:
                    bitmap.close()
            finally:
                page.close()
        finally:
            doc.close()
    return img.width, img.height, img.tobytes(), warnings
