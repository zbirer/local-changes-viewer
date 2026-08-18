import html

from PySide6.QtCore import QEvent, QUrl, Qt
from PySide6.QtGui import QCursor, QDesktopServices
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
from local_changes_viewer.gui.hover_popup import CommentPopup

_COLUMNS = ["Date", "Writer", "Title", "Comment Type"]
_TITLE_COLUMN = 2
_URL_ROLE = Qt.ItemDataRole.UserRole
_BODY_ROLE = Qt.ItemDataRole.UserRole + 1


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
        self._tree.setMouseTracking(True)
        self._tree.itemEntered.connect(self._on_item_entered)
        self._tree.viewport().installEventFilter(self)

        self._comment_popup = CommentPopup(self)

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
                item.setData(_TITLE_COLUMN, _BODY_ROLE, thread.body)
                item.setData(0, _URL_ROLE, thread.url)
                self._tree.addTopLevelItem(item)

                # thread.url is API-supplied; escape it before interpolating
                # into the anchor's href so a crafted URL can't break out of
                # the attribute and inject markup (the visible link text is
                # just our own timestamp formatting, not API text).
                escaped_url = html.escape(thread.url)
                date_label = QLabel(f'<a href="{escaped_url}">{format_timestamp(thread.created_at)}</a>')
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

    def _on_item_entered(self, item: QTreeWidgetItem, column: int) -> None:
        body = item.data(_TITLE_COLUMN, _BODY_ROLE) if column == _TITLE_COLUMN else None
        if not body:
            self._comment_popup.hide()
            return
        self._comment_popup.show_near(body, QCursor.pos())

    def eventFilter(self, obj, event) -> bool:
        if obj is self._tree.viewport() and event.type() == QEvent.Type.Leave:
            self._comment_popup.hide()
        return super().eventFilter(obj, event)
