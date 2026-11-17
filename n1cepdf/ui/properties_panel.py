"""Contextual Properties panel (right side). Shows tool options, or the editable properties of the
selected object. All coordinates are PDF user-space points (origin bottom-left of the unrotated page)."""
from __future__ import annotations
from dataclasses import dataclass, field
from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFormLayout, QLabel,
                               QPlainTextEdit, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)
from ..edit.objects import Comment, EditObject, ImageBox, Ink, Markup, Shape, TextBox
from .edit_tools import SHAPE_KINDS, TOOL_BY_ID

Color = tuple[float, float, float]


@dataclass
class ToolOptions:
    text_family: str = "Liberation Sans"
    text_size: float = 14.0
    text_bold: bool = False
    text_italic: bool = False
    text_color: Color = (0.0, 0.0, 0.0)
    shape: str = "rect"
    stroke: Color = (0.85, 0.1, 0.1)
    fill: Color | None = None
    width: float = 2.0
    opacity: float = 1.0
    ink_color: Color = (0.85, 0.1, 0.1)
    ink_width: float = 2.0
    markup_color: Color = (1.0, 0.9, 0.0)
    author: str = "N1cePDF"
    families: list[str] = field(default_factory=list)


class ColorButton(QPushButton):
    color_changed = Signal(object)

    def __init__(self, color: Color | None = (0.0, 0.0, 0.0), parent: QWidget | None = None):
        super().__init__(parent)
        self._c: Color | None = color
        self.setFixedWidth(64)
        self.clicked.connect(self.pick)
        self._paint()

    def color(self) -> Color | None:
        return self._c

    def set_color(self, c: Color | None) -> None:
        self._c = c
        self._paint()

    def _paint(self) -> None:
        if self._c is None:
            self.setStyleSheet("")
            self.setText("none")
        else:
            qc = QColor.fromRgbF(*self._c)
            self.setText("")
            self.setStyleSheet(f"background-color: {qc.name()}; border: 1px solid #888;")

    def pick(self) -> None:
        start = QColor.fromRgbF(*(self._c or (1.0, 1.0, 1.0)))
        col = QColorDialog.getColor(start, self, "Choose colour")
        if col.isValid():
            self._c = (col.redF(), col.greenF(), col.blueF())
            self._paint()
            self.color_changed.emit(self._c)


def _spin(lo: float, hi: float, step: float = 1.0, decimals: int = 1, suffix: str = "") -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(decimals)
    s.setKeyboardTracking(False)
    if suffix:
        s.setSuffix(suffix)
    return s


