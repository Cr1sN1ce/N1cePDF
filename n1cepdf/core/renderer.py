"""Qt-free rendering: pdfium via pypdfium2, bounded LRU cache, generation ids.

pdfium is not thread-safe, so every pdfium call is serialized by one lock.
Workers never touch GUI objects; they return plain data (RGB bytes) tagged with
the generation they were requested under so consumers can discard stale results.
"""
from __future__ import annotations
import contextlib
import ctypes
import threading
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from .pdfium_lock import PDFIUM_LOCK

_SUB = 16


def _v_overlap(b1: float, t1: float, b2: float, t2: float) -> float:
    """Vertical overlap of two intervals as a fraction of the smaller one."""
    inter = min(t1, t2) - max(b1, b2)
    smaller = min(t1 - b1, t2 - b2)
    return inter / smaller if smaller > 0 and inter > 0 else 0.0  # sub-pixel factor for integer device-coordinate APIs


@dataclass(frozen=True)
class RenderedPage:
    page: int
    scale: float
    generation: int
    width: int
    height: int
    rgb: bytes


@dataclass(frozen=True)
class SearchHit:
    page: int
    rects: tuple[tuple[float, float, float, float], ...]  # PDF points: l, b, r, t
    spans: tuple[tuple[int, int], ...] = field(default=())  # (char start, char count)
    snippets: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class OutlineEntry:
    level: int  # 0 = top level
    title: str
    page: int | None


class RendererClosed(RuntimeError):
    """Raised when a late worker touches a renderer whose document was closed."""


