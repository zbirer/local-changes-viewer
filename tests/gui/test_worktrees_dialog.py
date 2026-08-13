import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from local_changes_viewer.core.domain.worktree_info import WorktreeInfo
from local_changes_viewer.gui.worktrees_dialog import WorktreesDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class FakeAdapter:
    def __init__(self, repo_path: Path, details=None, remove_error: Exception | None = None):
        self.repo_path = repo_path
        self._details = details if details is not None else []
        self._remove_error = remove_error
        self.removed: list[tuple[Path, bool]] = []

    def list_worktree_details(self):
        return list(self._details)

    def remove_worktree(self, path: Path, force: bool = False) -> None:
        if self._remove_error is not None and not force:
            raise self._remove_error
        self.removed.append((path, force))
        self._details = [d for d in self._details if d.path != path]


def _info(path: Path, **overrides) -> WorktreeInfo:
    defaults = dict(
        path=path,
        branch_name="feature-x",
        last_activity=datetime(2026, 1, 1, 12, 0),
        has_unpushed_changes=False,
        created_at=datetime(2025, 12, 1, 9, 0),
    )
    defaults.update(overrides)
    return WorktreeInfo(**defaults)


def test_dialog_lists_worktrees_with_details(qapp, tmp_path: Path) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path, has_unpushed_changes=True)])

    dialog = WorktreesDialog(tmp_path, adapter_factory=lambda p: fake)

    assert dialog._table.rowCount() == 1
    assert dialog._table.item(0, 0).text() == str(wt_path)
    assert dialog._table.item(0, 1).text() == "feature-x"
    assert dialog._table.item(0, 3).text() == "Yes"


def test_dialog_shows_placeholder_when_no_worktrees(qapp, tmp_path: Path) -> None:
    fake = FakeAdapter(tmp_path, details=[])

    dialog = WorktreesDialog(tmp_path, adapter_factory=lambda p: fake)

    assert dialog._table.rowCount() == 1
    assert dialog._table.item(0, 0).text() == "No linked worktrees"


def test_delete_button_removes_worktree_after_confirmation(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path)])
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )

    dialog = WorktreesDialog(tmp_path, adapter_factory=lambda p: fake)
    dialog._on_delete(_info(wt_path))

    assert fake.removed == [(wt_path, False)]
    assert dialog.deleted_any is True
    assert dialog._table.rowCount() == 1
    assert dialog._table.item(0, 0).text() == "No linked worktrees"


def test_delete_button_does_nothing_when_confirmation_declined(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path)])
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
    )

    dialog = WorktreesDialog(tmp_path, adapter_factory=lambda p: fake)
    dialog._on_delete(_info(wt_path))

    assert fake.removed == []
    assert dialog.deleted_any is False


def test_delete_button_offers_force_delete_when_removal_fails(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(
        tmp_path, details=[_info(wt_path)], remove_error=RuntimeError("dirty worktree")
    )
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )

    dialog = WorktreesDialog(tmp_path, adapter_factory=lambda p: fake)
    dialog._on_delete(_info(wt_path))

    assert fake.removed == [(wt_path, True)]
    assert dialog.deleted_any is True


def test_reload_tracks_worktree_for_each_row(qapp, tmp_path: Path) -> None:
    wt_a = tmp_path / "wt" / "a"
    wt_b = tmp_path / "wt" / "b"
    fake = FakeAdapter(tmp_path, details=[_info(wt_a), _info(wt_b)])

    dialog = WorktreesDialog(tmp_path, adapter_factory=lambda p: fake)

    assert [wt.path for wt in dialog._row_worktrees] == [wt_a, wt_b]


def test_context_menu_show_changes_opens_worktree_changes_dialog(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path)])
    dialog = WorktreesDialog(tmp_path, adapter_factory=lambda p: fake)

    opened: list[tuple[Path, object]] = []

    class FakeChangesDialog:
        def __init__(self, worktree_path, adapter_factory=None, parent=None):
            opened.append((worktree_path, adapter_factory))

        def exec(self):
            opened.append(("exec", None))

    monkeypatch.setattr(
        "local_changes_viewer.gui.worktrees_dialog.WorktreeChangesDialog", FakeChangesDialog
    )

    dialog._on_show_changes(_info(wt_path))

    assert opened[0] == (wt_path, dialog._adapter_factory)
    assert opened[1][0] == "exec"


def test_context_menu_copy_path_sets_clipboard_to_worktree_path(qapp, tmp_path: Path) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path)])
    dialog = WorktreesDialog(tmp_path, adapter_factory=lambda p: fake)

    dialog._on_copy_path(_info(wt_path))

    assert QGuiApplication.clipboard().text() == str(wt_path)


def test_context_menu_ignores_click_outside_any_row(qapp, tmp_path: Path) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path)])
    dialog = WorktreesDialog(tmp_path, adapter_factory=lambda p: fake)

    # Should not raise even though no row exists at this position.
    dialog._on_context_menu_requested(QPoint(0, 10_000))
