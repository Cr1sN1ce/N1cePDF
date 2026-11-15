"""Main window: welcome screen <-> tabbed document workspace, menus, toolbar, status bar.

Menu bar note (KDE/Wayland): the menu bar is forced to be in-window (non-native) on Linux and
Windows so it cannot vanish into an absent global-menu applet; empty placeholder menus were removed.
"""
from __future__ import annotations
import logging
import sys
from pathlib import Path
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QIcon, QKeySequence
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog, QFormLayout, QInputDialog,
                               QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar, QSpinBox,
                               QStackedWidget, QTabWidget, QToolBar, QVBoxLayout, QDialogButtonBox)
from .. import __version__
from ..core import page_manager as pm
from ..core.document import PdfDocument
from ..core.errors import (EncryptedSaveRefused, InvalidPdfError, N1cePdfError, PasswordRequired,
                           SaveValidationError, WrongPassword)
from ..core.file_manager import same_file, save_atomic
from ..core.renderer import PageRenderer
from ..core.validation import parse_page_ranges
from ..runtime import install_excepthook, resource_path, set_windows_app_id, setup_logging
from ..services.recent import RecentFiles
from .document_tab import DocumentTab
from .edit_tools import TOOLS
from .icons import make_icon
from .tasks import TaskRunner
from .theme import apply_theme
from .welcome import WelcomeWidget

log = logging.getLogger(__name__)
ZOOM_PRESETS = [50, 75, 100, 125, 150, 200, 300, 400]


