# Enforces the contract at the top of FEATURES.md: that file is the
# hand-maintained inventory of every user-visible feature, and this module
# is what keeps it honest. See FEATURES.md's header for the four rules;
# each is implemented below in the same order.

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FEATURES_PATH = REPO_ROOT / "FEATURES.md"
GATE_MODULE = "tests/test_features_gate.py"

# Number of features in FEATURES.md currently marked `TESTS: NONE`, i.e. the
# ratchet ceiling for rule 4 below. This may only be LOWERED (as an untested
# feature gains a test) -- never raised to make room for a new untested
# feature or to un-block a coverage regression. It was set to the actual
# count of `TESTS: NONE` blocks when this gate was introduced.
MAX_UNTESTED_FEATURES = 30


@dataclass(frozen=True)
class Feature:
    number: int
    body: str  # raw text between this "### F<n>." heading and the next


_HEADING_RE = re.compile(r"^### F(\d+)\.", re.M)
_WHAT_FIELD_RE = re.compile(r"^WHAT:", re.M)
_WHERE_FIELD_RE = re.compile(r"^WHERE:", re.M)
_WHERE_VALUE_RE = re.compile(r"^WHERE:\s*`([^`]+)`", re.M)
_TESTS_FIELD_RE = re.compile(r"^TESTS:", re.M)
_TESTS_VALUE_RE = re.compile(r"^TESTS:\s*(.*)$", re.M)


def _load_features() -> list[Feature]:
    text = FEATURES_PATH.read_text()
    headings = list(_HEADING_RE.finditer(text))
    features = []
    for i, m in enumerate(headings):
        start = m.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        features.append(Feature(number=int(m.group(1)), body=text[start:end]))
    return features


FEATURES = _load_features()


def _tests_value(body: str) -> str:
    m = _TESTS_VALUE_RE.search(body)
    return m.group(1).strip() if m else ""


def _where_value(body: str) -> str | None:
    m = _WHERE_VALUE_RE.search(body)
    return m.group(1) if m else None


def _cited_tests(body: str) -> list[str]:
    value = _tests_value(body)
    if value == "NONE":
        return []
    return re.findall(r"`([^`]+)`", value)


# ---------------------------------------------------------------------------
# Rule 1 -- every block is well-formed: exactly one WHAT:/WHERE:/TESTS:, and
# feature numbers are unique and sequential starting at F1.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("feature", FEATURES, ids=[f"F{f.number}" for f in FEATURES])
def test_feature_block_is_well_formed(feature: Feature) -> None:
    what_count = len(_WHAT_FIELD_RE.findall(feature.body))
    where_count = len(_WHERE_FIELD_RE.findall(feature.body))
    tests_count = len(_TESTS_FIELD_RE.findall(feature.body))
    assert (what_count, where_count, tests_count) == (1, 1, 1), (
        f"FEATURES.md F{feature.number} must have exactly one WHAT:, one "
        f"WHERE:, and one TESTS: line; found WHAT={what_count} "
        f"WHERE={where_count} TESTS={tests_count}."
    )


def test_feature_numbers_are_unique_and_sequential_from_f1() -> None:
    numbers = [f.number for f in FEATURES]
    expected = list(range(1, len(numbers) + 1))
    assert numbers == expected, (
        "FEATURES.md feature numbers must be unique and sequential "
        f"starting at F1. Found order {numbers}, expected {expected}."
    )


# ---------------------------------------------------------------------------
# Rule 2 -- every WHERE: path exists (line numbers are advisory and stripped).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("feature", FEATURES, ids=[f"F{f.number}" for f in FEATURES])
def test_where_path_exists(feature: Feature) -> None:
    raw = _where_value(feature.body)
    assert raw is not None, (
        f"FEATURES.md F{feature.number} has no parseable WHERE: `path` value."
    )
    path_only = re.sub(r":\d+$", "", raw)
    assert (REPO_ROOT / path_only).exists(), (
        f"FEATURES.md F{feature.number}'s WHERE: path '{path_only}' does not "
        "exist relative to the repo root -- the implementation may have "
        "been moved or deleted. Update the WHERE: line in FEATURES.md."
    )


