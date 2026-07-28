from collections import defaultdict

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from local_changes_viewer.core.domain.pull_request import PullRequestInfo

_COLUMNS = ["PR ID", "Link", "Title", "Approved", "Unresolved", "Last Reviewer", "Files", "Checks"]
_URL_ROLE = Qt.ItemDataRole.UserRole

_CHECKS_TEXT = {
    "SUCCESS": "✓ Success",
    "PENDING": "⏳ Pending",
    "EXPECTED": "⏳ Pending",
    "FAILURE": "✗ Failure",
    "ERROR": "✗ Error",
}


def _approved_text(approved: bool | None) -> str:
    if approved is None:
        return "-"
    return "Yes" if approved else "No"


def _checks_text(checks_state: str | None) -> str:
    if checks_state is None:
        return "-"
    return _CHECKS_TEXT.get(checks_state, checks_state.title())


class MyPullRequestsDialog(QDialog):
    refresh_requested = Signal()

    def __init__(self, pull_requests: list[PullRequestInfo], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("My Open Pull Requests")
        width = int(parent.width() * 0.8) if parent is not None else 800
        self.resize(width, 500)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(len(_COLUMNS))
        self._tree.setHeaderLabels(_COLUMNS)
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)

        header = self._tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.setColumnWidth(0, 275)
        self._tree.setColumnWidth(1, 70)

        self.set_pull_requests(pull_requests)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        self._refresh_button = buttons.addButton(
            "Refresh", QDialogButtonBox.ButtonRole.ActionRole
        )
        self._refresh_button.clicked.connect(self.refresh_requested.emit)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tree)
        layout.addWidget(buttons)

    def set_pull_requests(self, pull_requests: list[PullRequestInfo]) -> None:
        self._tree.clear()

        if pull_requests:
            by_repo: dict[str, list[PullRequestInfo]] = defaultdict(list)
            for pr in pull_requests:
                by_repo[pr.repository].append(pr)

            for repo_name in sorted(by_repo):
                repo_item = QTreeWidgetItem([repo_name])
                repo_item.setFirstColumnSpanned(True)
                font = repo_item.font(0)
                font.setBold(True)
                repo_item.setFont(0, font)
                self._tree.addTopLevelItem(repo_item)

                for pr in by_repo[repo_name]:
                    pr_item = QTreeWidgetItem(
                        [
                            f"#{pr.number}",
                            "",
                            pr.title,
                            _approved_text(pr.approved),
                            str(pr.unresolved_review_thread_count),
                            pr.last_reviewer or "-",
                            str(pr.changed_files),
                            _checks_text(pr.checks_state),
                        ]
                    )
                    pr_item.setData(0, _URL_ROLE, pr.url)
                    repo_item.addChild(pr_item)

                    link_label = QLabel(f'<a href="{pr.url}">Open</a>')
                    link_label.setOpenExternalLinks(True)
                    link_label.setToolTip(pr.url)
                    self._tree.setItemWidget(pr_item, 1, link_label)

                repo_item.setExpanded(True)
        else:
            empty_item = QTreeWidgetItem(["No open pull requests found."])
            empty_item.setFirstColumnSpanned(True)
            self._tree.addTopLevelItem(empty_item)

    def set_refreshing(self, refreshing: bool) -> None:
        self._refresh_button.setEnabled(not refreshing)
        self._refresh_button.setText("Refreshing…" if refreshing else "Refresh")

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        url = item.data(0, _URL_ROLE)
        if url:
            QDesktopServices.openUrl(QUrl(url))
