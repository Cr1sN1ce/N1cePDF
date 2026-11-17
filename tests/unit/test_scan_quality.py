"""Real pixel/structure checks for font matching, reconstruction and export previews."""
import io
import threading
from dataclasses import replace
import numpy as np
import pytest
import pikepdf
import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from n1cepdf.core.document import PdfDocument
from n1cepdf.core.file_manager import save_atomic
from n1cepdf.core.ocr import recognize_pdf, Tesseract
from n1cepdf.edit.fonts import default_catalog, FontCatalog, covers, embedding_allowed
from n1cepdf.edit.font_match import match_font
from n1cepdf.edit.session import EditSession
from n1cepdf.edit.objects import TextBox
from n1cepdf.edit.preview import render_preview, display_bounds
from n1cepdf.edit.scan_rebuild import reconstruct, ReconstructionError, rebuild_page


def make_scan(path, mode='RGB', background=(240,225,180), form=False):
    image = Image.new('RGB',(1200,600),background)
    face = default_catalog().find('DejaVu Sans',False,False) or default_catalog().faces[0]
    ImageDraw.Draw(image).text((100,90),'Original text',font=ImageFont.truetype(str(face.path),50),fill=(20,40,90))
    if mode != 'RGB': image = image.convert(mode)
    c = canvas.Canvas(str(path),pagesize=(600,300))
    if form: c.beginForm('scan')
    c.drawImage(ImageReader(image),0,0,600,300)
    if form:
        c.endForm()
        c.doForm('scan')
    c.drawString(400,40,'Vector stays')
    c.showPage()
    c.drawString(72,200,'Untouched page')
    c.showPage()
    c.save()
    return image,face


def edit_scan(path, mode='RGB', form=False):
    image,face = make_scan(path,mode,form=form)
    doc = PdfDocument.open(path)
    session = EditSession(doc)
    box = TextBox(doc.page_key(0),50,210,240,260,text='Changed text',family=face.family,size=25,
                  color=(0.08,0.16,0.35),ocr_source=(48,220,230,258),ocr_original=None,
                  ocr_initial_rect=(50,210,240,260))
    session.add_object(box)
    return doc,session,box,image


def render_crop(path, rect, scale=2,rotation=0):
    with pikepdf.open(path) as pdf:
        page = pdf.pages[0]
        page.obj.MediaBox=list(rect)
        page.obj.CropBox=list(rect)
        page.obj.Rotate=rotation
        out=io.BytesIO();pdf.save(out)
    with pdfium.PdfDocument(out.getvalue()) as pdf:
        page=pdf[0];bitmap=page.render(scale=scale,fill_color=(255,255,255,0))
        try: return np.array(bitmap.to_pil().convert('RGBA'))
        finally: bitmap.close();page.close()


@pytest.mark.parametrize('family',['DejaVu Sans','DejaVu Serif','Liberation Mono'])
@pytest.mark.parametrize('text',['Hello world','Привет мир','Salut România ăîșț'])
def test_font_matching_unicode_size_color(text,family):
    catalog=default_catalog()
    faces=[f for f in catalog.faces if f.family in ('DejaVu Sans','DejaVu Serif','Liberation Sans','Liberation Serif','Liberation Mono') and not covers(f,text)]
    if not faces: pytest.skip('Test font missing')
    source=next((f for f in faces if f.family==family and not f.bold and not f.italic),None)
    if source is None: pytest.skip('Requested test font missing')
    font=ImageFont.truetype(str(source.path),52)
    bb=font.getbbox(text,anchor='ls')
    image=Image.new('RGB',(bb[2]-bb[0]+8,bb[3]-bb[1]+8),(240,225,180))
    ImageDraw.Draw(image).text((4-bb[0],4-bb[1]),text,font=font,anchor='ls',fill=(30,50,100))
    match=match_font(image,text,2,FontCatalog(faces))
    assert match.family == source.family
    assert match.bold == source.bold and match.italic == source.italic
    assert match.size == pytest.approx(26,abs=1)
    assert match.color == pytest.approx((30/255,50/255,100/255),abs=.04)
    assert match.baseline == pytest.approx(4-bb[1],abs=2)