# ---------------------------------------------------------------------------
# Rule 3 -- every cited test actually exists and is collected by pytest.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def collected_node_ids() -> tuple[set[str], set[tuple[str, str]]]:
    """Real, currently-collectible pytest node IDs for the whole suite.

    Collected via a subprocess `pytest --collect-only -q` run rather than
    grepping source for `def test_...` (which would silently accept a test
    that source-level exists but collection drops -- e.g. skipped/misnamed
    -- and would miss parametrized IDs entirely), and rather than in-process
    `_pytest` collection APIs (simpler to get wrong and to leak state into
    this already-running session). The subprocess explicitly `--ignore`s
    this gate module itself so it can never re-collect (and thus never
    re-run) test_features_gate.py -- that is the anti-recursion guard.
    Module-scoped so the whole suite is only collected once for this file.
    """
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            f"--ignore={GATE_MODULE}",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        "Collecting the test suite for the FEATURES.md gate failed:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    node_ids: set[str] = set()
    base_names: set[tuple[str, str]] = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if "::" not in line:
            continue
        node_ids.add(line)
        path, _, rest = line.partition("::")
        func = rest.rsplit("::", 1)[-1]
        func = func.split("[", 1)[0]
        base_names.add((path, func))

    assert node_ids, (
        "Collected zero tests via the subprocess collection run -- "
        "something is wrong with the collection command itself, not with "
        "FEATURES.md."
    )
    return node_ids, base_names


def _feature_test_citations() -> list[tuple[int, str]]:
    citations = []
    for feature in FEATURES:
        for cited in _cited_tests(feature.body):
            citations.append((feature.number, cited))
    return citations


_CITATIONS = _feature_test_citations()


@pytest.mark.parametrize(
    "feature_number, cited",
    _CITATIONS,
    ids=[f"F{n}-{c}" for n, c in _CITATIONS],
)
def test_cited_test_exists(
    feature_number: int,
    cited: str,
    collected_node_ids: tuple[set[str], set[tuple[str, str]]],
) -> None:
    node_ids, base_names = collected_node_ids
    if cited in node_ids:
        return

    # Fall back to a base-name match for parametrized tests, where the
    # citation names the function but not each individual parameter case
    # (e.g. `test_parse_github_owner_repo` covers every
    # `test_parse_github_owner_repo[...]` node ID).
    path, _, rest = cited.partition("::")
    func = rest.rsplit("::", 1)[-1] if rest else ""
    assert (path, func) in base_names, (
        f"FEATURES.md F{feature_number}'s TESTS: line cites '{cited}', "
        "which pytest does not actually collect (checked both the exact "
        "node ID and a parametrized-base-name match). The test was "
        "renamed, removed, or never existed under that name -- fix the "
        "citation in FEATURES.md, or change it to `TESTS: NONE` if no such "
        "test exists."
    )


# ---------------------------------------------------------------------------
# Rule 4 -- the untested-feature ratchet: never more than MAX_UNTESTED_FEATURES.
# ---------------------------------------------------------------------------


def test_untested_feature_count_does_not_exceed_ratchet() -> None:
    untested = [f.number for f in FEATURES if _tests_value(f.body) == "NONE"]
    assert len(untested) <= MAX_UNTESTED_FEATURES, (
        f"{len(untested)} features in FEATURES.md are marked TESTS: NONE, "
        f"which exceeds MAX_UNTESTED_FEATURES ({MAX_UNTESTED_FEATURES}) in "
        "tests/test_features_gate.py. Untested features: "
        + ", ".join(f"F{n}" for n in untested)
        + ". For a newly added feature: add a test for it and cite it in "
        "FEATURES.md's TESTS: line. For a previously-tested feature that "
        "lost coverage: restore the test. Do not raise "
        "MAX_UNTESTED_FEATURES to make this pass -- it may only be lowered."
    )
