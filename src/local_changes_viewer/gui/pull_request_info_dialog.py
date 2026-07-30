from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from local_changes_viewer.core.domain.pull_request import PullRequestDetails
from local_changes_viewer.gui.formatting import format_timestamp


def _label(text: str) -> QLabel:
    label = QLabel(text)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _branch_row(branch_name: str) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)

    copy_button = QPushButton("⧉")
    copy_button.setFlat(True)
    copy_button.setFixedSize(20, 20)
    copy_button.setToolTip("Copy branch name to clipboard")
    copy_button.setCursor(Qt.CursorShape.PointingHandCursor)
    copy_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    copy_button.clicked.connect(lambda: QApplication.clipboard().setText(branch_name))

    layout.addWidget(copy_button)
    layout.addWidget(_label(branch_name))
    return row


class PullRequestInfoDialog(QDialog):
    def __init__(self, details: PullRequestDetails, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"PR #{details.number} Info")

        form = QFormLayout()
        form.addRow("Title:", _label(details.title))
        form.addRow("Number:", _label(f"#{details.number}"))
        url_label = QLabel(f'<a href="{details.url}">{details.url}</a>')
        url_label.setOpenExternalLinks(True)
        form.addRow("URL:", url_label)
        form.addRow("From branch:", _branch_row(details.head_ref))
        form.addRow("To branch:", _branch_row(details.base_ref))
        form.addRow("Status:", _label(details.status))
        form.addRow("Created:", _label(format_timestamp(details.created_at)))
        form.addRow("Last modified:", _label(format_timestamp(details.updated_at)))
        form.addRow("Last comment by:", _label(details.last_comment_writer or "-"))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
