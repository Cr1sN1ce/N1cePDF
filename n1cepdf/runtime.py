"""Runtime environment helpers (Qt-free, unit-testable): frozen/dev resource paths, file logging,
uncaught-exception logging and the Windows taskbar identity.

A windowed PyInstaller build has no console (sys.stdout / sys.stderr may be None), so everything that
used to land on stderr must go to a log file instead.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading
from pathlib import Path
from typing import Callable

APP_NAME = "N1cePDF"
# Must match AppUserModelID in packaging/N1cePDF.iss so pinned/desktop shortcuts and the running
# window share one taskbar button and icon.
APP_USER_MODEL_ID = "N1cePDF.Desktop"
_LOG_FORMAT = "%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"
_HANDLER_TAG = "_n1cepdf_file_handler"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_path(relative: str | Path) -> Path:
    """Absolute path of a bundled read-only resource.

    Installed/frozen build: PyInstaller's data directory (``sys._MEIPASS``; ``_internal`` for onedir).
    Development: the project root (the directory that contains ``assets/``).
    """
    if is_frozen() and getattr(sys, "_MEIPASS", None):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).resolve().parents[1]
    return base / relative


def log_dir() -> Path:
    """Per-user, writable location for log files (never the install directory)."""
    if sys.platform.startswith("win"):
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / APP_NAME
    elif sys.platform == "darwin":
        root = Path.home() / "Library/Logs" / APP_NAME
        return root
    else:
        root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "n1cepdf"
    return root / "logs"


def setup_logging(directory: Path | None = None, level: int = logging.INFO) -> Path | None:
    """Install one rotating file handler on the root logger (idempotent). Returns the log file path,
    or None if no writable location exists (logging then stays best-effort; the app must still start)."""
    root = logging.getLogger()
    root.setLevel(level)
    for h in root.handlers:
        if getattr(h, _HANDLER_TAG, False):
            return Path(h.baseFilename)
    target = directory or log_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            target / "n1cepdf.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8", delay=True)
    except OSError:
        return None
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    setattr(handler, _HANDLER_TAG, True)
    root.addHandler(handler)
    return target / "n1cepdf.log"


def install_excepthook(notify: Callable[[str], None] | None = None) -> None:
    """Log every uncaught exception (GUI thread and plain threads). ``notify`` — if given — is called with
    a short user-facing message, for exceptions on the main thread only."""
    log = logging.getLogger("n1cepdf.uncaught")
    reporting = {"active": False}

    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.error("uncaught exception", exc_info=(exc_type, exc, tb))
        if notify is not None and not reporting["active"] and threading.current_thread() is threading.main_thread():
            reporting["active"] = True            # a failing dialog must not recurse
            try:
                notify(f"An unexpected error occurred ({exc_type.__name__}).")
            except Exception:  # noqa: BLE001
                log.exception("could not show the error dialog")
            finally:
                reporting["active"] = False

    def thread_hook(args):
        hook(args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = hook
    threading.excepthook = thread_hook


def set_windows_app_id(app_id: str = APP_USER_MODEL_ID) -> bool:
    """Give the process its own taskbar identity (icon grouping). No-op on other platforms."""
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(str(app_id))  # type: ignore[attr-defined]
        return True
    except (AttributeError, OSError):
        return False
