from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tabs.search_tab import TRUNCATED_MESSAGE
from ui_common import format_dt, run_async

COMPUTER_COLUMNS = ["Name", "DNS Hostname", "Operating System", "Disabled", "Last Logon"]


class ComputersTab(QWidget):
    def __init__(self, client):
        super().__init__()
        self.client = client
        self._results = []

        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("Search by computer name or DNS hostname")
        self.query_edit.returnPressed.connect(self._search)

        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self._search)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indeterminate
        self.progress_bar.setVisible(False)

        self.table = QTableWidget(0, len(COMPUTER_COLUMNS))
        self.table.setHorizontalHeaderLabels(COMPUTER_COLUMNS)
        header = self.table.horizontalHeader()
        for col in range(len(COMPUTER_COLUMNS)):
            header.setSectionResizeMode(
                col,
                QHeaderView.Stretch if COMPUTER_COLUMNS[col] == "DNS Hostname" else QHeaderView.ResizeToContents,
            )
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.itemSelectionChanged.connect(self._update_details)

        self.details_label = QLabel("Select a computer to view details.")
        self.details_label.setWordWrap(True)
        self.details_label.setAlignment(Qt.AlignTop)
        details_box = QGroupBox("Computer Details")
        details_layout = QVBoxLayout(details_box)
        details_layout.addWidget(self.details_label)

        search_row = QHBoxLayout()
        search_row.addWidget(self.query_edit)
        search_row.addWidget(self.search_button)

        layout = QVBoxLayout(self)
        layout.addLayout(search_row)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.table)
        layout.addWidget(details_box)

    def _search(self):
        term = self.query_edit.text().strip()
        if not term:
            return
        self._set_busy(True)
        run_async(
            self.client.search_computers,
            term,
            on_success=self._on_search_succeeded,
            on_failure=lambda message: QMessageBox.critical(self, "Search failed", message),
            on_finished=lambda: self._set_busy(False),
        )

    def _set_busy(self, busy):
        self.progress_bar.setVisible(busy)
        self.query_edit.setEnabled(not busy)
        self.search_button.setEnabled(not busy)

    def _on_search_succeeded(self, result):
        self._results = result.items
        self._populate_table()
        if result.truncated:
            QMessageBox.warning(self, "Results truncated", TRUNCATED_MESSAGE)

    def _populate_table(self):
        self.table.setRowCount(len(self._results))
        for row, computer in enumerate(self._results):
            values = [
                computer.common_name,
                computer.dns_hostname,
                computer.operating_system,
                "Yes" if computer.disabled else "No",
                format_dt(computer.last_logon),
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self._update_details()

    def _update_details(self):
        rows = {index.row() for index in self.table.selectedIndexes()}
        if not rows:
            self.details_label.setText("Select a computer to view details.")
            return
        if len(rows) > 1:
            self.details_label.setText("Multiple computers selected. Select one to view details.")
            return

        computer = self._results[next(iter(rows))]
        lines = [
            f"{computer.common_name} ({computer.sam_account_name})",
            f"DNS Hostname: {computer.dns_hostname or 'N/A'}",
            f"Operating System: {computer.operating_system or 'N/A'}",
            f"OS Version: {computer.operating_system_version or 'N/A'}",
            f"Description: {computer.description or 'N/A'}",
            f"Organizational Unit: {computer.organizational_unit or 'N/A'}",
            f"Created: {format_dt(computer.when_created, never='Unknown')}",
            f"Last Modified: {format_dt(computer.when_changed, never='Unknown')}",
            f"Last Logon (approx., replication delay up to 14 days): {format_dt(computer.last_logon)}",
            f"Disabled: {'Yes' if computer.disabled else 'No'}",
        ]
        self.details_label.setText("\n".join(lines))