class PropertiesPanel(QWidget):
    object_changed = Signal(dict)       # {field: value} for the selected added object
    option_changed = Signal(str, object)
    action_requested = Signal(str)      # edit_existing_text | apply_image | replace_image | delete_existing

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumWidth(230)
        self._loading = False
        self._object_id: str | None = None
        self.title = QLabel("Properties")
        self.title.setStyleSheet("font-weight: 600;")
        self.stack = QStackedWidget()
        lay = QVBoxLayout(self)
        lay.addWidget(self.title)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.stack)
        lay.addWidget(self.scroll, 1)
        self.families: list[str] = []

        self.page_tool = self._build_tool_page()
        self.page_text = self._build_text_page()
        self.page_image = self._build_image_page()
        self.page_shape = self._build_shape_page()
        self.page_simple = self._build_simple_page()
        self.page_ex_text = self._build_existing_text_page()
        self.page_ex_image = self._build_existing_image_page()
        for p in (self.page_tool, self.page_text, self.page_image, self.page_shape, self.page_simple,
                  self.page_ex_text, self.page_ex_image):
            self.stack.addWidget(p)
        self.show_tool_options("browse", ToolOptions())

    # ------------------------------------------------------------ helpers
    def set_families(self, families: list[str]) -> None:
        self.families = families
        for combo in (self.tx_family, self.opt_family):
            combo.blockSignals(True)
            cur = combo.currentText()
            combo.clear()
            combo.addItems(families)
            if cur:
                combo.setCurrentText(cur)
            combo.blockSignals(False)

    def _emit(self, **kw) -> None:
        if not self._loading:
            self.object_changed.emit(kw)

    def _geom_rows(self, form: QFormLayout):
        sx, sy = _spin(-20000, 20000, 1, 1, " pt"), _spin(-20000, 20000, 1, 1, " pt")
        sw, sh = _spin(1, 20000, 1, 1, " pt"), _spin(1, 20000, 1, 1, " pt")
        for label, w, key in (("X", sx, "x"), ("Y", sy, "y"), ("Width", sw, "w"), ("Height", sh, "h")):
            form.addRow(label, w)
            w.valueChanged.connect(lambda v, k=key: self._emit(**{k: v}))
        return sx, sy, sw, sh

    # ------------------------------------------------------------ pages
    def _build_tool_page(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.opt_hint = QLabel("")
        self.opt_hint.setWordWrap(True)
        f.addRow(self.opt_hint)
        self.opt_family = QComboBox()
        self.opt_size = _spin(1, 400, 0.5, 1, " pt")
        self.opt_bold, self.opt_italic = QCheckBox("Bold"), QCheckBox("Italic")
        self.opt_text_color = ColorButton((0, 0, 0))
        self.opt_shape = QComboBox()
        self.opt_shape.addItems(list(SHAPE_KINDS))
        self.opt_stroke = ColorButton((0.85, 0.1, 0.1))
        self.opt_fill = ColorButton(None)
        self.opt_nofill = QCheckBox("No fill")
        self.opt_nofill.setChecked(True)
        self.opt_width = _spin(0.25, 50, 0.25, 2, " pt")
        self.opt_opacity = _spin(5, 100, 5, 0, " %")
        self.opt_markup_color = ColorButton((1.0, 0.9, 0.0))
        self.opt_ink_color = ColorButton((0.85, 0.1, 0.1))
        self.opt_ink_width = _spin(0.5, 40, 0.5, 1, " pt")
        rows = [("Font", self.opt_family, "text"), ("Size", self.opt_size, "text"), ("", self.opt_bold, "text"),
                ("", self.opt_italic, "text"), ("Colour", self.opt_text_color, "text"),
                ("Shape", self.opt_shape, "shape"), ("Stroke", self.opt_stroke, "shape"),
                ("Fill", self.opt_fill, "shape"), ("", self.opt_nofill, "shape"),
                ("Stroke width", self.opt_width, "shape"), ("Opacity", self.opt_opacity, "shape"),
                ("Colour", self.opt_markup_color, "markup"),
                ("Colour", self.opt_ink_color, "ink"), ("Width", self.opt_ink_width, "ink")]
        self._opt_rows = rows
        for label, widget, _g in rows:
            f.addRow(label, widget)
        self._opt_form = f
        self.opt_family.currentTextChanged.connect(lambda v: self._opt("text_family", v))
        self.opt_size.valueChanged.connect(lambda v: self._opt("text_size", v))
        self.opt_bold.toggled.connect(lambda v: self._opt("text_bold", v))
        self.opt_italic.toggled.connect(lambda v: self._opt("text_italic", v))
        self.opt_text_color.color_changed.connect(lambda c: self._opt("text_color", c))
        self.opt_shape.currentTextChanged.connect(lambda v: self._opt("shape", v))
        self.opt_stroke.color_changed.connect(lambda c: self._opt("stroke", c))
        self.opt_fill.color_changed.connect(lambda c: (self.opt_nofill.setChecked(False), self._opt("fill", c)))
        self.opt_nofill.toggled.connect(lambda v: self._opt("fill", None if v else (self.opt_fill.color() or (1, 1, 0))))
        self.opt_width.valueChanged.connect(lambda v: self._opt("width", v))
        self.opt_opacity.valueChanged.connect(lambda v: self._opt("opacity", v / 100.0))
        self.opt_markup_color.color_changed.connect(lambda c: self._opt("markup_color", c))
        self.opt_ink_color.color_changed.connect(lambda c: self._opt("ink_color", c))
        self.opt_ink_width.valueChanged.connect(lambda v: self._opt("ink_width", v))
        return w

    def _opt(self, name: str, value) -> None:
        if not self._loading:
            self.option_changed.emit(name, value)

    def _build_text_page(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.tx_content = QPlainTextEdit()
        self.tx_content.setMinimumHeight(90)
        self.tx_content.textChanged.connect(lambda: self._emit(text=self.tx_content.toPlainText()))
        f.addRow("Content", self.tx_content)
        self.tx_family = QComboBox()
        self.tx_family.currentTextChanged.connect(lambda v: self._emit(family=v))
        f.addRow("Font", self.tx_family)
        self.tx_size = _spin(1, 400, 0.5, 1, " pt")
        self.tx_size.valueChanged.connect(lambda v: self._emit(size=v))
        f.addRow("Size", self.tx_size)
        self.tx_bold, self.tx_italic = QCheckBox("Bold"), QCheckBox("Italic")
        self.tx_bold.toggled.connect(lambda v: self._emit(bold=v))
        self.tx_italic.toggled.connect(lambda v: self._emit(italic=v))
        f.addRow("", self.tx_bold)
        f.addRow("", self.tx_italic)
        self.tx_color = ColorButton()
        self.tx_color.color_changed.connect(lambda c: self._emit(color=c))
        f.addRow("Colour", self.tx_color)
        self.tx_align = QComboBox()
        self.tx_align.addItems(["left", "center", "right"])
        self.tx_align.currentTextChanged.connect(lambda v: self._emit(align=v))
        f.addRow("Alignment", self.tx_align)
        self.tx_spacing = _spin(0.8, 4.0, 0.1, 2)
        self.tx_spacing.valueChanged.connect(lambda v: self._emit(line_spacing=v))
        f.addRow("Line spacing", self.tx_spacing)
        self.tx_rotation = _spin(-360, 360, 5, 1, "°")
        self.tx_rotation.valueChanged.connect(lambda v: self._emit(rotation=v))
        f.addRow("Rotation", self.tx_rotation)
        self.tx_opacity = _spin(5, 100, 5, 0, " %")
        self.tx_opacity.valueChanged.connect(lambda v: self._emit(opacity=v / 100.0))
        f.addRow("Opacity", self.tx_opacity)
        self.tx_char_spacing = _spin(-30,100,0.1,2," pt")
        self.tx_char_spacing.valueChanged.connect(lambda v: self._emit(char_spacing=v))
        f.addRow("Letter spacing",self.tx_char_spacing)
        self.tx_horizontal_scale = _spin(25,400,1,1," %")
        self.tx_horizontal_scale.valueChanged.connect(lambda v: self._emit(horizontal_scale=v))
        f.addRow("Text width",self.tx_horizontal_scale)
        self.tx_ocr_background = QComboBox()
        self.tx_ocr_background.addItem("Auto: solid background only","auto")
        self.tx_ocr_background.addItem("Local inpainting (review artifacts)","inpaint")
        self.tx_ocr_background.currentIndexChanged.connect(
            lambda _: self._emit(ocr_background=self.tx_ocr_background.currentData()))
        f.addRow("Scan background",self.tx_ocr_background)
        self.tx_page_raster = QCheckBox("Allow affected-page rasterization")
        self.tx_page_raster.setToolTip("Page-wide permission: applies to all OCR blocks on this page. Clearing it revokes the fallback for the whole page.")
        self.tx_page_raster.toggled.connect(lambda v: self._emit(ocr_allow_page_raster=v))
        f.addRow(self.tx_page_raster)
        self.tx_split_ocr = QPushButton("Split OCR block at cursor")
        self.tx_split_ocr.setToolTip("Place the caret between words. Legacy OCR without word positions can be replaced using the OCR dialog checkbox.")
        self.tx_split_ocr.clicked.connect(lambda: self.action_requested.emit("split_ocr"))
        f.addRow(self.tx_split_ocr)
        self.tx_merge_ocr = QPushButton("Merge neighbouring OCR blocks…")
        self.tx_merge_ocr.clicked.connect(lambda: self.action_requested.emit("merge_ocr"))
        f.addRow(self.tx_merge_ocr)
        self._text_form = f
        self.tx_geom = self._geom_rows(f)
        return w

    def _build_image_page(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.im_geom = self._geom_rows(f)
        self.im_rotation = _spin(-360, 360, 5, 1, "°")
        self.im_rotation.valueChanged.connect(lambda v: self._emit(rotation=v))
        f.addRow("Rotation", self.im_rotation)
        self.im_lock = QCheckBox("Lock aspect ratio")
        self.im_lock.toggled.connect(lambda v: self._emit(lock_aspect=v))
        f.addRow(self.im_lock)
        self.im_opacity = _spin(5, 100, 5, 0, " %")
        self.im_opacity.valueChanged.connect(lambda v: self._emit(opacity=v / 100.0))
        f.addRow("Opacity", self.im_opacity)
        return w

    def _build_shape_page(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.sh_geom = self._geom_rows(f)
        self.sh_stroke = ColorButton()
        self.sh_stroke.color_changed.connect(lambda c: self._emit(stroke=c))
        f.addRow("Stroke", self.sh_stroke)
        self.sh_fill = ColorButton(None)
        self.sh_nofill = QCheckBox("No fill")
        self.sh_fill.color_changed.connect(lambda c: (self.sh_nofill.setChecked(False), self._emit(fill=c)))
        self.sh_nofill.toggled.connect(lambda v: self._emit(fill=None if v else (self.sh_fill.color() or (1.0, 1.0, 0.0))))
        f.addRow("Fill", self.sh_fill)
        f.addRow("", self.sh_nofill)
        self.sh_width = _spin(0.25, 50, 0.25, 2, " pt")
        self.sh_width.valueChanged.connect(lambda v: self._emit(width=v))
        f.addRow("Stroke width", self.sh_width)
        self.sh_opacity = _spin(5, 100, 5, 0, " %")
        self.sh_opacity.valueChanged.connect(lambda v: self._emit(opacity=v / 100.0))
        f.addRow("Opacity", self.sh_opacity)
        return w

    def _build_simple_page(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.sm_color = ColorButton()
        self.sm_color.color_changed.connect(lambda c: self._emit(color=c))
        f.addRow("Colour", self.sm_color)
        self.sm_opacity = _spin(5, 100, 5, 0, " %")
        self.sm_opacity.valueChanged.connect(lambda v: self._emit(opacity=v / 100.0))
        f.addRow("Opacity", self.sm_opacity)
        self.sm_width = _spin(0.5, 40, 0.5, 1, " pt")
        self.sm_width.valueChanged.connect(lambda v: self._emit(width=v))
        f.addRow("Width", self.sm_width)
        self.sm_text = QPlainTextEdit()
        self.sm_text.setMinimumHeight(80)
        self.sm_text.textChanged.connect(lambda: self._emit(text=self.sm_text.toPlainText()))
        f.addRow("Note", self.sm_text)
        self._simple_form = f
        return w

    def _build_existing_text_page(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.ex_text = QLabel("")
        self.ex_text.setWordWrap(True)
        self.ex_font = QLabel("")
        self.ex_mode = QLabel("")
        self.ex_mode.setWordWrap(True)
        f.addRow("Text", self.ex_text)
        f.addRow("Font", self.ex_font)
        f.addRow(self.ex_mode)
        b = QPushButton("Edit text…")
        b.clicked.connect(lambda: self.action_requested.emit("edit_existing_text"))
        f.addRow(b)
        self.ex_edit_button = b
        return w

    def _build_existing_image_page(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.ei_geom = self._geom_rows(f)
        for b_text, act in (("Apply position / size", "apply_image"), ("Replace image…", "replace_image"),
                            ("Delete image", "delete_existing")):
            b = QPushButton(b_text)
            b.clicked.connect(lambda _c=False, a=act: self.action_requested.emit(a))
            f.addRow(b)
        note = QLabel("Changes the existing image object's placement; image data is untouched.")
        note.setWordWrap(True)
        f.addRow(note)
        return w

    # ------------------------------------------------------------ public API
    def show_tool_options(self, tool_id: str, o: ToolOptions) -> None:
        self._object_id = None
        self._loading = True
        try:
            t = TOOL_BY_ID[tool_id]
            self.title.setText(t.label)
            self.opt_hint.setText(t.hint)
            groups = {"add_text": {"text"}, "shapes": {"shape"}, "highlight": {"markup"}, "underline": {"markup"},
                      "strike": {"markup"}, "draw": {"ink"}}.get(tool_id, set())
            for _label, widget, g in self._opt_rows:
                self._opt_form.setRowVisible(widget, g in groups)
            if o.families:
                self.opt_family.setCurrentText(o.text_family)
            self.opt_size.setValue(o.text_size)
            self.opt_bold.setChecked(o.text_bold)
            self.opt_italic.setChecked(o.text_italic)
            self.opt_text_color.set_color(o.text_color)
            self.opt_shape.setCurrentText(o.shape)
            self.opt_stroke.set_color(o.stroke)
            self.opt_fill.set_color(o.fill)
            self.opt_nofill.setChecked(o.fill is None)
            self.opt_width.setValue(o.width)
            self.opt_opacity.setValue(o.opacity * 100)
            self.opt_markup_color.set_color(o.markup_color)
            self.opt_ink_color.set_color(o.ink_color)
            self.opt_ink_width.setValue(o.ink_width)
            self.stack.setCurrentWidget(self.page_tool)
        finally:
            self._loading = False

    def _set_geom(self, widgets, rect: tuple[float, float, float, float]) -> None:
        l, b, r, t = rect
        for w_, v in zip(widgets, (l, b, r - l, t - b)):
            w_.setValue(v)

    @staticmethod
    def _sync_text(editor: QPlainTextEdit, text: str, same_object: bool) -> None:
        # Model notifications are synchronous with textChanged. Replacing identical text
        # here resets the active cursor, selection, IME state and the editor's undo stack.
        if same_object and editor.toPlainText() == text:
            return
        cursor = editor.textCursor()
        position, anchor = cursor.position(), cursor.anchor()
        vertical = editor.verticalScrollBar().value()
        horizontal = editor.horizontalScrollBar().value()
        with QSignalBlocker(editor):
            editor.setPlainText(text)
            if same_object:
                # External changes (e.g. document undo) may shorten the text. Qt cursor
                # positions count UTF-16 code units, so use the document's character count.
                end = editor.document().characterCount() - 1
                cursor = editor.textCursor()
                cursor.setPosition(min(anchor, end))
                cursor.setPosition(min(position, end), QTextCursor.MoveMode.KeepAnchor)
                editor.setTextCursor(cursor)
                editor.verticalScrollBar().setValue(vertical)
                editor.horizontalScrollBar().setValue(horizontal)

    def show_object(self, o: EditObject) -> None:
        same_object = self._object_id == o.id
        self._object_id = o.id
        self._loading = True
        try:
            self.title.setText({"text": "Text", "image": "Image", "shape": "Shape", "markup": "Markup",
                                "comment": "Comment", "ink": "Drawing"}[o.kind])
            if isinstance(o, TextBox):
                self._sync_text(self.tx_content, o.text, same_object)
                self.tx_family.setCurrentText(o.family)
                self.tx_size.setValue(o.size)
                self.tx_bold.setChecked(o.bold)
                self.tx_italic.setChecked(o.italic)
                self.tx_color.set_color(o.color)
                self.tx_align.setCurrentText(o.align)
                self.tx_spacing.setValue(o.line_spacing)
                self.tx_rotation.setValue(o.rotation)
                self.tx_opacity.setValue(o.opacity * 100)
                self.tx_char_spacing.setValue(o.char_spacing)
                self.tx_horizontal_scale.setValue(o.horizontal_scale)
                self.tx_ocr_background.setCurrentIndex(max(0,self.tx_ocr_background.findData(o.ocr_background)))
                self.tx_page_raster.setChecked(o.ocr_allow_page_raster)
                self._text_form.setRowVisible(self.tx_ocr_background,o.ocr_source is not None)
                self._text_form.setRowVisible(self.tx_page_raster,o.ocr_source is not None)
                self._text_form.setRowVisible(self.tx_split_ocr,o.ocr_source is not None)
                self._text_form.setRowVisible(self.tx_merge_ocr,o.ocr_source is not None)
                self.tx_split_ocr.setEnabled(len(o.ocr_words)>1)
                self.tx_merge_ocr.setEnabled(bool(o.ocr_words))
                self._set_geom(self.tx_geom, (min(o.x0, o.x1), min(o.y0, o.y1), max(o.x0, o.x1), max(o.y0, o.y1)))
                self.stack.setCurrentWidget(self.page_text)
            elif isinstance(o, ImageBox):
                self._set_geom(self.im_geom, (min(o.x0, o.x1), min(o.y0, o.y1), max(o.x0, o.x1), max(o.y0, o.y1)))
                self.im_rotation.setValue(o.rotation)
                self.im_lock.setChecked(o.lock_aspect)
                self.im_opacity.setValue(o.opacity * 100)
                self.stack.setCurrentWidget(self.page_image)
            elif isinstance(o, Shape):
                self._set_geom(self.sh_geom, (min(o.x0, o.x1), min(o.y0, o.y1), max(o.x0, o.x1), max(o.y0, o.y1)))
                self.sh_stroke.set_color(o.stroke)
                self.sh_fill.set_color(o.fill)
                self.sh_nofill.setChecked(o.fill is None)
                self.sh_width.setValue(o.width)
                self.sh_opacity.setValue(o.opacity * 100)
                self.stack.setCurrentWidget(self.page_shape)
            else:
                self.sm_color.set_color(o.color)
                is_mark, is_ink, is_note = isinstance(o, Markup), isinstance(o, Ink), isinstance(o, Comment)
                self.sm_opacity.setValue((o.opacity if not is_note else 1.0) * 100)
                if is_ink:
                    self.sm_width.setValue(o.width)
                if is_note:
                    self._sync_text(self.sm_text, o.text, same_object)
                self._simple_form.setRowVisible(self.sm_opacity, not is_note)
                self._simple_form.setRowVisible(self.sm_width, is_ink)
                self._simple_form.setRowVisible(self.sm_text, is_note)
                self.stack.setCurrentWidget(self.page_simple)
        finally:
            self._loading = False

    def show_existing_text(self, text: str, font: str, size: float, native: bool) -> None:
        self._object_id = None
        self.title.setText("Existing text")
        self.ex_text.setText(text)
        self.ex_font.setText(f"{font}, {size:.1f} pt")
        self.ex_mode.setText("Text-only changes are made in place (same font)." if native else
                             "Embedded font: edits use remove-and-replace with a substituted font.")
        self.stack.setCurrentWidget(self.page_ex_text)

    def show_existing_image(self, rect: tuple[float, float, float, float]) -> None:
        self._object_id = None
        self._loading = True
        try:
            self.title.setText("Existing image")
            self._set_geom(self.ei_geom, rect)
            self.stack.setCurrentWidget(self.page_ex_image)
        finally:
            self._loading = False

    def existing_image_rect(self) -> tuple[float, float, float, float]:
        x, y, w, h = (s.value() for s in self.ei_geom)
        return (x, y, x + w, y + h)

    def focus_text_editor(self) -> None:
        self.stack.setCurrentWidget(self.page_text)
        self.tx_content.setFocus()
        self.tx_content.selectAll()
