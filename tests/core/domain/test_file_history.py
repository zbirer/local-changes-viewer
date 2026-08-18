from datetime import datetime, timezone
from pathlib import Path

from local_changes_viewer.core.domain.commit_log_entry import CommitLogEntry
from local_changes_viewer.core.domain.file_change import ChangeType
from local_changes_viewer.core.domain.file_history import (
    FileHistoryCommit,
    FileHistoryResult,
    TrackedFile,
    TrackedFilesResult,
)


def test_commit_log_entry_author_defaults_to_empty_string():
    entry = CommitLogEntry(
        hexsha="abc123",
        short_hexsha="abc123",
        message="msg",
        committed_datetime=datetime.now(timezone.utc),
    )
    assert entry.author == ""


def test_commit_log_entry_author_round_trips():
    entry = CommitLogEntry(
        hexsha="abc123",
        short_hexsha="abc123",
        message="msg",
        committed_datetime=datetime.now(timezone.utc),
        author="Jane Doe",
    )
    assert entry.author == "Jane Doe"


def test_tracked_file_round_trips():
    tracked = TrackedFile(path=Path("src/foo.py"), has_local_changes=True)
    assert tracked.path == Path("src/foo.py")
    assert tracked.has_local_changes is True


def test_tracked_files_result_defaults():
    result = TrackedFilesResult()
    assert result.files == []
    assert result.too_large is False


def test_tracked_files_result_too_large_round_trips():
    result = TrackedFilesResult(too_large=True)
    assert result.files == []
    assert result.too_large is True


def test_file_history_commit_round_trips():
    commit = CommitLogEntry(
        hexsha="abc123",
        short_hexsha="abc123",
        message="msg",
        committed_datetime=datetime.now(timezone.utc),
        author="Jane Doe",
    )
    entry = FileHistoryCommit(
        commit=commit,
        path_at_commit=Path("src/new_name.py"),
        change_type=ChangeType.RENAMED,
        renamed_from=Path("src/old_name.py"),
    )
    assert entry.commit is commit
    assert entry.path_at_commit == Path("src/new_name.py")
    assert entry.change_type is ChangeType.RENAMED
    assert entry.renamed_from == Path("src/old_name.py")


def test_file_history_commit_renamed_from_defaults_to_none():
    commit = CommitLogEntry(
        hexsha="abc123",
        short_hexsha="abc123",
        message="msg",
        committed_datetime=datetime.now(timezone.utc),
    )
    entry = FileHistoryCommit(
        commit=commit, path_at_commit=Path("src/foo.py"), change_type=ChangeType.MODIFIED
    )
    assert entry.renamed_from is None


def test_file_history_result_defaults_to_empty():
    result = FileHistoryResult()
    assert result.entries == []
    assert result.current_path is None


def test_file_history_result_holds_entries_and_current_path():
    commit = CommitLogEntry(
        hexsha="abc123",
        short_hexsha="abc123",
        message="msg",
        committed_datetime=datetime.now(timezone.utc),
    )
    entry = FileHistoryCommit(
        commit=commit, path_at_commit=Path("src/foo.py"), change_type=ChangeType.MODIFIED
    )
    result = FileHistoryResult(entries=[entry], current_path=Path("src/foo.py"))
    assert result.entries == [entry]
    assert result.current_path == Path("src/foo.py")
