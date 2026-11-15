"""Phase 2 GUI tests (pytest-qt, headless). Mouse input is synthesized from PDF coordinates through the
same transform the application uses. NOT executed by the author (no Qt in the authoring sandbox)."""
import io

import pikepdf
import pypdfium2 as pdfium
import pytest
from PIL import Image
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QKeySequence, QMouseEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from n1cepdf.edit.objects import Comment, ImageBox, Ink, Markup, Shape, TextBox
from n1cepdf.edit.session import default_style
from n1cepdf.ui.edit_tools import MARKUP_TOOLS, TOOLS
from tests.conftest import png_bytes, sha
from tests.gui.test_gui import open_and_wait

T = 20000
LEFT, NOBTN, NOMOD = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier


# ------------------------------------------------------------------ helpers
def view_pos(tab, page, x, y) -> QPoint:
    """PDF point -> viewport pixel, through the application's own transform."""
    tr = tab.controller.overlay.transform(page)
    px, py = tr.to_px(x, y)
    o = tab.view.page_item(page).pos()
    return tab.view.mapFromScene(QPointF(o.x() + px, o.y() + py))


def send(tab, kind, pos: QPoint, mods=NOMOD):
    t = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove,
         "release": QEvent.Type.MouseButtonRelease}[kind]
    button = LEFT if kind != "move" else NOBTN
    buttons = LEFT if kind != "release" else NOBTN
    ev = QMouseEvent(t, QPointF(pos), QPointF(tab.view.viewport().mapToGlobal(pos)), button, buttons, mods)
    QApplication.sendEvent(tab.view.viewport(), ev)


