"""Dialog listing recorded ERROR-level log entries (see applog.recent_errors()),
reachable both from the Actions menu ("Error Log", next to the existing "App
Log" clipboard action) and by clicking the status-bar error indicator once at
least one error has been logged.

Unlike CommitLogDialog/StashesDialog, there is no repo_path/adapter_factory
here -- the data source is applog's process-wide in-memory error store, not
git, so this dialog is much closer to a plain read-only viewer over that
store plus a Clear action.
"""

from collections.abc import Callable

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from local_changes_viewer.gui import applog


class ErrorLogDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        on_cleared: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        # Told about a Clear click so the caller (MainWindow) can hide its
        # status-bar indicator immediately, rather than waiting for that
        # indicator's own low-frequency refresh timer to notice the store is
        # now empty.
        self._on_cleared = on_cleared or (lambda: None)
        self.setWindowTitle("Error Log")
        parent_width = parent.width() if parent is not None else 900
        parent_height = parent.height() if parent is not None else 600
        self.resize(max(int(parent_width * 0.6), 600), max(int(parent_height * 0.6), 400))

        self._list = QListWidget()

        self._copy_button = QPushButton("Copy")
        self._copy_button.clicked.connect(self._on_copy)
        self._clear_button = QPushButton("Clear")
        self._clear_button.clicked.connect(self._on_clear)
        self._close_button = QPushButton("Close")
        self._close_button.clicked.connect(self.accept)

        button_row = QHBoxLayout()
        button_row.addWidget(self._copy_button)
        button_row.addWidget(self._clear_button)
        button_row.addStretch(1)
        button_row.addWidget(self._close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self._list)
        layout.addLayout(button_row)

        self._populate()

    def _populate(self) -> None:
        self._list.clear()
        errors = applog.recent_errors()
        for entry in errors:
            self._list.addItem(QListWidgetItem(entry))
        has_errors = bool(errors)
        self._copy_button.setEnabled(has_errors)
        self._clear_button.setEnabled(has_errors)

    def _on_copy(self) -> None:
        text = "\n".join(applog.recent_errors())
        QGuiApplication.clipboard().setText(text)

    def _on_clear(self) -> None:
        applog.clear_errors()
        self._populate()
        self._on_cleared()
