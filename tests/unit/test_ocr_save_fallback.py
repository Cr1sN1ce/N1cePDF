"""Regression: page fallback permission must not require consent on every OCR object."""
import hashlib
import io
import shutil
import threading
from dataclasses import replace
import pikepdf
import pypdfium2 as pdfium
import pypdfium2.raw as raw
import pytest
from PIL import Image,ImageDraw,ImageFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from n1cepdf.core.document import PdfDocument
from n1cepdf.core.file_manager import save_atomic
from n1cepdf.core.errors import SaveValidationError
from n1cepdf.core.ocr import recognize_pdf
from n1cepdf.edit.fonts import default_catalog
from n1cepdf.edit.objects import TextBox
from n1cepdf.edit.session import EditSession


def make_41_page_form_scan(path):
    face=default_catalog().find('DejaVu Sans',False,False) or default_catalog().faces[0]
    image=Image.new('RGB',(1600,600),'white')
    draw=ImageDraw.Draw(image)
    font=ImageFont.truetype(str(face.path),48)
    draw.text((80,100),'Original label',font=font,fill='black')
    draw.text((1200,100),'4/4 · 100%',font=font,fill='black')
    c=canvas.Canvas(str(path),pagesize=(800,300))
    c.beginForm('scan');c.drawImage(ImageReader(image),0,0,800,300);c.endForm()
    c.doForm('scan')
    c.drawString(50,40,'Native first-page vector')
    c.showPage()
    for page in range(2,42):
        c.drawString(50,200,f'Untouched page {page}')
        c.showPage()
    c.save()
    return face


def make_objects(doc,face):
    left_rect=(40,210,220,255)
    right_rect=(600,210,780,255)
    def box(rect,text,align):
        return TextBox(doc.page_key(0),*rect,text=text,family=face.family,size=24,align=align,
                       ocr_source=(rect[0]-.5,215,rect[2]+.5,250),
                       ocr_original=text,ocr_initial_rect=rect)
    return box(left_rect,'Original label','left'),box(right_rect,'4/4 · 100%','right')


def assert_only_first_page_rasterized(source,output):
    with pikepdf.open(source) as before,pikepdf.open(output) as after:
        assert len(after.pages)==41
        images=[obj for obj in after.pages[0].Resources.XObject.values() if obj.get('/Subtype')=='/Image']
        assert len(images)==1
        image=images[0]
        assert image.Subtype=='/Image'
        for i in range(1,41):
            assert before.pages[i].Contents.read_bytes()==after.pages[i].Contents.read_bytes()
            assert list(before.pages[i].MediaBox)==list(after.pages[i].MediaBox)
            assert str(before.pages[i].Resources.Font.F1)==str(after.pages[i].Resources.Font.F1)
    with pdfium.PdfDocument(str(output)) as pdf:
        page=pdf[0];tp=page.get_textpage()
        objects=list(page.get_objects(max_depth=32,textpage=tp))
        assert not any(obj.type==raw.FPDF_PAGEOBJ_TEXT and raw.FPDFTextObj_GetTextRenderMode(obj.raw)!=3 for obj in objects)
        for obj in objects:obj.close()
        tp.close();page.close()
        last=pdf[40];text=last.get_textpage()
        assert 'Untouched page 41' in text.get_text_range()
        text.close();last.close()


def test_one_explicit_page_permission_allows_multiple_changed_blocks(tmp_path):
    source=tmp_path/'source.pdf';face=make_41_page_form_scan(source)
    original=source.read_bytes()
    doc=PdfDocument.open(source);session=EditSession(doc)
    left,right=make_objects(doc,face)
    from n1cepdf.edit import commands
    session.push(commands.AddObject(session,replace(left,text='Updated label',ocr_original=None,ocr_allow_page_raster=True)))
    session.push(commands.AddObject(session,replace(right,text='3/3 · 90%',ocr_original=None)))
    output=tmp_path/'saved.pdf'
    save_atomic(doc,output,session=session)
    assert source.read_bytes()==original
    assert_only_first_page_rasterized(source,output)
    doc.close()


