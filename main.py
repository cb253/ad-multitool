import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler

from PySide6.QtWidgets import QApplication, QMessageBox

from app_data import get_app_data_dir
from login_dialog import LoginDialog
from main_window import MainWindow
from ui_common import wait_for_workers
from version import APP_NAME, __version__

APP_LOG_PATH = os.path.join(get_app_data_dir(), "app.log")

# The Windows 11 style marks selected cells with a thin accent bar on each
# cell's left edge. Fill the whole cell instead so a selected row reads as one block.
SELECTION_STYLE = """
QTableView, QListView {
    outline: 0;
}
QTableView::item:selected, QListView::item:selected {
    background-color: palette(highlight);
    color: palette(highlighted-text);
}

/* Windows 11 accent-family colors, so the action buttons sit alongside the selection blue. */
QPushButton#unlockButton, QPushButton#disableButton {
    color: white;
    border: none;
    border-radius: 4px;
    padding: 6px;
}
QPushButton#unlockButton { background-color: #107C10; }
QPushButton#unlockButton:hover { background-color: #138A13; }
QPushButton#unlockButton:pressed { background-color: #0B5A0B; }
QPushButton#disableButton { background-color: #C42B1C; }
QPushButton#disableButton:hover { background-color: #D13A2B; }
QPushButton#disableButton:pressed { background-color: #9A2216; }
QPushButton#unlockButton:disabled, QPushButton#disableButton:disabled {
    background-color: #3A3A3A;
    color: #8A8A8A;
}
"""


def configure_logging():
    handler = RotatingFileHandler(APP_LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    # ldap3 logs raw protocol traffic at DEBUG, which can include credentials. Keep it quiet.
    logging.getLogger("ldap3").setLevel(logging.WARNING)


def handle_uncaught_exception(exc_type, exc_value, exc_traceback):
    """The windowed exe has no console, so unhandled errors would otherwise vanish."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logging.getLogger(__name__).critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback))
    if QApplication.instance():
        summary = "".join(traceback.format_exception_only(exc_type, exc_value)).strip()
        QMessageBox.critical(
            None,
            "Unexpected error",
            f"{summary}\n\nDetails were written to:\n{APP_LOG_PATH}",
        )


def main():
    configure_logging()
    sys.excepthook = handle_uncaught_exception
    logging.getLogger(__name__).info("Starting %s %s", APP_NAME, __version__)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setStyleSheet(SELECTION_STYLE)

    login = LoginDialog()
    if login.exec() != LoginDialog.Accepted:
        wait_for_workers()
        return

    window = MainWindow(login.client)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
