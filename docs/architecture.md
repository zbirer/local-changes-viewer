# local-changes-viewer — Architecture & Design

Companion to `docs/spec.md` (product spec/feature list). This document covers module layout,
class responsibilities, threading model, and data flow.

## 1. Layering rule

```
gui/            ──▶  core/services/  ──▶  core/domain/
                                     ──▶  core/infra/  ──▶  core/domain/
```

- `gui/` may import `core.domain` and `core.services`. Never `core.infra`, never GitPython,
  never raw filesystem calls for repo/diff logic.
- `core/services/` may import `core.infra` and `core.domain`. This is the only place business
  rules live.
- `core/infra/` may import `core.domain` (to return domain objects) but never `core.services`.
- `core/domain/` imports nothing from the other three packages. Pure dataclasses/enums.

This keeps `core/domain` and `core/services` testable with plain pytest, no Qt, no real git
repos required (infra can be mocked/faked at the service boundary).

## 2. Package/module layout

```
local-changes-viewer/
├── pyproject.toml
├── docs/
│   ├── spec.md
│   └── architecture.md
├── src/
│   └── local_changes_viewer/
│       ├── __init__.py
│       ├── main.py                     # entry point: builds QApplication, MainWindow
│       │
│       ├── core/
│       │   ├── domain/
│       │   │   ├── workspace.py        # Workspace
│       │   │   ├── repository.py       # Repository, BranchStatus
│       │   │   ├── file_change.py      # FileChange, ChangeType
│       │   │   └── diff.py             # DiffResult, DiffHunk, DiffLine, DiffLineKind
│       │   │
│       │   ├── infra/
│       │   │   ├── filesystem_scanner.py   # FileSystemScanner
│       │   │   └── git_repo_adapter.py     # GitRepoAdapter
│       │   │
│       │   └── services/
│       │       ├── workspace_scanner_service.py  # WorkspaceScannerService
│       │       └── diff_service.py               # DiffService
│       │
│       └── gui/
│           ├── main_window.py          # MainWindow (top-level QMainWindow)
│           ├── workspace_tree/
│           │   ├── tree_model.py       # RepoTreeModel (QAbstractItemModel over Workspace)
│           │   └── tree_view.py        # RepoTreeView (QTreeView + filter/search box)
│           ├── diff_view/
│           │   ├── diff_view_widget.py # DiffViewWidget (container, toggle side-by-side/unified)
│           │   ├── side_by_side_view.py
│           │   ├── unified_view.py
│           │   └── syntax_highlighter.py  # Pygments-backed QSyntaxHighlighter
│           ├── workers/
│           │   ├── scan_worker.py      # QRunnable wrapping WorkspaceScannerService
│           │   └── diff_worker.py      # QRunnable wrapping DiffService
│           └── settings.py             # QSettings wrapper (last folder, window geometry, view mode)
│
└── tests/
    └── core/
        ├── domain/
        ├── infra/
        └── services/
```

## 3. Domain classes (`core/domain/`)

Dataclasses, immutable where practical (`frozen=True` unless mutation is needed for lazy fields).

```python
# workspace.py
@dataclass
class Workspace:
    root_path: Path
    repositories: list[Repository]

# repository.py
@dataclass
class BranchStatus:
    branch_name: str
    ahead: int
    behind: int

@dataclass
class Repository:
    path: Path
    name: str
    branch_status: BranchStatus
    changes: list[FileChange]

# file_change.py
class ChangeType(Enum):
    MODIFIED = auto()
    ADDED = auto()
    DELETED = auto()
    RENAMED = auto()
    UNTRACKED = auto()
    IGNORED = auto()

@dataclass
class FileChange:
    path: Path
    change_type: ChangeType
    old_path: Path | None = None      # set for RENAMED
    diff: DiffResult | None = None    # None until DiffService computes it (lazy)

# diff.py
class DiffLineKind(Enum):
    CONTEXT = auto()
    ADDED = auto()
    REMOVED = auto()

@dataclass
class DiffLine:
    kind: DiffLineKind
    old_lineno: int | None
    new_lineno: int | None
    text: str

@dataclass
class DiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[DiffLine]

@dataclass
class DiffResult:
    old_ref: str
    new_ref: str
    hunks: list[DiffHunk]
```

`FileChange.diff` is the one mutable field in the domain model — it starts `None` and is filled
in-place by `DiffService` once, then cached for the life of the `Workspace` instance (until the
next rescan replaces the object).

## 4. Infra classes (`core/infra/`)

No business rules — just I/O wrappers returning domain objects or raw data.

- **`FileSystemScanner`**
  - `find_git_repos(root: Path) -> list[Path]` — walks `root`, returns every directory
    containing a `.git` entry, arbitrary depth, flat result (no nesting logic — that's a service
    concern if any filtering is needed).

- **`GitRepoAdapter`** (constructed per-repo, wraps a `git.Repo` from GitPython)
  - `get_branch_status() -> BranchStatus`
  - `list_changes() -> list[FileChange]` — status only (`ChangeType` + paths), no diffs
  - `compute_diff(file_change: FileChange, *, ignore_whitespace: bool) -> DiffResult`
  - `read_blob(ref: str, path: Path) -> str` — used for encoding/line-ending detection (feature 26)

