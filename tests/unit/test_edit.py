"""Phase 2 core tests (Qt-free): geometry, undo, page-op undo, real content edits, writer, save/reopen."""
import io

import pikepdf
import pypdfium2 as pdfium
import pytest

from n1cepdf.core import page_manager as pm
from n1cepdf.core.document import PdfDocument
from n1cepdf.core.file_manager import save_atomic
from n1cepdf.core.renderer import PageRenderer
from n1cepdf.edit import pdfium_edit as pe
from n1cepdf.edit.fonts import default_catalog
from n1cepdf.edit.geometry import PageTransform
from n1cepdf.edit.layer import EditLayer
from n1cepdf.edit.objects import Comment, ImageBox, Ink, Markup, Shape, TextBox, bbox, moved
from n1cepdf.edit.preview import render_preview
from n1cepdf.edit.session import EditRefused, EditSession, TextStyle, default_style
from n1cepdf.edit.undo import Command, MacroCommand, UndoStack
from n1cepdf.edit.writer import apply_layer
from tests.conftest import DEJAVU, make_pdf, png_bytes, sha


def text_of(doc_or_bytes, page=0):
    data = doc_or_bytes if isinstance(doc_or_bytes, bytes) else doc_or_bytes.snapshot_bytes()
    d = pdfium.PdfDocument(data)
    try:
        return d[page].get_textpage().get_text_range()
    finally:
        d.close()


def saved_text(doc, session, tmp_path, name="out.pdf", page=0):
    out = save_atomic(doc, tmp_path / name, session=session)
    d = pdfium.PdfDocument(out.read_bytes())
    try:
        return out, d[page].get_textpage().get_text_range()
    finally:
        d.close()


# ------------------------------------------------------------------ geometry
def test_transform_matches_pdfium_for_all_rotations(sample3):
    for rot in (0, 90, 180, 270):
        d = PdfDocument.open(sample3)
        d.pdf.pages[0].obj.Rotate = rot
        r = PageRenderer(d.snapshot_bytes())
        w, h = r.page_size(0)
        s = 1.5
        t = PageTransform(d.page_crop_box(0), d.page_rotation(0), w * s, h * s)
        for (x, y) in ((72, 700), (300, 100), (10, 10), (500, 800)):
            px = r.rects_px(0, w * s, h * s, [(x, y, x, y)])[0]
            ours = t.to_px(x, y)
            assert ours[0] == pytest.approx(px[0], abs=0.2) and ours[1] == pytest.approx(px[1], abs=0.2), rot
            back = t.to_pdf(*ours)
            assert back[0] == pytest.approx(x, abs=1e-6) and back[1] == pytest.approx(y, abs=1e-6)
        r.close()


def test_transform_with_cropbox_offset():
    t = PageTransform((50, 60, 250, 360), 0, 400, 600)
    assert t.to_px(50, 360) == (0.0, 0.0) and t.to_px(250, 60) == (400.0, 600.0)
    assert t.rect_to_pdf(*t.rect_to_px((60, 70, 100, 120))) == pytest.approx((60, 70, 100, 120))


# ------------------------------------------------------------------ undo stack
class _Inc(Command):
    def __init__(self, box, n=1, merge=None):
        self.box, self.n, self._m, self.text = box, n, merge, f"inc{n}"

    def do(self): self.box[0] += self.n
    def undo(self): self.box[0] -= self.n
    def merge_key(self): return self._m
    def merge_from(self, newer): self.n += newer.n


def test_undo_redo_and_clean_state():
    box = [0]
    st = UndoStack()
    assert st.is_clean() and not st.can_undo
    st.push(_Inc(box)); st.push(_Inc(box, 2))
    assert box[0] == 3 and not st.is_clean()
    st.set_clean()
    assert st.is_clean()
    st.undo()
    assert box[0] == 1 and not st.is_clean()
    st.redo()
    assert box[0] == 3 and st.is_clean()
    st.undo(); st.undo(); st.push(_Inc(box, 5))   # new branch discards redo tail (which held the clean state)
    assert box[0] == 5 and not st.can_redo and not st.is_clean()


def test_merge_and_macro_rollback():
    box = [0]
    st = UndoStack()
    for _ in range(3):
        st.push(_Inc(box, 1, merge="drag"))
    assert box[0] == 3 and st.index == 1           # merged into one entry
    st.undo()
    assert box[0] == 0 and not st.can_undo

    class Boom(Command):
        def do(self): raise RuntimeError("x")
        def undo(self): pass
    with pytest.raises(RuntimeError):
        st.push(MacroCommand("m", [_Inc(box, 7), Boom()]))
    assert box[0] == 0 and st.index == 0           # half-applied macro rolled back, nothing recorded


