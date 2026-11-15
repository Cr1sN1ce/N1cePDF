"""pytest-qt tests. Require PySide6 + pytest-qt; run headless via QT_QPA_PLATFORM=offscreen."""
import pikepdf
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from tests.conftest import make_pdf

T = 15000


def open_and_wait(qtbot, w, path, count=None):
    before = w.tabs.count()
    w.open_path(str(path))
    qtbot.waitUntil(lambda: w.tabs.count() == (count or before + 1), timeout=T)
    return w.cur()


@pytest.fixture
def ten(tmp_path):
    return make_pdf(tmp_path / "ten.pdf", [f"page number {i + 1}" for i in range(10)], outline=True)


def test_main_window_opens(window):
    assert window.isVisible()
    assert window.windowTitle() == "N1cePDF"


def test_welcome_visible_without_document(window):
    assert window.stack.currentWidget() is window.welcome
    assert window.welcome.isVisible()
    assert window.welcome.open_button.isVisible() and window.welcome.blank_button.isVisible()


def test_open_button_works(qtbot, window, sample3, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(sample3), ""))
    qtbot.mouseClick(window.welcome.open_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.tabs.count() == 1, timeout=T)
    assert not window.welcome.isVisible()


def test_menu_bar_contains_expected_actions(window):
    assert window.menuBar().isVisible()
    assert not window.menuBar().isNativeMenuBar()
    found = {}
    for top in window.menuBar().actions():
        found[top.text().replace("&", "")] = {a.text().replace("&", "").replace("…", "")
                                              for a in top.menu().actions() if not a.isSeparator()}
    expected = {
        "File": {"Open", "Save", "Save As", "Close Document", "Exit"},
        "Edit": {"Undo", "Redo", "Copy", "Select All"},
        "View": {"Zoom In", "Zoom Out", "Fit Width", "Fit Page", "Actual Size", "Toggle Sidebar"},
        "Document": {"Rotate Page", "Insert Page", "Delete Page", "Extract Pages", "Merge PDFs"},
        "Tools": {"Search", "Document Properties"},
        "Help": {"About N1cePDF"},
    }
    for menu, names in expected.items():
        assert names <= found[menu], (menu, names - found[menu])
    assert not window.act["undo"].isEnabled() and not window.act["redo"].isEnabled()  # Phase 2, not faked


def test_toolbar_actions_exist(window):
    tb = window.toolbar.actions()
    for n in ("open", "save", "undo", "redo", "prev", "page_spin", "page_total", "next", "zoom_out",
              "zoom_combo", "zoom_in", "fit_width", "fit_page", "search"):
        assert window.act[n] in tb, n


def test_opening_pdf_creates_tab(qtbot, window, sample3):
    tab = open_and_wait(qtbot, window, sample3)
    assert window.tabs.tabText(0) == "three.pdf"
    assert tab.page_count == 3
    assert window.stack.currentWidget() is window.tabs
    assert window.act["zoom_in"].isEnabled()


def test_closing_last_tab_restores_welcome(qtbot, window, sample3):
    open_and_wait(qtbot, window, sample3)
    assert window.close_tab(0)
    assert window.tabs.count() == 0
    assert window.stack.currentWidget() is window.welcome and window.welcome.isVisible()
    assert not window.act["zoom_in"].isEnabled()


def test_pages_render(qtbot, window, sample3):
    tab = open_and_wait(qtbot, window, sample3)
    qtbot.waitUntil(lambda: not tab.view._pix[0].pixmap().isNull(), timeout=T)


def test_zoom_controls(qtbot, window, sample3):
    tab = open_and_wait(qtbot, window, sample3)
    tab.view.actual_size()
    assert tab.view.zoom == pytest.approx(1.0)
    window.act["zoom_in"].trigger()
    assert tab.view.zoom == pytest.approx(1.25)
    window.act["zoom_out"].trigger()
    window.act["zoom_out"].trigger()
    assert tab.view.zoom == pytest.approx(0.8)
    window.zoom_combo.setCurrentText("200%")
    window._zoom_from_text()
    assert tab.view.zoom == pytest.approx(2.0)
    assert window.zoom_label.text() == "200%"
    window.act["fit_width"].trigger()
    assert tab.view.fit_mode == "width"
    qtbot.waitUntil(lambda: not tab.view._pix[0].pixmap().isNull(), timeout=T)


