import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

import local_changes_viewer.gui.settings as settings_module
from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.domain.repository import BranchStatus, Repository
from local_changes_viewer.core.domain.workspace import Workspace
from local_changes_viewer.gui.settings import AppSettings
from local_changes_viewer.gui.workspace_tree.tree_view import RepoTreeView

_BRANCH = BranchStatus(branch_name="main", ahead=0, behind=0)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirects AppSettings' QSettings to a throwaway ini file so building a
    RepoTreeView never reads or writes the developer's real preferences."""
    ini_path = tmp_path / "settings.ini"

    def _fake_qsettings(*_args, **_kwargs) -> QSettings:
        return QSettings(str(ini_path), QSettings.Format.IniFormat)

    monkeypatch.setattr(settings_module, "QSettings", _fake_qsettings)
    return ini_path


def _tree_view(isolated_settings: Path) -> RepoTreeView:
    return RepoTreeView(AppSettings())


def _workspace_with_one_repo(repo_path: Path) -> Workspace:
    repo = Repository(
        path=repo_path,
        name=repo_path.name,
        branch_status=_BRANCH,
        changes=[FileChange(path=Path("a.txt"), change_type=ChangeType.MODIFIED)],
    )
    return Workspace(root_path=repo_path.parent, repositories=[repo])


def test_refresh_button_sits_left_of_expand_and_collapse(qapp, isolated_settings: Path) -> None:
    """The row-actions overlay must lay out refresh, then expand, then
    collapse -- refresh leftmost -- so the new button doesn't disturb the
    existing right-margin positioning of + and -."""
    view = _tree_view(isolated_settings)

    layout = view._row_actions_widget.layout()
    assert layout.count() == 3
    assert layout.itemAt(0).widget() is view._refresh_button
    assert layout.itemAt(1).widget() is view._expand_button
    assert layout.itemAt(2).widget() is view._collapse_button

    assert view._refresh_button.text() == "R"
    assert view._refresh_button.toolTip() == "Refresh this repo"
    assert view._refresh_button.size().width() == 18
    assert view._refresh_button.size().height() == 18


def test_refresh_button_click_emits_signal_with_repo_path(qapp, isolated_settings: Path) -> None:
    """Clicking the refresh button must emit refresh_repo_requested with the
    repo-root's folder path, and must not touch the model/scanner directly --
    TreeView stays free of a dependency on the scanner service."""
    repo_path = Path("/repos/example-repo")
    view = _tree_view(isolated_settings)
    view.set_workspace(_workspace_with_one_repo(repo_path))

    index = view.find_repo_index(repo_path)
    assert index.isValid()

    view._update_row_actions_widget(index)
    assert view._row_actions_index.isValid()

    received: list[Path] = []
    view.refresh_repo_requested.connect(received.append)

    view._refresh_button.click()

    assert received == [repo_path]


def test_row_actions_overlay_shown_for_repo_root_hidden_otherwise(
    qapp, isolated_settings: Path
) -> None:
    """All three buttons live in one overlay widget, so verifying the overlay
    is shown/hidden verifies refresh's visibility along with + and -."""
    repo_path = Path("/repos/example-repo")
    view = _tree_view(isolated_settings)
    view.set_workspace(_workspace_with_one_repo(repo_path))

    # The view itself is never shown in this headless test, so isVisible()
    # (which also depends on the ancestor chain being shown) would always be
    # False; isHidden() reflects only this widget's own explicit show()/hide()
    # state, which is what _update_row_actions_widget actually toggles.
    repo_index = view.find_repo_index(repo_path)
    assert repo_index.isValid()
    view._update_row_actions_widget(repo_index)
    assert not view._row_actions_widget.isHidden()

    file_index = view._proxy.index(0, 0, repo_index)
    assert file_index.isValid()
    view._update_row_actions_widget(file_index)
    assert view._row_actions_widget.isHidden()
