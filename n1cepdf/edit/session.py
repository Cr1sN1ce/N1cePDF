"""EditSession: one per open document. Owns the edit layer (PDF-space objects) and the undo stack,
builds verified edit plans (heavy; safe to run on a worker thread) and commits them (GUI thread)."""
from __future__ import annotations
import io
from dataclasses import dataclass, field
from typing import Callable
import pikepdf
import pypdfium2 as pdfium
from ..core.document import PdfDocument
from ..core.pdfium_lock import locked
from . import commands as C
from . import pdfium_edit as pe
from .fonts import FontCatalog, default_catalog, guess_family, metrics
from .geometry import Rect
from .layer import EditLayer
from .objects import EditObject, ImageBox, PageKey, TextBox
from .undo import Command, MacroCommand, UndoStack
from .writer import text_size_needed

EditRefused = pe.EditRefused


@dataclass(frozen=True)
class TextStyle:
    family: str = "Liberation Sans"
    size: float = 12.0
    bold: bool = False
    italic: bool = False
    color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    align: str = "left"
    line_spacing: float = 1.2


def default_style(run: pe.TextRun) -> TextStyle:
    fam, bold, italic = guess_family(run.font_name)
    return TextStyle(fam, round(run.font_size, 2), bold, italic, tuple(round(c, 3) for c in run.color))


@dataclass
class EditPlan:
    title: str
    mode: str                         # native | replace | delete-text | image-transform | image-delete | image-replace
    page_key: PageKey
    revision: int
    old_text: str = ""
    new_text: str = ""
    warnings: list[str] = field(default_factory=list)   # shown in the preview; the user must accept them
    notes: list[str] = field(default_factory=list)
    build: Callable[[], list[Command]] = lambda: []


@dataclass(frozen=True)
class ChangeEvent:
    structural: bool   # page tree / content changed: the viewer must rebuild its renderer
    how: str           # do | undo | redo
    text: str


