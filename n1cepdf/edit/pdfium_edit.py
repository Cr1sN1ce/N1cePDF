"""Real content editing through PDFium page objects, grafted back into the pikepdf document.

Pipeline for every content edit (never rasterizes, never paints over anything):
  1. open the current document state in PDFium, list top-level page objects (text / image / path / form);
  2. modify the targeted object(s): rewrite text in place, remove an object, or set an image matrix;
  3. PDFium regenerates that page's content stream; the result is verified by re-reading it;
  4. only that page's /Contents and /Resources are grafted into the pikepdf document (metadata,
     bookmarks, annotations and every other page are untouched). The old objects are kept for undo.

Limits (surfaced to the user, never hidden):
  * only top-level page objects are editable; text inside Form XObjects is reported as unsupported;
  * PDFium regenerates the whole page content stream, so marked-content/optional-content structure on
    an edited page may be flattened;
  * native in-place rewrite is only allowed for non-embedded standard-14 fonts and WinAnsi text.
"""
from __future__ import annotations
import contextlib
import ctypes
import io
import math
from dataclasses import dataclass, field, replace
import pikepdf
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from ..core.pdfium_lock import locked
from .geometry import Rect, rects_intersect

KIND = {pdfium_c.FPDF_PAGEOBJ_TEXT: "text", pdfium_c.FPDF_PAGEOBJ_PATH: "path",
        pdfium_c.FPDF_PAGEOBJ_IMAGE: "image", pdfium_c.FPDF_PAGEOBJ_SHADING: "shading",
        pdfium_c.FPDF_PAGEOBJ_FORM: "form"}
_STD14 = ("helvetica", "arial", "times", "courier")


class EditRefused(Exception):
    """The requested edit cannot be performed safely. The message is shown to the user."""


@dataclass(frozen=True)
class PageObjectInfo:
    index: int
    kind: str
    bbox: Rect


@dataclass(frozen=True)
class TextRun:
    page: int
    index: int                     # index among the page's top-level objects
    text: str
    bbox: Rect                     # (l, b, r, t) PDF space
    font_name: str
    font_size: float
    color: tuple[float, float, float]
    embedded: bool
    rotation: float                # degrees
    baseline: tuple[float, float]  # text origin in PDF space
    native_ok: bool                # standard-14 font, not embedded: eligible for in-place rewrite
    skewed: bool = False
    char_spacing: float = 0.0
    horizontal_scale: float = 100.0
    spacing_known: bool = False


@dataclass(frozen=True)
class PageContent:
    objects: tuple[PageObjectInfo, ...]
    runs: tuple[TextRun, ...]


@dataclass
class PageSwap:
    """What was replaced on a page; restore_page() puts it back exactly."""
    old_contents: object = None
    old_resources: object = None


# ---------------------------------------------------------------- inspection
def _fill_color(obj) -> tuple[float, float, float]:
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    if pdfium_c.FPDFPageObj_GetFillColor(obj.raw, r, g, b, a):
        return (r.value / 255.0, g.value / 255.0, b.value / 255.0)
    return (0.0, 0.0, 0.0)


@contextlib.contextmanager
def _open_page(doc: pdfium.PdfDocument, page_index: int):
    """(page, textpage, top-level objects); children are closed before the caller closes the document
    (pypdfium2 asserts if a child is finalized after its parent was closed)."""
    page = doc[page_index]
    tp = page.get_textpage()
    objs = list(page.get_objects(max_depth=1, textpage=tp))
    try:
        yield page, tp, objs
    finally:
        for o in objs:
            o.close()
        tp.close()
        page.close()


@locked
def inspect_page(doc: pdfium.PdfDocument, page_index: int) -> PageContent:
    """List top-level page objects and text runs. `doc` may be an already-open renderer document."""
    with _open_page(doc, page_index) as (_page, _tp, objs):
        content = _inspect(objs, page_index)
    # PDFium has no public character-spacing getter. Read PDF text-state operators and
    # use them only when their show-text sequence maps unambiguously to inspected runs.
    out = io.BytesIO()
    doc.save(out)
    states = []
    char_space,hscale = 0.0,100.0
    stack = []
    with pikepdf.open(io.BytesIO(out.getvalue())) as pdf:
        for operands,operator in pikepdf.parse_content_stream(pdf.pages[page_index]):
            op = str(operator)
            if op == "q": stack.append((char_space,hscale))
            elif op == "Q" and stack: char_space,hscale = stack.pop()
            elif op == "Tc": char_space = float(operands[0])
            elif op == "Tz": hscale = float(operands[0])
            elif op == '"':
                char_space = float(operands[1])
                states.append((char_space,hscale))
            elif op in ("Tj","'"):
                states.append((char_space,hscale))
            elif op == "TJ":
                states.extend((char_space,hscale) for part in operands[0] if isinstance(part,pikepdf.String))
    if len(states) == len(content.runs):
        content = replace(content,runs=tuple(replace(run,char_spacing=spacing,horizontal_scale=scale,spacing_known=True)
                           for run,(spacing,scale) in zip(content.runs,states)))
    return content


