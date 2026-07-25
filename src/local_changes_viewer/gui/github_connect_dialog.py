from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit


class GitHubConnectDialog(QDialog):
    def __init__(self, current_username: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Connect to GitHub")

        self._username_edit = QLineEdit(current_username)
        self._username_edit.setPlaceholderText("GitHub username")

        self._token_edit = QLineEdit()
        self._token_edit.setPlaceholderText("Personal access token")
        self._token_edit.setEchoMode(QLineEdit.EchoMode.Password)

        form = QFormLayout()
        form.addRow("Username:", self._username_edit)
        form.addRow("Token:", self._token_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self.setLayout(form)

    def username(self) -> str:
        return self._username_edit.text().strip()

    def token(self) -> str:
        return self._token_edit.text().strip()
