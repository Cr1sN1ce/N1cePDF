"""Deliver render-future results to the GUI thread via a queued signal."""
from __future__ import annotations
import logging
from concurrent.futures import Future
from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)


class RenderBridge(QObject):
    done = Signal(object)    # (tag, RenderedPage)
    failed = Signal(str)     # exception type name only (no document content)


def attach(future: Future, bridge: RenderBridge, tag=None) -> None:
    def cb(f: Future) -> None:
        try:
            if f.cancelled():
                return
            res = f.result()
            if res is None:  # cancelled by generation / renderer closed
                return
            bridge.done.emit((tag, res))
        except RuntimeError:
            return  # bridge already destroyed
        except Exception as e:  # noqa: BLE001
            log.warning("render failed: %s", type(e).__name__)
            try:
                bridge.failed.emit(type(e).__name__)
            except RuntimeError:
                pass
    future.add_done_callback(cb)
