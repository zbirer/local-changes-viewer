from dataclasses import dataclass
from enum import Enum


class FolderFilterMode(Enum):
    CONTAINS = "contains"
    EQUALS = "equals"


@dataclass(frozen=True)
class FolderFilterRule:
    text: str
    mode: FolderFilterMode

    def matches(self, folder_name: str) -> bool:
        if self.mode == FolderFilterMode.EQUALS:
            return folder_name == self.text
        return self.text in folder_name
