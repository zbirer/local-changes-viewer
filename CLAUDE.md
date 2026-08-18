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

## Features are a standing contract

Every `### F<n>.` block in `FEATURES.md` describes behavior that **must keep
working**. The file is not a changelog of what was once built — it is the list
of promises this app currently keeps.

- **Never remove, disable, or degrade a listed feature** as a side effect of
  other work. Refactors, bug fixes, performance work, and cleanup are all
  bound by this.
- A block is deleted, or its `WHAT:` weakened, **only when the user explicitly
  asks for that feature to be removed or changed.** Nothing else authorizes it
  — not "it looked unused", not "the new design supersedes it", not "the test
  was easier to delete than to fix".
- If a requested change would break a listed feature, **stop and name the F
  block it breaks** before writing code. That call is the user's, not yours.
- A test cited on a `TESTS:` line is that promise's proof. Deleting or
  weakening such a test is the same act as removing the feature.
- Prose drift counts as breakage: if the code no longer does what a `WHAT:`
  line says, either the code regressed or the block lies. Both are defects.
