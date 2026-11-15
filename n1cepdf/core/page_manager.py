"""Page operations on pikepdf documents. Content streams are not re-encoded."""
from __future__ import annotations
from pathlib import Path
from typing import Sequence
import pikepdf
from .document import PdfDocument


def insert_blank(doc: PdfDocument, index: int, size: tuple[float, float] = (595.0, 842.0)) -> None:
    page = pikepdf.Page(pikepdf.Dictionary(
        Type=pikepdf.Name.Page, MediaBox=[0, 0, size[0], size[1]],
        Resources=pikepdf.Dictionary(), Contents=doc.pdf.make_stream(b"")))
    doc.pdf.pages.insert(index, page)
    doc.mark_modified()


def delete_pages(doc: PdfDocument, indices: Sequence[int]) -> None:
    if len(set(indices)) >= doc.page_count:
        raise ValueError("Cannot delete every page.")
    for i in sorted(set(indices), reverse=True):
        del doc.pdf.pages[i]
    doc.mark_modified()


def duplicate_page(doc: PdfDocument, index: int) -> None:
    src = doc.pdf.pages[index]
    # new page dict sharing contents/resources objects (valid PDF, no re-encoding)
    clone = pikepdf.Page(pikepdf.Dictionary({k: v for k, v in src.obj.items() if k != "/Parent"}))
    doc.pdf.pages.insert(index + 1, clone)
    doc.mark_modified()


def rotate_pages(doc: PdfDocument, indices: Sequence[int], degrees: int) -> None:
    if degrees % 90:
        raise ValueError("Rotation must be a multiple of 90.")
    for i in indices:
        doc.pdf.pages[i].rotate(degrees, relative=True)
    doc.mark_modified()


def reorder_pages(doc: PdfDocument, new_order: Sequence[int]) -> None:
    n = doc.page_count
    if sorted(new_order) != list(range(n)):
        raise ValueError("new_order must be a permutation of all page indices.")
    # Re-add the *same* page objects in the new order (append() of an existing page would create new
    # page objects and change their identity, detaching anything keyed to a page).
    pages = [doc.pdf.pages[i] for i in new_order]
    del doc.pdf.pages[0:n]
    doc.pdf.pages.extend(pages)
    doc.mark_modified()


def extract_pages(doc: PdfDocument, indices: Sequence[int]) -> PdfDocument:
    out = pikepdf.Pdf.new()
    for i in indices:
        out.pages.append(doc.pdf.pages[i])
    return PdfDocument.from_pikepdf(out)


def merge(paths: Sequence[str | Path], passwords: dict[str, str] | None = None) -> PdfDocument:
    out = pikepdf.Pdf.new()
    for p in paths:
        src = PdfDocument.open(p, (passwords or {}).get(str(p)))
        out.pages.extend(src.pdf.pages)
    return PdfDocument.from_pikepdf(out)


def split_by_ranges(doc: PdfDocument, ranges: Sequence[Sequence[int]]) -> list[PdfDocument]:
    return [extract_pages(doc, r) for r in ranges]