class MainWindow(QMainWindow):
    def __init__(self, recent: RecentFiles | None = None):
        super().__init__()
        self.setWindowTitle("N1cePDF")
        self.resize(1280, 820)
        self.setAcceptDrops(True)
        self.runner = TaskRunner(self)
        self.recent = recent or RecentFiles()
        self._sidebar_visible = True
        self._busy = 0
        self._closing = False
        self.act: dict[str, QAction] = {}

        self.stack = QStackedWidget()
        self.welcome = WelcomeWidget(self.recent)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.stack.addWidget(self.welcome)
        self.stack.addWidget(self.tabs)
        self.setCentralWidget(self.stack)

        self._make_actions()
        self._make_menus()
        self._make_toolbar()
        self._make_statusbar()

        self.welcome.open_requested.connect(self.open_dialog)
        self.welcome.blank_requested.connect(self.create_blank)
        self.welcome.recent_requested.connect(self.open_path)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(lambda _i: self._update_ui())
        self._update_ui()

    # ------------------------------------------------------------------ actions
    def _add(self, name, text, slot, shortcut=None, icon=None, tip=None, checkable=False):
        a = QAction(text, self)
        if icon:
            a.setIcon(make_icon(icon, self.palette().windowText().color().name()))
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.setToolTip(tip or text.replace("&", "").replace("…", ""))
        a.setCheckable(checkable)
        if slot is None:
            a.setEnabled(False)  # not implemented yet: visibly disabled, never fake
        else:
            a.triggered.connect(slot)
        self.act[name] = a
        return a

    def _make_actions(self) -> None:
        K = QKeySequence.StandardKey
        add = self._add
        add("open", "&Open…", self.open_dialog, K.Open, "open", "Open PDF (Ctrl+O)")
        add("save", "&Save", self.save, K.Save, "save", "Save (Ctrl+S)")
        add("save_as", "Save &As…", self.save_as, "Ctrl+Shift+S", None)
        add("close", "&Close Document", self.close_current, K.Close)
        add("exit", "E&xit", self.close, "Ctrl+Q")
        add("undo", "&Undo", lambda: self._with_tab(lambda t: t.undo()), K.Undo, "undo", "Undo (Ctrl+Z)")
        add("redo", "&Redo", lambda: self._with_tab(lambda t: t.redo()), None, "redo", "Redo (Ctrl+Y / Ctrl+Shift+Z)")
        self.act["redo"].setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        add("copy", "&Copy", self.copy_text, K.Copy)
        add("paste", "&Paste", self.paste, K.Paste)
        add("delete_selected", "&Delete Selected", self.delete_selected, K.Delete)
        add("cancel_edit", "Cancel Operation", self.cancel_edit, "Esc")
        add("select_all", "Select &All", self.select_all, K.SelectAll)
        add("properties_panel", "&Properties Panel", self.toggle_properties, "F10", None, "Show/hide the Properties panel (F10)",
            checkable=True)
        self.act["properties_panel"].setChecked(True)
        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        for t in TOOLS:
            a = add("tool_" + t.id, t.label, (lambda _c=False, tid=t.id: self.set_tool(tid)), t.shortcut, t.icon,
                    f"{t.label} ({t.shortcut})", checkable=True)
            self.tool_group.addAction(a)
        self.act["tool_browse"].setChecked(True)
        add("zoom_in", "Zoom &In", lambda: self._with_tab(lambda t: t.view.zoom_by(1.25)), K.ZoomIn,
            "zoom_in", "Zoom in (Ctrl++)")
        add("zoom_out", "Zoom &Out", lambda: self._with_tab(lambda t: t.view.zoom_by(0.8)), K.ZoomOut,
            "zoom_out", "Zoom out (Ctrl+-)")
        add("fit_width", "Fit &Width", lambda: self._with_tab(lambda t: t.view.fit_width()), "Ctrl+2",
            "fit_width", "Fit width (Ctrl+2)")
        add("fit_page", "Fit &Page", lambda: self._with_tab(lambda t: t.view.fit_page()), "Ctrl+0",
            "fit_page", "Fit page (Ctrl+0)")
        add("actual", "&Actual Size", lambda: self._with_tab(lambda t: t.view.actual_size()), "Ctrl+1")
        add("sidebar", "Toggle &Sidebar", self.toggle_sidebar, "F9", "sidebar", "Toggle sidebar (F9)",
            checkable=True)
        self.act["sidebar"].setChecked(True)
        add("prev", "&Previous Page", lambda: self._with_tab(lambda t: t.prev_page()), "Alt+Up", "prev",
            "Previous page (Alt+Up)")
        add("next", "&Next Page", lambda: self._with_tab(lambda t: t.next_page()), "Alt+Down", "next",
            "Next page (Alt+Down)")
        add("rotate", "&Rotate Page", lambda: self._with_tab(lambda t: t.rotate_current(90)), "Ctrl+R",
            "rotate", "Rotate current page 90° clockwise (Ctrl+R)")
        add("insert", "&Insert Page", self.insert_page, None)
        add("delete", "&Delete Page", self.delete_page, None)
        add("extract", "&Extract Pages…", self.extract_pages, None)
        add("merge", "&Merge PDFs…", self.merge_pdfs, None)
        add("search", "&Search", lambda: self._with_tab(lambda t: t.show_search()), K.Find, "search",
            "Search (Ctrl+F)")
        add("properties", "Document &Properties…", self.show_properties, "Ctrl+D")
        add("about", "&About N1cePDF", self.show_about, None)

    def _make_menus(self) -> None:
        mb = self.menuBar()
        if sys.platform != "darwin":
            mb.setNativeMenuBar(False)  # in-window menu bar: robust on KDE/Wayland and Windows
        mb.setVisible(True)
        a = self.act
        spec = {
            "&File": ["open", "save", "save_as", None, "close", None, "exit"],
            "&Edit": ["undo", "redo", None, "copy", "paste", "delete_selected", None, "select_all", None,
                      "cancel_edit"],
            "&View": ["zoom_in", "zoom_out", "fit_width", "fit_page", "actual", None, "prev", "next",
                      None, "sidebar"],
            "&Document": ["rotate", "insert", "delete", None, "extract", "merge"],
            "&Tools": ["search", "properties", "properties_panel", None] + ["tool_" + t.id for t in TOOLS],
            "&Help": ["about"],
        }
        self.menus = {}
        for title, names in spec.items():
            m = mb.addMenu(title)
            self.menus[title.replace("&", "")] = m
            for n in names:
                m.addSeparator() if n is None else m.addAction(a[n])

    def _make_toolbar(self) -> None:
        tb = QToolBar("Main", self)
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.toolbar = tb
        a = self.act
        for n in ("open", "save", "sidebar"):
            tb.addAction(a[n])
        tb.addSeparator()
        tb.addAction(a["undo"])
        tb.addAction(a["redo"])
        tb.addSeparator()
        tb.addAction(a["prev"])
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setKeyboardTracking(False)
        self.page_spin.setToolTip("Current page")
        self.page_spin.valueChanged.connect(lambda v: self._with_tab(lambda t: t.goto(v - 1)))
        self.page_total = QLabel(" / 0 ")
        self.act["page_spin"] = tb.addWidget(self.page_spin)
        self.act["page_total"] = tb.addWidget(self.page_total)
        tb.addAction(a["next"])
        tb.addSeparator()
        tb.addAction(a["zoom_out"])
        self.zoom_combo = QComboBox()
        self.zoom_combo.setEditable(True)
        self.zoom_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.zoom_combo.addItems([f"{z}%" for z in ZOOM_PRESETS])
        self.zoom_combo.setMinimumContentsLength(5)
        self.zoom_combo.setToolTip("Zoom percentage")
        self.zoom_combo.activated.connect(lambda _i: self._zoom_from_text())
        self.zoom_combo.lineEdit().returnPressed.connect(self._zoom_from_text)
        self.act["zoom_combo"] = tb.addWidget(self.zoom_combo)
        tb.addAction(a["zoom_in"])
        tb.addAction(a["fit_width"])
        tb.addAction(a["fit_page"])
        tb.addSeparator()
        tb.addAction(a["rotate"])
        tb.addAction(a["search"])
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)
        self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        et = QToolBar("Edit tools", self)
        et.setMovable(False)
        et.setIconSize(QSize(20, 20))
        et.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        for t in TOOLS:
            et.addAction(a["tool_" + t.id])
            if t.id in ("browse", "edit_images", "add_image", "strike", "draw"):
                et.addSeparator()
        self.edit_toolbar = et
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, et)

    def _make_statusbar(self) -> None:
        sb = self.statusBar()
        self.page_label = QLabel("")
        self.zoom_label = QLabel("")
        self.mod_label = QLabel("")
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setMaximumWidth(120)
        self.progress.setTextVisible(False)
        self.progress.hide()
        for w in (self.progress, self.mod_label, self.page_label, self.zoom_label):
            sb.addPermanentWidget(w)
        sb.showMessage("Ready")

    # ------------------------------------------------------------------ helpers
    def cur(self) -> DocumentTab | None:
        w = self.tabs.currentWidget()
        return w if isinstance(w, DocumentTab) else None

    def _with_tab(self, fn) -> None:
        t = self.cur()
        if t is not None:
            fn(t)

    def _tabs(self) -> list[DocumentTab]:
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def _run(self, fn, on_result, on_error, text: str, tab: DocumentTab | None = None) -> None:
        """Run fn on a worker thread with a progress indicator; callbacks run on the GUI thread."""
        self._busy += 1
        if tab is not None:
            tab.busy += 1
        self.progress.show()
        self.statusBar().showMessage(text)

        def finish() -> None:
            self._busy -= 1
            if tab is not None:
                tab.busy -= 1
            if self._busy <= 0:
                self.progress.hide()

        def ok(res) -> None:
            finish()
            on_result(res)

        def bad(err: Exception) -> None:
            finish()
            on_error(err)

        self.runner.submit(fn, ok, bad)

    def _error(self, title: str, text: str) -> None:
        QMessageBox.warning(self, title, text)

    def _update_ui(self) -> None:
        t = self.cur()
        has = t is not None
        self.stack.setCurrentWidget(self.tabs if has else self.welcome)
        doc_actions = ("save_as", "close", "copy", "select_all", "zoom_in", "zoom_out", "fit_width",
                       "fit_page", "actual", "sidebar", "prev", "next", "rotate", "insert", "delete",
                       "extract", "search", "properties", "page_spin", "page_total", "zoom_combo", "paste",
                       "delete_selected", "cancel_edit", "properties_panel") + tuple("tool_" + t.id for t in TOOLS)
        for n in doc_actions:
            self.act[n].setEnabled(has)
        for n in ("merge", "open", "exit", "about"):
            self.act[n].setEnabled(True)
        if not has:
            self.act["save"].setEnabled(False)
            self.act["undo"].setEnabled(False)
            self.act["redo"].setEnabled(False)
            self.setWindowTitle("N1cePDF")
            self.page_label.setText("")
            self.zoom_label.setText("")
            self.mod_label.setText("")
            self.welcome.refresh()
            return
        self.act["save"].setEnabled(t.doc.modified or t.doc.path is None)
        st = t.session.stack
        self.act["undo"].setEnabled(st.can_undo)
        self.act["redo"].setEnabled(st.can_redo)
        self.act["undo"].setText(f"&Undo {st.undo_text}".rstrip())
        self.act["redo"].setText(f"&Redo {st.redo_text}".rstrip())
        self.act["tool_" + t.controller.tool].setChecked(True)
        self.act["properties_panel"].setChecked(t.panel.isVisibleTo(t))
        self.act["delete"].setEnabled(t.doc.page_count > 1)
        self.act["sidebar"].setChecked(t.sidebar.isVisibleTo(t))
        self._show_page(t.view.current_page, t.page_count)
        self._show_zoom(t.view.zoom)
        self.mod_label.setText("Modified" if t.doc.modified else "")
        self.setWindowTitle(f"{t.doc.display_name}{' *' if t.doc.modified else ''} — N1cePDF")
        i = self.tabs.indexOf(t)
        self.tabs.setTabText(i, t.title())
        self.tabs.setTabToolTip(i, str(t.doc.path or "Unsaved document"))

    def _show_page(self, page: int, total: int) -> None:
        self.page_label.setText(f"Page {page + 1} / {total}")
        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(max(1, total))
        self.page_spin.setValue(page + 1)
        self.page_spin.blockSignals(False)
        self.page_total.setText(f" / {total} ")

    def _show_zoom(self, z: float) -> None:
        text = f"{round(z * 100)}%"
        self.zoom_label.setText(text)
        self.zoom_combo.blockSignals(True)
        self.zoom_combo.setCurrentText(text)
        self.zoom_combo.blockSignals(False)

    def _zoom_from_text(self) -> None:
        txt = self.zoom_combo.currentText().strip().rstrip("%").strip()
        try:
            pct = float(txt)
        except ValueError:
            self._update_ui()
            return
        self._with_tab(lambda t: t.view.set_zoom(pct / 100.0))

    # ------------------------------------------------------------------ opening
    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF documents (*.pdf);;All files (*)")
        if path:
            self.open_path(path)

    def open_path(self, path, password: str | None = None) -> None:
        path = str(path)
        for t in self._tabs():
            if t.doc.path and same_file(path, t.doc.path):
                self.tabs.setCurrentWidget(t)   # already open (a Save As re-binds the tab to its new file)
                return

        def work(cancel):
            doc = PdfDocument.open(path, password)
            try:
                return doc, PageRenderer(doc.snapshot_bytes())
            except Exception:
                doc.close()
                raise

        self._run(work, lambda res: self._opened(path, res), lambda e: self._open_failed(path, e),
                  f"Opening {Path(path).name}…")

    def _open_failed(self, path: str, err: Exception) -> None:
        self.statusBar().showMessage("Ready")
        if isinstance(err, (PasswordRequired, WrongPassword)):
            pwd = self._ask_password(Path(path).name, isinstance(err, WrongPassword))
            if pwd is not None:
                self.open_path(path, pwd)
            return
        if isinstance(err, InvalidPdfError):
            self._error("Cannot open document", f"{Path(path).name}\n\n{err}")
        else:
            log.warning("open failed: %s", type(err).__name__)
            self._error("Cannot open document",
                        f"{Path(path).name}\n\nAn unexpected error occurred ({type(err).__name__}).")

    def _ask_password(self, name: str, wrong: bool) -> str | None:
        msg = ("Incorrect password. Try again:" if wrong else "This document is password protected.\nEnter the password:")
        text, ok = QInputDialog.getText(self, f"Password — {name}", msg, QLineEdit.EchoMode.Password)
        return text if ok else None

    def _opened(self, path: str | None, res) -> None:
        doc, renderer = res
        if self._closing:  # window is shutting down: release the resources instead of adding a tab
            renderer.close()
            doc.close()
            return
        self._add_tab(doc, renderer)
        if path:
            self.recent.add(path)
        self.statusBar().showMessage("Ready")

    def _add_tab(self, doc: PdfDocument, renderer: PageRenderer) -> DocumentTab:
        tab = DocumentTab(doc, renderer, self.runner)
        tab.sidebar.setVisible(self._sidebar_visible)
        tab.page_changed.connect(lambda p, n, t=tab: self._on_tab_page(t, p, n))
        tab.zoom_changed.connect(lambda z, t=tab: self._on_tab_zoom(t, z))
        tab.state_changed.connect(self._update_ui)
        tab.controller.tool_changed.connect(lambda _t, tb=tab: self._update_ui() if tb is self.cur() else None)
        tab.message.connect(self.statusBar().showMessage)
        idx = self.tabs.addTab(tab, tab.title())
        self.tabs.setCurrentIndex(idx)
        tab.view.fit_width()
        self._update_ui()
        return tab

    def _on_tab_page(self, tab: DocumentTab, p: int, n: int) -> None:
        if tab is self.cur():
            self._show_page(p, n)

    def _on_tab_zoom(self, tab: DocumentTab, z: float) -> None:
        if tab is self.cur():
            self._show_zoom(z)

    def create_blank(self) -> None:
        doc = PdfDocument.new_blank()
        self._add_tab(doc, PageRenderer(doc.snapshot_bytes()))

    # drag & drop -------------------------------------------------------
    @staticmethod
    def _pdf_urls(mime) -> list[str]:
        return [u.toLocalFile() for u in mime.urls() if u.isLocalFile() and u.toLocalFile().lower().endswith(".pdf")]

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls() and self._pdf_urls(e.mimeData()):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        for p in self._pdf_urls(e.mimeData()):
            self.open_path(p)
        e.acceptProposedAction()

    # ------------------------------------------------------------------ saving
    def _confirm_overwrite(self, name: str) -> str:
        """Returns 'overwrite', 'save_as' or 'cancel'."""
        box = QMessageBox(QMessageBox.Icon.Warning, "Overwrite original?",
                          f"Save will overwrite the original file “{name}”.", parent=self)
        ow = box.addButton("Overwrite", QMessageBox.ButtonRole.AcceptRole)
        sa = box.addButton("Save As…", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return "overwrite" if box.clickedButton() is ow else "save_as" if box.clickedButton() is sa else "cancel"

    def _confirm_decrypt(self) -> bool:
        return QMessageBox.question(self, "Save without encryption?",
                                    "This document is encrypted. The saved copy will be UNENCRYPTED. Continue?"
                                    ) == QMessageBox.StandardButton.Yes

    def _ask_save_path(self, tab: DocumentTab) -> str | None:
        start = str(tab.doc.path) if tab.doc.path else tab.doc.display_name
        path, _ = QFileDialog.getSaveFileName(self, "Save PDF as", start, "PDF documents (*.pdf)")
        if path and not path.lower().endswith(".pdf"):
            path += ".pdf"
        return path or None

    def save(self, on_success=None, sync: bool = False) -> None:
        t = self.cur()
        if t is not None:
            self._save_tab(t, as_new=False, on_success=on_success, sync=sync)

    def save_as(self) -> None:
        t = self.cur()
        if t is not None:
            self._save_tab(t, as_new=True)

    def _save_tab(self, tab: DocumentTab, as_new: bool, on_success=None, sync: bool = False) -> bool:
        if tab.busy:
            self.statusBar().showMessage("Please wait for the current operation to finish.")
            return False
        dest = tab.doc.path
        encrypted = tab.doc.pdf.is_encrypted
        if dest is None or as_new or encrypted:
            if encrypted and not self._confirm_decrypt():
                return False
            chosen = self._ask_save_path(tab)
            if not chosen:
                return False
            dest = Path(chosen)
        elif not tab.overwrite_confirmed:
            answer = self._confirm_overwrite(dest.name)
            if answer == "cancel":
                return False
            if answer == "save_as":
                return self._save_tab(tab, as_new=True, on_success=on_success, sync=sync)
            tab.overwrite_confirmed = True

        save_warnings: list[str] = []

        def work(cancel=None):
            with tab.doc_lock:
                return save_atomic(tab.doc, dest, allow_decrypt=encrypted, session=tab.session,
                                   warnings=save_warnings)

        def done(out) -> None:
            if tab.is_closed:
                return
            tab.doc.mark_saved(out if not encrypted else None)
            tab.session.mark_saved()
            if not encrypted:
                self.recent.add(out)
            extra = f" — {len(save_warnings)} note(s): " + " ".join(save_warnings) if save_warnings else ""
            self.statusBar().showMessage(f"Saved {Path(out).name}{extra}")
            tab.state_changed.emit()
            if on_success:
                on_success()

        def failed(err: Exception) -> None:
            if tab.is_closed:
                return
            self.statusBar().showMessage("Save failed")
            self._error("Save failed", str(err) if isinstance(err, (SaveValidationError, EncryptedSaveRefused))
                        else f"Unexpected error ({type(err).__name__}).")

        if sync:  # used on application exit, where a blocking save is the safe choice
            try:
                done(work())
                return True
            except Exception as e:  # noqa: BLE001
                failed(e)
                return False
        self._run(work, done, failed, f"Saving {dest.name}…", tab)
        return True

    # ------------------------------------------------------------------ closing
    def _confirm_discard(self, name: str) -> str:
        """Returns 'save', 'discard' or 'cancel'."""
        box = QMessageBox(QMessageBox.Icon.Question, "Unsaved changes",
                          f"“{name}” has unsaved changes.", parent=self)
        sv = box.addButton("Save…", QMessageBox.ButtonRole.AcceptRole)
        ds = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return "save" if box.clickedButton() is sv else "discard" if box.clickedButton() is ds else "cancel"

    def close_current(self) -> None:
        if self.tabs.count():
            self.close_tab(self.tabs.currentIndex())

    def close_tab(self, index: int) -> bool:
        tab = self.tabs.widget(index)
        if not isinstance(tab, DocumentTab):
            return False
        if tab.busy:
            self.statusBar().showMessage("Please wait for the current operation to finish.")
            return False
        if tab.doc.modified:
            answer = self._confirm_discard(tab.doc.display_name)
            if answer == "cancel":
                return False
            if answer == "save":
                if not self._save_tab(tab, as_new=tab.doc.path is None, sync=True) or tab.doc.modified:
                    return False
        self._dispose_tab(tab)
        return True

    def _dispose_tab(self, tab: DocumentTab) -> None:
        self.tabs.removeTab(self.tabs.indexOf(tab))
        tab.shutdown()
        tab.deleteLater()
        self._update_ui()

    def force_close_all(self) -> None:
        """Close everything without prompting (tests, shutdown after confirmation)."""
        for t in self._tabs():
            self._dispose_tab(t)

    def closeEvent(self, e) -> None:  # noqa: N802
        for t in reversed(self._tabs()):
            self.tabs.setCurrentWidget(t)
            if not self.close_tab(self.tabs.indexOf(t)):
                e.ignore()
                return
        self._closing = True
        self.runner.shutdown()
        e.accept()

    # ------------------------------------------------------------------ view / edit actions
    def toggle_sidebar(self) -> None:
        self._sidebar_visible = not self._sidebar_visible
        for t in self._tabs():
            t.sidebar.setVisible(self._sidebar_visible)
        self.act["sidebar"].setChecked(self._sidebar_visible)

    def set_tool(self, tool_id: str) -> None:
        self._with_tab(lambda t: t.controller.set_tool(tool_id))

    def cancel_edit(self) -> None:
        self._with_tab(lambda t: t.controller.cancel())

    def delete_selected(self) -> None:
        self._with_tab(lambda t: t.controller.delete_selected())

    def paste(self) -> None:
        self._with_tab(lambda t: t.controller.paste())

    def toggle_properties(self) -> None:
        self._with_tab(lambda t: t.toggle_properties())

    def copy_text(self) -> None:
        t = self.cur()
        if t is not None and t.controller.copy_selected():   # an added object is selected: copy that
            return
        self._with_tab(lambda t: t.copy_selection_async(
            lambda text: (QGuiApplication.clipboard().setText(text),
                          self.statusBar().showMessage("Copied selected text"))))

    def select_all(self) -> None:
        self._with_tab(lambda t: t.view.select_all())

    def insert_page(self) -> None:
        self._with_tab(lambda t: t.insert_blank_after_current())

    def delete_page(self) -> None:
        t = self.cur()
        if t is None:
            return
        if t.doc.page_count < 2:
            self._error("Delete page", "A document must keep at least one page.")
            return
        n = t.view.current_page + 1
        if QMessageBox.question(self, "Delete page",
                                f"Delete page {n}?\nYou can undo this with Ctrl+Z. The original file on disk "
                                "stays unchanged until you save.") == QMessageBox.StandardButton.Yes:
            t.delete_current()

    def extract_pages(self) -> None:
        t = self.cur()
        if t is None or t.busy:
            return
        spec, ok = QInputDialog.getText(self, "Extract pages", f"Pages to extract (1-{t.doc.page_count}), e.g. 1-3,5:")
        if not ok:
            return
        try:
            indices = [i for r in parse_page_ranges(spec, t.doc.page_count) for i in r]
        except ValueError as e:
            self._error("Extract pages", str(e))
            return
        dest, _ = QFileDialog.getSaveFileName(self, "Save extracted pages", "extract.pdf", "PDF documents (*.pdf)")
        if not dest:
            return

        def work(cancel):
            with t.doc_lock:
                out = pm.extract_pages(t.doc, indices)
                try:
                    return save_atomic(out, dest), len(indices)
                finally:
                    out.close()

        self._run(work, lambda r: self.statusBar().showMessage(f"Extracted {r[1]} pages to {Path(r[0]).name}"),
                  lambda e: self._error("Extract pages", f"Could not extract ({type(e).__name__})."),
                  "Extracting pages…", t)

    def merge_pdfs(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Select PDFs to merge (in order)", "", "PDF documents (*.pdf)")
        if len(paths) < 2:
            if paths:
                self._error("Merge PDFs", "Select at least two PDF files.")
            return
        dest, _ = QFileDialog.getSaveFileName(self, "Save merged PDF", "merged.pdf", "PDF documents (*.pdf)")
        if not dest:
            return

        def work(cancel):
            merged = pm.merge(paths)
            try:
                return save_atomic(merged, dest)
            finally:
                merged.close()

        def failed(e: Exception) -> None:
            msg = ("One of the files is password protected. Open and save an unlocked copy first."
                   if isinstance(e, (PasswordRequired, WrongPassword))
                   else str(e) if isinstance(e, N1cePdfError) else f"Unexpected error ({type(e).__name__}).")
            self._error("Merge PDFs", msg)

        self._run(work, lambda out: (self.statusBar().showMessage(f"Merged {len(paths)} files"), self.open_path(out)),
                  failed, "Merging PDFs…")

    # ------------------------------------------------------------------ dialogs
    def show_properties(self) -> None:
        t = self.cur()
        if t is None:
            return
        d, enc = t.doc, t.doc.encryption_info()
        dlg = QDialog(self)
        dlg.setWindowTitle("Document Properties")
        form = QFormLayout()
        w, h = d.page_size(0)
        rows = [("File", d.display_name), ("Location", str(d.path.parent) if d.path else "(not saved)"),
                ("Pages", str(d.page_count)), ("First page size", f"{w:.0f} × {h:.0f} pt"),
                ("Encrypted", "Yes" if enc.encrypted else "No")]
        if d.path and d.path.is_file():
            rows.insert(2, ("File size", f"{d.path.stat().st_size / 1024:.1f} KiB"))
        rows += [(k.lstrip("/"), v) for k, v in d.metadata().items()]
        for k, v in rows:
            lab = QLabel(v)
            lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lab.setWordWrap(True)
            form.addRow(f"{k}:", lab)
        lay = QVBoxLayout(dlg)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.exec()

    def show_about(self) -> None:
        QMessageBox.about(self, "About N1cePDF",
                          f"<b>N1cePDF {__version__}</b><br>Offline desktop PDF workspace.<br>"
                          "Rendering by PDFium (pypdfium2); structure by pikepdf/qpdf; UI by Qt for Python.<br>"
                          "No network access, accounts or telemetry.")


def app_icon() -> QIcon:
    """The application icon (window, taskbar, dialogs). Bundled PNG; empty icon if the file is missing."""
    p = resource_path("assets/n1cepdf.png")
    return QIcon(str(p)) if p.is_file() else QIcon()


def _fatal_dialog(message: str) -> None:
    QMessageBox.critical(None, "N1cePDF", f"{message}\n\nDetails were written to the log file.")


def run(argv: list[str]) -> int:
    log_file = setup_logging()
    install_excepthook(_fatal_dialog)
    set_windows_app_id()                       # before any window exists: taskbar grouping + icon
    QApplication.setApplicationName("N1cePDF")
    QApplication.setApplicationVersion(__version__)
    app = QApplication(argv)
    app.setDesktopFileName("n1cepdf")
    app.setWindowIcon(app_icon())
    apply_theme(app, "dark")
    logging.getLogger(__name__).info("N1cePDF %s started (log: %s)", __version__, log_file)
    win = MainWindow()
    win.show()
    # Files passed by Windows Explorer ("Open with", double-click) or the command line. Each argument is one
    # path, so spaces and non-ASCII characters need no special handling.
    for p in argv[1:]:
        if not p.startswith("-"):
            win.open_path(p)
    code = app.exec()
    logging.getLogger(__name__).info("N1cePDF exited with code %s", code)
    return code
