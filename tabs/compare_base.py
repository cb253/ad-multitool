"""Shared layout and behavior for the Compare Users and Compare Groups tabs."""

from dataclasses import dataclass

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
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

from ui_common import COMPARE_TEXT_COLOR, DIFF_COLOR, SHARED_COLOR, run_async, style_button

COPY_CONFIRM_MS = 1500


@dataclass
class ComparisonRow:
    name: str
    in_a: bool
    in_b: bool
    tooltip: str = ""


@dataclass
class Comparison:
    title: str
    item_header: str
    name_a: str
    name_b: str
    rows: list

    @property
    def summary(self):
        shared = sum(1 for row in self.rows if row.in_a and row.in_b)
        only_a = sum(1 for row in self.rows if row.in_a and not row.in_b)
        only_b = sum(1 for row in self.rows if row.in_b and not row.in_a)
        return f"{shared} shared | {only_a} only in {self.name_a} | {only_b} only in {self.name_b}"

    def as_text(self):
        """Tab-separated, so it pastes into Excel as columns and still reads cleanly in chat or email."""
        lines = [
            self.title,
            self.summary,
            "",
            "\t".join([self.item_header, self.name_a, self.name_b]),
        ]
        for row in self.rows:
            lines.append("\t".join([row.name, "Yes" if row.in_a else "No", "Yes" if row.in_b else "No"]))
        return "\n".join(lines)


class CompareTab(QWidget):
    """Two inputs, Compare and Copy buttons, a summary line, and one color-coded table.

    Subclasses set the class attributes and implement fetch() (runs on a worker
    thread) and build_comparison() (runs on the UI thread with fetch's result).
    """

    LABEL_A = "A"
    LABEL_B = "B"
    PLACEHOLDER_A = ""
    PLACEHOLDER_B = ""
    ITEM_HEADER = "Item"

    def __init__(self, client):
        super().__init__()
        self.client = client
        self._comparison = None

        self.edit_a = QLineEdit()
        self.edit_a.setPlaceholderText(self.PLACEHOLDER_A)
        self.edit_a.returnPressed.connect(self._compare)
        self.edit_b = QLineEdit()
        self.edit_b.setPlaceholderText(self.PLACEHOLDER_B)
        self.edit_b.returnPressed.connect(self._compare)

        self.compare_button = QPushButton("Compare")
        style_button(self.compare_button, "success")
        self.compare_button.clicked.connect(self._compare)

        self.copy_button = QPushButton("Copy Results")
        self.copy_button.setToolTip("Copy the comparison to the clipboard as text")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy)

        form = QHBoxLayout()
        form.addWidget(QLabel(self.LABEL_A))
        form.addWidget(self.edit_a)
        form.addWidget(QLabel(self.LABEL_B))
        form.addWidget(self.edit_b)
        form.addWidget(self.compare_button)
        form.addWidget(self.copy_button)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indeterminate
        self.progress_bar.setVisible(False)

        self.summary_label = QLabel("")

        self.table = QTableWidget(0, 3)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setHorizontalHeaderLabels([self.ITEM_HEADER, self.LABEL_A, self.LABEL_B])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.table)

    def fetch(self, client, value_a, value_b):
        raise NotImplementedError

    def build_comparison(self, value_a, value_b, result):
        """Return a Comparison, or None after showing the user why there isn't one."""
        raise NotImplementedError

    def _compare(self):
        value_a = self.edit_a.text().strip()
        value_b = self.edit_b.text().strip()
        if not value_a or not value_b:
            return
        self._set_busy(True)
        run_async(
            self.fetch,
            self.client,
            value_a,
            value_b,
            on_success=lambda result: self._show(self.build_comparison(value_a, value_b, result)),
            on_failure=lambda message: QMessageBox.critical(self, "Compare failed", message),
            on_finished=lambda: self._set_busy(False),
        )

    def _set_busy(self, busy):
        self.progress_bar.setVisible(busy)
        self.edit_a.setEnabled(not busy)
        self.edit_b.setEnabled(not busy)
        self.compare_button.setEnabled(not busy)
        self.copy_button.setEnabled(not busy and self._comparison is not None)

    def _show(self, comparison):
        if comparison is None:
            return
        self._comparison = comparison
        self.copy_button.setEnabled(True)
        self.summary_label.setText(comparison.summary)
        self.table.setHorizontalHeaderLabels([comparison.item_header, comparison.name_a, comparison.name_b])

        self.table.setRowCount(len(comparison.rows))
        for row_index, row in enumerate(comparison.rows):
            name_color = SHARED_COLOR if row.in_a and row.in_b else DIFF_COLOR
            # In a differing row the name stays red, but the side that has it shows green.
            cells = [
                (row.name, name_color),
                ("Yes", SHARED_COLOR) if row.in_a else ("No", DIFF_COLOR),
                ("Yes", SHARED_COLOR) if row.in_b else ("No", DIFF_COLOR),
            ]
            for col, (text, color) in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setBackground(color)
                item.setForeground(COMPARE_TEXT_COLOR)
                if row.tooltip:
                    item.setToolTip(row.tooltip)
                self.table.setItem(row_index, col, item)

    def _copy(self):
        if self._comparison is None:
            return
        QApplication.clipboard().setText(self._comparison.as_text())
        self.copy_button.setText("Copied!")
        QTimer.singleShot(COPY_CONFIRM_MS, self, lambda: self.copy_button.setText("Copy Results"))
