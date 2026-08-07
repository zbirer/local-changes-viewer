from pathlib import Path

from PySide6.QtCore import QEvent, QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QToolButton, QToolTip, QTreeView, QWidget

from local_changes_viewer.gui import applog
from local_changes_viewer.gui.settings import AppSettings
from local_changes_viewer.gui.workspace_tree.tree_model import (
    FILE_CHANGE_ROLE,
    FOLDER_PATH_ROLE,
    NODE_KEY_ROLE,
    REPO_PATH_ROLE,
    RepoTreeModel,
)

_PATH_SEPARATORS = ("/", "\\")


class _WorkspaceFilterProxyModel(QSortFilterProxyModel):
    def __init__(self) -> None:
        super().__init__()
        self._repo_query = ""
        self._file_query = ""
        self._split_mode = False

    def set_filter_parts(self, repo_query: str, file_query: str, split_mode: bool) -> None:
        self._repo_query = repo_query.lower()
        self._file_query = file_query.lower()
        self._split_mode = split_mode
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        if not self._split_mode:
            return super().filterAcceptsRow(source_row, source_parent)

        index = self.sourceModel().index(source_row, 0, source_parent)
        if not source_parent.isValid():
            return self._repo_query in self._repo_name(index).lower()

        if self._repo_query not in self._repo_name(self._top_level_ancestor(index)).lower():
            return False
        if not self._file_query:
            return True
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        return self._file_query in text.lower()

    @staticmethod
    def _repo_name(index: QModelIndex) -> str:
        key = index.data(NODE_KEY_ROLE) or ""
        return Path(key).name if key else ""

    @staticmethod
    def _top_level_ancestor(index: QModelIndex) -> QModelIndex:
        parent = index.parent()
        while parent.isValid():
            index = parent
            parent = index.parent()
        return index


