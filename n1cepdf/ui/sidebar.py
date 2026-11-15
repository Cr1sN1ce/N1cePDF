"""Left sidebar: lazy page thumbnails, bookmarks tree, document search."""
from __future__ import annotations
from collections import OrderedDict
from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QLabel, QLineEdit, QListView, QListWidget,
                               QListWidgetItem, QTabWidget, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)
from ..core.renderer import OutlineEntry, PageRenderer
from .bridge import RenderBridge, attach

THUMB_W, THUMB_H = 120, 160
MAX_THUMB_ICONS = 150  # bounded thumbnail cache (icons beyond this are dropped, LRU)


def rgb_to_pixmap(res, dpr: float) -> QPixmap:
    img = QImage(res.rgb, res.width, res.height, res.width * 3, QImage.Format.Format_RGB888).copy()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm


class ThumbnailPanel(QListWidget):
    page_selected = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.TopToBottom)
        self.setWrapping(False)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setIconSize(QSize(THUMB_W, THUMB_H))
        self.setGridSize(QSize(THUMB_W + 28, THUMB_H + 36))
        self.setUniformItemSizes(True)
        self.setSpacing(2)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        blank = QPixmap(THUMB_W, THUMB_H)
        blank.fill(QColor("#ffffff"))
        self._blank = QIcon(blank)  # one shared placeholder for every unrendered item
        self._renderer: PageRenderer | None = None
        self._sizes: list[tuple[float, float]] = []
        self._token = 0
        self._have: OrderedDict[int, bool] = OrderedDict()
        self._pending: set[int] = set()
        self._guard = False
        self._bridge = RenderBridge(self)
        self._bridge.done.connect(self._on_thumb)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._request_visible)
        self.verticalScrollBar().valueChanged.connect(lambda _v=0: self._timer.start())
        self.currentRowChanged.connect(self._on_row)

    def set_renderer(self, renderer: PageRenderer, sizes: list[tuple[float, float]]) -> None:
        self._token += 1
        self._guard = True
        self.clear()
        self._have.clear()
        self._pending.clear()
        self._renderer, self._sizes = renderer, sizes
        for i in range(len(sizes)):
            item = QListWidgetItem(self._blank, str(i + 1))
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.addItem(item)
        self._guard = False
        self._timer.start()

    def shutdown(self) -> None:
        self._token += 1
        self._renderer = None
        self._timer.stop()

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._timer.start()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._timer.start()

    def _request_visible(self) -> None:
        if self._renderer is None or self.count() == 0:
            return
        n = self.count()
        first = self.indexAt(QPoint(10, 10)).row()
        last = self.indexAt(QPoint(10, max(11, self.viewport().height() - 10))).row()
        first = 0 if first < 0 else first
        last = n - 1 if last < 0 else last
        dpr = self.devicePixelRatioF()
        for r in range(max(0, first - 2), min(n, last + 3)):
            if r in self._have or r in self._pending:
                continue
            w, h = self._sizes[r]
            scale = min(THUMB_W / w, THUMB_H / h)
            fut = self._renderer.render_async(r, scale * dpr, cancellable=False)
            self._pending.add(r)
            attach(fut, self._bridge, tag=self._token)

    def _on_thumb(self, payload) -> None:
        tag, res = payload
        if tag != self._token:
            return
        self._pending.discard(res.page)
        item = self.item(res.page)
        if item is None:
            return
        item.setIcon(QIcon(rgb_to_pixmap(res, self.devicePixelRatioF())))
        self._have[res.page] = True
        self._have.move_to_end(res.page)
        while len(self._have) > MAX_THUMB_ICONS:
            old, _ = self._have.popitem(last=False)
            old_item = self.item(old)
            if old_item is not None:
                old_item.setIcon(self._blank)

    def set_current(self, page: int) -> None:
        if 0 <= page < self.count():
            self._guard = True
            self.setCurrentRow(page)
            self._guard = False
            self.scrollToItem(self.item(page))

    def _on_row(self, row: int) -> None:
        if not self._guard and row >= 0:
            self.page_selected.emit(row)


class BookmarksPanel(QWidget):
    page_selected = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(self._clicked)
        self.empty = QLabel("This document has no bookmarks.")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        lay.addWidget(self.tree)
        lay.addWidget(self.empty)
        self.set_outline([])

    def set_outline(self, entries: list[OutlineEntry]) -> None:
        self.tree.clear()
        stack: list[QTreeWidgetItem] = []
        for e in entries:
            level = max(0, min(e.level, len(stack)))
            del stack[level:]
            item = QTreeWidgetItem([e.title])
            item.setData(0, Qt.ItemDataRole.UserRole, e.page)
            if stack:
                stack[-1].addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            stack.append(item)
        self.tree.expandToDepth(0)
        self.tree.setVisible(bool(entries))
        self.empty.setVisible(not entries)

    def _clicked(self, item: QTreeWidgetItem, _col: int = 0) -> None:
        page = item.data(0, Qt.ItemDataRole.UserRole)
        if page is not None:
            self.page_selected.emit(int(page))


class SearchPanel(QWidget):
    search_requested = Signal(str)
    result_activated = Signal(int)
    cleared = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Search in document…")
        self.input.setClearButtonEnabled(True)
        self.status = QLabel("")
        self.results = QListWidget()
        lay.addWidget(self.input)
        lay.addWidget(self.status)
        lay.addWidget(self.results)
        self._last_query = ""
        self.input.returnPressed.connect(self._submit)
        self.input.textChanged.connect(self._text_changed)
        self.results.currentRowChanged.connect(self._row)
        self.results.itemClicked.connect(lambda _it: self._row(self.results.currentRow()))

    def focus_input(self) -> None:
        self.input.setFocus()
        self.input.selectAll()

    def _submit(self) -> None:
        q = self.input.text().strip()
        if not q:
            return
        if q == self._last_query and self.results.count() > 0:
            self.next_result(1)
            return
        self._last_query = q
        self.search_requested.emit(q)

    def _text_changed(self, text: str) -> None:
        if not text.strip():
            self.results.clear()
            self.status.setText("")
            self._last_query = ""
            self.cleared.emit()

    def set_busy(self) -> None:
        self.status.setText("Searching…")
        self.results.clear()

    def set_results(self, items: list[tuple[int, str]], pages: int, truncated: bool = False) -> None:
        self.results.clear()
        for page, snippet in items:
            self.results.addItem(f"p. {page + 1}   {snippet}")
        if not items:
            self.status.setText("No matches")
        else:
            more = " (showing first results)" if truncated else ""
            self.status.setText(f"{len(items)} matches on {pages} pages{more}")

    def clear_results(self, message: str = "") -> None:
        """Drop results and forget the last query (so Enter searches again). The typed text is
        kept. Emits nothing, so it cannot loop back into DocumentTab."""
        self.results.clear()
        self.status.setText(message)
        self._last_query = ""

    def next_result(self, step: int) -> None:
        n = self.results.count()
        if n:
            self.results.setCurrentRow((self.results.currentRow() + step) % n)

    def _row(self, row: int) -> None:
        if row >= 0:
            self.result_activated.emit(row)


class Sidebar(QTabWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.thumbs = ThumbnailPanel()
        self.bookmarks = BookmarksPanel()
        self.search = SearchPanel()
        self.addTab(self.thumbs, "Pages")
        self.addTab(self.bookmarks, "Bookmarks")
        self.addTab(self.search, "Search")
        self.setMinimumWidth(190)

    def show_search(self) -> None:
        self.setCurrentWidget(self.search)
        self.search.focus_input()
