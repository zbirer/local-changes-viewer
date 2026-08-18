"""Pick a tracked file under a folder, then browse its git history.

The inverse of `CommitLogDialog` (`gui/commit_log_dialog.py`): that dialog is
commit-first (pick a commit, see the files it touched); this one is
file-first (pick a file, see the commits that touched it). The two dialogs
are deliberately independent -- this one is never constructed from, or by,
`CommitLogDialog`.

Every `Path` this dialog hands to a worker/adapter is repo-relative, matching
the domain rule stated in `core/domain/file_history.py` -- `_subtree`,
`_selected_file_path`, `TrackedFile.path`, `FileHistoryCommit.path_at_commit`
and `FileHistoryResult.current_path` are all repo-relative. Absolute paths
exist only at this GUI edge: the results list displays them, and commit
menu actions ("Copy file path") report them.

Cancellation follows the contract `core/infra/cancel_token.py` documents:
selecting a different file, a different commit, or flipping the mode radio
cancels the outgoing request's `CancelToken` *before* starting the
replacement (never after -- see `_select_file`/`_start_diff_worker`), and
closing the dialog (`reject`/`closeEvent`) cancels whatever is still
in flight. `cancel()` only kills the subprocess and flips a flag; the worker
that owned it always finishes `run()` and emits `finished`, so nothing is
ever freed out from under `QThreadPool` mid-flight -- that in-flight free is
the segfault this whole branch exists to fix. The stale-result guards below
(`file_path != self._selected_file_path`, `request_key != (...)`) are a
second, independent backstop: a signal can already be queued by the time
`cancel()` fires, and cancellation alone would not stop that queued result
from landing on a widget that has since moved on to something else.
"""

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QThreadPool, QUrl, Qt
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QRadioButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from local_changes_viewer.core.domain.diff import DiffResult
from local_changes_viewer.core.domain.file_history import (
    FileHistoryCommit,
    FileHistoryResult,
    TrackedFile,
    TrackedFilesResult,
)
from local_changes_viewer.core.infra.cancel_token import CancelToken
from local_changes_viewer.core.infra.git_repo_adapter import GitRepoAdapter
from local_changes_viewer.core.infra.github_client import parse_github_owner_repo
from local_changes_viewer.gui import applog
from local_changes_viewer.gui.diff_view.side_by_side_view import SideBySideView
from local_changes_viewer.gui.diff_view.unified_view import UnifiedView
from local_changes_viewer.gui.hover_popup import CommentPopup
from local_changes_viewer.gui.workers.file_history_commits_worker import FileHistoryCommitsWorker
from local_changes_viewer.gui.workers.file_history_diff_worker import (
    FileHistoryDiffMode,
    FileHistoryDiffWorker,
)
from local_changes_viewer.gui.workers.file_history_files_worker import FileHistoryFilesWorker
from local_changes_viewer.gui.workers.worker_keeper import start_worker

_TRACKED_FILE_ROLE = Qt.ItemDataRole.UserRole
_COMMIT_ENTRY_ROLE = Qt.ItemDataRole.UserRole

_MIN_QUERY_LEN = 2
_MAX_RESULTS_SHOWN = 10
_COMMIT_LIMIT = 10
_COMMIT_COLUMNS = ("Time", "Author", "Message")

_DOT_TOOLTIP = "Has uncommitted changes"
_NO_GITHUB_REMOTE_TOOLTIP = "No GitHub remote configured for this repository"

# Amber, matching the "heads-up, not an error" dot color already used for
# unpushed worktrees in bulk_delete_worktrees_dialog.py.
_DOT_COLOR = QColor("#f59e0b")