class EditSession:
    def __init__(self, doc: PdfDocument, catalog: FontCatalog | None = None):
        self.doc = doc
        self.catalog = catalog or default_catalog()
        self.layer = EditLayer()
        self.stack = UndoStack(self._on_change)
        self.listeners: list[Callable[[ChangeEvent], None]] = []
        self._unsaved_new = doc.path is None   # a never-saved document stays modified even at the clean index

    # ---------------------------------------------------------------- stack plumbing
    def _on_change(self, cmd: Command, how: str) -> None:
        self.doc.revision += 1
        self.doc.modified = self._unsaved_new or not self.stack.is_clean()
        ev = ChangeEvent(cmd.structural, how, cmd.text)
        for fn in list(self.listeners):
            fn(ev)

    def push(self, cmd: Command) -> None:
        self.stack.push(cmd)

    def undo(self) -> bool:
        return self.stack.undo()

    def redo(self) -> bool:
        return self.stack.redo()

    def mark_saved(self) -> None:
        self._unsaved_new = False
        self.stack.set_clean()
        self.doc.modified = False

    # ---------------------------------------------------------------- layer objects
    def add_object(self, obj: EditObject, text: str | None = None) -> None:
        self.push(C.AddObject(self, obj, text))

    def remove_object(self, oid: str) -> None:
        self.push(C.RemoveObject(self, oid))

    def update_object(self, after: EditObject, text: str = "Edit object", merge: str | None = None) -> None:
        before = self.layer.get(after.id)
        if before is None or before == after:
            return
        self.push(C.UpdateObject(self, before, after, text, merge))

    def layer_by_page_index(self) -> dict[int, list[EditObject]]:
        out: dict[int, list[EditObject]] = {}
        for i, key in enumerate(self.doc.page_keys()):
            objs = self.layer.on_page(key)
            if objs:
                out[i] = objs
        return out

    # ---------------------------------------------------------------- page operations
    def rotate_page(self, index: int, degrees: int = 90) -> None:
        self.push(C.RotatePage(self, self.doc.page_key(index), degrees))

    def insert_blank_after(self, index: int) -> None:
        self.push(C.InsertBlankPage(self, index + 1, self.doc.page_size(index)))

    def delete_page(self, index: int) -> None:
        self.push(C.DeletePage(self, self.doc.page_key(index)))

    def reorder_pages(self, new_order: list[int]) -> None:
        self.push(C.ReorderPages(self, new_order))

    # ---------------------------------------------------------------- existing content: planning
    def _resolve(self, key: PageKey, expected_index: int | None = None) -> int:
        i = self.doc.index_of_key(key)
        if i is None or (expected_index is not None and i != expected_index):
            raise EditRefused("The page order changed since the selection was made. Select again.")
        return i

    @locked
    def inspect_page(self, index: int) -> pe.PageContent:
        """Fresh inspection from the current document state (workers / tests)."""
        pdoc = pdfium.PdfDocument(self.doc.snapshot_bytes())
        try:
            return pe.inspect_page(pdoc, index)
        finally:
            pdoc.close()

    @locked
    def plan_text_edit(self, key: PageKey, run: pe.TextRun, new_text: str,
                       style: TextStyle | None = None) -> EditPlan:
        """Plan changing an existing text run. Heavy: call from a worker. Nothing is modified."""
        i = self._resolve(key, run.page)
        rev = self.doc.revision
        data = self.doc.snapshot_bytes()
        pdoc = pdfium.PdfDocument(data)
        try:
            content = pe.inspect_page(pdoc, i)
        finally:
            pdoc.close()
        cur = next((r for r in content.runs if r.index == run.index and r.text.strip() == run.text.strip()), None)
        if cur is None:
            raise EditRefused("The text on the page changed since it was selected. Select it again.")
        warnings = pe.describe_overlaps(pe.overlaps_for(content, run.index))
        base_style = default_style(run)
        style = style or base_style
        style_changed = style != base_style
        plan = EditPlan("Edit text", "replace", key, rev, run.text, new_text, warnings)

        if new_text == "":
            out = pe.remove_objects(data, i, [(run.index, "text", run.text)])
            plan.title, plan.mode = "Delete text", "delete-text"
            plan.notes.append("The text is removed from the page content stream.")
            plan.build = lambda: [C.ContentSwap(self, key, out, "Delete text")]
            return plan

        if not style_changed and "\n" not in new_text and run.native_ok:
            try:
                out = pe.native_set_text(data, i, run.index, run.text, new_text)
                plan.mode = "native"
                plan.notes.append(f"In-place edit: the text object is rewritten in its original font "
                                  f"({run.font_name}), size, colour and position.")
                self._width_warning(plan, run, new_text, style)
                plan.build = lambda: [C.ContentSwap(self, key, out, "Edit text")]
                return plan
            except EditRefused as e:
                plan.notes.append(f"In-place rewrite not possible ({e}). Using remove-and-replace.")
        elif not run.native_ok:
            plan.notes.append("The original font is embedded or non-standard, so its glyphs cannot be "
                              "reused for new text. Using remove-and-replace.")
        else:
            plan.notes.append("Font/size/colour/layout changed. Using remove-and-replace.")

        if run.skewed or abs(((run.rotation + 180) % 360) - 180) > 0.5:
            raise EditRefused("Rotated or skewed text can only be edited in place (standard fonts, unchanged "
                              "style). Remove-and-replace for rotated text is not supported.")
        face, missing, note = self.catalog.resolve(style.family, style.bold, style.italic, new_text)
        if face is None:
            raise EditRefused("No usable font is installed for the replacement text.")
        if note:
            plan.warnings.append(note)
        if missing:
            plan.warnings.append("These characters have no glyph in the chosen font: " + " ".join(missing[:20]))
        out = pe.remove_objects(data, i, [(run.index, "text", run.text)])
        asc, _d = metrics(face)
        box = TextBox(key, 0, 0, 1, 1, new_text, face.family, style.size, style.bold, style.italic, style.color,
                      style.align, style.line_spacing, origin="replacement")
        w, h = text_size_needed(box, self.catalog)
        x0 = run.bbox[0] if style.align != "left" else run.baseline[0]
        y1 = run.baseline[1] + asc * style.size
        x1 = max(x0 + w + 1.0, run.bbox[2]) if style.align != "left" else x0 + w + 1.0
        box = TextBox(key, x0, y1 - h, x1, y1, new_text, face.family, style.size, style.bold, style.italic,
                      style.color, style.align, style.line_spacing, origin="replacement")
        self._width_warning(plan, run, new_text, style, w)
        plan.warnings.append(f"The original font '{run.font_name}' is not reused; '{face.family}' is embedded "
                             "instead, so letter shapes and spacing can differ slightly.")
        plan.notes.append("The old text object is removed from the page content (not covered); the "
                          "replacement is real text in an embedded font.")
        plan.build = lambda: [C.ContentSwap(self, key, out, "Edit text"), C.AddObject(self, box, "Edit text")]
        return plan

    def _width_warning(self, plan: EditPlan, run: pe.TextRun, new_text: str, style: TextStyle,
                       width: float | None = None) -> None:
        if width is None:
            face = self.catalog.find(style.family, style.bold, style.italic)
            if face is None:
                return
            width, _h = text_size_needed(TextBox(plan.page_key, 0, 0, 1, 1, new_text, face.family, style.size,
                                                 style.bold, style.italic), self.catalog)
        old_w = run.bbox[2] - run.bbox[0]
        if width > old_w + 2.0:
            plan.warnings.append(f"The new text is about {width - old_w:.0f} pt wider than the original and may "
                                 "run into nearby content.")

    @locked
    def plan_image_edit(self, key: PageKey, index: int, info: pe.PageObjectInfo, rect: Rect,
                        rotation: float = 0.0) -> EditPlan:
        i = self._resolve(key, index)
        rev = self.doc.revision
        data = self.doc.snapshot_bytes()
        out = pe.set_image_rect(data, i, info.index, rect, rotation)
        plan = EditPlan("Transform image", "image-transform", key, rev)
        plan.notes.append("The existing image object's placement matrix is changed; image data is untouched.")
        plan.build = lambda: [C.ContentSwap(self, key, out, "Transform image")]
        return plan

    @locked
    def plan_image_delete(self, key: PageKey, index: int, info: pe.PageObjectInfo) -> EditPlan:
        i = self._resolve(key, index)
        rev = self.doc.revision
        data = self.doc.snapshot_bytes()
        content = self._content_of(data, i)
        warnings = pe.describe_overlaps(pe.overlaps_for(content, info.index))
        out = pe.remove_objects(data, i, [(info.index, "image", "")])
        plan = EditPlan("Delete image", "image-delete", key, rev, warnings=warnings)
        plan.build = lambda: [C.ContentSwap(self, key, out, "Delete image")]
        return plan

    @locked
    def plan_image_replace(self, key: PageKey, index: int, info: pe.PageObjectInfo, image_bytes: bytes) -> EditPlan:
        i = self._resolve(key, index)
        rev = self.doc.revision
        data = self.doc.snapshot_bytes()
        content = self._content_of(data, i)
        warnings = pe.describe_overlaps(pe.overlaps_for(content, info.index))
        out = pe.remove_objects(data, i, [(info.index, "image", "")])
        l, b, r, t = info.bbox
        box = ImageBox(key, l, b, r, t, image_bytes, lock_aspect=False)
        plan = EditPlan("Replace image", "image-replace", key, rev, warnings=warnings)
        plan.notes.append("The old image object is removed; the new image is placed over its bounds (stretched "
                          "to fit; resize afterwards to restore its aspect ratio).")
        plan.build = lambda: [C.ContentSwap(self, key, out, "Replace image"), C.AddObject(self, box, "Replace image")]
        return plan

    @staticmethod
    def _content_of(data: bytes, index: int) -> pe.PageContent:
        pdoc = pdfium.PdfDocument(data)
        try:
            return pe.inspect_page(pdoc, index)
        finally:
            pdoc.close()

    def commit(self, plan: EditPlan) -> None:
        if plan.revision != self.doc.revision:
            raise EditRefused("The document changed while the edit was being prepared. Try again.")
        cmds = plan.build()
        self.push(cmds[0] if len(cmds) == 1 else MacroCommand(plan.title, cmds))
