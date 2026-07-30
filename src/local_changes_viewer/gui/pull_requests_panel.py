from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from local_changes_viewer.core.domain.pull_request import PullRequestInfo
from local_changes_viewer.gui.my_pull_requests_dialog import PullRequestsTreeWidget


class PullRequestsPanel(QWidget):
    """Embeddable panel showing open pull requests, dockable below the folder tree."""

    closed = Signal()
    refresh_requested = Signal()
    pull_request_refresh_requested = Signal(str, int)  # repository, number
    pull_request_info_requested = Signal(str, int)  # repository, number
    pull_request_issues_requested = Signal(str, int)  # repository, number

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        title_label = QLabel("My Open Pull Requests")
        font = title_label.font()
        font.setBold(True)
        title_label.setFont(font)

        self._refresh_button = QPushButton("Refresh")
        self._refresh_button.setToolTip("Refresh the list of pull requests")
        self._refresh_button.clicked.connect(self.refresh_requested.emit)

        close_button = QPushButton("✕")
        close_button.setFlat(True)
        close_button.setFixedSize(20, 20)
        close_button.setToolTip("Close panel")
        close_button.clicked.connect(self._on_close_clicked)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(4, 2, 4, 2)
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        header_layout.addWidget(self._refresh_button)
        header_layout.addWidget(close_button)

        self._tree_widget = PullRequestsTreeWidget()
        self._tree_widget.pull_request_refresh_requested.connect(
            self.pull_request_refresh_requested.emit
        )
        self._tree_widget.pull_request_info_requested.connect(
            self.pull_request_info_requested.emit
        )
        self._tree_widget.pull_request_issues_requested.connect(
            self.pull_request_issues_requested.emit
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(header_layout)
        layout.addWidget(self._tree_widget)

    def _on_close_clicked(self) -> None:
        self.hide()
        self.closed.emit()

    def set_pull_requests(self, pull_requests: list[PullRequestInfo]) -> None:
        self._tree_widget.set_pull_requests(pull_requests)

    def set_refreshing(self, refreshing: bool) -> None:
        self._refresh_button.setEnabled(not refreshing)
        self._refresh_button.setText("Refreshing…" if refreshing else "Refresh")

    def update_pull_request_fields(
        self,
        repository: str,
        number: int,
        *,
        approved: bool | None,
        unresolved_review_thread_count: int,
        last_reviewer: str | None,
        last_reviewed_at: str | None,
        changed_files: int,
        checks_state: str | None,
    ) -> None:
        self._tree_widget.update_pull_request_fields(
            repository,
            number,
            approved=approved,
            unresolved_review_thread_count=unresolved_review_thread_count,
            last_reviewer=last_reviewer,
            last_reviewed_at=last_reviewed_at,
            changed_files=changed_files,
            checks_state=checks_state,
        )
