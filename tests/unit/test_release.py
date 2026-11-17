"""Release-phase tests (Qt-free): paths with spaces/Unicode, safe saving, runtime helpers, packaging
metadata consistency and the packaged-build self-test (core steps)."""
import logging
import os
import re
import sys
import tomllib
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
import pytest
from PIL import Image

import n1cepdf
from n1cepdf import runtime
from n1cepdf.core.document import PdfDocument
from n1cepdf.core.errors import SaveValidationError
from n1cepdf.core.file_manager import same_file, save_atomic
from n1cepdf.edit.objects import TextBox
from n1cepdf.edit.session import EditSession
from tests.conftest import make_pdf, sha

ROOT = Path(__file__).resolve().parents[2]


def _text(path, page=0):
    d = pdfium.PdfDocument(Path(path).read_bytes())
    try:
        return d[page].get_textpage().get_text_range()
    finally:
        d.close()


def test_unicode_and_space_paths_roundtrip(tmp_path):
    folder = tmp_path / "Мои документы Țară (копия) 1"
    folder.mkdir()
    src = make_pdf(folder / "договор №1 – contract.pdf", ["alpha", "beta"])
    d = PdfDocument.open(src)
    s = EditSession(d)
    s.add_object(TextBox(d.page_key(0), 72, 500, 300, 540, "edit", "Liberation Sans", 14))
    out = save_atomic(d, folder / "результат – rezultat final.pdf", session=s)
    d.pdf.close()
    assert out.name == "результат – rezultat final.pdf" and "edit" in _text(out)
    assert PdfDocument.open(out).page_count == 2
    assert not [p for p in folder.iterdir() if p.name.endswith(".tmp")]      # no temp litter


def test_same_file_detects_equivalent_paths(tmp_path):
    f = make_pdf(tmp_path / "a b.pdf", ["x"])
    sub = tmp_path / "sub"
    sub.mkdir()
    assert same_file(f, sub / ".." / "a b.pdf")
    assert not same_file(f, make_pdf(tmp_path / "other.pdf", ["y"]))
    assert not same_file(f, tmp_path / "does-not-exist.pdf")
    if os.name == "nt":                                                      # Windows is case-insensitive
        assert same_file(f, str(f).upper())


def test_save_into_missing_folder_reports_cleanly(sample3, tmp_path):
    d = PdfDocument.open(sample3)
    with pytest.raises(SaveValidationError, match="Could not write the file"):
        save_atomic(d, tmp_path / "no" / "such" / "dir" / "x.pdf")
    d.pdf.close()


def test_save_permission_denied_keeps_original_and_cleans_temp(sample3, tmp_path, monkeypatch):
    before = sha(sample3)
    d = PdfDocument.open(sample3)
    s = EditSession(d)
    s.add_object(TextBox(d.page_key(0), 72, 500, 300, 540, "never lands", "Liberation Sans", 14))

    def denied(*a, **k):
        raise PermissionError(13, "Access is denied")
    monkeypatch.setattr(os, "replace", denied)                               # destination open elsewhere (Windows)
    with pytest.raises(SaveValidationError, match="access was denied"):
        save_atomic(d, sample3, session=s)
    assert sha(sample3) == before                                            # original untouched
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]    # temp file removed
    assert d.modified and len(s.layer.all()) == 1                           # edits survive the failed save
    d.pdf.close()


def test_resource_path_in_development_and_frozen(tmp_path, monkeypatch):
    dev = runtime.resource_path("assets/n1cepdf.png")
    assert dev == ROOT / "assets" / "n1cepdf.png" and dev.is_file()
    bundle = tmp_path / "_internal"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    assert runtime.is_frozen() and runtime.resource_path("assets/x.png") == bundle / "assets" / "x.png"


def test_logging_goes_to_file_and_is_idempotent(tmp_path):
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        p1 = runtime.setup_logging(tmp_path / "logs")
        p2 = runtime.setup_logging(tmp_path / "other")
        assert p1 == p2 == tmp_path / "logs" / "n1cepdf.log"
        logging.getLogger("n1cepdf.test").error("Ünï — запись")
        for h in root.handlers:
            h.flush()
        assert "запись" in p1.read_text("utf-8")
        assert sum(getattr(h, runtime._HANDLER_TAG, False) for h in root.handlers) == 1
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()


def test_excepthook_logs_and_notifies_once(tmp_path):
    import threading
    seen = []
    old_hook, old_thread_hook = sys.excepthook, threading.excepthook
    try:
        runtime.install_excepthook(seen.append)
        try:
            raise ValueError("boom")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        assert seen == ["An unexpected error occurred (ValueError)."]
    finally:
        sys.excepthook, threading.excepthook = old_hook, old_thread_hook


def test_windows_app_id_is_noop_off_windows():
    if not sys.platform.startswith("win"):
        assert runtime.set_windows_app_id() is False


def test_version_is_consistent_everywhere():
    py = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    iss = (ROOT / "packaging" / "N1cePDF.iss").read_text("utf-8")
    iss_ver = re.search(r'#define\s+AppVersion\s+"([^"]+)"', iss).group(1)
    vi = (ROOT / "packaging" / "version_info.txt").read_text("utf-8")
    assert n1cepdf.__version__ == py == iss_ver == "1.1.0"
    assert "filevers=(1, 1, 0, 0)" in vi and "'1.1.0.0'" in vi


def test_icon_has_all_standard_sizes():
    ico = Image.open(ROOT / "assets" / "n1cepdf.ico")
    assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= set(ico.info["sizes"])
    png = Image.open(ROOT / "assets" / "n1cepdf.png")
    assert png.size == (256, 256) and png.mode == "RGBA"


def test_packaged_build_selftest_core_steps(tmp_path):
    import json
    from n1cepdf.selftest import main
    result = tmp_path / "result.json"
    assert main(["N1cePDF", "--self-test", str(result), "--no-gui"]) == 0
    data = json.loads(result.read_text("utf-8"))
    assert data["ok"] and len(data["steps"]) == 8 and all(s["ok"] for s in data["steps"])