# ------------------------------------------------------------------ layer + objects
def test_layer_add_remove_replace_and_bounds():
    key = (1, 0)
    lay = EditLayer()
    t = TextBox(key, 10, 20, 110, 40, "x")
    lay.add(t)
    assert lay.on_page(key) == [t] and lay.on_page((9, 0)) == []
    t2 = moved(t, 5, 5)
    assert bbox(t2) == (15, 25, 115, 45)
    lay.replace(t2)
    assert lay.get(t.id) == t2
    obj, idx = lay.remove(t.id)
    assert idx == 0 and len(lay) == 0
    assert bbox(Shape(key, "line", 0, 0, 10, 10, width=2)) == (-1, -1, 11, 11)


# ------------------------------------------------------------------ page operations undo
def test_page_ops_undo_redo_keep_layer_attached(sample3):
    d = PdfDocument.open(sample3)
    s = EditSession(d)
    keys = d.page_keys()
    s.add_object(TextBox(keys[2], 72, 500, 200, 520, "on page three"))
    s.insert_blank_after(0)
    assert d.page_count == 4 and d.index_of_key(keys[2]) == 3
    s.rotate_page(1, 90)
    assert d.page_rotation(1) == 90
    s.delete_page(0)
    assert d.page_count == 3
    assert [i for i in s.layer_by_page_index()] == [2]         # the object follows its page
    s.undo(); s.undo(); s.undo()                                # delete, rotate, insert
    assert d.page_count == 3 and d.page_keys() == keys
    s.redo(); s.redo(); s.redo()
    assert d.page_count == 3 and d.page_rotation(0) == 90 and list(s.layer_by_page_index()) == [2]


def test_reorder_undo_and_key_stability(sample3):
    d = PdfDocument.open(sample3)
    s = EditSession(d)
    keys = d.page_keys()
    s.reorder_pages([2, 0, 1])
    assert d.page_keys() == [keys[2], keys[0], keys[1]]
    s.undo()
    assert d.page_keys() == keys
    r = PageRenderer(d.snapshot_bytes())
    assert "alpha" in r.page_text(0) and "gamma" in r.page_text(2)


def test_modified_flag_follows_undo_and_revision_bumps(sample3):
    d = PdfDocument.open(sample3)
    s = EditSession(d)
    rev0 = d.revision
    s.rotate_page(0)
    assert d.modified and d.revision > rev0
    r1 = d.revision
    s.undo()
    assert not d.modified and d.revision > r1          # back to the clean state; renderers/search must refresh
    s.redo()
    s.mark_saved()
    assert not d.modified
    s.undo()
    assert d.modified                                    # undoing past the saved state is dirty again


def test_failed_command_is_not_recorded(sample3):
    d = PdfDocument.open(sample3)
    s = EditSession(d)
    s.delete_page(0); s.delete_page(0)
    with pytest.raises(ValueError):
        s.delete_page(0)                                  # would remove the last page
    assert d.page_count == 1 and s.stack.index == 2


# ------------------------------------------------------------------ existing text: real edits
def _run(session, i, text_start):
    content = session.inspect_page(i)
    return next(r for r in content.runs if r.text.startswith(text_start))


def test_native_text_edit_john_to_michael(rich, tmp_path):
    before = sha(rich)
    d = PdfDocument.open(rich)
    s = EditSession(d)
    key = d.page_key(0)
    run = _run(s, 0, "Hello")
    assert run.native_ok and default_style(run).size == 18.0
    plan = s.plan_text_edit(key, run, "Hello, my name is Michael.")
    assert plan.mode == "native" and any("In-place" in n for n in plan.notes)
    s.commit(plan)
    t = text_of(d)
    assert "Michael" in t and "John" not in t and "Second line stays." in t   # neighbours preserved
    assert len(s.layer) == 0                                                 # real content, not an overlay
    out, saved = saved_text(d, s, tmp_path)
    assert "Michael" in saved and sha(rich) == before                        # original file untouched
    assert b"John" not in out.read_bytes()
    s.undo()
    assert "John" in text_of(d) and not d.modified
    s.redo()
    assert "Michael" in text_of(d)


