import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from local_changes_viewer.gui.bulk_delete_worktrees_dialog import BulkDeleteWorktreesDialog
from tests.gui.test_worktrees_dialog import DeferredPool, FakeAdapter, ImmediatePool, _info


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _make_bulk_dialog(tmp_path: Path, fake, worktrees, *, thread_pool=None):
    return BulkDeleteWorktreesDialog(
        tmp_path,
        worktrees,
        adapter_factory=lambda p: fake,
        thread_pool=thread_pool if thread_pool is not None else ImmediatePool(),
    )


def test_default_check_state_unmodified_checked_unpushed_unchecked(qapp, tmp_path: Path) -> None:
    clean = _info(tmp_path / "wt" / "clean")
    dirty = _info(tmp_path / "wt" / "dirty", has_unpushed_changes=True)
    fake = FakeAdapter(tmp_path)

    dialog = _make_bulk_dialog(tmp_path, fake, [clean, dirty])

    assert dialog._list.count() == 2
    assert dialog._list.item(0).checkState() == Qt.CheckState.Checked
    assert dialog._list.item(1).checkState() == Qt.CheckState.Unchecked


def test_every_worktree_is_listed_not_just_unmodified(qapp, tmp_path: Path) -> None:
    clean = _info(tmp_path / "wt" / "clean")
    dirty_a = _info(tmp_path / "wt" / "dirty-a", has_unpushed_changes=True)
    dirty_b = _info(tmp_path / "wt" / "dirty-b", has_unpushed_changes=True)
    fake = FakeAdapter(tmp_path)

    dialog = _make_bulk_dialog(tmp_path, fake, [clean, dirty_a, dirty_b])

    assert dialog._list.count() == 3
    listed_paths = [dialog._list.item(i).text() for i in range(3)]
    assert any(str(clean.path) in text for text in listed_paths)
    assert any(str(dirty_a.path) in text for text in listed_paths)
    assert any(str(dirty_b.path) in text for text in listed_paths)


def test_unpushed_row_text_marked_with_warning_suffix(qapp, tmp_path: Path) -> None:
    dirty = _info(tmp_path / "wt" / "dirty", has_unpushed_changes=True)
    fake = FakeAdapter(tmp_path)

    dialog = _make_bulk_dialog(tmp_path, fake, [dirty])

    assert "has unpushed changes" in dialog._list.item(0).text()


def test_select_all_and_select_none_flip_every_row_and_count_label_tracks(
    qapp, tmp_path: Path
) -> None:
    clean = _info(tmp_path / "wt" / "clean")
    dirty = _info(tmp_path / "wt" / "dirty", has_unpushed_changes=True)
    fake = FakeAdapter(tmp_path)

    dialog = _make_bulk_dialog(tmp_path, fake, [clean, dirty])
    assert dialog._count_label.text() == "1 of 2 selected"

    dialog._select_all_button.click()
    assert dialog._count_label.text() == "2 of 2 selected"
    assert all(
        dialog._list.item(i).checkState() == Qt.CheckState.Checked
        for i in range(dialog._list.count())
    )

    dialog._select_none_button.click()
    assert dialog._count_label.text() == "0 of 2 selected"
    assert all(
        dialog._list.item(i).checkState() == Qt.CheckState.Unchecked
        for i in range(dialog._list.count())
    )


def test_delete_button_disabled_with_nothing_checked(qapp, tmp_path: Path) -> None:
    clean = _info(tmp_path / "wt" / "clean")
    fake = FakeAdapter(tmp_path)

    dialog = _make_bulk_dialog(tmp_path, fake, [clean])
    assert dialog._delete_button.isEnabled() is True

    dialog._select_none_button.click()
    assert dialog._delete_button.isEnabled() is False


def test_deleting_calls_remove_worktree_once_per_checked_path_only(
    qapp, tmp_path: Path
) -> None:
    clean_a = _info(tmp_path / "wt" / "clean-a")
    clean_b = _info(tmp_path / "wt" / "clean-b")
    dirty = _info(tmp_path / "wt" / "dirty", has_unpushed_changes=True)
    fake = FakeAdapter(tmp_path, details=[clean_a, clean_b, dirty])

    dialog = _make_bulk_dialog(tmp_path, fake, [clean_a, clean_b, dirty])
    dialog._delete_button.click()

    assert sorted(p for p, _ in fake.removed) == sorted([clean_a.path, clean_b.path])
    assert dirty.path not in [p for p, _ in fake.removed]
    assert sorted(dialog.deleted_paths) == sorted([clean_a.path, clean_b.path])
    assert dialog.result() == 1  # QDialog.DialogCode.Accepted


