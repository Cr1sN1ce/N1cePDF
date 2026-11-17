"""Local, cancellable Tesseract OCR. No Qt or network dependencies."""
from __future__ import annotations
import csv
import io
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from dataclasses import dataclass
import pypdfium2 as pdfium
from .pdfium_lock import PDFIUM_LOCK
from ..edit.geometry import PageTransform, rects_intersect
from .ocr_layout import OcrWord, bounds, segment_rows


class OcrError(RuntimeError):
    pass


class OcrCancelled(OcrError):
    pass


@dataclass(frozen=True)
class OcrLine:
    page: int
    text: str
    rect: tuple[float, float, float, float]
    size: float
    match: object = None
    words: tuple[OcrWord,...] = ()
    align: str = "left"


def _check(cancel):
    if cancel.is_set():
        raise OcrCancelled('OCR cancelled. No changes applied.')


class Tesseract:
    def __init__(self, executable=None):
        self.executable = executable or os.environ.get('N1CEPDF_TESSERACT') or shutil.which('tesseract')
        if not self.executable:
            raise OcrError('Tesseract not found. Install Tesseract and add it to PATH, or set N1CEPDF_TESSERACT.')

    def _run(self, args, cancel, timeout=120):
        _check(cancel)
        try:
            with tempfile.TemporaryFile() as output:
                proc = subprocess.Popen([self.executable, *args], stdout=output, stderr=subprocess.STDOUT,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                try:
                    deadline = time.monotonic() + timeout
                    while proc.poll() is None:
                        _check(cancel)
                        if time.monotonic() > deadline:
                            raise OcrError('Tesseract timed out.')
                        cancel.wait(0.05)
                    _check(cancel)
                    output.seek(0)
                    message = output.read().decode('utf-8', errors='replace')
                    if proc.returncode:
                        raise OcrError('Tesseract failed: ' + message[-2000:])
                    return message
                finally:
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait()
        except OSError as exc:
            raise OcrError(f'Cannot start Tesseract: {exc}') from exc

    def languages(self, cancel):
        return set(self._run(['--list-langs'], cancel, 15).splitlines()[1:])

    def recognize(self, image, language, cancel):
        with tempfile.TemporaryDirectory(prefix='n1cepdf-ocr-') as directory:
            source = Path(directory) / 'page.png'
            target = Path(directory) / 'result'
            image.save(source)
            self._run([str(source), str(target), '-l', language, '--dpi', '300', 'tsv'], cancel)
            return (target.with_suffix('.tsv')).read_text('utf-8')


def parse_lines(tsv, page, transform, native=()):
    groups = {}
    for row in csv.DictReader(io.StringIO(tsv), delimiter='\t', quoting=csv.QUOTE_NONE):
        if row['level'] != '5' or not row['text'].strip() or float(row['conf']) < 0:
            continue
        x, y, w, h = (float(row[k]) for k in ('left', 'top', 'width', 'height'))
        rect = transform.rect_to_pdf(x, y, w, h)
        if any(rects_intersect(rect, r) for r in native):
            continue
        key = tuple(row[k] for k in ('block_num', 'par_num', 'line_num'))
        groups.setdefault(key, []).append(OcrWord(row['text'],rect,float(row['conf'])))
    result = []
    for words,align in segment_rows(list(groups.values())):
        rect = bounds(words)
        result.append(OcrLine(page,' '.join(w.text for w in words),rect,
                              max(4,(rect[3]-rect[1])/.8),words=tuple(words),align=align))
    return result


def recognize_pdf(data, pages, language, cancel, progress=lambda done, total: None,
                  automatic=False, excluded=None, engine=None):
    """Return all results atomically; cancellation/errors never commit partial pages.

    Automatic mode skips pages with text; manual mode recognizes image text on mixed pages,
    filtering words overlapping native text or previously recognized objects.
    """
    engine = engine or Tesseract()
    missing = set(language.split('+')) - engine.languages(cancel)
    if missing:
        raise OcrError('Missing Tesseract language data: ' + ', '.join(sorted(missing)))
    results = []
    for count, index in enumerate(pages):
        _check(cancel)
        # PDFium is not thread safe. Release its lock before the external OCR process.
        with PDFIUM_LOCK:
            doc = pdfium.PdfDocument(data)
            try:
                page = doc[index]
                try:
                    textpage = page.get_textpage()
                    try:
                        has_text = bool(textpage.get_text_range().strip())
                        native = [textpage.get_rect(i) for i in range(textpage.count_rects())] if has_text else []
                    finally:
                        textpage.close()
                    if automatic and has_text:
                        image = None
                    else:
                        page.set_rotation(0)
                        crop = page.get_bbox()
                        scale = min(300 / 72, 8000 / max(page.get_size()))
                        bitmap = page.render(scale=scale)
                        try:
                            image = bitmap.to_pil().convert('RGB').copy()
                        finally:
                            bitmap.close()
                        transform = PageTransform(tuple(crop), 0, image.width, image.height)
                finally:
                    page.close()
            finally:
                doc.close()
        if image is not None:
            try:
                tsv = engine.recognize(image, language, cancel)
                from dataclasses import replace
                from ..edit.font_match import match_font
                lines = parse_lines(tsv, index, transform, native + list((excluded or {}).get(index, [])))
                for line in lines:
                    _check(cancel)
                    x,y,w,h = transform.rect_to_px(line.rect)
                    sample = image.crop((int(x),int(y),max(int(x)+1,int(x+w)),max(int(y)+1,int(y+h))))
                    matched = match_font(sample, line.text, transform._scale[0], cancel=cancel)
                    results.append(replace(line, match=matched))
            finally:
                image.close()
        _check(cancel)
        progress(count + 1, len(pages))
    return results
