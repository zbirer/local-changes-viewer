import os
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import git
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from local_changes_viewer.core.domain.stash_entry import StashEntry
from local_changes_viewer.gui.stashes_dialog import StashesDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def _no_modal_information_popups(monkeypatch: pytest.MonkeyPatch) -> None:
    # A real QMessageBox.information()/.warning() call opens a genuine modal
    # event loop that never returns under the offscreen test platform (see
    # test_main_window.py's own comment on this exact problem) -- every
    # success path below (_on_apply/_on_pop) shows one, so it's stubbed out
    # here rather than per-test. Tests that care about `.question()`'s
    # Yes/No answer, or `.critical()`'s call count, override those two
    # specifically with their own monkeypatch.
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)


def _entries() -> list[StashEntry]:
    return [
        StashEntry(
            ref="stash@{0}",
            message="On main: second",
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            author="Ziv",
        ),
        StashEntry(
            ref="stash@{1}",
            message="On main: first",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            author="Ziv",
        ),
    ]


def _single_file_patch(path: str, old_line: str, new_line: str) -> str:
    """A minimal, real-shaped single-file unified diff -- enough for
    `PatchService.split_patch`/`GitRepoAdapter.parse_unified_diff` to parse
    into exactly one file with one changed line, without needing a real git
    repo on disk.
    """
    return (
        f"diff --git a/{path} b/{path}\n"
        "index 0000000..1111111 100644\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1 @@\n"
        f"-{old_line}\n"
        f"+{new_line}\n"
    )


def _multi_file_patch(*paths: str) -> str:
    return "".join(_single_file_patch(path, f"old {path}", f"new {path}") for path in paths)


class _FakeAdapter:
    """Stands in for GitRepoAdapter -- constructed as `adapter_factory(repo_path)`
    by StashesDialog, so tests never need a real git repo on disk.
    """

    def __init__(self, repo_path: Path, entries: list[StashEntry] | None = None) -> None:
        self.repo_path = repo_path
        self._entries = list(entries) if entries is not None else _entries()
        # Each stash defaults to a realistic single-file patch (distinct per
        # entry) rather than the old placeholder text, since the dialog now
        # parses this into a file list + per-file DiffResult, not a raw pane.
        self.diffs: dict[str, str] = {
            entry.ref: _single_file_patch(f"file{i}.txt", "old", "new")
            for i, entry in enumerate(self._entries)
        }
        self.applied: list[str] = []
        self.popped: list[str] = []
        self.raise_on_apply: Exception | None = None
        self.raise_on_pop: Exception | None = None

    def list_stashes(self) -> list[StashEntry]:
        return list(self._entries)

    def stash_diff(self, ref: str) -> str:
        return self.diffs[ref]

    def apply_stash(self, ref: str) -> None:
        if self.raise_on_apply is not None:
            raise self.raise_on_apply
        self.applied.append(ref)

    def pop_stash(self, ref: str) -> None:
        if self.raise_on_pop is not None:
            raise self.raise_on_pop
        self.popped.append(ref)
        self._entries = [e for e in self._entries if e.ref != ref]


def _make_factory(adapter: _FakeAdapter):
    return lambda repo_path: adapter


def _select_row(dialog: StashesDialog, row: int) -> None:
    dialog._table.selectRow(row)


def test_rows_render_with_ref_message_and_date(qapp) -> None:
    adapter = _FakeAdapter(Path("/repo"))
    dialog = StashesDialog(Path("/repo"), adapter_factory=_make_factory(adapter))

    assert dialog._table.rowCount() == 2
    assert dialog._table.item(0, 0).text() == "stash@{0}"
    assert dialog._table.item(0, 1).text() == "On main: second"
    assert dialog._table.item(1, 0).text() == "stash@{1}"


def test_buttons_disabled_with_no_selection(qapp) -> None:
    adapter = _FakeAdapter(Path("/repo"))
    dialog = StashesDialog(Path("/repo"), adapter_factory=_make_factory(adapter))

    assert not dialog._apply_button.isEnabled()
    assert not dialog._pop_button.isEnabled()


def test_selecting_a_row_loads_its_diff_and_enables_buttons(qapp) -> None:
    adapter = _FakeAdapter(Path("/repo"))
    dialog = StashesDialog(Path("/repo"), adapter_factory=_make_factory(adapter))

    _select_row(dialog, 0)

    # One file, auto-selected, rendered through the real side-by-side panes
    # (not a raw-text pane) -- see test_diff_view.py for the same
    # `_left`/`_right` assertion style.
    assert dialog._file_list.count() == 1
    assert dialog._file_list.item(0).text() == "[M] file0.txt"
    assert "old" in dialog._side_by_side._left.toPlainText()
    assert "new" in dialog._side_by_side._right.toPlainText()
    assert dialog._apply_button.isEnabled()
    assert dialog._pop_button.isEnabled()


