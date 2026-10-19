from PySide6.QtCore import Qt, QTimer
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

from audit_log import log_event
from ui_common import format_dt, run_async

USER_COLUMNS = ["Username", "Name", "Email", "Locked Out", "Disabled", "Last Logon"]

TRUNCATED_MESSAGE = (
    "The directory server stopped returning results before the search finished. "
    "Narrow your search term to see the full set of matches."
)


def _unlock_users(client, users):
    """Returns [(user, error_or_None)]. Runs on a worker thread."""
    results = []
    for user in users:
        try:
            client.unlock_account(user.distinguished_name)
        except Exception as exc:
            log_event(
                client.bound_identity,
                "unlock_account",
                user.sam_account_name,
                f"{user.distinguished_name} | {exc}",
                outcome="failed",
            )
            results.append((user, str(exc)))
        else:
            log_event(client.bound_identity, "unlock_account", user.sam_account_name, user.distinguished_name)
            results.append((user, None))
    return results


def _check_disable(client, users):
    """Returns [(user, block_reason_or_None)]. Runs on a worker thread."""
    return [(user, client.disable_block_reason(user)) for user in users]


def _disable_users(client, users):
    """Returns [(user, error_or_None)]. Runs on a worker thread."""
    results = []
    for user in users:
        try:
            # Re-checked right before the write, in case group membership changed since the prompt.
            reason = client.disable_block_reason(user)
            if reason:
                raise RuntimeError(f"refused, {reason}")
            client.disable_account(user.distinguished_name)
        except Exception as exc:
            log_event(
                client.bound_identity,
                "disable_account",
                user.sam_account_name,
                f"{user.distinguished_name} | {exc}",
                outcome="failed",
            )
            results.append((user, str(exc)))
        else:
            log_event(client.bound_identity, "disable_account", user.sam_account_name, user.distinguished_name)
            results.append((user, None))
    return results


def _summarize(results, verb):
    done = [user.sam_account_name for user, error in results if error is None]
    failed = [f"- {user.sam_account_name}: {error}" for user, error in results if error is not None]
    parts = []
    if done:
        parts.append(f"{verb}: " + ", ".join(done))
    if failed:
        parts.append("Failed:\n" + "\n".join(failed))
    return "\n\n".join(parts), bool(failed)