def drag(tab, p0: QPoint, p1: QPoint, steps=5, mods=NOMOD):
    send(tab, "press", p0, mods)
    for i in range(1, steps + 1):
        send(tab, "move", QPoint(p0.x() + (p1.x() - p0.x()) * i // steps, p0.y() + (p1.y() - p0.y()) * i // steps), mods)
    send(tab, "release", p1, mods)


def tool(window, tid):
    window.act["tool_" + tid].trigger()


def prep(qtbot, window, path):
    tab = open_and_wait(qtbot, window, path)
    tab.view.set_zoom(0.5)                      # whole page visible, deterministic mapping
    tab.view.go_to_page(0)
    qtbot.waitUntil(lambda: not tab.view._pix[0].pixmap().isNull(), timeout=T)
    qtbot.wait(100)
    return tab


def quiet(qtbot, tab):
    qtbot.waitUntil(lambda: not tab.reloading and not tab.busy, timeout=T)


def text_of_file(path, page=0):
    d = pdfium.PdfDocument(str(path))
    try:
        return d[page].get_textpage().get_text_range()
    finally:
        d.close()


def save_as(qtbot, window, tab, out, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    window.act["save_as"].trigger()
    qtbot.waitUntil(lambda: not tab.doc.modified and out.exists(), timeout=T)


def close_saved_tab(qtbot, window, tab):
    """After Save As the tab is bound to the NEW file, and open_path() on a file that is already open just
    focuses its tab (one tab per file, by design). To prove the bytes on disk really re-open, close the clean
    tab first. (Before this helper, two tests waited forever for a second tab that is never created.)"""
    quiet(qtbot, tab)
    assert not tab.doc.modified
    assert window.close_tab(window.tabs.indexOf(tab)) is True
    assert window.tabs.count() == 0


def layer_objs(tab):
    return tab.session.layer.all()


# ------------------------------------------------------------------ 2A: toolbar, tools, shortcuts
def test_editing_toolbar_loads_and_is_exclusive(qtbot, window, sample3):
    tab = prep(qtbot, window, sample3)
    assert len(TOOLS) == 13 and {t.id for t in TOOLS} >= {
        "select", "edit_text", "add_text", "edit_images", "add_image", "highlight", "underline", "strike",
        "draw", "shapes", "comment", "erase"}
    for t in TOOLS:
        assert window.act["tool_" + t.id] in window.edit_toolbar.actions()
    for t in TOOLS:
        tool(window, t.id)
        assert tab.controller.tool == t.id
        assert [a.isChecked() for a in window.tool_group.actions()].count(True) == 1
        assert window.act["tool_" + t.id].isChecked()


def test_cursor_and_properties_follow_the_tool(qtbot, window, sample3):
    tab = prep(qtbot, window, sample3)
    tool(window, "shapes")
    assert tab.view.viewport().cursor().shape() == Qt.CursorShape.CrossCursor
    assert tab.panel.stack.currentWidget() is tab.panel.page_tool
    assert tab.panel.title.text() == "Shapes" and tab.panel.opt_shape.isVisibleTo(tab.panel)
    tool(window, "add_text")
    assert tab.panel.opt_family.isVisibleTo(tab.panel) and not tab.panel.opt_shape.isVisibleTo(tab.panel)
    tool(window, "browse")
    assert tab.view.viewport().cursor().shape() == Qt.CursorShape.ArrowCursor


def test_escape_cancels_drag_then_selection_then_tool(qtbot, window, sample3):
    tab = prep(qtbot, window, sample3)
    tool(window, "shapes")
    send(tab, "press", view_pos(tab, 0, 100, 300))
    send(tab, "move", view_pos(tab, 0, 200, 360))
    assert tab.controller._drag is not None
    window.act["cancel_edit"].trigger()
    assert tab.controller._drag is None
    send(tab, "release", view_pos(tab, 0, 200, 360))
    assert layer_objs(tab) == []                                    # the aborted shape was never created
    tab.session.add_object(Shape(tab.doc.page_key(0), "rect", 100, 300, 200, 360))
    tool(window, "select")
    tab.controller.select(layer_objs(tab)[0])
    window.act["cancel_edit"].trigger()
    assert tab.controller.selected_id is None and tab.controller.tool == "select"
    window.act["cancel_edit"].trigger()
    assert tab.controller.tool == "browse"


def test_undo_redo_shortcuts_and_page_rotation_undo(qtbot, window, sample3):
    tab = prep(qtbot, window, sample3)
    assert window.act["undo"].shortcut() == QKeySequence("Ctrl+Z")
    assert {s.toString() for s in window.act["redo"].shortcuts()} == {"Ctrl+Y", "Ctrl+Shift+Z"}
    assert not window.act["undo"].isEnabled()
    window.act["rotate"].trigger()
    quiet(qtbot, tab)
    assert tab.doc.page_rotation(0) == 90 and tab.doc.modified and window.act["undo"].isEnabled()
    window.act["undo"].trigger()
    quiet(qtbot, tab)
    assert tab.doc.page_rotation(0) == 0 and not tab.doc.modified          # back to the clean state
    assert window.act["redo"].isEnabled()
    window.act["redo"].trigger()
    quiet(qtbot, tab)
    assert tab.doc.page_rotation(0) == 90 and tab.doc.modified


# ------------------------------------------------------------------ 2B: text
def test_add_text_click_properties_and_save_roundtrip(qtbot, window, sample3, tmp_path, monkeypatch):
    tab = prep(qtbot, window, sample3)
    tool(window, "add_text")
    qtbot.mouseClick(tab.view.viewport(), LEFT, pos=view_pos(tab, 0, 150, 500))
    qtbot.waitUntil(lambda: len(layer_objs(tab)) == 1, timeout=T)
    box = layer_objs(tab)[0]
    assert isinstance(box, TextBox) and tab.controller.selected_id == box.id
    assert tab.panel.stack.currentWidget() is tab.panel.page_text            # properties panel switched
    tab.panel.tx_content.setPlainText("Привет, Țară ăâîșț\nsecond line")     # typing in the panel edits the object
    assert layer_objs(tab)[0].text.startswith("Привет")
    tab.panel.tx_size.setValue(20.0)
    assert layer_objs(tab)[0].size == 20.0
    out = tmp_path / "text.pdf"
    save_as(qtbot, window, tab, out, monkeypatch)
    saved = text_of_file(out)
    assert "Привет, Țară ăâîșț" in saved and "second line" in saved and "alpha page" in saved


def test_edit_existing_text_john_to_michael_and_save(qtbot, window, rich, tmp_path, monkeypatch):
    before = sha(rich)
    tab = prep(qtbot, window, rich)
    monkeypatch.setattr(tab.controller, "_ask_text_edit", lambda run: ("Hello, my name is Michael.", default_style(run)))
    monkeypatch.setattr(tab.controller, "_confirm_plan", lambda plan: True)
    tool(window, "edit_text")
    qtbot.mouseClick(tab.view.viewport(), LEFT, pos=view_pos(tab, 0, 80, 705))
    qtbot.waitUntil(lambda: tab.session.stack.can_undo, timeout=T)
    quiet(qtbot, tab)
    out = tmp_path / "michael.pdf"
    save_as(qtbot, window, tab, out, monkeypatch)
    saved = text_of_file(out)
    assert "Michael" in saved and "John" not in saved and "Second line stays." in saved
    assert b"John" not in out.read_bytes() and sha(rich) == before           # old text really gone; original intact
    window.act["undo"].trigger()
    quiet(qtbot, tab)
    assert "John" in pdfium.PdfDocument(tab.doc.snapshot_bytes())[0].get_textpage().get_text_range()


def test_edit_existing_text_unicode_replacement_shows_warnings(qtbot, window, rich, tmp_path, monkeypatch):
    tab = prep(qtbot, window, rich)
    seen = []
    monkeypatch.setattr(tab.controller, "_ask_text_edit",
                        lambda run: ("Привет, меня зовут Ион. Țară", default_style(run)))
    monkeypatch.setattr(tab.controller, "_confirm_plan", lambda plan: seen.append(plan) or True)
    tool(window, "edit_text")
    qtbot.mouseClick(tab.view.viewport(), LEFT, pos=view_pos(tab, 0, 80, 705))
    qtbot.waitUntil(lambda: tab.session.stack.can_undo, timeout=T)
    quiet(qtbot, tab)
    assert seen and seen[0].mode == "replace" and any("not reused" in w for w in seen[0].warnings)
    out = tmp_path / "ru.pdf"
    save_as(qtbot, window, tab, out, monkeypatch)
    saved = text_of_file(out)
    assert "Привет" in saved and "Țară" in saved and "John" not in saved and b"John" not in out.read_bytes()


def test_declined_plan_changes_nothing(qtbot, window, rich, monkeypatch):
    tab = prep(qtbot, window, rich)
    monkeypatch.setattr(tab.controller, "_ask_text_edit", lambda run: ("Привет", default_style(run)))
    monkeypatch.setattr(tab.controller, "_confirm_plan", lambda plan: False)
    tool(window, "edit_text")
    qtbot.mouseClick(tab.view.viewport(), LEFT, pos=view_pos(tab, 0, 80, 705))
    qtbot.waitUntil(lambda: not tab.controller._planning and tab.controller.existing is not None, timeout=T)
    qtbot.wait(300)
    assert not tab.doc.modified and not tab.session.stack.can_undo


def test_unsupported_text_is_reported_not_faked(qtbot, window, rich, monkeypatch):
    tab = prep(qtbot, window, rich)
    said = []
    tab.controller.status.connect(said.append)
    tool(window, "edit_text")
    qtbot.mouseClick(tab.view.viewport(), LEFT, pos=view_pos(tab, 0, 500, 100))     # empty area
    qtbot.waitUntil(lambda: any("No editable text" in s or "form XObject" in s for s in said), timeout=T)
    assert not tab.doc.modified


# ------------------------------------------------------------------ 2C: images, shapes, drawing
def _png_file(tmp_path, size=(80, 40), color=(255, 0, 0, 255)):
    p = tmp_path / "pic.png"
    Image.new("RGBA", size, color).save(p)
    return str(p)


def test_add_image_select_resize_and_aspect_lock(qtbot, window, sample3, tmp_path, monkeypatch):
    tab = prep(qtbot, window, sample3)
    monkeypatch.setattr(tab.controller, "_ask_image_path", lambda: _png_file(tmp_path))
    tool(window, "add_image")
    qtbot.mouseClick(tab.view.viewport(), LEFT, pos=view_pos(tab, 0, 100, 500))
    qtbot.waitUntil(lambda: len(layer_objs(tab)) == 1, timeout=T)
    img = layer_objs(tab)[0]
    assert isinstance(img, ImageBox) and tab.panel.stack.currentWidget() is tab.panel.page_image
    ratio = (img.x1 - img.x0) / (img.y1 - img.y0)
    tab.panel.im_geom[2].setValue(200.0)                                     # width spin, aspect locked
    o = layer_objs(tab)[0]
    assert (o.x1 - o.x0) == pytest.approx(200.0, abs=0.2) and (o.x1 - o.x0) / (o.y1 - o.y0) == pytest.approx(ratio, abs=0.01)
    tab.panel.im_lock.setChecked(False)
    tab.panel.im_geom[3].setValue(30.0)                                      # height only, now unlocked
    o = layer_objs(tab)[0]
    assert (o.y1 - o.y0) == pytest.approx(30.0, abs=0.2) and (o.x1 - o.x0) == pytest.approx(200.0, abs=0.2)
    tab.panel.im_rotation.setValue(30.0)
    assert layer_objs(tab)[0].rotation == 30.0
    # resizing with a handle drag (rotation 0 required for handles)
    tab.panel.im_rotation.setValue(0.0)
    o = layer_objs(tab)[0]
    tool(window, "select")
    tab.controller.select(o)
    x0, y0, x1, y1 = o.x0, o.y0, o.x1, o.y1
    drag(tab, view_pos(tab, 0, x1, y0), view_pos(tab, 0, x1 + 40, y0 - 20))   # bottom-right handle
    n = layer_objs(tab)[0]
    assert (n.x1 - n.x0) > (x1 - x0) + 20 and (n.y1 - n.y0) > (y1 - y0)
    window.act["undo"].trigger()
    assert layer_objs(tab)[0].x1 == pytest.approx(x1)


def test_select_move_shape_undo_redo(qtbot, window, sample3):
    tab = prep(qtbot, window, sample3)
    tool(window, "shapes")
    tab.controller.options.shape = "rect"
    drag(tab, view_pos(tab, 0, 100, 300), view_pos(tab, 0, 200, 360))
    qtbot.waitUntil(lambda: len(layer_objs(tab)) == 1, timeout=T)
    s = layer_objs(tab)[0]
    assert isinstance(s, Shape) and abs((s.x1 - s.x0) - 100) < 3 and abs((s.y1 - s.y0) - 60) < 3
    tool(window, "select")
    tab.controller.deselect()
    dx_px = 60
    drag(tab, view_pos(tab, 0, 150, 330), QPoint(view_pos(tab, 0, 150, 330).x() + dx_px, view_pos(tab, 0, 150, 330).y()))
    m = layer_objs(tab)[0]
    expect = dx_px / (tab.view.zoom * 96 / 72)
    assert (m.x0 - s.x0) == pytest.approx(expect, abs=0.8) and m.y0 == pytest.approx(s.y0, abs=0.8)
    window.act["undo"].trigger()
    assert layer_objs(tab)[0].x0 == pytest.approx(s.x0)
    window.act["redo"].trigger()
    assert layer_objs(tab)[0].x0 == pytest.approx(m.x0)


def test_hit_testing_follows_zoom_and_rotation(qtbot, window, sample3):
    tab = prep(qtbot, window, sample3)
    tab.session.add_object(Shape(tab.doc.page_key(0), "rect", 200, 300, 300, 400, fill=(0, 1, 0)))
    tool(window, "select")
    for zoom in (0.5, 0.75):
        tab.view.set_zoom(zoom)
        qtbot.wait(150)
        tab.controller.deselect()
        qtbot.mouseClick(tab.view.viewport(), LEFT, pos=view_pos(tab, 0, 250, 350))
        assert tab.controller.selected_id == layer_objs(tab)[0].id
    window.act["rotate"].trigger()                                          # page rotation changes the mapping
    quiet(qtbot, tab)
    tab.view.set_zoom(0.5)
    qtbot.wait(150)
    tab.controller.deselect()
    qtbot.mouseClick(tab.view.viewport(), LEFT, pos=view_pos(tab, 0, 250, 350))
    assert tab.controller.selected_id == layer_objs(tab)[0].id
    tab.controller.deselect()
    qtbot.mouseClick(tab.view.viewport(), LEFT, pos=view_pos(tab, 0, 50, 50))     # outside the shape
    assert tab.controller.selected_id is None


def test_properties_panel_updates_with_selection(qtbot, window, sample3):
    tab = prep(qtbot, window, sample3)
    key = tab.doc.page_key(0)
    s = Shape(key, "ellipse", 100, 100, 200, 160)
    t = TextBox(key, 72, 500, 250, 520, "hello")
    tab.session.add_object(s)
    tab.session.add_object(t)
    tab.controller.select(s)
    p = tab.panel
    assert p.stack.currentWidget() is p.page_shape and p.sh_geom[2].value() == pytest.approx(100.0)
    p.sh_stroke.color_changed.emit((0.0, 1.0, 0.0))
    p.sh_width.setValue(5.0)
    p.sh_opacity.setValue(50)
    n = layer_objs(tab)[0]
    assert n.stroke == (0.0, 1.0, 0.0) and n.width == 5.0 and n.opacity == pytest.approx(0.5)
    tab.controller.select(t)
    assert p.stack.currentWidget() is p.page_text and p.tx_content.toPlainText() == "hello"
    tab.controller.deselect()
    assert p.stack.currentWidget() is p.page_tool
    tab.controller.select(layer_objs(tab)[0])
    window.act["undo"].trigger()                                             # property edits are undoable, one field at a time
    assert layer_objs(tab)[0].opacity == pytest.approx(1.0) and layer_objs(tab)[0].width == 5.0


def test_existing_image_select_move_and_delete(qtbot, window, rich, monkeypatch):
    tab = prep(qtbot, window, rich)
    monkeypatch.setattr(tab.controller, "_confirm_plan", lambda plan: True)
    tool(window, "edit_images")
    qtbot.mouseClick(tab.view.viewport(), LEFT, pos=view_pos(tab, 0, 130, 440))
    qtbot.waitUntil(lambda: tab.controller.existing is not None, timeout=T)
    assert tab.panel.stack.currentWidget() is tab.panel.page_ex_image
    tab.panel.ei_geom[0].setValue(150.0)
    tab.panel.ei_geom[1].setValue(300.0)
    tab.controller.on_action("apply_image")
    qtbot.waitUntil(lambda: tab.session.stack.can_undo, timeout=T)
    quiet(qtbot, tab)
    img = next(o for o in tab.session.inspect_page(0).objects if o.kind == "image")
    assert [round(v) for v in img.bbox[:2]] == [150, 300] and round(img.bbox[2] - img.bbox[0]) == 120
    done = tab.session.stack.index
    tool(window, "erase")
    qtbot.mouseClick(tab.view.viewport(), LEFT, pos=view_pos(tab, 0, 200, 340))          # on the moved image
    qtbot.waitUntil(lambda: tab.session.stack.index > done, timeout=T)
    quiet(qtbot, tab)
    assert not any(o.kind == "image" for o in tab.session.inspect_page(0).objects)
    assert "Hello" in pdfium.PdfDocument(tab.doc.snapshot_bytes())[0].get_textpage().get_text_range()


# ------------------------------------------------------------------ 2D: annotations, persistence, integrity
def test_highlight_by_dragging_over_text_persists(qtbot, window, sample3, tmp_path, monkeypatch):
    tab = prep(qtbot, window, sample3)
    tool(window, "highlight")
    drag(tab, view_pos(tab, 0, 73, 705), view_pos(tab, 0, 130, 705))
    qtbot.waitUntil(lambda: any(isinstance(o, Markup) for o in layer_objs(tab)), timeout=T)
    out = tmp_path / "hl.pdf"
    save_as(qtbot, window, tab, out, monkeypatch)
    with pikepdf.open(out) as pdf:
        assert [str(a.Subtype) for a in pdf.pages[0].Annots] == ["/Highlight"]
    assert "alpha page" in text_of_file(out)


def test_comment_and_ink_tools_and_roundtrip(qtbot, window, sample3, tmp_path, monkeypatch):
    tab = prep(qtbot, window, sample3)
    monkeypatch.setattr(tab.controller, "_ask_comment_text", lambda: "Примечание")
    tool(window, "comment")
    qtbot.mouseClick(tab.view.viewport(), LEFT, pos=view_pos(tab, 0, 400, 700))
    tool(window, "draw")
    drag(tab, view_pos(tab, 0, 100, 200), view_pos(tab, 0, 250, 250), steps=8)
    qtbot.waitUntil(lambda: {type(o) for o in layer_objs(tab)} == {Comment, Ink}, timeout=T)
    out = tmp_path / "ann.pdf"
    save_as(qtbot, window, tab, out, monkeypatch)
    with pikepdf.open(out) as pdf:
        assert sorted(str(a.Subtype) for a in pdf.pages[0].Annots) == ["/Ink", "/Text"]
    close_saved_tab(qtbot, window, tab)
    reopened = open_and_wait(qtbot, window, out)
    assert reopened.doc.page_count == 3


def test_delete_key_action_and_erase_tool_remove_added_objects(qtbot, window, sample3):
    tab = prep(qtbot, window, sample3)
    key = tab.doc.page_key(0)
    tab.session.add_object(Shape(key, "rect", 100, 300, 200, 360))
    tab.session.add_object(Shape(key, "ellipse", 300, 300, 400, 360))
    tool(window, "select")
    tab.controller.select(layer_objs(tab)[0])
    assert window.act["delete_selected"].shortcut() == QKeySequence(QKeySequence.StandardKey.Delete)
    window.act["delete_selected"].trigger()
    assert len(layer_objs(tab)) == 1
    tool(window, "erase")
    qtbot.mouseClick(tab.view.viewport(), LEFT, pos=view_pos(tab, 0, 350, 330))
    assert layer_objs(tab) == []
    window.act["undo"].trigger()
    window.act["undo"].trigger()
    assert len(layer_objs(tab)) == 2


def test_copy_paste_added_object(qtbot, window, sample3):
    tab = prep(qtbot, window, sample3)
    tab.session.add_object(Shape(tab.doc.page_key(0), "rect", 100, 300, 200, 360))
    tab.controller.select(layer_objs(tab)[0])
    window.act["copy"].trigger()
    window.act["paste"].trigger()
    objs = layer_objs(tab)
    assert len(objs) == 2 and objs[0].id != objs[1].id and objs[1].x0 == pytest.approx(objs[0].x0 + 12)


def test_all_object_types_save_and_reopen_without_rasterizing(qtbot, window, sample3, tmp_path, monkeypatch):
    before = sha(sample3)
    tab = prep(qtbot, window, sample3)
    key = tab.doc.page_key(0)
    s = tab.session
    s.add_object(TextBox(key, 72, 500, 300, 540, "Persisted Ünï text", "Liberation Sans", 14))
    s.add_object(ImageBox(key, 72, 300, 172, 400, png_bytes()))
    s.add_object(Shape(key, "arrow", 100, 250, 300, 200))
    s.add_object(Markup(key, "underline", ((72, 696, 200, 713),), (0, 0, 1), 1.0))
    s.add_object(Comment(key, 400, 700, "note"))
    s.add_object(Ink(key, (((100, 100), (150, 140)),)))
    out = tmp_path / "all.pdf"
    save_as(qtbot, window, tab, out, monkeypatch)
    assert sha(sample3) == before
    close_saved_tab(qtbot, window, tab)
    reopened = open_and_wait(qtbot, window, out)
    assert reopened.doc.page_count == 3 and not reopened.doc.modified
    saved = text_of_file(out)
    assert "Persisted Ünï text" in saved and "alpha page" in saved
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages[0].Annots) == 3
        for page in pdf.pages:
            for xo in (page.get("/Resources", {}).get("/XObject", {}) or {}).values():
                assert str(xo.get("/Subtype")) != "/Image"


def test_opening_an_already_open_file_focuses_the_existing_tab(qtbot, window, sample3, tmp_path, monkeypatch):
    tab = prep(qtbot, window, sample3)
    window.open_path(str(sample3))                       # same file again -> no second tab
    qtbot.wait(300)
    assert window.tabs.count() == 1 and window.cur() is tab
    out = tmp_path / "copy.pdf"
    save_as(qtbot, window, tab, out, monkeypatch)        # the tab is now bound to copy.pdf
    quiet(qtbot, tab)
    window.open_path(str(out))
    qtbot.wait(300)
    assert window.tabs.count() == 1                      # still one tab: this is why reopening needs a close first
    window.open_path(str(sample3))                       # the original path is no longer open -> opens normally
    qtbot.waitUntil(lambda: window.tabs.count() == 2, timeout=T)


def test_unsaved_edits_prompt_on_close_save_discard_cancel(qtbot, window, sample3, tmp_path, monkeypatch):
    tab = prep(qtbot, window, sample3)
    tab.session.add_object(TextBox(tab.doc.page_key(0), 72, 500, 200, 520, "unsaved"))
    assert tab.doc.modified and tab.title().startswith("●")
    calls = []

    def answer(a):
        monkeypatch.setattr(type(window), "_confirm_discard", lambda self, name: calls.append(name) or a)

    answer("cancel")
    assert window.close_tab(0) is False and window.tabs.count() == 1
    answer("save")
    monkeypatch.setattr(type(window), "_confirm_overwrite", lambda self, name: "overwrite")
    assert window.close_tab(0) is True
    assert "unsaved" in text_of_file(sample3)                                 # Save wrote the edit
    assert calls == ["three.pdf", "three.pdf"]


def test_undo_back_to_saved_state_is_clean_and_discard_leaves_file(qtbot, window, sample3, monkeypatch):
    before = sha(sample3)
    tab = prep(qtbot, window, sample3)
    tab.session.add_object(Shape(tab.doc.page_key(0), "rect", 1, 1, 50, 50))
    assert tab.doc.modified
    window.act["undo"].trigger()
    assert not tab.doc.modified                                               # no prompt needed for a clean document
    assert window.close_tab(0) is True and sha(sample3) == before


def test_two_documents_edit_independently(qtbot, window, sample3, rich, tmp_path, monkeypatch):
    t1 = prep(qtbot, window, sample3)
    t2 = prep(qtbot, window, rich)
    t1.session.add_object(Shape(t1.doc.page_key(0), "rect", 1, 1, 50, 50))
    assert t1.doc.modified and not t2.doc.modified
    assert len(layer_objs(t1)) == 1 and layer_objs(t2) == []
    window.tabs.setCurrentWidget(t2)
    tool(window, "shapes")
    assert t2.controller.tool == "shapes" and t1.controller.tool == "browse"
    window.tabs.setCurrentWidget(t1)
    assert window.act["tool_browse"].isChecked() and window.act["undo"].isEnabled()
    window.tabs.setCurrentWidget(t2)
    assert not window.act["undo"].isEnabled()
    t2.session.add_object(Shape(t2.doc.page_key(0), "rect", 5, 5, 60, 60))
    t1.session.undo()
    assert len(layer_objs(t2)) == 1 and layer_objs(t1) == []


def test_edit_is_ignored_while_view_is_reloading(qtbot, window, sample3):
    tab = prep(qtbot, window, sample3)
    tool(window, "shapes")
    tab._reloading = True                                                     # simulate the async reload window
    send(tab, "press", view_pos(tab, 0, 100, 300))
    assert tab.controller._drag is None
    tab._reloading = False