def test_sidebar_toggle(qtbot, window, sample3):
    tab = open_and_wait(qtbot, window, sample3)
    assert tab.sidebar.isVisibleTo(tab)
    window.act["sidebar"].trigger()
    assert not tab.sidebar.isVisibleTo(tab)
    window.act["sidebar"].trigger()
    assert tab.sidebar.isVisibleTo(tab)


def test_thumbnails_navigate(qtbot, window, ten):
    tab = open_and_wait(qtbot, window, ten)
    thumbs = tab.sidebar.thumbs
    assert thumbs.count() == 10
    qtbot.waitUntil(lambda: not thumbs.item(0).icon().isNull(), timeout=T)
    thumbs.setCurrentRow(5)
    qtbot.waitUntil(lambda: tab.view.current_page == 5, timeout=T)
    assert window.page_label.text() == "Page 6 / 10"


def test_bookmarks_listed_and_navigate(qtbot, window, ten):
    tab = open_and_wait(qtbot, window, ten)
    tree = tab.sidebar.bookmarks.tree
    qtbot.waitUntil(lambda: tree.topLevelItemCount() > 0, timeout=T)
    first = tree.topLevelItem(0)
    assert first.text(0) == "Chapter 1" and first.childCount() == 1
    tab.sidebar.bookmarks._clicked(first.child(0))
    qtbot.waitUntil(lambda: tab.view.current_page == 1, timeout=T)


def test_search_lists_results_and_navigates(qtbot, window, ten):
    tab = open_and_wait(qtbot, window, ten)
    tab.start_search("number 7")
    qtbot.waitUntil(lambda: tab.sidebar.search.results.count() == 1, timeout=T)
    assert tab.sidebar.search.results.item(0).text().startswith("p. 7")
    qtbot.waitUntil(lambda: tab.view.current_page == 6, timeout=T)


def test_invalid_pdf_shows_friendly_error(qtbot, window, tmp_path, monkeypatch):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.7 definitely not a pdf " * 5)
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]) or QMessageBox.StandardButton.Ok)
    window.open_path(str(bad))
    qtbot.waitUntil(lambda: bool(shown), timeout=T)
    assert "bad.pdf" in shown[0] and "Traceback" not in shown[0]
    assert window.tabs.count() == 0 and window.welcome.isVisible()


def test_password_dialog_and_wrong_then_right(qtbot, window, encrypted, monkeypatch):
    answers = iter([("wrong", True), ("secret", True)])
    calls = []

    def fake(*a, **k):
        calls.append(a[2])
        return next(answers)

    monkeypatch.setattr(QInputDialog, "getText", fake)
    window.open_path(str(encrypted))
    qtbot.waitUntil(lambda: window.tabs.count() == 1, timeout=T)
    assert len(calls) == 2 and "Incorrect" in calls[1]


def test_password_dialog_cancel_opens_nothing(qtbot, window, encrypted, monkeypatch):
    calls = []
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: calls.append(1) or ("", False))
    window.open_path(str(encrypted))
    qtbot.waitUntil(lambda: bool(calls), timeout=T)
    qtbot.wait(200)
    assert window.tabs.count() == 0


def test_multiple_tabs_independent_state(qtbot, window, sample3, ten):
    t1 = open_and_wait(qtbot, window, sample3)
    t2 = open_and_wait(qtbot, window, ten)
    assert window.tabs.count() == 2 and window.cur() is t2
    t1.view.set_zoom(2.0)
    t2.view.set_zoom(0.5)
    window.tabs.setCurrentWidget(t1)
    assert window.zoom_label.text() == "200%"
    window.tabs.setCurrentWidget(t2)
    assert window.zoom_label.text() == "50%"
    assert window.close_tab(0) and window.tabs.count() == 1


def test_rotate_marks_modified_and_refreshes(qtbot, window, sample3, monkeypatch):
    tab = open_and_wait(qtbot, window, sample3)
    w0, h0 = tab.renderer.page_size(0)
    window.act["rotate"].trigger()
    qtbot.waitUntil(lambda: tab.renderer.page_size(0)[0] > w0, timeout=T)
    assert tab.doc.modified and tab.title().startswith("●")
    # closing a modified document asks first; cancelling keeps it open
    monkeypatch.setattr(type(window), "_confirm_discard", lambda self, name: "cancel")
    assert not window.close_tab(0) and window.tabs.count() == 1
    monkeypatch.setattr(type(window), "_confirm_discard", lambda self, name: "discard")
    assert window.close_tab(0) and window.tabs.count() == 0