@pytest.mark.parametrize('mode',['RGB','L','1','CMYK'])
def test_raster_edit_replaces_original_image_preserves_pages_and_resolution(tmp_path,mode):
    source=tmp_path/'scan.pdf'
    doc,session,box,original=edit_scan(source,mode)
    dest=tmp_path/'out.pdf'
    save_atomic(doc,dest,session=session)
    with pikepdf.open(source) as before,pikepdf.open(dest) as after:
        assert before.pages[1].Contents.read_bytes() == after.pages[1].Contents.read_bytes()
        assert before.pages[0].Contents.read_bytes() == after.pages[0].Contents.read_bytes()
        images=[o for o in after.objects if isinstance(o,pikepdf.Stream) and o.get('/Subtype')=='/Image']
        assert len(images)==1
        assert (images[0].Width,images[0].Height)==(1200,600)
        rebuilt=pikepdf.PdfImage(images[0]).as_pil_image()
        assert rebuilt.getpixel((900,500))==original.convert(rebuilt.mode).getpixel((900,500))
        assert '/N1ceOCREditState' not in after.pages[0].obj
    with pdfium.PdfDocument(str(dest)) as pdf:
        page=pdf[0];tp=page.get_textpage()
        assert 'Changed text' not in tp.get_text_range()  # genuinely raster, not hidden by a patch
        assert 'Vector stays' in tp.get_text_range()
        import pypdfium2.raw as raw
        objects=list(page.get_objects(max_depth=20,textpage=tp))
        visible=[o for o in objects if o.type==raw.FPDF_PAGEOBJ_TEXT and raw.FPDFTextObj_GetTextRenderMode(o.raw)!=3]
        assert len(visible)==1  # native 'Vector stays' only; no visible replacement text layer
        for obj in objects: obj.close()
        tp.close();page.close()
    again=tmp_path/'again.pdf'
    save_atomic(doc,again,session=session)
    with pikepdf.open(again) as pdf:
        assert len([o for o in pdf.objects if isinstance(o,pikepdf.Stream) and o.get('/Subtype')=='/Image'])==1
    reopened=PdfDocument.open(dest)
    assert not EditSession(reopened).layer.all()
    reopened.close();doc.close()


@pytest.mark.parametrize('scale',[.75,1,2,4])
@pytest.mark.parametrize('rotation',[0,90,180,270])
def test_raster_preview_equals_saved_pixels(tmp_path,scale,rotation):
    doc,session,box,_=edit_scan(tmp_path/'source.pdf')
    doc.pdf.pages[0].obj.Rotate=rotation
    output=tmp_path/'output.pdf';save_atomic(doc,output,session=session)
    w,h,rgba,_=render_preview(box,rotation,scale,session.catalog,doc.snapshot_bytes())
    preview=np.frombuffer(rgba,np.uint8).reshape(h,w,4)
    actual=render_crop(output,display_bounds(box),scale,rotation)
    assert preview.shape==actual.shape
    assert np.abs(preview.astype(float)-actual.astype(float)).mean() < .05
    doc.close()


def test_complex_background_requires_opt_in_and_inpaint_is_local():
    image=Image.new('RGB',(600,300))
    pixels=np.array(image)
    for y in range(300):
        pixels[y,:,0]=170+np.arange(600)%50
        pixels[y,:,1]=160+y%50
        pixels[y,:,2]=200
    image=Image.fromarray(pixels)
    before=image.copy()
    ImageDraw.Draw(image).text((100,100),'Old',fill=(10,10,10),font_size=40)
    obj=TextBox((1,0),100,150,240,200,text='New',size=28,ocr_source=(95,150,200,205),ocr_original=None)
    with pytest.raises(ReconstructionError,match='Complex'):
        reconstruct(image,(0,0,600,300),[obj],default_catalog())
    result,_=reconstruct(image,(0,0,600,300),[replace(obj,ocr_background='inpaint')],default_catalog())
    assert result.getpixel((20,20))==before.getpixel((20,20))
    # Inpainting is approximate: require a local bounded error, not perfect restoration.
    error=np.abs(np.asarray(result.crop((95,95,200,150))).astype(float)-np.asarray(before.crop((95,95,200,150))).astype(float)).mean()
    assert error < 60


def test_fallback_is_explicit_and_only_affects_target_page(tmp_path):
    doc,session,box,_=edit_scan(tmp_path/'form.pdf',form=True)
    with pytest.raises(ReconstructionError,match='Enable affected-page'):
        rebuild_page(doc.pdf,0,[box],session.catalog)
    original=doc.pdf.pages[1].Contents.read_bytes()
    notes=rebuild_page(doc.pdf,0,[replace(box,ocr_allow_page_raster=True)],session.catalog)
    assert any('rasterized' in n for n in notes)
    assert doc.pdf.pages[1].Contents.read_bytes()==original
    out=io.BytesIO();doc.pdf.save(out)
    with pdfium.PdfDocument(out.getvalue()) as pdf:
        page=pdf[0];tp=page.get_textpage()
        assert not tp.get_text_range().strip()
        tp.close();page.close()
    doc.close()


