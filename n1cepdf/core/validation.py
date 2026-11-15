"""Input validation for untrusted PDF files."""
from __future__ import annotations
from pathlib import Path
from .errors import InvalidPdfError

MAX_FILE_BYTES = 1024 * 1024 * 1024  # 1 GiB default resource limit
MAX_PAGES = 20000


def validate_pdf_path(path: str | Path, max_bytes: int = MAX_FILE_BYTES) -> Path:
    p = Path(path)
    if not p.is_file():
        raise InvalidPdfError("Not a regular file.")
    size = p.stat().st_size
    if size == 0:
        raise InvalidPdfError("File is empty.")
    if size > max_bytes:
        raise InvalidPdfError(f"File exceeds the size limit ({max_bytes // (1024 * 1024)} MiB).")
    with p.open("rb") as f:
        head = f.read(1024)
    if b"%PDF-" not in head:
        raise InvalidPdfError("Missing %PDF- header; not a PDF file.")
    return p


def parse_page_ranges(spec: str, page_count: int) -> list[list[int]]:
    """'1-3,5,7-' -> zero-based index lists. Raises ValueError on bad input."""
    out: list[list[int]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start = int(a) if a.strip() else 1
            end = int(b) if b.strip() else page_count
        else:
            start = end = int(part)
        if start < 1 or end > page_count or start > end:
            raise ValueError(f"Range '{part}' is outside 1-{page_count}.")
        out.append(list(range(start - 1, end)))
    if not out:
        raise ValueError("Empty page range.")
    return out
