from difflib import SequenceMatcher


def intraline_ranges(old_text: str, new_text: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Returns (old_ranges, new_ranges): half-open [start, end) character
    offsets of the parts of old_text/new_text that differ from each other."""
    matcher = SequenceMatcher(None, old_text, new_text, autojunk=False)
    old_ranges: list[tuple[int, int]] = []
    new_ranges: list[tuple[int, int]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if i1 != i2:
            old_ranges.append((i1, i2))
        if j1 != j2:
            new_ranges.append((j1, j2))
    return old_ranges, new_ranges
