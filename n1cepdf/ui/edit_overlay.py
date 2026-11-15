"""Qt items for edit objects, selection handles and rubber-band previews.

Everything is positioned from PDF-space coordinates through PageTransform for the current zoom and page
rotation (never stored as pixels). Text/image objects are shown through exact bitmaps produced by the
same writer used for saving (edit.preview); those are only re-rendered after a change or zoom settles,
never per mouse move (dragging just moves/scales the cached bitmap).
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QImage, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsRectItem)
from ..edit.geometry import PageTransform, Rect
from ..edit.objects import Comment, EditObject, ImageBox, Ink, Markup, Shape, TextBox, bbox
from ..edit.preview import display_bounds, render_preview

log = logging.getLogger(__name__)
HANDLE = 8.0
HANDLE_NAMES = ("tl", "t", "tr", "r", "br", "b", "bl", "l")


def _qc(c, a: float = 1.0) -> QColor:
    q = QColor.fromRgbF(c[0], c[1], c[2])
    q.setAlphaF(max(0.0, min(1.0, a)))
    return q


@dataclass
class _Entry:
    obj: EditObject
    page: int
    items: list = field(default_factory=list)
    pix: QGraphicsPixmapItem | None = None
    pix_logical_w: float = 0.0
    scale_key: float | None = None
    rendered: EditObject | None = None   # the object value the current bitmap was rendered for


class EditOverlay:
    def __init__(self, tab):
        self.tab = tab
        self.view = tab.view
        self.entries: dict[str, _Entry] = {}
        self._cache: dict[tuple, tuple[int, int, bytes]] = {}
        self._sel_items: list = []
        self._temp: list = []
        self._sel_rect: tuple = (None, None, False, False)
        self._warned: set[str] = set()
        self._rerender = QTimer(tab)
        self._rerender.setSingleShot(True)
        self._rerender.setInterval(150)
        self._rerender.timeout.connect(self._request_all_previews)
        self.view.rebuilding.connect(self.forget_items)
        self.view.rebuilt.connect(self.rebuild)
        self.view.layout_changed.connect(self.relayout)

    # ---------------------------------------------------------------- geometry
    def transform(self, page: int) -> PageTransform:
        w, h = self.view.page_px_size(page)
        return PageTransform(self.tab.doc.page_crop_box(page), self.tab.doc.page_rotation(page), w, h)

    # ---------------------------------------------------------------- lifecycle
    def forget_items(self) -> None:
        """The scene is about to be cleared: Qt deletes every item, so only drop our references."""
        for e in self.entries.values():
            e.items, e.pix = [], None
        self.entries.clear()
        self._sel_items, self._temp = [], []

    def rebuild(self) -> None:
        self.forget_items()
        self.sync()

    def sync(self) -> None:
        """Make the Qt items match the edit layer (add / update / remove).

        Skipped while the view is out of step with the document (a structural edit is being reloaded):
        page indices in the model may differ from the view's page items until `rebuilt` fires."""
        if self.tab.reloading:
            return
        layer = self.tab.session.layer
        live = {o.id: o for o in layer.all()}
        for oid in [i for i in self.entries if i not in live]:
            self._destroy(oid)
        for oid, obj in live.items():
            idx = self.tab.doc.index_of_key(obj.page_key)
            e = self.entries.get(oid)
            if idx is None or idx >= self.view.page_count:   # page currently deleted (undo restores it)
                if e is not None:
                    self._destroy(oid)
                continue
            if e is None or e.page != idx:
                if e is not None:
                    self._destroy(oid)
                e = self.entries[oid] = _Entry(obj, idx)
                self._create_items(e)
            else:
                if e.obj != obj:
                    e.obj = obj
                    self._layout(e)
                if isinstance(obj, (TextBox, ImageBox)) and e.rendered != obj:
                    self._request_preview(e)      # bitmap is stale (content/size changed): exact re-render

    def _destroy(self, oid: str) -> None:
        e = self.entries.pop(oid, None)
        if e is None:
            return
        for it in e.items:
            self._remove_item(it)

    @staticmethod
    def _remove_item(it) -> None:
        try:
            if it.scene() is not None:
                it.scene().removeItem(it)
        except RuntimeError:
            pass  # already deleted by the scene

    # ---------------------------------------------------------------- item creation / layout
    def _create_items(self, e: _Entry) -> None:
        parent = self.view.page_item(e.page)
        o = e.obj
        if isinstance(o, (TextBox, ImageBox)):
            e.pix = QGraphicsPixmapItem(parent)
            e.pix.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            e.pix.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            e.items = [e.pix]
        elif isinstance(o, (Shape, Ink)):
            it = QGraphicsPathItem(parent)
            it.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            e.items = [it]
        elif isinstance(o, Markup):
            e.items = [self._rect_item(parent) for _ in o.rects]
        else:
            e.items = [self._rect_item(parent)]
        self._layout(e)
        if isinstance(o, (TextBox, ImageBox)):
            self._request_preview(e)

    @staticmethod
    def _rect_item(parent) -> QGraphicsRectItem:
        it = QGraphicsRectItem(parent)
        it.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        return it

    def relayout(self) -> None:
        for e in self.entries.values():
            self._layout(e)
        self._rerender.start()      # re-render previews once zooming settles
        self.refresh_selection()

    def _layout(self, e: _Entry) -> None:
        tr, o = self.transform(e.page), e.obj
        try:
            if isinstance(o, (TextBox, ImageBox)):
                x, y, w, h = tr.rect_to_px(display_bounds(o))
                e.pix.setPos(x, y)
                if e.pix_logical_w > 0:
                    e.pix.setScale(w / e.pix_logical_w)
            elif isinstance(o, Shape):
                self._style_shape(e.items[0], o, tr)
            elif isinstance(o, Ink):
                path = QPainterPath()
                for s in o.strokes:
                    if len(s) < 2:
                        continue
                    p0 = tr.to_px(*s[0])
                    path.moveTo(p0[0], p0[1])
                    for pt in s[1:]:
                        q = tr.to_px(*pt)
                        path.lineTo(q[0], q[1])
                it = e.items[0]
                it.setPath(path)
                pen = QPen(_qc(o.color, o.opacity), max(1.0, o.width * self.tab.view.zoom * 96 / 72))
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                it.setPen(pen)
            elif isinstance(o, Markup):
                for it, r in zip(e.items, o.rects):
                    x, y, w, h = tr.rect_to_px(r)
                    if o.markup == "highlight":
                        it.setRect(x, y, w, h)
                        it.setBrush(QBrush(_qc(o.color, o.opacity)))
                    else:
                        ty = (y + h * 0.5) if o.markup == "strike" else (y + h - 1.0)
                        it.setRect(x, ty, w, max(1.0, self.tab.view.zoom * 96 / 72))
                        it.setBrush(QBrush(_qc(o.color, 1.0)))
                    it.setPen(QPen(Qt.PenStyle.NoPen))
            elif isinstance(o, Comment):
                x, y, w, h = tr.rect_to_px(bbox(o))
                it = e.items[0]
                it.setRect(x, y, w, h)
                it.setBrush(QBrush(_qc(o.color)))
                it.setPen(QPen(QColor("#222222"), 1))
                it.setToolTip(o.text)
        except RuntimeError:
            log.debug("overlay item already deleted during layout")

    def _style_shape(self, it: QGraphicsPathItem, o: Shape, tr: PageTransform) -> None:
        import math
        path = QPainterPath()
        x0, y0 = tr.to_px(o.x0, o.y0)
        x1, y1 = tr.to_px(o.x1, o.y1)
        if o.shape == "rect":
            path.addRect(QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized())
        elif o.shape == "ellipse":
            path.addEllipse(QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized())
        else:
            path.moveTo(x0, y0)
            path.lineTo(x1, y1)
            if o.shape == "arrow":
                ang = math.atan2(y1 - y0, x1 - x0)
                head = max(8.0, o.width * 4.5) * self.tab.view.zoom * 96 / 72
                for da in (math.radians(155), -math.radians(155)):
                    path.moveTo(x1, y1)
                    path.lineTo(x1 + head * math.cos(ang + da), y1 + head * math.sin(ang + da))
        it.setPath(path)
        pen = QPen(_qc(o.stroke, o.opacity), max(1.0, o.width * self.tab.view.zoom * 96 / 72))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        it.setPen(pen)
        it.setBrush(QBrush(_qc(o.fill, o.opacity)) if (o.fill is not None and o.shape in ("rect", "ellipse"))
                    else QBrush(Qt.BrushStyle.NoBrush))

    # ---------------------------------------------------------------- previews (text / image)
    def _request_all_previews(self) -> None:
        for e in self.entries.values():
            if isinstance(e.obj, (TextBox, ImageBox)):
                self._request_preview(e)

    def _request_preview(self, e: _Entry) -> None:
        obj, page = e.obj, e.page
        rot = self.tab.doc.page_rotation(page)
        scale = self.view.render_scale()
        key = (obj, rot, scale)
        if key in self._cache:
            self._apply_preview(e.obj.id, key, self._cache[key])
            return
        cat = self.tab.session.catalog
        self.tab.submit(lambda cancel: render_preview(obj, rot, scale, cat)[:4],
                        lambda res: self._preview_done(obj.id, key, res),
                        lambda err: self.tab.message.emit(f"Preview failed: {type(err).__name__}"))

    def _preview_done(self, oid: str, key: tuple, res) -> None:
        w, h, rgba, warnings = res
        self._cache[key] = (w, h, rgba)
        while len(self._cache) > 48:
            self._cache.pop(next(iter(self._cache)))
        for msg in warnings:
            if msg not in self._warned:
                self._warned.add(msg)
                self.tab.message.emit(msg)
        self._apply_preview(oid, key, (w, h, rgba))

    def _apply_preview(self, oid: str, key: tuple, bitmap: tuple[int, int, bytes]) -> None:
        e = self.entries.get(oid)
        if e is None or e.obj != key[0] or key[2] != self.view.render_scale() or e.pix is None:
            return  # stale: the object or zoom changed while rendering
        w, h, rgba = bitmap
        dpr = self.view.dpr()
        img = QImage(rgba, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        try:
            e.pix.setPixmap(pm)
            e.pix_logical_w = w / dpr
            e.rendered = key[0]
            self._layout(e)
        except RuntimeError:
            pass

    # ---------------------------------------------------------------- live (temporary) display during drags
    def show_temp(self, obj: EditObject) -> None:
        """Show obj's geometry without touching the layer (moves/scales cached bitmaps; no re-render)."""
        e = self.entries.get(obj.id)
        if e is None:
            return
        e.obj = obj
        self._layout(e)   # the next sync() restores the committed object (and re-renders if its size changed)

    def revert_temp(self, obj: EditObject) -> None:
        e = self.entries.get(obj.id)
        if e is not None:
            e.obj = obj
            self._layout(e)

    # ---------------------------------------------------------------- selection visuals
    def set_selection(self, page: int | None, rect: Rect | None, resizable: bool = True,
                      dashed: bool = False) -> None:
        self._sel_rect = (page, rect, resizable, dashed)
        self.refresh_selection()

    def clear_selection(self) -> None:
        self._sel_rect = (None, None, False, False)
        self.refresh_selection()

    def refresh_selection(self) -> None:
        for it in self._sel_items:
            self._remove_item(it)
        self._sel_items = []
        page, rect, resizable, dashed = self._sel_rect
        if page is None or rect is None or page >= self.view.page_count:
            return
        tr = self.transform(page)
        x, y, w, h = tr.rect_to_px(rect)
        parent = self.view.page_item(page)
        frame = QGraphicsRectItem(x, y, w, h, parent)
        pen = QPen(QColor("#3b6cff"), 1.5)
        pen.setStyle(Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine)
        frame.setPen(pen)
        frame.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        frame.setZValue(1000)
        frame.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._sel_items.append(frame)
        if resizable:
            for name, (hx, hy) in self.handle_points(x, y, w, h).items():
                hi = QGraphicsRectItem(hx - HANDLE / 2, hy - HANDLE / 2, HANDLE, HANDLE, parent)
                hi.setBrush(QBrush(QColor("#ffffff")))
                hi.setPen(QPen(QColor("#3b6cff"), 1.5))
                hi.setZValue(1001)
                hi.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                self._sel_items.append(hi)

    @staticmethod
    def handle_points(x: float, y: float, w: float, h: float) -> dict[str, tuple[float, float]]:
        cx, cy = x + w / 2, y + h / 2
        return {"tl": (x, y), "t": (cx, y), "tr": (x + w, y), "r": (x + w, cy),
                "br": (x + w, y + h), "b": (cx, y + h), "bl": (x, y + h), "l": (x, cy)}

    def handle_at(self, page: int, rect: Rect, px: float, py: float) -> str | None:
        """Which resize handle (if any) is under the page-pixel point. Handle size is constant in screen
        pixels, so it stays easy to hit at every zoom level."""
        x, y, w, h = self.transform(page).rect_to_px(rect)
        for name, (hx, hy) in self.handle_points(x, y, w, h).items():
            if abs(px - hx) <= HANDLE and abs(py - hy) <= HANDLE:
                return name
        return None

    # ---------------------------------------------------------------- rubber band for new shapes / ink
    def clear_temp(self) -> None:
        for it in self._temp:
            self._remove_item(it)
        self._temp = []

    def temp_shape(self, page: int, shape: Shape) -> None:
        self.clear_temp()
        it = QGraphicsPathItem(self.view.page_item(page))
        it.setZValue(900)
        it.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._style_shape(it, shape, self.transform(page))
        self._temp.append(it)

    def temp_ink(self, page: int, pts: list[tuple[float, float]], color, width: float) -> None:
        self.clear_temp()
        it = QGraphicsPathItem(self.view.page_item(page))
        it.setZValue(900)
        it.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        tr = self.transform(page)
        path = QPainterPath()
        if pts:
            a = tr.to_px(*pts[0])
            path.moveTo(a[0], a[1])
            for p in pts[1:]:
                b = tr.to_px(*p)
                path.lineTo(b[0], b[1])
        it.setPath(path)
        pen = QPen(_qc(color), max(1.0, width * self.view.zoom * 96 / 72))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        it.setPen(pen)
        self._temp.append(it)
