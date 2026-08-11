import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

import local_changes_viewer.gui.settings as settings_module
from local_changes_viewer.core.domain.folder_filter_rule import FolderFilterMode, FolderFilterRule
from local_changes_viewer.core.domain.profile import Profile
from local_changes_viewer.gui.main_window import MainWindow
from local_changes_viewer.gui.settings_dialog import SettingsDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirects AppSettings' QSettings to a throwaway ini file, mirroring
    test_main_window.py's fixture of the same name, so constructing a
    MainWindow/SettingsDialog never touches the developer's real settings."""
    ini_path = tmp_path / "settings.ini"

    def _fake_qsettings(*_args, **_kwargs) -> QSettings:
        return QSettings(str(ini_path), QSettings.Format.IniFormat)

    monkeypatch.setattr(settings_module, "QSettings", _fake_qsettings)
    return ini_path


@pytest.fixture
def window(qapp, isolated_settings: Path):
    win = MainWindow()
    yield win
    win.close()


def test_dialog_reflects_current_action_states(qapp, window: MainWindow) -> None:
    window._ignore_md_action.setChecked(True)
    window._hide_empty_repos_action.setChecked(False)
    window._include_ignored_action.setChecked(True)

    dialog = SettingsDialog(window)
    try:
        assert dialog._ignore_md_checkbox.isChecked() is True
        assert dialog._hide_empty_repos_checkbox.isChecked() is False
        assert dialog._show_ignored_checkbox.isChecked() is True
    finally:
        dialog.close()