def test_replace_workflow_unicode_removes_old_text(rich, tmp_path):
    if DEJAVU is None:
        pytest.skip("no Unicode font")
    d = PdfDocument.open(rich)
    s = EditSession(d)
    key = d.page_key(0)
    run = _run(s, 0, "Hello")
    plan = s.plan_text_edit(key, run, "Привет, меня зовут Ион. Țară ăâîșț")
    assert plan.mode == "replace"
    assert any("not reused" in w for w in plan.warnings)                     # honest preview warnings
    s.commit(plan)
    assert len(s.layer) == 1 and s.layer.all()[0].origin == "replacement"
    assert "John" not in text_of(d)                                          # old content really removed
    out, saved = saved_text(d, s, tmp_path)
    assert "Привет" in saved and "Țară ăâîșț" in saved and "John" not in saved and "Second line stays." in saved
    assert b"John" not in out.read_bytes()
    s.undo()                                                                  # macro: restores text AND drops the box
    assert len(s.layer) == 0 and "John" in text_of(d)


def test_style_change_forces_replace_and_delete_text(rich, tmp_path):
    d = PdfDocument.open(rich)
    s = EditSession(d)
    key = d.page_key(0)
    run = _run(s, 0, "Hello")
    st = TextStyle("DejaVu Sans", 20.0, True, False, (1.0, 0.0, 0.0)) if DEJAVU else default_style(run)
    plan = s.plan_text_edit(key, run, "Bigger red text")
    assert plan.mode in ("native", "replace")
    if DEJAVU:
        plan = s.plan_text_edit(key, run, "Bigger red text", st)
        assert plan.mode == "replace"
    plan = s.plan_text_edit(key, _run(s, 0, "Second"), "")
    assert plan.mode == "delete-text"
    s.commit(plan)
    out, saved = saved_text(d, s, tmp_path)
    assert "Second line" not in saved and "Hello" in saved and b"Second line" not in out.read_bytes()


def test_overlap_and_form_text_are_reported_not_silently_removed(rich, tmp_path):
    d = PdfDocument.open(rich)
    s = EditSession(d)
    content = s.inspect_page(0)
    assert any(o.kind == "form" for o in content.objects)
    assert not any("inside form" in r.text for r in content.runs)            # nested form text is not editable
    synthetic = [pe.PageObjectInfo(0, "text", (0, 0, 100, 20)), pe.PageObjectInfo(1, "form", (50, 5, 150, 30)),
                 pe.PageObjectInfo(2, "path", (10, 10, 20, 12)), pe.PageObjectInfo(3, "image", (500, 500, 600, 600))]
    msgs = pe.describe_overlaps(pe.overlaps_for(pe.PageContent(tuple(synthetic), ()), 0))
    assert any("form XObject" in m for m in msgs) and any("vector" in m for m in msgs)
    assert not any("image" in m for m in msgs) and all("NOT be changed" in m for m in msgs)
    # a real page: text drawn over a filled rect -> warning appears in the plan, rect survives the edit
    from reportlab.pdfgen import canvas
    p = tmp_path / "over.pdf"
    c = canvas.Canvas(str(p))
    c.setFillColorRGB(1, 1, 0); c.rect(60, 690, 300, 30, stroke=0, fill=1)
    c.setFillColorRGB(0, 0, 0); c.setFont("Helvetica", 18); c.drawString(72, 700, "over the box")
    c.showPage(); c.save()
    d2 = PdfDocument.open(p)
    s2 = EditSession(d2)
    run = s2.inspect_page(0).runs[0]
    plan = s2.plan_text_edit(d2.page_key(0), run, "over the BOX")
    assert any("vector shape" in w for w in plan.warnings)
    s2.commit(plan)
    assert any(o.kind == "path" for o in s2.inspect_page(0).objects)         # the rect was not removed


def test_stale_plan_and_stale_selection_are_refused(rich):
    d = PdfDocument.open(rich)
    s = EditSession(d)
    key = d.page_key(0)
    run = _run(s, 0, "Hello")
    plan = s.plan_text_edit(key, run, "Hello, my name is Ann.")
    s.rotate_page(1)                                         # document changes while the plan exists
    with pytest.raises(EditRefused):
        s.commit(plan)
    changed = type(run)(**{**run.__dict__, "text": "Different text"})
    with pytest.raises(EditRefused):
        s.plan_text_edit(key, changed, "x")


