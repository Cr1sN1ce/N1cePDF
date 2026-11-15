"""Undoable commands. Each changes the document model structurally and can be reversed exactly."""
from __future__ import annotations
import pikepdf
from ..core import page_manager as pm
from . import pdfium_edit as pe
from .objects import EditObject, PageKey
from .undo import Command


class AddObject(Command):
    def __init__(self, session, obj: EditObject, text: str | None = None, index: int | None = None):
        self.session, self.obj, self.index = session, obj, index
        self.text = text or f"Add {obj.kind}"

    def do(self) -> None:
        self.session.layer.add(self.obj, self.index)

    def undo(self) -> None:
        self.session.layer.remove(self.obj.id)


class RemoveObject(Command):
    def __init__(self, session, oid: str, text: str | None = None):
        self.session, self.oid = session, oid
        self.obj = session.layer.get(oid)
        self.text = text or f"Delete {self.obj.kind}"
        self._index = 0

    def do(self) -> None:
        self.obj, self._index = self.session.layer.remove(self.oid)

    def undo(self) -> None:
        self.session.layer.add(self.obj, self._index)


class UpdateObject(Command):
    def __init__(self, session, before: EditObject, after: EditObject, text: str = "Edit object",
                 merge: str | None = None):
        self.session, self.before, self.after, self.text, self._merge = session, before, after, text, merge

    def do(self) -> None:
        self.session.layer.replace(self.after)

    def undo(self) -> None:
        self.session.layer.replace(self.before)

    def merge_key(self):
        return (self.after.id, self._merge) if self._merge else None

    def merge_from(self, newer: "UpdateObject") -> None:
        self.after = newer.after


class RotatePage(Command):
    structural = True

    def __init__(self, session, key: PageKey, degrees: int):
        self.session, self.key, self.degrees = session, key, degrees
        self.text = "Rotate page"

    def _rot(self, deg: int) -> None:
        i = self.session.doc.index_of_key(self.key)
        if i is None:
            raise RuntimeError("page no longer exists")
        pm.rotate_pages(self.session.doc, [i], deg)

    def do(self) -> None:
        self._rot(self.degrees)

    def undo(self) -> None:
        self._rot(-self.degrees)


class InsertBlankPage(Command):
    structural = True

    def __init__(self, session, index: int, size: tuple[float, float]):
        self.session, self.index, self.size = session, index, size
        self.text = "Insert page"
        self._page: pikepdf.Page | None = None  # same page object is re-inserted on redo (stable key)

    def do(self) -> None:
        pdf = self.session.doc.pdf
        if self._page is None:
            self._page = pikepdf.Page(pikepdf.Dictionary(
                Type=pikepdf.Name.Page, MediaBox=[0, 0, self.size[0], self.size[1]],
                Resources=pikepdf.Dictionary(), Contents=pdf.make_stream(b"")))
        pdf.pages.insert(self.index, self._page)
        self.session.doc.mark_modified()

    def undo(self) -> None:
        del self.session.doc.pdf.pages[self.index]
        self.session.doc.mark_modified()


class DeletePage(Command):
    structural = True

    def __init__(self, session, key: PageKey):
        self.session, self.key = session, key
        self.text = "Delete page"
        self._page: pikepdf.Page | None = None
        self._index = 0

    def do(self) -> None:
        doc = self.session.doc
        if doc.page_count < 2:
            raise ValueError("A document must keep at least one page.")
        i = doc.index_of_key(self.key)
        if i is None:
            raise RuntimeError("page no longer exists")
        self._index, self._page = i, doc.pdf.pages[i]
        del doc.pdf.pages[i]
        doc.mark_modified()

    def undo(self) -> None:
        self.session.doc.pdf.pages.insert(self._index, self._page)
        self.session.doc.mark_modified()


class ReorderPages(Command):
    structural = True

    def __init__(self, session, new_order: list[int]):
        self.session, self.order = session, list(new_order)
        self.text = "Reorder pages"

    def do(self) -> None:
        pm.reorder_pages(self.session.doc, self.order)

    def undo(self) -> None:
        inverse = [0] * len(self.order)
        for new_pos, old_idx in enumerate(self.order):
            inverse[old_idx] = new_pos
        pm.reorder_pages(self.session.doc, inverse)


class ContentSwap(Command):
    """Apply a verified PDFium-regenerated page content (text/image edit) to one page; undo restores
    the exact previous /Contents and /Resources objects."""
    structural = True

    def __init__(self, session, key: PageKey, new_bytes: bytes, text: str):
        self.session, self.key, self.new_bytes, self.text = session, key, new_bytes, text
        self._swap: pe.PageSwap | None = None

    def do(self) -> None:
        i = self.session.doc.index_of_key(self.key)
        if i is None:
            raise RuntimeError("page no longer exists")
        self._swap = pe.graft_page(self.session.doc.pdf, i, self.new_bytes)
        self.session.doc.mark_modified()

    def undo(self) -> None:
        i = self.session.doc.index_of_key(self.key)
        if i is None or self._swap is None:
            raise RuntimeError("cannot undo: page no longer exists")
        pe.restore_page(self.session.doc.pdf, i, self._swap)
        self.session.doc.mark_modified()