## 5. Service classes (`core/services/`)

Business rules live here. These are the classes pytest exercises against fake/stub infra.

- **`WorkspaceScannerService`**
  - `scan(root: Path, *, include_ignored: bool) -> Workspace`
  - Uses `FileSystemScanner.find_git_repos`, then a `GitRepoAdapter` per repo path to build
    `Repository` objects via `list_changes()` + `get_branch_status()`.
  - Owns: ignored-file filtering (feature 10), nested-repo de-duplication rules (feature 24 —
    flat list, but if a "child" repo's own changes shouldn't also appear under the parent's
    working-tree scan, that exclusion rule is decided here, not in infra).

- **`DiffService`**
  - `load_diff(file_change: FileChange, repo_path: Path, *, ignore_whitespace: bool) -> DiffResult`
  - Calls `GitRepoAdapter.compute_diff`, sets `file_change.diff`, returns it. Called on-demand
    when the GUI selects a file (feature 29) — never during `WorkspaceScannerService.scan`.

## 6. GUI layer

- **`MainWindow`** — owns `RepoTreeView` (left) + `DiffViewWidget` (right), a `QSplitter`
  between them (mirrors the screenshot layout), toolbar (refresh, view-mode toggle, whitespace
  toggle, search box), and status bar (summary counts, encoding/line-ending of selected file).

- **`RepoTreeModel`** — read-only `QAbstractItemModel` wrapping a `Workspace`. Tree levels:
  repo → directory → file. Provides color/icon per `ChangeType` (feature 7) and per-node file
  counts (feature 8).

- **`DiffViewWidget`** — holds both `SideBySideView` and `UnifiedView`; only one is visible at a
  time based on the toggle (feature 13). Both consume the same `DiffResult`/`DiffHunk`/`DiffLine`
  objects — no separate data transformation per view.

- **`SyntaxHighlighter`** — `QSyntaxHighlighter` subclass driven by Pygments lexers, applied per
  pane based on file extension.

- **Workers (`gui/workers/`)** — `QRunnable` subclasses that call into `core/services` on a
  `QThreadPool` thread, then emit a Qt `Signal` back to the main thread with the resulting domain
  object (`Workspace` or `DiffResult`). GUI code never blocks on `WorkspaceScannerService.scan`
  or `DiffService.load_diff` directly on the main thread.

- **`settings.py`** — thin wrapper over `QSettings` for: last root folder, window
  geometry/splitter sizes, last-used view mode (side-by-side vs unified), whitespace-ignore
  toggle state.

## 7. Data flow — two key sequences

**A. Opening a folder / refresh**
1. User picks a folder (or app launches with last-used folder from `settings.py`).
2. `MainWindow` submits a `ScanWorker(root_path)` to the `QThreadPool`.
3. `ScanWorker` runs `WorkspaceScannerService.scan(root_path)` off the main thread.
4. On completion, `ScanWorker` emits `workspace_ready(Workspace)`.
5. `MainWindow` (main thread) hands the `Workspace` to `RepoTreeModel`, tree view updates.
6. No diffs are computed yet — `FileChange.diff` is `None` for every entry.

**B. Selecting a file in the tree**
1. User clicks a file node in `RepoTreeView`.
2. `MainWindow` checks `file_change.diff` — if already cached (re-selecting a file this
   session), render immediately from the cached `DiffResult`.
3. If `None`, submit a `DiffWorker(file_change, repo_path)` to the thread pool.
4. `DiffWorker` runs `DiffService.load_diff(...)` off the main thread.
5. On completion, `DiffWorker` emits `diff_ready(FileChange)`.
6. `MainWindow` passes the `FileChange.diff` to `DiffViewWidget`, which renders into whichever
   of `SideBySideView`/`UnifiedView` is currently active.

## 8. Error handling strategy

- `core/infra` raises typed exceptions (e.g. `GitCommandError`, `RepoNotFoundError`) — it never
  swallows errors or returns partial/fake domain objects.
- `core/services` catches per-repo infra errors during `WorkspaceScannerService.scan` so one
  broken/corrupt repo doesn't abort the whole scan — it attaches an error marker to that
  `Repository` (or excludes it with a logged reason) rather than raising.
- `gui/workers` catch any exception from the service call and emit an `error(str)` signal
  instead of letting it cross the thread boundary; `MainWindow` surfaces it via a status-bar
  message or dialog — never a silent failure.

## 9. Testing strategy

- `tests/core/domain/` — trivial construction/equality tests for dataclasses.
- `tests/core/infra/` — exercised against small real throwaway git repos created in a pytest
  `tmp_path` fixture (init repo, make changes, assert `GitRepoAdapter` output) — not mocked,
  since this layer's whole job is correctly wrapping real git behavior.
- `tests/core/services/` — exercised against a fake/stub `GitRepoAdapter`/`FileSystemScanner`
  (dependency-injected) to test business rules (filtering, categorization) in isolation from
  real git.
- `gui/` — manual click-through per implementation step, no automated GUI tests in v1.
