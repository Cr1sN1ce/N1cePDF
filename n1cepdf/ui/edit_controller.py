"""EditController: tool state machine for one document tab.

Mouse input is mapped viewport -> page pixels -> PDF space (PageTransform), so hit-testing and edits are
correct at any zoom, scroll position and page rotation. Added objects live in the EditLayer and every change
is an undoable command. Existing text/images are changed through verified PDFium edit plans that are
computed on a worker thread and shown to the user (with all warnings) before being applied.
"""
from __future__ import annotations
import io
import logging
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from PySide6.QtCore import QBuffer, QIODevice, QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox
from PIL import Image
from ..edit import pdfium_edit as pe
from ..edit.geometry import Rect, point_in_rect
from ..edit.objects import (Comment, EditObject, ImageBox, Ink, Markup, Shape, TextBox, bbox, moved, new_id,
                            with_rect)
from ..edit.preview import display_bounds
from ..edit.session import EditPlan, EditRefused, TextStyle, default_style
from ..edit.writer import fit_text_box
from .edit_overlay import EditOverlay
from .edit_tools import MARKUP_TOOLS, TOOL_BY_ID
from .properties_panel import PropertiesPanel, ToolOptions
from .text_dialog import EditPlanDialog, TextEditDialog

log = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 50 * 1024 * 1024
PX_PER_PT = 96.0 / 72.0
_RESIZE_CURSORS = {"tl": "SizeFDiagCursor", "br": "SizeFDiagCursor", "tr": "SizeBDiagCursor",
                   "bl": "SizeBDiagCursor", "t": "SizeVerCursor", "b": "SizeVerCursor",
                   "l": "SizeHorCursor", "r": "SizeHorCursor"}


@dataclass
class ExistingSel:
    kind: str                       # text | image
    page: int
    key: tuple
    rect: Rect
    run: pe.TextRun | None = None
    info: pe.PageObjectInfo | None = None


@dataclass
class _Drag:
    kind: str                       # move | resize | ink | shape | existing-move | existing-resize
    page: int
    start: tuple[float, float]
    before: EditObject | None = None
    current: EditObject | None = None
    handle: str | None = None
    rect: Rect | None = None        # existing image rectangle (PDF space)
    new_rect: Rect | None = None
    points: list = field(default_factory=list)
    shape: str = "rect"
    moved: bool = False


def raw_rect(o: EditObject) -> Rect:
    return (min(o.x0, o.x1), min(o.y0, o.y1), max(o.x0, o.x1), max(o.y0, o.y1))


