from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
)

from ad_client import ADClient
from app_settings import load_settings, save_settings


class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect to Active Directory")
        self.client = None

        settings = load_settings()

        self.server_edit = QLineEdit()
        self.base_dn_edit = QLineEdit()
        self.domain_edit = QLineEdit()
        self.username_edit = QLineEdit()
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)

        self.use_ssl_check = QCheckBox("Use LDAPS (port 636)")
        self.use_ssl_check.setChecked(settings["use_ssl"])

        self.server_edit.setText(settings["server"])
        self.server_edit.setPlaceholderText("dc01.example.com")
        self.base_dn_edit.setText(settings["base_dn"])
        self.domain_edit.setText(settings["domain"])
        self.username_edit.setText(settings["username"])
        self.username_edit.setPlaceholderText("jdoe")

        form = QFormLayout(self)
        form.addRow("Server", self.server_edit)
        form.addRow(self.use_ssl_check)
        form.addRow("Base DN", self.base_dn_edit)
        form.addRow("UPN Suffix / Domain", self.domain_edit)
        form.addRow("Username", self.username_edit)
        form.addRow("Password", self.password_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._try_connect)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _try_connect(self):
        server = self.server_edit.text().strip()
        base_dn = self.base_dn_edit.text().strip()
        domain = self.domain_edit.text().strip()
        username = self.username_edit.text().strip()
        use_ssl = self.use_ssl_check.isChecked()

        client = ADClient(server, base_dn, use_ssl=use_ssl)
        try:
            client.connect(domain, username, self.password_edit.text())
        except Exception as exc:
            QMessageBox.critical(self, "Connection failed", str(exc))
            return

        save_settings(
            {
                "server": server,
                "base_dn": base_dn,
                "domain": domain,
                "use_ssl": use_ssl,
                "username": username,
            }
        )

        self.client = client
        self.accept()
