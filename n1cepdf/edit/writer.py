"""Write edit objects into a pikepdf document as real PDF content.

* ordinary text / image / shape -> PDF content merged through ReportLab overlays;
* changed OCR -> source image pixels reconstructed by scan_rebuild before other overlays;
* unchanged OCR -> invisible searchable text;
* markup / ink / comment -> standard PDF annotations with appearance streams (kept as annotations).
The same drawing code renders previews, so what the user sees is what gets saved.
"""
from __future__ import annotations
import io
import math
from PIL import Image, ImageOps
import pikepdf
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas
from .fonts import FontCatalog, default_catalog, metrics
from .geometry import Rect
from .objects import Comment, EditObject, ImageBox, Ink, Markup, Shape, TextBox

MAX_IMAGE_PIXELS = 100_000_000


class WriteError(Exception):
    pass


# ---------------------------------------------------------------- text layout
def wrap_text(text: str, font: str, size: float, width: float,
              char_spacing: float = 0, horizontal_scale: float = 100) -> list[str]:
    def measure(value):
        return (stringWidth(value,font,size)+max(0,len(value)-1)*char_spacing)*horizontal_scale/100
    lines: list[str] = []
    for para in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if width <= 0 or measure(para) <= width:
            lines.append(para)
            continue
        cur = ""
        for word in para.split(" "):
            cand = word if not cur else cur + " " + word
            if measure(cand) <= width:
                cur = cand
                continue
            if cur:
                lines.append(cur)
                cur = ""
            while measure(word) > width and len(word) > 1:   # break overlong words
                k = len(word)
                while k > 1 and measure(word[:k]) > width:
                    k -= 1
                lines.append(word[:k])
                word = word[k:]
            cur = word
        lines.append(cur)
    return lines


def text_size_needed(o: TextBox, catalog: FontCatalog | None = None) -> tuple[float, float]:
    """(width, height) in points that the text needs without wrapping; used to auto-size boxes."""
    cat = catalog or default_catalog()
    face, _m, _n = cat.resolve(o.family, o.bold, o.italic, o.text)
    if face is None:
        return o.x1 - o.x0, o.y1 - o.y0
    lines = o.text.replace("\r\n", "\n").split("\n")
    w = max(((stringWidth(ln, face.rl_name, o.size)+max(0,len(ln)-1)*o.char_spacing)*o.horizontal_scale/100 for ln in lines), default=0.0)
    return w, len(lines) * o.size * o.line_spacing


# ---------------------------------------------------------------- drawing
def _alpha(c: canvas.Canvas, a: float) -> None:
    if a < 0.999:
        c.setFillAlpha(max(0.0, min(1.0, a)))
        c.setStrokeAlpha(max(0.0, min(1.0, a)))


def _draw_text(c: canvas.Canvas, o: TextBox, cat: FontCatalog, warnings: list[str]) -> None:
    unchanged_ocr = (o.ocr_source is not None and o.text == o.ocr_original
                     and (o.x0, o.y0, o.x1, o.y1) == o.ocr_initial_rect)
    face, missing, note = cat.resolve(o.family, o.bold, o.italic, o.text)
    if face is None:
        raise WriteError("No usable font is installed to render this text.")
    if note:
        warnings.append(note)
    if missing:
        warnings.append("Missing glyphs for: " + " ".join(missing[:20]))
    if (o.bold and not face.bold) or (o.italic and not face.italic):
        warnings.append(f"'{face.family}' has no {'bold ' if o.bold else ''}{'italic ' if o.italic else ''}face; "
                        "regular weight is used.")
    asc, _desc = metrics(face)
    width = o.x1 - o.x0
    lines = [o.text] if unchanged_ocr else wrap_text(o.text, face.rl_name, o.size, width, o.char_spacing, o.horizontal_scale)
    c.saveState()
    _alpha(c, o.opacity)
    if o.rotation:
        cx, cy = (o.x0 + o.x1) / 2, (o.y0 + o.y1) / 2
        c.translate(cx, cy)
        c.rotate(o.rotation)
        c.translate(-cx, -cy)
    c.setFillColorRGB(*o.color)
    c.setFont(face.rl_name, o.size)
    y = o.y1 - asc * o.size
    for ln in lines:
        w = (stringWidth(ln, face.rl_name, o.size)+max(0,len(ln)-1)*o.char_spacing)*o.horizontal_scale/100
        x = o.x0 if o.align == "left" else (o.x0 + (width - w) / 2 if o.align == "center" else o.x1 - w)
        if unchanged_ocr:
            text = c.beginText(x, y)
            text.setTextRenderMode(3)
            text.setCharSpace(o.char_spacing)
            if w > 0:
                text.setHorizScale(max(1, o.horizontal_scale * width / w))
            text.textOut(ln)
            c.drawText(text)
        else:
            text = c.beginText(x,y)
            text.setCharSpace(o.char_spacing)
            text.setHorizScale(o.horizontal_scale)
            text.textOut(ln)
            c.drawText(text)
        y -= o.size * o.line_spacing
    c.restoreState()


