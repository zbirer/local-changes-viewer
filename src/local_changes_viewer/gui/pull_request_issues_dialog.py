from PySide6.QtCore import QEvent, QPoint, QUrl, Qt
from PySide6.QtGui import QCursor, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from local_changes_viewer.core.domain.pull_request import PullRequestThread
from local_changes_viewer.gui.formatting import format_timestamp

_COLUMNS = ["Date", "Writer", "Title", "Comment Type"]
_TITLE_COLUMN = 2
_URL_ROLE = Qt.ItemDataRole.UserRole
_BODY_ROLE = Qt.ItemDataRole.UserRole + 1
_POPUP_TOP_MARGIN = 60  # keep clear of the macOS menu bar / camera notch


class _CommentPopup(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._label = QLabel(self)
        self._label.setWordWrap(True)
        self._label.setStyleSheet(
            "background-color: #2b2b2b; color: white; border: 1px solid #666; padding: 6px;"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)

    def show_near(self, text: str, anchor: QPoint) -> None:
        self._label.setMaximumWidth(600)
        self._label.setMinimumHeight(0)
        self._label.setMaximumHeight(16777215)
        self._label.setText(text)
        self.adjustSize()

        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        available = screen.availableGeometry()
        usable_height = available.height() - _POPUP_TOP_MARGIN - 20

        if self.height() > usable_height:
            self._label.setFixedHeight(usable_height)
            self.adjustSize()

        x = min(max(anchor.x(), available.left()), available.right() - self.width())
        y = available.top() + _POPUP_TOP_MARGIN + max(
            0, (usable_height - self.height()) // 2
        )
        self.move(x, y)
        self.show()


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

        self._comment_popup = _CommentPopup(self)

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
