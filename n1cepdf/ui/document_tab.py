"""One open document: sidebar + view. Owns its renderer, search state and zoom/scroll state."""
from __future__ import annotations
import logging
import threading
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QSplitter, QWidget
from ..core.document import PdfDocument
from ..core.renderer import PageRenderer, SearchHit
from ..edit.session import ChangeEvent, EditSession
from .document_view import DocumentView
from .edit_controller import EditController
from .properties_panel import PropertiesPanel
from .sidebar import Sidebar
from .tasks import TaskHandle, TaskRunner

log = logging.getLogger(__name__)
MAX_RESULTS = 3000
SEARCH_STALE = "Search results cleared because the document changed. Search again."


class DocumentTab(QSplitter):
    page_changed = Signal(int, int)
    zoom_changed = Signal(float)
    state_changed = Signal()       # modified flag / busy state changed
    selection_changed = Signal(bool)
    message = Signal(str)

    def __init__(self, doc: PdfDocument, renderer: PageRenderer, runner: TaskRunner,
                 parent: QWidget | None = None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.doc, self.renderer, self.runner = doc, renderer, runner
        self.busy = 0
        self.overwrite_confirmed = False
        # Held by any worker that reads/writes self.doc, and by shutdown() before closing it,
        # so a tab can never close its document underneath a running save/snapshot.
        self.doc_lock = threading.RLock()
        self._handles: set[TaskHandle] = set()
        self._search_handle: TaskHandle | None = None
        self._outline_handle: TaskHandle | None = None
        self._reloading = False
        self._closed = False

        self.sidebar = Sidebar()
        self.view = DocumentView(renderer)
        self.session = EditSession(doc)
        self.panel = PropertiesPanel()
        self.addWidget(self.sidebar)
        self.addWidget(self.view)
        self.addWidget(self.panel)
        self.setStretchFactor(0, 0)
        self.setStretchFactor(1, 1)
        self.setStretchFactor(2, 0)
        self.setSizes([260, 900, 250])
        self.setChildrenCollapsible(False)
        self.controller = EditController(self)
        self.controller.attach_panel(self.panel)
        self.controller.status.connect(self.message)
        self.session.listeners.append(self._on_session_event)

        self.sidebar.thumbs.set_renderer(renderer, renderer.page_sizes())
        self.sidebar.thumbs.page_selected.connect(self.view.go_to_page)
        self.sidebar.bookmarks.page_selected.connect(self.view.go_to_page)
        self.sidebar.search.search_requested.connect(self.start_search)
        self.sidebar.search.result_activated.connect(self.view.highlight_result)
        self.sidebar.search.cleared.connect(self.clear_search)
        self.view.page_changed.connect(self._on_page_changed)
        self.view.zoom_changed.connect(self.zoom_changed)
        self.view.selection_changed.connect(self.selection_changed)
        self.view.error.connect(lambda m: self.message.emit(f"Render error: {m}"))
        self._load_outline()

    # ---- guarded background tasks --------------------------------------
    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def reloading(self) -> bool:
        return self._reloading

    def submit(self, fn, on_ok, on_err=None) -> TaskHandle:
        """Public alias of the guarded task runner (callbacks are dropped once the tab is closed)."""
        return self._submit(fn, on_ok, on_err)

    def _submit(self, fn, on_ok, on_err=None) -> TaskHandle:
        """Run fn on a worker. Callbacks are dropped if the tab was closed meanwhile, so a late
        result can never touch a destroyed widget or a closed document."""
        box: list[TaskHandle] = []

        def ok(res) -> None:
            self._handles.discard(box[0])
            if not self._closed:
                on_ok(res)

        def err(e: Exception) -> None:
            self._handles.discard(box[0])
            if self._closed:
                return
            if on_err is not None:
                on_err(e)
            else:
                log.warning("tab task failed: %s", type(e).__name__)

        handle = self.runner.submit(fn, ok, err)
        box.append(handle)
        self._handles.add(handle)
        return handle

    # ---- title / state -------------------------------------------------
    def title(self) -> str:
        return ("● " if self.doc.modified else "") + self.doc.display_name

    @property
    def page_count(self) -> int:
        return self.view.page_count

    def _on_page_changed(self, page: int, total: int) -> None:
        self.sidebar.thumbs.set_current(page)
        self.page_changed.emit(page, total)

    def _load_outline(self) -> None:
        if self._outline_handle:
            self._outline_handle.cancel()
        r = self.renderer
        self._outline_handle = self._submit(
            lambda cancel: r.outline(),
            lambda entries: self.sidebar.bookmarks.set_outline(entries) if r is self.renderer else None)

    def start_ocr(self):
        if self._can_edit():
            from .ocr_dialog import OcrDialog
            OcrDialog(self).exec()

    # ---- navigation ----------------------------------------------------
    def goto(self, page: int) -> None:
        self.view.go_to_page(page)

    def next_page(self) -> None:
        self.view.go_to_page(self.view.current_page + 1)

    def prev_page(self) -> None:
        self.view.go_to_page(self.view.current_page - 1)

    def toggle_sidebar(self, visible: bool | None = None) -> None:
        self.sidebar.setVisible(not self.sidebar.isVisibleTo(self) if visible is None else visible)

    def show_search(self) -> None:
        self.sidebar.setVisible(True)
        self.sidebar.show_search()

    # ---- search --------------------------------------------------------
    def _cancel_search(self) -> None:
        if self._search_handle:
            self._search_handle.cancel()
            self._handles.discard(self._search_handle)
            self._search_handle = None

    def start_search(self, text: str) -> None:
        self._cancel_search()
        self.sidebar.search.set_busy()
        self.view.clear_search()
        r, rev = self.renderer, self.doc.revision  # the results are only valid for this state
        ocr_objects = [(i, o) for i, key in enumerate(self.doc.page_keys())
                       for o in self.session.layer.on_page(key)
                       if getattr(o, "ocr_source", None) is not None]

        def search(cancel):
            hits = r.search(text, cancel=cancel)
            if text.strip():
                query = text.casefold()
                for index, obj in ocr_objects:
                    if cancel.is_set():
                        break
                    if query in obj.text.casefold():
                        hits.append(SearchHit(index, ((obj.x0, obj.y0, obj.x1, obj.y1),),
                                              snippets=(obj.text,)))
            return sorted(hits, key=lambda hit: hit.page)

        self._search_handle = self._submit(search,
                                           lambda hits: self._on_search_done(hits, r, rev),
                                           self._on_search_error)

    def _on_search_done(self, hits, renderer: PageRenderer, revision: int) -> None:
        if renderer is not self.renderer or revision != self.doc.revision:
            return  # document changed while searching: stale, discard
        flat, truncated = [], False
        for h in hits:
            for snip in h.snippets:
                flat.append((h.page, snip))
        if len(flat) > MAX_RESULTS:
            flat, truncated = flat[:MAX_RESULTS], True
        self.sidebar.search.set_results(flat, len(hits), truncated)
        self.view.set_search_hits(hits)
        if flat:
            self.sidebar.search.results.setCurrentRow(0)  # navigates to the first match

    def _on_search_error(self, e: Exception) -> None:
        self.sidebar.search.clear_results("Search failed")
        self.message.emit(f"Search failed: {type(e).__name__}")

    def invalidate_search(self, message: str = "") -> None:
        """Cancel a running search and reset results + highlights (typed query text is kept)."""
        self._cancel_search()
        self.sidebar.search.clear_results(message)
        self.view.clear_search()

    def clear_search(self) -> None:
        self.invalidate_search("")

    # ---- structural edits (reversible only via Save As; undo arrives in Phase 2) ----
    def _can_edit(self) -> bool:
        if self.busy or self._reloading:
            self.message.emit("Please wait for the current operation to finish.")
            return False
        return True

    def rotate_current(self, degrees: int = 90) -> None:
        if self._can_edit():
            self.session.rotate_page(self.view.current_page, degrees)

    def insert_blank_after_current(self) -> None:
        if self._can_edit():
            self.session.insert_blank_after(self.view.current_page)

    def delete_current(self) -> bool:
        if not self._can_edit() or self.doc.page_count < 2:
            return False
        self.session.delete_page(self.view.current_page)
        return True

    def undo(self) -> None:
        if self._can_edit():
            self.session.undo()

    def redo(self) -> None:
        if self._can_edit():
            self.session.redo()

    def _on_session_event(self, ev: ChangeEvent) -> None:
        """Every undoable change (do / undo / redo) lands here after the model changed."""
        if ev.structural:
            self._after_edit(min(self.view.current_page, max(0, self.doc.page_count - 1)))
        else:
            self.invalidate_search(SEARCH_STALE)
            self.state_changed.emit()

    def toggle_properties(self, visible: bool | None = None) -> None:
        self.panel.setVisible(not self.panel.isVisibleTo(self) if visible is None else visible)

    def _after_edit(self, goto_page: int) -> None:
        self._reloading = True
        # Page layout changed: any search results/highlights now point at the wrong places.
        self.invalidate_search(SEARCH_STALE)
        self.state_changed.emit()
        doc = self.doc

        def work(cancel):
            with self.doc_lock:
                if self._closed:
                    return None
                return PageRenderer(doc.snapshot_bytes())

        def done(new_renderer: PageRenderer | None) -> None:
            self._reloading = False
            if new_renderer is None:
                return
            old, self.renderer = self.renderer, new_renderer
            self.view.reload(new_renderer, keep_page=goto_page)
            self.sidebar.thumbs.set_renderer(new_renderer, new_renderer.page_sizes())
            self.invalidate_search(SEARCH_STALE)
            self._load_outline()
            old.close()
            self.state_changed.emit()

        def failed(e: Exception) -> None:
            self._reloading = False
            self.message.emit(f"Could not refresh view: {type(e).__name__}")

        self._submit(work, done, failed)

    # ---- clipboard -----------------------------------------------------
    def copy_selection_async(self, on_text) -> None:
        if not self.view.has_selection:
            return
        view = self.view
        self._submit(lambda cancel: view.selected_text(), on_text,
                     lambda e: self.message.emit(f"Copy failed: {type(e).__name__}"))

    # ---- lifecycle -----------------------------------------------------
    def shutdown(self) -> None:
        self._closed = True
        self.view.tool_handler = None
        if self._on_session_event in self.session.listeners:
            self.session.listeners.remove(self._on_session_event)
        for h in list(self._handles):
            h.cancel()
        self.sidebar.thumbs.shutdown()
        self.view.shutdown()
        self.renderer.close()
        with self.doc_lock:  # wait for any in-flight save/snapshot before closing the document
            self.doc.close()
