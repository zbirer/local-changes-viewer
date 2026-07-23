from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)

from local_changes_viewer.core.domain.folder_filter_rule import FolderFilterMode, FolderFilterRule


class FolderFilterDialog(QDialog):
    rules_changed = Signal(list)  # list[FolderFilterRule]

    def __init__(self, rules: list[FolderFilterRule], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Folder Filters")
        self._rules = list(rules)

        self._list_widget = QListWidget()

        self._text_input = QLineEdit()
        self._text_input.setPlaceholderText("Folder name…")
        self._mode_combo = QComboBox()
        self._mode_combo.addItem("contains", FolderFilterMode.CONTAINS)
        self._mode_combo.addItem("equals", FolderFilterMode.EQUALS)

        add_button = QPushButton("Add")
        add_button.clicked.connect(self._on_add)

        remove_button = QPushButton("Remove Selected")
        remove_button.clicked.connect(self._on_remove)

        input_layout = QHBoxLayout()
        input_layout.addWidget(self._text_input)
        input_layout.addWidget(self._mode_combo)
        input_layout.addWidget(add_button)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("Files under a folder matching any rule below are hidden from the changes:")
        )
        layout.addWidget(self._list_widget)
        layout.addLayout(input_layout)
        layout.addWidget(remove_button)
        layout.addWidget(close_button)

        self._refresh_list()

    def _refresh_list(self) -> None:
        self._list_widget.clear()
        for rule in self._rules:
            self._list_widget.addItem(f"{rule.mode.value}: {rule.text}")

    def _on_add(self) -> None:
        text = self._text_input.text().strip()
        if not text:
            return
        mode = self._mode_combo.currentData()
        self._rules.append(FolderFilterRule(text=text, mode=mode))
        self._text_input.clear()
        self._refresh_list()
        self.rules_changed.emit(list(self._rules))

    def _on_remove(self) -> None:
        row = self._list_widget.currentRow()
        if row < 0:
            return
        del self._rules[row]
        self._refresh_list()
        self.rules_changed.emit(list(self._rules))
