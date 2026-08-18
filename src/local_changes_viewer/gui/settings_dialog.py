from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from local_changes_viewer.gui import applog

if TYPE_CHECKING:
    # Only for type hints -- importing MainWindow at runtime would be
    # circular (main_window.py imports this module to open the dialog).
    from local_changes_viewer.gui.main_window import MainWindow

# Matches the dimmed secondary-text color already used for explanatory
# labels elsewhere in the app (commit_log_dialog.py, diff_view_widget.py).
_EXPLANATION_STYLE = "color: #6B7280;"

# Sourced from applog.LOG_LEVEL_NAMES (not a hardcoded list here) so this
# combo can never drift from -- and silently narrow -- the level set the
# legacy Log Level… dialog offers. A user on VERBOSE must see VERBOSE here,
# not have it disappear the moment this dialog opens.
_LOG_LEVELS = applog.LOG_LEVEL_NAMES

# Matches the legacy Tooltip Font Size… dialog's range exactly (main_window
# _on_configure_tooltip_font_size uses QInputDialog.getInt(..., 0, 36)) so a
# persisted value from that dialog is never out of this spinbox's range.
_TOOLTIP_FONT_SIZE_MAX = 36


def _explanation_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(_EXPLANATION_STYLE)
    return label


def _widen_for_special_value_text(spinbox: QSpinBox, text: str) -> None:
    """A QSpinBox sizes itself from its numeric range, not from
    setSpecialValueText()'s string, so a special-value label longer than
    the widest number (e.g. "System default" vs. "36") gets clipped.
    Widen the box to fit the longest of its special-value text and its
    numeric+suffix display, plus room for the up/down spin arrows."""
    metrics = QFontMetrics(spinbox.font())
    numeric_text = f"{spinbox.maximum()}{spinbox.suffix()}"
    text_width = max(metrics.horizontalAdvance(text), metrics.horizontalAdvance(numeric_text))
    spinbox.setMinimumWidth(text_width + 40)