def test_page_permission_is_configuration_and_undo_redo_revokes_it(tmp_path):
    from n1cepdf.edit.scan_rebuild import changed
    source=tmp_path/'source.pdf';face=make_41_page_form_scan(source)
    doc=PdfDocument.open(source);session=EditSession(doc)
    left,right=make_objects(doc,face)
    session.add_object(left);session.add_object(right)
    session.update_object(replace(left,text='Updated label'))
    session.set_page_raster_permission(doc.page_key(0),True)
    assert all(obj.ocr_allow_page_raster for obj in session.layer.all())
    assert not changed(session.layer.get(right.id))
    assert session.layer.get(right.id).ocr_original==right.text
    output=tmp_path/'output.pdf';save_atomic(doc,output,session=session)
    successful=output.read_bytes()
    session.undo()
    assert not session.page_raster_allowed(doc.page_key(0))
    with pytest.raises(SaveValidationError,match='Page 1:.*Enable affected-page'):
        save_atomic(doc,output,session=session)
    assert output.read_bytes()==successful
    session.redo()
    assert session.page_raster_allowed(doc.page_key(0))
    save_atomic(doc,output,session=session)
    session.set_page_raster_permission(doc.page_key(0),False)
    assert not any(obj.ocr_allow_page_raster for obj in session.layer.all())
    assert not changed(session.layer.get(right.id))
    with pytest.raises(SaveValidationError,match='Page 1:'):
        save_atomic(doc,output,session=session)
    assert not list(tmp_path.glob('.n1cepdf-*.tmp'))
    doc.close()


def test_permission_on_unchanged_block_is_passed_to_writer(tmp_path):
    from n1cepdf.edit import commands
    source=tmp_path/'source.pdf';face=make_41_page_form_scan(source)
    doc=PdfDocument.open(source);session=EditSession(doc)
    left,right=make_objects(doc,face)
    # Simulate legacy mixed consent: the changed block has no flag, the untouched one does.
    session.push(commands.AddObject(session,replace(left,text='Updated label',ocr_original=None)))
    session.push(commands.AddObject(session,replace(right,ocr_allow_page_raster=True)))
    output=tmp_path/'output.pdf';save_atomic(doc,output,session=session)
    assert_only_first_page_rasterized(source,output)
    restored=PdfDocument.open(output);edits=EditSession(restored)
    assert [obj.text for obj in edits.layer.all()]==[right.text]
    assert edits.page_raster_allowed(restored.page_key(0))
    restored.close();doc.close()


def test_other_page_permission_never_authorizes_unapproved_page(tmp_path):
    source=tmp_path/'source.pdf';face=make_41_page_form_scan(source)
    original=source.read_bytes()
    doc=PdfDocument.open(source);session=EditSession(doc)
    left,_=make_objects(doc,face)
    session.add_object(replace(left,text='Updated label',ocr_original=None,ocr_allow_page_raster=True))
    session.add_object(TextBox(doc.page_key(1),50,180,300,220,text='Changed second page',
                               ocr_source=(50,180,300,220),ocr_original=None))
    output=tmp_path/'already-saved.pdf';output.write_bytes(original)
    with pytest.raises(SaveValidationError,match='Page 2:.*Enable affected-page'):
        save_atomic(doc,output,session=session)
    assert output.read_bytes()==original and source.read_bytes()==original
    assert not list(tmp_path.glob('.n1cepdf-*.tmp'))
    doc.close()