def test_toggling_ignore_md_checkbox_flips_action_and_refreshes_display(
    qapp, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    window._ignore_md_action.setChecked(False)
    refresh_calls: list[tuple] = []
    monkeypatch.setattr(
        MainWindow,
        "_refresh_display",
        lambda self, *args, **kwargs: refresh_calls.append((args, kwargs)),
    )

    dialog = SettingsDialog(window)
    try:
        assert refresh_calls == []  # constructing/populating must not mutate anything
        dialog._ignore_md_checkbox.setChecked(True)
        assert window._ignore_md_action.isChecked() is True
        assert len(refresh_calls) == 1
    finally:
        dialog.close()


def test_toggling_watch_file_changes_checkbox_flips_action_and_persists_setting(
    qapp, window: MainWindow
) -> None:
    window._use_file_watcher_action.setChecked(False)

    dialog = SettingsDialog(window)
    try:
        dialog._watch_file_changes_checkbox.setChecked(True)
        assert window._use_file_watcher_action.isChecked() is True
        assert window._settings.use_file_watcher() is True
    finally:
        dialog.close()


def test_auto_refresh_spinbox_persists_and_applies_interval(qapp, window: MainWindow) -> None:
    dialog = SettingsDialog(window)
    try:
        dialog._auto_refresh_spinbox.setValue(15)
        assert window._settings.auto_refresh_minutes() == 15
        assert window._auto_refresh_minutes == 15
        assert window._auto_refresh_timer.isActive() is True
    finally:
        dialog.close()


def test_tooltip_font_size_spinbox_persists_value(qapp, window: MainWindow) -> None:
    dialog = SettingsDialog(window)
    try:
        dialog._tooltip_font_size_spinbox.setValue(14)
        assert window._settings.tooltip_font_size() == 14
    finally:
        dialog.close()


def test_log_level_combo_persists_value(qapp, window: MainWindow) -> None:
    dialog = SettingsDialog(window)
    try:
        dialog._log_level_combo.setCurrentText("DEBUG")
        assert window._settings.log_level() == "DEBUG"
    finally:
        dialog.close()


def test_constructing_dialog_does_not_mutate_any_setting(qapp, window: MainWindow) -> None:
    window._settings.set_auto_refresh_minutes(7)
    window._settings.set_tooltip_font_size(11)
    window._settings.set_log_level("WARNING")
    window._settings.set_ignore_md_files(True)
    window._ignore_md_action.setChecked(True)

    dialog = SettingsDialog(window)
    try:
        # Population must reflect state, not re-derive/rewrite it.
        assert window._settings.auto_refresh_minutes() == 7
        assert window._settings.tooltip_font_size() == 11
        assert window._settings.log_level() == "WARNING"
        assert window._settings.ignore_md_files() is True
        assert dialog._auto_refresh_spinbox.value() == 7
        assert dialog._tooltip_font_size_spinbox.value() == 11
        assert dialog._log_level_combo.currentText() == "WARNING"
        assert dialog._ignore_md_checkbox.isChecked() is True
    finally:
        dialog.close()


def test_population_never_rewrites_out_of_range_or_unlisted_persisted_values(
    qapp, window: MainWindow
) -> None:
    """Regression test for the bug where a value a control's range/list
    didn't (yet) represent -- VERBOSE was missing from the log-level combo,
    and 36 was outside the tooltip-font-size spinbox's old 0-32 range --
    got silently narrowed by population and then rewritten to the
    narrowed value by this instant-apply dialog. Both controls must now
    display the real persisted value, and population must never write
    back regardless."""
    window._settings.set_log_level("VERBOSE")
    window._settings.set_tooltip_font_size(36)

    dialog = SettingsDialog(window)
    try:
        assert dialog._log_level_combo.currentText() == "VERBOSE"
        assert dialog._tooltip_font_size_spinbox.value() == 36
        assert window._settings.log_level() == "VERBOSE"
        assert window._settings.tooltip_font_size() == 36
    finally:
        dialog.close()


def test_folder_filter_summary_reflects_current_rules(qapp, window: MainWindow) -> None:
    window._folder_filter_rules = [
        FolderFilterRule(text="build", mode=FolderFilterMode.CONTAINS),
        FolderFilterRule(text="docs", mode=FolderFilterMode.EQUALS),
    ]

    dialog = SettingsDialog(window)
    try:
        text = dialog._folder_filter_summary_label.text()
        assert "2 rule(s)" in text
        assert "contains:'build'" in text
        assert "equals:'docs'" in text
    finally:
        dialog.close()


def test_manage_folder_filters_button_refreshes_summary_after_dialog_closes(
    qapp, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fake_manage_folder_filters(self) -> None:
        # Simulate the sub-dialog mutating the rules and closing.
        self._folder_filter_rules = [FolderFilterRule(text="build", mode=FolderFilterMode.CONTAINS)]

    monkeypatch.setattr(MainWindow, "_on_manage_folder_filters", _fake_manage_folder_filters)

    dialog = SettingsDialog(window)
    try:
        assert dialog._folder_filter_summary_label.text() == "No filtered folders."
        dialog._on_manage_folder_filters_clicked()
        assert "1 rule(s)" in dialog._folder_filter_summary_label.text()
    finally:
        dialog.close()


def test_profiles_summary_reflects_current_profiles_and_active_profile(
    qapp, window: MainWindow
) -> None:
    window._profiles = [Profile(name="Frontend", repo_names=["a"]), Profile(name="Backend")]
    window._active_profile_name = "Frontend"

    dialog = SettingsDialog(window)
    try:
        text = dialog._profiles_summary_label.text()
        assert "2 profile(s)" in text
        assert "Frontend" in text
    finally:
        dialog.close()


def test_manage_profiles_button_refreshes_summary_after_dialog_closes(
    qapp, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fake_manage_profiles(self) -> None:
        self._profiles = [Profile(name="Frontend")]
        self._active_profile_name = "Frontend"

    monkeypatch.setattr(MainWindow, "_on_manage_profiles", _fake_manage_profiles)

    dialog = SettingsDialog(window)
    try:
        assert dialog._profiles_summary_label.text() == "No profiles defined."
        dialog._on_manage_profiles_clicked()
        text = dialog._profiles_summary_label.text()
        assert "1 profile(s)" in text
        assert "Frontend" in text
    finally:
        dialog.close()


def test_view_menu_has_settings_action_that_opens_dialog(
    qapp, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Reference-hold each actions() list explicitly: filtering it inline in
    # a generator expression lets PySide6 garbage-collect the Python
    # wrapper for a not-yet-matched QAction, which for a submenu action
    # can delete the underlying QMenu it owns before we reach it.
    menu_bar_actions = list(window.menuBar().actions())
    view_action = next(action for action in menu_bar_actions if action.text() == "View")
    view_menu = view_action.menu()
    view_menu_actions = list(view_menu.actions())
    settings_action = next(
        action for action in view_menu_actions if action.text() == "Settings…"
    )

    opened: list[SettingsDialog] = []
    monkeypatch.setattr(SettingsDialog, "exec", lambda self: (opened.append(self), None)[1])

    settings_action.trigger()

    assert len(opened) == 1
    assert isinstance(opened[0], SettingsDialog)
    opened[0].deleteLater()
