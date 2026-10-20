"""Small helpers shared by the tabs: background workers, formatting, colors."""

import logging

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QColor

SHARED_COLOR = QColor(27, 94, 32)  # dark green
DIFF_COLOR = QColor(140, 20, 20)  # dark red
COMPARE_TEXT_COLOR = QColor(255, 255, 255)  # white, kept fixed for contrast against the colors above

_logger = logging.getLogger(__name__)

# Strong references to running workers. A QThread that is garbage collected
# while still running aborts the process, so nothing may drop the last reference early.
_active_workers = set()


def format_dt(value, never="Never"):
    return value.strftime("%Y-%m-%d %H:%M") if value else never


def style_button(button, role):
    """Color a button via the app stylesheet. role is "success" (green) or "danger" (red)."""
    button.setProperty("role", role)


class AsyncWorker(QThread):
    """Runs a single blocking call off the UI thread. Accepts any return type."""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, func, *args):
        super().__init__()
        self.func = func
        self.args = args

    def run(self):
        try:
            result = self.func(*self.args)
        except Exception as exc:
            _logger.exception("Background task %s failed", getattr(self.func, "__name__", self.func))
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)


def run_async(func, *args, on_success=None, on_failure=None, on_finished=None):
    """Start func(*args) on a worker thread and route its outcome back to the UI thread."""
    worker = AsyncWorker(func, *args)
    _active_workers.add(worker)
    if on_success:
        worker.succeeded.connect(on_success)
    if on_failure:
        worker.failed.connect(on_failure)
    if on_finished:
        worker.finished.connect(on_finished)
    worker.finished.connect(lambda: _active_workers.discard(worker))
    worker.start()
    return worker


def wait_for_workers(timeout_ms=5000):
    """Give in-flight LDAP calls a chance to finish before the app exits."""
    for worker in list(_active_workers):
        worker.wait(timeout_ms)