def test_rotated_text_replacement_is_refused_clearly(tmp_path):
    from reportlab.pdfgen import canvas
    p = tmp_path / "rot.pdf"
    c = canvas.Canvas(str(p))
    c.translate(200, 200); c.rotate(30)
    c.setFont("Helvetica", 14); c.drawString(0, 0, "tilted words")
    c.showPage(); c.save()
    d = PdfDocument.open(p)
    s = EditSession(d)
    run = s.inspect_page(0).runs[0]
    assert abs(run.rotation - 30) < 1
    plan = s.plan_text_edit(d.page_key(0), run, "tilted text")   # native path still allowed (same style)
    assert plan.mode == "native"
    with pytest.raises(EditRefused):
        s.plan_text_edit(d.page_key(0), run, "Привет")           # needs replacement: refused, not faked


# ------------------------------------------------------------------ existing images
def test_existing_image_move_delete_replace(rich, tmp_path):
    d = PdfDocument.open(rich)
    s = EditSession(d)
    key = d.page_key(0)
    content = s.inspect_page(0)
    img = next(o for o in content.objects if o.kind == "image")
    s.commit(s.plan_image_edit(key, 0, img, (100, 300, 160, 345)))
    moved_img = next(o for o in s.inspect_page(0).objects if o.kind == "image")
    assert [round(v) for v in moved_img.bbox] == [100, 300, 160, 345]
    s.undo()
    assert [round(v) for v in next(o for o in s.inspect_page(0).objects if o.kind == "image").bbox] == [72, 400, 192, 490]
    s.commit(s.plan_image_delete(key, 0, img))
    assert not any(o.kind == "image" for o in s.inspect_page(0).objects)
    assert "Hello" in text_of(d)                                              # nothing else was touched
    s.undo()
    plan = s.plan_image_replace(key, 0, img, png_bytes((0, 255, 0, 255)))
    s.commit(plan)
    assert not any(o.kind == "image" for o in s.inspect_page(0).objects) and len(s.layer) == 1
    out = save_atomic(d, tmp_path / "img.pdf", session=s)
    page = pdfium.PdfDocument(out.read_bytes())[0].render(scale=1).to_pil().convert("RGB")
    assert page.getpixel((100, page.height - 445))[1] > 200                  # green replacement at old position


# ------------------------------------------------------------------ writer: new objects, annotations, round trips
def test_new_text_image_shapes_round_trip(sample3, tmp_path):
    d = PdfDocument.open(sample3)
    s = EditSession(d)
    key = d.page_key(0)
    s.add_object(TextBox(key, 72, 500, 300, 540, "Added text\nsecond line, Țară", "DejaVu Sans" if DEJAVU else "Liberation Sans", 14,
                         color=(1, 0, 0)))
    s.add_object(ImageBox(key, 72, 300, 172, 400, png_bytes()))
    s.add_object(Shape(key, "rect", 200, 300, 300, 400, fill=(0, 1, 0)))
    s.add_object(Shape(key, "arrow", 100, 250, 300, 200))
    before = sha(sample3)
    out, saved = saved_text(d, s, tmp_path)
    assert "Added text" in saved and "alpha page" in saved                    # new + existing text both extractable
    assert "Țară" in saved
    reopened = PdfDocument.open(out)
    assert reopened.page_count == 3
    pix = pdfium.PdfDocument(out.read_bytes())[0].render(scale=1).to_pil().convert("RGB")
    H = pix.height
    assert pix.getpixel((250, H - 350)) == (0, 255, 0)
    assert pix.getpixel((120, H - 350))[0] > 100 and pix.getpixel((120, H - 350))[2] == 255   # PNG alpha blended
    assert sha(sample3) == before


def test_no_page_rasterization(sample3, tmp_path):
    d = PdfDocument.open(sample3)
    s = EditSession(d)
    s.add_object(TextBox(d.page_key(0), 72, 500, 300, 540, "vector text"))
    s.add_object(Shape(d.page_key(0), "ellipse", 100, 100, 200, 200, fill=(1, 0, 0)))
    out, _ = saved_text(d, s, tmp_path)
    with pikepdf.open(out) as pdf:
        for page in pdf.pages:
            for xo in (page.get("/Resources", {}).get("/XObject", {}) or {}).values():
                assert str(xo.get("/Subtype")) != "/Image"                    # no page-sized (or any) bitmap added
    assert text_of(out.read_bytes()).count("alpha") == 1