class EditController(QObject):
    selection_changed = Signal(object)
    tool_changed = Signal(str)
    status = Signal(str)

    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.view = tab.view
        self.session = tab.session
        self.overlay = EditOverlay(tab)
        self.panel: PropertiesPanel | None = None
        self.tool = "browse"
        self.options = ToolOptions()
        fams = self.session.catalog.families()
        self.options.families = fams
        for pref in ("Liberation Sans", "DejaVu Sans", "Noto Sans", "Arial"):
            if pref in fams:
                self.options.text_family = pref
                break
        else:
            if fams:
                self.options.text_family = fams[0]
        self.selected_id: str | None = None
        self.existing: ExistingSel | None = None
        self._drag: _Drag | None = None
        self._planning = False
        self._clip: EditObject | None = None
        self._runs: dict[tuple, pe.PageContent] = {}
        self.view.tool_handler = self
        self.view.selection_finished.connect(self._finish_markup)
        self.view.rebuilt.connect(self._on_view_rebuilt)
        self.session.listeners.append(self.on_session_event)
        self.overlay.rebuild()

    # ------------------------------------------------------------------ wiring
    def attach_panel(self, panel: PropertiesPanel) -> None:
        self.panel = panel
        panel.set_families(self.options.families)
        panel.object_changed.connect(self.on_object_changed)
        panel.option_changed.connect(self.on_option_changed)
        panel.action_requested.connect(self.on_action)
        panel.show_tool_options(self.tool, self.options)

    def _say(self, text: str) -> None:
        self.status.emit(text)

    # ------------------------------------------------------------------ tools
    def set_tool(self, tool_id: str) -> None:
        if tool_id not in TOOL_BY_ID:
            raise ValueError(f"unknown tool {tool_id}")
        self._abort_drag()
        if tool_id != self.tool:
            self.deselect()
            self.view.clear_selection()
        self.tool = tool_id
        t = TOOL_BY_ID[tool_id]
        if tool_id == "browse" or tool_id in MARKUP_TOOLS:
            self.view.viewport().unsetCursor()
        else:
            self.view.viewport().setCursor(getattr(Qt.CursorShape, t.cursor))
        if self.panel is not None and self.selected_id is None and self.existing is None:
            self.panel.show_tool_options(tool_id, self.options)
        self._say(t.hint)
        self.tool_changed.emit(tool_id)

    def cancel(self) -> bool:
        """Esc: abort a drag, else clear the selection, else leave the current tool."""
        if self._drag is not None:
            self._abort_drag()
            return True
        if self.selected_id is not None or self.existing is not None:
            self.deselect()
            return True
        if self.view.has_selection:
            self.view.clear_selection()
            return True
        if self.tool != "browse":
            self.set_tool("browse")
            return True
        return False

    def _abort_drag(self) -> None:
        d, self._drag = self._drag, None
        self.overlay.clear_temp()
        if d is not None and d.before is not None:
            self.overlay.revert_temp(d.before)
            self._refresh_selection_visual()
        if d is not None and d.kind.startswith("existing"):
            self._refresh_selection_visual()

    # ------------------------------------------------------------------ geometry helpers
    @property
    def _ppp(self) -> float:
        return self.view.zoom * PX_PER_PT

    def _tr(self, page: int):
        return self.overlay.transform(page)

    def _local(self, page: int, view_pos) -> tuple[float, float]:
        sp = self.view.mapToScene(view_pos)
        o = self.view.page_item(page).pos()
        w, h = self.view.page_px_size(page)
        return min(max(sp.x() - o.x(), 0.0), w), min(max(sp.y() - o.y(), 0.0), h)

    def _hit_layer(self, key, px: float, py: float, kinds: tuple | None = None) -> EditObject | None:
        pad = 4.0 / self._ppp
        for o in reversed(self.session.layer.on_page(key)):
            if kinds and not isinstance(o, kinds):
                continue
            r = display_bounds(o, 0.0) if isinstance(o, (TextBox, ImageBox)) else bbox(o)
            if point_in_rect(px, py, r, pad):
                return o
        return None

    # ------------------------------------------------------------------ selection
    def deselect(self) -> None:
        self.selected_id = None
        self.existing = None
        self.session.stack.break_merge()
        self.overlay.clear_selection()
        if self.panel is not None:
            self.panel.show_tool_options(self.tool, self.options)
        self.selection_changed.emit(None)

    def select(self, obj: EditObject) -> None:
        self.existing = None
        self.selected_id = obj.id
        self.session.stack.break_merge()
        self._refresh_selection_visual()
        if self.panel is not None:
            self.panel.show_object(obj)
        self.selection_changed.emit(obj)

    def selected_object(self) -> EditObject | None:
        return self.session.layer.get(self.selected_id) if self.selected_id else None

    def _refresh_selection_visual(self) -> None:
        o = self.selected_object()
        if o is not None:
            page = self.tab.doc.index_of_key(o.page_key)
            if page is None or page >= self.view.page_count:
                self.overlay.clear_selection()
                return
            box_like = isinstance(o, (TextBox, ImageBox, Shape))
            rot = getattr(o, "rotation", 0.0)
            rect = display_bounds(o, 0.0) if isinstance(o, (TextBox, ImageBox)) else bbox(o)
            self.overlay.set_selection(page, rect, resizable=box_like and not rot)
        elif self.existing is not None:
            ex = self.existing
            self.overlay.set_selection(ex.page, ex.rect, resizable=ex.kind == "image", dashed=True)
        else:
            self.overlay.clear_selection()

    # ------------------------------------------------------------------ session events
    def on_session_event(self, ev) -> None:
        if not ev.structural:
            self.overlay.sync()
        # structural: the view reloads asynchronously and emits `rebuilt`, which rebuilds the overlay
        if ev.structural:
            self._runs.clear()
            self._abort_drag()
            if self.existing is not None:
                self.existing = None
                self.overlay.clear_selection()
                if self.panel is not None:
                    self.panel.show_tool_options(self.tool, self.options)
        if self.selected_id is not None:
            o = self.selected_object()
            if o is None or self.tab.doc.index_of_key(o.page_key) is None:
                self.deselect()
            else:
                self._refresh_selection_visual()
                if self.panel is not None and self._drag is None:
                    self.panel.show_object(o)

    def _on_view_rebuilt(self) -> None:
        self._runs.clear()
        self._refresh_selection_visual()

    # ------------------------------------------------------------------ mouse (called by DocumentView)
    def mouse_press(self, e) -> bool:
        if self.tool == "browse" or self.tool in MARKUP_TOOLS:
            return False
        if self.tab.reloading:          # the view is being rebuilt after a structural edit: ignore input
            return True
        page, x, y, inside = self.view.locate(e.position().toPoint())
        if not inside:
            if self.tool in ("select", "edit_images", "edit_text"):
                self.deselect()
            return True
        tr = self._tr(page)
        px, py = tr.to_pdf(x, y)
        getattr(self, "_press_" + self.tool)(page, x, y, tr, px, py, e)
        return True

    def mouse_move(self, e) -> bool:
        if self.tool == "browse" or self.tool in MARKUP_TOOLS:
            return False
        d = self._drag
        if d is not None:
            x, y = self._local(d.page, e.position().toPoint())
            self._drag_update(d, x, y, e)
            return True
        page, x, y, inside = self.view.locate(e.position().toPoint())
        self._hover(page, x, y, inside)
        return True

    def mouse_release(self, e) -> bool:
        if self.tool == "browse" or self.tool in MARKUP_TOOLS or self._drag is None:
            return False
        d, self._drag = self._drag, None
        self.overlay.clear_temp()
        self._drag_finish(d)
        return True

    def double_click(self, e) -> bool:
        if self.tool in ("select", "edit_text"):
            o = self.selected_object()
            if isinstance(o, TextBox) and self.panel is not None:
                self.panel.focus_text_editor()
                return True
        return False

    def key_press(self, e) -> bool:
        o = self.selected_object()
        if o is None or self.tool != "select":
            return False
        step = (10.0 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0)
        d = {int(Qt.Key.Key_Left): (-step, 0.0), int(Qt.Key.Key_Right): (step, 0.0),
             int(Qt.Key.Key_Up): (0.0, step), int(Qt.Key.Key_Down): (0.0, -step)}.get(int(e.key()))
        if d is None:
            return False
        self.session.update_object(moved(o, *d), f"Move {o.kind}", merge="nudge")
        return True

    def _hover(self, page: int, x: float, y: float, inside: bool) -> None:
        cursor = getattr(Qt.CursorShape, TOOL_BY_ID[self.tool].cursor)
        sel = self.selected_object()
        rect, resizable, sel_page = None, False, None
        if sel is not None:
            sel_page = self.tab.doc.index_of_key(sel.page_key)
            rect = display_bounds(sel, 0.0) if isinstance(sel, (TextBox, ImageBox)) else bbox(sel)
            resizable = isinstance(sel, (TextBox, ImageBox, Shape)) and not getattr(sel, "rotation", 0.0)
        elif self.existing is not None:
            sel_page, rect, resizable = self.existing.page, self.existing.rect, self.existing.kind == "image"
        if rect is not None and sel_page == page and inside:
            h = self.overlay.handle_at(page, rect, x, y) if resizable else None
            if h:
                cursor = getattr(Qt.CursorShape, _RESIZE_CURSORS[h])
            else:
                px, py = self._tr(page).to_pdf(x, y)
                if point_in_rect(px, py, rect, 0):
                    cursor = Qt.CursorShape.SizeAllCursor
        self.view.viewport().setCursor(cursor)

    # ------------------------------------------------------------------ tool: select / move
    def _press_select(self, page, x, y, tr, px, py, e) -> None:
        sel = self.selected_object()
        if sel is not None and self.tab.doc.index_of_key(sel.page_key) == page:
            rect = display_bounds(sel, 0.0) if isinstance(sel, (TextBox, ImageBox)) else bbox(sel)
            resizable = isinstance(sel, (TextBox, ImageBox, Shape)) and not getattr(sel, "rotation", 0.0)
            h = self.overlay.handle_at(page, rect, x, y) if resizable else None
            if h:
                self._drag = _Drag("resize", page, (x, y), sel, sel, handle=h)
                return
        hit = self._hit_layer(self.tab.doc.page_key(page), px, py)
        if hit is None:
            self.deselect()
            return
        if hit.id != self.selected_id:
            self.select(hit)
        self._drag = _Drag("move", page, (x, y), hit, hit)

    def _drag_update(self, d: _Drag, x: float, y: float, e) -> None:
        tr = self._tr(d.page)
        shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if abs(x - d.start[0]) + abs(y - d.start[1]) > 2.0:
            d.moved = True
        if d.kind == "move":
            dx, dy = tr.delta_to_pdf(x - d.start[0], y - d.start[1])
            d.current = moved(d.before, dx, dy)
            self.overlay.show_temp(d.current)
            self._refresh_selection_visual_for(d.current)
        elif d.kind == "resize":
            d.current = self._resized(d.before, d.handle, x, y, tr, shift)
            self.overlay.show_temp(d.current)
            self._refresh_selection_visual_for(d.current)
        elif d.kind == "ink":
            p = tr.to_pdf(x, y)
            if not d.points or abs(p[0] - d.points[-1][0]) + abs(p[1] - d.points[-1][1]) > 0.5 / self._ppp:
                d.points.append(p)
            self.overlay.temp_ink(d.page, d.points, self.options.ink_color, self.options.ink_width)
        elif d.kind == "shape":
            a, b = tr.to_pdf(*d.start), tr.to_pdf(x, y)
            d.points = [a, b]
            self.overlay.temp_shape(d.page, self._make_shape(d.page, d.shape, a, b))
        elif d.kind == "existing-move":
            dx, dy = tr.delta_to_pdf(x - d.start[0], y - d.start[1])
            r = d.rect
            d.new_rect = (r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy)
            self.overlay.set_selection(d.page, d.new_rect, resizable=True, dashed=True)
        elif d.kind == "existing-resize":
            probe = ImageBox(self.tab.doc.page_key(d.page), *d.rect, lock_aspect=True)
            d.new_rect = raw_rect(self._resized(probe, d.handle, x, y, tr, shift))
            self.overlay.set_selection(d.page, d.new_rect, resizable=True, dashed=True)

    def _refresh_selection_visual_for(self, o: EditObject) -> None:
        page = self.tab.doc.index_of_key(o.page_key)
        if page is not None:
            rect = display_bounds(o, 0.0) if isinstance(o, (TextBox, ImageBox)) else bbox(o)
            self.overlay.set_selection(page, rect, resizable=isinstance(o, (TextBox, ImageBox, Shape))
                                       and not getattr(o, "rotation", 0.0))

    def _resized(self, o: EditObject, handle: str, x: float, y: float, tr, shift: bool) -> EditObject:
        rx, ry, rw, rh = tr.rect_to_px(raw_rect(o))
        left, top, right, bottom = rx, ry, rx + rw, ry + rh
        if "l" in handle:
            left = min(x, right - 6.0)
        if "r" in handle:
            right = max(x, left + 6.0)
        if "t" in handle:
            top = min(y, bottom - 6.0)
        if "b" in handle:
            bottom = max(y, top + 6.0)
        keep = isinstance(o, ImageBox) and (o.lock_aspect or shift) or (shift and isinstance(o, Shape))
        if keep and rw > 0 and rh > 0:
            ratio = rw / rh
            w, h = right - left, bottom - top
            if handle in ("l", "r"):
                h = w / ratio
                cy = (ry + ry + rh) / 2
                top, bottom = cy - h / 2, cy + h / 2
            elif handle in ("t", "b"):
                w = h * ratio
                cx = rx + rw / 2
                left, right = cx - w / 2, cx + w / 2
            else:  # corner: width drives, the opposite corner stays anchored
                h = w / ratio
                if "t" in handle:
                    top = bottom - h
                else:
                    bottom = top + h
        rect = tr.rect_to_pdf(left, top, right - left, bottom - top)
        return with_rect(o, rect)

    def _drag_finish(self, d: _Drag) -> None:
        if d.kind in ("move", "resize"):
            after = d.current
            if d.moved and after is not None and after != d.before:
                self.session.update_object(after, f"{'Move' if d.kind == 'move' else 'Resize'} {after.kind}")
            else:
                self.overlay.revert_temp(d.before)
                self._refresh_selection_visual()
        elif d.kind == "ink":
            if len(d.points) >= 2:
                o = self.options
                self.session.add_object(Ink(self.tab.doc.page_key(d.page), (tuple(d.points),), o.ink_color,
                                            o.ink_width, 1.0), "Draw")
        elif d.kind == "shape":
            if d.moved and len(d.points) == 2:
                shape = self._make_shape(d.page, d.shape, d.points[0], d.points[1])
                self.session.add_object(shape, "Add shape")
                self.select(shape)
        elif d.kind.startswith("existing") and d.moved and d.new_rect and self.existing is not None:
            ex = self.existing
            self._run_plan(lambda: self.session.plan_image_edit(ex.key, ex.page, ex.info, d.new_rect))

    # ------------------------------------------------------------------ tool: add text
    def _press_add_text(self, page, x, y, tr, px, py, e) -> None:
        o = self.options
        key = self.tab.doc.page_key(page)
        h = o.text_size * 1.2 + o.text_size * 0.25
        box = TextBox(key, px, py - h, px + 180.0, py, "Text", o.text_family, o.text_size, o.text_bold,
                      o.text_italic, o.text_color)
        box = fit_text_box(box, self.session.catalog)
        self.session.add_object(box, "Add text")
        self.select(box)
        if self.panel is not None:
            self.panel.focus_text_editor()

    # ------------------------------------------------------------------ tool: shapes / draw
    def _make_shape(self, page: int, kind: str, a, b) -> Shape:
        o = self.options
        return Shape(self.tab.doc.page_key(page), kind, a[0], a[1], b[0], b[1], o.stroke,
                     o.fill if kind in ("rect", "ellipse") else None, o.width, o.opacity)

    def _press_shapes(self, page, x, y, tr, px, py, e) -> None:
        self._drag = _Drag("shape", page, (x, y), shape=self.options.shape)

    def _press_draw(self, page, x, y, tr, px, py, e) -> None:
        self._drag = _Drag("ink", page, (x, y), points=[(px, py)])

    # ------------------------------------------------------------------ tool: comment
    def _ask_comment_text(self) -> str | None:
        text, ok = QInputDialog.getMultiLineText(self.tab, "New comment", "Comment:")
        return text if ok and text.strip() else None

    def _press_comment(self, page, x, y, tr, px, py, e) -> None:
        text = self._ask_comment_text()
        if text is None:
            return
        c = Comment(self.tab.doc.page_key(page), px, py, text, self.options.author)
        self.session.add_object(c, "Add comment")
        self.select(c)

    # ------------------------------------------------------------------ tool: add image
    def _ask_image_path(self) -> str | None:
        path, _ = QFileDialog.getOpenFileName(self.tab, "Choose image", "",
                                              "Images (*.png *.jpg *.jpeg *.webp);;All files (*)")
        return path or None

    def _load_image(self, path: str) -> tuple[bytes, int, int] | None:
        try:
            p = Path(path)
            if p.stat().st_size > MAX_IMAGE_BYTES:
                self._inform("Image too large", "Images larger than 50 MiB are not supported.")
                return None
            data = p.read_bytes()
            with Image.open(io.BytesIO(data)) as im:
                if (im.format or "").upper() not in ("PNG", "JPEG", "WEBP"):
                    self._inform("Unsupported image", "Only PNG, JPEG and WebP images are supported.")
                    return None
                return data, im.width, im.height
        except Exception as ex:  # noqa: BLE001 - untrusted file
            log.warning("image load failed: %s", type(ex).__name__)
            self._inform("Cannot read image", "The file could not be read as a PNG, JPEG or WebP image.")
            return None

    def _press_add_image(self, page, x, y, tr, px, py, e) -> None:
        path = self._ask_image_path()
        if not path:
            return
        loaded = self._load_image(path)
        if loaded is None:
            return
        data, wpx, hpx = loaded
        self._place_image(page, px, py, data, wpx, hpx)

    def _place_image(self, page: int, px: float, py: float, data: bytes, wpx: int, hpx: int) -> None:
        w, h = wpx * 0.75, hpx * 0.75                      # 96 dpi
        crop = self.tab.doc.page_crop_box(page)
        limit = 0.6 * (crop[2] - crop[0])
        if w > limit:
            h, w = h * limit / w, limit
        box = ImageBox(self.tab.doc.page_key(page), px, py - h, px + w, py, data)
        self.session.add_object(box, "Add image")
        self.select(box)

    # ------------------------------------------------------------------ existing content (async)
    def _with_page_content(self, page: int, cb) -> None:
        r = self.tab.renderer
        key = (id(r), page)
        if key in self._runs:
            cb(self._runs[key])
            return

        def done(content) -> None:
            if r is not self.tab.renderer:
                return                                       # the view was reloaded meanwhile
            self._runs[key] = content
            cb(content)

        self.tab.submit(lambda cancel: r.page_content(page), done,
                        lambda err: self._say(f"Could not read the page content ({type(err).__name__})."))

    def _press_edit_text(self, page, x, y, tr, px, py, e) -> None:
        hit = self._hit_layer(self.tab.doc.page_key(page), px, py, (TextBox,))
        if hit is not None:
            self.select(hit)
            if self.panel is not None:
                self.panel.focus_text_editor()
            return
        self._with_page_content(page, lambda c: self._pick_text(page, c, px, py))

    def _pick_text(self, page: int, content: pe.PageContent, px: float, py: float) -> None:
        pad = 2.0
        cands = [r for r in content.runs if point_in_rect(px, py, r.bbox, pad)]
        if not cands:
            forms = [o for o in content.objects if o.kind == "form" and point_in_rect(px, py, o.bbox, pad)]
            self._say("This text is inside a form XObject and cannot be edited." if forms else
                      "No editable text here (it may be part of an image, or converted to outlines).")
            return
        run = min(cands, key=lambda r: (r.bbox[2] - r.bbox[0]) * (r.bbox[3] - r.bbox[1]))
        self.selected_id = None
        self.existing = ExistingSel("text", page, self.tab.doc.page_key(page), run.bbox, run=run)
        self.overlay.set_selection(page, run.bbox, resizable=False, dashed=True)
        if self.panel is not None:
            self.panel.show_existing_text(run.text, run.font_name or "unknown", run.font_size, run.native_ok)
        self.selection_changed.emit(self.existing)
        self.edit_selected_text()

    def _ask_text_edit(self, run: pe.TextRun) -> tuple[str, TextStyle] | None:
        dlg = TextEditDialog(run, self.options.families, self.tab)
        if dlg.exec():
            return dlg.result_values()
        return None

    def edit_selected_text(self) -> None:
        ex = self.existing
        if ex is None or ex.kind != "text":
            return
        res = self._ask_text_edit(ex.run)
        if res is None:
            return
        text, style = res
        if text == ex.run.text and style == default_style(ex.run):
            self._say("No changes.")
            return
        self._run_plan(lambda: self.session.plan_text_edit(ex.key, ex.run, text, style))

    def _press_edit_images(self, page, x, y, tr, px, py, e) -> None:
        sel = self.selected_object()
        if isinstance(sel, ImageBox) or self._hit_layer(self.tab.doc.page_key(page), px, py, (ImageBox,)):
            self._press_select(page, x, y, tr, px, py, e)
            return
        ex = self.existing
        if ex is not None and ex.kind == "image" and ex.page == page:
            h = self.overlay.handle_at(page, ex.rect, x, y)
            if h:
                self._drag = _Drag("existing-resize", page, (x, y), handle=h, rect=ex.rect)
                return
            if point_in_rect(px, py, ex.rect, 0):
                self._drag = _Drag("existing-move", page, (x, y), rect=ex.rect)
                return
        self._with_page_content(page, lambda c: self._pick_image(page, c, px, py))

    def _pick_image(self, page: int, content: pe.PageContent, px: float, py: float) -> None:
        cands = [o for o in content.objects if o.kind == "image" and point_in_rect(px, py, o.bbox, 1.0)]
        if not cands:
            self.deselect()
            self._say("No editable image here.")
            return
        info = min(cands, key=lambda o: (o.bbox[2] - o.bbox[0]) * (o.bbox[3] - o.bbox[1]))
        self.selected_id = None
        self.existing = ExistingSel("image", page, self.tab.doc.page_key(page), info.bbox, info=info)
        self.overlay.set_selection(page, info.bbox, resizable=True, dashed=True)
        if self.panel is not None:
            self.panel.show_existing_image(info.bbox)
        self.selection_changed.emit(self.existing)

    # ------------------------------------------------------------------ tool: erase
    def _press_erase(self, page, x, y, tr, px, py, e) -> None:
        hit = self._hit_layer(self.tab.doc.page_key(page), px, py)
        if hit is not None:
            self.session.remove_object(hit.id)
            return
        self._with_page_content(page, lambda c: self._erase_existing(page, c, px, py))

    def _erase_existing(self, page: int, content: pe.PageContent, px: float, py: float) -> None:
        key = self.tab.doc.page_key(page)
        runs = [r for r in content.runs if point_in_rect(px, py, r.bbox, 2.0)]
        imgs = [o for o in content.objects if o.kind == "image" and point_in_rect(px, py, o.bbox, 1.0)]
        if runs:
            run = min(runs, key=lambda r: (r.bbox[2] - r.bbox[0]) * (r.bbox[3] - r.bbox[1]))
            self._run_plan(lambda: self.session.plan_text_edit(key, run, ""))
        elif imgs:
            info = imgs[0]
            self._run_plan(lambda: self.session.plan_image_delete(key, page, info))
        else:
            self._say("Only added objects, existing images and existing text lines can be erased.")

    # ------------------------------------------------------------------ plans (worker -> confirm -> commit)
    def _run_plan(self, build) -> None:
        if self._planning or self.tab.busy or self.tab.reloading:
            self._say("Please wait for the current operation to finish.")
            return
        self._planning = True
        self._say("Preparing edit…")

        def work(cancel):
            with self.tab.doc_lock:
                return build()

        self.tab.submit(work, self._plan_ready, self._plan_failed)

    def _plan_failed(self, err: Exception) -> None:
        self._planning = False
        if isinstance(err, EditRefused):
            self._inform("Cannot edit this content", str(err))
        else:
            log.warning("edit planning failed: %s", type(err).__name__)
            self._inform("Edit failed", f"The edit could not be prepared ({type(err).__name__}).")
        self._say("Edit not applied.")

    def _confirm_plan(self, plan: EditPlan) -> bool:
        if plan.mode in ("native",) and not plan.warnings:
            return True
        return bool(EditPlanDialog(plan, self.tab).exec())

    def _plan_ready(self, plan: EditPlan) -> None:
        self._planning = False
        if not self._confirm_plan(plan):
            self._say("Edit cancelled.")
            return
        try:
            self.session.commit(plan)
        except EditRefused as e:
            self._inform("Cannot edit this content", str(e))
            return
        self.deselect()
        self._say(f"{plan.title}: done ({plan.mode}).")

    def _inform(self, title: str, text: str) -> None:
        QMessageBox.warning(self.tab, title, text)

    # ------------------------------------------------------------------ markup tools (text-selection based)
    def _finish_markup(self) -> None:
        if self.tool not in MARKUP_TOOLS:
            return
        info = self.view.selection_info()
        if info is None:
            return
        page, a, b = info
        rects = self.tab.renderer.selection_rects_pdf(page, a, b)
        self.view.clear_selection()
        if not rects:
            return
        hl = self.tool == "highlight"
        self.session.add_object(Markup(self.tab.doc.page_key(page), self.tool, tuple(rects),
                                       self.options.markup_color, 0.4 if hl else 1.0),
                                {"highlight": "Highlight", "underline": "Underline", "strike": "Strikethrough"}[self.tool])

    # ------------------------------------------------------------------ properties panel
    def on_option_changed(self, name: str, value) -> None:
        if hasattr(self.options, name):
            setattr(self.options, name, value)

    def on_action(self, action: str) -> None:
        if action == "edit_existing_text":
            self.edit_selected_text()
        elif action == "apply_image" and self.existing is not None and self.panel is not None:
            ex, rect = self.existing, self.panel.existing_image_rect()
            self._run_plan(lambda: self.session.plan_image_edit(ex.key, ex.page, ex.info, rect))
        elif action == "replace_image" and self.existing is not None:
            path = self._ask_image_path()
            loaded = self._load_image(path) if path else None
            if loaded is not None:
                ex = self.existing
                self._run_plan(lambda: self.session.plan_image_replace(ex.key, ex.page, ex.info, loaded[0]))
        elif action == "delete_existing":
            self.delete_selected()

    def on_object_changed(self, changes: dict) -> None:
        o = self.selected_object()
        if o is None:
            return
        new = self._apply_changes(o, changes)
        if new != o:
            self.session.update_object(new, f"Edit {o.kind}", merge="prop:" + ",".join(sorted(changes)))

    def _apply_changes(self, o: EditObject, ch: dict) -> EditObject:
        geom = {k: ch[k] for k in ("x", "y", "w", "h") if k in ch}
        other = {k: v for k, v in ch.items() if k not in geom}
        names = {f.name for f in fields(o)}
        other = {k: v for k, v in other.items() if k in names and k not in ("id", "kind", "page_key")}
        if other:
            o = replace(o, **other)
        if geom and isinstance(o, (TextBox, ImageBox, Shape)):
            l, b, r, t = raw_rect(o)
            w, h = r - l, t - b
            if "x" in geom:
                l = geom["x"]
            if "y" in geom:
                b = geom["y"]
            if "w" in geom:
                nw = geom["w"]
                h = (h * nw / w) if isinstance(o, ImageBox) and o.lock_aspect and w else h
                w = nw
            if "h" in geom:
                nh = geom["h"]
                w = (w * nh / h) if isinstance(o, ImageBox) and o.lock_aspect and h and "w" not in geom else w
                h = nh
            o = with_rect(o, (l, b, l + w, b + h))
        if isinstance(o, TextBox) and ({"text", "size", "family", "bold", "italic", "line_spacing", "w", "h"} & set(ch)):
            o = fit_text_box(o, self.session.catalog)
        return o

    # ------------------------------------------------------------------ delete / copy / paste
    def delete_selected(self) -> bool:
        o = self.selected_object()
        if o is not None:
            self.session.remove_object(o.id)
            return True
        ex = self.existing
        if ex is None:
            return False
        if ex.kind == "image":
            self._run_plan(lambda: self.session.plan_image_delete(ex.key, ex.page, ex.info))
        else:
            self._run_plan(lambda: self.session.plan_text_edit(ex.key, ex.run, ""))
        return True

    def copy_selected(self) -> bool:
        o = self.selected_object()
        if o is None:
            return False
        self._clip = o
        self._say(f"Copied {o.kind}.")
        return True

    def paste(self) -> bool:
        page = self.view.current_page
        key = self.tab.doc.page_key(page)
        if self._clip is not None:
            o = moved(replace(self._clip, id=new_id(), page_key=key), 12.0, -12.0)
            self.session.add_object(o, f"Paste {o.kind}")
            self.select(o)
            return True
        img = QGuiApplication.clipboard().image()
        if img.isNull():
            return False
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        crop = self.tab.doc.page_crop_box(page)
        self._place_image(page, crop[0] + 72.0, crop[3] - 72.0, bytes(buf.data()), img.width(), img.height())
        return True
