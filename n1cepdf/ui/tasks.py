"""Background tasks. Worker threads run plain Python and report back through a queued signal,
so callbacks always execute on the GUI thread. Workers never touch widgets."""
from __future__ import annotations
import logging
import threading
from typing import Callable
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

log = logging.getLogger(__name__)


class TaskHandle:
    def __init__(self) -> None:
        self.cancel_event = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def cancel(self) -> None:
        self.cancel_event.set()


class _Job(QRunnable):
    def __init__(self, runner: "TaskRunner", tid: int, fn: Callable, handle: TaskHandle):
        super().__init__()
        self.setAutoDelete(False)  # lifetime managed by TaskRunner._jobs
        self._runner, self._tid, self._fn, self._handle = runner, tid, fn, handle

    def run(self) -> None:
        result, error = None, None
        try:
            result = self._fn(self._handle.cancel_event)
        except BaseException as e:  # noqa: BLE001 - reported to the GUI thread, never swallowed
            error = e
        try:
            self._runner.finished.emit(self._tid, result, error)
        except RuntimeError:
            pass  # runner already destroyed during shutdown


class TaskRunner(QObject):
    finished = Signal(int, object, object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._jobs: dict[int, tuple[_Job, TaskHandle, Callable, Callable | None]] = {}
        self._next = 0
        self.finished.connect(self._dispatch)

    @property
    def active(self) -> int:
        return len(self._jobs)

    def submit(self, fn: Callable, on_result: Callable, on_error: Callable | None = None) -> TaskHandle:
        """fn(cancel_event) runs on a pool thread; on_result/on_error run on the GUI thread."""
        self._next += 1
        handle = TaskHandle()
        job = _Job(self, self._next, fn, handle)
        self._jobs[self._next] = (job, handle, on_result, on_error)
        QThreadPool.globalInstance().start(job)
        return handle

    @Slot(int, object, object)
    def _dispatch(self, tid: int, result, error) -> None:
        entry = self._jobs.pop(tid, None)
        if entry is None:
            return
        _job, handle, on_result, on_error = entry
        if handle.cancelled:
            return
        if error is not None:
            log.warning("background task failed: %s", type(error).__name__)
            if on_error is not None:
                on_error(error)
            return
        on_result(result)

    def shutdown(self) -> None:
        for _job, handle, _r, _e in list(self._jobs.values()):
            handle.cancel()
        QThreadPool.globalInstance().waitForDone(5000)
        self._jobs.clear()
