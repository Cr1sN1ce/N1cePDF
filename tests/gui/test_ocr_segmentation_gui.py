"""Independent OCR selection, cursor split and neighbour merge through Qt controls."""
import shutil
import pytest
from PySide6.QtWidgets import QInputDialog
from PySide6.QtCore import Qt
from n1cepdf.ui.ocr_dialog import OcrDialog
from tests.gui.test_edit_gui import prep,view_pos,send,save_as
from tests.unit.test_ocr_segmentation import make_layout_pdf,layout_objects


def click_block(tab,obj):
    pos=view_pos(tab,0,(obj.x0+obj.x1)/2,(obj.y0+obj.y1)/2)
    send(tab,'press',pos);send(tab,'release',pos)


def test_manual_split_and_merge_buttons_are_undoable(qtbot,window,tmp_path,monkeypatch):
    source=tmp_path/'layout.pdf';_,face=make_layout_pdf(source)
    tab=prep(qtbot,window,source)
    left,right=layout_objects(tab.doc,face)
    tab.session.add_object(left);tab.session.add_object(right)
    tab.controller.set_tool('edit_text')
    click_block(tab,left);assert tab.controller.selected_id==left.id
    click_block(tab,right);assert tab.controller.selected_id==right.id
    tab.controller.select(left)
    monkeypatch.setattr(QInputDialog,'getItem',lambda parent,title,label,items,*args:(items[0],True))
    qtbot.mouseClick(tab.panel.tx_merge_ocr,Qt.MouseButton.LeftButton)
    merged,=tab.session.layer.all()
    assert merged.ocr_words==left.ocr_words+right.ocr_words
    cursor=tab.panel.tx_content.textCursor();cursor.setPosition(len(left.text))
    tab.panel.tx_content.setTextCursor(cursor)
    qtbot.mouseClick(tab.panel.tx_split_ocr,Qt.MouseButton.LeftButton)
    children=tab.session.layer.all()
    assert len(children)==2 and children[1].align=='right'
    tab.undo();assert tab.session.layer.all()==[merged]
    tab.undo();assert tab.session.layer.all()==[left,right]
    tab.redo();tab.redo();assert tab.session.layer.all()==children


@pytest.mark.skipif(not shutil.which('tesseract'),reason='Tesseract unavailable')
def test_real_ocr_left_right_mouse_selection_and_independent_edit(qtbot,window,tmp_path):
    source=tmp_path/'layout.pdf';make_layout_pdf(source)
    tab=prep(qtbot,window,source)
    dialog=OcrDialog(tab)
    dialog.language.setCurrentIndex(2)  # Romanian
    dialog.start()
    qtbot.waitUntil(lambda:not tab.busy,timeout=30000)
    objects=tab.session.layer.all()
    left=next(obj for obj in objects if 'Parole' in obj.text)
    right=min((obj for obj in objects if '100%' in obj.text),key=lambda obj:abs(obj.y1-left.y1))
    assert left.x1<right.x0 and right.align=='right'
    click_block(tab,left);assert tab.controller.selected_id==left.id
    click_block(tab,right);assert tab.controller.selected_id==right.id
    tab.controller.on_object_changed({'text':'3/3 · 90%'})
    updated=tab.session.layer.get(right.id)
    assert updated.x1==right.x1
    assert tab.session.layer.get(left.id)==left
    tab.undo();assert tab.session.layer.get(right.id)==right
    click_block(tab,left)
    window.activateWindow();tab.panel.tx_content.setFocus()
    qtbot.waitUntil(tab.panel.tx_content.hasFocus)
    tab.panel.tx_content.selectAll();qtbot.keyClicks(tab.panel.tx_content,'Changed label')
    assert tab.session.layer.get(right.id)==right
    assert tab.panel.tx_content.textCursor().position()==len('Changed label')


def test_resegment_legacy_ocr_is_undoable(qtbot,window,tmp_path,monkeypatch):
    from n1cepdf.edit.objects import TextBox
    from n1cepdf.core.ocr import OcrLine
    source=tmp_path/'layout.pdf';make_layout_pdf(source)
    tab=prep(qtbot,window,source)
    legacy=TextBox(tab.doc.page_key(0),30,200,860,240,text='Parole și identitate 4/4 · 100%',
                   ocr_source=(30,200,860,240),ocr_original='Parole și identitate 4/4 · 100%',
                   ocr_initial_rect=(30,200,860,240))
    tab.session.add_object(legacy)
    def recognize(data,pages,language,cancel,progress,**options):
        assert not options['excluded'][0]
        return [OcrLine(0,'Parole și identitate',(30,210,240,235),22),
                OcrLine(0,'4/4 · 100%',(710,210,860,235),22,align='right')]
    monkeypatch.setattr('n1cepdf.ui.ocr_dialog.recognize_pdf',recognize)
    dialog=OcrDialog(tab);dialog.replace_previous.setChecked(True);dialog.start()
    qtbot.waitUntil(lambda:not tab.busy)
    assert len(tab.session.layer.all())==2
    tab.undo();assert tab.session.layer.all()==[legacy]
    tab.redo();assert len(tab.session.layer.all())==2
