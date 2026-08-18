from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CommitLogEntry:
    hexsha: str
    short_hexsha: str
    message: str
    committed_datetime: datetime
    branch_name: str = ""
    full_message: str = ""
    # Defaulted so the pre-existing construction site (get_recent_commits)
    # and every consumer that predates this field keep working unchanged --
    # File History (get_file_history) is the caller that actually needs it,
    # since its commit table has an author column and no branch column.
    author: str = ""
