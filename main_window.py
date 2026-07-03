from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from audit_log import log_event

USER_COLUMNS = ["Username", "Name", "Email", "Locked Out", "Disabled", "Last Logon"]
COMPUTER_COLUMNS = ["Name", "DNS Hostname", "Operating System", "Disabled", "Last Logon"]

SHARED_COLOR = QColor(27, 94, 32)  # dark green
DIFF_COLOR = QColor(140, 20, 20)  # dark red
COMPARE_TEXT_COLOR = QColor(255, 255, 255)  # white, kept fixed for contrast against the colors above


def format_dt(value, never="Never"):
    return value.strftime("%Y-%m-%d %H:%M") if value else never


def _fetch_group_pair(client, dn_a, dn_b):
    return client.get_group_members(dn_a), client.get_group_members(dn_b)


def _fetch_user_comparison(client, name_a, name_b):
    user_a = client.get_user(name_a)
    user_b = client.get_user(name_b)
    groups_a = client.get_effective_groups(user_a.distinguished_name) if user_a else []
    groups_b = client.get_effective_groups(user_b.distinguished_name) if user_b else []
    return user_a, user_b, groups_a, groups_b


class _AsyncWorker(QThread):
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
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)


class SearchTab(QWidget):
    def __init__(self, client):
        super().__init__()
        self.client = client
        self._results = []

        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("Search by username, name, or email")
        self.query_edit.returnPressed.connect(self._search)

        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self._search)

        self.unlock_button = QPushButton("Unlock Selected")
        self.unlock_button.clicked.connect(self._unlock_selected)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indeterminate
        self.progress_bar.setVisible(False)

        self.table = QTableWidget(0, len(USER_COLUMNS))
        self.table.setHorizontalHeaderLabels(USER_COLUMNS)
        header = self.table.horizontalHeader()
        for col in range(len(USER_COLUMNS)):
            header.setSectionResizeMode(
                col,
                QHeaderView.Stretch if USER_COLUMNS[col] == "Email" else QHeaderView.ResizeToContents,
            )
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.itemSelectionChanged.connect(self._update_details)

        self.details_label = QLabel("Select an account to view details.")
        self.details_label.setWordWrap(True)
        self.details_label.setAlignment(Qt.AlignTop)

        self.groups_list = QListWidget()

        details_box = QGroupBox("Account Details")
        details_layout = QHBoxLayout(details_box)
        details_layout.addWidget(self.details_label, stretch=1)
        groups_column = QVBoxLayout()
        groups_column.addWidget(QLabel("Group Memberships"))
        groups_column.addWidget(self.groups_list)
        details_layout.addLayout(groups_column, stretch=2)

        search_row = QHBoxLayout()
        search_row.addWidget(self.query_edit)
        search_row.addWidget(self.search_button)

        layout = QVBoxLayout(self)
        layout.addLayout(search_row)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.table)
        layout.addWidget(details_box)
        layout.addWidget(self.unlock_button)

        self._worker = None

    def _search(self):
        term = self.query_edit.text().strip()
        if not term:
            return
        self._set_busy(True)
        self._worker = _AsyncWorker(self.client.search_users, term)
        self._worker.succeeded.connect(self._on_search_succeeded)
        self._worker.failed.connect(self._on_search_failed)
        self._worker.finished.connect(lambda: self._set_busy(False))
        self._worker.start()

    def _set_busy(self, busy):
        self.progress_bar.setVisible(busy)
        self.query_edit.setEnabled(not busy)
        self.search_button.setEnabled(not busy)
        self.unlock_button.setEnabled(not busy)

    def _on_search_succeeded(self, results):
        self._results = results
        self._populate_table()
        if self.client.last_search_truncated:
            QMessageBox.warning(
                self,
                "Results truncated",
                "The directory server stopped returning results before the search finished. "
                "Narrow your search term to see the full set of matches.",
            )

    def _on_search_failed(self, message):
        QMessageBox.critical(self, "Search failed", message)

    def _populate_table(self):
        self.table.setRowCount(len(self._results))
        for row, user in enumerate(self._results):
            values = [
                user.sam_account_name,
                user.common_name,
                user.email,
                "Yes" if user.is_locked_out else "No",
                "Yes" if user.disabled else "No",
                format_dt(user.last_logon),
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self._update_details()

    def _update_details(self):
        rows = {index.row() for index in self.table.selectedIndexes()}
        if not rows:
            self.details_label.setText("Select an account to view details.")
            self.groups_list.clear()
            return
        if len(rows) > 1:
            self.details_label.setText("Multiple accounts selected. Select one to view details.")
            self.groups_list.clear()
            return

        user = self._results[next(iter(rows))]
        lines = [
            f"{user.common_name} ({user.sam_account_name})",
            f"UPN: {user.user_principal_name or 'N/A'}",
            f"Email: {user.email or 'N/A'}",
            f"Title: {user.title or 'N/A'}",
            f"Department: {user.department or 'N/A'}",
            f"Office: {user.office or 'N/A'}",
            f"Phone: {user.phone or 'N/A'}",
            f"Manager: {user.manager_name or 'N/A'}",
            f"Description: {user.description or 'N/A'}",
            f"Organizational Unit: {user.organizational_unit or 'N/A'}",
            f"Created: {format_dt(user.when_created, never='Unknown')}",
            f"Last Modified: {format_dt(user.when_changed, never='Unknown')}",
            f"Password Last Set: {format_dt(user.password_last_set, never='Never (must change at next logon)')}",
            f"Account Expires: {format_dt(user.account_expires)}",
            f"Last Logon (approx., replication delay up to 14 days): {format_dt(user.last_logon)}",
            f"Locked Out: {'Yes, at ' + format_dt(user.locked_out_at) if user.is_locked_out else 'No'}",
            f"Disabled: {'Yes' if user.disabled else 'No'}",
        ]
        self.details_label.setText("\n".join(lines))

        self.groups_list.clear()
        try:
            # Resolves nested group membership, not just directly assigned groups.
            self.groups_list.addItems(self.client.get_effective_groups(user.distinguished_name))
        except Exception as exc:
            self.groups_list.addItem(f"Error loading groups: {exc}")

    def _unlock_selected(self):
        rows = {index.row() for index in self.table.selectedIndexes()}
        if not rows:
            return

        users = [self._results[row] for row in rows]
        names = "\n".join(f"- {user.sam_account_name}" for user in users)
        confirm = QMessageBox.question(
            self,
            "Confirm Unlock",
            f"Unlock the following account(s)?\n\n{names}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        unlocked = []
        for user in users:
            try:
                self.client.unlock_account(user.distinguished_name)
                unlocked.append(user.sam_account_name)
                log_event(self.client.username, "unlock_account", user.sam_account_name, user.distinguished_name)
            except Exception as exc:
                QMessageBox.critical(self, "Unlock failed", f"{user.sam_account_name}: {exc}")
        if unlocked:
            QMessageBox.information(self, "Unlocked", ", ".join(unlocked))
            self._search()


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

        self._worker = None

    def _search(self):
        term = self.query_edit.text().strip()
        if not term:
            return
        self._set_busy(True)
        self._worker = _AsyncWorker(self.client.search_computers, term)
        self._worker.succeeded.connect(self._on_search_succeeded)
        self._worker.failed.connect(self._on_search_failed)
        self._worker.finished.connect(lambda: self._set_busy(False))
        self._worker.start()

    def _set_busy(self, busy):
        self.progress_bar.setVisible(busy)
        self.query_edit.setEnabled(not busy)
        self.search_button.setEnabled(not busy)

    def _on_search_succeeded(self, results):
        self._results = results
        self._populate_table()
        if self.client.last_search_truncated:
            QMessageBox.warning(
                self,
                "Results truncated",
                "The directory server stopped returning results before the search finished. "
                "Narrow your search term to see the full set of matches.",
            )

    def _on_search_failed(self, message):
        QMessageBox.critical(self, "Search failed", message)

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


class CompareGroupsTab(QWidget):
    def __init__(self, client):
        super().__init__()
        self.client = client

        self.group_a_edit = QLineEdit()
        self.group_a_edit.setPlaceholderText("CN=GroupA,OU=Groups,DC=example,DC=com")
        self.group_a_edit.returnPressed.connect(self._compare)
        self.group_b_edit = QLineEdit()
        self.group_b_edit.setPlaceholderText("CN=GroupB,OU=Groups,DC=example,DC=com")
        self.group_b_edit.returnPressed.connect(self._compare)

        self.compare_button = QPushButton("Compare")
        self.compare_button.clicked.connect(self._compare)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indeterminate
        self.progress_bar.setVisible(False)

        form = QVBoxLayout()
        form.addWidget(QLabel("Group A DN"))
        form.addWidget(self.group_a_edit)
        form.addWidget(QLabel("Group B DN"))
        form.addWidget(self.group_b_edit)
        form.addWidget(self.compare_button)

        self.only_a_table = self._make_table("Only in Group A")
        self.only_b_table = self._make_table("Only in Group B")
        self.both_table = self._make_table("In Both")

        tables_row = QHBoxLayout()
        tables_row.addLayout(self._wrap_with_label(self.only_a_table, "Only in Group A"))
        tables_row.addLayout(self._wrap_with_label(self.both_table, "In Both"))
        tables_row.addLayout(self._wrap_with_label(self.only_b_table, "Only in Group B"))

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.progress_bar)
        layout.addLayout(tables_row)

        self._worker = None

    def _make_table(self, _title):
        table = QTableWidget(0, 1)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setHorizontalHeaderLabels(["Username"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        return table

    def _wrap_with_label(self, table, title):
        column = QVBoxLayout()
        column.addWidget(QLabel(title))
        column.addWidget(table)
        return column

    def _compare(self):
        dn_a = self.group_a_edit.text().strip()
        dn_b = self.group_b_edit.text().strip()
        if not dn_a or not dn_b:
            return
        self._set_busy(True)
        self._worker = _AsyncWorker(_fetch_group_pair, self.client, dn_a, dn_b)
        self._worker.succeeded.connect(self._on_compare_succeeded)
        self._worker.failed.connect(self._on_compare_failed)
        self._worker.finished.connect(lambda: self._set_busy(False))
        self._worker.start()

    def _set_busy(self, busy):
        self.progress_bar.setVisible(busy)
        self.group_a_edit.setEnabled(not busy)
        self.group_b_edit.setEnabled(not busy)
        self.compare_button.setEnabled(not busy)

    def _on_compare_failed(self, message):
        QMessageBox.critical(self, "Compare failed", message)

    def _on_compare_succeeded(self, result):
        members_a, members_b = result
        names_a = {user.sam_account_name: user for user in members_a}
        names_b = {user.sam_account_name: user for user in members_b}

        only_a = sorted(set(names_a) - set(names_b))
        only_b = sorted(set(names_b) - set(names_a))
        both = sorted(set(names_a) & set(names_b))

        self._fill_column(self.only_a_table, only_a)
        self._fill_column(self.only_b_table, only_b)
        self._fill_column(self.both_table, both)

    def _fill_column(self, table, names):
        table.setRowCount(len(names))
        for row, name in enumerate(names):
            table.setItem(row, 0, QTableWidgetItem(name))


class CompareUsersTab(QWidget):
    def __init__(self, client):
        super().__init__()
        self.client = client

        self.user_a_edit = QLineEdit()
        self.user_a_edit.setPlaceholderText("Username A")
        self.user_a_edit.returnPressed.connect(self._compare)
        self.user_b_edit = QLineEdit()
        self.user_b_edit.setPlaceholderText("Username B")
        self.user_b_edit.returnPressed.connect(self._compare)

        self.compare_button = QPushButton("Compare")
        self.compare_button.clicked.connect(self._compare)

        form = QHBoxLayout()
        form.addWidget(QLabel("User A"))
        form.addWidget(self.user_a_edit)
        form.addWidget(QLabel("User B"))
        form.addWidget(self.user_b_edit)
        form.addWidget(self.compare_button)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indeterminate
        self.progress_bar.setVisible(False)

        self.summary_label = QLabel("")

        self.table = QTableWidget(0, 3)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setHorizontalHeaderLabels(["Group", "User A", "User B"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.table)

        self._worker = None

    def _compare(self):
        name_a = self.user_a_edit.text().strip()
        name_b = self.user_b_edit.text().strip()
        if not name_a or not name_b:
            return
        self._set_busy(True)
        self._worker = _AsyncWorker(_fetch_user_comparison, self.client, name_a, name_b)
        self._worker.succeeded.connect(self._on_compare_succeeded)
        self._worker.failed.connect(self._on_compare_failed)
        self._worker.finished.connect(lambda: self._set_busy(False))
        self._worker.start()

    def _set_busy(self, busy):
        self.progress_bar.setVisible(busy)
        self.user_a_edit.setEnabled(not busy)
        self.user_b_edit.setEnabled(not busy)
        self.compare_button.setEnabled(not busy)

    def _on_compare_failed(self, message):
        QMessageBox.critical(self, "Compare failed", message)

    def _on_compare_succeeded(self, result):
        name_a = self.user_a_edit.text().strip()
        name_b = self.user_b_edit.text().strip()
        user_a, user_b, groups_a, groups_b = result

        if user_a is None or user_b is None:
            missing = name_a if user_a is None else name_b
            QMessageBox.warning(self, "User not found", f"Could not find account '{missing}'")
            return

        groups_a = set(groups_a)
        groups_b = set(groups_b)
        all_groups = sorted(groups_a | groups_b, key=str.lower)

        self.summary_label.setText(
            f"{len(groups_a & groups_b)} shared | "
            f"{len(groups_a - groups_b)} only in {user_a.sam_account_name} | "
            f"{len(groups_b - groups_a)} only in {user_b.sam_account_name}"
        )

        self.table.setRowCount(len(all_groups))
        for row, group in enumerate(all_groups):
            in_a = group in groups_a
            in_b = group in groups_b
            color = SHARED_COLOR if in_a and in_b else DIFF_COLOR

            for col, text in enumerate([group, "Yes" if in_a else "No", "Yes" if in_b else "No"]):
                item = QTableWidgetItem(text)
                item.setBackground(color)
                item.setForeground(COMPARE_TEXT_COLOR)
                self.table.setItem(row, col, item)


class MainWindow(QMainWindow):
    def __init__(self, client):
        super().__init__()
        self.setWindowTitle("AD Multitool")
        self.resize(1250, 700)

        tabs = QTabWidget()
        tabs.addTab(SearchTab(client), "Search && Unlock")
        tabs.addTab(ComputersTab(client), "Computers")
        tabs.addTab(CompareGroupsTab(client), "Compare Groups")
        tabs.addTab(CompareUsersTab(client), "Compare Users")
        self.setCentralWidget(tabs)