def test_annotations_persist_and_stay_annotations(sample3, tmp_path):
    d = PdfDocument.open(sample3)
    s = EditSession(d)
    key = d.page_key(0)
    r = PageRenderer(d.snapshot_bytes())
    hit = r.search("alpha")[0].rects[0]
    s.add_object(Markup(key, "highlight", (hit,), color=(1, 1, 0)))
    s.add_object(Markup(key, "underline", (hit,), color=(0, 0, 1), opacity=1.0))
    s.add_object(Markup(key, "strike", (hit,), color=(1, 0, 0), opacity=1.0))
    s.add_object(Comment(key, 400, 700, "Примечание"))
    s.add_object(Ink(key, (((100, 100), (150, 140), (200, 100)),)))
    out, saved = saved_text(d, s, tmp_path)
    assert "alpha page" in saved                                              # text untouched by annotations
    with pikepdf.open(out) as pdf:
        subs = [str(a.Subtype) for a in pdf.pages[0].Annots]
        assert subs == ["/Highlight", "/Underline", "/StrikeOut", "/Text", "/Ink"]
        assert str(pdf.pages[0].Annots[3].Contents) == "Примечание"
        assert all("/AP" in a for a in pdf.pages[0].Annots)
    again = PdfDocument.open(out)                                             # reopen -> save again keeps them
    out2 = save_atomic(again, tmp_path / "again.pdf")
    with pikepdf.open(out2) as pdf:
        assert len(pdf.pages[0].Annots) == 5
    pix = pdfium.PdfDocument(out.read_bytes())[0].render(scale=2).to_pil().convert("RGB")
    assert any(p[2] < 200 for p in [pix.getpixel((x, pix.height - int(705 * 2))) for x in range(150, 400, 6)])  # highlight tints the text line


def test_save_preserves_metadata_bookmarks_and_page_rotation(tmp_path):
    p = make_pdf(tmp_path / "o.pdf", ["a", "b"], outline=True)
    d = PdfDocument.open(p)
    with d.pdf.open_metadata() as m:
        m["dc:title"] = "My Title"
    d.pdf.docinfo["/Title"] = "My Title"
    s = EditSession(d)
    s.rotate_page(1, 90)
    s.add_object(TextBox(d.page_key(0), 72, 500, 200, 520, "x"))
    out, _ = saved_text(d, s, tmp_path)
    r = PageRenderer(out.read_bytes())
    assert [e.title for e in r.outline()] == ["Chapter 1", "Chapter 2"]
    reopened = PdfDocument.open(out)
    assert reopened.metadata().get("/Title") == "My Title" and reopened.page_rotation(1) == 90


def test_encrypted_document_edit_and_authorised_save(encrypted, tmp_path):
    d = PdfDocument.open(encrypted, "secret")
    s = EditSession(d)
    s.add_object(TextBox(d.page_key(0), 72, 500, 200, 520, "added"))
    from n1cepdf.core.errors import EncryptedSaveRefused
    with pytest.raises(EncryptedSaveRefused):
        save_atomic(d, tmp_path / "x.pdf", session=s)
    out = save_atomic(d, tmp_path / "x.pdf", allow_decrypt=True, session=s)
    assert "added" in text_of(out.read_bytes())


