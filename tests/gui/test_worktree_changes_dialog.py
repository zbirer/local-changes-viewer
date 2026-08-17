import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from local_changes_viewer.core.domain.diff import DiffResult
from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.gui.worktree_changes_dialog import WorktreeChangesDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class FakeAdapter:
    def __init__(self, worktree_path: Path, changes=None, diff_error: Exception | None = None):
        self.worktree_path = worktree_path
        self._changes = changes if changes is not None else []
        self._diff_error = diff_error
        self.diffed: list[FileChange] = []

    def list_changes(self, include_unpushed_commits: bool = False):
        assert include_unpushed_commits is True
        return list(self._changes)

    def compute_diff(self, change: FileChange, ignore_whitespace: bool = False) -> DiffResult:
        if self._diff_error is not None:
            raise self._diff_error
        self.diffed.append(change)
        return DiffResult(old_ref="old", new_ref="new")


def test_dialog_lists_changes_with_committed_status(qapp, tmp_path: Path) -> None:
    changes = [
        FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED, is_unpushed_commit=True),
        FileChange(path=Path("b.py"), change_type=ChangeType.ADDED, is_unpushed_commit=False),
    ]
    fake = FakeAdapter(tmp_path, changes=changes)

    dialog = WorktreeChangesDialog(tmp_path, adapter_factory=lambda p: fake)

    assert dialog._file_list.count() == 2
    assert "[Committed]" in dialog._file_list.item(0).text()
    assert "a.py" in dialog._file_list.item(0).text()
    assert "[Not committed]" in dialog._file_list.item(1).text()
    assert "b.py" in dialog._file_list.item(1).text()


def test_dialog_filters_out_ignored_files(qapp, tmp_path: Path) -> None:
    changes = [
        FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED),
        FileChange(path=Path(".claude/settings.local.json"), change_type=ChangeType.IGNORED),
    ]
    fake = FakeAdapter(tmp_path, changes=changes)

    dialog = WorktreeChangesDialog(tmp_path, adapter_factory=lambda p: fake)

    assert dialog._file_list.count() == 1
    assert "a.py" in dialog._file_list.item(0).text()


def test_file_list_item_tooltip_shows_full_text(qapp, tmp_path: Path) -> None:
    changes = [FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)]
    fake = FakeAdapter(tmp_path, changes=changes)

    dialog = WorktreeChangesDialog(tmp_path, adapter_factory=lambda p: fake)

    item = dialog._file_list.item(0)
    assert item.toolTip() == item.text()


def test_dialog_shows_no_files_when_no_changes(qapp, tmp_path: Path) -> None:
    fake = FakeAdapter(tmp_path, changes=[])

    dialog = WorktreeChangesDialog(tmp_path, adapter_factory=lambda p: fake)

    assert dialog._file_list.count() == 0


def test_selecting_a_file_computes_diff(qapp, tmp_path: Path) -> None:
    change = FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)
    fake = FakeAdapter(tmp_path, changes=[change])

    dialog = WorktreeChangesDialog(tmp_path, adapter_factory=lambda p: fake)

    assert fake.diffed == [change]


def test_diff_view_starts_side_by_side(qapp, tmp_path: Path) -> None:
    change = FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)
    fake = FakeAdapter(tmp_path, changes=[change])

    dialog = WorktreeChangesDialog(tmp_path, adapter_factory=lambda p: fake)

    assert dialog._diff_stack.currentIndex() == 1
    assert dialog._diff_toggle_button.isChecked()
    assert dialog._diff_toggle_button.text() == "Unified"


def test_diff_toggle_switches_stack_index(qapp, tmp_path: Path) -> None:
    change = FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)
    fake = FakeAdapter(tmp_path, changes=[change])

    dialog = WorktreeChangesDialog(tmp_path, adapter_factory=lambda p: fake)

    dialog._diff_toggle_button.setChecked(False)
    assert dialog._diff_stack.currentIndex() == 0
    assert dialog._diff_toggle_button.text() == "Side-by-side"

    dialog._diff_toggle_button.setChecked(True)
    assert dialog._diff_stack.currentIndex() == 1
