"""One process-wide lock for ALL PDFium use. PDFium is not thread-safe, not even across different
documents, so renderers, edit planning and previews (running on different threads) must serialize."""
import threading

PDFIUM_LOCK = threading.RLock()


def locked(fn):
    """Decorator: run fn while holding the PDFium lock (re-entrant)."""
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **k):
        with PDFIUM_LOCK:
            return fn(*a, **k)
    return wrapper