def test_selecting_a_stash_with_multiple_files_lists_every_file_exactly_once(qapp) -> None:
    adapter = _FakeAdapter(Path("/repo"))
    adapter.diffs["stash@{1}"] = _multi_file_patch("one.txt", "two.txt", "three.txt")
    dialog = StashesDialog(Path("/repo"), adapter_factory=_make_factory(adapter))

    _select_row(dialog, 1)

    labels = [dialog._file_list.item(i).text() for i in range(dialog._file_list.count())]
    assert labels == ["[M] one.txt", "[M] three.txt", "[M] two.txt"]
    assert len(labels) == len(set(labels))


def test_selecting_a_second_file_swaps_the_side_by_side_content(qapp) -> None:
    adapter = _FakeAdapter(Path("/repo"))
    adapter.diffs["stash@{0}"] = _multi_file_patch("alpha.txt", "beta.txt")
    dialog = StashesDialog(Path("/repo"), adapter_factory=_make_factory(adapter))

    _select_row(dialog, 0)

    # First file auto-selected.
    assert dialog._file_list.currentRow() == 0
    assert "new alpha.txt" in dialog._side_by_side._right.toPlainText()
    assert "new beta.txt" not in dialog._side_by_side._right.toPlainText()

    dialog._file_list.setCurrentRow(1)

    assert "new beta.txt" in dialog._side_by_side._right.toPlainText()
    assert "new alpha.txt" not in dialog._side_by_side._right.toPlainText()


def test_stash_with_no_changed_files_shows_a_message_not_a_blank_pane(qapp) -> None:
    adapter = _FakeAdapter(Path("/repo"))
    adapter.diffs["stash@{0}"] = ""
    dialog = StashesDialog(Path("/repo"), adapter_factory=_make_factory(adapter))

    _select_row(dialog, 0)

    assert dialog._file_list.count() == 0
    # The dialog is never shown in this test (no .show()/.exec()), so
    # isVisible() would be False regardless -- isHidden() reflects each
    # widget's own explicit setVisible() call instead, same reasoning as
    # test_empty_state_shows_message_and_disables_buttons above.
    assert not dialog._file_status_label.isHidden()
    assert dialog._diff_splitter.isHidden()


def test_stash_diff_failure_shows_error_instead_of_raising(qapp) -> None:
    adapter = _FakeAdapter(Path("/repo"))

    def _raise(_ref: str) -> str:
        raise git.GitCommandError(["git", "stash", "show"], 1, "boom")

    adapter.stash_diff = _raise
    dialog = StashesDialog(Path("/repo"), adapter_factory=_make_factory(adapter))

    _select_row(dialog, 0)

    assert dialog._file_list.count() == 0
    assert not dialog._file_status_label.isHidden()
    assert "Failed to load diff" in dialog._file_status_label.text()
    assert dialog._diff_splitter.isHidden()


def test_apply_calls_the_injected_adapter_with_the_selected_ref(qapp) -> None:
    adapter = _FakeAdapter(Path("/repo"))
    dialog = StashesDialog(Path("/repo"), adapter_factory=_make_factory(adapter))
    _select_row(dialog, 1)

    dialog._apply_button.click()

    assert adapter.applied == ["stash@{1}"]
    assert dialog.restored is True


def test_pop_confirms_before_deleting_the_stash_entry(qapp, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _FakeAdapter(Path("/repo"))
    dialog = StashesDialog(Path("/repo"), adapter_factory=_make_factory(adapter))
    _select_row(dialog, 0)

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    dialog._pop_button.click()
    assert adapter.popped == []
    assert dialog.restored is False

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    dialog._pop_button.click()
    assert adapter.popped == ["stash@{0}"]
    assert dialog.restored is True
    assert dialog._table.rowCount() == 1


def test_apply_failure_shows_critical_and_does_not_mark_restored(
    qapp, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _FakeAdapter(Path("/repo"))
    adapter.raise_on_apply = git.GitCommandError(["git", "stash", "apply"], 1, "conflict!")
    dialog = StashesDialog(Path("/repo"), adapter_factory=_make_factory(adapter))
    _select_row(dialog, 0)

    critical_calls: list = []
    monkeypatch.setattr(
        QMessageBox, "critical", lambda *a, **k: critical_calls.append(a) or QMessageBox.StandardButton.Ok
    )

    dialog._apply_button.click()

    assert len(critical_calls) == 1
    assert dialog.restored is False


def test_empty_state_shows_message_and_disables_buttons(qapp) -> None:
    adapter = _FakeAdapter(Path("/repo"), entries=[])
    dialog = StashesDialog(Path("/repo"), adapter_factory=_make_factory(adapter))

    # The dialog itself is never shown in this test (no .show()/.exec()), so
    # isVisible() (which also requires every ancestor to be shown) would be
    # False either way -- isHidden() reflects each widget's own explicit
    # setVisible() call regardless of the top-level window's shown state.
    assert not dialog._empty_label.isHidden()
    assert dialog._table.isHidden()
    assert not dialog._apply_button.isEnabled()
    assert not dialog._pop_button.isEnabled()
