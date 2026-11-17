"""Exercise real Qt checkbox state through the writer on a 41-page Form scan."""
import shutil
import pytest
from PySide6.QtWidgets import QMessageBox
from n1cepdf.ui.ocr_dialog import OcrDialog
from n1cepdf.edit.scan_rebuild import changed
from n1cepdf.core.ocr import OcrLine
from tests.gui.test_edit_gui import prep,save_as,view_pos,send
from tests.unit.test_ocr_save_fallback import make_41_page_form_scan,assert_only_first_page_rasterized
from tests.unit.test_ocr_segmentation import make_layout_pdf,layout_objects


@pytest.mark.skipif(not shutil.which('tesseract'),reason='Tesseract unavailable')
def test_properties_permission_reaches_real_writer_and_raster_fallback(qtbot,window,tmp_path,monkeypatch):
    source=tmp_path/'source.pdf';make_41_page_form_scan(source)
    original=source.read_bytes()
    tab=prep(qtbot,window,source)
    dialog=OcrDialog(tab);dialog.scope.setCurrentIndex(1);dialog.pages.setText('1');dialog.start()
    qtbot.waitUntil(lambda:not tab.busy,timeout=30000)
    left=next(obj for obj in tab.session.layer.all() if 'Original' in obj.text)
    right=next(obj for obj in tab.session.layer.all() if '100%' in obj.text)
    assert left.x1<right.x0 and right.align=='right'
    # Edit two independent blocks, then grant page consent through just the selected block.
    for obj,text in ((right,'3/3 · 90%'),(left,'Updated label')):
        pos=view_pos(tab,0,(obj.x0+obj.x1)/2,(obj.y0+obj.y1)/2)
        send(tab,'press',pos);send(tab,'release',pos)
        assert tab.controller.selected_id==obj.id
        window.activateWindow();editor=tab.panel.tx_content;editor.setFocus()
        qtbot.waitUntil(editor.hasFocus)
        editor.selectAll();qtbot.keyClicks(editor,text)
        assert editor.textCursor().position()==len(text)
    confirmations=[]
    def approve(parent,title,message):
        confirmations.append(message);return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox,'question',approve)
    tab.panel.tx_page_raster.setChecked(True)
    assert confirmations and tab.session.page_raster_allowed(left.page_key)
    assert all(obj.ocr_allow_page_raster for obj in tab.session.layer.all())
    # One undo revokes the whole-page consent; redo restores it, leaving both texts intact.
    tab.undo();assert not tab.session.page_raster_allowed(left.page_key)
    tab.redo();assert tab.session.page_raster_allowed(left.page_key)
    assert tab.session.layer.get(left.id).text=='Updated label'
    seen=[]
    import n1cepdf.edit.writer as writer
    real_apply=writer.apply_layer
    def observe(pdf,by_page,catalog=None):
        seen.append([(obj.text,obj.ocr_allow_page_raster) for obj in by_page[0]])
        return real_apply(pdf,by_page,catalog)
    monkeypatch.setattr(writer,'apply_layer',observe)
    output=tmp_path/'saved.pdf';save_as(qtbot,window,tab,output,monkeypatch)
    assert seen and all(allow for _,allow in seen[-1])
    assert source.read_bytes()==original
    assert_only_first_page_rasterized(source,output)


def test_checkbox_on_untouched_block_does_not_rasterize_that_block(qtbot,window,tmp_path,monkeypatch):
    source=tmp_path/'layout.pdf';_,face=make_layout_pdf(source)
    tab=prep(qtbot,window,source)
    left,right=layout_objects(tab.doc,face)
    tab.session.add_object(left);tab.session.add_object(right)
    tab.controller.select(left);tab.controller.on_object_changed({'text':'Changed label'})
    tab.controller.select(right)
    monkeypatch.setattr(QMessageBox,'question',lambda *_:QMessageBox.StandardButton.Yes)
    tab.panel.tx_page_raster.setChecked(True)
    assert tab.session.layer.get(right.id).ocr_original==right.text
    assert not changed(tab.session.layer.get(right.id))
    assert tab.session.layer.get(left.id).ocr_allow_page_raster
    tab.controller.select(left);assert tab.panel.tx_page_raster.isChecked()
    tab.panel.tx_page_raster.setChecked(False)
    assert not tab.session.page_raster_allowed(left.page_key)
    tab.controller.select(right);assert not tab.panel.tx_page_raster.isChecked()