def _image_reader(data: bytes) -> ImageReader:
    try:
        im = Image.open(io.BytesIO(data))
        if im.width * im.height > MAX_IMAGE_PIXELS:
            raise WriteError("Image is too large.")
        fmt = (im.format or "").upper()
        if fmt not in ("PNG", "JPEG", "WEBP"):
            raise WriteError(f"Unsupported image format: {fmt or 'unknown'}.")
        orientation = (im.getexif() or {}).get(274, 1) if fmt == "JPEG" else 1
        if fmt == "JPEG" and orientation in (1, None):
            return ImageReader(io.BytesIO(data))            # embedded as-is, no recompression
        if fmt == "JPEG":
            buf = io.BytesIO()
            ImageOps.exif_transpose(im).convert("RGB").save(buf, "JPEG", quality=95)
            return ImageReader(io.BytesIO(buf.getvalue()))
        buf = io.BytesIO()
        (im if im.mode in ("RGB", "RGBA", "L", "LA") else im.convert("RGBA")).save(buf, "PNG")
        return ImageReader(io.BytesIO(buf.getvalue()))
    except WriteError:
        raise
    except Exception as e:  # noqa: BLE001 - untrusted file: report, never crash
        raise WriteError("The image file could not be decoded.") from e


def _draw_image(c: canvas.Canvas, o: ImageBox) -> None:
    reader = _image_reader(o.data)
    c.saveState()
    _alpha(c, o.opacity)
    if o.rotation:
        cx, cy = (o.x0 + o.x1) / 2, (o.y0 + o.y1) / 2
        c.translate(cx, cy)
        c.rotate(o.rotation)
        c.translate(-cx, -cy)
    c.drawImage(reader, min(o.x0, o.x1), min(o.y0, o.y1), abs(o.x1 - o.x0), abs(o.y1 - o.y0),
                mask="auto", preserveAspectRatio=False)
    c.restoreState()


def _draw_shape(c: canvas.Canvas, o: Shape) -> None:
    c.saveState()
    _alpha(c, o.opacity)
    c.setStrokeColorRGB(*o.stroke)
    c.setLineWidth(o.width)
    if o.fill is not None:
        c.setFillColorRGB(*o.fill)
    fill = 1 if o.fill is not None else 0
    x0, y0, x1, y1 = min(o.x0, o.x1), min(o.y0, o.y1), max(o.x0, o.x1), max(o.y0, o.y1)
    if o.shape == "rect":
        c.rect(x0, y0, x1 - x0, y1 - y0, stroke=1, fill=fill)
    elif o.shape == "ellipse":
        c.ellipse(x0, y0, x1, y1, stroke=1, fill=fill)
    elif o.shape in ("line", "arrow"):
        c.setLineCap(1)
        c.line(o.x0, o.y0, o.x1, o.y1)
        if o.shape == "arrow":
            ang = math.atan2(o.y1 - o.y0, o.x1 - o.x0)
            head = max(8.0, o.width * 4.5)
            p = c.beginPath()
            p.moveTo(o.x1, o.y1)
            for da in (math.radians(155), -math.radians(155)):
                p.lineTo(o.x1 + head * math.cos(ang + da), o.y1 + head * math.sin(ang + da))
            p.close()
            c.setFillColorRGB(*o.stroke)
            c.drawPath(p, stroke=0, fill=1)
    else:
        raise WriteError(f"Unknown shape '{o.shape}'.")
    c.restoreState()


