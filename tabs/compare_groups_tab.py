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

from ui_common import run_async


def _fetch_group_pair(client, dn_a, dn_b):
    return (
        client.get_group_members(dn_a),
        client.get_group_members(dn_b),
        client.get_group_description(dn_a),
        client.get_group_description(dn_b),
    )


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

        self.only_a_table = self._make_table()
        self.only_b_table = self._make_table()
        self.both_table = self._make_table()

        tables_row = QHBoxLayout()
        tables_row.addLayout(self._wrap_with_label(self.only_a_table, "Only in Group A"))
        tables_row.addLayout(self._wrap_with_label(self.both_table, "In Both"))
        tables_row.addLayout(self._wrap_with_label(self.only_b_table, "Only in Group B"))

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.progress_bar)
        layout.addLayout(tables_row)

    def _make_table(self):
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
        run_async(
            _fetch_group_pair,
            self.client,
            dn_a,
            dn_b,
            on_success=self._on_compare_succeeded,
            on_failure=lambda message: QMessageBox.critical(self, "Compare failed", message),
            on_finished=lambda: self._set_busy(False),
        )

    def _set_busy(self, busy):
        self.progress_bar.setVisible(busy)
        self.group_a_edit.setEnabled(not busy)
        self.group_b_edit.setEnabled(not busy)
        self.compare_button.setEnabled(not busy)

    def _on_compare_succeeded(self, result):
        members_a, members_b, description_a, description_b = result
        self.group_a_edit.setToolTip(description_a or "No description")
        self.group_b_edit.setToolTip(description_b or "No description")

        names_a = {user.sam_account_name for user in members_a.items}
        names_b = {user.sam_account_name for user in members_b.items}

        self._fill_column(self.only_a_table, sorted(names_a - names_b))
        self._fill_column(self.only_b_table, sorted(names_b - names_a))
        self._fill_column(self.both_table, sorted(names_a & names_b))

        if members_a.truncated or members_b.truncated:
            QMessageBox.warning(
                self,
                "Results truncated",
                "The directory server did not return every member, so this comparison is incomplete.",
            )

    def _fill_column(self, table, names):
        table.setRowCount(len(names))
        for row, name in enumerate(names):
            table.setItem(row, 0, QTableWidgetItem(name))
