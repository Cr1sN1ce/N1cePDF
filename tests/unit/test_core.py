import time
import pikepdf
import pytest
from n1cepdf.core import page_manager as pm
from n1cepdf.core.document import PdfDocument
from n1cepdf.core.errors import (EncryptedSaveRefused, InvalidPdfError, PasswordRequired,
                                 WrongPassword)
from n1cepdf.core.file_manager import save_atomic
from n1cepdf.core.renderer import PageRenderer, RendererClosed
from n1cepdf.core.validation import parse_page_ranges
import os
import sys
import threading
from n1cepdf.services.recent import RecentFiles
from n1cepdf.core.fonts import find_unicode_font
from tests.conftest import DEJAVU, make_pdf, sha


def test_open_valid(sample3):
    d = PdfDocument.open(sample3)
    assert d.page_count == 3 and not d.encryption_info().encrypted


def test_reject_malformed(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.7\n garbage that is not a pdf " * 10)
    with pytest.raises(InvalidPdfError):
        PdfDocument.open(bad)
    txt = tmp_path / "x.pdf"
    txt.write_text("hello")
    with pytest.raises(InvalidPdfError):
        PdfDocument.open(txt)
    with pytest.raises(InvalidPdfError):
        PdfDocument.open(tmp_path / "missing.pdf")


def test_render_pages(sample3):
    r = PageRenderer(PdfDocument.open(sample3).snapshot_bytes())
    res = r.render_sync(0, 1.0)
    assert res.width > 100 and len(res.rgb) == res.width * res.height * 3
    assert r.render_sync(0, 1.0).rgb == res.rgb  # cache hit


def test_cache_is_bounded(sample3):
    r = PageRenderer(PdfDocument.open(sample3).snapshot_bytes(), max_cache_bytes=1)
    for i in range(3):
        r.render_sync(i, 1.0)
    assert len(r._cache) == 1


def test_search_and_text(sample3):
    r = PageRenderer(PdfDocument.open(sample3).snapshot_bytes())
    hits = r.search("beta")
    assert [h.page for h in hits] == [1] and len(hits[0].rects) == 1
    assert "gamma" in r.page_text(2)


@pytest.mark.skipif(DEJAVU is None, reason="no Unicode TrueType font found on this system")
def test_unicode_text(tmp_path):
    p = make_pdf(tmp_path / "u.pdf", ["Привет мир", "Țară și ăâîșț"], unicode_font=True)
    r = PageRenderer(PdfDocument.open(p).snapshot_bytes())
    assert "Привет" in r.page_text(0)
    assert [h.page for h in r.search("Țară")] == [1]
    assert "ăâîșț" in r.page_text(1)
    assert r.render_sync(0, 1.0).width > 100  # renders without error


def test_stale_generation_discarded(sample3):
    r = PageRenderer(PdfDocument.open(sample3).snapshot_bytes(), workers=1)
    fut = r.render_async(0, 1.0)
    res = fut.result(timeout=10)
    r.bump_generation()
    assert res is not None and not r.is_current(res)


def test_cancel_before_start(sample3):
    r = PageRenderer(PdfDocument.open(sample3).snapshot_bytes(), workers=1)
    import threading
    gate = threading.Event()
    r._pool.submit(gate.wait)          # occupy the only worker
    fut = r.render_async(0, 1.0)
    r.bump_generation()                # cancel while queued
    gate.set()
    assert fut.result(timeout=10) is None


def test_page_ops(sample3):
    d = PdfDocument.open(sample3)
    pm.insert_blank(d, 1)
    assert d.page_count == 4 and d.modified
    pm.duplicate_page(d, 0)
    assert d.page_count == 5
    pm.delete_pages(d, [1])
    assert d.page_count == 4
    pm.rotate_pages(d, [0], 90)
    assert int(d.pdf.pages[0].Rotate) == 90
    w, h = d.page_size(0)
    assert (round(w), round(h)) == (842, 595)  # 90° rotation swaps axes of A4
    with pytest.raises(ValueError):
        pm.delete_pages(d, [0, 1, 2, 3])


def test_reorder_preserves_content(sample3):
    d = PdfDocument.open(sample3)
    pm.reorder_pages(d, [2, 0, 1])
    r = PageRenderer(d.snapshot_bytes())
    assert d.page_count == 3
    assert [("gamma" in r.page_text(0)), ("alpha" in r.page_text(1)), ("beta" in r.page_text(2))] == [True] * 3


def test_merge_and_split(tmp_path, sample3):
    other = make_pdf(tmp_path / "two.pdf", ["delta", "epsilon"])
    m = pm.merge([sample3, other])
    assert m.page_count == 5
    parts = pm.split_by_ranges(m, parse_page_ranges("1-2,5", 5))
    assert [p.page_count for p in parts] == [2, 1]
    with pytest.raises(ValueError):
        parse_page_ranges("9", 5)


def test_save_as_leaves_original_untouched(tmp_path, sample3):
    before = sha(sample3)
    d = PdfDocument.open(sample3)
    pm.rotate_pages(d, [0], 90)
    out = save_atomic(d, tmp_path / "copy.pdf")
    assert sha(sample3) == before
    assert PdfDocument.open(out).page_count == 3
    assert not list(tmp_path.glob(".n1cepdf-*"))


def test_encrypted_flow(encrypted, tmp_path):
    with pytest.raises(PasswordRequired):
        PdfDocument.open(encrypted)
    with pytest.raises(WrongPassword):
        PdfDocument.open(encrypted, "nope")
    d = PdfDocument.open(encrypted, "secret")
    assert d.encryption_info().encrypted
    assert d.page_count == 3
    with pytest.raises(EncryptedSaveRefused):
        save_atomic(d, tmp_path / "plain.pdf")
    out = save_atomic(d, tmp_path / "plain.pdf", allow_decrypt=True)
    assert not pikepdf.open(out).is_encrypted


def test_english_russian_romanian_extraction(tmp_path):
    if DEJAVU is None:
        pytest.skip("no Unicode TrueType font")
    p = make_pdf(tmp_path / "m.pdf", ["Hello world", "Привет мир", "Șoarece țânțar"], unicode_font=True)
    r = PageRenderer(PdfDocument.open(p).snapshot_bytes())
    assert "Hello" in r.page_text(0) and "Привет" in r.page_text(1) and "țânțar" in r.page_text(2)


def test_font_discovery_returns_ttf_or_none():
    f = find_unicode_font()
    assert f is None or (f.suffix.lower() == ".ttf" and f.is_file())


def test_outline_nested(tmp_path):
    p = make_pdf(tmp_path / "o.pdf", ["a", "b", "c"], outline=True)
    r = PageRenderer(PdfDocument.open(p).snapshot_bytes())
    o = r.outline()
    assert [(e.level, e.page) for e in o] == [(0, 0), (1, 1), (0, 2)]
    assert o[0].title == "Chapter 1"


def test_no_outline_is_empty(sample3):
    assert PageRenderer(PdfDocument.open(sample3).snapshot_bytes()).outline() == []


def test_search_spans_snippets_and_cancel(sample3):
    r = PageRenderer(PdfDocument.open(sample3).snapshot_bytes())
    hit = r.search("page")[0]
    assert hit.spans and "page" in hit.snippets[0]
    ev = threading.Event(); ev.set()
    assert r.search("page", cancel=ev) == []


def test_selection_geometry_and_text(sample3):
    r = PageRenderer(PdfDocument.open(sample3).snapshot_bytes())
    w, h = r.page_size(0)
    s = 96 / 72
    # 'alpha page' is drawn at x=72pt, baseline y=700pt -> device y ~ (h-700-5)*s
    idx = r.char_at(0, w * s, h * s, 80 * s, (h - 705) * s)
    assert idx is not None and r.selected_text(0, 0, idx).startswith("a")
    assert r.selected_text(0, 0, r.char_count(0) - 1).startswith("alpha page")
    rects = r.selection_rects_px(0, w * s, h * s, 0, 9)
    assert len(rects) == 1 and rects[0][0] == pytest.approx(72 * s, abs=3) and rects[0][2] > 50
    assert r.char_at(0, w * s, h * s, 5, 5) is None  # empty corner


def test_rotated_page_geometry(sample3):
    d = PdfDocument.open(sample3)
    pm.rotate_pages(d, [0], 90)
    r = PageRenderer(d.snapshot_bytes())
    w, h = r.page_size(0)
    assert w > h
    rects = r.selection_rects_px(0, w, h, 0, 9)
    assert rects and 0 <= rects[0][0] <= w and 0 <= rects[0][1] <= h


def test_blank_document_and_mark_saved(tmp_path):
    d = PdfDocument.new_blank()
    assert d.page_count == 1 and d.path is None and d.modified
    out = save_atomic(d, tmp_path / "b.pdf")
    d.mark_saved(out)
    assert not d.modified and d.display_name == "b.pdf"


def test_non_cancellable_render_survives_generation_bump(sample3):
    r = PageRenderer(PdfDocument.open(sample3).snapshot_bytes(), workers=1)
    gate = threading.Event()
    r._pool.submit(gate.wait)
    fut = r.render_async(0, 0.2, cancellable=False)
    r.bump_generation(); gate.set()
    assert fut.result(timeout=10) is not None


def test_recent_files(tmp_path, sample3):
    rf = RecentFiles(tmp_path / "cfg" / "recent.json")
    assert rf.list() == []
    rf.add(sample3); rf.add(sample3)
    assert rf.list() == [str(sample3.resolve())]
    rf.clear()
    assert rf.list() == []


def test_closed_renderer_refuses_late_workers(sample3):
    r = PageRenderer(PdfDocument.open(sample3).snapshot_bytes())
    r.render_sync(0, 0.3)
    r.close()
    with pytest.raises(RendererClosed):
        r.render_sync(0, 0.3)
    with pytest.raises(RendererClosed):
        r.search("alpha")
    assert r.char_at(0, 100, 100, 5, 5) is None
    r.close()  # idempotent


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc (Linux)")
def test_open_does_not_hold_source_file_handle(sample3):
    """A held handle blocks os.replace over the original on Windows (Save) and locks the file."""
    d = PdfDocument.open(sample3)
    held = []
    for fd in os.listdir("/proc/self/fd"):
        try:
            if os.readlink(f"/proc/self/fd/{fd}") == str(sample3):
                held.append(fd)
        except OSError:
            pass
    assert held == [] and d.page_count == 3


def test_save_over_own_source_path(sample3):
    d = PdfDocument.open(sample3)
    pm.rotate_pages(d, [0], 90)
    save_atomic(d, sample3)               # overwrite the original that this document was opened from
    reopened = PdfDocument.open(sample3)  # keep a reference: pages die with their Pdf
    assert int(reopened.pdf.pages[0].Rotate) == 90
