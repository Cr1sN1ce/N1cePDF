"""Continuous-scroll PDF view on QGraphicsScene.

Coordinates: one QGraphicsRectItem per page (logical pixels, 1 pt = zoom*96/72 px), with a
child pixmap item holding the rendered bitmap. Mapping between PDF user space and page-item
pixels is done by pdfium itself (FPDF_PageToDevice), so rotation and CropBox are handled.
Only pages near the viewport keep bitmaps; the others are evicted.
Selection is limited to one page per drag; Select All covers the whole document.
"""
from __future__ import annotations
import logging
from bisect import bisect_right
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView
from ..core.renderer import PageRenderer, SearchHit
from .bridge import RenderBridge, attach
from .sidebar import rgb_to_pixmap

log = logging.getLogger(__name__)

GAP = 14
MARGIN = 16
MIN_ZOOM, MAX_ZOOM = 0.1, 8.0
PX_PER_PT = 96.0 / 72.0

_SEARCH_BRUSH = QBrush(QColor(255, 213, 0, 90))
_CURRENT_BRUSH = QBrush(QColor(255, 120, 0, 150))
_SELECT_BRUSH = QBrush(QColor(59, 108, 255, 110))


class DocumentView(QGraphicsView):
    page_changed = Signal(int, int)   # current page (0-based), total
    zoom_changed = Signal(float)
    selection_changed = Signal(bool)
    selection_finished = Signal()     # a text-selection drag ended (used by the markup tools)
    layout_changed = Signal()         # page positions/sizes changed (zoom, resize, reload)
    rebuilding = Signal()             # emitted BEFORE page items are destroyed (drop references to them)
    rebuilt = Signal()                # all page items were recreated (renderer replaced)
    error = Signal(str)

    def __init__(self, renderer: PageRenderer, parent=None):
        super().__init__(parent)
        self.renderer = renderer
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.setBackgroundBrush(QBrush(QColor("#2a2b31")))
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)  # stable fit-width
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(False)
        self.viewport().setAcceptDrops(False)
        self.setMouseTracking(True)

        self._bridge = RenderBridge(self)
        self._bridge.done.connect(self._on_rendered)
        self._bridge.failed.connect(self.error)

        self._zoom = 1.0
        self._fit: str | None = None  # "width" | "page" | None
        self._closed = False
        self.tool_handler = None  # object with mouse_press/move/release/double_click/key_press -> bool
        self._current = 0
        self._sizes: list[tuple[float, float]] = []
        self._rects: list[QGraphicsRectItem] = []
        self._pix: list[QGraphicsPixmapItem] = []
        self._pixscale: list[float | None] = []
        self._pixw: list[float] = []
        self._tops: list[float] = []
        self._pw: list[float] = []
        self._ph: list[float] = []
        self._pending: set[tuple[int, float]] = set()

        self._hits: list[SearchHit] = []
        self._flat: list[tuple[int, int]] = []  # (page, rect index within page hit)
        self._search_current = -1
        self._selection: dict[int, tuple[int, int]] = {}
        self._select_all = False
        self._anchor: tuple[int, int] | None = None
        self._selecting = False
        self._panning: QPointF | None = None
        self._overlays: dict[int, list[QGraphicsRectItem]] = {}
        self._ov_key = 0
        self._ov_done: dict[int, int] = {}

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._update_visible)
        self._refit_timer = QTimer(self)
        self._refit_timer.setSingleShot(True)
        self._refit_timer.setInterval(80)
        self._refit_timer.timeout.connect(self._refit)
        self.verticalScrollBar().valueChanged.connect(lambda _v=0: self._timer.start())
        self.horizontalScrollBar().valueChanged.connect(lambda _v=0: self._timer.start())
        self._build()

    # ---- properties ----------------------------------------------------
    @property
    def zoom(self) -> float:
        return self._zoom

    @property
    def fit_mode(self) -> str | None:
        return self._fit

    @property
    def current_page(self) -> int:
        return self._current

    @property
    def page_count(self) -> int:
        return len(self._sizes)

    @property
    def has_selection(self) -> bool:
        return self._select_all or bool(self._selection)

    # ---- public geometry helpers (used by the editing layer) -------------
    def dpr(self) -> float:
        return self._dpr()

    def render_scale(self) -> float:
        return self._render_scale()

    def page_item(self, i: int) -> QGraphicsRectItem:
        return self._rects[i]

    def page_px_size(self, i: int) -> tuple[float, float]:
        return self._pw[i], self._ph[i]

    def page_at(self, scene_y: float) -> int:
        return self._page_at(scene_y)

    def locate(self, view_pos) -> tuple[int, float, float, bool]:
        """View (viewport) point -> (page index, x, y in page-item pixels, inside the page)."""
        sp = self.mapToScene(view_pos)
        i = self._page_at(sp.y())
        o = self._rects[i].pos()
        x, y = sp.x() - o.x(), sp.y() - o.y()
        return i, x, y, (0 <= x <= self._pw[i] and 0 <= y <= self._ph[i])

    def selection_info(self) -> tuple[int, int, int] | None:
        """(page, first char, last char) of the current single-page text selection, if any."""
        if self._select_all or not self._selection:
            return None
        page, (a, b) = next(iter(self._selection.items()))
        return page, min(a, b), max(a, b)

    def _dpr(self) -> float:
        return self.devicePixelRatioF()

    def _render_scale(self) -> float:
        return round(self._zoom * PX_PER_PT * self._dpr(), 3)

    # ---- (re)building --------------------------------------------------
    def _build(self) -> None:
        self.rebuilding.emit()
        self.renderer.bump_generation()
        self._scene.clear()
        self._overlays.clear()
        self._ov_done.clear()
        self._pending.clear()
        self._sizes = self.renderer.page_sizes()
        n = len(self._sizes)
        self._rects, self._pix = [], []
        for _ in range(n):
            rect = QGraphicsRectItem(0, 0, 1, 1)
            rect.setBrush(QBrush(QColor("#ffffff")))
            rect.setPen(QPen(QColor("#555a66"), 0))
            self._scene.addItem(rect)
            pix = QGraphicsPixmapItem(rect)
            pix.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            self._rects.append(rect)
            self._pix.append(pix)
        self._pixscale = [None] * n
        self._pixw = [0.0] * n
        self._apply_layout()
        self.rebuilt.emit()

    def reload(self, renderer: PageRenderer, keep_page: int | None = None) -> None:
        """Swap in a new renderer (after structural edits). Caller closes the old one."""
        page = self._current if keep_page is None else keep_page
        self.renderer = renderer
        self._hits, self._flat, self._search_current = [], [], -1
        self._selection, self._select_all, self._anchor = {}, False, None
        self._build()
        self.go_to_page(min(page, max(0, self.page_count - 1)))
        self.selection_changed.emit(False)

    def _apply_layout(self) -> None:
        k = self._zoom * PX_PER_PT
        widest = max((w for w, _ in self._sizes), default=1.0) * k
        y = 0.0
        self._tops, self._pw, self._ph = [], [], []
        for i, (wpt, hpt) in enumerate(self._sizes):
            w, h = wpt * k, hpt * k
            self._rects[i].setRect(0, 0, w, h)
            self._rects[i].setPos((widest - w) / 2.0, y)
            if self._pixw[i] > 0:
                self._pix[i].setScale(w / self._pixw[i])  # stretch old bitmap until re-rendered
            self._tops.append(y)
            self._pw.append(w)
            self._ph.append(h)
            y += h + GAP
        self._scene.setSceneRect(-GAP, -GAP, widest + 2 * GAP, max(y, 1.0) + GAP)
        self._ov_key += 1
        self._timer.start()
        self.layout_changed.emit()

    # ---- geometry helpers ----------------------------------------------
    def _page_at(self, y: float) -> int:
        if not self._tops:
            return 0
        i = bisect_right(self._tops, y) - 1
        return max(0, min(i, len(self._tops) - 1))

    def _visible_range(self, margin_px: float = 0.0) -> tuple[int, int]:
        r = self.mapToScene(self.viewport().rect()).boundingRect()
        a, b = r.top() - margin_px, r.bottom() + margin_px
        first = self._page_at(a)
        last = first
        while last + 1 < len(self._tops) and self._tops[last + 1] <= b:
            last += 1
        return first, last

    def _anchor_state(self) -> tuple[int, float]:
        cy = self.mapToScene(self.viewport().rect().center()).y()
        i = self._page_at(cy)
        return i, (cy - self._tops[i]) / max(self._ph[i], 1.0) if self._tops else 0.0

    def _restore_anchor(self, i: int, frac: float) -> None:
        if not self._tops:
            return
        cy = self._tops[i] + frac * self._ph[i]
        value = cy - self.viewport().height() / 2.0 - self.sceneRect().top()
        self.verticalScrollBar().setValue(int(value))

    # ---- navigation / zoom ---------------------------------------------
    def go_to_page(self, i: int) -> None:
        if not self._tops:
            return
        i = max(0, min(i, len(self._tops) - 1))
        self.verticalScrollBar().setValue(int(self._tops[i] - GAP / 2 - self.sceneRect().top()))
        self._set_current(i)
        self._timer.start()

    def _set_current(self, i: int) -> None:
        if i != self._current:
            self._current = i
            self.page_changed.emit(i, self.page_count)

    def set_zoom(self, z: float, *, keep_fit: bool = False) -> None:
        z = max(MIN_ZOOM, min(float(z), MAX_ZOOM))
        if not keep_fit:
            self._fit = None
        if abs(z - self._zoom) < 1e-4:
            self.zoom_changed.emit(self._zoom)
            return
        i, frac = self._anchor_state()
        self._zoom = z
        self.renderer.bump_generation()  # cancels queued renders at the old scale
        self._pending.clear()
        self._apply_layout()
        self._restore_anchor(i, frac)
        self.zoom_changed.emit(self._zoom)

    def zoom_by(self, factor: float) -> None:
        self.set_zoom(self._zoom * factor)

    def actual_size(self) -> None:
        self.set_zoom(1.0)

    def fit_width(self) -> None:
        self._fit = "width"
        self._refit()

    def fit_page(self) -> None:
        self._fit = "page"
        self._refit()

    def _refit(self) -> None:
        if self._fit is None or not self._sizes:
            return
        avail_w = max(50, self.viewport().width() - 2 * MARGIN)
        if self._fit == "width":
            widest = max(w for w, _ in self._sizes)
            z = avail_w / (widest * PX_PER_PT)
        else:
            w, h = self._sizes[self._current]
            avail_h = max(50, self.viewport().height() - 2 * MARGIN)
            z = min(avail_w / (w * PX_PER_PT), avail_h / (h * PX_PER_PT))
        cur = self._current
        self.set_zoom(z, keep_fit=True)
        if self._fit == "page":
            self.go_to_page(cur)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        if self._fit is not None:
            self._refit_timer.start()
        self._timer.start()

    def wheelEvent(self, e) -> None:  # noqa: N802
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            steps = e.angleDelta().y() / 120.0
            if steps:
                self.zoom_by(1.1 ** steps)
            e.accept()
        else:
            super().wheelEvent(e)

    # ---- lazy rendering ------------------------------------------------
    def _update_visible(self) -> None:
        if self._closed or not self._tops:
            return
        first, last = self._visible_range()
        pre_a, pre_b = self._visible_range(self.viewport().height())
        key = self._render_scale()
        for i in range(pre_a, pre_b + 1):
            if self._pixscale[i] != key and (i, key) not in self._pending:
                self._pending.add((i, key))
                attach(self.renderer.render_async(i, key), self._bridge, tag=self.renderer)
        # evict bitmaps and overlays far from the viewport (bounded memory)
        keep_a, keep_b = self._visible_range(self.viewport().height() * 3)
        for i in range(len(self._pix)):
            if keep_a <= i <= keep_b:
                continue
            if self._pixscale[i] is not None:
                self._pix[i].setPixmap(QPixmap())
                self._pixscale[i], self._pixw[i] = None, 0.0
                self._pix[i].setScale(1.0)
            if i in self._ov_done:
                self._clear_overlay(i)
        for i in range(first, last + 1):
            if self._ov_done.get(i) != self._ov_key:
                self._refresh_overlay(i)
        mid = self._page_at(self.mapToScene(self.viewport().rect().center()).y())
        self._set_current(mid)

    def _on_rendered(self, payload) -> None:
        if self._closed:
            return
        tag, res = payload
        if tag is not self.renderer:
            return  # result from a replaced renderer (after reload): never apply
        self._pending.discard((res.page, res.scale))
        if not self.renderer.is_current(res) or res.page >= len(self._pix):
            return  # stale result discarded
        if res.scale != self._render_scale():
            return
        dpr = self._dpr()
        self._pix[res.page].setPixmap(rgb_to_pixmap(res, dpr))
        self._pixscale[res.page] = res.scale
        self._pixw[res.page] = res.width / dpr
        self._pix[res.page].setScale(self._pw[res.page] / self._pixw[res.page])

    # ---- overlays: search highlights and selection ---------------------
    def _clear_overlay(self, i: int) -> None:
        for item in self._overlays.pop(i, []):
            item.setParentItem(None)
            self._scene.removeItem(item)
        self._ov_done.pop(i, None)

    def _add_overlay(self, i: int, rects, brush: QBrush) -> None:
        for x, y, w, h in rects:
            item = QGraphicsRectItem(x, y, w, h, self._rects[i])
            item.setBrush(brush)
            item.setPen(QPen(Qt.PenStyle.NoPen))
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self._overlays.setdefault(i, []).append(item)

    def _refresh_overlay(self, i: int) -> None:
        for item in self._overlays.pop(i, []):
            item.setParentItem(None)
            self._scene.removeItem(item)
        w, h = self._pw[i], self._ph[i]
        # search hits
        cur_page, cur_rect = (self._flat[self._search_current] if 0 <= self._search_current < len(self._flat)
                              else (-1, -1))
        for hit in self._hits:
            if hit.page != i:
                continue
            rects = self.renderer.rects_px(i, w, h, list(hit.rects))
            normal = [r for k, r in enumerate(rects) if not (cur_page == i and k == cur_rect)]
            self._add_overlay(i, normal, _SEARCH_BRUSH)
            if cur_page == i and 0 <= cur_rect < len(rects):
                self._add_overlay(i, [rects[cur_rect]], _CURRENT_BRUSH)
        # selection
        sel = None
        if self._select_all:
            n = self.renderer.char_count(i)
            sel = (0, n - 1) if n > 0 else None
        elif i in self._selection:
            sel = self._selection[i]
        if sel is not None:
            self._add_overlay(i, self.renderer.selection_rects_px(i, w, h, *sel), _SELECT_BRUSH)
        self._ov_done[i] = self._ov_key

    def _invalidate_overlays(self) -> None:
        self._ov_key += 1
        self._timer.start()
        first, last = self._visible_range()
        for i in range(first, last + 1):
            self._refresh_overlay(i)

    # ---- search highlighting -------------------------------------------
    def set_search_hits(self, hits: list[SearchHit]) -> None:
        self._hits = hits
        self._flat = [(h.page, k) for h in hits for k in range(len(h.rects))]
        self._search_current = -1
        self._invalidate_overlays()

    def clear_search(self) -> None:
        self.set_search_hits([])

    def highlight_result(self, k: int) -> None:
        if not (0 <= k < len(self._flat)):
            return
        self._search_current = k
        page, rect_idx = self._flat[k]
        hit = next(h for h in self._hits if h.page == page)
        x, y, w, h = self.renderer.rects_px(page, self._pw[page], self._ph[page], [hit.rects[rect_idx]])[0]
        origin = self._rects[page].pos()
        self.ensureVisible(QRectF(origin.x() + x, origin.y() + y, max(w, 1), max(h, 1)), 60,
                           int(self.viewport().height() / 3))
        self._invalidate_overlays()

    # ---- text selection ------------------------------------------------
    def select_all(self) -> None:
        self._select_all = True
        self._selection.clear()
        self._invalidate_overlays()
        self.selection_changed.emit(True)

    def clear_selection(self) -> None:
        had = self.has_selection
        self._select_all = False
        self._selection.clear()
        self._anchor = None
        self._invalidate_overlays()
        if had:
            self.selection_changed.emit(False)

    def selected_text(self) -> str:
        """Safe to call from a worker thread (renderer calls are lock-protected)."""
        if self._select_all:
            return "\n".join(self.renderer.page_text(i) for i in range(self.page_count))
        parts = [self.renderer.selected_text(i, a, b) for i, (a, b) in sorted(dict(self._selection).items())]
        return "\n".join(p for p in parts if p)

    def _char_under(self, page: int, scene_pos: QPointF, tol: float, clamp: bool) -> int | None:
        origin = self._rects[page].pos()
        x, y = scene_pos.x() - origin.x(), scene_pos.y() - origin.y()
        w, h = self._pw[page], self._ph[page]
        if clamp:
            x, y = min(max(x, 0.0), w), min(max(y, 0.0), h)
        elif not (0 <= x <= w and 0 <= y <= h):
            return None
        return self.renderer.char_at(page, w, h, x, y, tol)

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        if self.tool_handler is not None and self.tool_handler.double_click(e):
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if self.tool_handler is not None and self.tool_handler.key_press(e):
            e.accept()
            return
        super().keyPressEvent(e)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if (e.button() == Qt.MouseButton.LeftButton and self.tool_handler is not None
                and self.tool_handler.mouse_press(e)):
            e.accept()
            return
        if e.button() == Qt.MouseButton.MiddleButton:
            self._panning = e.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton and self._tops:
            sp = self.mapToScene(e.position().toPoint())
            page = self._page_at(sp.y())
            idx = self._char_under(page, sp, 6.0, clamp=False)
            self._select_all = False
            if idx is None:
                self._selection.clear()
                self._anchor = None
                self._invalidate_overlays()
                self.selection_changed.emit(False)
            else:
                self._anchor = (page, idx)
                self._selection = {page: (idx, idx)}
                self._selecting = True
                self._invalidate_overlays()
                self.selection_changed.emit(True)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._panning is None and self.tool_handler is not None and self.tool_handler.mouse_move(e):
            e.accept()
            return
        if self._panning is not None:
            d = e.position() - self._panning
            self._panning = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            e.accept()
            return
        sp = self.mapToScene(e.position().toPoint())
        if self._selecting and self._anchor is not None:
            page = self._anchor[0]
            idx = self._char_under(page, sp, 24.0, clamp=True)
            if idx is not None and self._selection.get(page) != (self._anchor[1], idx):
                self._selection = {page: (self._anchor[1], idx)}
                self._invalidate_overlays()
            e.accept()
            return
        if self._tops:
            page = self._page_at(sp.y())
            over = self._char_under(page, sp, 4.0, clamp=False) is not None
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor if over else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.MiddleButton and self._panning is not None:
            self._panning = None
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton and self.tool_handler is not None \
                and self.tool_handler.mouse_release(e):
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton and self._selecting:
            self._selecting = False
            self.selection_finished.emit()
            e.accept()
            return
        super().mouseReleaseEvent(e)

    # ---- lifecycle -----------------------------------------------------
    def shutdown(self) -> None:
        self._closed = True
        self._timer.stop()
        self._refit_timer.stop()
        self.renderer.bump_generation()
        self._pending.clear()
