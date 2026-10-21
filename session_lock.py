"""Locks the session after a period with no keyboard or mouse input.

On lock the LDAP connection is closed (dropping the password ldap3 holds for
rebinding) and the main window is hidden, so an unattended workstation shows
neither directory data nor a live, privileged session.
"""

import logging

from PySide6.QtCore import QEvent, QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
)

from login_dialog import sign_in
from ui_common import run_async

IDLE_TIMEOUT_MINUTES = 10

_logger = logging.getLogger(__name__)

_ACTIVITY_EVENTS = {
    QEvent.KeyPress,
    QEvent.MouseButtonPress,
    QEvent.MouseMove,
    QEvent.Wheel,
}


class IdleWatcher(QObject):
    """Emits idle_timeout after timeout_ms with no user input anywhere in the app."""

    idle_timeout = Signal()

    def __init__(self, timeout_ms, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(timeout_ms)
        self._timer.timeout.connect(self.idle_timeout)

    def start(self):
        QApplication.instance().installEventFilter(self)
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def eventFilter(self, watched, event):  # noqa: N802 (Qt override)
        if event.type() in _ACTIVITY_EVENTS and self._timer.isActive():
            self._timer.start()
        return False


class UnlockDialog(QDialog):
    """Asks for the signed-in user's password again. Rejecting it means sign out."""

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.setWindowTitle("Session Locked")

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.status_label = QLabel(
            f"The session was locked after {IDLE_TIMEOUT_MINUTES} minutes of inactivity.\n"
            f"Enter the password for {client.bound_identity or client.username} to continue."
        )

        self.buttons = QDialogButtonBox()
        self.unlock_button = self.buttons.addButton("Unlock", QDialogButtonBox.AcceptRole)
        self.buttons.addButton("Sign Out", QDialogButtonBox.RejectRole)
        self.buttons.accepted.connect(self._try_unlock)
        self.buttons.rejected.connect(self.reject)

        form = QFormLayout(self)
        form.addRow(self.status_label)
        form.addRow("Password", self.password_edit)
        form.addRow(self.buttons)
        self.password_edit.setFocus()

    def _set_busy(self, busy):
        self.password_edit.setEnabled(not busy)
        self.unlock_button.setEnabled(not busy)

    def _try_unlock(self):
        password = self.password_edit.text()
        self.password_edit.clear()
        if not password:
            return
        self._set_busy(True)
        run_async(
            sign_in,
            self.client.reauthenticate,
            self.client,
            password,
            on_success=lambda _client: self.accept(),
            on_failure=self._on_unlock_failed,
            on_finished=lambda: self._set_busy(False),
        )

    def _on_unlock_failed(self, message):
        _logger.warning("Session unlock failed for %s", self.client.bound_identity)
        QMessageBox.critical(self, "Unlock failed", message)
        self.password_edit.setFocus()


class SessionLock(QObject):
    """Wires the idle watcher to a window: lock, hide, re-authenticate, restore."""

    def __init__(self, window, client, timeout_ms=IDLE_TIMEOUT_MINUTES * 60 * 1000):
        super().__init__(window)
        self.window = window
        self.client = client
        self.locked = False
        self.watcher = IdleWatcher(timeout_ms, self)
        self.watcher.idle_timeout.connect(self.lock)

    def start(self):
        self.watcher.start()

    def lock(self):
        if self.locked:
            return
        self.locked = True
        _logger.info("Locking session for %s after inactivity", self.client.bound_identity)
        self.client.close()
        was_maximized = self.window.isMaximized()
        # Hide every other window too (lockout history, open prompts). Hiding a modal prompt
        # cancels it, so a confirmation left open cannot be accepted after the lock.
        other_windows = [
            widget for widget in QApplication.topLevelWidgets() if widget is not self.window and widget.isVisible()
        ]
        for widget in other_windows:
            widget.hide()
        self.window.hide()

        dialog = UnlockDialog(self.client)
        unlocked = dialog.exec() == QDialog.Accepted
        self.locked = False

        if not unlocked:
            _logger.info("Signed out from the session lock screen")
            self.window.close()
            QApplication.instance().quit()
            return

        _logger.info("Session unlocked for %s", self.client.bound_identity)
        if was_maximized:
            self.window.showMaximized()
        else:
            self.window.show()
        for widget in other_windows:
            if not widget.isModal():
                widget.show()
        self.watcher.start()
