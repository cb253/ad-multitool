from PySide6.QtWidgets import (
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

from ui_common import COMPARE_TEXT_COLOR, DIFF_COLOR, SHARED_COLOR, run_async


def _fetch_user_comparison(client, name_a, name_b):
    user_a = client.get_user(name_a)
    user_b = client.get_user(name_b)
    groups_a = client.get_effective_groups_with_descriptions(user_a.distinguished_name) if user_a else []
    groups_b = client.get_effective_groups_with_descriptions(user_b.distinguished_name) if user_b else []
    return user_a, user_b, groups_a, groups_b


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

    def _compare(self):
        name_a = self.user_a_edit.text().strip()
        name_b = self.user_b_edit.text().strip()
        if not name_a or not name_b:
            return
        self._set_busy(True)
        run_async(
            _fetch_user_comparison,
            self.client,
            name_a,
            name_b,
            on_success=lambda result: self._on_compare_succeeded(name_a, name_b, result),
            on_failure=lambda message: QMessageBox.critical(self, "Compare failed", message),
            on_finished=lambda: self._set_busy(False),
        )

    def _set_busy(self, busy):
        self.progress_bar.setVisible(busy)
        self.user_a_edit.setEnabled(not busy)
        self.user_b_edit.setEnabled(not busy)
        self.compare_button.setEnabled(not busy)

    def _on_compare_succeeded(self, name_a, name_b, result):
        user_a, user_b, groups_a, groups_b = result

        if user_a is None or user_b is None:
            missing = name_a if user_a is None else name_b
            QMessageBox.warning(self, "User not found", f"Could not find account '{missing}'")
            return

        descriptions = dict(groups_a + groups_b)
        groups_a = {name for name, _ in groups_a}
        groups_b = {name for name, _ in groups_b}
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
                item.setToolTip(descriptions[group] or "No description")
                self.table.setItem(row, col, item)
