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
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from audit_log import log_event
from tabs.lockout_dialog import LockoutHistoryDialog
from ui_common import format_dt, run_async, style_button

RESULT_COLUMNS = ["Type", "Name", "Account / Host", "Email / OS", "Locked Out", "Disabled", "Last Logon"]
USER, COMPUTER = "user", "computer"

TRUNCATED_MESSAGE = (
    "The directory server stopped returning results before the search finished. "
    "Narrow your search term to see the full set of matches."
)


def _search_directory(client, term):
    """Users and computers matching one query. Runs on a worker thread."""
    return client.search_users(term), client.search_computers(term)


def _user_details(user):
    return [
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


def _computer_details(computer):
    return [
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


def _result_row(kind, item):
    if kind == USER:
        return [
            "User",
            item.common_name,
            item.sam_account_name,
            item.email,
            "Yes" if item.is_locked_out else "No",
            "Yes" if item.disabled else "No",
            format_dt(item.last_logon),
        ]
    return [
        "Computer",
        item.common_name,
        item.dns_hostname,
        item.operating_system,
        "",
        "Yes" if item.disabled else "No",
        format_dt(item.last_logon),
    ]


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
    """One search for users and computers. The details panel follows the selected row's type."""

    PAGE_MESSAGE, PAGE_USER, PAGE_COMPUTER = 0, 1, 2

    def __init__(self, client):
        super().__init__()
        self.client = client
        self._results = []  # [(USER | COMPUTER, ADUser | ADComputer)]
        self._groups_request = 0
        self._empty_message = "Search for a user or computer, then select a result to view details."

        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("Search users and computers by name, username, email, or hostname")
        self.query_edit.returnPressed.connect(self._search)

        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self._search)

        self.unlock_button = QPushButton("Unlock Selected")
        style_button(self.unlock_button, "success")
        self.unlock_button.clicked.connect(self._unlock_selected)

        self.disable_button = QPushButton("Disable Selected")
        style_button(self.disable_button, "danger")
        self.disable_button.clicked.connect(self._disable_selected)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indeterminate
        self.progress_bar.setVisible(False)

        self.table = QTableWidget(0, len(RESULT_COLUMNS))
        self.table.setHorizontalHeaderLabels(RESULT_COLUMNS)
        header = self.table.horizontalHeader()
        for col in range(len(RESULT_COLUMNS)):
            header.setSectionResizeMode(
                col,
                QHeaderView.Stretch if RESULT_COLUMNS[col] == "Email / OS" else QHeaderView.ResizeToContents,
            )
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.itemSelectionChanged.connect(self._update_details)

        # Page 0: prompt or multi-selection message.
        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        self.message_label.setAlignment(Qt.AlignTop)

        # Page 1: user details, groups, lockout history.
        self.details_label = QLabel()
        self.details_label.setWordWrap(True)
        self.details_label.setAlignment(Qt.AlignTop)

        self.groups_list = QTableWidget(0, 2)
        self.groups_list.horizontalHeader().setVisible(False)
        self.groups_list.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.groups_list.verticalHeader().setVisible(False)
        self.groups_list.setShowGrid(False)
        self.groups_list.setEditTriggers(QTableWidget.NoEditTriggers)

        self.lockout_button = QPushButton("Lockout History")
        self.lockout_button.setToolTip("Bad password counts per domain controller and lockout events from the PDC")
        self.lockout_button.clicked.connect(self._show_lockout_history)

        user_page = QWidget()
        user_layout = QHBoxLayout(user_page)
        user_layout.setContentsMargins(0, 0, 0, 0)
        details_column = QVBoxLayout()
        details_column.addWidget(self.details_label, stretch=1)
        details_column.addWidget(self.lockout_button, alignment=Qt.AlignLeft)
        user_layout.addLayout(details_column, stretch=1)
        groups_column = QVBoxLayout()
        groups_column.addWidget(QLabel("Group Memberships"))
        groups_column.addWidget(self.groups_list)
        user_layout.addLayout(groups_column, stretch=2)

        # Page 2: computer details.
        self.computer_label = QLabel()
        self.computer_label.setWordWrap(True)
        self.computer_label.setAlignment(Qt.AlignTop)

        self.details_stack = QStackedWidget()
        self.details_stack.addWidget(self.message_label)
        self.details_stack.addWidget(user_page)
        self.details_stack.addWidget(self.computer_label)

        self.details_box = QGroupBox("Details")
        details_box_layout = QVBoxLayout(self.details_box)
        details_box_layout.addWidget(self.details_stack)

        search_row = QHBoxLayout()
        search_row.addWidget(self.query_edit)
        search_row.addWidget(self.search_button)

        # Account actions only apply to users, so the whole row hides when none are selected.
        self.actions_row = QWidget()
        actions_layout = QHBoxLayout(self.actions_row)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.addWidget(self.unlock_button)
        actions_layout.addWidget(self.disable_button)

        layout = QVBoxLayout(self)
        layout.addLayout(search_row)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.table)
        layout.addWidget(self.details_box)
        layout.addWidget(self.actions_row)

        self._update_details()

    def _search(self):
        term = self.query_edit.text().strip()
        if not term:
            return
        self._set_busy(True)
        run_async(
            _search_directory,
            self.client,
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
        users, computers = result
        self._results = [(USER, user) for user in users.items] + [(COMPUTER, pc) for pc in computers.items]
        self._empty_message = (
            "Select a result to view details." if self._results else "No users or computers matched that search."
        )
        self._populate_table()
        if users.truncated or computers.truncated:
            QMessageBox.warning(self, "Results truncated", TRUNCATED_MESSAGE)

    def _populate_table(self):
        self.table.clearSelection()
        self.table.setRowCount(len(self._results))
        for row, (kind, item) in enumerate(self._results):
            for col, value in enumerate(_result_row(kind, item)):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self._update_details()

    def _selected(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        return [self._results[row] for row in rows]

    def _selected_users(self):
        return [item for kind, item in self._selected() if kind == USER]

    def _show_page(self, page, title="Details", message=None):
        if message is not None:
            self.message_label.setText(message)
        self.details_stack.setCurrentIndex(page)
        self.details_box.setTitle(title)

    def _show_groups_message(self, text):
        self.groups_list.clearSpans()
        self.groups_list.setRowCount(1)
        self.groups_list.setSpan(0, 0, 1, 2)
        self.groups_list.setItem(0, 0, QTableWidgetItem(text))

    def _update_details(self):
        # Any group lookup still in flight is now stale; its result is ignored when it lands.
        self._groups_request += 1
        selected = self._selected()
        users = [item for kind, item in selected if kind == USER]
        self.actions_row.setVisible(bool(users))

        if not selected:
            self._show_page(self.PAGE_MESSAGE, message=self._empty_message)
            return
        if len(selected) > 1:
            computers = len(selected) - len(users)
            if not computers:
                message = "Multiple accounts selected. Select one to view details."
            elif not users:
                message = "Multiple computers selected. Select one to view details."
            else:
                message = (
                    f"{len(users)} user(s) and {computers} computer(s) selected. "
                    "Unlock and Disable apply to the selected users only."
                )
            self._show_page(self.PAGE_MESSAGE, message=message)
            return

        kind, item = selected[0]
        if kind == COMPUTER:
            self.computer_label.setText("\n".join(_computer_details(item)))
            self._show_page(self.PAGE_COMPUTER, title="Computer Details")
            return

        self.details_label.setText("\n".join(_user_details(item)))
        self._show_page(self.PAGE_USER, title="Account Details")
        self._show_groups_message("Loading groups...")
        request = self._groups_request
        # Resolves nested group membership, not just directly assigned groups.
        run_async(
            self.client.get_effective_groups_with_descriptions,
            item.distinguished_name,
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

    def _show_lockout_history(self):
        users = self._selected_users()
        if len(users) != 1:
            return
        # Parented to the main window so the session lock hides it along with everything else.
        dialog = LockoutHistoryDialog(self.client, users[0], parent=self.window())
        dialog.show()

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