def test_real_fallback_failure_reports_technical_cause_and_keeps_original(tmp_path):
    source=tmp_path/'source.pdf';face=make_41_page_form_scan(source)
    original=source.read_bytes()
    doc=PdfDocument.open(source);session=EditSession(doc)
    left,_=make_objects(doc,face)
    session.add_object(replace(left,text='Updated label',ocr_original=None,ocr_allow_page_raster=True))
    doc.pdf.pages[0].obj.MediaBox=[0,0,10000,10000]
    with pytest.raises(SaveValidationError,match='Page 1:.*100 megapixel'):
        save_atomic(doc,source,session=session)
    assert source.read_bytes()==original
    assert not list(tmp_path.glob('.n1cepdf-*.tmp'))
    doc.close()


def test_fallback_restores_crop_rotation_if_snapshot_fails(tmp_path,monkeypatch):
    from n1cepdf.edit.scan_rebuild import rebuild_page,ReconstructionError
    source=tmp_path/'source.pdf';face=make_41_page_form_scan(source)
    doc=PdfDocument.open(source);session=EditSession(doc)
    left,_=make_objects(doc,face)
    doc.pdf.pages[0].obj.CropBox=[10,20,790,280]
    doc.pdf.pages[0].obj.Rotate=90
    original_save=pikepdf.Pdf.save
    def fail(pdf,*args,**kwargs):
        if pdf is doc.pdf: raise RuntimeError('injected snapshot failure')
        return original_save(pdf,*args,**kwargs)
    monkeypatch.setattr(pikepdf.Pdf,'save',fail)
    with pytest.raises(ReconstructionError,match='Page 1:.*injected snapshot failure'):
        rebuild_page(doc.pdf,0,[replace(left,text='New',ocr_original=None)],session.catalog,allow_page_raster=True)
    assert list(doc.pdf.pages[0].CropBox)==[10,20,790,280]
    assert doc.pdf.pages[0].Rotate==90
    doc.close()


def test_undecodable_local_image_uses_explicit_fallback(tmp_path,monkeypatch):
    from tests.unit.test_scan_quality import edit_scan
    doc,session,box,_=edit_scan(tmp_path/'direct.pdf')
    session.set_page_raster_permission(doc.page_key(0),True)
    def unsupported(*args,**kwargs):
        raise NotImplementedError('injected image extraction limitation')
    monkeypatch.setattr(pikepdf.PdfImage,'as_pil_image',unsupported)
    output=tmp_path/'fallback.pdf';notes=[]
    save_atomic(doc,output,session=session,warnings=notes)
    assert output.exists() and any('rasterized' in note for note in notes)
    doc.close()


@pytest.mark.skipif(not shutil.which('tesseract'),reason='Tesseract unavailable')
def test_real_ocr_41_page_fallback_reopens_with_new_pixels(tmp_path):
    from n1cepdf.core.ocr import Tesseract
    if 'eng' not in Tesseract().languages(threading.Event()):pytest.skip('English data unavailable')
    source=tmp_path/'source.pdf';face=make_41_page_form_scan(source)
    original=source.read_bytes()
    lines=recognize_pdf(original,[0],'eng',threading.Event())
    assert any('Original label' in line.text for line in lines)
    assert all(line.page==0 for line in lines)
    doc=PdfDocument.open(source);session=EditSession(doc)
    left,right=make_objects(doc,face)
    session.add_object(left);session.add_object(right)
    session.update_object(replace(left,text='Updated label'))
    session.set_page_raster_permission(doc.page_key(0),True)
    output=tmp_path/'saved.pdf';save_atomic(doc,output,session=session)
    assert_only_first_page_rasterized(source,output)
    reopened=PdfDocument.open(output);restored=EditSession(reopened)
    # The untouched counter persists as an independent object over the committed image.
    assert [obj.text for obj in restored.layer.all()]==[right.text]
    recognized=recognize_pdf(reopened.snapshot_bytes(),[0],'eng',threading.Event())
    text=' '.join(line.text for line in recognized)
    assert 'Updated label' in text and 'Original label' not in text
    assert source.read_bytes()==original
    restored_output=tmp_path/'resaved.pdf';save_atomic(reopened,restored_output,session=restored)
    assert_only_first_page_rasterized(source,restored_output)
    reopened.close();doc.close()
