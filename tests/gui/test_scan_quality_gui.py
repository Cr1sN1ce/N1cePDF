"""End-to-end real OCR, Properties editing, device pixels and raster PDF export."""
import math
import shutil
import numpy as np
import pytest
import pikepdf
from PySide6.QtWidgets import QMessageBox
from n1cepdf.ui.ocr_dialog import OcrDialog
from n1cepdf.edit.preview import display_bounds
from n1cepdf.core.document import PdfDocument
from n1cepdf.edit.session import EditSession
from tests.unit.test_scan_quality import make_scan, render_crop
from tests.gui.test_edit_gui import prep, quiet, view_pos, send, save_as


@pytest.mark.skipif(not shutil.which('tesseract'),reason='Tesseract unavailable')
def test_real_gui_ocr_font_preview_hidpi_raster_save(qtbot,window,tmp_path,monkeypatch):
    source=tmp_path/'source.pdf'
    make_scan(source)
    tab=prep(qtbot,window,source)
    dialog=OcrDialog(tab)
    dialog.scope.setCurrentIndex(1)
    dialog.pages.setText("1")
    dialog.start()
    qtbot.waitUntil(lambda: not tab.busy,timeout=30000)
    box=next(o for o in tab.session.layer.all() if 'Original' in o.text)
    assert box.family in tab.session.catalog.families()
    assert box.color[2] > box.color[0]  # blue scan glyphs, not default black
    x=(box.x0+box.x1)/2;y=(box.y0+box.y1)/2
    send(tab,'press',view_pos(tab,0,x,y));send(tab,'release',view_pos(tab,0,x,y))
    assert tab.controller.selected_id==box.id
    editor=tab.panel.tx_content
    window.activateWindow()
    editor.setFocus()
    qtbot.waitUntil(editor.hasFocus)
    editor.selectAll()
    qtbot.keyClicks(editor,'Changed text')
    changed=tab.session.layer.get(box.id)
    assert changed.text=='Changed text' and editor.hasFocus()
    assert editor.textCursor().position()==len('Changed text')
    tab.undo();assert tab.session.layer.get(box.id).text==box.text
    tab.redo();assert tab.session.layer.get(box.id).text=='Changed text'
    monkeypatch.setattr(tab.view,'_dpr',lambda:2.0)
    tab.view.set_zoom(1.25)
    overlay=tab.controller.overlay
    qtbot.waitUntil(lambda: overlay.entries[box.id].rendered==changed and
                   overlay.entries[box.id].scale_key==tab.view.render_scale(),timeout=30000)
    entry=overlay.entries[box.id]
    assert entry.pix.scale()==1.0
    assert entry.pix.pixmap().devicePixelRatio()==2.0
    output=tmp_path/'saved.pdf'
    save_as(qtbot,window,tab,output,monkeypatch)
    actual=render_crop(output,display_bounds(changed),tab.view.render_scale())
    from PySide6.QtGui import QImage
    image=entry.pix.pixmap().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    preview=np.frombuffer(image.bits(),np.uint8).reshape(image.height(),image.bytesPerLine())[:,:image.width()*4].reshape(image.height(),image.width(),4)
    assert preview.shape==actual.shape
    assert np.abs(preview.astype(float)-actual.astype(float)).mean()<.1
    with pikepdf.open(output) as pdf:
        assert len([o for o in pdf.objects if isinstance(o,pikepdf.Stream) and o.get('/Subtype')=='/Image'])==1
        assert '/N1ceOCREditState' not in pdf.pages[0].obj
    reopened=PdfDocument.open(output)
    assert not EditSession(reopened).layer.all()
    reopened.close()


def test_reconstruction_permissions_require_confirmation(qtbot,window,tmp_path,monkeypatch):
    from n1cepdf.edit.objects import TextBox
    source=tmp_path/'source.pdf';make_scan(source)
    tab=prep(qtbot,window,source)
    rect=(50,210,240,260)
    obj=TextBox(tab.doc.page_key(0),*rect,text='OCR',ocr_source=rect,ocr_original='OCR',ocr_initial_rect=rect)
    tab.session.add_object(obj);tab.controller.select(obj)
    messages=[]
    def refuse(parent,title,message):
        messages.append(message)
        return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox,'question',refuse)
    tab.panel.tx_ocr_background.setCurrentIndex(1)
    assert messages and tab.session.layer.get(obj.id).ocr_background=='auto'
    assert tab.panel.tx_ocr_background.currentIndex()==0
    monkeypatch.setattr(QMessageBox,'question',lambda *_:QMessageBox.StandardButton.Yes)
    tab.panel.tx_page_raster.setChecked(True)
    assert tab.session.layer.get(obj.id).ocr_allow_page_raster
    tab.undo()
    assert not tab.session.layer.get(obj.id).ocr_allow_page_raster
