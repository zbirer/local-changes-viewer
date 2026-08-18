from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

_POPUP_TOP_MARGIN = 60  # keep clear of the macOS menu bar / camera notch


class CommentPopup(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._label = QLabel(self)
        # The popup text is a comment body straight from the GitHub API, so
        # it must be shown literally -- QLabel's default AutoText format
        # would otherwise render any markup the comment happens to contain.
        self._label.setTextFormat(Qt.TextFormat.PlainText)
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
