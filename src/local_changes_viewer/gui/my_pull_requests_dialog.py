from collections import defaultdict

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from local_changes_viewer.core.domain.pull_request import PullRequestInfo
from local_changes_viewer.gui.formatting import format_review_time

_COLUMNS = [
    "PR ID",
    "Link",
    "Title",
    "Approved",
    "Unresolved",
    "Last Reviewer",
    "Last Review Time",
    "Files",
    "Checks",
]
_URL_ROLE = Qt.ItemDataRole.UserRole
_REPO_ROLE = Qt.ItemDataRole.UserRole + 1
_NUMBER_ROLE = Qt.ItemDataRole.UserRole + 2

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


class PullRequestsTreeWidget(QWidget):
    """Tree of open pull requests, grouped by repository, with per-item and per-repo actions."""

    pull_request_refresh_requested = Signal(str, int)  # repository, number
    pull_request_info_requested = Signal(str, int)  # repository, number
    pull_request_issues_requested = Signal(str, int)  # repository, number

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(len(_COLUMNS))
        self._tree.setHeaderLabels(_COLUMNS)
        self._repo_prs: dict[str, list[PullRequestInfo]] = {}
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu_requested)

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
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.setColumnWidth(0, 275)
        self._tree.setColumnWidth(1, 70)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._tree)

    def set_pull_requests(self, pull_requests: list[PullRequestInfo]) -> None:
        self._tree.clear()
        self._repo_prs = {}

        if pull_requests:
            by_repo: dict[str, list[PullRequestInfo]] = defaultdict(list)
            for pr in pull_requests:
                by_repo[pr.repository].append(pr)
            self._repo_prs = dict(by_repo)

            for repo_name in sorted(by_repo):
                repo_item = QTreeWidgetItem([repo_name])
                font = repo_item.font(0)
                font.setBold(True)
                repo_item.setFont(0, font)
                self._tree.addTopLevelItem(repo_item)

                open_all_label = QLabel('<a href="#">Open All</a>')
                open_all_label.setOpenExternalLinks(False)
                open_all_label.linkActivated.connect(
                    lambda _checked, repo=repo_name: self._open_all_prs(repo)
                )
                self._tree.setItemWidget(repo_item, 1, open_all_label)

                for pr in by_repo[repo_name]:
                    pr_item = QTreeWidgetItem(
                        [
                            f"#{pr.number}",
                            "",
                            pr.title,
                            _approved_text(pr.approved),
                            str(pr.unresolved_review_thread_count),
                            pr.last_reviewer or "-",
                            format_review_time(pr.last_reviewed_at) if pr.last_reviewed_at else "-",
                            str(pr.changed_files),
                            _checks_text(pr.checks_state),
                        ]
                    )
                    pr_item.setData(0, _URL_ROLE, pr.url)
                    pr_item.setData(0, _REPO_ROLE, pr.repository)
                    pr_item.setData(0, _NUMBER_ROLE, pr.number)
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
        item = self._find_pr_item(repository, number)
        if item is None:
            return
        item.setText(3, _approved_text(approved))
        item.setText(4, str(unresolved_review_thread_count))
        item.setText(5, last_reviewer or "-")
        item.setText(6, format_review_time(last_reviewed_at) if last_reviewed_at else "-")
        item.setText(7, str(changed_files))
        item.setText(8, _checks_text(checks_state))

    def _find_pr_item(self, repository: str, number: int) -> QTreeWidgetItem | None:
        for i in range(self._tree.topLevelItemCount()):
            repo_item = self._tree.topLevelItem(i)
            for j in range(repo_item.childCount()):
                pr_item = repo_item.child(j)
                if (
                    pr_item.data(0, _REPO_ROLE) == repository
                    and pr_item.data(0, _NUMBER_ROLE) == number
                ):
                    return pr_item
        return None

    def _on_context_menu_requested(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if item is None:
            return

        if item.parent() is None:
            repository = item.text(0)
            if repository not in self._repo_prs:
                return
            self._tree.setCurrentItem(item)

            menu = QMenu(self._tree)
            menu.addAction("Open All", lambda: self._open_all_prs(repository))
            menu.addAction("Copy All URLs", lambda: self._copy_all_urls(repository))
            menu.exec(self._tree.viewport().mapToGlobal(pos))
            return

        repository = item.data(0, _REPO_ROLE)
        number = item.data(0, _NUMBER_ROLE)
        if repository is None or number is None:
            return
        self._tree.setCurrentItem(item)

        menu = QMenu(self._tree)
        menu.addAction(
            "Refresh", lambda: self.pull_request_refresh_requested.emit(repository, number)
        )
        menu.addAction(
            "Info", lambda: self.pull_request_info_requested.emit(repository, number)
        )
        menu.addAction(
            "Open Issues", lambda: self.pull_request_issues_requested.emit(repository, number)
        )
        menu.addAction(
            "Copy URL", lambda: self._copy_pr_url(item.data(0, _URL_ROLE), item.text(2))
        )
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _open_all_prs(self, repository: str) -> None:
        for pr in self._repo_prs.get(repository, []):
            QDesktopServices.openUrl(QUrl(pr.url))

    def _copy_all_urls(self, repository: str) -> None:
        lines = [f"{pr.url} - {pr.title}" for pr in self._repo_prs.get(repository, [])]
        QApplication.clipboard().setText("\n".join(lines))

    def _copy_pr_url(self, url: str, title: str) -> None:
        QApplication.clipboard().setText(f"{url} - {title}")

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        url = item.data(0, _URL_ROLE)
        if url:
            QDesktopServices.openUrl(QUrl(url))


class MyPullRequestsDialog(QDialog):
    refresh_requested = Signal()
    pull_request_refresh_requested = Signal(str, int)  # repository, number
    pull_request_info_requested = Signal(str, int)  # repository, number
    pull_request_issues_requested = Signal(str, int)  # repository, number

    def __init__(self, pull_requests: list[PullRequestInfo], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("My Open Pull Requests")
        width = int(parent.width() * 0.8) if parent is not None else 800
        self.resize(width, 500)

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
        self._tree_widget.set_pull_requests(pull_requests)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        self._refresh_button = buttons.addButton(
            "Refresh", QDialogButtonBox.ButtonRole.ActionRole
        )
        self._refresh_button.setToolTip("Refresh the list of pull requests")
        self._refresh_button.clicked.connect(self.refresh_requested.emit)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tree_widget)
        layout.addWidget(buttons)

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
