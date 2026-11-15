"""Document model: pikepdf holds structure/edits; pdfium renders snapshots."""
from __future__ import annotations
import io
from dataclasses import dataclass
from pathlib import Path
import pikepdf
from .errors import InvalidPdfError, PasswordRequired, WrongPassword
from .validation import validate_pdf_path, MAX_PAGES


@dataclass(frozen=True)
class EncryptionInfo:
    encrypted: bool
    revision: int | None = None
    bits: int | None = None


class PdfDocument:
    def __init__(self, pdf: pikepdf.Pdf, path: Path | None, password: str | None):
        self._pdf = pdf
        self.path = path
        self._password = password  # memory only; never logged or persisted
        self.modified = False
        self.revision = 0  # bumped on every mutation; renderers key on it

    @classmethod
    def open(cls, path: str | Path, password: str | None = None) -> "PdfDocument":
        p = validate_pdf_path(path)
        try:
            # Read into memory: pikepdf.open(path) keeps the file handle open, which on Windows
            # blocks Save over the original (os.replace) and locks the file while it is viewed.
            pdf = pikepdf.open(io.BytesIO(p.read_bytes()), password=password or "")
        except pikepdf.PasswordError as e:
            raise (WrongPassword("Incorrect password.") if password else
                   PasswordRequired("This document requires a password.")) from e
        except (pikepdf.PdfError, RuntimeError, ValueError) as e:
            raise InvalidPdfError("The file is damaged or not a valid PDF.") from e
        if len(pdf.pages) > MAX_PAGES:
            pdf.close()
            raise InvalidPdfError("Document exceeds the page limit.")
        return cls(pdf, p, password)

    @classmethod
    def from_pikepdf(cls, pdf: pikepdf.Pdf) -> "PdfDocument":
        d = cls(pdf, None, None)
        d.modified = True
        return d

    @classmethod
    def new_blank(cls, size: tuple[float, float] = (595.0, 842.0)) -> "PdfDocument":
        pdf = pikepdf.Pdf.new()
        pdf.add_blank_page(page_size=size)
        return cls.from_pikepdf(pdf)

    @property
    def display_name(self) -> str:
        return self.path.name if self.path else "Untitled.pdf"

    def mark_saved(self, path: Path | None = None) -> None:
        if path is not None:
            self.path = Path(path)
        self.modified = False

    @property
    def pdf(self) -> pikepdf.Pdf:
        return self._pdf

    @property
    def page_count(self) -> int:
        return len(self._pdf.pages)

    def encryption_info(self) -> EncryptionInfo:
        if not self._pdf.is_encrypted:
            return EncryptionInfo(False)
        e = self._pdf.encryption
        return EncryptionInfo(True, getattr(e, "R", None), getattr(e, "bits", None))

    def metadata(self) -> dict[str, str]:
        return {str(k): str(v) for k, v in (self._pdf.docinfo or {}).items()}

    def page_size(self, index: int) -> tuple[float, float]:
        """Rotation- and CropBox-aware size in points."""
        page = self._pdf.pages[index]
        box = [float(x) for x in (page.get("/CropBox") or page.mediabox)]
        w, h = abs(box[2] - box[0]), abs(box[3] - box[1])
        rot = int(page.get("/Rotate", 0)) % 360
        return (h, w) if rot in (90, 270) else (w, h)

    # ---- stable page identity (survives reorder / insert / delete) ---------
    def page_key(self, index: int) -> tuple[int, int]:
        return tuple(self._pdf.pages[index].obj.objgen)  # type: ignore[return-value]

    def page_keys(self) -> list[tuple[int, int]]:
        return [tuple(p.obj.objgen) for p in self._pdf.pages]  # type: ignore[misc]

    def index_of_key(self, key: tuple[int, int]) -> int | None:
        for i, p in enumerate(self._pdf.pages):
            if tuple(p.obj.objgen) == tuple(key):
                return i
        return None

    def page_rotation(self, index: int) -> int:
        return int(self._pdf.pages[index].get("/Rotate", 0)) % 360

    def page_crop_box(self, index: int) -> tuple[float, float, float, float]:
        page = self._pdf.pages[index]
        b = [float(x) for x in (page.get("/CropBox") or page.mediabox)]
        return (min(b[0], b[2]), min(b[1], b[3]), max(b[0], b[2]), max(b[1], b[3]))

    def mark_modified(self) -> None:
        self.modified = True
        self.revision += 1

    def snapshot_bytes(self) -> bytes:
        """Current in-memory state as PDF bytes (rendering only, never written to disk)."""
        buf = io.BytesIO()
        if self._pdf.is_encrypted:
            self._pdf.save(buf, encryption=False)
        else:
            self._pdf.save(buf)
        return buf.getvalue()

    def close(self) -> None:
        self._pdf.close()
        self._password = None