@pytest.mark.parametrize('retained_words',[False,True])
def test_repeated_ocr_repairs_restored_common_frame_and_preserves_edits(qtbot,window,tmp_path,monkeypatch,retained_words):
    from dataclasses import replace
    from n1cepdf.core.document import PdfDocument
    from n1cepdf.core.file_manager import save_atomic
    from n1cepdf.edit.session import EditSession
    from n1cepdf.edit.objects import TextBox
    from n1cepdf.core.ocr_layout import OcrWord
    source=tmp_path/'layout.pdf';_,face=make_layout_pdf(source)
    doc=PdfDocument.open(source);session=EditSession(doc)
    left,right=layout_objects(doc,face)
    rect=(left.x0,left.y0,right.x1,max(left.y1,right.y1))
    text=left.text+' '+right.text
    legacy=replace(left,id='legacy-common',x0=rect[0],y0=rect[1],x1=rect[2],y1=rect[3],
                   text=text,ocr_original=text,ocr_initial_rect=rect,
                   ocr_source=(left.ocr_source[0],left.ocr_source[1],right.ocr_source[2],right.ocr_source[3]),
                   ocr_words=left.ocr_words+right.ocr_words if retained_words else ())
    session.add_object(legacy)
    saved=tmp_path/'legacy.pdf';save_atomic(doc,saved,session=session);doc.close()
    tab=prep(qtbot,window,saved)
    old=tab.session.layer.get(legacy.id);assert old.text==text
    protected=TextBox(tab.doc.page_key(0),30,70,300,105,text='User correction',
                      ocr_source=(30,70,300,105),ocr_original=None)
    tab.session.add_object(protected)
    messages=[];tab.message.connect(messages.append)
    def recognize(data,pages,language,cancel,progress,**options):
        assert protected.ocr_source in options['excluded'][0]
        assert old.ocr_source not in options['excluded'][0]
        return [OcrLine(0,left.text,(30,210,240,235),22,
                        words=tuple(OcrWord(t,r) for t,r in left.ocr_words)),
                OcrLine(0,right.text,(710,210,860,235),22,
                        words=tuple(OcrWord(t,r) for t,r in right.ocr_words),align='right')]
    monkeypatch.setattr('n1cepdf.ui.ocr_dialog.recognize_pdf',recognize)
    dialog=OcrDialog(tab)
    assert dialog.replace_previous.isChecked()
    assert 'не будут удалены' in dialog.resegment_hint.text()
    dialog.start();qtbot.waitUntil(lambda:not tab.busy)
    assert tab.session.layer.get(legacy.id) is None
    assert tab.session.layer.get(protected.id)==protected
    recognized=[obj for obj in tab.session.layer.all() if obj.id!=protected.id]
    assert len(recognized)==2 and recognized[0].x1<recognized[1].x0
    assert recognized[1].align=='right'
    tab.controller.set_tool('edit_text')
    for obj in recognized:
        pos=view_pos(tab,0,(obj.x0+obj.x1)/2,(obj.y0+obj.y1)/2)
        send(tab,'press',pos);send(tab,'release',pos)
        assert tab.controller.selected_id==obj.id
    assert any('не пересегментирована' in message for message in messages)
    tab.undo()
    assert tab.session.layer.get(legacy.id)==old and tab.session.layer.get(protected.id)==protected


def test_old_preview_denial_is_discarded_after_page_consent(qtbot,window,tmp_path,monkeypatch):
    import threading
    from n1cepdf.edit.objects import TextBox
    from n1cepdf.edit.scan_rebuild import ReconstructionError
    source=tmp_path/'layout.pdf';make_layout_pdf(source)
    tab=prep(qtbot,window,source)
    started=threading.Event();release=threading.Event()
    def preview(obj,rotation,scale,catalog,data,index,objects,allowed):
        if not allowed:
            started.set();release.wait(5)
            raise ReconstructionError('old permission denied')
        return 1,1,bytes([255,255,255,255]),[]
    monkeypatch.setattr('n1cepdf.ui.edit_overlay.render_preview',preview)
    monkeypatch.setattr(QMessageBox,'question',lambda *_:QMessageBox.StandardButton.Yes)
    messages=[];tab.message.connect(messages.append)
    obj=TextBox(tab.doc.page_key(0),30,200,240,240,text='Changed',ocr_source=(30,210,240,235),ocr_original=None)
    tab.session.add_object(obj);tab.controller.select(obj)
    try:
        qtbot.waitUntil(started.is_set)
        tab.panel.tx_page_raster.setChecked(True)
    finally:
        release.set()
    current=tab.session.layer.get(obj.id)
    overlay=tab.controller.overlay
    qtbot.waitUntil(lambda:overlay.entries[obj.id].rendered==current,timeout=10000)
    assert not any('old permission denied' in message for message in messages)
