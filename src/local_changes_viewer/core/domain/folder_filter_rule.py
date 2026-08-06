from dataclasses import dataclass
from enum import Enum


class FolderFilterMode(Enum):
    CONTAINS = "contains"
    EQUALS = "equals"
    FILE_PATH = "file_path"


@dataclass(frozen=True)
class FolderFilterRule:
    text: str
    mode: FolderFilterMode

    def matches(self, folder_name: str) -> bool:
        if self.mode == FolderFilterMode.EQUALS:
            return folder_name == self.text
        if self.mode == FolderFilterMode.FILE_PATH:
            return False
        return self.text in folder_name

    def matches_path(self, relative_path: str) -> bool:
        return self.mode == FolderFilterMode.FILE_PATH and self.text == relative_path
