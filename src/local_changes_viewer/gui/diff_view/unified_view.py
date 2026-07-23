from PySide6.QtGui import QFont
from PySide6.QtWidgets import QPlainTextEdit

from local_changes_viewer.core.domain.diff import DiffResult


class UnifiedView(QPlainTextEdit):
    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.setFont(QFont("Menlo", 12))

    def set_diff(self, diff: DiffResult) -> None:
        lines: list[str] = []
        for hunk in diff.hunks:
            lines.append(
                f"@@ -{hunk.old_start},{hunk.old_count} +{hunk.new_start},{hunk.new_count} @@"
            )
            for line in hunk.lines:
                prefix = {"ADDED": "+", "REMOVED": "-", "CONTEXT": " "}[line.kind.name]
                lines.append(f"{prefix}{line.text}")
        self.setPlainText("\n".join(lines) if lines else "(no changes)")

    def clear_diff(self) -> None:
        self.setPlainText("")