def test_progress_status_label_updates_per_item(qapp, tmp_path: Path) -> None:
    wt_a = _info(tmp_path / "wt" / "a")
    wt_b = _info(tmp_path / "wt" / "b")
    fake = FakeAdapter(tmp_path, details=[wt_a, wt_b])
    pool = DeferredPool()

    dialog = _make_bulk_dialog(tmp_path, fake, [wt_a, wt_b], thread_pool=pool)
    dialog._delete_button.click()

    assert len(pool.pending) == 1
    worker = pool.pending[0]
    seen: list[str] = []
    worker.signals.progress.connect(lambda i, t, p: seen.append(dialog._status_label.text()))
    pool.run_pending()

    assert seen[0] == f"Deleting 1 of 2: {wt_a.path.name} …"
    assert seen[1] == f"Deleting 2 of 2: {wt_b.path.name} …"


def test_one_failing_removal_does_not_abort_the_rest(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt_a = _info(tmp_path / "wt" / "a")
    wt_b = _info(tmp_path / "wt" / "b")
    fake = FakeAdapter(tmp_path, details=[wt_a, wt_b])
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))

    def _remove(path: Path, force: bool = False) -> None:
        if path == wt_a.path:
            raise RuntimeError("boom")
        fake.removed.append((path, force))

    fake.remove_worktree = _remove

    dialog = _make_bulk_dialog(tmp_path, fake, [wt_a, wt_b])
    dialog._delete_button.click()

    assert dialog.failed == [(wt_a.path, "boom")]
    assert dialog.deleted_paths == [wt_b.path]
    assert dialog.result() == 1  # still accepted -- summary already shown


def test_adapter_factory_failure_reports_every_path_failed_and_does_not_stick(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: adapter_factory(...) used to run outside the worker's
    try/except, so a construction failure (bad repo path, missing git
    binary) raised straight out of QRunnable.run(), `finished` never fired,
    and the dialog was left permanently stuck -- list and every button
    (including Cancel) disabled, status label frozen on "Deleting 1 of N …",
    with no way to close it short of killing the app."""
    wt_a = _info(tmp_path / "wt" / "a")
    wt_b = _info(tmp_path / "wt" / "b")
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))

    def _raising_factory(repo_path):
        raise RuntimeError("no such repo")

    dialog = BulkDeleteWorktreesDialog(
        tmp_path, [wt_a, wt_b], adapter_factory=_raising_factory, thread_pool=ImmediatePool()
    )
    dialog.show()

    dialog._delete_button.click()

    # (a) the dialog ends up accepted/closed rather than stuck.
    assert dialog.result() == 1  # QDialog.DialogCode.Accepted
    assert dialog.isVisible() is False
    # (b) every requested path is reported failed ...
    assert sorted(dialog.failed) == sorted(
        [(wt_a.path, "no such repo"), (wt_b.path, "no such repo")]
    )
    # (c) ... and nothing is reported deleted.
    assert dialog.deleted_paths == []
    # (d) the escape from the disabled state is the dialog closing itself
    # (accept()) once `finished` fires -- not the user having to click a
    # button that was left disabled forever.
    assert dialog._cancel_button.isEnabled() is False


def test_checking_unpushed_row_triggers_warning_confirmation_declining_cancels(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dirty = _info(tmp_path / "wt" / "dirty", has_unpushed_changes=True)
    fake = FakeAdapter(tmp_path, details=[dirty])
    warnings: list[tuple] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *a, **k: warnings.append(a) or QMessageBox.StandardButton.No),
    )

    dialog = _make_bulk_dialog(tmp_path, fake, [dirty])
    dialog._list.item(0).setCheckState(Qt.CheckState.Checked)
    dialog._delete_button.click()

    assert len(warnings) == 1
    assert str(dirty.path) in warnings[0][2]
    assert fake.removed == []
    assert dialog.result() == 0  # QDialog.DialogCode.Rejected (still open/not accepted)


def test_checking_unpushed_row_confirmation_accepted_deletes_it(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dirty = _info(tmp_path / "wt" / "dirty", has_unpushed_changes=True)
    fake = FakeAdapter(tmp_path, details=[dirty])
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )

    dialog = _make_bulk_dialog(tmp_path, fake, [dirty])
    dialog._list.item(0).setCheckState(Qt.CheckState.Checked)
    dialog._delete_button.click()

    assert fake.removed == [(dirty.path, False)]
    assert dialog.deleted_paths == [dirty.path]


def test_clean_only_selection_deletes_without_any_extra_confirmation(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean = _info(tmp_path / "wt" / "clean")
    fake = FakeAdapter(tmp_path, details=[clean])
    calls: list = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: calls.append(a))
    )

    dialog = _make_bulk_dialog(tmp_path, fake, [clean])
    dialog._delete_button.click()

    assert calls == []  # Delete click alone is the approval when nothing is dirty
    assert fake.removed == [(clean.path, False)]
