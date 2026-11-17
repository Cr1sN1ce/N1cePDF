"""Adaptive word segmentation, independent pixel masks and editable layout persistence."""
import io
import shutil
import threading
from dataclasses import replace
import numpy as np
import pytest
import pikepdf
from PIL import Image,ImageDraw,ImageFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from n1cepdf.core.ocr import parse_lines,recognize_pdf,Tesseract
from n1cepdf.core.ocr_layout import OcrWord,segment_rows
from n1cepdf.core.document import PdfDocument
from n1cepdf.edit.geometry import PageTransform
from n1cepdf.core.file_manager import save_atomic
from n1cepdf.edit.fonts import default_catalog
from n1cepdf.edit.session import EditSession
from n1cepdf.edit.objects import TextBox
from n1cepdf.edit.preview import render_preview,display_bounds
from tests.unit.test_scan_quality import render_crop

HEADER='level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'


def tsv_rows(rows,scale=1):
    text=HEADER
    for line,words in enumerate(rows,1):
        for number,(word,x,width,height) in enumerate(words,1):
            values=(5,1,1,1,line,number,x*scale,(30+line*40)*scale,width*scale,height*scale,95,word)
            text+='\t'.join(str(value) for value in values)+'\n'
    return text


def left_right_rows():
    return [[('Parole',30,55,20),('și',95,15,20),('identitate',120,85,20),
             ('4/4',700,28,20),('·',738,5,8),('100%',753,45,20)],
            [('Securitate',30,90,20),('fizică',130,45,20),
             ('6/6',700,28,20),('·',738,5,8),('100%',753,45,20)]]


@pytest.mark.parametrize('scale',[.5,1,4])
def test_left_label_right_counter_separate_at_any_resolution(scale):
    lines=parse_lines(tsv_rows(left_right_rows(),scale),0,
                      PageTransform((0,0,900,400),0,900*scale,400*scale))
    assert [line.text for line in lines]==['Parole și identitate','4/4 · 100%','Securitate fizică','6/6 · 100%']
    assert [line.align for line in lines]==['left','right','left','right']
    assert lines[0].rect[2] < 210 and lines[1].rect[0]==700
    assert lines[1].rect[2]==798 and lines[3].rect[2]==798
    assert len(lines[0].words)==3


@pytest.mark.parametrize('words',[
    [('Securitatea',10,100,20),('informațională',120,130,20)],
    [('A',10,8,20),('normal',27,55,20),('English',92,65,20),('sentence.',167,80,20)],
    [('Привет',10,65,20),('мир',85,35,20),('România',130,75,20)]])
def test_ordinary_sentence_is_not_oversegmented(words):
    from n1cepdf.edit.geometry import PageTransform
    lines=parse_lines(tsv_rows([words]),0,PageTransform((0,0,900,400),0,900,400))
    assert len(lines)==1
    assert lines[0].text==' '.join(word[0] for word in words)


def test_table_and_two_columns():
    rows=[[OcrWord('Name',(10,200,50,220)),OcrWord('Account',(180,200,245,220)),OcrWord('100%',(400,200,450,220))],
          [OcrWord('Other',(10,160,60,180)),OcrWord('Status',(180,160,230,180)),OcrWord('90%',(410,160,450,180))]]
    segments=segment_rows(rows)
    assert len(segments)==6
    assert [align for words,align in segments if any('%' in word.text for word in words)]==['right','right']
    columns=[[OcrWord('Left',(10,200,50,220)),OcrWord('paragraph',(60,200,150,220)),
              OcrWord('Right',(350,200,400,220)),OcrWord('paragraph',(410,200,500,220))]]
    assert len(segment_rows(columns))==2


def make_layout_pdf(path):
    image=Image.new('RGB',(1800,700),(245,235,205))
    face=default_catalog().find('DejaVu Sans',False,False) or default_catalog().faces[0]
    font=ImageFont.truetype(str(face.path),44)
    draw=ImageDraw.Draw(image)
    rows=[('Securitate fizică','6/6 · 100%'),('Parole și identitate','4/4 · 100%'),
          ('Securitatea dispozitivelor','8/8 · 100%')]
    for n,(label,value) in enumerate(rows):
        y=70+n*160
        draw.text((60,y),label,font=font,fill=(20,30,70))
        draw.text((1720-draw.textlength(value,font=font),y),value,font=font,fill=(20,30,70))
    # This detail in the gutter must survive both independent edits and manual merges.
    draw.rectangle((850,80,860,480),fill=(70,160,110))
    c=canvas.Canvas(str(path),pagesize=(900,350))
    c.drawImage(ImageReader(image),0,0,900,350);c.showPage();c.save()
    return image,face


def layout_objects(doc,face):
    words=(('Parole',(30,210,95,235)),('și',(105,210,120,235)),('identitate',(130,210,240,235)),
           ('4/4',(710,210,745,235)),('·',(755,210,760,235)),('100%',(770,210,860,235)))
    left=TextBox(doc.page_key(0),30,200,241,240,text='Parole și identitate',family=face.family,size=22,
                 ocr_source=(29.5,209.5,240.5,235.5),ocr_original='Parole și identitate',
                 ocr_initial_rect=(30,200,241,240),ocr_words=words[:3])
    right=TextBox(doc.page_key(0),710,200,860,240,text='4/4 · 100%',family=face.family,size=22,align='right',
                  ocr_source=(709.5,209.5,860.5,235.5),ocr_original='4/4 · 100%',
                  ocr_initial_rect=(710,200,860,240),ocr_words=words[3:])
    return left,right


