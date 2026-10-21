"""Lockout history for one account: per-DC lockout attributes plus PDC lockout events."""

from datetime import datetime, timezone

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from lockout_events import load_lockout_events
from ui_common import format_dt, run_async

COPY_CONFIRM_MS = 1500
DC_COLUMNS = ["Domain Controller", "Bad Password Count", "Last Bad Password", "Last Logon", "Lockout Time", "Status"]
EVENT_COLUMNS = ["Time", "Event", "Domain Controller", "Caller Computer / Unlocked By"]


def format_duration(value):
    if value is None:
        return None
    minutes = int(value.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def describe_policy(policy):
    if policy is None:
        return "Lockout policy: unknown"
    if not policy.threshold:
        return "Lockout policy: accounts never lock out (threshold is 0)"
    duration = format_duration(policy.duration) or "until an admin unlocks it"
    window = format_duration(policy.observation_window) or "never"
    return (
        f"Lockout policy: locks after {policy.threshold} bad passwords, stays locked for {duration}, "
        f"bad password count resets after {window}"
    )


def sort_dc_statuses(statuses):
    """Most recent bad password first, then DCs with no bad password, then DCs that errored."""
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(
        statuses,
        key=lambda status: (bool(status.error), -(status.last_bad_password or oldest).timestamp(), status.dc.lower()),
    )


def dc_row(status):
    if status.error:
        return [status.dc, "", "", "", "", f"Error: {status.error}"]
    return [
        status.dc,
        "" if status.bad_password_count is None else str(status.bad_password_count),
        format_dt(status.last_bad_password),
        format_dt(status.last_logon),
        format_dt(status.lockout_time, never="Not locked"),
        "OK",
    ]


def event_row(event):
    return [format_dt(event.time, never="Unknown"), event.kind, event.dc, event.detail]


def _load_directory(client, user):
    return (
        client.get_lockout_policy(),
        client.get_lockout_status_per_dc(user.distinguished_name),
        client.get_pdc_hostname(),
    )


def _load_events(client, pdc, sam_account_name):
    # Credentials are fetched on the worker, so a session lock in between raises "not signed in".
    return load_lockout_events(pdc, sam_account_name, client.event_log_credentials())


def _make_table(columns):
    table = QTableWidget(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.verticalHeader().setVisible(False)
    header = table.horizontalHeader()
    for col in range(len(columns)):
        header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
    header.setSectionResizeMode(len(columns) - 1, QHeaderView.Stretch)
    return table


def _fill_table(table, rows):
    table.setRowCount(len(rows))
    for row_index, row in enumerate(rows):
        for col, text in enumerate(row):
            table.setItem(row_index, col, QTableWidgetItem(text))


class LockoutHistoryDialog(QDialog):
    def __init__(self, client, user, parent=None):
        super().__init__(parent)
        self.client = client
        self.user = user
        self.setWindowTitle(f"Lockout History - {user.sam_account_name}")
        self.resize(1000, 560)
        # Deleted only once closed AND no background load is still going to call back into it.
        self._pending = 0
        self._closed = False

        self._policy = None
        self._dc_rows = []
        self._events_text = ""
        self._event_rows = []
        self._pdc = ""

        self.header_label = QLabel()
        self.header_label.setWordWrap(True)
        self.dc_table = _make_table(DC_COLUMNS)
        self.events_title = QLabel("Lockout events")
        self.events_table = _make_table(EVENT_COLUMNS)
        self.events_note = QLabel()
        self.events_note.setWordWrap(True)
        self.events_note.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.copy_button = QPushButton("Copy Results")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.load)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)

        buttons = QHBoxLayout()
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.refresh_button)
        buttons.addStretch()
        buttons.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.header_label)
        layout.addWidget(QLabel("Per domain controller (bad password counts are tracked separately on each DC)"))
        layout.addWidget(self.dc_table, stretch=1)
        layout.addWidget(self.events_title)
        layout.addWidget(self.events_table, stretch=1)
        layout.addWidget(self.events_note)
        layout.addLayout(buttons)

        self._update_header()
        self.load()

    def _account_line(self):
        return f"{self.user.common_name} ({self.user.sam_account_name})"

    def _locked_line(self):
        if self.user.is_locked_out:
            return f"Currently locked out: Yes, since {format_dt(self.user.locked_out_at)}"
        return "Currently locked out: No"

    def _update_header(self):
        policy = describe_policy(self._policy) if self._policy is not None else "Lockout policy: loading..."
        self.header_label.setText(f"{self._account_line()}\n{self._locked_line()}\n{policy}")

    def _start(self, func, *args, on_success, on_failure):
        self._pending += 1
        run_async(
            func,
            *args,
            on_success=lambda result: None if self._closed else on_success(result),
            on_failure=lambda message: None if self._closed else on_failure(message),
            on_finished=self._on_task_finished,
        )

    def _on_task_finished(self):
        self._pending -= 1
        if self._closed and self._pending == 0:
            self.deleteLater()

    def closeEvent(self, event):
        self._closed = True
        if self._pending == 0:
            self.deleteLater()
        super().closeEvent(event)

    def load(self):
        self.refresh_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self.dc_table.setRowCount(0)
        self.events_table.setRowCount(0)
        self.events_note.setText("Loading...")
        self._start(
            _load_directory,
            self.client,
            self.user,
            on_success=self._on_directory_loaded,
            on_failure=self._on_directory_failed,
        )

    def _on_directory_failed(self, message):
        self.events_note.setText(f"Could not read lockout data: {message}")
        self.refresh_button.setEnabled(True)

    def _on_directory_loaded(self, result):
        self._policy, statuses, self._pdc = result
        self._update_header()
        self._dc_rows = [dc_row(status) for status in sort_dc_statuses(statuses)]
        _fill_table(self.dc_table, self._dc_rows)
        self.copy_button.setEnabled(True)

        if not self._pdc:
            self._show_events_message("Could not determine the PDC emulator, so lockout events were not read.")
            return
        self.events_title.setText(f"Lockout events (from the PDC emulator, {self._pdc})")
        self.events_note.setText(f"Reading the Security log on {self._pdc}...")
        self._start(
            _load_events,
            self.client,
            self._pdc,
            self.user.sam_account_name,
            on_success=self._on_events_loaded,
            on_failure=self._show_events_message,
        )

    def _on_events_loaded(self, result):
        self._event_rows = [event_row(event) for event in result.events]
        _fill_table(self.events_table, self._event_rows)
        oldest = format_dt(result.oldest, never="unknown")
        found = f"{len(result.events)} event(s) found." if result.events else "No lockout or unlock events found."
        self._events_text = f"{found} Read from {result.pdc} as {result.identity}. Oldest event retained: {oldest}."
        self.events_note.setText(self._events_text)
        self.refresh_button.setEnabled(True)

    def _show_events_message(self, message):
        self._event_rows = []
        self._events_text = message
        self.events_table.setRowCount(0)
        self.events_note.setText(message)
        self.refresh_button.setEnabled(True)

    def as_text(self):
        lines = [
            f"Lockout history: {self._account_line()}",
            self._locked_line(),
            describe_policy(self._policy),
            "",
            "Per domain controller",
            "\t".join(DC_COLUMNS),
            *("\t".join(row) for row in self._dc_rows),
            "",
            f"Lockout events ({self._pdc or 'PDC unknown'})",
        ]
        if self._event_rows:
            lines.append("\t".join(EVENT_COLUMNS))
            lines.extend("\t".join(row) for row in self._event_rows)
        lines.append(self._events_text)
        return "\n".join(lines)

    def _copy(self):
        QApplication.clipboard().setText(self.as_text())
        self.copy_button.setText("Copied!")
        # Context object: the timer is cancelled if the dialog is deleted first.
        QTimer.singleShot(COPY_CONFIRM_MS, self, lambda: self.copy_button.setText("Copy Results"))
