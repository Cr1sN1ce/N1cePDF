"""Inline SVG icons (stroke style), tinted at runtime so they work in dark and light themes."""
from __future__ import annotations
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_WRAP = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
         'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
         '{}</svg>')

_PATHS = {
    "open": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "save": '<path d="M5 3h11l3 3v15H5z"/><path d="M8 3v6h7V3"/><path d="M8 21v-7h8v7"/>',
    "undo": '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
    "redo": '<path d="M15 14l5-5-5-5"/><path d="M20 9H10a6 6 0 0 0 0 12h3"/>',
    "prev": '<path d="M6 15l6-6 6 6"/>',
    "next": '<path d="M6 9l6 6 6-6"/>',
    "zoom_in": '<circle cx="11" cy="11" r="7"/><path d="M21 21l-5-5M11 8v6M8 11h6"/>',
    "zoom_out": '<circle cx="11" cy="11" r="7"/><path d="M21 21l-5-5M8 11h6"/>',
    "fit_width": '<path d="M3 5v14M21 5v14M7 12h10M7 12l3-3M7 12l3 3M17 12l-3-3M17 12l-3 3"/>',
    "fit_page": '<rect x="6" y="3" width="12" height="18" rx="1"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M21 21l-5-5"/>',
    "sidebar": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
    "rotate": '<path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 4v5h-5"/>',
    "cursor": '<path d="M5 3l14 7-6 2-2 6z"/>',
    "select": '<path d="M12 3v18M3 12h18M12 3l-3 3M12 3l3 3M12 21l-3-3M12 21l3-3M3 12l3-3M3 12l3 3M21 12l-3-3M21 12l-3 3"/>',
    "text_edit": '<path d="M3 6V4h11v2M8.5 4v12M6 16h5"/><path d="M13 21l1-4 7-7 3 3-7 7z"/>',
    "text_add": '<path d="M3 6V4h13v2M9.5 4v14M7 18h5"/><path d="M18 14v7M14.5 17.5h7"/>',
    "image_edit": '<rect x="2" y="4" width="14" height="13" rx="2"/><circle cx="7" cy="9" r="1.5"/><path d="M2 14l4-4 5 5"/><path d="M13 22l1-4 6-6 3 3-6 6z"/>',
    "image_add": '<rect x="2" y="4" width="14" height="13" rx="2"/><circle cx="7" cy="9" r="1.5"/><path d="M2 14l4-4 5 5M19 15v7M15.5 18.5h7"/>',
    "highlight": '<path d="M4 21h16"/><path d="M14 4l6 6-8 8H6v-6z"/>',
    "underline": '<path d="M6 4v7a6 6 0 0 0 12 0V4M4 21h16"/>',
    "strike": '<path d="M4 12h16M16 6c-1-1.5-3-2-5-2-3 0-5 1.5-5 3.5S8 11 12 12M8 18c1 1.5 3 2 5 2 3 0 5-1.5 5-3.5"/>',
    "draw": '<path d="M3 21c3-1 4-6 7-6s2 4 5 2 3-8 6-12"/>',
    "shapes": '<rect x="2" y="11" width="10" height="10" rx="1"/><circle cx="16" cy="8" r="5"/>',
    "comment": '<path d="M4 4h16v12H9l-5 5z"/>',
    "erase": '<path d="M3 17l8-12 10 7-8 8H7z"/><path d="M14 21h7"/>',
    "logo": '<path d="M6 2h9l5 5v15H6z"/><path d="M15 2v5h5"/><path d="M9 13h8M9 17h8M9 9h3"/>',
}
_cache: dict[tuple, QIcon] = {}


def make_icon(name: str, color: str = "#e6e6ea", size: int = 48) -> QIcon:
    key = (name, color, size)
    if key in _cache:
        return _cache[key]
    svg = _WRAP.format(_PATHS[name]).replace("currentColor", str(color))
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    icon = QIcon(pm)
    _cache[key] = icon
    return icon
