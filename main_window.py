from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QMenu,
    QMessageBox,
    QTabBar,
    QTabWidget,
    QToolButton,
    QWidget,
)

from app_data import get_app_data_dir
from help_dialog import UserGuideDialog
from session_lock import SessionLock
from tabs import CompareGroupsTab, CompareUsersTab, NetworkingTab, SearchTab
from ui_common import wait_for_workers
from version import APP_NAME, __version__


class EqualWidthTabBar(QTabBar):
    """Every tab as wide as the widest one, keeping the native tab drawing."""

    def tabSizeHint(self, index):  # noqa: N802 (Qt override)
        hint = super().tabSizeHint(index)
        widest = max(super(EqualWidthTabBar, self).tabSizeHint(i).width() for i in range(self.count()))
        return QSize(widest, hint.height())


class MainWindow(QMainWindow):
    def __init__(self, client):
        super().__init__()
        self.client = client
        self.setWindowTitle(f"{APP_NAME} {__version__} - {client.bound_identity or client.username}")
        self.resize(1250, 700)

        self.networking_tab = NetworkingTab()

        tabs = QTabWidget()
        tabs.setTabBar(EqualWidthTabBar())
        tabs.addTab(SearchTab(client), "Search")
        tabs.addTab(CompareUsersTab(client), "Compare Users")
        tabs.addTab(CompareGroupsTab(client), "Compare Groups")
        tabs.addTab(self.networking_tab, "Networking")
        self.setCentralWidget(tabs)

        self.open_logs_action = QAction("Open Logs Folder", self)
        # A tooltip, not a status tip: this window has no status bar to show status tips in.
        self.open_logs_action.setToolTip(f"{get_app_data_dir()}\naudit.log, app.log, settings.json")
        self.open_logs_action.triggered.connect(self.open_logs_folder)
        self.user_guide_action = QAction("User Guide", self)
        self.user_guide_action.setShortcut(QKeySequence.HelpContents)  # F1
        self.user_guide_action.triggered.connect(self.show_user_guide)
        self.addAction(self.user_guide_action)  # so F1 works from anywhere in the window, menu closed
        self._user_guide = None

        help_menu = QMenu(self)
        help_menu.setToolTipsVisible(True)
        help_menu.addAction(self.user_guide_action)
        help_menu.addSeparator()
        help_menu.addAction(self.open_logs_action)

        # Help sits in the tab row at the far right instead of a separate menu bar row.
        self.help_button = QToolButton()
        self.help_button.setText("Help")
        self.help_button.setMenu(help_menu)
        self.help_button.setPopupMode(QToolButton.InstantPopup)
        self.help_button.setAutoRaise(True)
        self.help_button.setStyleSheet(
            "QToolButton { padding: 2px 14px; } QToolButton::menu-indicator { image: none; }"
        )
        self.help_button.setFixedHeight(tabs.tabBar().sizeHint().height())
        help_corner = QWidget()
        corner_layout = QHBoxLayout(help_corner)
        corner_layout.setContentsMargins(0, 0, 6, 0)  # keep it off the window edge
        corner_layout.addWidget(self.help_button)
        tabs.setCornerWidget(help_corner, Qt.TopRightCorner)

        self.session_lock = SessionLock(self, client)
        self.session_lock.start()

    def show_user_guide(self):
        # One guide window. Parented to this window so the session lock hides and restores it.
        if self._user_guide is None:
            self._user_guide = UserGuideDialog(self)
        self._user_guide.show()
        self._user_guide.raise_()
        self._user_guide.activateWindow()

    def open_logs_folder(self):
        """Show the local folder holding the audit log, app log, and settings."""
        folder = get_app_data_dir()
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(folder)):
            QMessageBox.information(
                self,
                "Logs Folder",
                f"The logs folder could not be opened automatically. It is located at:\n\n{folder}",
            )

    def closeEvent(self, event):
        self.networking_tab.stop_running_command()
        wait_for_workers()
        self.client.close()
        super().closeEvent(event)
