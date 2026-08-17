import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QModelIndex, QPoint, QPointF, Qt, QSettings
from PySide6.QtGui import QCursor, QMouseEvent
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


def _send_mouse_move(view: RepoTreeView, pos: QPoint) -> None:
    """Drives the view's real hover entry point (viewportEvent's MouseMove
    branch) rather than calling the private _update_row_actions_widget
    helper directly -- the whole point of these tests is to prove the
    overlay is now hover-driven, not selection-driven."""
    global_pos = view.viewport().mapToGlobal(pos)
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(pos),
        QPointF(global_pos),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(view.viewport(), event)


def _send_leave(view: RepoTreeView) -> None:
    QApplication.sendEvent(view.viewport(), QEvent(QEvent.Type.Leave))


def test_hovering_repo_root_row_shows_overlay(qapp, isolated_settings: Path) -> None:
    repo_path = Path("/repos/example-repo")
    view = _tree_view(isolated_settings)
    view.set_workspace(_workspace_with_one_repo(repo_path))

    repo_index = view.find_repo_index(repo_path)
    assert repo_index.isValid()
    rect = view.visualRect(repo_index)
    assert not rect.isEmpty()

    _send_mouse_move(view, rect.center())

    assert not view._row_actions_widget.isHidden()
    assert view._hovered_index == repo_index


def test_hovering_non_repo_root_row_hides_overlay(qapp, isolated_settings: Path) -> None:
    repo_path = Path("/repos/example-repo")
    view = _tree_view(isolated_settings)
    view.set_workspace(_workspace_with_one_repo(repo_path))

    repo_index = view.find_repo_index(repo_path)
    assert repo_index.isValid()
    _send_mouse_move(view, view.visualRect(repo_index).center())
    assert not view._row_actions_widget.isHidden()

    file_index = view._proxy.index(0, 0, repo_index)
    assert file_index.isValid()
    file_rect = view.visualRect(file_index)
    assert not file_rect.isEmpty()

    _send_mouse_move(view, file_rect.center())

    assert view._row_actions_widget.isHidden()


def test_current_changed_alone_does_not_show_overlay(qapp, isolated_settings: Path) -> None:
    """Selecting/clicking a repo-root row must not, by itself, reveal the
    overlay -- only hovering it does. This is the bug the hover feature
    fixes: previously currentChanged drove the overlay directly."""
    repo_path = Path("/repos/example-repo")
    view = _tree_view(isolated_settings)
    view.set_workspace(_workspace_with_one_repo(repo_path))

    repo_index = view.find_repo_index(repo_path)
    assert repo_index.isValid()

    view.setCurrentIndex(repo_index)

    assert view._row_actions_widget.isHidden()


