import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFontMetrics, QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

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


def test_reload_shows_status_message_while_pending_then_populates_table(
    qapp, tmp_path: Path
) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path)])
    pool = DeferredPool()

    dialog = _make_dialog(tmp_path, fake, thread_pool=pool)

    # Worker hasn't run yet: table not populated, status message up and
    # disabled so a delete can't be triggered against stale rows.
    assert dialog._table.rowCount() == 0
    assert dialog._loading is True
    assert not dialog._status_label.isHidden()
    assert "reading" in dialog._status_label.text().lower()
    assert dialog._table.isEnabled() is False

    pool.run_pending()

    assert dialog._table.rowCount() == 1
    assert dialog._table.item(0, 0).text() == str(wt_path)
    assert dialog._loading is False
    assert dialog._status_label.isHidden()
    assert dialog._table.isEnabled() is True


def test_reload_error_hides_status_message_and_shows_warning(
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

    assert dialog._loading is False
    assert dialog._status_label.isHidden()
    assert dialog._table.isEnabled() is True
    assert len(warnings) == 1
    assert "git failed" in warnings[0][2]
    assert dialog._table.rowCount() == 1
    assert dialog._table.item(0, 0).text() == "No linked worktrees"


def test_delete_itself_runs_off_the_gui_thread_then_post_delete_reload_also_goes_through_the_worker(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: `_on_delete` used to call `adapter.remove_worktree()`
    -- a `git worktree remove` subprocess -- directly on the GUI thread, so a
    slow disk or network share froze the whole app with no busy indicator
    and no way to cancel. It now runs on the same thread_pool the dialog
    already uses for loading, disabling the table and showing a status
    message for the duration, exactly like `_reload()` does.
    """
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path)])
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    pool = DeferredPool()

    dialog = _make_dialog(tmp_path, fake, thread_pool=pool)
    pool.run_pending()  # initial load from __init__

    dialog._on_delete(_info(wt_path))

    # The delete worker is queued but hasn't run yet -- nothing removed, and
    # the dialog is already showing its busy state.
    assert fake.removed == []
    assert dialog._loading is True
    assert not dialog._status_label.isHidden()
    assert dialog._table.isEnabled() is False
    assert len(pool.pending) == 1

    pool.run_pending()  # runs the delete worker

    # Deletion is done; its `finished` handler immediately queued the
    # post-delete _reload() worker rather than calling
    # list_worktree_details() synchronously on the GUI thread.
    assert fake.removed == [(wt_path, False)]
    assert dialog._loading is True
    assert not dialog._status_label.isHidden()
    assert dialog._table.isEnabled() is False
    assert len(pool.pending) == 1

    pool.run_pending()  # runs the post-delete reload worker

    assert dialog._loading is False
    assert dialog._status_label.isHidden()
    assert dialog._table.isEnabled() is True
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


def test_force_delete_retry_also_runs_off_the_gui_thread(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(
        tmp_path, details=[_info(wt_path)], remove_error=RuntimeError("dirty worktree")
    )
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    pool = DeferredPool()

    dialog = _make_dialog(tmp_path, fake, thread_pool=pool)
    pool.run_pending()  # initial load from __init__

    dialog._on_delete(_info(wt_path))
    pool.run_pending()  # runs the non-forced delete worker -> raises -> force prompt -> queues retry

    assert fake.removed == []
    assert len(pool.pending) == 1  # the force=True retry worker
    assert dialog._loading is True
    assert dialog._table.isEnabled() is False

    pool.run_pending()  # runs the forced delete worker
    pool.run_pending()  # runs the post-delete reload worker

    assert fake.removed == [(wt_path, True)]
    assert dialog.deleted_any is True
    assert dialog._loading is False
    assert dialog._table.isEnabled() is True


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


def test_action_buttons_disabled_with_no_selection_enabled_after_selecting_row(
    qapp, tmp_path: Path
) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path)])
    dialog = _make_dialog(tmp_path, fake)

    # Freshly populated table has no current selection.
    assert dialog._delete_button.isEnabled() is False
    assert dialog._show_changes_button.isEnabled() is False
    assert dialog._copy_path_button.isEnabled() is False

    dialog._table.selectRow(0)

    assert dialog._delete_button.isEnabled() is True
    assert dialog._show_changes_button.isEnabled() is True
    assert dialog._copy_path_button.isEnabled() is True


def test_action_buttons_stay_disabled_when_placeholder_row_is_selected(
    qapp, tmp_path: Path
) -> None:
    fake = FakeAdapter(tmp_path, details=[])
    dialog = _make_dialog(tmp_path, fake)

    dialog._table.selectRow(0)  # the "No linked worktrees" placeholder row

    assert dialog._delete_button.isEnabled() is False
    assert dialog._show_changes_button.isEnabled() is False
    assert dialog._copy_path_button.isEnabled() is False


def test_action_buttons_disabled_while_loading_then_reenabled(qapp, tmp_path: Path) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path)])
    pool = DeferredPool()

    dialog = _make_dialog(tmp_path, fake, thread_pool=pool)

    # Still pending: no rows yet, buttons disabled regardless of selection.
    assert dialog._delete_button.isEnabled() is False
    assert dialog._show_changes_button.isEnabled() is False
    assert dialog._copy_path_button.isEnabled() is False

    pool.run_pending()
    dialog._table.selectRow(0)
    assert dialog._delete_button.isEnabled() is True

    # A post-delete reload should disable the buttons again while pending.
    dialog._loading = True
    dialog._update_action_buttons_enabled()
    assert dialog._delete_button.isEnabled() is False
    assert dialog._show_changes_button.isEnabled() is False
    assert dialog._copy_path_button.isEnabled() is False


def test_delete_button_invokes_same_effect_as_context_menu_delete(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path)])
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )

    dialog = _make_dialog(tmp_path, fake)
    dialog._table.selectRow(0)

    dialog._delete_button.click()

    assert fake.removed == [(wt_path, False)]
    assert dialog.deleted_any is True


def test_show_changes_button_invokes_same_effect_as_context_menu_show_changes(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path)])
    dialog = _make_dialog(tmp_path, fake)
    dialog._table.selectRow(0)

    opened: list[tuple[Path, object]] = []

    class FakeChangesDialog:
        def __init__(self, worktree_path, adapter_factory=None, parent=None):
            opened.append((worktree_path, adapter_factory))

        def exec(self):
            opened.append(("exec", None))

    monkeypatch.setattr(
        "local_changes_viewer.gui.worktrees_dialog.WorktreeChangesDialog", FakeChangesDialog
    )

    dialog._show_changes_button.click()

    assert opened[0] == (wt_path, dialog._adapter_factory)
    assert opened[1][0] == "exec"


def test_copy_path_button_invokes_same_effect_as_context_menu_copy_path(
    qapp, tmp_path: Path
) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path)])
    dialog = _make_dialog(tmp_path, fake)
    dialog._table.selectRow(0)

    dialog._copy_path_button.click()

    assert QGuiApplication.clipboard().text() == str(wt_path)


def test_dialog_width_fits_every_column_header_without_clipping(qapp, tmp_path: Path) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path, has_unpushed_changes=True)])

    dialog = _make_dialog(tmp_path, fake)

    header = dialog._table.horizontalHeader()
    metrics = QFontMetrics(header.font())
    for column, title in enumerate(
        ("Path", "Branch", "Last Commit / Modified", "Unpushed Changes", "Created")
    ):
        # Real width check: each column must be at least as wide as its own
        # header label text, or the label -- e.g. "Unpushed Changes" -- would
        # be visually clipped.
        assert dialog._table.columnWidth(column) >= metrics.horizontalAdvance(title)


def test_bulk_delete_button_exists_and_is_labeled(qapp, tmp_path: Path) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path)])
    dialog = _make_dialog(tmp_path, fake)

    assert dialog._bulk_delete_button.text() == "Delete Unmodified…"
    assert dialog._bulk_delete_button.isEnabled() is True


def test_bulk_delete_button_disabled_while_loading(qapp, tmp_path: Path) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path)])
    pool = DeferredPool()

    dialog = _make_dialog(tmp_path, fake, thread_pool=pool)

    assert dialog._bulk_delete_button.isEnabled() is False

    pool.run_pending()
    assert dialog._bulk_delete_button.isEnabled() is True


def test_bulk_delete_button_disabled_with_zero_worktrees(qapp, tmp_path: Path) -> None:
    fake = FakeAdapter(tmp_path, details=[])
    dialog = _make_dialog(tmp_path, fake)

    assert dialog._bulk_delete_button.isEnabled() is False


def test_bulk_delete_button_not_gated_on_row_selection(qapp, tmp_path: Path) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path)])
    dialog = _make_dialog(tmp_path, fake)

    # No row selected at all, unlike the other three action buttons.
    assert dialog._table.currentRow() < 0
    assert dialog._bulk_delete_button.isEnabled() is True


def test_bulk_delete_button_with_zero_worktrees_shows_information_and_does_not_open_dialog(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeAdapter(tmp_path, details=[])
    dialog = _make_dialog(tmp_path, fake)
    infos: list = []
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *a, **k: infos.append(a))
    )

    dialog._on_bulk_delete_button_clicked()

    assert len(infos) == 1


def test_bulk_delete_button_opens_bulk_dialog_and_reloads_on_deletions(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path)])
    dialog = _make_dialog(tmp_path, fake)

    opened: list = []

    class FakeBulkDialog:
        def __init__(self, repo_path, worktrees, adapter_factory, parent=None, thread_pool=None):
            opened.append((repo_path, worktrees, adapter_factory, thread_pool))
            self.deleted_paths = [wt_path]

        def exec(self):
            from PySide6.QtWidgets import QDialog

            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(
        "local_changes_viewer.gui.worktrees_dialog.BulkDeleteWorktreesDialog", FakeBulkDialog
    )
    reload_calls: list = []
    monkeypatch.setattr(dialog, "_reload", lambda: reload_calls.append(True))

    dialog._on_bulk_delete_button_clicked()

    assert opened[0][0] == tmp_path
    assert [wt.path for wt in opened[0][1]] == [wt_path]
    assert opened[0][2] == dialog._adapter_factory
    assert opened[0][3] is dialog._thread_pool
    assert dialog.deleted_any is True
    assert reload_calls == [True]


def test_bulk_delete_button_does_not_reload_when_nothing_was_deleted(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path)])
    dialog = _make_dialog(tmp_path, fake)

    class FakeBulkDialog:
        def __init__(self, repo_path, worktrees, adapter_factory, parent=None, thread_pool=None):
            self.deleted_paths = []

        def exec(self):
            from PySide6.QtWidgets import QDialog

            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(
        "local_changes_viewer.gui.worktrees_dialog.BulkDeleteWorktreesDialog", FakeBulkDialog
    )
    reload_calls: list = []
    monkeypatch.setattr(dialog, "_reload", lambda: reload_calls.append(True))

    dialog._on_bulk_delete_button_clicked()

    assert dialog.deleted_any is False
    assert reload_calls == []


def test_context_menu_delete_unmodified_routes_to_same_handler(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path)])
    dialog = _make_dialog(tmp_path, fake)

    calls: list = []
    monkeypatch.setattr(dialog, "_on_bulk_delete_button_clicked", lambda: calls.append(True))

    created_menus: list = []

    class FakeMenu:
        def __init__(self, parent=None):
            self.actions: list[tuple[str, object]] = []
            created_menus.append(self)

        def addAction(self, text, callback=None):
            self.actions.append((text, callback))

        def exec(self, position):
            pass

    monkeypatch.setattr("local_changes_viewer.gui.worktrees_dialog.QMenu", FakeMenu)

    dialog._on_context_menu_requested(QPoint(5, 5))

    assert len(created_menus) == 1
    action_texts = [text for text, _ in created_menus[0].actions]
    assert "Delete Unmodified…" in action_texts
    callback = dict(created_menus[0].actions)["Delete Unmodified…"]
    callback()

    assert calls == [True]


def test_dialog_width_never_exceeds_parent_window_width(qapp, tmp_path: Path) -> None:
    wt_path = tmp_path / "wt" / "feature-x"
    fake = FakeAdapter(tmp_path, details=[_info(wt_path, has_unpushed_changes=True)])
    parent = QWidget()
    parent.resize(240, 600)

    dialog = WorktreesDialog(
        tmp_path,
        adapter_factory=lambda p: fake,
        parent=parent,
        thread_pool=ImmediatePool(),
    )

    assert dialog.width() <= parent.width()
