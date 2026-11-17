import io
import shutil
import threading
from dataclasses import replace
import pytest
import pikepdf
import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from n1cepdf.core.ocr import Tesseract, OcrError, OcrCancelled, recognize_pdf, parse_lines
from n1cepdf.core.document import PdfDocument
from n1cepdf.core.file_manager import save_atomic
from n1cepdf.edit.session import EditSession
from n1cepdf.edit.objects import TextBox
from n1cepdf.edit.geometry import PageTransform
from n1cepdf.ui.ocr_dialog import parse_pages

TSV = ('level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
       '5\t1\t1\t1\t1\t1\t100\t100\t200\t60\t95\tHello\n')

class FakeEngine:
    def languages(self, cancel): return {'eng', 'rus', 'ron'}
    def recognize(self, image, language, cancel): return TSV


def scan_pdf(mixed=False):
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=(600, 800))
    image = Image.new('RGB', (1200, 1600), 'white')
    from tests.conftest import DEJAVU
    font = ImageFont.truetype(str(DEJAVU), 55) if DEJAVU else ImageFont.load_default(size=55)
    ImageDraw.Draw(image).text((100, 100), 'Hello scanned world', font=font, fill='black')
    c.drawImage(ImageReader(image), 0, 0, 600, 800)
    if mixed:
        c.setFont('Helvetica', 20)
        c.drawString(50, 500, 'Native text')
    c.showPage()
    c.drawString(50, 700, 'Native second page')
    c.showPage()
    c.save()
    return out.getvalue()


def test_coordinates_and_native_filter():
    tr = PageTransform((10, 20, 610, 820), 0, 1200, 1600)
    line, = parse_lines(TSV, 0, tr)
    assert line.rect == (60, 740, 160, 770)
    assert not parse_lines(TSV, 0, tr, [line.rect])


def test_multipage_automatic_and_mixed():
    progress = []
    data = scan_pdf(True)
    cancel = threading.Event()
    assert not recognize_pdf(data, [0, 1], 'eng', cancel, automatic=True, engine=FakeEngine())
    lines = recognize_pdf(data, [0, 1], 'eng', cancel, lambda a,b: progress.append((a,b)), engine=FakeEngine())
    assert {l.page for l in lines} == {0, 1}
    assert progress == [(1, 2), (2, 2)]
    assert not recognize_pdf(data, [0], 'eng', cancel, engine=FakeEngine(), excluded={0: [(0,0,600,800)]})
    assert len(recognize_pdf(scan_pdf(), [0,1], 'eng', cancel, automatic=True, engine=FakeEngine())) == 1


def test_missing_language_and_cancel():
    event = threading.Event()
    with pytest.raises(OcrError, match='Missing'):
        recognize_pdf(scan_pdf(), [0], 'zzz', event, engine=FakeEngine())
    event.set()
    with pytest.raises(OcrCancelled):
        recognize_pdf(scan_pdf(), [0], 'eng', event, engine=FakeEngine())
    event.clear()
    with pytest.raises(OcrCancelled):
        recognize_pdf(scan_pdf(), [0,1], 'eng', event, lambda *_: event.set(), engine=FakeEngine())


def test_missing_executable():
    with pytest.raises(OcrError, match='Cannot start'):
        Tesseract('/nonexistent/tesseract').languages(threading.Event())


def text_of(path):
    with pdfium.PdfDocument(str(path)) as doc:
        page = doc[0]
        try:
            text = page.get_textpage()
            try: return text.get_text_range()
            finally: text.close()
        finally: page.close()


def test_edit_save_undo_no_duplicates(tmp_path):
    source = tmp_path/'scan.pdf'
    source.write_bytes(scan_pdf())
    doc = PdfDocument.open(source)
    session = EditSession(doc)
    rect = (50, 720, 320, 760)
    box = TextBox(doc.page_key(0), *rect, text='Original OCR', size=20,
                  ocr_source=rect, ocr_original='Original OCR', ocr_initial_rect=rect)
    session.add_object(box)
    dest = tmp_path/'out.pdf'
    save_atomic(doc, dest, session=session)
    assert text_of(dest).count('Original OCR') == 1
    session.update_object(replace(box, text='Replacement'))
    save_atomic(doc, dest, session=session)
    assert 'Replacement' not in text_of(dest) and 'Original OCR' not in text_of(dest)
    # Edited OCR is now raster content, not a visible PDF text object.
    with pdfium.PdfDocument(str(dest)) as pdf:
        page = pdf[0]
        bitmap = page.render(scale=1)
        assert bitmap.to_pil().convert('RGB').getpixel((55,60)) == (255,255,255)
        bitmap.close()
        page.close()
    session.undo()
    assert session.layer.get(box.id) == box
    session.redo()
    session.remove_object(box.id)
    save_atomic(doc, dest, session=session)
    assert 'Replacement' not in text_of(dest)
    doc.close()


