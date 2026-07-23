"""Throwaway manual-verification helper: scan a real folder and print the Workspace.

Usage: python scripts/debug_scan.py /path/to/folder
"""
import sys
from pathlib import Path

from local_changes_viewer.core.services.workspace_scanner_service import (
    WorkspaceScannerService,
)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/debug_scan.py /path/to/folder")
        return 1

    root = Path(sys.argv[1]).expanduser().resolve()
    workspace = WorkspaceScannerService().scan(root)

    print(f"Workspace root: {workspace.root_path}")
    print(f"Repositories found: {len(workspace.repositories)}")
    for repo in workspace.repositories:
        branch = repo.branch_status
        print(
            f"\n- {repo.name}  [{branch.branch_name}, "
            f"+{branch.ahead}/-{branch.behind}]  ({repo.path})"
        )
        if not repo.changes:
            print("    (no changes)")
        for change in repo.changes:
            rename = f" <- {change.old_path}" if change.old_path else ""
            print(f"    {change.change_type.name:10s} {change.path}{rename}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
