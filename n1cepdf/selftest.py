"""Packaged-build smoke test:  ``N1cePDF.exe --self-test [result.json]``.

Runs real work inside the *installed* application — the same bundled Python, PDFium, qpdf, ReportLab,
Pillow and Qt plugins an end user gets — and writes a JSON report. The exit code is 0 only if every
step passed. A windowed build has no console, so the JSON file is the primary output.

Steps: dependency versions · create a PDF in a Unicode + spaces path · open and render it (PDFium) ·
add text/shape/image/comment · undo + redo · save · re-open and verify persisted content (pikepdf + PDFium) ·
(optional) start the real Qt main window, open the file through the GUI and shut down cleanly.
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

UNICODE_TEXT = "Привет мир Țară și România ăâîșț"   # Cyrillic + Romanian diacritics
ASCII_TEXT = "Hello offline world"


class Report:
    def __init__(self) -> None:
        self.steps: list[dict] = []

    def run(self, name: str, fn) -> bool:
        t0 = time.perf_counter()
        try:
            detail = fn()
            self.steps.append({"step": name, "ok": True, "detail": detail or "",
                               "seconds": round(time.perf_counter() - t0, 3)})
            return True
        except Exception as e:  # noqa: BLE001 - the report must carry every failure
            self.steps.append({"step": name, "ok": False, "detail": f"{type(e).__name__}: {e}",
                               "traceback": traceback.format_exc(limit=6),
                               "seconds": round(time.perf_counter() - t0, 3)})
            return False

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(s["ok"] for s in self.steps)

    def as_dict(self) -> dict:
        from . import __version__
        from .runtime import is_frozen
        return {"app": "N1cePDF", "version": __version__, "frozen": is_frozen(),
                "python": sys.version.split()[0], "platform": sys.platform, "ok": self.ok, "steps": self.steps}


def _png() -> bytes:
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGBA", (30, 30), (0, 0, 255, 128)).save(b, "PNG")
    return b.getvalue()


def _make_source_pdf(path: Path) -> None:
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path))
    for n in range(3):
        c.setFont("Helvetica", 18)
        c.drawString(72, 700, f"smoke page {n + 1}")
        c.showPage()
    c.save()


def run_core_checks(work: Path, rep: Report) -> None:
    """Qt-free checks (also executed by the unit tests)."""
    folder = work / "Тест документ Țară (1)"          # Cyrillic + Romanian + spaces + parentheses
    folder.mkdir(parents=True, exist_ok=True)
    src = folder / "исходный файл №1.pdf"
    out = folder / "сохранено – copie.pdf"
    state: dict = {}

    def versions():
        import pikepdf
        import PIL
        import pypdfium2 as pdfium
        import reportlab
        return {"pikepdf": pikepdf.__version__, "qpdf": pikepdf.__libqpdf_version__,
                "pypdfium2": getattr(pdfium, "PYPDFIUM_INFO", None) and str(pdfium.PYPDFIUM_INFO) or "ok",
                "reportlab": reportlab.Version, "Pillow": PIL.__version__}

    def create():
        _make_source_pdf(src)
        assert src.stat().st_size > 0

    def open_render():
        from .core.document import PdfDocument
        from .core.renderer import PageRenderer
        doc = PdfDocument.open(src)
        state["doc"] = doc
        r = PageRenderer(doc.snapshot_bytes())
        try:
            page = r.render_sync(0, 1.0)
            assert doc.page_count == 3
            assert "smoke page 1" in r.page_text(0)
            return f"{doc.page_count} pages, page 1 rendered, text extracted"
        finally:
            r.close()

    def edit():
        from .edit.objects import Comment, ImageBox, Shape, TextBox
        from .edit.session import EditSession
        doc = state["doc"]
        s = EditSession(doc)
        state["session"] = s
        key = doc.page_key(0)
        face, _warn, _note = s.catalog.resolve("Arial", False, False, UNICODE_TEXT)
        covered = face is not None and not _warn
        state["text"] = UNICODE_TEXT if covered else ASCII_TEXT
        family = face.family if face is not None else "Helvetica"
        s.add_object(TextBox(key, 72, 500, 420, 540, state["text"], family, 14))
        s.add_object(Shape(key, "rect", 200, 300, 300, 400, fill=(0, 1, 0)))
        s.add_object(ImageBox(key, 72, 300, 172, 400, _png()))
        s.add_object(Comment(key, 400, 700, "note"))
        assert len(s.layer.all()) == 4 and doc.modified
        return ("Cyrillic/Romanian text" if covered else "ASCII text (no covering font installed — reported, not hidden)")

    def undo_redo():
        s = state["session"]
        assert s.undo() and len(s.layer.all()) == 3
        assert s.undo() and s.undo() and s.undo() and len(s.layer.all()) == 0
        assert s.redo() and s.redo() and s.redo() and s.redo() and len(s.layer.all()) == 4

    def save():
        from .core.file_manager import save_atomic
        save_atomic(state["doc"], out, session=state["session"])
        assert out.is_file() and out.stat().st_size > 0
        state["session"].mark_saved()
        assert not state["doc"].modified

    def reopen():
        import pikepdf
        import pypdfium2 as pdfium
        from .core.document import PdfDocument
        d2 = PdfDocument.open(out)
        try:
            assert d2.page_count == 3
        finally:
            d2.pdf.close()
        pd = pdfium.PdfDocument(out.read_bytes())
        try:
            text = pd[0].get_textpage().get_text_range()
        finally:
            pd.close()
        assert state["text"] in text and "smoke page 1" in text, "edited and original text must both persist"
        with pikepdf.open(out) as pdf:
            assert len(pdf.pages[0].Annots) == 1
        return f"re-opened {out.name}; text + annotation persisted"

    def release():
        state["doc"].pdf.close()

    for name, fn in (("dependency versions", versions), ("create PDF in unicode path", create),
                     ("open + render with PDFium", open_render), ("add text/shape/image/comment", edit),
                     ("undo and redo", undo_redo), ("save atomically", save),
                     ("re-open and verify persistence", reopen), ("release document", release)):
        if not rep.run(name, fn):
            break
    state["out"] = out


def run_gui_check(work: Path, rep: Report) -> None:
    """Start the real main window with the platform's Qt plugin, open a PDF through the GUI path, close."""
    def gui():
        from PySide6.QtWidgets import QApplication
        from .services.recent import RecentFiles
        from .ui.main_window import MainWindow, app_icon
        from .ui.theme import apply_theme
        src = work / "gui smoke.pdf"
        _make_source_pdf(src)
        app = QApplication.instance() or QApplication(["N1cePDF"])
        icon = app_icon()
        assert not icon.isNull(), "bundled assets/n1cepdf.png was not found"
        apply_theme(app, "dark")
        win = MainWindow(recent=RecentFiles(work / "recent.json"))
        win.show()
        win.open_path(str(src))
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and win.tabs.count() < 1:
            app.processEvents()
            time.sleep(0.02)
        assert win.tabs.count() == 1, "document did not open within 45 s"
        tab = win.cur()
        assert tab.doc.page_count == 3
        t_end = time.monotonic() + 2                      # let the first page render
        while time.monotonic() < t_end:
            app.processEvents()
            time.sleep(0.02)
        win.force_close_all()
        win.runner.shutdown()
        win.close()
        return f"Qt platform plugin '{app.platformName()}', document opened and closed cleanly"
    rep.run("start Qt main window, open PDF, shut down", gui)


def main(argv: list[str]) -> int:
    """argv: the full command line; ``--self-test`` may be followed by a result path."""
    args = argv[1:]
    i = args.index("--self-test")
    result_path = Path(args[i + 1]) if i + 1 < len(args) and not args[i + 1].startswith("-") else None
    with_gui = "--no-gui" not in args
    work = Path(tempfile.mkdtemp(prefix="n1cepdf-selftest-"))
    rep = Report()
    try:
        run_core_checks(work, rep)
        if rep.ok and with_gui:
            run_gui_check(work, rep)
    finally:
        data = json.dumps(rep.as_dict(), indent=2, ensure_ascii=False)
        target = result_path or (work / "selftest-result.json")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(data, encoding="utf-8")
        except OSError:
            pass
        if sys.stdout is not None:
            try:
                print(data)
            except (OSError, UnicodeError):
                pass
        if result_path:                      # keep the work dir only when it holds the default result file
            shutil.rmtree(work, ignore_errors=True)
    return 0 if rep.ok else 1
