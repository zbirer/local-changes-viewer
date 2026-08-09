# CLAUDE.md

## Feature inventory (FEATURES.md)

`FEATURES.md` is the inventory of every user-visible feature this app has.
It is enforced by `tests/test_features_gate.py`, which fails the suite if a
block is malformed, a `WHERE:` implementation path no longer exists, a cited
test no longer exists, or the untested-feature count rises.

- **Changed behavior?** Update the affected `### F<n>.` block in FEATURES.md
  in the same commit as the code change.
- **Added a feature?** Add a new block with a real test cited in `TESTS:`.
  The ratchet (`MAX_UNTESTED_FEATURES` in `tests/test_features_gate.py`) will
  not accept a new `TESTS: NONE`.
- **Moved a file?** Update the feature's `WHERE:` path.
- **Renamed or removed a test?** Update every FEATURES.md block citing it.
- `MAX_UNTESTED_FEATURES` may only go down (as untested features gain
  coverage), never up.
