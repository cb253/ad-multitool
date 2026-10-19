from PySide6.QtWidgets import QMainWindow, QTabWidget

from tabs import CompareGroupsTab, CompareUsersTab, ComputersTab, NetworkingTab, SearchTab
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
        tabs.addTab(SearchTab(client), "Search && Unlock")
        tabs.addTab(ComputersTab(client), "Computers")
        tabs.addTab(CompareUsersTab(client), "Compare Users")
        tabs.addTab(CompareGroupsTab(client), "Compare Groups")
        tabs.addTab(self.networking_tab, "Networking")
        self.setCentralWidget(tabs)

    def closeEvent(self, event):
        self.networking_tab.stop_running_command()
        wait_for_workers()
        self.client.close()
        super().closeEvent(event)
