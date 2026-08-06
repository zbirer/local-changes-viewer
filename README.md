# local-changes-viewer

A lightweight desktop GUI for macOS that shows git changes across **all repositories** found
under a chosen root folder — similar to JetBrains WebStorm's "Local Changes" view, but working
across many repos at once instead of one project at a time.

> **Status**: in development, v1 is **view-only** (no staging/committing/discarding from the
> GUI). See [`docs/implementation-plan.md`](docs/implementation-plan.md) for build progress.

## Features (v1)

### Multi-repo scanning

- Recursively discovers every git repo under a folder, including nested worktrees, and shows
  their changes in one tree
- Background scanning and lazy diff loading — stays responsive on folders with many repos
- Watches the workspace for file-system changes and auto-refreshes the tree
- Manual "Refresh" (whole workspace) and "Refresh Repo" (single repo) actions
- Auto-refresh on a configurable timer
- Optional GitHub PR/branch-status lookup per repo (skipped for repos only pulled in via
  worktree-parent inheritance, to save API calls)

### Change tree & diffs

- Groups changes by Modified / Added / Deleted / Renamed / Untracked / Ignored
- Optionally shows files changed by local commits not yet pushed upstream, in their own color
- Side-by-side **and** unified diff views, with a toggle between them
- Syntax-highlighted diffs (via Pygments), with word-level highlighting inside changed lines
- In-place file editing from the side-by-side view, with save support
- Copy diff / file path / file name to clipboard, open in default editor, reveal in Finder

### Filtering & organization

- Search/filter the tree by repo and/or file name
- Collapse/expand all, expand/collapse current repo, expand only changed repos
- "Ignore MD files", "Show ignored files", "Hide repos without changes", and
  "Ignore whitespace" toggles
- Custom folder-name filter rules (contains/equals/exact file path), manageable from a dialog
  or added directly from the tree's right-click menu ("Filter Out This Folder/File")
- Profiles to scope the workspace to a named subset of repos, with a menu to add/remove the
  current repo from any profile

### GitHub integration

- Connect/disconnect a GitHub account
- "My Open Pull Requests" panel and dialog, with checks/review status
- Approved PRs highlighted in the tree

### App behavior & preferences

- Remembers your last-opened folder, window layout, splitter sizes, and view preferences
- Adjustable diff font size, toggleable line numbers
- Configurable log level and an in-app log viewer, copyable to clipboard
- Persistent "Scanning…" status bar indicator for the duration of a scan

Full feature list: [`docs/spec.md`](docs/spec.md). Architecture/design:
[`docs/architecture.md`](docs/architecture.md).

## Requirements

- macOS (v1 targets macOS only)
- Python 3.11+
- `git` installed and on your `PATH`

## Running from source (not pre-compiled)

If you don't have the packaged `.app` bundle, run it straight from the Python source with a
virtualenv:

```bash
git clone <this-repo-url>
cd local-changes-viewer

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

python -m local_changes_viewer
```

On launch, use **File → Open Folder** to pick the root folder containing the git repos you want
to view. The app remembers this folder for next time.

## Running tests

Tests cover the `core` package (domain models, git/filesystem infra, business-rule services).
The GUI layer is verified manually — see [`docs/implementation-plan.md`](docs/implementation-plan.md).

```bash
pytest
```

## Compiling a standalone macOS app

v1 packages as a standalone `.app` bundle via [PyInstaller](https://pyinstaller.org/):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

pyinstaller packaging/local-changes-viewer.spec --noconfirm
```

The built app is placed under `dist/local-changes-viewer.app`. Double-click to run it — it does
not require the venv or Python to be active, and can be moved/distributed on its own.

## Project structure

```
local-changes-viewer/
├── docs/                    # spec, architecture, implementation plan
├── src/local_changes_viewer/
│   ├── core/
│   │   ├── domain/          # plain dataclasses: Workspace, Repository, FileChange, DiffResult...
│   │   ├── infra/           # git/filesystem wrappers (GitPython, os/pathlib)
│   │   └── services/        # business logic: WorkspaceScannerService, DiffService
│   └── gui/                 # PySide6 windows/widgets
├── tests/core/               # pytest suite for core/
├── packaging/                 # PyInstaller spec
└── pyproject.toml
```

See [`docs/architecture.md`](docs/architecture.md) for the full layering rationale (business
logic is isolated from GitPython/filesystem details and from the GUI).