def _inspect(objs, page_index: int) -> PageContent:
    infos: list[PageObjectInfo] = []
    runs: list[TextRun] = []
    for i, o in enumerate(objs):
        bb = tuple(float(v) for v in o.get_bounds())
        infos.append(PageObjectInfo(i, KIND.get(o.type, "other"), bb))
        if o.type != pdfium_c.FPDF_PAGEOBJ_TEXT:
            continue
        try:
            text = o.extract()
            font = o.get_font()
            base = (font.get_base_name() or "")
            m = o.get_matrix()
            rot = math.degrees(math.atan2(m.b, m.a))
            skewed = abs(m.a * m.c + m.b * m.d) > 1e-3 * max(1.0, abs(m.a * m.d))
            low = base.split("+", 1)[-1].lower()
            embedded = bool(font.is_embedded)
            runs.append(TextRun(page_index, i, text, bb, base, float(o.get_font_size()), _fill_color(o),
                                embedded, rot, (float(m.e), float(m.f)),
                                (not embedded) and low.startswith(_STD14), skewed))
        except Exception:  # noqa: BLE001 - a single odd text object must not break the page
            continue
    return PageContent(tuple(infos), tuple(runs))


def overlaps_for(content: PageContent, index: int, pad: float = 0.5) -> list[PageObjectInfo]:
    """Other top-level objects whose bounds intersect object `index` (reported, never touched)."""
    target = next(o for o in content.objects if o.index == index)
    return [o for o in content.objects if o.index != index and rects_intersect(o.bbox, target.bbox, -pad)]


def describe_overlaps(others: list[PageObjectInfo]) -> list[str]:
    counts: dict[str, int] = {}
    for o in others:
        counts[o.kind] = counts.get(o.kind, 0) + 1
    names = {"text": "other text run(s)", "image": "image(s)", "path": "vector shape(s)",
             "form": "form XObject(s) (may contain text that cannot be edited)", "shading": "shading(s)",
             "other": "other object(s)"}
    return [f"Overlaps {n} {names[k]}; they will NOT be changed." for k, n in sorted(counts.items())]


