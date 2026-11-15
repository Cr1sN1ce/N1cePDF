"""Dialogs: edit existing text (content + style) and preview/confirm a content-changing edit plan."""
from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel,
                               QPlainTextEdit, QVBoxLayout)
from ..edit.pdfium_edit import TextRun
from ..edit.session import EditPlan, TextStyle, default_style
from .properties_panel import ColorButton


class TextEditDialog(QDialog):
    def __init__(self, run: TextRun, families: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit text")
        self._run = run
        base = default_style(run)
        form = QFormLayout()
        info = QLabel(f"Original font: {run.font_name or 'unknown'}, {run.font_size:.1f} pt"
                      f"{' (embedded)' if run.embedded else ''}")
        info.setWordWrap(True)
        form.addRow(info)
        self.content = QPlainTextEdit(run.text)
        self.content.setMinimumSize(360, 90)
        form.addRow("Text", self.content)
        self.family = QComboBox()
        self.family.addItems(families)
        self.family.setCurrentText(base.family)
        form.addRow("Font", self.family)
        self.size = QDoubleSpinBox()
        self.size.setRange(1, 400)
        self.size.setDecimals(2)
        self.size.setValue(base.size)
        form.addRow("Size", self.size)
        self.bold, self.italic = QCheckBox("Bold"), QCheckBox("Italic")
        self.bold.setChecked(base.bold)
        self.italic.setChecked(base.italic)
        form.addRow("", self.bold)
        form.addRow("", self.italic)
        self.color = ColorButton(base.color)
        form.addRow("Colour", self.color)
        self.align = QComboBox()
        self.align.addItems(["left", "center", "right"])
        form.addRow("Alignment", self.align)
        self.spacing = QDoubleSpinBox()
        self.spacing.setRange(0.8, 4.0)
        self.spacing.setSingleStep(0.1)
        self.spacing.setValue(base.line_spacing)
        form.addRow("Line spacing", self.spacing)
        note = QLabel("Changing only the words keeps the original font when it is a standard font. Any style "
                      "change uses remove-and-replace with an embedded font. You will see a preview first.")
        note.setWordWrap(True)
        form.addRow(note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(bb)

    def result_values(self) -> tuple[str, TextStyle]:
        style = TextStyle(self.family.currentText(), round(self.size.value(), 2), self.bold.isChecked(),
                          self.italic.isChecked(), tuple(round(c, 3) for c in (self.color.color() or (0, 0, 0))),
                          self.align.currentText(), round(self.spacing.value(), 2))
        return self.content.toPlainText(), style


class EditPlanDialog(QDialog):
    """Shows exactly what an edit will do, and every warning, before anything is applied."""

    def __init__(self, plan: EditPlan, parent=None):
        super().__init__(parent)
        self.setWindowTitle(plan.title)
        lay = QVBoxLayout(self)
        mode = {"native": "In-place edit", "replace": "Remove and replace", "delete-text": "Delete text",
                "image-transform": "Move / resize image", "image-delete": "Delete image",
                "image-replace": "Replace image"}.get(plan.mode, plan.mode)
        head = QLabel(f"<b>{mode}</b>")
        lay.addWidget(head)
        if plan.old_text or plan.new_text:
            lab = QLabel(f"Old:  {plan.old_text}\nNew:  {plan.new_text or '(deleted)'}")
            lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lab.setWordWrap(True)
            lay.addWidget(lab)
        for n in plan.notes:
            lab = QLabel("• " + n)
            lab.setWordWrap(True)
            lay.addWidget(lab)
        for w in plan.warnings:
            lab = QLabel("⚠ " + w)
            lab.setWordWrap(True)
            lab.setStyleSheet("color: #e0a030;")
            lay.addWidget(lab)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Apply")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
