import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
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


class ImmediatePool:
    """Runs a QRunnable synchronously in `start()`, standing in for QThreadPool.

    WorktreesDialog now loads worktree details on a background QThreadPool
    (see gui/workers/worktree_details_worker.py) instead of blocking the GUI
    thread. Every test below except the two that exercise the pending/error
    states cares only about the eventual result, so it swaps in this
    synchronous stand-in to keep behaving like the pre-worker, call-and-done
    dialog rather than depending on QThreadPool's real worker threads.
    """

    def start(self, runnable) -> None:
        runnable.run()


class DeferredPool:
    """Collects started QRunnables instead of running them, for pending-state tests."""

    def __init__(self) -> None:
        self.pending: list = []

    def start(self, runnable) -> None:
        self.pending.append(runnable)

    def run_pending(self) -> None:
        runnables, self.pending = self.pending, []
        for runnable in runnables:
            runnable.run()


def _make_dialog(tmp_path: Path, fake, *, thread_pool=None) -> WorktreesDialog:
    return WorktreesDialog(
        tmp_path,
        adapter_factory=lambda p: fake,
        thread_pool=thread_pool if thread_pool is not None else ImmediatePool(),
    )


def test_dialog_lists_worktrees_with_details(qapp, tmp_path: Path) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path, has_unpushed_changes=True)])

    dialog = _make_dialog(tmp_path, fake)

    assert dialog._table.rowCount() == 1
    assert dialog._table.item(0, 0).text() == str(wt_path)
    assert dialog._table.item(0, 1).text() == "feature-x"
    assert dialog._table.item(0, 3).text() == "Yes"


def test_dialog_shows_placeholder_when_no_worktrees(qapp, tmp_path: Path) -> None:
    fake = FakeAdapter(tmp_path, details=[])

    dialog = _make_dialog(tmp_path, fake)

    assert dialog._table.rowCount() == 1
    assert dialog._table.item(0, 0).text() == "No linked worktrees"


def test_reload_shows_busy_dialog_while_pending_then_populates_table(
    qapp, tmp_path: Path
) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path)])
    pool = DeferredPool()

    dialog = _make_dialog(tmp_path, fake, thread_pool=pool)

    # Worker hasn't run yet: table not populated, busy dialog up.
    assert dialog._table.rowCount() == 0
    assert dialog._busy_dialog is not None
    assert dialog._busy_dialog.isVisible()

    pool.run_pending()

    assert dialog._table.rowCount() == 1
    assert dialog._table.item(0, 0).text() == str(wt_path)
    assert dialog._busy_dialog is None


def test_reload_error_closes_busy_dialog_and_shows_warning(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeAdapter(tmp_path)

    def _raise() -> None:
        raise RuntimeError("git failed")

    fake.list_worktree_details = _raise
    pool = DeferredPool()
    warnings: list[tuple] = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a))
    )

    dialog = _make_dialog(tmp_path, fake, thread_pool=pool)
    pool.run_pending()

    assert dialog._busy_dialog is None
    assert len(warnings) == 1
    assert "git failed" in warnings[0][2]
    assert dialog._table.rowCount() == 1
    assert dialog._table.item(0, 0).text() == "No linked worktrees"


def test_post_delete_reload_also_goes_through_the_worker(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path)])
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    pool = DeferredPool()

    dialog = _make_dialog(tmp_path, fake, thread_pool=pool)
    pool.run_pending()  # initial load from __init__

    dialog._on_delete(_info(wt_path))

    # The post-delete _reload() started a second worker on the pool rather
    # than calling list_worktree_details() synchronously on the GUI thread.
    assert fake.removed == [(wt_path, False)]
    assert dialog._busy_dialog is not None
    assert len(pool.pending) == 1

    pool.run_pending()

    assert dialog._busy_dialog is None
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

    dialog = _make_dialog(tmp_path, fake)
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

    dialog = _make_dialog(tmp_path, fake)
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

    dialog = _make_dialog(tmp_path, fake)
    dialog._on_delete(_info(wt_path))

    assert fake.removed == [(wt_path, True)]
    assert dialog.deleted_any is True


def test_reload_tracks_worktree_for_each_row(qapp, tmp_path: Path) -> None:
    wt_a = tmp_path / "wt" / "a"
    wt_b = tmp_path / "wt" / "b"
    fake = FakeAdapter(tmp_path, details=[_info(wt_a), _info(wt_b)])

    dialog = _make_dialog(tmp_path, fake)

    assert [wt.path for wt in dialog._row_worktrees] == [wt_a, wt_b]


def test_context_menu_show_changes_opens_worktree_changes_dialog(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path)])
    dialog = _make_dialog(tmp_path, fake)

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


def test_double_click_row_opens_worktree_changes_dialog(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path)])
    dialog = _make_dialog(tmp_path, fake)

    opened: list[tuple[Path, object]] = []

    class FakeChangesDialog:
        def __init__(self, worktree_path, adapter_factory=None, parent=None):
            opened.append((worktree_path, adapter_factory))

        def exec(self):
            opened.append(("exec", None))

    monkeypatch.setattr(
        "local_changes_viewer.gui.worktrees_dialog.WorktreeChangesDialog", FakeChangesDialog
    )

    dialog._on_cell_double_clicked(0, 0)

    assert opened[0] == (wt_path, dialog._adapter_factory)
    assert opened[1][0] == "exec"


def test_double_click_ignores_click_outside_any_row(qapp, tmp_path: Path) -> None:
    fake = FakeAdapter(tmp_path, details=[])
    dialog = _make_dialog(tmp_path, fake)

    # Should not raise even though the placeholder row has no worktree.
    dialog._on_cell_double_clicked(0, 0)


def test_context_menu_copy_path_sets_clipboard_to_worktree_path(qapp, tmp_path: Path) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path)])
    dialog = _make_dialog(tmp_path, fake)

    dialog._on_copy_path(_info(wt_path))

    assert QGuiApplication.clipboard().text() == str(wt_path)


def test_clicking_header_sorts_table_and_toggles_order_on_repeat_click(
    qapp, tmp_path: Path
) -> None:
    wt_a = tmp_path / "wt" / "a"
    wt_b = tmp_path / "wt" / "b"
    fake = FakeAdapter(tmp_path, details=[_info(wt_b), _info(wt_a)])

    dialog = _make_dialog(tmp_path, fake)

    assert dialog._table.isSortingEnabled() is True

    dialog._table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
    assert [dialog._table.item(r, 0).text() for r in range(2)] == [str(wt_a), str(wt_b)]

    dialog._table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
    assert [dialog._table.item(r, 0).text() for r in range(2)] == [str(wt_b), str(wt_a)]


def test_row_lookup_follows_worktree_after_sorting(qapp, tmp_path: Path) -> None:
    wt_a = tmp_path / "wt" / "a"
    wt_b = tmp_path / "wt" / "b"
    fake = FakeAdapter(tmp_path, details=[_info(wt_b), _info(wt_a)])

    dialog = _make_dialog(tmp_path, fake)
    dialog._table.sortByColumn(0, Qt.SortOrder.AscendingOrder)

    assert dialog._worktree_at_row(0).path == wt_a
    assert dialog._worktree_at_row(1).path == wt_b


def test_context_menu_ignores_click_outside_any_row(qapp, tmp_path: Path) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path)])
    dialog = _make_dialog(tmp_path, fake)

    # Should not raise even though no row exists at this position.
    dialog._on_context_menu_requested(QPoint(0, 10_000))