def test_insert_and_delete_page(qtbot, window, sample3, monkeypatch):
    tab = open_and_wait(qtbot, window, sample3)
    window.act["insert"].trigger()
    qtbot.waitUntil(lambda: tab.view.page_count == 4, timeout=T)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    qtbot.waitUntil(lambda: not tab._reloading, timeout=T)
    window.act["delete"].trigger()
    qtbot.waitUntil(lambda: tab.view.page_count == 3, timeout=T)


def test_save_as_copy_leaves_original(qtbot, window, sample3, tmp_path, monkeypatch):
    from tests.conftest import sha
    before = sha(sample3)
    tab = open_and_wait(qtbot, window, sample3)
    window.act["rotate"].trigger()
    qtbot.waitUntil(lambda: tab.doc.modified and not tab._reloading, timeout=T)
    out = tmp_path / "copy.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    window.act["save_as"].trigger()
    qtbot.waitUntil(lambda: not tab.doc.modified, timeout=T)
    assert out.is_file() and sha(sample3) == before
    assert len(pikepdf.open(out).pages) == 3


def test_select_all_and_copy(qtbot, window, sample3):
    tab = open_and_wait(qtbot, window, sample3)
    QGuiApplication.clipboard().clear()
    window.act["select_all"].trigger()
    window.act["copy"].trigger()
    qtbot.waitUntil(lambda: "alpha page" in QGuiApplication.clipboard().text(), timeout=T)
    assert "gamma page" in QGuiApplication.clipboard().text()


def test_create_blank_pdf(qtbot, window):
    window.welcome.blank_button.click()
    assert window.tabs.count() == 1 and window.cur().doc.page_count == 1


def test_recent_documents_listed(qtbot, window, sample3):
    open_and_wait(qtbot, window, sample3)
    window.close_tab(0)
    assert window.welcome.recent_list.count() == 1


# ---- regression: search invalidation after document modification -----------------------------
def _search_ready(qtbot, tab, query="number"):
    tab.sidebar.search.input.setText(query)
    tab.start_search(query)
    qtbot.waitUntil(lambda: tab.sidebar.search.results.count() == 10, timeout=T)
    assert tab.view._hits and tab.view._flat


@pytest.mark.parametrize("op", ["rotate", "insert", "delete"])
def test_search_cleared_when_document_modified(qtbot, window, ten, monkeypatch, op):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    tab = open_and_wait(qtbot, window, ten)
    _search_ready(qtbot, tab)
    window.act[op].trigger()
    # reset happens synchronously, before the asynchronous view reload finishes
    assert tab.sidebar.search.results.count() == 0
    assert tab.view._hits == [] and tab.view._flat == []
    assert "changed" in tab.sidebar.search.status.text()
    qtbot.waitUntil(lambda: not tab._reloading, timeout=T)
    assert tab.sidebar.search.results.count() == 0 and tab.view._hits == []
    assert tab.sidebar.search.input.text() == "number"  # typed query is kept


def test_inflight_search_is_discarded_after_edit(qtbot, window, ten):
    tab = open_and_wait(qtbot, window, ten)
    tab.start_search("number")           # results not delivered yet
    window.act["rotate"].trigger()       # edit before they arrive
    qtbot.waitUntil(lambda: not tab._reloading, timeout=T)
    qtbot.wait(300)
    assert tab.sidebar.search.results.count() == 0 and tab.view._hits == []


def test_stale_search_result_is_ignored(qtbot, window, ten):
    tab = open_and_wait(qtbot, window, ten)
    hits = tab.renderer.search("number")
    old_rev = tab.doc.revision
    tab.doc.mark_modified()              # document state moved on
    tab._on_search_done(hits, tab.renderer, old_rev)
    assert tab.sidebar.search.results.count() == 0 and tab.view._hits == []


def test_clearing_search_text_resets_results_and_highlights(qtbot, window, ten):
    tab = open_and_wait(qtbot, window, ten)
    _search_ready(qtbot, tab)
    tab.sidebar.search.input.clear()
    assert tab.sidebar.search.results.count() == 0 and tab.view._hits == [] and tab.view._flat == []


def test_search_works_again_after_edit(qtbot, window, ten):
    tab = open_and_wait(qtbot, window, ten)
    window.act["rotate"].trigger()
    qtbot.waitUntil(lambda: not tab._reloading, timeout=T)
    _search_ready(qtbot, tab)