@pytest.mark.skipif(not shutil.which('tesseract'), reason='Tesseract not installed')
def test_real_tesseract_scan():
    engine = Tesseract()
    if 'eng' not in engine.languages(threading.Event()):
        pytest.skip('English traineddata unavailable')
    lines = recognize_pdf(scan_pdf(), [0,1], 'eng', threading.Event(), automatic=True)
    assert 'Hello scanned world' in ' '.join(l.text for l in lines)
    assert all(l.page == 0 for l in lines)


def test_ranges():
    assert parse_pages('1,3-5,3', 5) == [0,2,3,4]
    for bad in ('', '0', '6', '4-2', '1-2-3', 'abc'):
        with pytest.raises(ValueError): parse_pages(bad, 5)


def test_saved_ocr_reopens_as_editable_objects(tmp_path):
    source = tmp_path/'source.pdf'
    source.write_bytes(scan_pdf())
    doc = PdfDocument.open(source)
    session = EditSession(doc)
    rect = (50, 720, 320, 760)
    box = TextBox(doc.page_key(0), *rect, text='Saved OCR', size=20,
                  ocr_source=rect, ocr_original='Saved OCR', ocr_initial_rect=rect)
    session.add_object(box)
    output = tmp_path/'out.pdf'
    save_atomic(doc, output, session=session)
    doc.close()
    reopened = PdfDocument.open(output)
    edits = EditSession(reopened)
    restored, = edits.layer.all()
    assert restored.text == 'Saved OCR'
    edits.update_object(replace(restored, text='Edited after reopen'))
    second = tmp_path/'second.pdf'
    save_atomic(reopened, second, session=edits)
    assert 'Edited after reopen' not in text_of(second)
    with pikepdf.open(second) as saved:
        assert '/N1ceOCREditState' not in saved.pages[0].obj
    assert 'Saved OCR' not in text_of(second)
    reopened.close()


def test_text_inside_form_is_not_ocr_duplicated():
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=(600,800))
    c.beginForm('native')
    c.drawString(20, 600, 'Form text')
    c.endForm()
    c.doForm('native')
    c.showPage()
    c.save()
    assert not recognize_pdf(out.getvalue(), [0], 'eng', threading.Event(), automatic=True, engine=FakeEngine())


@pytest.mark.parametrize('rotation', [0,90,180,270])
def test_crop_and_rotation_mapping(rotation):
    import pikepdf
    with pikepdf.open(io.BytesIO(scan_pdf())) as pdf:
        pdf.pages[0].obj.CropBox = [10,20,590,780]
        pdf.pages[0].obj.Rotate = rotation
        out = io.BytesIO()
        pdf.save(out)
    line, = recognize_pdf(out.getvalue(), [0], 'eng', threading.Event(), engine=FakeEngine())
    assert line.rect == pytest.approx((34,741.6,82,756), abs=0.1)


def test_cancel_running_process(tmp_path):
    import sys
    script = tmp_path/'slow.py'
    script.write_text('import time; time.sleep(30)')
    event = threading.Event()
    timer = threading.Timer(0.15, event.set)
    timer.start()
    try:
        with pytest.raises(OcrCancelled):
            Tesseract(sys.executable)._run([str(script)], event)
    finally:
        timer.cancel()


def test_timeout_and_process_error(tmp_path):
    import sys
    script = tmp_path/'slow.py'
    script.write_text('import time; time.sleep(30)')
    with pytest.raises(OcrError, match='timed out'):
        Tesseract(sys.executable)._run([str(script)], threading.Event(), timeout=0.05)
    with pytest.raises(OcrError, match='failed'):
        Tesseract(sys.executable)._run(['-c', 'raise SystemExit(2)'], threading.Event())


@pytest.mark.skipif(not shutil.which('tesseract'), reason='Tesseract not installed')
@pytest.mark.parametrize('language,text', [('eng', 'Hello scanned world'),
                                         ('rus', 'Привет мир'), ('ron', 'Salut România')])
