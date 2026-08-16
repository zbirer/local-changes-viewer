"""Clipboard-source step for the "Apply patch..." feature (see main_window.py's
`_on_apply_patch_for_repo`): shows the current clipboard text in an editable
box so the user can paste a patch in, or correct one that's already there
(e.g. a chat client mangled the whitespace), before it's parsed. A plain
`QMessageBox` can't do this -- it has no room for a multi-line editable field
-- so this is a small dedicated dialog rather than reusing one of the
existing message boxes.
"""

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)


class PatchTextInputDialog(QDialog):
    def __init__(self, clipboard_text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Apply Patch — From Clipboard")
        parent_width = parent.width() if parent is not None else 640
        self.resize(int(parent_width * 0.6), 420)

        self._text_edit = QPlainTextEdit()
        self._text_edit.setPlainText(clipboard_text)
        self._text_edit.textChanged.connect(self._update_ok_enabled)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._text_edit)
        layout.addWidget(self._buttons)

        # Applying blank text is never a useful outcome, so OK starts (and
        # stays) disabled the moment the box is empty -- mirroring
        # PatchFileSelectionDialog's "OK disabled with nothing checked" rule.
        self._update_ok_enabled()

    def _update_ok_enabled(self) -> None:
        has_text = bool(self._text_edit.toPlainText().strip())
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(has_text)

    def patch_text(self) -> str:
        return self._text_edit.toPlainText()