def test_save_failure_is_reported_and_layer_survives(sample3, tmp_path, monkeypatch):
    from n1cepdf.core.errors import SaveValidationError
    d = PdfDocument.open(sample3)
    s = EditSession(d)
    s.add_object(TextBox(d.page_key(0), 72, 500, 200, 520, "keep me"))
    import n1cepdf.edit.writer as w
    monkeypatch.setattr(w, "apply_layer", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(SaveValidationError):
        save_atomic(d, tmp_path / "bad.pdf", session=s)
    assert not (tmp_path / "bad.pdf").exists() and not list(tmp_path.glob(".n1cepdf-*")) and len(s.layer) == 1


def test_image_decoding_errors_are_clean(sample3, tmp_path):
    from n1cepdf.edit.writer import WriteError
    d = PdfDocument.open(sample3)
    s = EditSession(d)
    s.add_object(ImageBox(d.page_key(0), 0, 0, 50, 50, b"not an image"))
    with pytest.raises(Exception):
        save_atomic(d, tmp_path / "x.pdf", session=s)
    assert not (tmp_path / "x.pdf").exists()


def test_webp_and_jpeg_images(sample3, tmp_path):
    from PIL import Image
    d = PdfDocument.open(sample3)
    s = EditSession(d)
    key = d.page_key(0)
    wb, jb = io.BytesIO(), io.BytesIO()
    try:
        Image.new("RGB", (20, 20), (255, 0, 255)).save(wb, "WEBP")
        s.add_object(ImageBox(key, 10, 10, 60, 60, wb.getvalue()))
    except (OSError, KeyError):
        pass  # Pillow built without WebP: skipped, reported in docs
    Image.new("RGB", (20, 20), (0, 255, 255)).save(jb, "JPEG")
    s.add_object(ImageBox(key, 100, 10, 150, 60, jb.getvalue()))
    out = save_atomic(d, tmp_path / "wj.pdf", session=s)
    with pikepdf.open(out) as pdf:   # the overlay is a form XObject: look at every image object in the file
        filters = [str(o.get("/Filter")) for o in pdf.objects
                   if isinstance(o, pikepdf.Stream) and o.get("/Subtype") == "/Image"]
    assert any("DCTDecode" in f for f in filters)                             # JPEG kept as DCT (no recompression)


def test_preview_renders_object_with_alpha_and_rotation(sample3):
    key = (1, 0)
    t = TextBox(key, 10, 10, 200, 40, "Preview", "DejaVu Sans" if DEJAVU else "Liberation Sans", 20, color=(1, 0, 0))
    w, h, rgba, warns = render_preview(t, 0, 2.0)
    assert w > 300 and h > 40 and len(rgba) == w * h * 4
    assert any(rgba[i + 3] == 0 for i in range(0, len(rgba), 4 * 50))        # transparent background
    assert any(rgba[i] > 200 and rgba[i + 3] > 200 for i in range(0, len(rgba), 4))
    w2, h2, _b, _w = render_preview(t, 90, 2.0)
    assert (w2, h2) == pytest.approx((h, w), abs=6)                           # page rotation swaps axes


def test_font_resolution_supports_cyrillic_and_romanian():
    cat = default_catalog()
    face, missing, note = cat.resolve("Arial", False, False, "Привет Țară ăâîșț")
    assert face is not None and missing == []
    assert face.family  # a covering font was chosen (note says if substituted)


# ------------------------------------------------------------------ helpers added for the editor UI
def test_renderer_selection_rects_pdf_and_page_content(rich):
    d = PdfDocument.open(rich)
    r = PageRenderer(d.snapshot_bytes())
    rects = r.selection_rects_pdf(0, 0, 4)                         # "Hello"
    assert len(rects) == 1 and rects[0][0] == pytest.approx(73, abs=2) and rects[0][2] < 140
    content = r.page_content(0)
    assert [x.text for x in content.runs][:2] == ["Hello, my name is John.", "Second line stays."]
    assert {o.kind for o in content.objects} >= {"text", "image", "path", "form"}


def test_with_rect_keeps_line_direction_and_fit_text_box():
    from n1cepdf.edit.objects import with_rect
    from n1cepdf.edit.writer import fit_text_box
    key = (1, 0)
    ln = Shape(key, "arrow", 10, 100, 110, 20)                      # points down-right
    out = with_rect(ln, (0, 0, 50, 50))
    assert (out.x0, out.y0, out.x1, out.y1) == pytest.approx((0, 50, 50, 0))
    box = TextBox(key, 72, 500, 150, 512, "one two three four five six seven eight nine ten", "Liberation Sans", 12)
    grown = fit_text_box(box)
    assert grown.y0 < box.y0 and grown.y1 == box.y1 and fit_text_box(grown) == grown   # grows once, then stable


def test_global_pdfium_lock_is_shared():
    from n1cepdf.core.pdfium_lock import PDFIUM_LOCK
    r = PageRenderer(make_pdf_bytes())
    assert r._lock is PDFIUM_LOCK


def make_pdf_bytes():
    from reportlab.pdfgen import canvas
    b = io.BytesIO()
    c = canvas.Canvas(b); c.drawString(72, 700, "x"); c.showPage(); c.save()
    return b.getvalue()


def test_undo_clean_state_for_never_saved_document(tmp_path):
    d = PdfDocument.new_blank()
    s = EditSession(d)
    s.add_object(TextBox(d.page_key(0), 72, 500, 200, 520, "t"))
    s.undo()
    assert d.modified                                               # never saved: stays modified even at index 0
    out = save_atomic(d, tmp_path / "n.pdf", session=s)
    s.mark_saved(); d.mark_saved(out)
    assert not d.modified
    s.redo()
    assert d.modified
