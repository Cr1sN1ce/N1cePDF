"""Atomic, validated saving. Original files are never modified by Save As."""
from __future__ import annotations
import io
import os
import tempfile
from pathlib import Path
import pikepdf
from .document import PdfDocument
from .errors import EncryptedSaveRefused, SaveValidationError


def same_file(a: str | Path, b: str | Path) -> bool:
    """True if both paths name the same file. Uses the OS file identity where possible, so Windows
    case differences, short (8.3) names and symlinks do not defeat duplicate detection."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _temp_beside(dest: Path) -> tuple[int, Path]:
    try:
        fd, name = tempfile.mkstemp(prefix=".n1cepdf-", suffix=".tmp", dir=dest.parent)
    except PermissionError as e:
        raise SaveValidationError(_DENIED) from e
    except OSError as e:
        raise SaveValidationError(f"Could not write the file: {e.strerror or type(e).__name__}.") from e
    return fd, Path(name)


_DENIED = ("Could not write the file: access was denied. Check that the folder is writable and that the "
           "file is not open in another program.")


def save_atomic(doc: PdfDocument, dest: str | Path, *, allow_decrypt: bool = False,
                session=None, warnings: list[str] | None = None) -> Path:
    """Atomic, validated save. If `session` holds pending edit objects they are written into the output
    (real content / annotations); the in-memory document and layer are left untouched so editing continues.
    The destination is replaced only after the temporary file re-opens and has the expected page count."""
    dest = Path(dest)
    if doc.pdf.is_encrypted and not allow_decrypt:
        raise EncryptedSaveRefused(
            "This document is encrypted. Saving would remove encryption; authorization required.")
    expected = doc.page_count
    fd, tmp = _temp_beside(dest)
    try:
        with os.fdopen(fd, "wb") as f:
            by_page = session.layer_by_page_index() if session is not None else {}
            if by_page:
                from ..edit.writer import apply_layer  # local import: keeps core free of edit deps at import time
                with pikepdf.open(io.BytesIO(doc.snapshot_bytes())) as out:
                    w = apply_layer(out, by_page, session.catalog)
                    if warnings is not None:
                        warnings.extend(w)
                    out.save(f)
            elif doc.pdf.is_encrypted:
                doc.pdf.save(f, encryption=False)
            else:
                doc.pdf.save(f)
            f.flush()
            os.fsync(f.fileno())
        _validate_output(tmp, expected)
        os.replace(tmp, dest)
    except SaveValidationError:
        raise
    except PermissionError as e:   # e.g. the destination is open in another program on Windows
        raise SaveValidationError(_DENIED) from e
    except OSError as e:           # disk full, path too long, network share dropped, ...
        raise SaveValidationError(f"Could not write the file: {e.strerror or type(e).__name__}.") from e
    except Exception as e:  # noqa: BLE001
        from ..edit.scan_rebuild import ReconstructionError
        if isinstance(e,ReconstructionError):
            raise SaveValidationError(str(e)) from e
        raise SaveValidationError(f"Could not write output: {type(e).__name__}: {e}") from e
    finally:
        tmp.unlink(missing_ok=True)
    return dest


def _validate_output(path: Path, expected_pages: int) -> None:
    try:
        with pikepdf.open(path) as out:
            if len(out.pages) != expected_pages:
                raise SaveValidationError("Output page count mismatch.")
    except pikepdf.PdfError as e:
        raise SaveValidationError("Output failed re-open validation.") from e
