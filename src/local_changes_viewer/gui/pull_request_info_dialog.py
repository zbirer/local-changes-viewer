from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout

from local_changes_viewer.core.domain.pull_request import PullRequestDetails
from local_changes_viewer.gui.formatting import format_timestamp


def _label(text: str) -> QLabel:
    label = QLabel(text)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


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
        form.addRow("From branch:", _label(details.head_ref))
        form.addRow("To branch:", _label(details.base_ref))
        form.addRow("Status:", _label(details.status))
        form.addRow("Created:", _label(format_timestamp(details.created_at)))
        form.addRow("Last modified:", _label(format_timestamp(details.updated_at)))
        form.addRow("Last comment by:", _label(details.last_comment_writer or "-"))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