def test_real_multilingual_image(language, text):
    from tests.conftest import DEJAVU
    if not DEJAVU:
        pytest.skip('Unicode test font unavailable')
    engine = Tesseract()
    cancel = threading.Event()
    if language not in engine.languages(cancel):
        pytest.skip(f'{language} traineddata unavailable')
    image = Image.new('RGB', (1400, 300), 'white')
    ImageDraw.Draw(image).text((70, 90), text, font=ImageFont.truetype(str(DEJAVU), 60), fill='black')
    tsv = engine.recognize(image, language, cancel)
    lines = parse_lines(tsv, 0, PageTransform((0,0,1400,300),0,1400,300))
    assert text in ' '.join(line.text for line in lines)


def test_mixed_native_overlap_filtered():
    class NativeEngine(FakeEngine):
        def recognize(self, image, language, cancel):
            # Native text at x=50,y=500 (unrotated 600x800 pt page).
            return TSV.replace('100\t100\t200\t60', '208\t1190\t420\t70')
    assert not recognize_pdf(scan_pdf(True), [0], 'eng', threading.Event(), engine=NativeEngine())


def test_absent_tesseract(monkeypatch):
    monkeypatch.delenv('N1CEPDF_TESSERACT', raising=False)
    monkeypatch.setattr(shutil, 'which', lambda _: None)
    with pytest.raises(OcrError, match='not found'):
        Tesseract()


def test_unchanged_ocr_is_visually_identical(tmp_path):
    source = tmp_path/'source.pdf'
    source.write_bytes(scan_pdf())
    doc = PdfDocument.open(source)
    edits = EditSession(doc)
    rect = (50, 720, 320, 760)
    edits.add_object(TextBox(doc.page_key(0), *rect, text='Hello scanned world', size=20,
                            ocr_source=rect, ocr_original='Hello scanned world', ocr_initial_rect=rect))
    output = tmp_path/'out.pdf'
    save_atomic(doc, output, session=edits)
    def pixels(path):
        with pdfium.PdfDocument(str(path)) as pdf:
            page = pdf[0]
            bitmap = page.render(scale=1)
            try: return bitmap.to_pil().tobytes()
            finally:
                bitmap.close()
                page.close()
    assert pixels(source) == pixels(output)
    with pikepdf.open(source) as original, pikepdf.open(output) as saved:
        original_images = [obj.read_bytes() for obj in original.objects
                           if isinstance(obj, pikepdf.Stream) and obj.get('/Subtype') == '/Image']
        saved_images = [obj.read_bytes() for obj in saved.objects
                        if isinstance(obj, pikepdf.Stream) and obj.get('/Subtype') == '/Image']
        assert original_images == saved_images
    doc.close()


def test_other_objects_survive_ocr_state_roundtrip(tmp_path):
    from n1cepdf.edit.objects import ImageBox, Shape, Markup, Comment, Ink
    from tests.conftest import png_bytes
    source = tmp_path/'source.pdf'
    source.write_bytes(scan_pdf())
    doc = PdfDocument.open(source)
    session = EditSession(doc)
    key = doc.page_key(0)
    rect = (50,720,320,760)
    objects = [TextBox(key, *rect, text='OCR', ocr_source=rect, ocr_original='OCR', ocr_initial_rect=rect),
               TextBox(key, 20,20,200,60, text='Ordinary text'),
               ImageBox(key, 300,300,330,330, data=png_bytes()),
               Shape(key, 'rect', 100,100,120,120),
               Markup(key, 'highlight', ((10,10,30,30),)),
               Comment(key, 50,50, text='note'),
               Ink(key, (((10,10),(30,30)),))]
    for obj in objects: session.add_object(obj)
    output = tmp_path/'out.pdf'
    save_atomic(doc, output, session=session)
    doc.close()
    reopened = PdfDocument.open(output)
    restored = EditSession(reopened)
    assert [replace(obj, page_key=reopened.page_key(0)) for obj in objects] == restored.layer.all()
    second = tmp_path/'second.pdf'
    save_atomic(reopened, second, session=restored)
    assert text_of(second).count('Ordinary text') == 1
    with pikepdf.open(second) as pdf:
        # highlight, comment and ink must not accumulate duplicates on reopen/save.
        assert len(pdf.pages[0].Annots) == 3
    reopened.close()