class SearchTab(QWidget):
    def __init__(self, client):
        super().__init__()
        self.client = client
        self._results = []
        self._groups_request = 0

        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("Search by username, name, or email")
        self.query_edit.returnPressed.connect(self._search)

        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self._search)

        self.unlock_button = QPushButton("Unlock Selected")
        self.unlock_button.setObjectName("unlockButton")
        self.unlock_button.clicked.connect(self._unlock_selected)

        self.disable_button = QPushButton("Disable Selected")
        self.disable_button.setObjectName("disableButton")
        self.disable_button.clicked.connect(self._disable_selected)

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

        self.groups_list = QTableWidget(0, 2)
        self.groups_list.horizontalHeader().setVisible(False)
        self.groups_list.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.groups_list.verticalHeader().setVisible(False)
        self.groups_list.setShowGrid(False)
        self.groups_list.setEditTriggers(QTableWidget.NoEditTriggers)

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
        actions_row = QHBoxLayout()
        actions_row.addWidget(self.unlock_button)
        actions_row.addWidget(self.disable_button)
        layout.addLayout(actions_row)

    def _search(self):
        term = self.query_edit.text().strip()
        if not term:
            return
        self._set_busy(True)
        run_async(
            self.client.search_users,
            term,
            on_success=self._on_search_succeeded,
            on_failure=lambda message: QMessageBox.critical(self, "Search failed", message),
            on_finished=lambda: self._set_busy(False),
        )

    def _set_busy(self, busy):
        self.progress_bar.setVisible(busy)
        self.query_edit.setEnabled(not busy)
        self.search_button.setEnabled(not busy)
        self.unlock_button.setEnabled(not busy)
        self.disable_button.setEnabled(not busy)

    def _on_search_succeeded(self, result):
        self._results = result.items
        self._populate_table()
        if result.truncated:
            QMessageBox.warning(self, "Results truncated", TRUNCATED_MESSAGE)

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

    def _selected_users(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        return [self._results[row] for row in rows]

    def _show_groups_message(self, text):
        self.groups_list.clearSpans()
        self.groups_list.setRowCount(1)
        self.groups_list.setSpan(0, 0, 1, 2)
        self.groups_list.setItem(0, 0, QTableWidgetItem(text))

    def _update_details(self):
        # Any group lookup still in flight is now stale; its result is ignored when it lands.
        self._groups_request += 1
        users = self._selected_users()
        if not users:
            self.details_label.setText("Select an account to view details.")
            self.groups_list.setRowCount(0)
            return
        if len(users) > 1:
            self.details_label.setText("Multiple accounts selected. Select one to view details.")
            self.groups_list.setRowCount(0)
            return

        user = users[0]
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

        self._show_groups_message("Loading groups...")
        request = self._groups_request
        # Resolves nested group membership, not just directly assigned groups.
        run_async(
            self.client.get_effective_groups_with_descriptions,
            user.distinguished_name,
            on_success=lambda groups: self._fill_groups(request, groups),
            on_failure=lambda message: self._on_groups_failed(request, message),
        )

    def _on_groups_failed(self, request, message):
        if request == self._groups_request:
            self._show_groups_message(f"Error loading groups: {message}")

    def _fill_groups(self, request, groups):
        if request != self._groups_request:
            return
        self.groups_list.clearSpans()
        # Fill top to bottom, left column first, so the list still reads alphabetically.
        rows_needed = (len(groups) + 1) // 2
        self.groups_list.setRowCount(rows_needed)
        for index, (name, description) in enumerate(groups):
            item = QTableWidgetItem(name)
            item.setToolTip(description or "No description")
            self.groups_list.setItem(index % rows_needed, index // rows_needed, item)

    def _unlock_selected(self):
        users = self._selected_users()
        if not users:
            return

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

        self._set_busy(True)
        run_async(
            _unlock_users,
            self.client,
            users,
            on_success=lambda results: self._on_action_finished(results, "Unlocked", "Unlock"),
            on_failure=lambda message: QMessageBox.critical(self, "Unlock failed", message),
            on_finished=lambda: self._set_busy(False),
        )

    def _disable_selected(self):
        users = [user for user in self._selected_users() if not user.disabled]
        if not users:
            if self._selected_users():
                QMessageBox.information(self, "Already disabled", "The selected account(s) are already disabled.")
            return

        self._set_busy(True)
        run_async(
            _check_disable,
            self.client,
            users,
            on_success=self._confirm_disable,
            on_failure=lambda message: QMessageBox.critical(self, "Disable failed", message),
            on_finished=lambda: self._set_busy(False),
        )

    def _confirm_disable(self, checks):
        allowed = [user for user, reason in checks if reason is None]
        blocked = [(user, reason) for user, reason in checks if reason is not None]
        for user, reason in blocked:
            log_event(
                self.client.bound_identity,
                "disable_account",
                user.sam_account_name,
                f"{user.distinguished_name} | {reason}",
                outcome="blocked",
            )

        blocked_text = "\n".join(f"- {user.sam_account_name}: {reason}" for user, reason in blocked)
        if not allowed:
            QMessageBox.warning(
                self,
                "Disable not allowed",
                f"None of the selected accounts can be disabled from this tool.\n\n{blocked_text}\n\n"
                "Use ADUC if this is intentional.",
            )
            return

        names = "\n".join(f"- {user.sam_account_name}" for user in allowed)
        message = f"Disable the following account(s)?\n\n{names}"
        if blocked:
            message += f"\n\nThese will be skipped:\n{blocked_text}"
        confirm = QMessageBox.question(
            self, "Confirm Disable", message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        self._set_busy(True)
        run_async(
            _disable_users,
            self.client,
            allowed,
            on_success=lambda results: self._on_action_finished(results, "Disabled", "Disable"),
            on_failure=lambda message: QMessageBox.critical(self, "Disable failed", message),
            on_finished=lambda: self._set_busy(False),
        )

    def _on_action_finished(self, results, done_title, action):
        text, any_failed = _summarize(results, done_title)
        if any_failed:
            QMessageBox.warning(self, f"{action} finished with errors", text)
        else:
            QMessageBox.information(self, done_title, text)
        if any(error is None for _, error in results):
            # Deferred so it starts after this worker's finished signal clears the busy state.
            QTimer.singleShot(0, self._search)