def test_leaving_viewport_hides_overlay(
    qapp, isolated_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_path = Path("/repos/example-repo")
    view = _tree_view(isolated_settings)
    view.set_workspace(_workspace_with_one_repo(repo_path))

    repo_index = view.find_repo_index(repo_path)
    assert repo_index.isValid()
    _send_mouse_move(view, view.visualRect(repo_index).center())
    assert not view._row_actions_widget.isHidden()

    # Cursor is nowhere near the overlay -- comfortably outside its bounds.
    overlay_rect = view._row_actions_widget.geometry()
    outside_local = QPoint(overlay_rect.right() + 500, overlay_rect.bottom() + 500)
    outside_global = view.viewport().mapToGlobal(outside_local)
    monkeypatch.setattr(QCursor, "pos", staticmethod(lambda: outside_global))

    _send_leave(view)

    assert view._row_actions_widget.isHidden()
    assert not view._hovered_index.isValid()


def test_row_actions_overlay_has_green_chip_stylesheet(qapp, isolated_settings: Path) -> None:
    """The overlay must paint as an opaque green chip -- via a non-empty
    stylesheet naming the chosen green plus WA_StyledBackground actually
    enabled -- so the R/+/- buttons stay legible over both a blue-selected
    and a yellow-flashed row background (see the comment in __init__)."""
    view = _tree_view(isolated_settings)

    stylesheet = view._row_actions_widget.styleSheet()
    assert stylesheet
    assert "#2E7D32" in stylesheet
    assert view._row_actions_widget.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    # The chip must stay exactly as tall as the buttons it wraps: it is
    # centred on its row, so any extra height spills past the viewport's top
    # edge on the first visible row and the chip renders clipped.
    assert (
        view._row_actions_widget.sizeHint().height()
        == view._refresh_button.height()
    )


def test_hover_and_row_actions_indices_reset_on_workspace_rebuild(
    qapp, isolated_settings: Path
) -> None:
    """Reproduces the crash class this guards against: hover a repo row,
    then let a background refresh rebuild the model (set_workspace calls
    RepoTreeModel.clear(), update_workspace churns rows via removeRows/
    appendRow -- see tree_model.py's _sync_level) with the mouse never
    having moved. Both trackers must be invalidated as part of that
    rebuild, or a later dereference (visualRect()/.data() in
    _update_row_actions_widget, or a button handler) hits a QModelIndex
    whose QStandardItem the rebuild already deleted -- the "libshiboken
    ... already deleted" crash tree_model.py:70-74 warns about."""
    repo_path = Path("/repos/example-repo")
    view = _tree_view(isolated_settings)
    view.set_workspace(_workspace_with_one_repo(repo_path))

    repo_index = view.find_repo_index(repo_path)
    assert repo_index.isValid()
    view._update_row_actions_widget(repo_index)
    view._hovered_index = repo_index
    assert view._row_actions_index.isValid()
    assert not view._row_actions_widget.isHidden()

    view.set_workspace(_workspace_with_one_repo(repo_path))

    assert view._hovered_index == QModelIndex()
    assert view._row_actions_index == QModelIndex()
    assert view._row_actions_widget.isHidden()

    # A click landing right after the rebuild (before any new mouse-move
    # re-establishes hover) must be inert, not a dereference of a stale
    # index.
    received: list[Path] = []
    view.refresh_repo_requested.connect(received.append)
    view._on_row_refresh_clicked()
    assert received == []


def test_hover_and_row_actions_indices_reset_on_update_workspace(
    qapp, isolated_settings: Path
) -> None:
    """Same guard as above, for update_workspace's in-place row churn
    (RepoTreeModel._sync_level's removeRows/appendRow) rather than
    set_workspace's full clear()."""
    repo_path = Path("/repos/example-repo")
    view = _tree_view(isolated_settings)
    view.set_workspace(_workspace_with_one_repo(repo_path))

    repo_index = view.find_repo_index(repo_path)
    assert repo_index.isValid()
    view._update_row_actions_widget(repo_index)
    view._hovered_index = repo_index
    assert view._row_actions_index.isValid()

    # A different change set for the same repo forces _sync_level to drop
    # and rebuild that repo's child rows (the _CHANGE_SIGNATURE_ROLE
    # mismatch path at tree_model.py:107-110).
    other_repo = Repository(
        path=repo_path,
        name=repo_path.name,
        branch_status=_BRANCH,
        changes=[FileChange(path=Path("b.txt"), change_type=ChangeType.MODIFIED)],
    )
    view.update_workspace(Workspace(root_path=repo_path.parent, repositories=[other_repo]))

    assert view._hovered_index == QModelIndex()
    assert view._row_actions_index == QModelIndex()
    assert view._row_actions_widget.isHidden()


def test_overlay_stays_visible_when_cursor_over_overlay_widget(
    qapp, isolated_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The overlay sits on top of its row, so moving the mouse from the row
    onto the R/+/- buttons makes the viewport see a Leave event. The overlay
    must NOT hide in that case, or the buttons become unclickable."""
    repo_path = Path("/repos/example-repo")
    view = _tree_view(isolated_settings)
    view.set_workspace(_workspace_with_one_repo(repo_path))

    repo_index = view.find_repo_index(repo_path)
    assert repo_index.isValid()
    _send_mouse_move(view, view.visualRect(repo_index).center())
    assert not view._row_actions_widget.isHidden()

    overlay_center_local = view._row_actions_widget.geometry().center()
    overlay_center_global = view.viewport().mapToGlobal(overlay_center_local)
    monkeypatch.setattr(QCursor, "pos", staticmethod(lambda: overlay_center_global))

    _send_leave(view)

    assert not view._row_actions_widget.isHidden()