class SettingsDialog(QDialog):
    """Lists every user-configurable setting in one tabbed dialog -- one
    tab per area -- each paired with a plain-English explanation.

    Takes the live MainWindow (rather than a narrower interface) because
    every control here drives a QAction or a persist+apply helper that
    MainWindow already owns (see D1 in the settings-dialog design): the
    dialog reuses `action.setChecked()`/`action.isChecked()` and helpers
    like `_set_auto_refresh_minutes()` so behavior is identical to using
    the existing menus, never a re-implementation. Constructing this
    dialog does no scanning and mutates no setting (see `_loading`), so
    it's cheap and safe to instantiate against a MainWindow that has no
    open workspace -- e.g. in tests.

    Tabs, not a scroll area: each tab is sized to fit its own controls at
    the dialog's default size, so the tallest tab (Display, with 7
    controls) needs no internal scrolling -- see the dialog's fixed
    resize() height below, picked by rendering the Display tab and
    reading back its required height.
    """

    def __init__(self, main_window: "MainWindow", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        # 630 was measured empirically: QTabWidget's sizeHint already
        # incorporates the tallest tab's page (QStackedWidget sizes to the
        # largest of its children), and 616 was that measured sizeHint's
        # height at this dialog's default width -- +14px of headroom.
        self.resize(640, 630)
        self.setMinimumWidth(560)
        self._main_window = main_window
        # True only while __init__ is populating widgets from current
        # state; value-changed handlers no-op while this is set, mirroring
        # MainWindow's own _restoring_settings guard (D3) so populating
        # the dialog can never re-apply/re-persist N settings it merely
        # read.
        self._loading = True

        self._tabs = QTabWidget()
        # "&" in a tab label (like a QGroupBox title) is eaten as a
        # mnemonic marker by Qt -- "&&" is the escape for a literal "&".
        self._tabs.addTab(self._build_scanning_tab(), "Scanning")
        self._tabs.addTab(self._build_display_tab(), "Display")
        self._tabs.addTab(self._build_filters_profiles_tab(), "Filters && Profiles")
        self._tabs.addTab(self._build_diagnostics_tab(), "Diagnostics")

        close_button = QPushButton("Close")
        close_button.setToolTip("Close this dialog")
        close_button.clicked.connect(self.accept)
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_row.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tabs)
        layout.addLayout(close_row)

        self._load_current_state()
        self._loading = False

    # -- row builders ---------------------------------------------------

    @staticmethod
    def _row(top: QWidget, explanation: str) -> QWidget:
        row = QWidget()
        row_layout = QVBoxLayout(row)
        row_layout.setContentsMargins(0, 4, 0, 4)
        row_layout.addWidget(top)
        row_layout.addWidget(_explanation_label(explanation))
        return row

    @staticmethod
    def _titled_control(title: str, control: QWidget) -> QWidget:
        top = QWidget()
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addWidget(QLabel(title))
        top_layout.addWidget(control)
        top_layout.addStretch(1)
        return top

    def _checkbox_row(
        self, label: str, explanation: str, on_toggled
    ) -> tuple[QCheckBox, QWidget]:
        checkbox = QCheckBox(label)
        checkbox.toggled.connect(on_toggled)
        return checkbox, self._row(checkbox, explanation)

    # -- tabs -----------------------------------------------------------

    def _build_scanning_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        self._show_ignored_checkbox, row = self._checkbox_row(
            "Show ignored files",
            "Files matched by .gitignore are included in each repo's change "
            "list instead of being hidden.",
            self._on_show_ignored_toggled,
        )
        layout.addWidget(row)

        self._show_unpushed_checkbox, row = self._checkbox_row(
            "Show committed but not pushed files",
            "Files changed in commits that exist locally but haven't been "
            "pushed to the remote are included in the change list.",
            self._on_show_unpushed_toggled,
        )
        layout.addWidget(row)

        self._watch_file_changes_checkbox, row = self._checkbox_row(
            "Watch for file changes",
            "The app watches every changed file and repo directory on disk "
            "and automatically rescans shortly after something changes, "
            "without waiting for the next auto-refresh tick.",
            self._on_watch_file_changes_toggled,
        )
        layout.addWidget(row)

        self._auto_refresh_spinbox = QSpinBox()
        self._auto_refresh_spinbox.setRange(0, 1440)
        self._auto_refresh_spinbox.setSuffix(" min")
        self._auto_refresh_spinbox.setSpecialValueText("Off")
        _widen_for_special_value_text(self._auto_refresh_spinbox, "Off")
        self._auto_refresh_spinbox.valueChanged.connect(self._on_auto_refresh_changed)
        layout.addWidget(
            self._row(
                self._titled_control("Auto refresh interval:", self._auto_refresh_spinbox),
                "Periodically rescans the workspace on its own, without "
                "needing a manual refresh; 0 turns this off.",
            )
        )

        layout.addStretch(1)
        return tab

    def _build_display_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        self._ignore_md_checkbox, row = self._checkbox_row(
            "Ignore MD files",
            "Markdown files are excluded from every repo's change list; a "
            "repo whose only change is a .md file will then look empty.",
            self._on_ignore_md_toggled,
        )
        layout.addWidget(row)

        self._hide_empty_repos_checkbox, row = self._checkbox_row(
            "Hide repos without changes",
            "Repositories and worktrees with nothing to show are omitted "
            "from the tree, including clean git worktrees.",
            self._on_hide_empty_repos_toggled,
        )
        layout.addWidget(row)

        self._hide_changeless_worktrees_checkbox, row = self._checkbox_row(
            "Hide empty worktrees",
            "Worktrees with no changed files are hidden from the folder "
            "tree; unchecked (default), every worktree is always shown, "
            'regardless of changes -- matching "List Worktrees".',
            self._on_hide_changeless_worktrees_toggled,
        )
        layout.addWidget(row)

        self._ignore_whitespace_checkbox, row = self._checkbox_row(
            "Ignore whitespace",
            "Diffs are computed ignoring whitespace-only changes, so a line "
            "that only had its indentation or trailing spaces changed won't "
            "show up as modified.",
            self._on_ignore_whitespace_toggled,
        )
        layout.addWidget(row)

        self._always_reload_diff_checkbox, row = self._checkbox_row(
            "Always reload fresh diff",
            "Selecting a file always recomputes its diff from disk instead "
            "of reusing a diff computed earlier in the session.",
            self._on_always_reload_diff_toggled,
        )
        layout.addWidget(row)

        self._sync_scroll_checkbox, row = self._checkbox_row(
            "Sync side-by-side scroll",
            "In side-by-side diff view, scrolling one pane scrolls the "
            "other pane to match.",
            self._on_sync_scroll_toggled,
        )
        layout.addWidget(row)

        self._tooltip_font_size_spinbox = QSpinBox()
        self._tooltip_font_size_spinbox.setRange(0, _TOOLTIP_FONT_SIZE_MAX)
        self._tooltip_font_size_spinbox.setSpecialValueText("System default")
        _widen_for_special_value_text(self._tooltip_font_size_spinbox, "System default")
        self._tooltip_font_size_spinbox.valueChanged.connect(self._on_tooltip_font_size_changed)
        layout.addWidget(
            self._row(
                self._titled_control("Tooltip font size:", self._tooltip_font_size_spinbox),
                "Sets the point size used for every tooltip shown by the "
                "app; 0 uses the operating system's default size.",
            )
        )

        layout.addStretch(1)
        return tab

    def _build_filters_profiles_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        manage_filters_button = QPushButton("Manage Filtered Folders…")
        manage_filters_button.setToolTip("Open the Filtered Folders dialog")
        manage_filters_button.clicked.connect(self._on_manage_folder_filters_clicked)
        self._folder_filter_summary_label = QLabel()
        self._folder_filter_summary_label.setWordWrap(True)
        top = QWidget()
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addWidget(manage_filters_button)
        top_layout.addWidget(self._folder_filter_summary_label, 1)
        layout.addWidget(
            self._row(
                top,
                "Files under a folder matching any rule are hidden from "
                "every repo's change list.",
            )
        )

        manage_profiles_button = QPushButton("Manage Profiles…")
        manage_profiles_button.setToolTip("Open the Profiles dialog")
        manage_profiles_button.clicked.connect(self._on_manage_profiles_clicked)
        self._profiles_summary_label = QLabel()
        self._profiles_summary_label.setWordWrap(True)
        top = QWidget()
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.addWidget(manage_profiles_button)
        top_layout.addWidget(self._profiles_summary_label, 1)
        layout.addWidget(
            self._row(
                top,
                "A profile limits the tree to a named subset of "
                "repositories; the active profile is shown for every scan "
                "until switched back to 'No Profile'.",
            )
        )

        layout.addStretch(1)
        return tab

    def _build_diagnostics_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        self._log_level_combo = QComboBox()
        self._log_level_combo.addItems(_LOG_LEVELS)
        self._log_level_combo.currentTextChanged.connect(self._on_log_level_changed)
        layout.addWidget(
            self._row(
                self._titled_control("Log level:", self._log_level_combo),
                "Controls how much detail is written to the in-memory and "
                "on-disk app log; VERBOSE logs the most, ERROR the least.",
            )
        )

        layout.addStretch(1)
        return tab

    # -- population ---------------------------------------------------------

    def _load_current_state(self) -> None:
        mw = self._main_window
        self._show_ignored_checkbox.setChecked(mw._include_ignored_action.isChecked())
        self._show_unpushed_checkbox.setChecked(mw._include_unpushed_commits_action.isChecked())
        self._watch_file_changes_checkbox.setChecked(mw._use_file_watcher_action.isChecked())
        self._auto_refresh_spinbox.setValue(mw._settings.auto_refresh_minutes())

        self._ignore_md_checkbox.setChecked(mw._ignore_md_action.isChecked())
        self._hide_empty_repos_checkbox.setChecked(mw._hide_empty_repos_action.isChecked())
        self._hide_changeless_worktrees_checkbox.setChecked(
            mw._hide_changeless_worktrees_checkbox.isChecked()
        )
        self._ignore_whitespace_checkbox.setChecked(mw._ignore_whitespace_action.isChecked())
        self._always_reload_diff_checkbox.setChecked(mw._always_reload_diff_action.isChecked())
        self._sync_scroll_checkbox.setChecked(mw._sync_scroll_action.isChecked())
        self._tooltip_font_size_spinbox.setValue(mw._settings.tooltip_font_size())

        self._refresh_folder_filter_summary()
        self._refresh_profiles_summary()

        current_log_level = mw._settings.log_level()
        if current_log_level in _LOG_LEVELS:
            self._log_level_combo.setCurrentText(current_log_level)

    def _refresh_folder_filter_summary(self) -> None:
        rules = self._main_window._folder_filter_rules
        if not rules:
            self._folder_filter_summary_label.setText("No filtered folders.")
            return
        rules_desc = ", ".join(f"{r.mode.value}:{r.text!r}" for r in rules)
        self._folder_filter_summary_label.setText(f"{len(rules)} rule(s): {rules_desc}")

    def _refresh_profiles_summary(self) -> None:
        mw = self._main_window
        if not mw._profiles:
            self._profiles_summary_label.setText("No profiles defined.")
            return
        active = mw._active_profile_name or "No Profile"
        self._profiles_summary_label.setText(f"{len(mw._profiles)} profile(s); active: {active}")

    # -- handlers -------------------------------------------------------

    def _on_show_ignored_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        self._main_window._include_ignored_action.setChecked(checked)

    def _on_show_unpushed_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        self._main_window._include_unpushed_commits_action.setChecked(checked)

    def _on_watch_file_changes_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        self._main_window._use_file_watcher_action.setChecked(checked)

    def _on_auto_refresh_changed(self, minutes: int) -> None:
        if self._loading:
            return
        self._main_window._set_auto_refresh_minutes(minutes)

    def _on_ignore_md_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        self._main_window._ignore_md_action.setChecked(checked)

    def _on_hide_empty_repos_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        self._main_window._hide_empty_repos_action.setChecked(checked)

    def _on_hide_changeless_worktrees_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        self._main_window._hide_changeless_worktrees_checkbox.setChecked(checked)

    def _on_ignore_whitespace_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        self._main_window._ignore_whitespace_action.setChecked(checked)

    def _on_always_reload_diff_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        self._main_window._always_reload_diff_action.setChecked(checked)

    def _on_sync_scroll_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        self._main_window._sync_scroll_action.setChecked(checked)

    def _on_tooltip_font_size_changed(self, size: int) -> None:
        if self._loading:
            return
        self._main_window._set_tooltip_font_size(size)

    def _on_log_level_changed(self, level_name: str) -> None:
        if self._loading:
            return
        self._main_window._set_log_level(level_name)

    def _on_manage_folder_filters_clicked(self) -> None:
        # _on_manage_folder_filters() opens FolderFilterDialog modally
        # (dialog.exec()), so by the time this call returns the sub-dialog
        # has already closed and MainWindow's rules are up to date.
        self._main_window._on_manage_folder_filters()
        self._refresh_folder_filter_summary()

    def _on_manage_profiles_clicked(self) -> None:
        self._main_window._on_manage_profiles()
        self._refresh_profiles_summary()
