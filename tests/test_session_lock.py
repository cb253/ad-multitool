import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from session_lock import IdleWatcher  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _pump(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_idle_timeout_fires_without_input(app):
    watcher = IdleWatcher(100)
    fired = []
    watcher.idle_timeout.connect(lambda: fired.append(True))
    watcher.start()
    _pump(300)
    assert fired == [True]


def test_input_resets_the_idle_timer(app):
    watcher = IdleWatcher(200)
    fired = []
    watcher.idle_timeout.connect(lambda: fired.append(True))
    widget = QWidget()
    watcher.start()
    for _ in range(4):
        _pump(100)
        QCoreApplication.sendEvent(widget, QKeyEvent(QEvent.KeyPress, Qt.Key_A, Qt.NoModifier))
    assert fired == []
    _pump(400)
    assert fired == [True]