def test_real_reocr_finds_new_raster_text(tmp_path):
    import shutil
    if not shutil.which('tesseract'): pytest.skip('Tesseract unavailable')
    doc,session,box,_=edit_scan(tmp_path/'source.pdf')
    dest=tmp_path/'out.pdf';save_atomic(doc,dest,session=session)
    lines=recognize_pdf(dest.read_bytes(),[0],'eng',threading.Event())
    text=' '.join(line.text for line in lines)
    assert 'Changed text' in text
    assert 'Original' not in text
    doc.close()


def test_native_font_metadata_character_spacing_and_width(tmp_path):
    source=tmp_path/'native.pdf'
    c=canvas.Canvas(str(source),pagesize=(500,300))
    text=c.beginText(50,200)
    text.setFont('Times-BoldItalic',20)
    text.setFillColorRGB(.2,.3,.6)
    text.setCharSpace(1.75)
    text.setHorizScale(85)
    text.textOut('Spaced original')
    c.drawText(text);c.showPage();c.save()
    doc=PdfDocument.open(source);session=EditSession(doc)
    run=session.inspect_page(0).runs[0]
    assert run.font_name=='Times-BoldItalic'
    assert run.spacing_known and run.char_spacing==pytest.approx(1.75)
    assert run.horizontal_scale==pytest.approx(85)
    from n1cepdf.edit.session import default_style
    style=default_style(run)
    assert style.bold and style.italic
    assert style.size==pytest.approx(20)
    assert style.color==pytest.approx((.2,.3,.6),abs=.005)
    plan=session.plan_text_edit(doc.page_key(0),run,'Привет România')
    session.commit(plan)
    replacement=session.layer.all()[0]
    assert replacement.char_spacing==pytest.approx(1.75)
    assert replacement.horizontal_scale==pytest.approx(85)
    assert replacement.size==20 and replacement.color==style.color
    output=tmp_path/'edited.pdf';save_atomic(doc,output,session=session)
    with pdfium.PdfDocument(str(output)) as pdf:
        page=pdf[0];tp=page.get_textpage()
        assert 'Привет România' in tp.get_text_range()
        tp.close();page.close()
    doc.close()


def test_restricted_embedded_font_is_not_reused():
    import struct
    data=bytearray(64)
    struct.pack_into('>H',data,4,1)
    struct.pack_into('>4sIII',data,12,b'OS/2',0,32,12)
    for restricted in (2,0x100,0x200):
        struct.pack_into('>H',data,40,restricted)
        assert not embedding_allowed(bytes(data))
    struct.pack_into('>H',data,40,0)
    assert embedding_allowed(bytes(data))


def test_full_embedded_true_type_can_be_reused_when_coverage_allows(tmp_path):
    source=tmp_path/'embedded.pdf'
    c=canvas.Canvas(str(source));c.drawString(50,700,'Original');c.showPage();c.save()
    face=default_catalog().faces[0]
    if not embedding_allowed(face.path.read_bytes()): pytest.skip('Test font restricted')
    # A full, permission-compatible font program, rather than a Unicode-remapped subset.
    with pikepdf.open(source) as pdf:
        font=pdf.pages[0].Resources.Font.F1
        font.BaseFont=pikepdf.Name('/ReusableTestFont')
        font.Subtype=pikepdf.Name.TrueType
        font.FontDescriptor=pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.FontDescriptor,FontName=pikepdf.Name('/ReusableTestFont'),
            Flags=32,FontBBox=[-100,-250,1200,1000],ItalicAngle=0,Ascent=900,Descent=-250,
            CapHeight=700,StemV=80,FontFile2=pdf.make_stream(face.path.read_bytes())))
        pdf.save(tmp_path/'full.pdf')
    doc=PdfDocument.open(tmp_path/'full.pdf')
    catalog=FontCatalog([])
    session=EditSession(doc,catalog)
    imported=catalog.find('ReusableTestFont',False,False)
    assert imported is not None
    assert not covers(imported,'Original')
    doc.close()


