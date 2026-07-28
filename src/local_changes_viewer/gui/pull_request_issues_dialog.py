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

from local_changes_viewer.core.domain.pull_request import PullRequestThread
from local_changes_viewer.gui.formatting import format_timestamp

_COLUMNS = ["Date", "Writer", "Title", "Comment Type"]
_TITLE_COLUMN = 2
_URL_ROLE = Qt.ItemDataRole.UserRole


class PullRequestIssuesDialog(QDialog):
    def __init__(self, threads: list[PullRequestThread], pr_number: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Open Issues — PR #{pr_number}")
        width = int(parent.width() * 0.7) if parent is not None else 700
        self.resize(width, 400)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(len(_COLUMNS))
        self._tree.setHeaderLabels(_COLUMNS)
        self._tree.itemClicked.connect(self._on_item_clicked)

        point_size = self._tree.font().pointSize()
        if point_size > 0:
            self._tree.setStyleSheet(f"QToolTip {{ font-size: {point_size}pt; }}")

        if threads:
            for thread in threads:
                item = QTreeWidgetItem(
                    [
                        "",
                        thread.writer or "-",
                        thread.title,
                        thread.comment_type,
                    ]
                )
                item.setToolTip(_TITLE_COLUMN, thread.body)
                item.setData(0, _URL_ROLE, thread.url)
                self._tree.addTopLevelItem(item)

                date_label = QLabel(f'<a href="{thread.url}">{format_timestamp(thread.created_at)}</a>')
                date_label.setOpenExternalLinks(True)
                self._tree.setItemWidget(item, 0, date_label)
        else:
            empty_item = QTreeWidgetItem(["No open issues found."])
            empty_item.setFirstColumnSpanned(True)
            self._tree.addTopLevelItem(empty_item)

        header = self._tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tree)
        layout.addWidget(buttons)

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if column != _TITLE_COLUMN:
            return
        url = item.data(0, _URL_ROLE)
        if url:
            QDesktopServices.openUrl(QUrl(url))