# ---------------------------------------------------------------- modification
def _save_bytes(doc: pdfium.PdfDocument, page) -> bytes:
    page.gen_content()
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _wide(text: str):
    u = text.encode("utf-16-le") + b"\0\0"
    return (ctypes.c_ushort * (len(u) // 2)).from_buffer_copy(u)


def can_encode_winansi(text: str) -> bool:
    try:
        text.encode("cp1252")
        return True
    except UnicodeEncodeError:
        return False


def _norm(s: str) -> str:
    return s.replace("\r\n", "\n").replace("\r", "\n").strip()


def _target(objs, index: int, expected: str):
    if index < 0 or index >= len(objs) or objs[index].type != pdfium_c.FPDF_PAGEOBJ_TEXT:
        raise EditRefused("The page changed since the text was selected. Select it again.")
    if _norm(objs[index].extract()) != _norm(expected):
        raise EditRefused("The text on the page changed since it was selected. Select it again.")
    return objs[index]


@locked
def native_set_text(pdf_bytes: bytes, page_index: int, index: int, expected_old: str, new_text: str) -> bytes:
    """Rewrite one text object's string in place (same font, size, colour and position)."""
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        with _open_page(doc, page_index) as (page, _tp, objs):
            obj = _target(objs, index, expected_old)
            run = next(r for r in _inspect(objs, page_index).runs if r.index == index)
            if not run.native_ok:
                raise EditRefused("This text uses an embedded or non-standard font; in-place rewrite is unsafe.")
            if not can_encode_winansi(new_text):
                raise EditRefused("The new text has characters the original font cannot encode.")
            if not pdfium_c.FPDFText_SetText(obj.raw, _wide(new_text)):
                raise EditRefused("PDFium refused to set the text.")
            out = _save_bytes(doc, page)
    finally:
        doc.close()
    # verify by re-reading the generated result
    chk = pdfium.PdfDocument(out)
    try:
        run2 = [r for r in inspect_page(chk, page_index).runs if r.index == index]
        if not run2 or _norm(run2[0].text) != _norm(new_text):
            raise EditRefused("Verification failed: the rewritten text did not read back correctly.")
    finally:
        chk.close()
    return out


@locked
def remove_objects(pdf_bytes: bytes, page_index: int, targets: list[tuple[int, str, str]]) -> bytes:
    """Remove top-level objects. targets = (index, kind, expected_text_for_text_objects)."""
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        with _open_page(doc, page_index) as (page, _tp, objs):
            before = len(objs)
            picked = []
            for index, kind, expected in targets:
                if index < 0 or index >= before or KIND.get(objs[index].type, "other") != kind:
                    raise EditRefused("The page changed since the object was selected. Select it again.")
                if kind == "text":
                    _target(objs, index, expected)
                picked.append(objs[index])
            for o in picked:
                page.remove_obj(o)
            out = _save_bytes(doc, page)
    finally:
        doc.close()
    chk = pdfium.PdfDocument(out)
    try:
        with _open_page(chk, page_index) as (_p, _t, after):
            if len(after) != before - len(targets):
                raise EditRefused("Verification failed: object count after removal is unexpected.")
    finally:
        chk.close()
    return out


@locked
def set_image_rect(pdf_bytes: bytes, page_index: int, index: int, rect: Rect, rotation: float = 0.0) -> bytes:
    """Move/resize/rotate an existing image object so it fills `rect` (PDF space)."""
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        with _open_page(doc, page_index) as (page, _tp, objs):
            n_before = len(objs)
            if index >= len(objs) or objs[index].type != pdfium_c.FPDF_PAGEOBJ_IMAGE:
                raise EditRefused("The page changed since the image was selected. Select it again.")
            w, h = rect[2] - rect[0], rect[3] - rect[1]
            if w < 1 or h < 1:
                raise EditRefused("The image would become too small.")
            t = math.radians(rotation)
            c, s = math.cos(t), math.sin(t)
            cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
            objs[index].set_matrix(pdfium.PdfMatrix(w * c, w * s, -h * s, h * c,
                                                    cx - c * w / 2 + s * h / 2, cy - s * w / 2 - c * h / 2))
            out = _save_bytes(doc, page)
    finally:
        doc.close()
    chk = pdfium.PdfDocument(out)
    try:
        with _open_page(chk, page_index) as (_p, _t, after):
            if len(after) != n_before or after[index].type != pdfium_c.FPDF_PAGEOBJ_IMAGE:
                raise EditRefused("Verification failed after transforming the image.")
    finally:
        chk.close()
    return out


# ---------------------------------------------------------------- grafting
def _import(dst: pikepdf.Pdf, src: pikepdf.Pdf, obj):
    """copy_foreign needs an indirect object; direct dictionaries are made indirect in their own file first."""
    if not obj.is_indirect:
        obj = src.make_indirect(obj)
    return dst.copy_foreign(obj)


def graft_page(pdf: pikepdf.Pdf, page_index: int, new_pdf_bytes: bytes) -> PageSwap:
    """Replace only /Contents and /Resources of one page with PDFium's regenerated versions."""
    page = pdf.pages[page_index]
    swap = PageSwap(page.obj.get("/Contents"), page.obj.get("/Resources"))
    with pikepdf.open(io.BytesIO(new_pdf_bytes)) as new:
        np_ = new.pages[page_index]
        contents, res = np_.obj.get("/Contents"), np_.obj.get("/Resources")
        page.obj.Contents = _import(pdf, new, contents) if contents is not None else pdf.make_stream(b"")
        page.obj.Resources = _import(pdf, new, res) if res is not None else pikepdf.Dictionary()
    return swap


def restore_page(pdf: pikepdf.Pdf, page_index: int, swap: PageSwap) -> None:
    obj = pdf.pages[page_index].obj
    for key, old in (("/Contents", swap.old_contents), ("/Resources", swap.old_resources)):
        if old is None:
            if key in obj:
                del obj[key]
        else:
            obj[key] = old
