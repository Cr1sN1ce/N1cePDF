"""Fusion style + palette theme. Palette-based (not QSS-based) so menus, menu bar and
native dialogs stay reliable on KDE/Wayland and Windows."""
from __future__ import annotations
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

ACCENT = "#3b6cff"

_DARK = dict(window="#1e1f24", base="#17181c", alt="#25262c", text="#e6e6ea", disabled="#7a7b83",
             button="#2b2c33", tip="#2b2c33")
_LIGHT = dict(window="#f2f3f5", base="#ffffff", alt="#eceef1", text="#1d1e22", disabled="#9a9ba2",
              button="#e4e6ea", tip="#ffffff")

_QSS = """
QToolTip { padding: 4px; }
QToolBar { spacing: 4px; padding: 3px; border: 0; }
QTabBar::tab { padding: 6px 14px; }
QSplitter::handle { background: transparent; }
"""


def apply_theme(app: QApplication, name: str = "dark") -> None:
    c = _DARK if name == "dark" else _LIGHT
    app.setStyle("Fusion")
    p = QPalette()
    R, G = QPalette.ColorRole, QPalette.ColorGroup
    for role, key in ((R.Window, "window"), (R.Base, "base"), (R.AlternateBase, "alt"),
                      (R.WindowText, "text"), (R.Text, "text"), (R.ButtonText, "text"),
                      (R.Button, "button"), (R.ToolTipBase, "tip"), (R.ToolTipText, "text")):
        p.setColor(role, QColor(c[key]))
    p.setColor(R.Highlight, QColor(ACCENT))
    p.setColor(R.HighlightedText, QColor("#ffffff"))
    p.setColor(R.Link, QColor(ACCENT))
    p.setColor(R.PlaceholderText, QColor(c["disabled"]))
    for role in (R.Text, R.ButtonText, R.WindowText):
        p.setColor(G.Disabled, role, QColor(c["disabled"]))
    app.setPalette(p)
    app.setStyleSheet(_QSS)
