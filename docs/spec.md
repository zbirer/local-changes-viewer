# local-changes-viewer — Spec

## 1. Purpose

A desktop GUI tool (macOS, v1) that shows git changes across **all repositories** found under a
chosen root folder, similar to JetBrains WebStorm's "Local Changes" view. v1 is **view-only** —
no staging, committing, or discarding from the GUI.

## 2. Tech stack

| Concern              | Choice                                  |
|----------------------|------------------------------------------|
| GUI framework        | PySide6 (Qt for Python)                  |
| Git access           | GitPython                                |
| Platform (v1)        | macOS only                                |
| Distribution         | Standalone macOS app bundle (PyInstaller) |
| Diff syntax highlight| Pygments                                  |
| Settings storage     | Qt `QSettings` (native macOS prefs)       |
| Project layout       | `src/` layout with `pyproject.toml`       |
| Dependency mgmt       | `pyproject.toml` + venv + pip             |
| Concurrency           | QThread / QRunnable + Qt signals          |
| Testing              | pytest for `core` (logic); manual click-through for GUI |
| License              | None for v1                               |

Scale target: small workspaces (~10 repos, a few hundred changed files total) — informs the
lazy-diff-loading and async-scan decisions below, not heavy pagination/virtualization.

## 3. Architecture — layered, business logic isolated from low-level tooling

```
gui/            Qt widgets/windows. Imports ONLY core.domain and core.services.
                Never imports GitPython, os, pathlib for repo logic directly.

core/services/  Application/business-rule layer. The only layer allowed to call
                core.infra and construct core.domain objects.
                - WorkspaceScannerService
                - DiffService

core/domain/    Plain Python dataclasses. No GitPython/PySide/file-IO imports.
                The "business objects" — see section 4.

core/infra/     Thin low-level wrappers. No business rules here.
                - GitRepoAdapter   (wraps GitPython: status, diff, branch, ahead/behind)
                - FileSystemScanner (walks a root folder, finds .git directories)
```

Rule of thumb: if a class needs to import GitPython or touch the filesystem, it belongs in
`core/infra`. If a class encodes a business rule or decision (how to categorize a change,
when to compute a diff), it belongs in `core/services`. Everything passed between layers is a
`core/domain` object.

## 4. Domain model (business-level objects)

All are plain dataclasses, no I/O, no Qt, no GitPython.

- **`Workspace`** — root folder path + flat list of `Repository`.
  Nested repos (a repo inside another repo's working tree) are modeled as independent entries
  in this flat list — no parent/child linkage.

- **`Repository`** — path, name, current branch, `BranchStatus`, list of `FileChange`.

- **`BranchStatus`** — branch name, commits ahead, commits behind (vs. upstream).

- **`FileChange`** — file path, `ChangeType`, optional old path (for renames), optional cached
  `DiffResult`. The `DiffResult` is populated lazily — `None` until the GUI selects the file.

- **`ChangeType`** (enum) — `MODIFIED`, `ADDED`, `DELETED`, `RENAMED`, `UNTRACKED`, `IGNORED`.

- **`DiffResult`** — old ref/blob id, new ref/blob id, list of `DiffHunk`.

- **`DiffHunk`** — old_start, old_count, new_start, new_count, list of `DiffLine`.

- **`DiffLine`** — kind (`CONTEXT` / `ADDED` / `REMOVED`), old_lineno, new_lineno, text.
  One shared line model feeds both the side-by-side and the unified diff renderers — the GUI
  never needs two different diff data shapes.

### Application services

- **`WorkspaceScannerService`** — orchestrates `FileSystemScanner` + `GitRepoAdapter` to build a
  `Workspace`. Owns the business rules: change categorization, ignored-file filtering, nested-repo
  handling (flat list, no double-counting).

- **`DiffService`** — orchestrates `GitRepoAdapter` to compute a `DiffResult` for one
  `FileChange`, called lazily when the GUI selects that file (not upfront during scan).

## 5. v1 feature list

**Repo discovery & structure**
1. Recursively scan a root folder for git repos (detect `.git` dirs, arbitrary depth).
2. Tree view grouped by repo → directory → file.
3. Per-repo metadata: current branch, ahead/behind counts vs. upstream.
4. Manual refresh + optional auto-refresh (filesystem watcher / poll interval).
5. Collapse/expand all; remember expand state between sessions.

**Change categorization**
6. Group changes into Modified, Added, Deleted, Renamed, Untracked, Ignored.
7. Color-code files by change type.
8. Per-repo and per-folder file counts.
9. Filter/search box by filename or path substring.
10. Toggle "show ignored files" on/off.

**Diff viewer**
11. Side-by-side diff view.
12. Unified diff view.
13. Toggle between side-by-side and unified per file.
14. Syntax highlighting in diff (Pygments, based on file extension).
15. Line numbers on both sides, with change markers/gutter icons.
16. Word-level / character-level highlight within changed lines.
17. Collapse unchanged regions (context folding), expand-on-click.
18. Prev/Next change navigation buttons (jump between diff hunks).
19. Whitespace-ignore toggle.
20. Copy diff / copy file path to clipboard.

**Multi-repo aggregation**
21. Single unified "all changes across all repos" list.
22. Selecting a repo/folder node filters the diff list to that scope.
23. Summary bar/status showing total changed files across all repos.
24. Handle nested repos correctly without double-counting (flat `Workspace.repositories` list).

**File/version info**
25. Show old blob hash / commit ref for the "before" version.
26. Show file encoding, line-ending style (LF/CRLF) in status bar.
27. Click a file to open it in default editor / reveal in Finder.

**Performance/UX**
28. Async/background scanning (QThread/QRunnable) so the UI doesn't freeze on large folder trees.
29. Lazy-load diffs — only computed when a file is selected, via `DiffService`.
30. Remember last-opened root folder + window size/layout between launches (`QSettings`).

## 6. Out of scope for v1

- Staging, committing, discarding, or any git write operations.
- Non-macOS platforms.
- Automated GUI testing (manual click-through only; pytest covers `core` logic only).
