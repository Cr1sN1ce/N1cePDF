import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # headless unless the caller overrides

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox  # noqa: E402
from n1cepdf.services.recent import RecentFiles  # noqa: E402
from n1cepdf.ui.main_window import MainWindow  # noqa: E402
from n1cepdf.ui.theme import apply_theme  # noqa: E402


def _cleanup_window(win: MainWindow) -> None:
    """Prompt-free, explicit teardown. Production close/confirm behaviour is untouched (the tests
    below exercise it directly); this only stops pytest-qt's automatic w.close() from hitting the
    unsaved-changes dialog for tests that deliberately end with a modified document."""
    win.force_close_all()   # dispose tabs: cancels tasks, closes renderers/documents under doc_lock
    win.runner.shutdown()   # cancel remaining jobs and wait for pool threads to finish


@pytest.fixture
def window(qtbot, tmp_path, qapp):
    apply_theme(qapp, "dark")
    w = MainWindow(recent=RecentFiles(tmp_path / "recent.json"))
    # pytest-qt closes tracked widgets in its own teardown hook, which runs BEFORE fixture
    # finalizers; before_close_func is the supported hook that runs right before that w.close().
    qtbot.addWidget(w, before_close_func=_cleanup_window)
    w.show()
    qtbot.waitExposed(w)
    return w


@pytest.fixture(autouse=True)
def _no_blocking_dialogs(monkeypatch):
    """A modal dialog in a headless test blocks forever. Make any dialog that a test did not
    explicitly stub fail immediately with a clear message instead (tests override per-case)."""
    def boom(name):
        def _raise(*a, **k):
            raise AssertionError(f"unexpected modal dialog: {name}")
        return _raise
    for cls, names in ((QMessageBox, ("warning", "question", "information", "critical", "about", "exec")),
                       (QDialog, ("exec",)),
                       (QInputDialog, ("getText",)),
                       (QFileDialog, ("getOpenFileName", "getOpenFileNames", "getSaveFileName"))):
        for n in names:
            monkeypatch.setattr(cls, n, boom(f"{cls.__name__}.{n}"))
