from PySide6.QtWidgets import QDialog, QHBoxLayout, QListWidget, QTextBrowser

from user_guide import SECTIONS


class UserGuideDialog(QDialog):
    """Section list on the left, the selected section's content on the right."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("User Guide")
        self.resize(900, 600)

        self.sections = QListWidget()
        self.sections.addItems([title for title, _ in SECTIONS])
        self.sections.setFixedWidth(200)
        self.sections.currentRowChanged.connect(self._show_section)

        self.content = QTextBrowser()
        self.content.setOpenExternalLinks(False)

        layout = QHBoxLayout(self)
        layout.addWidget(self.sections)
        layout.addWidget(self.content, stretch=1)

        self.sections.setCurrentRow(0)

    def _show_section(self, row):
        if 0 <= row < len(SECTIONS):
            self.content.setMarkdown(SECTIONS[row][1])
