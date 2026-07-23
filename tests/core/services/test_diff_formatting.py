from local_changes_viewer.core.domain.diff import DiffHunk, DiffLine, DiffLineKind, DiffResult
from local_changes_viewer.core.services.diff_formatting import format_unified_diff


def test_formats_hunks_with_correct_prefixes() -> None:
    hunk = DiffHunk(
        old_start=1,
        old_count=2,
        new_start=1,
        new_count=2,
        lines=[
            DiffLine(DiffLineKind.CONTEXT, 1, 1, "same"),
            DiffLine(DiffLineKind.REMOVED, 2, None, "old"),
            DiffLine(DiffLineKind.ADDED, None, 2, "new"),
        ],
    )
    diff = DiffResult(old_ref="HEAD", new_ref="working tree", hunks=[hunk])

    text = format_unified_diff(diff)

    assert text == "@@ -1,2 +1,2 @@\n same\n-old\n+new"


def test_includes_file_header_when_file_path_given() -> None:
    diff = DiffResult(old_ref="HEAD", new_ref="working tree", hunks=[])

    text = format_unified_diff(diff, file_path="src/foo.py")

    assert text == "--- a/src/foo.py\n+++ b/src/foo.py"
