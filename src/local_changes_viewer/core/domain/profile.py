from dataclasses import dataclass, field


@dataclass
class Profile:
    name: str
    repo_names: list[str] = field(default_factory=list)
