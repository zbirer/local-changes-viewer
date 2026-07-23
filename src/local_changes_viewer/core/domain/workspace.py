from dataclasses import dataclass, field
from pathlib import Path

from local_changes_viewer.core.domain.repository import Repository


@dataclass
class Workspace:
    root_path: Path
    repositories: list[Repository] = field(default_factory=list)