class PageRenderer:
    def __init__(self, pdf_bytes: bytes, max_cache_bytes: int = 256 * 1024 * 1024, workers: int = 2):
        self._lock = PDFIUM_LOCK  # shared by every renderer and by edit/preview code
        with PDFIUM_LOCK:
            self._doc = pdfium.PdfDocument(pdf_bytes)
        self._cache: OrderedDict[tuple, RenderedPage] = OrderedDict()
        self._cache_bytes = 0
        self._max = max_cache_bytes
        self._gen = 0
        self._closed = False
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="render")

    # ---- generations ---------------------------------------------------
    @property
    def generation(self) -> int:
        return self._gen

    def bump_generation(self) -> int:
        """Invalidate in-flight cancellable work (zoom change, document edit)."""
        self._gen += 1
        return self._gen

    def is_current(self, result: RenderedPage) -> bool:
        return result.generation == self._gen

    def _live(self) -> None:
        """Call with the lock held before touching pdfium."""
        if self._closed:
            raise RendererClosed("document closed")

    # ---- locking -------------------------------------------------------
    @contextlib.contextmanager
    def _try_lock(self):
        """Non-blocking acquire for GUI-thread callers; yields True if acquired."""
        got = self._lock.acquire(blocking=False)
        try:
            yield got
        finally:
            if got:
                self._lock.release()

    # ---- geometry ------------------------------------------------------
    def __len__(self) -> int:
        with self._lock:
            self._live()
            return len(self._doc)

    def page_size(self, page: int) -> tuple[float, float]:
        """Displayed size in points (rotation and CropBox applied by pdfium)."""
        with self._lock:
            self._live()
            return self._doc[page].get_size()

    def page_sizes(self) -> list[tuple[float, float]]:
        with self._lock:
            self._live()
            return [self._doc[i].get_size() for i in range(len(self._doc))]

    # ---- rendering -----------------------------------------------------
    def render_sync(self, page: int, scale: float, generation: int | None = None) -> RenderedPage:
        gen = self._gen if generation is None else generation
        key = (page, round(scale, 3))
        with self._lock:
            self._live()
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return RenderedPage(hit.page, hit.scale, gen, hit.width, hit.height, hit.rgb)
            bitmap = self._doc[page].render(scale=scale)
            img = bitmap.to_pil().convert("RGB")
            res = RenderedPage(page, scale, gen, img.width, img.height, img.tobytes())
            self._cache[key] = res
            self._cache_bytes += len(res.rgb)
            while self._cache_bytes > self._max and len(self._cache) > 1:
                _, old = self._cache.popitem(last=False)
                self._cache_bytes -= len(old.rgb)
            return res

    def render_async(self, page: int, scale: float, *, cancellable: bool = True) -> Future:
        """Queue a render. Cancellable jobs return None if the generation moved on before
        they started; non-cancellable jobs (thumbnails) always run."""
        gen = self._gen

        def job() -> RenderedPage | None:
            if self._closed:
                return None
            if cancellable and gen != self._gen:
                return None
            return self.render_sync(page, scale, gen)

        return self._pool.submit(job)

    @property
    def cache_bytes(self) -> int:
        return self._cache_bytes

    # ---- text ----------------------------------------------------------
    def page_text(self, page: int) -> str:
        with self._lock:
            self._live()
            return self._doc[page].get_textpage().get_text_range()

    def search(self, query: str, match_case: bool = False,
               cancel: threading.Event | None = None) -> list[SearchHit]:
        """Search page by page; the lock is released between pages so rendering continues."""
        hits: list[SearchHit] = []
        if not query:
            return hits
        n = len(self)
        for i in range(n):
            if cancel is not None and cancel.is_set():
                break
            with self._lock:
                self._live()
                tp = self._doc[i].get_textpage()
                searcher = tp.search(query, match_case=match_case)
                rects, spans, snips = [], [], []
                while True:
                    found = searcher.get_next()
                    if not found:
                        break
                    start, count = found
                    boxes = [tp.get_charbox(j) for j in range(start, start + count)]
                    rects.append((min(b[0] for b in boxes), min(b[1] for b in boxes),
                                  max(b[2] for b in boxes), max(b[3] for b in boxes)))
                    spans.append((start, count))
                    s0 = max(0, start - 20)
                    snips.append(" ".join(tp.get_text_range(s0, count + (start - s0) + 20).split()))
            if rects:
                hits.append(SearchHit(i, tuple(rects), tuple(spans), tuple(snips)))
        return hits

    def outline(self) -> list[OutlineEntry]:
        out: list[OutlineEntry] = []
        with self._lock:
            self._live()
            try:
                for item in self._doc.get_toc():
                    dest = item.get_dest()
                    idx = dest.get_index() if dest is not None else None
                    out.append(OutlineEntry(item.level, item.get_title() or "(untitled)", idx))
            except Exception:  # malformed outline must never break opening  # noqa: BLE001
                return out
        return out

    # ---- selection geometry (all coordinates are device pixels of the page item,
    #      i.e. page rendered at width_px x height_px, origin top-left) ----------
    def _to_device(self, page_obj, w: float, h: float, x: float, y: float) -> tuple[float, float]:
        dx, dy = ctypes.c_int(), ctypes.c_int()
        pdfium_c.FPDF_PageToDevice(page_obj.raw, 0, 0, round(w * _SUB), round(h * _SUB), 0,
                                   x, y, dx, dy)
        return dx.value / _SUB, dy.value / _SUB

    def _to_page(self, page_obj, w: float, h: float, x: float, y: float) -> tuple[float, float]:
        px, py = ctypes.c_double(), ctypes.c_double()
        pdfium_c.FPDF_DeviceToPage(page_obj.raw, 0, 0, round(w * _SUB), round(h * _SUB), 0,
                                   round(x * _SUB), round(y * _SUB), px, py)
        return px.value, py.value

    def char_count(self, page: int) -> int:
        with self._lock:
            self._live()
            return self._doc[page].get_textpage().count_chars()

    def char_at(self, page: int, w: float, h: float, x: float, y: float,
                tol: float = 6.0) -> int | None:
        """Index of the character under device point (x, y), or None. Never blocks."""
        with self._try_lock() as got:
            if not got or self._closed:
                return None
            pg = self._doc[page]
            px, py = self._to_page(pg, w, h, x, y)
            idx = pg.get_textpage().get_index(px, py, tol, tol)
            return idx if idx is not None and idx >= 0 else None

    def rects_px(self, page: int, w: float, h: float,
                 page_rects: list[tuple[float, float, float, float]]) -> list[tuple[float, float, float, float]]:
        """Convert PDF-point rects (l, b, r, t) to device rects (x, y, w, h)."""
        out = []
        with self._lock:
            self._live()
            pg = self._doc[page]
            for l, b, r, t in page_rects:
                x1, y1 = self._to_device(pg, w, h, l, t)
                x2, y2 = self._to_device(pg, w, h, r, b)
                out.append((min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)))
        return out

    def selection_rects_pdf(self, page: int, start: int, end: int) -> list[tuple[float, float, float, float]]:
        """Line-merged rects (l, b, r, t) in PDF space covering chars start..end (inclusive)."""
        with self._lock:
            self._live()
            return self._merged_lines(self._doc[page].get_textpage(), start, end)

    def page_content(self, page: int):
        """Top-level page objects + text runs (see edit.pdfium_edit); uses the already-open document."""
        from ..edit import pdfium_edit  # local import: core must not depend on edit at import time
        with self._lock:
            self._live()
            return pdfium_edit.inspect_page(self._doc, page)

    def selection_rects_px(self, page: int, w: float, h: float, start: int, end: int):
        """Line-merged device rects covering chars start..end (inclusive)."""
        with self._lock:
            self._live()
            lines = self._merged_lines(self._doc[page].get_textpage(), start, end)
        return self.rects_px(page, w, h, lines)

    @staticmethod
    def _merged_lines(tp, start: int, end: int):
        lo, hi = sorted((start, end))
        if True:
            n = tp.count_chars()
            hi = min(hi, n - 1)
            lines: list[list[float]] = []
            for i in range(max(lo, 0), hi + 1):
                l, b, r, t = tp.get_charbox(i)
                if r <= l or (t - b) < 1.0:
                    continue  # whitespace/newline: degenerate box; the line union spans the gap
                if lines and _v_overlap(lines[-1][1], lines[-1][3], b, t) > 0.5 \
                        and l <= lines[-1][2] + (t - b):
                    ln = lines[-1]
                    ln[0], ln[1] = min(ln[0], l), min(ln[1], b)
                    ln[2], ln[3] = max(ln[2], r), max(ln[3], t)
                else:
                    lines.append([l, b, r, t])
        return [tuple(x) for x in lines]

    def selected_text(self, page: int, start: int, end: int) -> str:
        lo, hi = sorted((start, end))
        with self._lock:
            self._live()
            tp = self._doc[page].get_textpage()
            hi = min(hi, tp.count_chars() - 1)
            if hi < lo:
                return ""
            return tp.get_text_range(lo, hi - lo + 1)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        with self._lock:  # waits for any in-flight pdfium call to finish
            if not self._closed:
                self._closed = True
                self._doc.close()
