import threading
from PySide6.QtWidgets import QProgressDialog, QMessageBox
from n1cepdf.ui.ocr_dialog import OcrDialog
from n1cepdf.core.ocr import OcrLine, OcrCancelled, OcrError
from n1cepdf.edit.objects import TextBox
from tests.gui.test_edit_gui import prep, view_pos, send, quiet
from tests.conftest import make_pdf


def test_ocr_dialog_edit_and_search(qtbot, window, tmp_path, monkeypatch):
    tab = prep(qtbot, window, make_pdf(tmp_path/'source.pdf', ['Native']))
    def fake(data, pages, language, cancel, progress, **kwargs):
        progress(1, 1)
        return [OcrLine(0, 'Recognized line', (72, 600, 250, 620), 18)]
    monkeypatch.setattr('n1cepdf.ui.ocr_dialog.recognize_pdf', fake)
    dialog = OcrDialog(tab)
    dialog.start()
    quiet(qtbot, tab)
    box, = tab.session.layer.all()
    assert box.text == 'Recognized line'
    assert tab.controller.tool == 'edit_text'
    qtbot.waitUntil(lambda: box.id in tab.controller.overlay.entries)
    send(tab, 'press', view_pos(tab, 0, 100, 610))
    send(tab, 'release', view_pos(tab, 0, 100, 610))
    assert tab.controller.selected_id == box.id
    tab.controller.on_object_changed({'text': 'Changed OCR'})
    assert tab.session.layer.get(box.id).ocr_original is None
    tab.start_search('Changed OCR')
    qtbot.waitUntil(lambda: bool(tab.view._hits))
    assert tab.view._hits[0].snippets == ('Changed OCR',)
    tab.undo()
    assert tab.session.layer.get(box.id).text == 'Recognized line'
    tab.undo()
    assert len(tab.session.layer) == 0


def test_ocr_dialog_cancel(qtbot, window, tmp_path, monkeypatch):
    tab = prep(qtbot, window, make_pdf(tmp_path/'source.pdf', ['Native']))
    started = threading.Event()
    def fake(data, pages, language, cancel, progress, **kwargs):
        started.set()
        while not cancel.is_set():
            cancel.wait(0.01)
        raise OcrCancelled()
    monkeypatch.setattr('n1cepdf.ui.ocr_dialog.recognize_pdf', fake)
    OcrDialog(tab).start()
    qtbot.waitUntil(started.is_set)
    progress = tab.findChild(QProgressDialog)
    assert progress is not None
    progress.canceled.emit()
    quiet(qtbot, tab)
    assert not tab.session.layer.all()
    assert not tab.doc.modified


def test_ocr_dialog_error(qtbot, window, tmp_path, monkeypatch):
    tab = prep(qtbot, window, make_pdf(tmp_path/'source.pdf', ['Native']))
    def fail(*args, **kwargs):
        raise OcrError('Missing language data')
    messages = []
    monkeypatch.setattr('n1cepdf.ui.ocr_dialog.recognize_pdf', fail)
    monkeypatch.setattr(QMessageBox, 'warning', lambda parent, title, message: messages.append(message))
    OcrDialog(tab).start()
    quiet(qtbot, tab)
    assert messages == ['Missing language data']
    assert not tab.doc.modified


def test_ocr_moved_box_keeps_independent_mask(qtbot, window, tmp_path):
    from dataclasses import replace
    from tests.gui.test_edit_gui import drag, tool
    from n1cepdf.ui.edit_controller import object_bounds
    tab = prep(qtbot, window, make_pdf(tmp_path/'source.pdf', ['Native']))
    key = tab.doc.page_key(0)
    rect = (72,500,250,530)
    box = TextBox(key, *rect, text='Move me', size=14, ocr_source=rect,
                  ocr_original='Move me', ocr_initial_rect=rect)
    tab.session.add_object(box)
    tool(window, 'select')
    drag(tab, view_pos(tab,0,120,515), view_pos(tab,0,220,415))
    moved = tab.session.layer.get(box.id)
    assert moved.ocr_source == rect
    assert moved.x0 > box.x0 + 90
    assert object_bounds(moved) == (moved.x0,moved.y0,moved.x1,moved.y1)
    assert tab.controller._hit_layer(key, 80,520) is None
    tab.controller.on_object_changed({'w': 240, 'h': 50})
    resized = tab.session.layer.get(box.id)
    assert resized.x1-resized.x0 == 240
    assert resized.ocr_source == rect
