"""Welcome screen shown whenever no document is open."""
from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout, QWidget)
from ..services.recent import RecentFiles
from .icons import make_icon
from .theme import ACCENT

DESCRIPTION = ("View PDFs with fast lazy rendering, thumbnails, bookmarks and full-text search; "
               "select and copy text; rotate, insert, delete, extract and merge pages. "
               "Everything runs locally — no network, no accounts, no telemetry.")


class WelcomeWidget(QWidget):
    open_requested = Signal()
    blank_requested = Signal()
    recent_requested = Signal(str)

    def __init__(self, recent: RecentFiles, parent: QWidget | None = None):
        super().__init__(parent)
        self._recent = recent
        outer = QVBoxLayout(self)
        outer.addStretch(1)
        col = QVBoxLayout()
        col.setSpacing(12)
        col.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        logo = QLabel()
        logo.setPixmap(make_icon("logo", ACCENT, 96).pixmap(96, 96))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("N1cePDF")
        title.setStyleSheet("font-size: 34px; font-weight: 600;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc = QLabel(DESCRIPTION)
        desc.setWordWrap(True)
        desc.setMaximumWidth(520)
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)

        row = QHBoxLayout()
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.open_button = QPushButton("Open PDF…  (Ctrl+O)")
        self.blank_button = QPushButton("Create Blank PDF")
        for b in (self.open_button, self.blank_button):
            b.setMinimumHeight(36)
            b.setMinimumWidth(170)
            row.addWidget(b)
        self.open_button.clicked.connect(self.open_requested)
        self.blank_button.clicked.connect(self.blank_requested)

        hint = QLabel("…or drag and drop a PDF file anywhere in this window")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.recent_label = QLabel("Recent documents")
        self.recent_label.setStyleSheet("font-weight: 600;")
        self.recent_list = QListWidget()
        self.recent_list.setMaximumWidth(520)
        self.recent_list.setMinimumWidth(420)
        self.recent_list.setMaximumHeight(200)
        self.recent_list.itemActivated.connect(self._activated)
        self.recent_list.itemClicked.connect(self._activated)
        self.clear_button = QPushButton("Clear recent list")
        self.clear_button.clicked.connect(self._clear)

        for w in (logo, title, desc):
            col.addWidget(w)
        col.addLayout(row)
        col.addWidget(hint)
        col.addWidget(self.recent_label, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(self.recent_list, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(self.clear_button, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addLayout(col)
        outer.addStretch(2)
        self.refresh()

    def refresh(self) -> None:
        self.recent_list.clear()
        for p in self._recent.list():
            item = QListWidgetItem(Path(p).name)
            item.setToolTip(p)
            item.setData(Qt.ItemDataRole.UserRole, p)
            self.recent_list.addItem(item)
        has = self.recent_list.count() > 0
        self.recent_label.setVisible(has)
        self.recent_list.setVisible(has)
        self.clear_button.setVisible(has)

    def _activated(self, item: QListWidgetItem) -> None:
        self.recent_requested.emit(item.data(Qt.ItemDataRole.UserRole))

    def _clear(self) -> None:
        self._recent.clear()
        self.refresh()
