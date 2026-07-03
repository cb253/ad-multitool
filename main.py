import sys

from PySide6.QtWidgets import QApplication

from login_dialog import LoginDialog
from main_window import MainWindow


def main():
    app = QApplication(sys.argv)

    login = LoginDialog()
    if login.exec() != LoginDialog.Accepted:
        return

    window = MainWindow(login.client)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