class RepoTreeView(QTreeView):
    file_selected = Signal(object, object)  # repo_path: Path, change: FileChange
    scope_changed = Signal(object, object)  # repo_path: Path | None, prefix: Path | None

    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self._settings = settings
        self._programmatic_change = False
        self._model = RepoTreeModel()
        self._proxy = _WorkspaceFilterProxyModel()
        self._proxy.setSourceModel(self._model)
        self._proxy.setRecursiveFilteringEnabled(True)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setModel(self._proxy)
        self.setHeaderHidden(True)
        self.collapsed.connect(self._on_collapsed)
        self.expanded.connect(self._on_expanded)
        self.selectionModel().currentChanged.connect(self._on_current_changed)

        self._row_actions_index = QModelIndex()
        self._row_actions_widget = QWidget(self.viewport())
        row_actions_layout = QHBoxLayout(self._row_actions_widget)
        row_actions_layout.setContentsMargins(0, 0, 0, 0)
        row_actions_layout.setSpacing(2)
        self._expand_button = QToolButton(self._row_actions_widget)
        self._expand_button.setText("+")
        self._expand_button.setToolTip("Expand All")
        self._expand_button.setAutoRaise(True)
        self._expand_button.setFixedSize(18, 18)
        self._collapse_button = QToolButton(self._row_actions_widget)
        self._collapse_button.setText("−")
        self._collapse_button.setToolTip("Collapse All")
        self._collapse_button.setAutoRaise(True)
        self._collapse_button.setFixedSize(18, 18)
        row_actions_layout.addWidget(self._expand_button)
        row_actions_layout.addWidget(self._collapse_button)
        self._row_actions_widget.hide()
        self._expand_button.clicked.connect(self._on_row_expand_clicked)
        self._collapse_button.clicked.connect(self._on_row_collapse_clicked)
        self.verticalScrollBar().valueChanged.connect(self._on_row_actions_scroll)
        self.horizontalScrollBar().valueChanged.connect(self._on_row_actions_scroll)

    def _on_row_expand_clicked(self) -> None:
        if self._row_actions_index.isValid():
            self.expand_index_recursive(self._row_actions_index)

    def _on_row_collapse_clicked(self) -> None:
        if self._row_actions_index.isValid():
            self.collapse_index_recursive(self._row_actions_index)

    def _on_row_actions_scroll(self, _value: int) -> None:
        self._update_row_actions_widget(self._row_actions_index)

    def _update_row_actions_widget(self, index: QModelIndex) -> None:
        is_repo_root = (
            index.isValid()
            and index.data(NODE_KEY_ROLE) == index.data(FOLDER_PATH_ROLE)
            and index.data(FOLDER_PATH_ROLE) is not None
        )
        if not is_repo_root:
            self._row_actions_widget.hide()
            self._row_actions_index = QModelIndex()
            return

        self._row_actions_index = index
        rect = self.visualRect(index)
        if rect.isEmpty() or not rect.intersects(self.viewport().rect()):
            self._row_actions_widget.hide()
            return

        widget_size = self._row_actions_widget.sizeHint()
        x = rect.right() - widget_size.width() - 4
        y = rect.top() + (rect.height() - widget_size.height()) // 2
        self._row_actions_widget.move(x, y)
        self._row_actions_widget.show()
        self._row_actions_widget.raise_()

    def viewportEvent(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            index = self.indexAt(event.pos())
            tooltip = index.data(Qt.ItemDataRole.ToolTipRole)
            if tooltip:
                rect = self.visualRect(index)
                QToolTip.showText(self.viewport().mapToGlobal(rect.bottomLeft()), tooltip, self, rect)
            else:
                QToolTip.hideText()
            return True
        return super().viewportEvent(event)

    def _on_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if self._programmatic_change:
            return
        change = current.data(FILE_CHANGE_ROLE)
        repo_path = current.data(REPO_PATH_ROLE)
        if change is not None and repo_path is not None:
            self.file_selected.emit(repo_path, change)
            self.scope_changed.emit(Path(repo_path), change.path.parent)
            self._update_row_actions_widget(current)
            return

        key = current.data(NODE_KEY_ROLE)
        if key is None:
            self._update_row_actions_widget(current)
            return
        if "::" in key:
            repo_str, prefix_str = key.split("::", maxsplit=1)
            self.scope_changed.emit(Path(repo_str), Path(prefix_str))
        else:
            self.scope_changed.emit(Path(key), None)
        self._update_row_actions_widget(current)

    def set_workspace(self, workspace) -> None:
        self._programmatic_change = True
        self._model.set_workspace(workspace)
        self.expandAll()
        self._restore_collapsed_state()
        self._programmatic_change = False

    def update_workspace(self, workspace) -> None:
        self._programmatic_change = True
        self._model.update_workspace(workspace)
        self._programmatic_change = False

    def has_rows(self) -> bool:
        return self._model.has_rows()

    def highlight_repo(self, repo_path: Path) -> None:
        self._model.set_repo_highlighted(repo_path, True)

    def unhighlight_repo(self, repo_path: Path) -> None:
        self._model.set_repo_highlighted(repo_path, False)

    def clear_repo_highlights(self) -> None:
        self._model.clear_all_highlights()

    def set_filter_text(self, text: str) -> None:
        sep_positions = [text.index(sep) for sep in _PATH_SEPARATORS if sep in text]
        if sep_positions:
            sep_index = min(sep_positions)
            self._proxy.set_filter_parts(text[:sep_index], text[sep_index + 1 :], True)
        else:
            self._proxy.set_filter_parts("", "", False)
            self._proxy.setFilterFixedString(text)
        if text:
            self._programmatic_change = True
            self.expandAll()
            self._programmatic_change = False

    def collapse_all(self) -> None:
        applog.log("Collapse All", level=applog.LogLevel.INFO)
        self._programmatic_change = True
        self.collapseAll()
        self._programmatic_change = False
        self._settings.set_collapsed_node_keys(self._collect_all_keys(QModelIndex()))

    def expand_all(self) -> None:
        applog.log("Expand All", level=applog.LogLevel.INFO)
        self._programmatic_change = True
        self.expandAll()
        self._programmatic_change = False
        self._settings.set_collapsed_node_keys(set())

    def expand_changed_repos(self) -> None:
        applog.log("Expand Changed Repos", level=applog.LogLevel.INFO)
        self._programmatic_change = True
        for row in range(self._proxy.rowCount(QModelIndex())):
            index = self._proxy.index(row, 0, QModelIndex())
            if self._proxy.rowCount(index) > 0:
                self.expand(index)
                self._expand_all_descendants(index)
        self._programmatic_change = False
        self._settings.set_collapsed_node_keys(self._collect_collapsed_keys(QModelIndex()))

    def _expand_all_descendants(self, index: QModelIndex) -> None:
        for row in range(self._proxy.rowCount(index)):
            child = self._proxy.index(row, 0, index)
            if self._proxy.rowCount(child) > 0:
                self.expand(child)
                self._expand_all_descendants(child)

    def _collapse_all_descendants(self, index: QModelIndex) -> None:
        for row in range(self._proxy.rowCount(index)):
            child = self._proxy.index(row, 0, index)
            if self._proxy.rowCount(child) > 0:
                self._collapse_all_descendants(child)
                self.collapse(child)

    def find_repo_index(self, repo_path: Path) -> QModelIndex:
        return self._find_index_by_key(str(repo_path), QModelIndex())

    def _find_index_by_key(self, key: str, parent: QModelIndex) -> QModelIndex:
        for row in range(self._proxy.rowCount(parent)):
            index = self._proxy.index(row, 0, parent)
            if index.data(NODE_KEY_ROLE) == key:
                return index
            found = self._find_index_by_key(key, index)
            if found.isValid():
                return found
        return QModelIndex()

    def current_repo_path(self) -> Path | None:
        index = self.currentIndex()
        while index.isValid():
            key = index.data(NODE_KEY_ROLE)
            folder = index.data(FOLDER_PATH_ROLE)
            if key is not None and folder is not None and key == folder:
                return Path(key)
            index = index.parent()
        return None

    def expand_repo(self, repo_path: Path) -> None:
        index = self.find_repo_index(repo_path)
        if not index.isValid():
            return
        applog.log(f"Expand Repo: {repo_path}", level=applog.LogLevel.INFO)
        self.expand_index_recursive(index)

    def collapse_repo(self, repo_path: Path) -> None:
        index = self.find_repo_index(repo_path)
        if not index.isValid():
            return
        applog.log(f"Collapse Repo: {repo_path}", level=applog.LogLevel.INFO)
        self.collapse_index_recursive(index)

    def expand_index_recursive(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        self._programmatic_change = True
        self.expand(index)
        self._expand_all_descendants(index)
        self._programmatic_change = False
        self._settings.set_collapsed_node_keys(self._collect_collapsed_keys(QModelIndex()))

    def collapse_index_recursive(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        self._programmatic_change = True
        self._collapse_all_descendants(index)
        self.collapse(index)
        self._programmatic_change = False
        self._settings.set_collapsed_node_keys(self._collect_collapsed_keys(QModelIndex()))

    def expand_current_repo(self) -> None:
        repo_path = self.current_repo_path()
        if repo_path is not None:
            self.expand_repo(repo_path)

    def collapse_current_repo(self) -> None:
        repo_path = self.current_repo_path()
        if repo_path is not None:
            self.collapse_repo(repo_path)

    def _collect_collapsed_keys(self, parent: QModelIndex) -> set[str]:
        keys: set[str] = set()
        for row in range(self._proxy.rowCount(parent)):
            index = self._proxy.index(row, 0, parent)
            if self._proxy.rowCount(index) > 0 and not self.isExpanded(index):
                key = index.data(NODE_KEY_ROLE)
                if key is not None:
                    keys.add(key)
            keys |= self._collect_collapsed_keys(index)
        return keys

    def _collect_all_keys(self, parent: QModelIndex) -> set[str]:
        keys: set[str] = set()
        for row in range(self._proxy.rowCount(parent)):
            index = self._proxy.index(row, 0, parent)
            key = index.data(NODE_KEY_ROLE)
            if key is not None:
                keys.add(key)
            keys |= self._collect_all_keys(index)
        return keys

    def _restore_collapsed_state(self) -> None:
        collapsed_keys = self._settings.collapsed_node_keys()
        applog.log(
            f"_restore_collapsed_state: collapsed_keys={collapsed_keys!r}",
            level=applog.LogLevel.DEBUG,
        )
        if not collapsed_keys:
            return
        all_keys = self._collect_all_keys(QModelIndex())
        applog.log(
            f"_restore_collapsed_state: current tree node keys={all_keys!r}",
            level=applog.LogLevel.DEBUG,
        )
        self._for_each_index(QModelIndex(), collapsed_keys)

    def _for_each_index(self, parent: QModelIndex, collapsed_keys: set[str]) -> None:
        for row in range(self._proxy.rowCount(parent)):
            index = self._proxy.index(row, 0, parent)
            key = index.data(NODE_KEY_ROLE)
            if key is not None and key in collapsed_keys:
                applog.log(f"_for_each_index: collapsing key={key!r}", level=applog.LogLevel.DEBUG)
                self.collapse(index)
            self._for_each_index(index, collapsed_keys)

    def _on_collapsed(self, index: QModelIndex) -> None:
        key = index.data(NODE_KEY_ROLE)
        if key is None or self._programmatic_change:
            self._update_row_actions_widget(self._row_actions_index)
            return
        applog.log(f"Collapsed folder: {key}", level=applog.LogLevel.INFO)
        keys = self._settings.collapsed_node_keys()
        keys.add(key)
        self._settings.set_collapsed_node_keys(keys)
        self._update_row_actions_widget(self._row_actions_index)

    def _on_expanded(self, index: QModelIndex) -> None:
        key = index.data(NODE_KEY_ROLE)
        if key is None or self._programmatic_change:
            self._update_row_actions_widget(self._row_actions_index)
            return
        applog.log(f"Expanded folder: {key}", level=applog.LogLevel.INFO)
        keys = self._settings.collapsed_node_keys()
        keys.discard(key)
        self._settings.set_collapsed_node_keys(keys)
        self._update_row_actions_widget(self._row_actions_index)