def draw_content_objects(c: canvas.Canvas, objects: list[EditObject], cat: FontCatalog) -> list[str]:
    warnings: list[str] = []
    for o in objects:
        if isinstance(o, TextBox):
            if o.text.strip():
                _draw_text(c, o, cat, warnings)
        elif isinstance(o, ImageBox):
            _draw_image(c, o)
        elif isinstance(o, Shape):
            _draw_shape(c, o)
    return list(dict.fromkeys(warnings))


def build_overlay_pdf(box: Rect, objects: list[EditObject], cat: FontCatalog) -> tuple[bytes, list[str]]:
    """One-page PDF whose MediaBox equals `box` (PDF space) and whose content uses absolute PDF coordinates."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(max(box[2], 1.0), max(box[3], 1.0)), pageCompression=1)
    warnings = draw_content_objects(c, objects, cat)
    c.showPage()
    c.save()
    return buf.getvalue(), warnings


# ---------------------------------------------------------------- annotations
def _form(pdf: pikepdf.Pdf, bbox: Rect, content: str, blend_multiply: bool = False, alpha: float = 1.0):
    gs = pikepdf.Dictionary(Type=pikepdf.Name.ExtGState, ca=alpha, CA=alpha)
    if blend_multiply:
        gs.BM = pikepdf.Name.Multiply
    stream = pikepdf.Stream(pdf, content.encode("latin-1"))
    stream.Type = pikepdf.Name.XObject
    stream.Subtype = pikepdf.Name.Form
    stream.BBox = list(bbox)
    stream.Resources = pikepdf.Dictionary(ExtGState=pikepdf.Dictionary(GS=gs))
    return pdf.make_indirect(stream)


def _append_annot(pdf: pikepdf.Pdf, page: pikepdf.Page, annot: pikepdf.Dictionary) -> None:
    ref = pdf.make_indirect(annot)
    if "/Annots" not in page.obj:
        page.obj.Annots = pdf.make_indirect(pikepdf.Array())
    page.obj.Annots.append(ref)


def _f(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".") or "0"


def _write_markup(pdf: pikepdf.Pdf, page: pikepdf.Page, o: Markup) -> None:
    if not o.rects:
        return
    sub = {"highlight": "Highlight", "underline": "Underline", "strike": "StrikeOut"}[o.markup]
    rect = (min(r[0] for r in o.rects), min(r[1] for r in o.rects),
            max(r[2] for r in o.rects), max(r[3] for r in o.rects))
    quads: list[float] = []
    ops = [f"/GS gs {' '.join(_f(v) for v in o.color)} {'rg' if o.markup == 'highlight' else 'RG'}"]
    for l, b, r, t in o.rects:
        quads += [l, t, r, t, l, b, r, b]
        if o.markup == "highlight":
            ops.append(f"{_f(l)} {_f(b)} {_f(r - l)} {_f(t - b)} re f")
        else:
            y = b + 0.8 if o.markup == "underline" else (b + t) / 2
            ops.append(f"1 w {_f(l)} {_f(y)} m {_f(r)} {_f(y)} l S")
    ap = _form(pdf, rect, "\n".join(ops), blend_multiply=o.markup == "highlight",
               alpha=1.0 if o.markup != "highlight" else o.opacity)
    annot = pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name("/" + sub), Rect=list(rect),
                               QuadPoints=quads, C=list(o.color), CA=o.opacity, F=4,
                               NM=pikepdf.String(o.id), AP=pikepdf.Dictionary(N=ap))
    _append_annot(pdf, page, annot)


def _write_ink(pdf: pikepdf.Pdf, page: pikepdf.Page, o: Ink) -> None:
    strokes = [s for s in o.strokes if len(s) >= 2]
    if not strokes:
        return
    pts = [p for s in strokes for p in s]
    pad = o.width + 1
    rect = (min(p[0] for p in pts) - pad, min(p[1] for p in pts) - pad,
            max(p[0] for p in pts) + pad, max(p[1] for p in pts) + pad)
    ops = [f"/GS gs {' '.join(_f(v) for v in o.color)} RG {_f(o.width)} w 1 J 1 j"]
    for s in strokes:
        ops.append(f"{_f(s[0][0])} {_f(s[0][1])} m " + " ".join(f"{_f(x)} {_f(y)} l" for x, y in s[1:]) + " S")
    ap = _form(pdf, rect, "\n".join(ops), alpha=o.opacity)
    annot = pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Ink, Rect=list(rect),
                               InkList=[[v for p in s for v in p] for s in strokes], C=list(o.color),
                               CA=o.opacity, BS=pikepdf.Dictionary(W=o.width), F=4,
                               NM=pikepdf.String(o.id), AP=pikepdf.Dictionary(N=ap))
    _append_annot(pdf, page, annot)


def _write_comment(pdf: pikepdf.Pdf, page: pikepdf.Page, o: Comment) -> None:
    rect = (o.x, o.y - 20.0, o.x + 20.0, o.y)
    content = (f"{' '.join(_f(v) for v in o.color)} rg 0 0 20 20 re f 0 0 0 RG 1 w 0.5 0.5 19 19 re S "
               "4 14 m 16 14 l S 4 10 m 16 10 l S 4 6 m 12 6 l S")
    ap = _form(pdf, (0, 0, 20, 20), content)
    annot = pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Text, Rect=list(rect),
                               Contents=o.text, T=o.author, Name=pikepdf.Name.Comment, Open=False,
                               C=list(o.color), F=4, NM=pikepdf.String(o.id), AP=pikepdf.Dictionary(N=ap))
    _append_annot(pdf, page, annot)


# ---------------------------------------------------------------- entry point
def apply_layer(pdf: pikepdf.Pdf, by_page: dict[int, list[EditObject]],
                catalog: FontCatalog | None = None) -> list[str]:
    """Write all objects into `pdf` (mutates it). `by_page` maps page index -> objects in z-order."""
    cat = catalog or default_catalog()
    warnings: list[str] = []
    for index, objs in sorted(by_page.items()):
        if not (0 <= index < len(pdf.pages)):
            continue
        page = pdf.pages[index]
        from .scan_rebuild import changed, rebuild_page
        scan_edits = [o for o in objs if isinstance(o, TextBox) and changed(o)]
        if scan_edits:
            page_permission=any(isinstance(o,TextBox) and o.ocr_source is not None and o.ocr_allow_page_raster for o in objs)
            warnings += rebuild_page(pdf,index,scan_edits,cat,allow_page_raster=page_permission)
            objs = [o for o in objs if o not in scan_edits]
        # Persist only live overlays over the reconstructed raster. Never restore old scan pixels.
        if any(isinstance(o, TextBox) and o.ocr_source is not None for o in objs):
            from .ocr_state import store_page
            store_page(pdf, page, objs)
        content = [o for o in objs if isinstance(o, (TextBox, ImageBox, Shape))]
        if any(isinstance(o, TextBox) and (o.text.strip() or o.ocr_source is not None) for o in content) or any(
                not isinstance(o, TextBox) for o in content):
            mb = [float(v) for v in page.mediabox]
            box = (min(mb[0], mb[2]), min(mb[1], mb[3]), max(mb[0], mb[2]), max(mb[1], mb[3]))
            data, w = build_overlay_pdf(box, content, cat)
            warnings += w
            with pikepdf.open(io.BytesIO(data)) as ov:
                ov.pages[0].obj.MediaBox = list(box)
                page.add_overlay(ov.pages[0], pikepdf.Rectangle(*box))
        for o in objs:
            if isinstance(o, Markup):
                _write_markup(pdf, page, o)
            elif isinstance(o, Ink):
                _write_ink(pdf, page, o)
            elif isinstance(o, Comment):
                _write_comment(pdf, page, o)
    return list(dict.fromkeys(warnings))


def fit_text_box(o: TextBox, catalog: FontCatalog | None = None) -> TextBox:
    """Grow the box downward so all wrapped lines fit inside it (the preview and the saved page then agree)."""
    from dataclasses import replace
    cat = catalog or default_catalog()
    face, _m, _n = cat.resolve(o.family, o.bold, o.italic, o.text)
    if face is None:
        return o
    lines = wrap_text(o.text, face.rl_name, o.size, o.x1 - o.x0, o.char_spacing, o.horizontal_scale)
    need = max(1, len(lines)) * o.size * o.line_spacing + o.size * 0.25
    if (o.y1 - o.y0) >= need:
        return o
    return replace(o, y0=o.y1 - need)
