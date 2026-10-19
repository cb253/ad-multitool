from PySide6.QtCore import QProcess
from PySide6.QtGui import QFontDatabase, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from net_tools import check_tcp_port, is_valid_target
from ui_common import run_async


class NetworkingTab(QWidget):
    def __init__(self):
        super().__init__()

        self.target_edit = QLineEdit()
        self.target_edit.setPlaceholderText("IP address or computer name")
        self.target_edit.returnPressed.connect(lambda: self._run_command("ping", ["-n", "4"]))

        self.ping_button = QPushButton("Ping")
        self.ping_button.clicked.connect(lambda: self._run_command("ping", ["-n", "4"]))
        self.continuous_ping_button = QPushButton("Continuous Ping")
        self.continuous_ping_button.clicked.connect(lambda: self._run_command("ping", ["-t"]))
        self.trace_button = QPushButton("Traceroute")
        self.trace_button.clicked.connect(lambda: self._run_command("tracert", []))
        self.nslookup_button = QPushButton("NSLookup")
        self.nslookup_button.clicked.connect(lambda: self._run_command("nslookup", []))

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(3389)
        self.port_button = QPushButton("Test Port")
        self.port_button.clicked.connect(self._test_port)

        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop)
        self.clear_button = QPushButton("Clear")

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.clear_button.clicked.connect(self.output.clear)

        self._command_buttons = [
            self.ping_button,
            self.continuous_ping_button,
            self.trace_button,
            self.nslookup_button,
            self.port_button,
        ]

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Target"))
        target_row.addWidget(self.target_edit)

        commands_row = QHBoxLayout()
        for button in self._command_buttons[:-1]:
            commands_row.addWidget(button)
        commands_row.addSpacing(16)
        commands_row.addWidget(QLabel("Port"))
        commands_row.addWidget(self.port_spin)
        commands_row.addWidget(self.port_button)
        commands_row.addStretch()
        commands_row.addWidget(self.stop_button)
        commands_row.addWidget(self.clear_button)

        layout = QVBoxLayout(self)
        layout.addLayout(target_row)
        layout.addLayout(commands_row)
        layout.addWidget(self.output)

        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._on_process_finished)
        self.process.errorOccurred.connect(self._on_process_error)

    def _target(self):
        target = self.target_edit.text().strip()
        if not is_valid_target(target):
            QMessageBox.warning(self, "Invalid target", "Enter a valid IP address or computer name.")
            return None
        return target

    def _run_command(self, program, args):
        if self.process.state() != QProcess.NotRunning:
            return
        target = self._target()
        if not target:
            return
        full_args = args + [target]
        self._append(f"\n> {program} {' '.join(full_args)}\n")
        self._set_busy(True)
        self.process.start(program, full_args)

    def _test_port(self):
        target = self._target()
        if not target:
            return
        port = self.port_spin.value()
        self._append(f"\n> Test TCP port {port} on {target}\n")
        self._set_busy(True, stoppable=False)
        run_async(
            check_tcp_port,
            target,
            port,
            on_success=lambda message: self._append(message + "\n"),
            on_failure=lambda message: self._append(f"Error: {message}\n"),
            on_finished=lambda: self._set_busy(False),
        )

    def _stop(self):
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()

    def stop_running_command(self):
        """Kill any running command and wait for it, so nothing outlives the window."""
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()
            self.process.waitForFinished(2000)

    def _read_output(self):
        # Windows console tools write in the OEM code page, not UTF-8.
        data = bytes(self.process.readAllStandardOutput()).decode("oem", errors="replace")
        self._append(data.replace("\r\n", "\n"))

    def _on_process_finished(self, _exit_code, exit_status):
        if exit_status == QProcess.CrashExit:
            self._append("[Stopped]\n")
        self._set_busy(False)

    def _on_process_error(self, error):
        if error == QProcess.FailedToStart:
            self._append(f"Failed to start: {self.process.errorString()}\n")
            self._set_busy(False)

    def _append(self, text):
        self.output.moveCursor(QTextCursor.End)
        self.output.insertPlainText(text)
        self.output.ensureCursorVisible()

    def _set_busy(self, busy, stoppable=True):
        for button in self._command_buttons:
            button.setEnabled(not busy)
        self.target_edit.setEnabled(not busy)
        self.stop_button.setEnabled(busy and stoppable)
