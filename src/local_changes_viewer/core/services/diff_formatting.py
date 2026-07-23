from local_changes_viewer.core.domain.diff import DiffResult

_PREFIX_BY_KIND = {"ADDED": "+", "REMOVED": "-", "CONTEXT": " "}


def format_unified_diff(diff: DiffResult, file_path: str | None = None) -> str:
    lines: list[str] = []
    if file_path is not None:
        lines.append(f"--- a/{file_path}")
        lines.append(f"+++ b/{file_path}")

    for hunk in diff.hunks:
        lines.append(f"@@ -{hunk.old_start},{hunk.old_count} +{hunk.new_start},{hunk.new_count} @@")
        for line in hunk.lines:
            lines.append(f"{_PREFIX_BY_KIND[line.kind.name]}{line.text}")

    return "\n".join(lines)
