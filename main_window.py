from PySide6.QtWidgets import QMainWindow, QTabWidget

from session_lock import SessionLock
from tabs import CompareGroupsTab, CompareUsersTab, NetworkingTab, SearchTab
from ui_common import wait_for_workers
from version import APP_NAME, __version__


class MainWindow(QMainWindow):
    def __init__(self, client):
        super().__init__()
        self.client = client
        self.setWindowTitle(f"{APP_NAME} {__version__} - {client.bound_identity or client.username}")
        self.resize(1250, 700)

        self.networking_tab = NetworkingTab()

        tabs = QTabWidget()
        tabs.addTab(SearchTab(client), "Search")
        tabs.addTab(CompareUsersTab(client), "Compare Users")
        tabs.addTab(CompareGroupsTab(client), "Compare Groups")
        tabs.addTab(self.networking_tab, "Networking")
        self.setCentralWidget(tabs)

        self.session_lock = SessionLock(self, client)
        self.session_lock.start()

    def closeEvent(self, event):
        self.networking_tab.stop_running_command()
        wait_for_workers()
        self.client.close()
        super().closeEvent(event)
