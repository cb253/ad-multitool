from ldap3.core.exceptions import LDAPBindError, LDAPSocketOpenError
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
)

from ad_client import LDAPS_PORT, ADClient, qualify_server_name
from app_settings import load_settings, save_settings
from ui_common import run_async


def sign_in(sign_in_call, client, *args):
    """Bind on a worker thread, turning ldap3's errors into messages an operator can act on."""
    try:
        sign_in_call(*args)
    except LDAPBindError as exc:
        if "invalidCredentials" in str(exc):
            raise RuntimeError("Invalid username or password.") from exc
        raise RuntimeError(f"Sign-in was rejected by the server: {exc}") from exc
    except LDAPSocketOpenError as exc:
        if "certificate" in str(exc).lower():
            raise RuntimeError(
                f"The certificate presented by {client.server_name} could not be verified.\n\n"
                "Enter the domain controller's full name exactly as it appears on its certificate "
                "(for example dc01.example.com). If this persists, the connection may be intercepted. "
                "Do not continue and contact the domain administrator."
            ) from exc
        raise RuntimeError(f"Could not reach {client.server_name} on port {LDAPS_PORT} (LDAPS).\n\n{exc}") from exc
    return client


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

        self.server_edit.setText(settings["server"])
        self.server_edit.setPlaceholderText("dc01.example.com")
        self.base_dn_edit.setText(settings["base_dn"])
        self.base_dn_edit.setPlaceholderText("DC=example,DC=com")
        self.domain_edit.setText(settings["domain"])
        self.domain_edit.setPlaceholderText("example.com")
        self.username_edit.setText(settings["username"])
        self.username_edit.setPlaceholderText("jdoe")

        self.status_label = QLabel(f"Connects over LDAPS (port {LDAPS_PORT}) with certificate validation.")

        form = QFormLayout(self)
        form.addRow("Server", self.server_edit)
        form.addRow("Base DN", self.base_dn_edit)
        form.addRow("UPN Suffix / Domain", self.domain_edit)
        form.addRow("Username", self.username_edit)
        form.addRow("Password", self.password_edit)
        form.addRow(self.status_label)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._try_connect)
        self.buttons.rejected.connect(self.reject)
        form.addRow(self.buttons)

        if settings["username"]:
            self.password_edit.setFocus()

    def _set_busy(self, busy):
        for widget in (self.server_edit, self.base_dn_edit, self.domain_edit, self.username_edit, self.password_edit):
            widget.setEnabled(not busy)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(not busy)

    def _try_connect(self):
        base_dn = self.base_dn_edit.text().strip()
        server = qualify_server_name(self.server_edit.text(), base_dn)
        domain = self.domain_edit.text().strip()
        username = self.username_edit.text().strip()
        if not server or not base_dn or not username:
            QMessageBox.warning(self, "Missing details", "Server, Base DN, and Username are required.")
            return

        self.server_edit.setText(server)
        self.status_label.setText(f"Connecting to {server}...")
        self._set_busy(True)
        client = ADClient(server, base_dn)
        run_async(
            sign_in,
            client.connect,
            client,
            domain,
            username,
            self.password_edit.text(),
            on_success=lambda client: self._on_connected(client, server, base_dn, domain, username),
            on_failure=self._on_connect_failed,
            on_finished=lambda: self._set_busy(False),
        )

    def _on_connect_failed(self, message):
        self.status_label.setText("Not connected.")
        self.password_edit.clear()
        QMessageBox.critical(self, "Connection failed", message)
        self.password_edit.setFocus()

    def _on_connected(self, client, server, base_dn, domain, username):
        self.password_edit.clear()
        save_settings({"server": server, "base_dn": base_dn, "domain": domain, "username": username})
        self.client = client
        self.accept()