class FileHistoryDialog(QDialog):
    def __init__(
        self,
        repo_path: Path,
        folder_path: Path,
        adapter_factory: Callable[[Path], object] = GitRepoAdapter,
        thread_pool: QThreadPool | None = None,
        parent: QWidget | None = None,
        initial_file: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._repo_path = repo_path
        self._adapter_factory = adapter_factory
        self._thread_pool = thread_pool if thread_pool is not None else QThreadPool.globalInstance()
        try:
            self._subtree = folder_path.relative_to(repo_path)
        except ValueError:
            # Defensive only: the caller (MainWindow's _find_owning_repository)
            # is responsible for ensuring folder_path is under repo_path.
            self._subtree = Path(".")

        # Cached once at open (or on an explicit Refresh) -- every keystroke
        # in the search box re-filters this in-memory list rather than
        # re-invoking list_tracked_files, per the settled spec.
        self._all_files: list[TrackedFile] = []
        self._subtree_too_large = False

        self._selected_file_path: Path | None = None
        self._commits: list[FileHistoryCommit] = []
        self._current_path: Path | None = None
        self._selected_commit_index = -1
        self._diff_mode = FileHistoryDiffMode.COMMIT

        # One outstanding token per cancellable request category -- selecting
        # a new file cancels both (its own commits fetch AND whatever diff
        # fetch was in flight for the file it replaces); selecting a new
        # commit or flipping the mode only cancels the diff token.
        self._commits_cancel_token: CancelToken | None = None
        self._diff_cancel_token: CancelToken | None = None

        self._dot_icon_cache: QIcon | None = None

        self.setWindowTitle(f"File History — {folder_path.name}")
        parent_width = parent.width() if parent is not None else 1200
        parent_height = parent.height() if parent is not None else 700
        self.resize(int(parent_width * 0.85), int(parent_height * 0.85))

        self._build_ui()
        self._apply_filter()
        self._search_box.setFocus()

        self._load_files()
        if initial_file is not None:
            self._select_file(initial_file)

    # -- construction ----------------------------------------------------

    def _build_ui(self) -> None:
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Search files by name (or path/, with a '/')…")
        self._search_box.textChanged.connect(self._on_search_text_changed)
        self._search_box.installEventFilter(self)

        self._refresh_button = QPushButton("Refresh")
        self._refresh_button.clicked.connect(self._load_files)

        search_row = QHBoxLayout()
        search_row.addWidget(self._search_box, 1)
        search_row.addWidget(self._refresh_button)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)

        self._results_list = QListWidget()
        self._results_list.itemClicked.connect(self._on_result_item_activated)
        self._results_list.itemActivated.connect(self._on_result_item_activated)

        self._commit_status_label = QLabel("Select a file to see its history")
        self._commit_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._commit_status_label.setWordWrap(True)

        self._commit_table = QTableWidget(0, len(_COMMIT_COLUMNS))
        self._commit_table.setHorizontalHeaderLabels(_COMMIT_COLUMNS)
        self._commit_table.verticalHeader().setVisible(False)
        self._commit_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._commit_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._commit_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self._commit_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self._commit_table.currentCellChanged.connect(self._on_commit_current_cell_changed)
        self._commit_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._commit_table.customContextMenuRequested.connect(self._on_commit_table_context_menu)
        # Hover-anywhere-on-the-row -> full message popup, same wiring idiom
        # as PullRequestIssuesDialog (setMouseTracking + cellEntered/
        # itemEntered + a viewport eventFilter that hides on Leave) -- see
        # hover_popup.py for why the popup class itself moved there.
        self._commit_table.setMouseTracking(True)
        self._commit_table.cellEntered.connect(self._on_commit_cell_entered)
        self._commit_table.viewport().installEventFilter(self)

        self._comment_popup = CommentPopup(self)

        self._commit_area_stack = QStackedWidget()
        self._commit_area_stack.addWidget(self._commit_status_label)
        self._commit_area_stack.addWidget(self._commit_table)

        left_splitter = QSplitter(Qt.Orientation.Vertical)
        left_splitter.addWidget(self._labeled_panel("Results", self._results_list))
        left_splitter.addWidget(self._labeled_panel("Commits", self._commit_area_stack))
        left_splitter.setStretchFactor(0, 2)
        left_splitter.setStretchFactor(1, 3)

        # Builds self._diff_area_stack (and everything nested inside it)
        # as a side effect; nothing here needs its return value directly.
        self._build_diff_panel()

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.addWidget(left_splitter)
        main_splitter.addWidget(self._labeled_panel("Diff", self._diff_area_stack))
        main_splitter.setStretchFactor(0, 2)
        main_splitter.setStretchFactor(1, 3)

        layout = QVBoxLayout(self)
        layout.addLayout(search_row)
        layout.addWidget(self._status_label)
        layout.addWidget(main_splitter)

    def _build_diff_panel(self) -> None:
        self._view_toggle_button = QPushButton("Side-by-side")
        self._view_toggle_button.setToolTip("Toggle side-by-side / unified diff view")
        self._view_toggle_button.setCheckable(True)
        self._view_toggle_button.toggled.connect(self._on_view_toggled)

        toggle_row = QHBoxLayout()
        toggle_row.addWidget(self._view_toggle_button)
        toggle_row.addStretch(1)

        self._commit_mode_radio = QRadioButton("Changes in this commit")
        self._disk_mode_radio = QRadioButton("Compared to file on disk")
        self._commit_mode_radio.setChecked(True)
        self._mode_button_group = QButtonGroup(self)
        self._mode_button_group.addButton(self._commit_mode_radio)
        self._mode_button_group.addButton(self._disk_mode_radio)
        self._commit_mode_radio.toggled.connect(self._on_mode_toggled)
        self._disk_mode_radio.toggled.connect(self._on_mode_toggled)

        mode_row = QHBoxLayout()
        mode_row.addWidget(self._commit_mode_radio)
        mode_row.addWidget(self._disk_mode_radio)
        mode_row.addStretch(1)

        self._now_at_label = QLabel("")
        self._now_at_label.setVisible(False)

        self._unified_view = UnifiedView()
        self._side_by_side_view = SideBySideView()
        self._diff_stack = QStackedWidget()
        self._diff_stack.addWidget(self._unified_view)
        self._diff_stack.addWidget(self._side_by_side_view)

        self._diff_panel_widget = QWidget()
        panel_layout = QVBoxLayout(self._diff_panel_widget)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.addLayout(toggle_row)
        panel_layout.addLayout(mode_row)
        panel_layout.addWidget(self._now_at_label)
        panel_layout.addWidget(self._diff_stack)

        self._diff_status_label = QLabel("Select a commit above to view its diff")
        self._diff_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._diff_status_label.setWordWrap(True)

        self._diff_area_stack = QStackedWidget()
        self._diff_area_stack.addWidget(self._diff_status_label)
        self._diff_area_stack.addWidget(self._diff_panel_widget)

    @staticmethod
    def _labeled_panel(title: str, widget: QWidget) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(title)
        label.setStyleSheet("color: #6B7280;")
        layout.addWidget(label)
        layout.addWidget(widget)
        return panel

    # -- file listing / search --------------------------------------------

    def _load_files(self) -> None:
        worker = FileHistoryFilesWorker(self._repo_path, self._subtree, self._adapter_factory)

        def _on_success(result: TrackedFilesResult) -> None:
            self._all_files = result.files
            self._subtree_too_large = result.too_large
            self._apply_filter()

        def _on_error(message: str) -> None:
            applog.log(message, level=applog.LogLevel.WARNING)
            self._all_files = []
            self._subtree_too_large = False
            self._results_list.clear()
            self._status_label.setText(f"Failed to list files: {message}")

        worker.signals.succeeded.connect(_on_success)
        worker.signals.error.connect(_on_error)
        start_worker(self._thread_pool, worker)

    def _on_search_text_changed(self, _text: str) -> None:
        self._apply_filter()

    def _apply_filter(self) -> None:
        query = self._search_box.text()
        self._results_list.clear()

        if self._subtree_too_large:
            self._status_label.setText(
                "This folder has too many tracked files to list (over 5,000). "
                "Narrow your selection and try again."
            )
            return

        if len(query) < _MIN_QUERY_LEN:
            self._status_label.setText("Type at least 2 characters to search")
            return

        matches = self._match_and_rank(self._all_files, query)
        if not matches:
            self._status_label.setText(f"No files match '{query}'")
            return

        shown = matches[:_MAX_RESULTS_SHOWN]
        self._populate_results_list(shown)
        if len(matches) > _MAX_RESULTS_SHOWN:
            self._status_label.setText(
                f"showing {_MAX_RESULTS_SHOWN} of {len(matches)} matches — narrow your search"
            )
        else:
            self._status_label.setText("")

    @staticmethod
    def _match_and_rank(files: list[TrackedFile], query: str) -> list[TrackedFile]:
        """Filename-only match, unless `query` contains '/' (then it matches
        against the repo-relative path instead) -- mirrors the convention
        `workspace_tree/tree_view.py`'s filter proxy uses, though the ranking
        itself (exact -> prefix -> substring, alpha within each tier) is new:
        nothing in this repo already ranks a flat file list like this.
        """
        use_path = "/" in query
        q = query.lower()

        def key(tf: TrackedFile) -> str:
            return str(tf.path).lower() if use_path else tf.path.name.lower()

        def tier(tf: TrackedFile) -> int:
            text = key(tf)
            if text == q:
                return 0
            if text.startswith(q):
                return 1
            return 2

        matches = [tf for tf in files if q in key(tf)]
        matches.sort(key=lambda tf: (tier(tf), key(tf)))
        return matches

    def _populate_results_list(self, files: list[TrackedFile]) -> None:
        for tracked_file in files:
            abs_path = self._repo_path / tracked_file.path
            item = QListWidgetItem(str(abs_path))
            item.setData(_TRACKED_FILE_ROLE, tracked_file)
            tooltip = str(abs_path)
            if tracked_file.has_local_changes:
                item.setIcon(self._dot_icon())
                tooltip = f"{tooltip}\n{_DOT_TOOLTIP}"
            item.setToolTip(tooltip)
            self._results_list.addItem(item)

    def _dot_icon(self) -> QIcon:
        if self._dot_icon_cache is None:
            pixmap = QPixmap(10, 10)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(_DOT_COLOR)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(1, 1, 8, 8)
            painter.end()
            self._dot_icon_cache = QIcon(pixmap)
        return self._dot_icon_cache

    def _on_result_item_activated(self, item: QListWidgetItem) -> None:
        tracked_file: TrackedFile | None = item.data(_TRACKED_FILE_ROLE)
        if tracked_file is None:
            return
        self._select_file(tracked_file.path)

    # -- commit history ----------------------------------------------------

    def _select_file(self, file_path: Path) -> None:
        self._selected_file_path = file_path
        self._selected_commit_index = -1
        self._commits = []
        self._current_path = None
        self._show_commit_status("Loading commit history…")
        self._clear_diff_pane_to_empty()

        if self._commits_cancel_token is not None:
            self._commits_cancel_token.cancel()
        if self._diff_cancel_token is not None:
            self._diff_cancel_token.cancel()
            self._diff_cancel_token = None

        token = CancelToken()
        self._commits_cancel_token = token
        worker = FileHistoryCommitsWorker(
            self._repo_path, file_path, token, self._adapter_factory, limit=_COMMIT_LIMIT
        )

        def _on_success(result: FileHistoryResult) -> None:
            if file_path != self._selected_file_path or token.is_cancelled:
                return
            self._commits = result.entries
            self._current_path = result.current_path
            if self._commits:
                self._populate_commit_table()
                self._commit_table.selectRow(0)
            else:
                self._show_commit_status("No commits yet for this file")

        def _on_error(message: str) -> None:
            if file_path != self._selected_file_path:
                return
            applog.log(message, level=applog.LogLevel.WARNING)
            self._show_commit_status(f"Failed to load commit history: {message}")

        worker.signals.succeeded.connect(_on_success)
        worker.signals.error.connect(_on_error)
        start_worker(self._thread_pool, worker)

    def _populate_commit_table(self) -> None:
        self._commit_table.setRowCount(len(self._commits))
        for row, entry in enumerate(self._commits):
            commit = entry.commit
            values = (
                commit.committed_datetime.strftime("%Y-%m-%d %H:%M"),
                commit.author,
                commit.message,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                tooltip = commit.full_message if column == 2 and commit.full_message else value
                item.setToolTip(tooltip)
                self._commit_table.setItem(row, column, item)
            self._commit_table.item(row, 0).setData(_COMMIT_ENTRY_ROLE, entry)
        self._commit_area_stack.setCurrentWidget(self._commit_table)

    def _commit_at_row(self, row: int) -> FileHistoryCommit | None:
        item = self._commit_table.item(row, 0)
        if item is None:
            return None
        return item.data(_COMMIT_ENTRY_ROLE)

    def _show_commit_status(self, text: str) -> None:
        self._commit_status_label.setText(text)
        self._commit_area_stack.setCurrentWidget(self._commit_status_label)

    def _on_commit_cell_entered(self, row: int, _column: int) -> None:
        entry = self._commit_at_row(row)
        if entry is None:
            self._comment_popup.hide()
            return
        text = entry.commit.full_message or entry.commit.message
        self._comment_popup.show_near(text, QCursor.pos())

    # -- commit menu -------------------------------------------------------

    def _on_commit_table_context_menu(self, pos) -> None:
        menu = self._build_commit_context_menu(pos)
        if menu is not None:
            menu.exec(self._commit_table.viewport().mapToGlobal(pos))

    def _build_commit_context_menu(self, pos) -> QMenu | None:
        """Split out from the connected slot so tests can build the menu and
        trigger its actions without calling QMenu.exec() -- exec() opens a
        real native modal loop under the offscreen platform that never gets
        a click to close it (same reasoning as unified_view.py's
        _build_context_menu).
        """
        index = self._commit_table.indexAt(pos)
        if not index.isValid():
            return None
        self._commit_table.selectRow(index.row())
        entry = self._commit_at_row(index.row())
        if entry is None:
            return None

        menu = QMenu(self._commit_table)
        menu.addAction(
            "Copy commit hash",
            lambda: QGuiApplication.clipboard().setText(entry.commit.hexsha),
        )

        github_url = self._github_commit_url(entry.commit.hexsha)
        github_action = menu.addAction(
            "Open commit on GitHub",
            lambda: QDesktopServices.openUrl(QUrl(github_url)) if github_url else None,
        )
        github_action.setEnabled(github_url is not None)
        github_action.setToolTip(github_url if github_url is not None else _NO_GITHUB_REMOTE_TOOLTIP)

        abs_path = self._repo_path / entry.path_at_commit
        menu.addAction(
            "Copy file path",
            lambda: QGuiApplication.clipboard().setText(str(abs_path)),
        )
        return menu

    def _github_commit_url(self, hexsha: str) -> str | None:
        adapter = self._adapter_factory(self._repo_path)
        remote_url = adapter.get_remote_url()
        if not remote_url:
            return None
        parsed = parse_github_owner_repo(remote_url)
        if parsed is None:
            return None
        owner, repo = parsed
        return f"https://github.com/{owner}/{repo}/commit/{hexsha}"

    # -- diff ---------------------------------------------------------------

    def _on_commit_current_cell_changed(
        self, current_row: int, _current_column: int, _previous_row: int, _previous_column: int
    ) -> None:
        self._selected_commit_index = current_row
        if current_row < 0 or current_row >= len(self._commits):
            self._clear_diff_pane_to_empty()
            return
        self._start_diff_worker(current_row)

    def _on_mode_toggled(self, checked: bool) -> None:
        if not checked:
            # QButtonGroup fires toggled(False) on the button losing the
            # check and toggled(True) on the one gaining it -- react only
            # to the latter, or this would run the whole reload twice.
            return
        mode = FileHistoryDiffMode.AGAINST_DISK if self._disk_mode_radio.isChecked() else FileHistoryDiffMode.COMMIT
        if mode == self._diff_mode:
            return
        self._diff_mode = mode
        if 0 <= self._selected_commit_index < len(self._commits):
            self._start_diff_worker(self._selected_commit_index)

    def _on_view_toggled(self, checked: bool) -> None:
        self._diff_stack.setCurrentIndex(1 if checked else 0)
        self._view_toggle_button.setText("Unified" if checked else "Side-by-side")

    def _start_diff_worker(self, commit_index: int) -> None:
        entry = self._commits[commit_index]

        if self._diff_cancel_token is not None:
            self._diff_cancel_token.cancel()
        token = CancelToken()
        self._diff_cancel_token = token

        mode = self._diff_mode
        file_path = self._selected_file_path
        request_key = (file_path, commit_index, mode)

        if mode is FileHistoryDiffMode.COMMIT:
            worker = FileHistoryDiffWorker(
                self._repo_path,
                mode,
                entry.commit.hexsha,
                entry.path_at_commit,
                self._adapter_factory,
                renamed_from=entry.renamed_from,
                cancel_token=token,
            )
            highlight_path = str(entry.path_at_commit)
        else:
            current_path = self._current_path
            current_abs = self._repo_path / current_path if current_path is not None else None
            current_exists = current_abs is not None and current_abs.exists()
            worker = FileHistoryDiffWorker(
                self._repo_path,
                mode,
                entry.commit.hexsha,
                entry.path_at_commit,
                self._adapter_factory,
                current_path=current_path,
                cancel_token=token,
            )
            highlight_path = str(current_path) if current_exists else str(entry.path_at_commit)

        def _on_success(diff: DiffResult) -> None:
            if request_key != (self._selected_file_path, self._selected_commit_index, self._diff_mode):
                return
            if token.is_cancelled:
                return
            self._render_diff(diff, highlight_path, entry)

        def _on_error(message: str) -> None:
            if request_key != (self._selected_file_path, self._selected_commit_index, self._diff_mode):
                return
            applog.log(message, level=applog.LogLevel.WARNING)
            self._show_diff_status(f"Failed to load diff: {message}")

        worker.signals.succeeded.connect(_on_success)
        worker.signals.error.connect(_on_error)
        start_worker(self._thread_pool, worker)

    def _render_diff(self, diff: DiffResult, highlight_path: str, entry: FileHistoryCommit) -> None:
        self._diff_area_stack.setCurrentWidget(self._diff_panel_widget)
        self._unified_view.set_diff(diff, highlight_path)
        self._side_by_side_view.set_diff(diff, highlight_path)
        self._update_now_at_label(entry)

    def _update_now_at_label(self, entry: FileHistoryCommit) -> None:
        if (
            self._diff_mode is FileHistoryDiffMode.AGAINST_DISK
            and self._current_path is not None
            and self._current_path != entry.path_at_commit
        ):
            abs_path = self._repo_path / self._current_path
            self._now_at_label.setText(f"now at {abs_path}")
            self._now_at_label.setVisible(True)
        else:
            self._now_at_label.clear()
            self._now_at_label.setVisible(False)

    def _show_diff_status(self, text: str) -> None:
        self._diff_status_label.setText(text)
        self._diff_area_stack.setCurrentWidget(self._diff_status_label)

    def _clear_diff_pane_to_empty(self) -> None:
        self._unified_view.clear_diff()
        self._side_by_side_view.clear_diff()
        self._now_at_label.clear()
        self._now_at_label.setVisible(False)
        self._show_diff_status("Select a commit above to view its diff")

    # -- keyboard / focus ----------------------------------------------------

    def eventFilter(self, obj, event) -> bool:
        if obj is self._commit_table.viewport() and event.type() == QEvent.Type.Leave:
            self._comment_popup.hide()
            return super().eventFilter(obj, event)
        if (
            obj is self._search_box
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Down
            and self._results_list.count() > 0
        ):
            self._results_list.setFocus()
            if self._results_list.currentRow() < 0:
                self._results_list.setCurrentRow(0)
            return True
        return super().eventFilter(obj, event)

    # -- cancel-on-close -----------------------------------------------------

    def reject(self) -> None:
        self._cancel_all()
        super().reject()

    def closeEvent(self, event) -> None:
        self._cancel_all()
        super().closeEvent(event)

    def _cancel_all(self) -> None:
        if self._commits_cancel_token is not None:
            self._commits_cancel_token.cancel()
        if self._diff_cancel_token is not None:
            self._diff_cancel_token.cancel()