# ---- regression: stale callbacks after tab closure -------------------------------------------
def test_close_tab_with_search_and_copy_in_flight(qtbot, window, ten):
    tab = open_and_wait(qtbot, window, ten)
    window.act["select_all"].trigger()
    window.act["copy"].trigger()
    tab.start_search("number")
    assert window.close_tab(0)
    qtbot.wait(400)                      # late callbacks must be dropped silently (pytest-qt fails on any)
    assert window.tabs.count() == 0


def test_close_tab_while_reload_in_flight(qtbot, window, ten):
    tab = open_and_wait(qtbot, window, ten)
    window.act["rotate"].trigger()
    window.force_close_all()             # worker may still be snapshotting the document
    qtbot.wait(500)
    assert window.tabs.count() == 0 and window.welcome.isVisible()


# ---- unsaved-changes confirmation on close: Save / Discard / Cancel ----------------------------
@pytest.fixture
def modified_tab(qtbot, window, sample3):
    tab = open_and_wait(qtbot, window, sample3)
    window.act["rotate"].trigger()
    qtbot.waitUntil(lambda: tab.doc.modified and not tab._reloading, timeout=T)
    return tab


def _answer(monkeypatch, window, answer):
    calls = []

    def fake(self, name):
        calls.append(name)
        return answer

    monkeypatch.setattr(type(window), "_confirm_discard", fake)
    return calls


def test_close_modified_cancel_keeps_document(qtbot, window, modified_tab, sample3, monkeypatch):
    from tests.conftest import sha
    before = sha(sample3)
    calls = _answer(monkeypatch, window, "cancel")
    assert window.close_tab(0) is False
    assert calls == ["three.pdf"]
    assert window.tabs.count() == 1 and modified_tab.doc.modified and not modified_tab.is_closed
    assert sha(sample3) == before


def test_close_modified_discard_closes_without_saving(qtbot, window, modified_tab, sample3, monkeypatch):
    from tests.conftest import sha
    before = sha(sample3)
    calls = _answer(monkeypatch, window, "discard")
    assert window.close_tab(0) is True
    assert calls == ["three.pdf"]
    assert window.tabs.count() == 0 and window.welcome.isVisible() and modified_tab.is_closed
    assert sha(sample3) == before            # nothing was written


def test_close_modified_save_writes_then_closes(qtbot, window, modified_tab, sample3, monkeypatch):
    calls = _answer(monkeypatch, window, "save")
    monkeypatch.setattr(type(window), "_confirm_overwrite", lambda self, name: "overwrite")
    assert window.close_tab(0) is True
    assert calls == ["three.pdf"] and window.tabs.count() == 0
    with pikepdf.open(sample3) as pdf:
        assert int(pdf.pages[0].get("/Rotate", 0)) == 90 and len(pdf.pages) == 3


def test_close_modified_save_declined_overwrite_keeps_tab(qtbot, window, modified_tab, sample3, monkeypatch):
    from tests.conftest import sha
    before = sha(sample3)
    _answer(monkeypatch, window, "save")
    monkeypatch.setattr(type(window), "_confirm_overwrite", lambda self, name: "cancel")
    assert window.close_tab(0) is False
    assert window.tabs.count() == 1 and modified_tab.doc.modified and sha(sample3) == before


def test_close_untitled_save_cancelled_in_file_dialog_keeps_tab(qtbot, window, monkeypatch):
    window.create_blank()
    assert window.cur().doc.modified and window.cur().doc.path is None
    _answer(monkeypatch, window, "save")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    assert window.close_tab(0) is False and window.tabs.count() == 1


def test_close_untitled_save_to_path_then_closes(qtbot, window, tmp_path, monkeypatch):
    window.create_blank()
    out = tmp_path / "new.pdf"
    _answer(monkeypatch, window, "save")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    assert window.close_tab(0) is True and window.tabs.count() == 0
    assert len(pikepdf.open(out).pages) == 1


def test_window_close_cancel_then_discard(qtbot, window, modified_tab, monkeypatch):
    _answer(monkeypatch, window, "cancel")
    assert window.close() is False                       # closeEvent ignored
    assert window.isVisible() and window.tabs.count() == 1
    _answer(monkeypatch, window, "discard")
    assert window.close() is True
    assert not window.isVisible() and window.tabs.count() == 0


def test_unmodified_document_closes_without_any_dialog(qtbot, window, sample3):
    open_and_wait(qtbot, window, sample3)
    assert window.close_tab(0) is True                   # the autouse guard fails the test on any dialog
