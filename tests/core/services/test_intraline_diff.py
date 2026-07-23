from local_changes_viewer.core.services.intraline_diff import intraline_ranges


def test_single_word_change_in_long_line_highlights_only_that_word() -> None:
    old_text = "the quick brown fox jumps over the lazy dog"
    new_text = "the quick brown fox leaps over the lazy dog"

    old_ranges, new_ranges = intraline_ranges(old_text, new_text)

    # SequenceMatcher narrows to the minimal differing substring within the
    # word ("jum"/"lea"), leaving the shared "ps" suffix unhighlighted -
    # everything outside the changed word stays untouched either way.
    assert len(old_ranges) == 1
    assert len(new_ranges) == 1
    old_start, old_end = old_ranges[0]
    new_start, new_end = new_ranges[0]
    assert old_start >= len("the quick brown fox ")
    assert old_end <= len("the quick brown fox jumps")
    assert new_start >= len("the quick brown fox ")
    assert new_end <= len("the quick brown fox leaps")


def test_identical_text_produces_no_ranges() -> None:
    old_ranges, new_ranges = intraline_ranges("same text", "same text")

    assert old_ranges == []
    assert new_ranges == []


def test_completely_different_text_covers_whole_string() -> None:
    old_ranges, new_ranges = intraline_ranges("abc", "xyz")

    assert old_ranges == [(0, 3)]
    assert new_ranges == [(0, 3)]