def test_jpeg_shared_resource_untouched_page_not_recompressed(tmp_path):
    image=Image.new('RGB',(1200,600),(240,225,180))
    jpeg=io.BytesIO();image.save(jpeg,'JPEG',quality=95)
    source=tmp_path/'shared.pdf'
    c=canvas.Canvas(str(source),pagesize=(600,300))
    for _ in range(2):
        c.drawImage(ImageReader(io.BytesIO(jpeg.getvalue())),0,0,600,300)
        c.showPage()
    c.save()
    doc=PdfDocument.open(source);session=EditSession(doc)
    box=TextBox(doc.page_key(0),50,200,250,250,text='Changed',size=22,ocr_source=(50,210,240,250),ocr_original=None)
    session.add_object(box)
    output=tmp_path/'out.pdf';notes=[]
    save_atomic(doc,output,session=session,warnings=notes)
    assert any('JPEG' in note for note in notes)
    with pikepdf.open(source) as before,pikepdf.open(output) as after:
        def image_on(page):
            return next(o for _,o in page.Resources.XObject.items() if o.get('/Subtype')=='/Image')
        assert image_on(before.pages[1]).read_raw_bytes()==image_on(after.pages[1]).read_raw_bytes()
        assert '/DCTDecode' in str(image_on(after.pages[1]).Filter)
        assert image_on(after.pages[0]).Filter=='/FlateDecode'
        assert image_on(after.pages[0]).objgen != image_on(after.pages[1]).objgen
    doc.close()


def test_crop_rotation_and_moved_rotated_text_keep_geometry(tmp_path):
    doc,session,box,_=edit_scan(tmp_path/'source.pdf')
    doc.pdf.pages[0].obj.CropBox=[20,10,580,290]
    doc.pdf.pages[0].obj.Rotate=90
    doc.pdf.pages[0].obj.UserUnit=1
    moved=replace(box,x0=260,y0=150,x1=460,y1=200,rotation=12)
    session.update_object(moved)
    output=tmp_path/'moved.pdf';save_atomic(doc,output,session=session)
    with pikepdf.open(output) as saved:
        assert list(saved.pages[0].CropBox)==[20,10,580,290]
        assert list(saved.pages[0].MediaBox)==[0,0,600,300]
        assert saved.pages[0].Rotate==90
    w,h,rgba,_=render_preview(moved,90,3,session.catalog,doc.snapshot_bytes())
    preview=np.frombuffer(rgba,np.uint8).reshape(h,w,4)
    actual=render_crop(output,display_bounds(moved),3,90)
    assert np.abs(preview.astype(float)-actual.astype(float)).mean()<.1
    doc.close()


def test_ordinary_text_preview_layout_remains_vector(tmp_path):
    from n1cepdf.edit.writer import apply_layer
    doc=PdfDocument.new_blank((500,300));session=EditSession(doc)
    box=TextBox(doc.page_key(0),60,180,440,250,text='Привет România',size=24,
                color=(.1,.2,.6),char_spacing=1.5,horizontal_scale=90)
    session.add_object(box)
    output=tmp_path/'native.pdf';save_atomic(doc,output,session=session)
    for scale in (1,2.5,4):
        w,h,rgba,_=render_preview(box,0,scale,session.catalog)
        preview=np.frombuffer(rgba,np.uint8).reshape(h,w,4)
        actual=render_crop(output,display_bounds(box),scale)
        # Transparent overlays and the blank saved page should share actual glyph samples.
        assert preview.shape==actual.shape
        assert np.abs(preview.astype(float)-actual.astype(float)).mean()<.1
    with pdfium.PdfDocument(str(output)) as pdf:
        page=pdf[0];tp=page.get_textpage()
        assert 'Привет România' in tp.get_text_range()
        tp.close();page.close()
    doc.close()


def test_overlapping_ocr_objects_preview_uses_combined_reconstruction(tmp_path):
    doc,session,box,_=edit_scan(tmp_path/'source.pdf')
    other=replace(box,id='second-line',text='Second',x0=180,x1=300,y0=180,y1=230,
                  ocr_source=(180,180,260,220))
    session.add_object(other)
    output=tmp_path/'both.pdf';save_atomic(doc,output,session=session)
    for obj in (box,other):
        w,h,rgba,_=render_preview(obj,0,2,session.catalog,doc.snapshot_bytes(),0,[box,other])
        preview=np.frombuffer(rgba,np.uint8).reshape(h,w,4)
        actual=render_crop(output,display_bounds(obj),2)
        assert np.abs(preview.astype(float)-actual.astype(float)).mean()<.1
    doc.close()
