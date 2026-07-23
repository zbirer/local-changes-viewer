# local-changes-viewer — Implementation Plan

Each step produces something you can run and manually verify before moving to the next. Steps
follow the layering in `docs/architecture.md` (domain → infra → services → GUI) and build up to
the full feature list in `docs/spec.md` §5.

Legend: **Build** = what gets written. **Verify** = what you do by hand to confirm it works.
Feature numbers refer to `docs/spec.md` §5.

---

## Phase 0 — Scaffolding

### Step 1 — Project skeleton
- **Build**: `pyproject.toml` (deps: PySide6, GitPython, Pygments, pytest), `src/` layout per
  architecture doc, empty `main.py` that opens a blank `QMainWindow` titled
  "local-changes-viewer".
- **Verify**: `pip install -e .`, run `python -m local_changes_viewer`, see an empty window open
  and close cleanly.

---

## Phase 1 — Domain & Infra (no GUI yet, pytest-only)

### Step 2 — Domain model
- **Build**: all dataclasses/enums in `core/domain/` (`Workspace`, `Repository`,
  `BranchStatus`, `FileChange`, `ChangeType`, `DiffResult`, `DiffHunk`, `DiffLine`,
  `DiffLineKind`).
- **Verify**: `pytest tests/core/domain` green — construction/equality tests only.

### Step 3 — Filesystem scanning
- **Build**: `FileSystemScanner.find_git_repos(root)`. *(Feature 1)*
- **Verify**: pytest against a `tmp_path` fixture with nested fake `.git` dirs at varying depths,
  including a nested-repo case (repo inside a repo) to confirm both are found *(Feature 24
  groundwork)*.

### Step 4 — Git status/branch reading
- **Build**: `GitRepoAdapter.list_changes()` and `get_branch_status()` using GitPython.
  *(Features 3, 6)*
- **Verify**: pytest creates a real throwaway git repo in `tmp_path` (init, commit, modify/add/
  delete/rename/untrack a file), asserts `list_changes()` returns correct `ChangeType`s and
  `get_branch_status()` returns correct ahead/behind after adding a fake remote-tracking branch.

### Step 5 — Workspace scanning service
- **Build**: `WorkspaceScannerService.scan(root, include_ignored=False)` combining Steps 3+4 into
  a `Workspace`. Ignored-file filtering rule. *(Features 6, 10, 24)*
- **Verify**: pytest with stub infra for business-rule tests, **plus** a small throwaway CLI
  script (`scripts/debug_scan.py`, not shipped) that runs `scan()` against a real folder you pass
  in and prints the resulting `Workspace` as text — point it at the folder from your screenshot
  and eyeball that repo names/branches/file counts look right.

---

## Phase 2 — GUI shell + repo tree (no diffs yet)

### Step 6 — Folder picker + persistence
- **Build**: `MainWindow` toolbar "Open Folder" action (native folder dialog), `settings.py`
  wrapper over `QSettings` storing last root folder. *(Feature 30, partial)*
- **Verify**: launch app, pick a folder, quit, relaunch — same folder is remembered (title bar
  or status bar shows the path).

### Step 7 — Background scanning
- **Build**: `ScanWorker` (QRunnable) running `WorkspaceScannerService.scan` on `QThreadPool`,
  emitting `workspace_ready(Workspace)` / `error(str)` back to `MainWindow`. *(Feature 28)*
- **Verify**: point at a folder with several real repos; UI stays responsive (window can still be
  moved/resized) while scan runs; a status bar message shows "Scanning..." then clears.

### Step 8 — Repo tree view
- **Build**: `RepoTreeModel` + `RepoTreeView` rendering `Workspace` as repo → directory → file
  tree. *(Features 1, 2, 3)*
- **Verify**: run against your real multi-repo folder, compare the tree shape/branch names
  side-by-side with the WebStorm screenshot layout.

### Step 9 — Change-type coloring + counts
- **Build**: color/icon per `ChangeType` in the tree, per-node file counts. *(Features 6, 7, 8)*
- **Verify**: modify/add/delete files in a couple of real repos, confirm colors and counts match
  what `git status` reports.

### Step 10 — Ignored-files toggle
- **Build**: toolbar checkbox wired to `include_ignored` on rescan. *(Feature 10)*
- **Verify**: toggle on/off against a repo with a populated `.gitignore`, confirm ignored files
  appear/disappear.

### Step 11 — Search/filter box
- **Build**: filter input above the tree, filters visible nodes by path substring. *(Feature 9)*
- **Verify**: type a partial filename, confirm only matching files (and their ancestor repo/
  folder nodes) remain visible.

### Step 12 — Expand/collapse state
- **Build**: "Collapse all" / "Expand all" toolbar buttons; persist expand state per session via
  `QSettings`. *(Feature 5)*
- **Verify**: collapse a repo, relaunch app, confirm it's still collapsed.