@pytest.mark.parametrize('side',['left','right'])
def test_edit_one_block_preserves_neighbour_and_gutter_pixels(tmp_path,side):
    source=tmp_path/'layout.pdf';original,face=make_layout_pdf(source)
    doc=PdfDocument.open(source);session=EditSession(doc)
    left,right=layout_objects(doc,face)
    session.add_object(left);session.add_object(right)
    selected=left if side=='left' else right
    neighbour=right if side=='left' else left
    # These test boxes deliberately contain the relevant line but only their own masks.
    session.update_object(replace(selected,text='Changed' if side=='left' else '3/3 · 90%'))
    assert session.layer.get(neighbour.id)==neighbour
    if side=='right': assert session.layer.get(right.id).x1==right.x1
    output=tmp_path/'out.pdf';save_atomic(doc,output,session=session)
    with pikepdf.open(output) as pdf:
        stream=next(obj for obj in pdf.objects if isinstance(obj,pikepdf.Stream) and obj.get('/Subtype')=='/Image')
        raster=pikepdf.PdfImage(stream).as_pil_image().convert('RGB')
    other_crop=(1400,200,1750,310) if side=='left' else (40,200,600,310)
    assert np.array_equal(np.array(original.crop(other_crop)),np.array(raster.crop(other_crop)))
    assert np.array_equal(np.array(original.crop((840,60,880,500))),np.array(raster.crop((840,60,880,500))))
    reopened=PdfDocument.open(output);restored=EditSession(reopened)
    assert [obj.text for obj in restored.layer.all()]==[neighbour.text]
    # Reopening must retain committed raster edits, even while restoring untouched OCR metadata.
    second=tmp_path/'resaved.pdf';save_atomic(reopened,second,session=restored)
    assert np.array_equal(render_crop(output,(0,0,900,350)),render_crop(second,(0,0,900,350)))
    reopened.close();doc.close()


def test_manual_merge_split_undo_and_word_geometry_persistence(tmp_path):
    source=tmp_path/'layout.pdf';_,face=make_layout_pdf(source)
    doc=PdfDocument.open(source);session=EditSession(doc)
    left,right=layout_objects(doc,face)
    session.add_object(left);session.add_object(right)
    merged=session.merge_ocr_blocks(left.id,right.id)
    assert len(merged.ocr_regions)==2
    assert len(merged.ocr_words)==6
    session.undo();assert session.layer.all()==[left,right]
    session.redo();assert session.layer.all()==[merged]
    children=session.split_ocr_block(merged.id,len(left.text))
    assert [child.text for child in children]==[left.text,right.text]
    assert children[0].ocr_source[2]<children[1].ocr_source[0]
    assert children[1].align=='right'
    session.undo();assert session.layer.all()==[merged]
    session.redo();assert session.layer.all()==children
    output=tmp_path/'state.pdf';save_atomic(doc,output,session=session)
    reopened=PdfDocument.open(output);restored=EditSession(reopened)
    assert [obj.ocr_words for obj in restored.layer.all()]==[obj.ocr_words for obj in children]
    assert [obj.align for obj in restored.layer.all()]==['left','right']
    reopened.close();doc.close()


@pytest.mark.skipif(not shutil.which('tesseract'),reason='Tesseract unavailable')
def test_real_tesseract_independent_romanian_fields(tmp_path):
    engine=Tesseract()
    if 'ron' not in engine.languages(threading.Event()): pytest.skip('Romanian data unavailable')
    source=tmp_path/'real.pdf';make_layout_pdf(source)
    lines=recognize_pdf(source.read_bytes(),[0],'ron',threading.Event())
    assert any('Parole' in line.text and 'identitate' in line.text for line in lines)
    assert sum('100%' in line.text for line in lines)==3
    assert all(not ('100%' in line.text and any(word in line.text for word in ('Parole','Securitate','Securitatea'))) for line in lines)
    values=[line for line in lines if '100%' in line.text]
    assert all(line.align=='right' for line in values)
    assert max(line.rect[2] for line in values)-min(line.rect[2] for line in values)<3


def test_manual_merged_masks_preserve_gutter_detail_after_raster_edit(tmp_path):
    source=tmp_path/'layout.pdf';original,face=make_layout_pdf(source)
    doc=PdfDocument.open(source);session=EditSession(doc)
    left,right=layout_objects(doc,face)
    session.add_object(left);session.add_object(right)
    merged=session.merge_ocr_blocks(left.id,right.id)
    session.update_object(replace(merged,text='New'))
    output=tmp_path/'merged-edit.pdf';save_atomic(doc,output,session=session)
    with pikepdf.open(output) as saved:
        stream=next(obj for obj in saved.objects if isinstance(obj,pikepdf.Stream) and obj.get('/Subtype')=='/Image')
        raster=pikepdf.PdfImage(stream).as_pil_image()
        assert np.array_equal(np.array(original.crop((840,60,880,500))),np.array(raster.crop((840,60,880,500))))
    doc.close()
