from dataclasses import dataclass, field
from enum import Enum, auto


class DiffLineKind(Enum):
    CONTEXT = auto()
    ADDED = auto()
    REMOVED = auto()


@dataclass(frozen=True)
class DiffLine:
    kind: DiffLineKind
    old_lineno: int | None
    new_lineno: int | None
    text: str


@dataclass(frozen=True)
class DiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[DiffLine] = field(default_factory=list)


@dataclass(frozen=True)
class DiffResult:
    old_ref: str
    new_ref: str
    hunks: list[DiffHunk] = field(default_factory=list)
    old_blob_id: str | None = None
    new_blob_id: str | None = None
