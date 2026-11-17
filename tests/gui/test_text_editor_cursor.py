"""Regression coverage for synchronous Properties/model feedback during text input."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QInputMethodEvent, QTextCursor
from PySide6.QtWidgets import QApplication
from n1cepdf.edit.objects import TextBox, Comment
from tests.gui.test_edit_gui import prep, save_as, text_of_file


def select_editor(tab, kind, text=''):
    key = tab.doc.page_key(0)
    if kind == 'comment':
        obj = Comment(key, 72, 650, text=text)
        editor = tab.panel.sm_text
    else:
        rect = (72, 580, 400, 630)
        kwargs = (dict(ocr_source=rect, ocr_original=text, ocr_initial_rect=rect)
                  if kind == 'ocr' else {})
        obj = TextBox(key, *rect, text=text, **kwargs)
        editor = tab.panel.tx_content
    tab.session.add_object(obj)
    tab.controller.select(obj)
    editor.setFocus()
    return obj, editor


def commit_character(editor, char):
    # QTest.keyClicks supports Latin-1 only. IME commits exercise real Unicode input.
    event = QInputMethodEvent()
    event.setCommitString(char)
    QApplication.sendEvent(editor, event)


@pytest.mark.parametrize('kind', ['ocr', 'text', 'comment'])
def test_continuous_unicode_input_keeps_cursor(qtbot, window, sample3, kind):
    tab = prep(qtbot, window, sample3)
    obj, editor = select_editor(tab, kind)
    changes = []
    tab.panel.object_changed.connect(changes.append)
    phrase = 'Привет мир'
    for index, char in enumerate(phrase, 1):
        commit_character(editor, char)
        assert editor.toPlainText() == phrase[:index]
        assert editor.textCursor().position() == index
        assert editor.hasFocus()
        assert tab.session.layer.get(obj.id).text == phrase[:index]
    assert len(changes) == len(phrase)  # no recursive UI/model updates


@pytest.mark.parametrize('kind', ['ocr', 'text', 'comment'])
def test_middle_insertion_clipboard_delete_and_undo(qtbot, window, sample3, kind):
    tab = prep(qtbot, window, sample3)
    obj, editor = select_editor(tab, kind, 'Hello world')
    cursor = editor.textCursor()
    cursor.setPosition(6)
    editor.setTextCursor(cursor)
    qtbot.keyClicks(editor, 'nice ')
    assert editor.toPlainText() == 'Hello nice world'
    assert editor.textCursor().position() == 11
    qtbot.keyClick(editor, Qt.Key.Key_Backspace)
    assert editor.toPlainText() == 'Hello niceworld'
    assert editor.textCursor().position() == 10
    qtbot.keyClick(editor, Qt.Key.Key_Delete)
    assert editor.toPlainText() == 'Hello niceorld'
    assert editor.textCursor().position() == 10

    cursor = editor.textCursor()
    cursor.setPosition(6)
    cursor.setPosition(10, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    # An unrelated model property refresh must preserve selection and caret direction.
    tab.controller.on_object_changed({'color': (0.2, 0.3, 0.4)})
    assert editor.textCursor().selectedText() == 'nice'
    assert editor.textCursor().anchor() == 6
    assert editor.textCursor().position() == 10
    assert editor.hasFocus()
    qtbot.keyClick(editor, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == 'nice'
    qtbot.keyClick(editor, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert editor.toPlainText() == 'Hello niceorld'
    assert editor.textCursor().position() == 10
    QApplication.clipboard().setText('Привет ')
    qtbot.keyClick(editor, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert editor.toPlainText() == 'Hello niceПривет orld'
    assert editor.textCursor().position() == 17
    pasted = editor.toPlainText()
    qtbot.keyClick(editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert editor.toPlainText() == 'Hello niceorld'
    qtbot.keyClick(editor, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert editor.toPlainText() == pasted
    assert tab.session.layer.get(obj.id).text == pasted

    qtbot.keyClick(editor, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert editor.textCursor().selectedText() == pasted
    qtbot.keyClicks(editor, 'Replacement')
    assert editor.toPlainText() == 'Replacement'
    assert editor.textCursor().position() == len('Replacement')
    assert editor.hasFocus()
    assert tab.session.layer.get(obj.id).text == 'Replacement'


def test_ocr_typing_saves_and_document_undo_refreshes(qtbot, window, sample3, tmp_path, monkeypatch):
    from tests.unit.test_ocr import scan_pdf
    scanned = tmp_path/'scanned.pdf'
    scanned.write_bytes(scan_pdf())
    tab = prep(qtbot, window, scanned)
    obj, editor = select_editor(tab, 'ocr', 'Old scan')
    editor.selectAll()
    for char in 'Привет мир':
        commit_character(editor, char)
    assert editor.toPlainText() == 'Привет мир'
    tab.undo()
    assert editor.toPlainText() == 'Old scan'
    assert editor.textCursor().position() == len('Old scan')
    assert editor.hasFocus()
    tab.redo()
    assert editor.toPlainText() == 'Привет мир'
    assert editor.hasFocus()
    assert tab.session.layer.get(obj.id).text == 'Привет мир'
    output = tmp_path/'typed.pdf'
    save_as(qtbot, window, tab, output, monkeypatch)
    assert 'Привет мир' not in text_of_file(output)  # OCR edits are saved as scan pixels
    import pikepdf
    with pikepdf.open(output) as saved:
        assert len([o for o in saved.objects if isinstance(o,pikepdf.Stream) and o.get('/Subtype')=='/Image']) == 1
    assert 'Old scan' not in text_of_file(output)


def test_switching_equal_text_objects_resets_editor_undo(qtbot, window, sample3):
    tab = prep(qtbot, window, sample3)
    first, editor = select_editor(tab, 'text', 'first')
    editor.selectAll()
    qtbot.keyClicks(editor, 'shared')
    assert editor.document().isUndoAvailable()
    second = TextBox(first.page_key, 72, 450, 400, 500, text='shared')
    tab.session.add_object(second)
    tab.controller.select(second)
    assert not editor.document().isUndoAvailable()
    assert editor.toPlainText() == 'shared'


def test_native_pdf_text_dialog_keeps_cursor_and_saves(qtbot, window, sample3, tmp_path, monkeypatch):
    from n1cepdf.ui.text_dialog import TextEditDialog
    from tests.gui.test_edit_gui import quiet
    tab = prep(qtbot, window, sample3)
    run = tab.session.inspect_page(0).runs[0]
    dialog = TextEditDialog(run, tab.session.catalog.families(), tab)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    dialog.activateWindow()
    editor = dialog.content
    editor.setFocus()
    qtbot.waitUntil(editor.hasFocus)
    editor.selectAll()
    for index, char in enumerate('Hello world', 1):
        qtbot.keyClicks(editor, char)
        assert editor.toPlainText() == 'Hello world'[:index]
        assert editor.textCursor().position() == index
        assert editor.hasFocus()
    cursor = editor.textCursor()
    cursor.setPosition(6)
    editor.setTextCursor(cursor)
    QApplication.clipboard().setText('new ')
    qtbot.keyClick(editor, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert editor.toPlainText() == 'Hello new world'
    assert editor.textCursor().position() == 10
    dialog.accept()
    text, style = dialog.result_values()
    plan = tab.session.plan_text_edit(tab.doc.page_key(0), run, text, style)
    tab.session.commit(plan)
    quiet(qtbot, tab)
    output = tmp_path/'native-edited.pdf'
    save_as(qtbot, window, tab, output, monkeypatch)
    assert 'Hello new world' in text_of_file(output)
