from collections import defaultdict

from PySide6.QtCore import QUrl, Qt
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

_COLUMNS = ["PR ID", "Link", "Title", "Approved", "Unresolved", "Last Reviewer"]
_URL_ROLE = Qt.ItemDataRole.UserRole


def _approved_text(approved: bool | None) -> str:
    if approved is None:
        return "-"
    return "Yes" if approved else "No"


class MyPullRequestsDialog(QDialog):
    def __init__(self, pull_requests: list[PullRequestInfo], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("My Open Pull Requests")
        self.resize(800, 500)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(len(_COLUMNS))
        self._tree.setHeaderLabels(_COLUMNS)
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)

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

        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.setColumnWidth(0, 275)
        self._tree.setColumnWidth(1, 70)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tree)
        layout.addWidget(buttons)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        url = item.data(0, _URL_ROLE)
        if url:
            QDesktopServices.openUrl(QUrl(url))
