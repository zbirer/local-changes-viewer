# local-changes-viewer

A lightweight desktop GUI for macOS that shows git changes across **all repositories** found
under a chosen root folder — similar to JetBrains WebStorm's "Local Changes" view, but working
across many repos at once instead of one project at a time.

> **Status**: in development, v1 is **view-only** (no staging/committing/discarding from the
> GUI). See [`docs/implementation-plan.md`](docs/implementation-plan.md) for build progress.

## Features (v1)

- Recursively discovers every git repo under a folder and shows their changes in one tree
- Groups changes by Modified / Added / Deleted / Renamed / Untracked / Ignored
- Side-by-side **and** unified diff views, with a toggle between them
- Syntax-highlighted diffs (via Pygments), with word-level highlighting inside changed lines
- Search/filter, collapse/expand, ignored-files toggle, whitespace-ignore toggle
- Background scanning and lazy diff loading — stays responsive on folders with many repos
- Remembers your last-opened folder, window layout, and view preferences

Full feature list: [`docs/spec.md`](docs/spec.md). Architecture/design:
[`docs/architecture.md`](docs/architecture.md).

## Requirements

- macOS (v1 targets macOS only)
- Python 3.11+
- `git` installed and on your `PATH`

## Running from source

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

## Building a standalone macOS app

v1 packages as a standalone `.app` bundle via [PyInstaller](https://pyinstaller.org/):

```bash
pip install -e ".[dev]"
pyinstaller packaging/local-changes-viewer.spec
```

The built app is placed under `dist/local-changes-viewer.app`. Double-click to run it — it does
not require the venv or Python to be active.

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

## Tech stack

| Concern         | Choice                    |
|-----------------|---------------------------|
| GUI             | PySide6 (Qt for Python)   |
| Git access      | GitPython                 |
| Diff highlighting | Pygments                |
| Settings        | Qt `QSettings`            |
| Packaging       | PyInstaller               |
| Tests           | pytest                    |

## Contributing

This project is under active initial development against the plan in
[`docs/implementation-plan.md`](docs/implementation-plan.md). Issues/PRs welcome once v1 is
further along.

## License

No license file yet — all rights reserved until one is added.