### Step 13 — Refresh
- **Build**: manual "Refresh" button (re-runs Step 7's worker); optional filesystem watcher
  (`QFileSystemWatcher`) triggering auto-rescan on change. *(Feature 4)*
- **Verify**: edit a file in a real repo outside the app, click Refresh (or wait for
  auto-refresh), confirm the tree updates without a full app restart.

---

## Phase 3 — Diff viewer

### Step 14 — Diff computation (infra + service)
- **Build**: `GitRepoAdapter.compute_diff()` → `DiffResult`/`DiffHunk`/`DiffLine`;
  `DiffService.load_diff()`. *(Features 25 groundwork)*
- **Verify**: pytest against a real throwaway repo with a modified file — assert hunks/lines
  match expected added/removed/context lines.

### Step 15 — Lazy diff loading + basic unified view
- **Build**: `DiffWorker`, plain-text `UnifiedView` (no highlighting yet), wired to tree
  selection — diff computed only on click, cached on `FileChange.diff` after. *(Features 12, 29)*
- **Verify**: click a modified file in the tree, see its unified diff text appear on the right;
  click the same file again, confirm it renders instantly (no re-compute — check via a temporary
  log line or debugger if not visually obvious).

### Step 16 — Line numbers + gutter markers
- **Build**: line-number columns and added/removed gutter markers in `UnifiedView`.
  *(Feature 15)*
- **Verify**: compare line numbers shown against the file opened in a text editor.

### Step 17 — Side-by-side view + toggle
- **Build**: `SideBySideView`, `DiffViewWidget` container with a toggle button switching between
  it and `UnifiedView`, both reading the same `DiffResult`. *(Features 11, 13)*
- **Verify**: toggle back and forth on the same file, confirm both views show equivalent content
  in their respective layouts (matches the screenshot's side-by-side style).

### Step 18 — Syntax highlighting
- **Build**: Pygments-backed `QSyntaxHighlighter` applied to both diff views based on file
  extension. *(Feature 14)*
- **Verify**: open a `.tsx`/`.py`/`.md` file's diff, confirm keyword/string/comment coloring
  matches the file's language.

### Step 19 — Intraline highlighting
- **Build**: word/char-level diff within changed lines (e.g. via `difflib.SequenceMatcher` on
  paired old/new lines), rendered as a sub-highlight on top of the line-level color.
  *(Feature 16)*
- **Verify**: modify a single word in a long line, confirm only that word is highlighted
  differently from the rest of the (still-changed) line.

### Step 20 — Context folding
- **Build**: collapse unchanged regions between hunks with an expand-on-click control.
  *(Feature 17)*
- **Verify**: diff a file with a small change in a large file, confirm most of the file is
  folded and expandable.

### Step 21 — Change navigation
- **Build**: Prev/Next toolbar buttons jumping the diff view's scroll position between hunks.
  *(Feature 18)*
- **Verify**: open a file with multiple hunks, click Next/Prev, confirm the view scrolls to each
  hunk in order.

### Step 22 — Whitespace-ignore toggle
- **Build**: toolbar toggle passed through to `compute_diff(ignore_whitespace=...)`.
  *(Feature 19)*
- **Verify**: reindent a line without changing its content, confirm toggling whitespace-ignore
  on hides that line from the diff.

### Step 23 — Copy actions
- **Build**: context-menu/toolbar actions "Copy diff" and "Copy file path". *(Feature 20)*
- **Verify**: click each, paste into a text editor, confirm expected content.

---

## Phase 4 — Multi-repo aggregation & file info

### Step 24 — Unified change list + scoped filtering + summary bar
- **Build**: an "all changes" aggregate list across repos in addition to the tree; selecting a
  repo/folder node scopes the diff list to it; status bar shows total changed file count across
  all repos. *(Features 21, 22, 23)*
- **Verify**: with several real repos changed, confirm the summary count matches manual addition
  of each repo's changed-file count; click a specific repo node, confirm the list scopes down.

### Step 25 — File/version info
- **Build**: show old blob id / ref in the diff header (feature 25, already partly available
  from Step 14's `DiffResult`); status bar shows encoding + line-ending style of the selected
  file (`GitRepoAdapter.read_blob` + detection); "Open in default editor" / "Reveal in Finder"
  actions. *(Features 25, 26, 27)*
- **Verify**: select a file, confirm status bar shows correct LF/CRLF and encoding; trigger
  "Reveal in Finder", confirm Finder opens to the right file.

---

## Phase 5 — Persistence polish & packaging

### Step 26 — Full settings persistence
- **Build**: extend `settings.py` to also persist window geometry/splitter sizes, last view mode
  (side-by-side vs unified), and whitespace-ignore state. *(Feature 30, completed)*
- **Verify**: resize window/splitter, switch to side-by-side, enable whitespace-ignore, quit,
  relaunch — all four restored.

### Step 27 — Packaging
- **Build**: PyInstaller spec file producing a standalone macOS `.app` bundle.
- **Verify**: run the built `.app` by double-clicking (not via terminal/venv), confirm it behaves
  identically to the dev run against a real multi-repo folder.

---

## Notes

- Every step that touches `core/` gets a pytest addition in the matching `tests/core/...`
  subfolder per `docs/architecture.md` §9 before moving to the next step.
- Any step involving Qt widgets is manually verified by running the app — no automated GUI tests
  in v1, per `docs/spec.md` §6.
- If a step reveals a design gap (e.g. a domain field missing), fix `docs/architecture.md`
  alongside the code rather than working around it silently.
