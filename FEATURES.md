# Feature Inventory

Every user-visible behavior this app has. A "feature" is something a user would
notice losing — not an internal helper.

**This file is enforced by a test.** `tests/test_features_gate.py` parses it on
every run and fails the suite when:

1. a block is malformed (missing `WHAT:`, `WHERE:`, or `TESTS:`),
2. a `WHERE:` path no longer exists — the implementation was moved or deleted,
3. a cited test no longer exists — coverage was removed,
4. the number of features marked `TESTS: NONE` rises above the recorded baseline.

Rule 4 is a ratchet. It does not demand tests for the features that lack them
today; it only forbids the number growing. Adding a feature without a test, or
stripping the tests off a covered one, breaks the build.

## When you change code

- **Changed behavior?** Update the affected block here in the same commit.
- **Added a feature?** Add a block, with a test, under the category it best
  fits, and renumber so numbering stays sequential from F1 in document order.
  The ratchet will not accept a new `TESTS: NONE`.
- **Moved a file?** Update its `WHERE:` anchor.
- **Renamed a test?** Update every block citing it.
- **Reorganized categories, or moved a block to a different one?** Keep every
  block's `### F<n>.` heading sequential (F1…F87) in document order — a block
  that changes position gets renumbered to match, even if nothing else about
  it changed.

`WHERE:` line numbers drift and are advisory — the gate checks the *file*
exists, not the line. `TESTS:` entries are checked exactly and must name real
tests.

## Categories

- [Workspace Tree & Navigation](#workspace-tree--navigation) — F1–F16, F89
- [Scanning, Refresh & Caching](#scanning-refresh--caching) — F17–F28
- [Filtering](#filtering) — F29–F36, F95
- [Profiles](#profiles) — F37–F43
- [Git Change Detection](#git-change-detection) — F44–F52
- [GitHub Integration & Pull Requests](#github-integration--pull-requests) — F53–F64
- [Diff Viewing & Editing](#diff-viewing--editing) — F65–F78, F87, F96
- [Settings, Persistence & Logging](#settings-persistence--logging) — F79–F86, F88, F90

---

## Workspace Tree & Navigation

The folder-tree view of scanned repos: how rows render, expand/collapse, and get filtered or acted on.

### F1. Open a folder to scan for git repos
WHAT: Actions > Open Folder… lets the user pick the root directory to scan.
WHERE: `src/local_changes_viewer/gui/main_window.py:198`
TESTS: NONE

### F2. Tree groups each repo's changed files by folder
WHAT: The folder tree nests a repo's changed files under their containing directories, with per-dir change counts.
WHERE: `src/local_changes_viewer/gui/workspace_tree/tree_model.py:355`
TESTS: NONE

### F3. Repo row label shows branch, ahead/behind, change count, PR badge
WHAT: Each repo row's text is "name [branch, +ahead/-behind] (N)" plus "[PR #n state]" if one is associated.
WHERE: `src/local_changes_viewer/gui/workspace_tree/tree_model.py:289`
TESTS: NONE

### F4. Repo row tooltip shows status, branches, PR summary, absolute path, and (for worktrees) folder creation/modification times
WHAT: Hovering a repo row shows name, change/ahead-behind summary, branch, parent/default branch, PR summary, and absolute path. If the row is a worktree (identified by `logical_parent_path`, F6), the tooltip also shows the worktree folder's creation time and last-modification time (from the filesystem, not git history).
WHERE: `src/local_changes_viewer/gui/workspace_tree/tree_model.py:316`
TESTS: `tests/gui/test_workspace_tree_model.py::test_repo_row_tooltip_includes_absolute_path`, `tests/gui/test_workspace_tree_model.py::test_worktree_row_tooltip_includes_folder_created_and_modified`

### F5. Changed files colored by change type; unpushed commits styled distinctly
WHAT: Modified/added/deleted/renamed/untracked/ignored files render in different colors; unpushed-commit files get a distinct color, a "(unpushed commit)" suffix, and a commit-message tooltip.
WHERE: `src/local_changes_viewer/gui/workspace_tree/tree_model.py:392`
TESTS: NONE

### F6. Nested repos/worktrees always render as sub-trees, regardless of changes
WHAT: A worktree or nested repo appears as its own sub-branch inside its parent's tree unconditionally, matching top-level repos and matching WorktreesDialog's listing (which queries `git worktree list` directly) -- including a worktree with zero uncommitted changes. Hiding a repo with no changes is the job of two separate, opt-in, off-by-default settings applied before the workspace ever reaches the tree: "Hide repos without changes" (F35) for regular top-level repos, and "Hide empty worktrees" (F95) for worktrees specifically. A worktree whose own directory lives outside its parent repo's directory tree (e.g. a sibling worktree like `~/dev/.worktrees/dashboard-eh-12404`, physically outside `~/dev/dashboard`) still renders nested under its logical parent, but its displayed label is prefixed with `[external] ` to distinguish it from a worktree nested inside the parent's own tree (e.g. `dashboard/.claude/worktrees/x`, which keeps its plain name). The prefix is purely cosmetic on the QStandardItem's displayed text -- it never touches `repo.name`, `NODE_KEY_ROLE`, or `REPO_PATH_ROLE`.
WHERE: `src/local_changes_viewer/gui/workspace_tree/tree_model.py:113`
TESTS: `tests/gui/test_tree_model.py::test_sync_nested_repos_does_not_crash_when_repo_has_both_a_direct_worktree_and_a_filesystem_nested_repo`, `tests/gui/test_tree_model.py::test_set_workspace_renders_clean_worktree_as_nested_repo_row`, `tests/gui/test_tree_model.py::test_set_workspace_renders_all_worktrees_when_repo_has_several`, `tests/gui/test_tree_model.py::test_set_workspace_renders_internal_worktree_name_without_external_prefix`, `tests/gui/test_tree_model.py::test_set_workspace_renders_external_worktree_name_with_external_prefix`

### F7. Tree survives duplicate repo paths without going empty
WHAT: If two scan results share a path, the tree still renders exactly one row for it instead of collapsing to nothing.
WHERE: `src/local_changes_viewer/gui/workspace_tree/tree_model.py:199`
TESTS: `tests/gui/test_tree_model.py::test_partition_deduplicates_repositories_sharing_a_path`

### F8. Tree preserves expansion/scroll state during incremental scan updates
WHAT: While a scan is running (or on repo refresh), the tree updates in place instead of clearing and losing expand/scroll state.
WHERE: `src/local_changes_viewer/gui/main_window.py:1562`
TESTS: `tests/gui/test_main_window.py::test_on_workspace_ready_preserves_tree_in_place_when_tree_already_has_rows`, `tests/gui/test_main_window.py::test_scan_refresh_tick_does_not_empty_tree_while_repos_reappear`, `tests/gui/test_main_window.py::test_on_workspace_ready_rebuilds_when_tree_is_empty`

### F9. View menu: Collapse/Expand All, Expand Changed Repos, Expand/Collapse Current Repo
WHAT: Bulk expand/collapse commands for the whole tree or the currently selected repo.
WHERE: `src/local_changes_viewer/gui/workspace_tree/tree_view.py:245`
TESTS: NONE

### F10. Per-folder collapsed/expanded state persists across restarts
WHAT: Any folder the user manually collapses stays collapsed next time the app opens.
WHERE: `src/local_changes_viewer/gui/settings.py:196`
TESTS: NONE

### F11. Repo-row hover buttons: Refresh / Expand All / Collapse All
WHAT: Hovering a repo-root row reveals inline R/+/− buttons that refresh, expand, or collapse just that repo, painted as an opaque green chip so they stay legible over a blue-selected or yellow-flashed row; selecting a row does not show them.
WHERE: `src/local_changes_viewer/gui/workspace_tree/tree_view.py:133`
TESTS: `tests/gui/test_tree_view.py::test_refresh_button_sits_left_of_expand_and_collapse`, `tests/gui/test_tree_view.py::test_refresh_button_click_emits_signal_with_repo_path`, `tests/gui/test_tree_view.py::test_row_actions_overlay_shown_for_repo_root_hidden_otherwise`, `tests/gui/test_tree_view.py::test_hovering_repo_root_row_shows_overlay`, `tests/gui/test_tree_view.py::test_hovering_non_repo_root_row_hides_overlay`, `tests/gui/test_tree_view.py::test_current_changed_alone_does_not_show_overlay`, `tests/gui/test_tree_view.py::test_leaving_viewport_hides_overlay`, `tests/gui/test_tree_view.py::test_row_actions_overlay_has_green_chip_stylesheet`, `tests/gui/test_tree_view.py::test_overlay_stays_visible_when_cursor_over_overlay_widget`

### F12. Filter tree by path text box, with repo/file split syntax
WHAT: Typing in the "Filter by path…" box filters visible rows; typing a "/" or "\" splits the query into a repo-name part and a file part.
WHERE: `src/local_changes_viewer/gui/workspace_tree/tree_view.py:232`
TESTS: NONE

### F13. "All Changes" aggregate flat list tab, scoped to selection
WHAT: A second tab lists every changed file as a flat "repo/path" list, narrowing to whatever repo/folder is selected in the tree.
WHERE: `src/local_changes_viewer/gui/workspace_tree/aggregate_list.py`
TESTS: NONE

### F14. Refresh a single repo (row button or context menu), with highlight flash
WHAT: Refreshing one repo re-scans just that repo, guards against double-clicks starting two concurrent refreshes, and briefly highlights the row when done.
WHERE: `src/local_changes_viewer/gui/main_window.py:1309`
TESTS: `tests/gui/test_tree_view.py::test_refresh_button_click_emits_signal_with_repo_path`

### F15. File-row right-click menu: Copy Path/Name, Refresh Diff, Create patch, Filter Out This File
WHAT: Right-clicking a changed file offers path/name copy, a forced diff reload, generating a patch of just that file (or, for a collapsed untracked directory row, everything under it), and a one-file filter shortcut.
WHERE: `src/local_changes_viewer/gui/main_window.py:1214`
TESTS: NONE

### F16. Folder-row right-click menu, with repo-root-only Refresh/Show Log/Add to Profile
WHAT: Right-clicking a folder or repo-root row offers folder-scoped actions -- including generating a patch of every change under that folder -- and only repo roots additionally get Refresh Repo, Show Log, and Add to Profile.
WHERE: `src/local_changes_viewer/gui/main_window.py:1225`
TESTS: NONE

## Scanning, Refresh & Caching

How the app discovers repos, rescans them, and reuses cached state for fast startup and refresh.

### F17. Startup scan discovers git repos under the chosen root, with progress messages
WHAT: Opening a folder walks it for git repos and streams progress ("Found N repos…", "Scanned i/N…") to the status bar.
WHERE: `src/local_changes_viewer/core/services/workspace_scanner_service.py:79`
TESTS: `tests/core/services/test_workspace_scanner_service.py::test_scan_builds_workspace_from_multiple_repos`, `tests/core/services/test_workspace_scanner_service.py::test_scan_reports_progress_for_discovery_and_each_repo`, `tests/core/services/test_workspace_scanner_service.py::test_scan_reports_progress_for_empty_workspace`, `tests/core/services/test_workspace_scanner_service.py::test_scan_returns_empty_workspace_when_no_repos_found`

### F18. Tree paints instantly from an on-disk cache at startup, before the real scan finishes
WHAT: On launch, if a cache matching the chosen root exists, the tree shows it immediately while a fresh background scan replaces it.
WHERE: `src/local_changes_viewer/gui/main_window.py:1394`
TESTS: NONE

### F19. Manual "Refresh" (Actions menu / Ctrl+R) forces a full, non-cached rescan
WHAT: Explicit refresh always re-reads real git state for every repo, never reusing a per-repo cache.
WHERE: `src/local_changes_viewer/gui/main_window.py:497`
TESTS: NONE

### F20. Auto Refresh… interval setting, with a minimum-interval guard against overlapping scans
WHAT: A configurable periodic auto-rescan (0 = disabled) that skips itself if the previous scan finished too recently.
WHERE: `src/local_changes_viewer/gui/main_window.py:709`
TESTS: `tests/gui/test_main_window.py::test_auto_refresh_skipped_when_previous_scan_finished_too_recently`, `tests/gui/test_main_window.py::test_auto_refresh_proceeds_once_minimum_interval_has_elapsed`

### F21. "Watch for File Changes" setting: debounced auto-rescan on filesystem changes
WHAT: Toggling this setting watches repo directories (and every already-changed file) and triggers a debounced rescan on any edit/create/delete.
WHERE: `src/local_changes_viewer/gui/workspace_watcher.py`
TESTS: `tests/gui/test_workspace_watcher.py::test_collect_watch_paths_includes_repo_root_and_subdirs`, `tests/gui/test_workspace_watcher.py::test_dirty_repo_roots_maps_subdirectory_change_to_owning_repo_root`, `tests/gui/test_workspace_watcher.py::test_file_changed_marks_owning_repo_dirty`, `tests/gui/test_workspace_watcher.py::test_file_changed_signal_is_wired_to_the_underlying_watcher`, `tests/gui/test_workspace_watcher.py::test_set_watched_files_caps_at_max_and_marks_watcher_files`

### F22. Scan reuses cached per-repo git state unless dirty, forced, or aged past a max cache age
WHAT: A repo not flagged dirty by the watcher and within the age floor skips a real git rescan; dirty/forced/stale repos always rescan.
WHERE: `src/local_changes_viewer/core/services/workspace_scanner_service.py:294`
TESTS: `tests/core/services/test_workspace_scanner_service.py::test_scan_reuses_cached_changes_for_repo_not_in_dirty_paths`, `tests/core/services/test_workspace_scanner_service.py::test_scan_rescans_repo_in_dirty_paths`, `tests/core/services/test_workspace_scanner_service.py::test_scan_force_full_rescan_bypasses_dirty_paths_gate`, `tests/core/services/test_workspace_scanner_service.py::test_scan_age_floor_rescans_stale_repo_even_when_not_dirty`, `tests/core/services/test_workspace_scanner_service.py::test_scan_age_floor_does_not_rescan_repo_within_max_age`, `tests/core/services/test_workspace_scanner_service.py::test_scan_age_floor_caps_rescans_per_tick`

### F23. "Verify Changes Against Git…" self-check reports scan and render-side discrepancies
WHAT: Re-runs live git status per repo and diffs it against app state and what's actually rendered, reporting mismatches by name and reason.
WHERE: `src/local_changes_viewer/core/services/workspace_scanner_service.py:408`
TESTS: `tests/core/services/test_workspace_scanner_service.py::test_verify_changes_against_git_reports_clean_when_matching`, `tests/core/services/test_workspace_scanner_service.py::test_verify_changes_against_git_reports_discrepancy_when_cache_diverges`

### F24. Scan tolerates a repo that fails to read without aborting the whole scan
WHAT: If reading one repo's git state throws, that repo is skipped (logged) and the rest of the scan continues.
WHERE: `src/local_changes_viewer/core/services/workspace_scanner_service.py:348`
TESTS: `tests/core/services/test_workspace_scanner_service.py::test_scan_skips_repo_that_fails_to_read`, `tests/core/services/test_workspace_scanner_service.py::test_scan_does_not_call_on_repo_ready_for_broken_repo`

### F25. Scan discovers linked git worktrees as separate repo rows with a logical parent
WHAT: Worktrees of a discovered repo are scanned and shown as their own tree rows, tracked back to their parent repo, even when the worktree's own directory is excluded by the parent repo's .gitignore -- discovery goes through `git worktree list --porcelain` (GitRepoAdapter.list_worktrees), which never consults gitignore, so an unrelated ignored directory (e.g. node_modules) that isn't a registered worktree still never becomes a repo row.
WHERE: `src/local_changes_viewer/core/services/workspace_scanner_service.py:270`
TESTS: `tests/core/services/test_workspace_scanner_service.py::test_scan_includes_linked_worktrees_as_separate_repos`, `tests/core/services/test_workspace_scanner_service.py::test_scan_records_logical_parent_for_sibling_directory_worktree`, `tests/core/services/test_workspace_scanner_service.py::test_scan_skips_repo_when_listing_worktrees_fails`, `tests/core/services/test_workspace_scanner_service.py::test_scan_skips_stale_worktree_paths_that_no_longer_exist`, `tests/core/services/test_workspace_scanner_service.py::test_scan_discovers_gitignored_worktree_as_nested_repo_but_not_other_ignored_dirs`

### F26. "Show ignored files" setting includes git-ignored files in the change list
WHAT: Toggling this setting shows or hides files git reports as ignored.
WHERE: `src/local_changes_viewer/core/services/workspace_scanner_service.py:357`
TESTS: `tests/core/services/test_workspace_scanner_service.py::test_scan_filters_ignored_files_by_default`, `tests/core/services/test_workspace_scanner_service.py::test_scan_includes_ignored_files_when_requested`, `tests/core/infra/test_git_repo_adapter.py::test_list_changes_detects_ignored_file`

### F27. "Show committed but not pushed files" setting surfaces unpushed local commits
WHAT: Toggling this setting adds files changed by commits ahead of upstream (but not yet pushed) to the change list, each tagged with its commit message.
WHERE: `src/local_changes_viewer/core/infra/git_repo_adapter.py:73`
TESTS: `tests/core/infra/test_git_repo_adapter.py::test_list_changes_excludes_unpushed_commit_by_default`, `tests/core/infra/test_git_repo_adapter.py::test_list_changes_includes_unpushed_commit_when_requested`, `tests/core/infra/test_git_repo_adapter.py::test_list_changes_includes_commit_message_for_unpushed_commit`, `tests/core/infra/test_git_repo_adapter.py::test_list_changes_does_not_duplicate_file_already_dirty_in_working_tree`, `tests/core/services/test_workspace_scanner_service.py::test_scan_includes_unpushed_commits_when_requested`

### F28. Workspace scan results persist to an on-disk cache, version-gated, diff never persisted
WHAT: After every scan the workspace is saved to disk (schema-versioned; a mismatched or corrupt cache is silently ignored) so the next launch paints fast; per-file diffs are always dropped before saving.
WHERE: `src/local_changes_viewer/core/services/workspace_cache.py`
TESTS: `tests/core/services/test_workspace_cache.py::test_round_trip_preserves_workspace_with_multiple_repos_and_changes`, `tests/core/services/test_workspace_cache.py::test_diff_is_dropped_on_save_and_loads_back_as_none`, `tests/core/services/test_workspace_cache.py::test_load_workspace_returns_none_when_file_does_not_exist`, `tests/core/services/test_workspace_cache.py::test_load_workspace_returns_none_on_malformed_json`, `tests/core/services/test_workspace_cache.py::test_load_workspace_returns_none_on_version_mismatch`, `tests/core/services/test_workspace_cache.py::test_save_workspace_swallows_errors_when_write_fails`

## Filtering

Rules that narrow which folders, files, and repos show up in the tree.

### F29. Filtered Folders… dialog manages contains/equals folder-name filter rules
WHAT: A dialog to add and remove named folder filter rules that hide matching files from every repo's change list.
WHERE: `src/local_changes_viewer/gui/folder_filter_dialog.py`
TESTS: NONE

### F30. Folder filter rules hide files/dirs under any matching folder-name path segment
WHAT: A "contains" or "equals" rule hides a changed file or directory if any ancestor folder name matches, not just the leaf name.
WHERE: `src/local_changes_viewer/core/services/workspace_filter.py:11`
TESTS: `tests/core/services/test_workspace_filter.py::test_folder_filter_rule_equals_matches_full_folder_name_only`, `tests/core/services/test_workspace_filter.py::test_folder_filter_rule_contains_matches_substring`, `tests/core/services/test_workspace_filter.py::test_folder_filter_rule_checks_any_ancestor_folder_not_filename`, `tests/core/services/test_workspace_filter.py::test_folder_filter_rule_matches_untracked_directory_leaf_entry`, `tests/core/services/test_workspace_filter.py::test_folder_filter_rule_equals_removes_symlinked_directory_entry`

### F31. "Filter Out This File" context action adds an exact-file-path filter rule
WHAT: Right-clicking a file and choosing this hides exactly that one relative path, everywhere.
WHERE: `src/local_changes_viewer/core/domain/folder_filter_rule.py`
TESTS: `tests/core/services/test_workspace_filter.py::test_folder_filter_rule_file_path_matches_only_that_exact_file`

### F32. "Filter Out This Folder" context action adds an equals-mode filter rule
WHAT: Right-clicking a folder and choosing this hides every folder with that exact name, anywhere in the tree.
WHERE: `src/local_changes_viewer/gui/main_window.py:1290`
TESTS: NONE

### F33. Folder filter rules also hide whole repos located inside a filtered folder
WHAT: If a repo's own path lies under a filtered folder name, the whole repo and its filtered nested-repo descendants disappear from the tree.
WHERE: `src/local_changes_viewer/core/services/workspace_filter.py:22`
TESTS: `tests/core/services/test_workspace_filter.py::test_folder_filter_rule_excludes_nested_repo_under_filtered_folder`

### F34. "Ignore MD files" setting hides changes to .md files
WHAT: Toggling this setting removes all Markdown-file changes from the display, case-insensitively.
WHERE: `src/local_changes_viewer/core/services/workspace_filter.py:90`
TESTS: `tests/core/services/test_workspace_filter.py::test_ignore_md_files_filters_by_suffix_case_insensitive`

### F35. "Hide repos without changes" hides empty repos, keeping a parent with a changed nested worktree, and never hides worktrees themselves
WHAT: Regular (non-worktree) repos with zero changes drop out of the tree, unless a nested worktree beneath them still has changes. Worktree/nested repos (identified by `logical_parent_path` being set) are exempt from this rule entirely and always render regardless of the setting or whether they have changes, matching what "List Worktrees" already shows unconditionally -- so the user can always navigate to any worktree, clean or dirty. The "hidden — no changes" debug log line never fires for a worktree, since it isn't actually hidden. Worktrees have their own separate, independent hide-when-empty switch -- see "Hide empty worktrees" (F95).
WHERE: `src/local_changes_viewer/core/services/workspace_filter.py:157`
TESTS: `tests/core/services/test_workspace_filter.py::test_hide_repos_without_changes_drops_empty_repos`, `tests/core/services/test_workspace_filter.py::test_hide_repos_without_changes_after_ignoring_md_files`, `tests/core/services/test_workspace_filter.py::test_hide_repos_without_changes_never_hides_worktree_with_no_changes`, `tests/core/services/test_workspace_filter.py::test_hide_repos_without_changes_still_drops_empty_top_level_repo`, `tests/core/services/test_workspace_filter.py::test_no_hidden_log_for_worktree_with_no_changes`

### F36. Last-Commit-Time filter (toolbar slider / Ctrl+D toggle) hides files older than N minutes
WHAT: Limits the visible changes to files whose mtime is within the last N minutes; a missing file is treated as still-recent.
WHERE: `src/local_changes_viewer/core/services/workspace_filter.py:36`
TESTS: `tests/core/services/test_workspace_filter.py::test_max_age_minutes_zero_shows_all_changes`, `tests/core/services/test_workspace_filter.py::test_max_age_minutes_filters_out_old_files`, `tests/core/services/test_workspace_filter.py::test_max_age_minutes_includes_change_when_file_missing`

## Profiles

Named subsets of repos that can be switched between and managed independently of the folder filters.

### F37. Profiles restrict the displayed tree to a named subset of repos
WHAT: An active profile hides every repo not in its list; a nested worktree still shows if its logical parent is in the profile.
WHERE: `src/local_changes_viewer/core/services/workspace_filter.py:45`
TESTS: `tests/core/services/test_workspace_filter.py::test_profile_keeps_nested_worktree_child_of_matching_parent`

### F38. Display filtering never mutates the original scanned data
WHAT: Applying display filters produces a new Workspace/Repository tree; the underlying scanned Repository objects and their change lists are left untouched.
WHERE: `src/local_changes_viewer/core/services/workspace_filter.py:68`
TESTS: `tests/core/services/test_workspace_filter.py::test_does_not_mutate_original_repository_changes`

### F39. Filtering preserves a repo's logical-parent relationship
WHAT: A filtered Repository keeps its logical_parent_path so nested-worktree grouping still works after filtering.
WHERE: `src/local_changes_viewer/core/services/workspace_filter.py:101`
TESTS: `tests/core/services/test_workspace_filter.py::test_preserves_logical_parent_path_through_filtering`

### F40. Scanning itself can be scoped to only the active profile's repos
WHAT: With a profile active the scan skips git and GitHub work entirely for repos outside it, except worktrees inherited from an in-profile parent, which get git-scanned but not GitHub-fetched.
WHERE: `src/local_changes_viewer/core/services/workspace_scanner_service.py:108`
TESTS: `tests/core/services/test_workspace_scanner_service.py::test_scan_with_profile_repo_names_only_scans_matching_repos`, `tests/core/services/test_workspace_scanner_service.py::test_scan_with_profile_repo_names_keeps_worktree_of_matching_parent`, `tests/core/services/test_workspace_scanner_service.py::test_scan_with_profile_repo_names_skips_github_fetch_for_inherited_worktree`

### F41. Profiles… dialog manages named profiles
WHAT: A dialog for creating, renaming, and deleting named profiles and checking which discovered repos belong to each.
WHERE: `src/local_changes_viewer/gui/profile_dialog.py`
TESTS: NONE

### F42. Repo context-menu "Add to Profile" toggle and "New Profile…" shortcut
WHAT: Right-clicking a repo root can add or remove it from any existing profile, or create a new profile seeded with it.
WHERE: `src/local_changes_viewer/gui/main_window.py:1148`
TESTS: NONE

### F43. Active profile switch via View > Profile submenu, shown in the status bar
WHAT: A radio-style submenu of "No Profile" plus each defined profile; the active one's name shows in the status bar.
WHERE: `src/local_changes_viewer/gui/main_window.py:1116`
TESTS: NONE

## Git Change Detection

Reading a repo's actual git state: changed files, branch status, diffs, and commit history.

### F44. Detects modified/untracked/added/deleted/renamed/ignored files via git status
WHAT: The core change detection: every git status code maps to a ChangeType shown in the tree.
WHERE: `src/local_changes_viewer/core/infra/git_repo_adapter.py:29`
TESTS: `tests/core/infra/test_git_repo_adapter.py::test_list_changes_detects_modified_file`, `tests/core/infra/test_git_repo_adapter.py::test_list_changes_detects_untracked_file`, `tests/core/infra/test_git_repo_adapter.py::test_list_changes_detects_added_staged_file`, `tests/core/infra/test_git_repo_adapter.py::test_list_changes_detects_deleted_file`, `tests/core/infra/test_git_repo_adapter.py::test_list_changes_detects_renamed_staged_file`, `tests/core/infra/test_git_repo_adapter.py::test_list_changes_empty_for_clean_repo`

### F45. Untracked/ignored directories, including symlinked ones, report as a single directory entry
WHAT: An untracked or ignored folder — even a symlink pointing at one — shows as one directory row instead of being descended into.
WHERE: `src/local_changes_viewer/core/infra/git_repo_adapter.py:43`
TESTS: `tests/core/infra/test_git_repo_adapter.py::test_list_changes_detects_untracked_directory_as_single_directory_entry`, `tests/core/infra/test_git_repo_adapter.py::test_list_changes_classifies_symlinked_directory_as_directory`

### F46. Branch name plus ahead/behind-vs-upstream counts shown per repo
WHAT: Each repo reports its current branch and how many commits it is ahead of and behind its upstream.
WHERE: `src/local_changes_viewer/core/infra/git_repo_adapter.py:121`
TESTS: `tests/core/infra/test_git_repo_adapter.py::test_branch_status_with_no_upstream`, `tests/core/infra/test_git_repo_adapter.py::test_branch_status_ahead_and_behind`

### F47. Repo tooltip shows a guessed local parent branch and the remote default branch
WHAT: Best-effort "parent of branch" (nearest merge-base among local branches) and the remote's default branch name.
WHERE: `src/local_changes_viewer/core/infra/git_repo_adapter.py:245`
TESTS: `tests/core/infra/test_git_repo_adapter.py::test_branch_status_finds_local_parent_branch`, `tests/core/infra/test_git_repo_adapter.py::test_branch_status_parent_branch_none_when_no_other_branches`, `tests/core/infra/test_git_repo_adapter.py::test_branch_status_default_branch_falls_back_to_init_default_branch_config`, `tests/core/infra/test_git_repo_adapter.py::test_branch_status_default_branch_queried_live_from_remote`

### F48. Full-context unified diff computed for any change type
WHAT: Selecting a modified, deleted, untracked, renamed, or unpushed-commit file computes its diff against the right ref.
WHERE: `src/local_changes_viewer/core/infra/git_repo_adapter.py:269`
TESTS: `tests/core/infra/test_git_repo_adapter.py::test_compute_diff_for_modified_file`, `tests/core/infra/test_git_repo_adapter.py::test_compute_diff_for_deleted_file`, `tests/core/infra/test_git_repo_adapter.py::test_compute_diff_for_untracked_file`, `tests/core/infra/test_git_repo_adapter.py::test_compute_diff_for_renamed_file`, `tests/core/infra/test_git_repo_adapter.py::test_compute_diff_for_unpushed_commit_diffs_against_upstream`

### F49. "Ignore whitespace" setting recomputes the diff ignoring whitespace-only changes
WHAT: Toggling this setting re-diffs the current file with git's --ignore-all-space.
WHERE: `src/local_changes_viewer/core/infra/git_repo_adapter.py:273`
TESTS: `tests/core/infra/test_git_repo_adapter.py::test_compute_diff_ignore_whitespace`

### F50. Linked worktrees are discovered and excluded from their parent's own change list
WHAT: A worktree checkout under a repo does not show as a spurious untracked-directory change on the parent; it is shown separately instead.
WHERE: `src/local_changes_viewer/core/infra/git_repo_adapter.py:217`
TESTS: `tests/core/infra/test_git_repo_adapter.py::test_list_worktrees_returns_linked_worktree_paths`, `tests/core/infra/test_git_repo_adapter.py::test_list_worktrees_returns_empty_list_when_no_linked_worktrees`

### F51. "Show Log" dialog: browse recent commits, per-commit changed files, per-file diff
WHAT: A repo-root context action opens a dialog listing recent commits (count adjustable via slider), the files each commit touched, and each file's diff.
WHERE: `src/local_changes_viewer/gui/commit_log_dialog.py`
TESTS: `tests/core/infra/test_git_repo_adapter.py::test_get_recent_commits_returns_newest_first_with_limit`, `tests/core/infra/test_git_repo_adapter.py::test_get_commit_files_lists_changed_paths`, `tests/core/infra/test_git_repo_adapter.py::test_get_commit_file_diff_shows_added_content`

### F52. Repo discovery is limited to immediate children and never descends into a found repo
WHAT: Scanning a root treats the root itself as a repo if it is one, otherwise scans only its direct subdirectories, and never looks for repos within repos.
WHERE: `src/local_changes_viewer/core/infra/filesystem_scanner.py`
TESTS: `tests/core/infra/test_filesystem_scanner.py::test_finds_repo_at_root`, `tests/core/infra/test_filesystem_scanner.py::test_root_itself_is_a_repo`, `tests/core/infra/test_filesystem_scanner.py::test_does_not_descend_past_immediate_children`, `tests/core/infra/test_filesystem_scanner.py::test_finds_multiple_sibling_repos`, `tests/core/infra/test_filesystem_scanner.py::test_does_not_look_inside_a_found_repo_for_further_repos`, `tests/core/infra/test_filesystem_scanner.py::test_ignores_folders_without_git`, `tests/core/infra/test_filesystem_scanner.py::test_detects_git_as_file_for_submodules`, `tests/core/infra/test_filesystem_scanner.py::test_returns_empty_list_when_no_repos_found`

## GitHub Integration & Pull Requests

Connecting to GitHub and surfacing pull-request state for the repos in the tree.

### F53. Connect to GitHub… dialog authenticates and stores credentials
WHAT: A dialog collects a username and personal access token, validates it against GitHub's own reported login, and stores it for reuse.
WHERE: `src/local_changes_viewer/gui/github_connect_dialog.py`
TESTS: NONE

### F54. Disconnect GitHub clears stored credentials
WHAT: GitHub > Disconnect GitHub removes the stored token and username.
WHERE: `src/local_changes_viewer/gui/main_window.py:879`
TESTS: NONE

### F55. Auto-reconnects to GitHub on launch using stored credentials
WHAT: If a username and token were previously saved, the app reconnects silently at startup and shows a status message.
WHERE: `src/local_changes_viewer/gui/main_window.py:833`
TESTS: NONE

### F56. Repo row and tooltip can show its associated GitHub PR, resolved by branch name
WHAT: If the current branch has an open, closed, or merged PR on GitHub, its number and state show in the row and tooltip.
WHERE: `src/local_changes_viewer/core/infra/github_client.py:344`
TESTS: `tests/core/infra/test_github_client.py::test_find_pull_request_returns_none_when_no_matches`, `tests/core/infra/test_github_client.py::test_find_pull_request_returns_info_when_found`, `tests/core/infra/test_github_client.py::test_find_pull_request_lowercases_merged_state`, `tests/core/infra/test_github_client.py::test_find_pull_request_raises_github_error_on_graphql_errors`

### F57. PR lookups are cached with a TTL, and a terminal-state PR is reused across refreshes
WHAT: A branch's PR result is cached for 60s to avoid re-querying GitHub every refresh; a merged or closed PR for an unchanged branch is reused indefinitely.
WHERE: `src/local_changes_viewer/core/services/workspace_scanner_service.py:469`
TESTS: `tests/core/services/test_workspace_scanner_service.py::test_scan_reuses_cached_pr_for_terminal_state_and_unchanged_branch`, `tests/core/services/test_workspace_scanner_service.py::test_scan_still_fetches_when_previous_pr_is_open`, `tests/core/services/test_workspace_scanner_service.py::test_scan_still_fetches_when_branch_changed`, `tests/core/services/test_workspace_scanner_service.py::test_scan_reuses_open_pr_within_ttl_window`, `tests/core/services/test_workspace_scanner_service.py::test_scan_refetches_pr_once_ttl_expires`, `tests/core/services/test_workspace_scanner_service.py::test_scan_refetches_pr_immediately_when_branch_changes_within_ttl`

### F58. GitHub fetch errors degrade gracefully instead of failing the scan
WHAT: A PR-lookup failure (GraphQL or HTTP error) is logged and treated as "no PR", never crashing or aborting the workspace scan.
WHERE: `src/local_changes_viewer/core/services/workspace_scanner_service.py:514`
TESTS: `tests/core/services/test_workspace_scanner_service.py::test_scan_routes_github_pr_fetch_error_through_on_log_not_an_exception`

### F59. "My Open Pull Requests…" dialog and dockable PRs panel list the user's own open PRs
WHAT: Lists every open PR authored by the connected user across every GitHub-remote repo currently in the tree, grouped by repo.
WHERE: `src/local_changes_viewer/gui/my_pull_requests_dialog.py`
TESTS: `tests/core/infra/test_github_client.py::test_list_authored_open_pull_requests_empty_pairs_returns_empty_list`, `tests/core/infra/test_github_client.py::test_list_authored_open_pull_requests_returns_matches_filtered_by_author`, `tests/core/infra/test_github_client.py::test_list_authored_open_pull_requests_skips_repo_on_error`

### F60. Each PR row shows approval, unresolved threads, last reviewer, changed files, checks state
WHAT: Per-PR columns summarizing review status, refreshable individually.
WHERE: `src/local_changes_viewer/core/infra/github_client.py:146`
TESTS: `tests/core/infra/test_github_client.py::test_get_pull_request_review_status_returns_none_reviewer_when_no_reviews`

### F61. Double-click or right-click a PR row opens it in browser, or shows Info / Open Issues
WHAT: Double-click opens the PR URL; right-click offers Refresh, Info, Open Issues, and Copy URL. Info shows title, branches, status, dates, and last commenter; Open Issues lists unresolved review threads plus general comments.
WHERE: `src/local_changes_viewer/core/infra/github_client.py:245`
TESTS: `tests/core/infra/test_github_client.py::test_get_pull_request_details_returns_open_status_and_last_comment_writer`, `tests/core/infra/test_github_client.py::test_get_pull_request_details_reports_draft_status_and_no_comments`, `tests/core/infra/test_github_client.py::test_get_pull_request_open_threads_returns_only_unresolved_review_comments`, `tests/core/infra/test_github_client.py::test_get_pull_request_open_threads_returns_issue_comments`, `tests/core/infra/test_github_client.py::test_get_pull_request_open_threads_maps_review_states_to_comment_types`, `tests/core/infra/test_github_client.py::test_get_pull_request_open_threads_sorts_across_categories_newest_first`, `tests/core/infra/test_github_client.py::test_get_pull_request_open_threads_returns_empty_list_when_nothing_open`

### F62. "Open All" and "Copy All URLs" for a PR repository group
WHAT: Right-clicking a repo group in the PR list opens every one of its PRs in the browser, or copies all their URLs.
WHERE: `src/local_changes_viewer/gui/my_pull_requests_dialog.py:229`
TESTS: NONE

### F63. Recognizes https, ssh, and git@ GitHub remote URL forms
WHAT: Parses an origin remote URL into (owner, repo) across GitHub's various URL styles, including SSH host aliases.
WHERE: `src/local_changes_viewer/core/infra/github_client.py:68`
TESTS: `tests/core/infra/test_github_client.py::test_parse_github_owner_repo`

### F64. Token validation surfaces authentication errors
WHAT: get_authenticated_login confirms a token works and raises a GitHubError with detail on HTTP failure.
WHERE: `src/local_changes_viewer/core/infra/github_client.py:142`
TESTS: `tests/core/infra/test_github_client.py::test_get_authenticated_login`, `tests/core/infra/test_github_client.py::test_get_authenticated_login_raises_github_error_on_http_error`

## Diff Viewing & Editing

Rendering and interacting with a selected file's diff, including in-place editing.

### F65. Unified diff view with syntax highlighting and a toggleable line-number gutter
WHAT: The default diff view renders +/-/context lines with language-aware syntax coloring and old/new line numbers in a gutter.
WHERE: `src/local_changes_viewer/gui/diff_view/unified_view.py`
TESTS: NONE

### F66. Side-by-side diff view with syncable left/right scrolling
WHAT: An alternate two-pane diff view whose scroll can be locked together via "Sync side-by-side scroll".
WHERE: `src/local_changes_viewer/gui/diff_view/side_by_side_view.py`
TESTS: NONE

### F67. Long unchanged context runs fold to a "click to expand" marker
WHAT: A big run of unchanged context lines collapses to one clickable marker line in both diff views; clicking expands it.
WHERE: `src/local_changes_viewer/core/services/context_folding.py`
TESTS: `tests/core/services/test_context_folding.py::test_short_context_run_stays_visible`, `tests/core/services/test_context_folding.py::test_long_context_run_between_changes_folds_middle_keeping_margins`, `tests/core/services/test_context_folding.py::test_long_context_run_at_file_start_has_no_head_margin`, `tests/core/services/test_context_folding.py::test_long_context_run_at_file_end_has_no_tail_margin`

### F68. Side-by-side pairs removed and added lines row-by-row for substitutions
WHAT: In side-by-side view a run of removed lines lines up against a same-length run of added lines row by row; uneven runs leave the shorter side blank.
WHERE: `src/local_changes_viewer/core/services/diff_pairing.py`
TESTS: `tests/core/services/test_diff_pairing.py::test_pairs_context_line_on_both_sides`, `tests/core/services/test_diff_pairing.py::test_pairs_equal_length_removed_and_added_runs_row_by_row`, `tests/core/services/test_diff_pairing.py::test_pairs_unequal_length_runs_leaving_unmatched_side_none`, `tests/core/services/test_diff_pairing.py::test_pair_substitution_indices_matches_same_row_removed_added`

### F69. Intraline diff highlights the exact changed substring within a modified line
WHAT: Within a paired removed/added line, only the actually-changed word or substring is highlighted, not the whole line.
WHERE: `src/local_changes_viewer/core/services/intraline_diff.py`
TESTS: `tests/core/services/test_intraline_diff.py::test_single_word_change_in_long_line_highlights_only_that_word`, `tests/core/services/test_intraline_diff.py::test_identical_text_produces_no_ranges`, `tests/core/services/test_intraline_diff.py::test_completely_different_text_covers_whole_string`

### F70. Diff toolbar: view-mode toggle, prev/next change, refresh, line numbers, font size
WHAT: Toggle side-by-side/unified, jump between changes (a maximal run of added/removed lines, not the underlying git hunk -- `compute_diff`'s `--unified=100000` usually collapses a whole file's diff into a single hunk, so jumping by hunk found only one target), force-reload the diff, toggle gutters, and zoom in and out, also via View menu and Ctrl+= / Ctrl+-.
WHERE: `src/local_changes_viewer/gui/diff_view/diff_view_widget.py`
TESTS: `tests/gui/test_diff_view.py::test_successive_next_change_clicks_reach_successive_changes_in_diff_mode`, `tests/core/services/test_diff_pairing.py::test_change_runs_finds_multiple_separate_runs_inside_a_single_hunk`

### F71. In-place file editing from the side-by-side view, with save and discard-on-navigate
WHAT: An Edit toggle makes the right pane of side-by-side view editable, preserving original encoding and line endings; both panes switch from the folded diff view to their own full source (right pane the live on-disk/as-typed file, left pane the reconstructed pre-change original) with real file line numbers (1..N, live as you type on the right) instead of diff-row numbers, so scrolling the two together lines up whole files instead of folded hunks; removed/added lines stay highlighted at their real line numbers. Prev/Next change in edit mode scrolls each pane to that change's own real line (left to its old line, right to its new line) with sync-scroll suppressed for the jump and keyboard focus left in the right pane. Save (the toolbar button, or Cmd+S/Ctrl+S via the platform-standard save shortcut, which drives that same button) writes the right pane back; navigating away or closing with unsaved edits prompts to discard.
WHERE: `src/local_changes_viewer/gui/diff_view/side_by_side_view.py:362`
TESTS: `tests/gui/test_diff_view.py::test_entering_edit_mode_shows_real_sequential_line_numbers`, `tests/gui/test_diff_view.py::test_typing_a_new_line_keeps_gutter_sequential`, `tests/gui/test_diff_view.py::test_exiting_edit_mode_restores_diff_row_line_numbers`, `tests/gui/test_diff_view.py::test_entering_edit_mode_expands_left_pane_to_full_original_source`, `tests/gui/test_diff_view.py::test_exiting_edit_mode_restores_left_pane_folded_diff_rendering`, `tests/gui/test_diff_view.py::test_cmd_s_shortcut_saves_through_the_same_path_as_the_save_button`, `tests/gui/test_diff_view.py::test_next_change_in_edit_mode_lands_each_pane_on_its_own_real_line`

### F72. File-info status label shows detected encoding and line-ending
WHAT: Selecting a file shows its detected text encoding (UTF-8, UTF-8 BOM, Latin-1, Binary, Unknown) and line-ending style (LF, CRLF, Mixed, N-A) in the status bar.
WHERE: `src/local_changes_viewer/core/services/file_info.py`
TESTS: `tests/core/services/test_file_info.py::test_detects_lf`, `tests/core/services/test_file_info.py::test_detects_crlf`, `tests/core/services/test_file_info.py::test_detects_mixed_line_endings`, `tests/core/services/test_file_info.py::test_detects_utf8`, `tests/core/services/test_file_info.py::test_detects_utf8_bom`, `tests/core/services/test_file_info.py::test_detects_binary`, `tests/core/services/test_file_info.py::test_detects_latin1_fallback`

### F73. "Copy Diff" copies the unified diff text to the clipboard
WHAT: Copies the selected file's diff, formatted as a standard unified-diff text block, to the clipboard.
WHERE: `src/local_changes_viewer/core/services/diff_formatting.py`
TESTS: `tests/core/services/test_diff_formatting.py::test_formats_hunks_with_correct_prefixes`, `tests/core/services/test_diff_formatting.py::test_includes_file_header_when_file_path_given`

### F74. Copy File Path / Copy File Name / Open in Default Editor / Reveal in Finder
WHAT: Four Actions-menu commands operating on the currently selected file.
WHERE: `src/local_changes_viewer/gui/main_window.py:1194`
TESTS: NONE

### F75. "Always reload fresh diff" setting bypasses the per-selection diff cache
WHAT: When enabled, selecting a file always re-reads its diff from disk instead of reusing a previously computed one.
WHERE: `src/local_changes_viewer/gui/main_window.py:289`
TESTS: NONE

### F76. Diff view mode, window geometry, and splitter sizes persist across restarts
WHAT: The unified/side-by-side choice, window size and position, and the main splitter's pane sizes are restored on next launch.
WHERE: `src/local_changes_viewer/gui/main_window.py:420`
TESTS: NONE

### F77. Ctrl+F opens an inline find bar in the edit pane
WHAT: While editing (side-by-side view, right pane focused), Ctrl+F opens a non-modal find bar at the bottom of the view; typing searches incrementally from the cursor, Enter/Next and Shift+Enter/Prev step through matches and wrap at the document ends, a non-match turns the input red, and Escape closes the bar and returns focus to the pane. Inert outside edit mode or when focus is elsewhere.
WHERE: `src/local_changes_viewer/gui/diff_view/side_by_side_view.py:249`
TESTS: `tests/gui/test_diff_view.py::test_ctrl_f_shows_find_bar_only_in_edit_mode`, `tests/gui/test_diff_view.py::test_find_next_advances_through_occurrences_and_wraps`, `tests/gui/test_diff_view.py::test_non_matching_search_gives_feedback_and_leaves_cursor_put`, `tests/gui/test_diff_view.py::test_escape_closes_bar_and_returns_focus_to_pane`

### F78. Ctrl+G jumps to a line number in the edit pane via a popup dialog
WHAT: While editing (side-by-side view, right pane focused), Ctrl+G opens a simple modal "Go to Line" input dialog ranged 1..blockCount (so out-of-range input is clamped by the dialog itself); accepting moves the cursor to that line's start and centers it, Cancel is a no-op. Inert outside edit mode or when focus is elsewhere.
WHERE: `src/local_changes_viewer/gui/diff_view/side_by_side_view.py:252`
TESTS: `tests/gui/test_diff_view.py::test_ctrl_g_opens_dialog_with_full_line_range_and_jumps_on_accept`, `tests/gui/test_diff_view.py::test_goto_dialog_cancel_leaves_cursor_untouched`

## Settings, Persistence & Logging

App-wide settings, restored window/view state, credential storage, and logging.

### F79. Settings-menu toggles persist and restore without re-triggering a scan or refresh
WHAT: All checkable Settings-menu items are saved and restored, and restoring them at startup must not fire a redundant scan or display refresh.
WHERE: `src/local_changes_viewer/gui/settings.py`
TESTS: `tests/gui/test_main_window.py::test_only_one_scan_starts_during_window_init`, `tests/gui/test_main_window.py::test_display_filter_toggle_does_not_refresh_during_settings_restore`

### F80. Log Level… dialog sets and persists app log verbosity
WHAT: Choose ERROR, WARNING, INFO, DEBUG, or VERBOSE; persists and takes effect immediately.
WHERE: `src/local_changes_viewer/gui/applog.py`
TESTS: NONE

### F81. Tooltip Font Size… dialog sets and persists the app-wide tooltip font size
WHAT: Sets a custom point size for all Qt tooltips app-wide; 0 means system default.
WHERE: `src/local_changes_viewer/gui/main_window.py:808`
TESTS: NONE

### F82. Help menu: dialogs documenting Settings, Actions, PR panel, and toolbar buttons
WHAT: Four static help dialogs describing menu items and toolbar buttons.
WHERE: `src/local_changes_viewer/gui/help_dialog.py`
TESTS: NONE

### F83. Last-opened root folder is remembered and reopened automatically at launch
WHAT: The app reopens whatever folder was open when it last closed.
WHERE: `src/local_changes_viewer/gui/main_window.py:415`
TESTS: NONE

### F84. GitHub credentials are stored in a local token file with restrictive permissions
WHAT: Tokens are written to ~/.local-changes-viewer/github_token.json chmod 0600, keyed by username, and can be deleted per user.
WHERE: `src/local_changes_viewer/gui/github_auth.py`
TESTS: `tests/gui/test_github_auth.py::test_set_and_get_token_round_trips`, `tests/gui/test_github_auth.py::test_get_token_returns_none_when_file_missing`, `tests/gui/test_github_auth.py::test_delete_token_removes_only_that_user`, `tests/gui/test_github_auth.py::test_delete_token_is_noop_when_user_not_present`, `tests/gui/test_github_auth.py::test_set_token_writes_file_with_restrictive_permissions`

### F85. In-memory and on-disk app log, filtered by configured log level
WHAT: Every logged message is kept in memory for the "App Log" copy action and appended to a log file under ~/Library/Logs/local-changes-viewer/, filtered by the current log level.
WHERE: `src/local_changes_viewer/gui/applog.py`
TESTS: NONE

### F86. "App Log" action copies the full in-memory app log to the clipboard
WHAT: Actions > App Log dumps every logged line to the clipboard for bug reports.
WHERE: `src/local_changes_viewer/gui/main_window.py:1181`
TESTS: NONE

### F87. Edit is disabled, with an explaining tooltip, for a diff that can't be safely edited
WHAT: The Edit button is disabled -- with a tooltip naming the reason -- for an already-committed-but-unpushed change (the file on disk no longer matches the shown upstream-to-HEAD diff), a deleted file, or a folder; it is enabled with its normal tooltip for an editable working-tree change. This belongs to the Diff Viewing & Editing category (see `## Categories`); it is numbered last because renumbering existing features is not allowed.
WHERE: `src/local_changes_viewer/gui/main_window.py`
TESTS: `tests/gui/test_diff_view.py::test_edit_disabled_with_explaining_tooltip_for_committed_diff`, `tests/gui/test_diff_view.py::test_edit_enabled_with_normal_tooltip_for_working_tree_diff`

### F88. View > Settings… dialog lists every user-configurable setting with an explanation
WHAT: A single tabbed dialog, one tab per group -- Scanning / Display / Filters & Profiles / Diagnostics -- shows every user-configurable setting alongside a plain-English explanation and the right control (checkbox, spinbox, dropdown, or button); the tallest tab fits at the dialog's default size with no scrolling. Every control drives the same existing QAction or persist+apply helper the corresponding menu item already uses, so behavior is identical to using the menus; there is no OK/Cancel, changes apply instantly. This belongs to the Settings, Persistence & Logging category (see `## Categories`); it is numbered last because renumbering existing features is not allowed.
WHERE: `src/local_changes_viewer/gui/settings_dialog.py`
TESTS: `tests/gui/test_settings_dialog.py::test_dialog_reflects_current_action_states`, `tests/gui/test_settings_dialog.py::test_toggling_ignore_md_checkbox_flips_action_and_refreshes_display`, `tests/gui/test_settings_dialog.py::test_toggling_watch_file_changes_checkbox_flips_action_and_persists_setting`, `tests/gui/test_settings_dialog.py::test_auto_refresh_spinbox_persists_and_applies_interval`, `tests/gui/test_settings_dialog.py::test_tooltip_font_size_spinbox_persists_value`, `tests/gui/test_settings_dialog.py::test_log_level_combo_persists_value`, `tests/gui/test_settings_dialog.py::test_constructing_dialog_does_not_mutate_any_setting`, `tests/gui/test_settings_dialog.py::test_folder_filter_summary_reflects_current_rules`, `tests/gui/test_settings_dialog.py::test_manage_folder_filters_button_refreshes_summary_after_dialog_closes`, `tests/gui/test_settings_dialog.py::test_profiles_summary_reflects_current_profiles_and_active_profile`, `tests/gui/test_settings_dialog.py::test_manage_profiles_button_refreshes_summary_after_dialog_closes`, `tests/gui/test_settings_dialog.py::test_view_menu_has_settings_action_that_opens_dialog`

### F89. Repo-root context menu: "List Worktrees" dialog with per-worktree delete and change viewer
WHAT: A repo-root context action opens a dialog table of that repo's linked worktrees, each row showing its path, branch, last commit-or-modification time, whether it has unpushed changes (uncommitted or committed-but-unpushed; ignored paths such as the worktree's own `node_modules` never count, since they can never be pushed and the change viewer hides them), and a best-effort creation time. Clicking a column header sorts the table by that column, text-ascending on first click and toggling to descending on each repeat click of the same header. Double-clicking a row opens its "Show Changes" dialog. Right-clicking a row opens a context menu with "Delete" (removes the worktree from disk, with a force-delete fallback if it has uncommitted/unpushed changes), "Show Changes" (opens a second dialog listing that worktree's modified files, each tagged "Committed" or "Not committed" per file, with a tooltip showing the file's full entry when hovered, where selecting a file renders its diff in a toggleable unified/side-by-side view), and "Copy Path" (copies the worktree's full filesystem path to the clipboard). Selecting a collapsed untracked-directory entry (e.g. `node_modules`, which `git status` reports as one path rather than one per file) shows a bounded file-count summary instead of erroring; selecting an untracked binary file or one that vanished from disk since the scan shows a one-line placeholder instead of a crash. This belongs to the Workspace Tree & Navigation category (see `## Categories`); it is numbered last because renumbering existing features is not allowed.
WHERE: `src/local_changes_viewer/gui/worktrees_dialog.py`, `src/local_changes_viewer/gui/worktree_changes_dialog.py`, `src/local_changes_viewer/core/infra/git_repo_adapter.py`
TESTS: `tests/core/infra/test_git_repo_adapter.py::test_list_worktree_details_reports_branch_and_clean_pushed_state`, `tests/core/infra/test_git_repo_adapter.py::test_list_worktree_details_flags_uncommitted_changes_as_unpushed`, `tests/core/infra/test_git_repo_adapter.py::test_list_worktree_details_flags_commits_ahead_of_upstream_as_unpushed`, `tests/core/infra/test_git_repo_adapter.py::test_list_worktree_details_skips_worktree_path_that_no_longer_exists_on_disk`, `tests/core/infra/test_git_repo_adapter.py::test_has_unpushed_changes_false_for_clean_pushed_branch`, `tests/core/infra/test_git_repo_adapter.py::test_has_unpushed_changes_false_when_only_ignored_paths_are_present`, `tests/core/infra/test_git_repo_adapter.py::test_has_unpushed_changes_true_with_no_upstream_configured`, `tests/core/infra/test_git_repo_adapter.py::test_list_changes_reports_local_only_commits_with_no_upstream_configured`, `tests/core/infra/test_git_repo_adapter.py::test_remove_worktree_deletes_it_from_worktree_list`, `tests/core/infra/test_git_repo_adapter.py::test_remove_worktree_force_removes_worktree_with_uncommitted_changes`, `tests/core/infra/test_git_repo_adapter.py::test_compute_diff_for_untracked_directory_does_not_crash`, `tests/core/infra/test_git_repo_adapter.py::test_compute_diff_for_untracked_binary_file_shows_placeholder`, `tests/core/infra/test_git_repo_adapter.py::test_compute_diff_for_untracked_file_deleted_after_scan`, `tests/gui/test_worktrees_dialog.py::test_dialog_lists_worktrees_with_details`, `tests/gui/test_worktrees_dialog.py::test_dialog_shows_placeholder_when_no_worktrees`, `tests/gui/test_worktrees_dialog.py::test_delete_button_removes_worktree_after_confirmation`, `tests/gui/test_worktrees_dialog.py::test_delete_button_does_nothing_when_confirmation_declined`, `tests/gui/test_worktrees_dialog.py::test_delete_button_offers_force_delete_when_removal_fails`, `tests/gui/test_worktrees_dialog.py::test_reload_tracks_worktree_for_each_row`, `tests/gui/test_worktrees_dialog.py::test_context_menu_show_changes_opens_worktree_changes_dialog`, `tests/gui/test_worktrees_dialog.py::test_double_click_row_opens_worktree_changes_dialog`, `tests/gui/test_worktrees_dialog.py::test_double_click_ignores_click_outside_any_row`, `tests/gui/test_worktrees_dialog.py::test_context_menu_copy_path_sets_clipboard_to_worktree_path`, `tests/gui/test_worktrees_dialog.py::test_context_menu_ignores_click_outside_any_row`, `tests/gui/test_worktrees_dialog.py::test_clicking_header_sorts_table_and_toggles_order_on_repeat_click`, `tests/gui/test_worktrees_dialog.py::test_row_lookup_follows_worktree_after_sorting`, `tests/gui/test_worktree_changes_dialog.py::test_dialog_lists_changes_with_committed_status`, `tests/gui/test_worktree_changes_dialog.py::test_dialog_filters_out_ignored_files`, `tests/gui/test_worktree_changes_dialog.py::test_dialog_shows_no_files_when_no_changes`, `tests/gui/test_worktree_changes_dialog.py::test_selecting_a_file_computes_diff`, `tests/gui/test_worktree_changes_dialog.py::test_diff_toggle_switches_stack_index`, `tests/gui/test_worktree_changes_dialog.py::test_file_list_item_tooltip_shows_full_text`

### F90. Crash diagnostics: fatal-signal stack dump and uncaught-exception logging
WHAT: At startup, `faulthandler` is enabled to dump the Python stack of the crashing thread to `~/Library/Logs/local-changes-viewer/crash.log` on a fatal signal (e.g. a native segfault, which otherwise leaves no Python-level trace at all), and any uncaught Python exception is written to the app log (ERROR level) with its full traceback before falling through to the default interpreter behavior. This belongs to the Settings, Persistence & Logging category (see `## Categories`); it is numbered last because renumbering existing features is not allowed.
WHERE: `src/local_changes_viewer/main.py`
TESTS: `tests/test_main.py::test_enable_crash_diagnostics_enables_faulthandler`, `tests/test_main.py::test_uncaught_exception_hook_logs_to_applog`

### F91. Worktree repo-root context menu: "Start" / "Stop" the dev server
WHAT: Right-clicking a repo-root row that is a git worktree (`logical_parent_path` set) offers "Start" and "Stop" alongside the existing Refresh Repo/Show Log/List Worktrees actions. "Start" opens a new Terminal.app window in that worktree's directory and runs `nvm use && npm install && npm start`, tracking the window's id; "Stop" signals every process attached to that window's tty (without closing the window, which would otherwise trigger Terminal's "still running" confirmation) and forgets the tracked window. "Start" is disabled while already running for that worktree, and "Stop" is disabled otherwise. This belongs to the Workspace Tree & Navigation category (see `## Categories`); it is numbered last because renumbering existing features is not allowed.
WHERE: `src/local_changes_viewer/core/services/worktree_terminal_service.py`
TESTS: `tests/core/services/test_worktree_terminal_service.py::test_start_worktree_process_returns_window_id_and_embeds_start_command`, `tests/core/services/test_worktree_terminal_service.py::test_start_worktree_process_raises_on_failure`, `tests/core/services/test_worktree_terminal_service.py::test_stop_worktree_process_signals_processes_on_the_window_tty`, `tests/core/services/test_worktree_terminal_service.py::test_stop_worktree_process_noops_when_window_already_closed`

### F92. "List Worktrees" loads in the background behind a "Reading data ..." dialog
WHAT: Listing a repo's worktrees runs several git commands per worktree, which used to block the whole app for a few seconds; it now runs on a background thread while a modal "Reading data ..." dialog (no Cancel button, not user-dismissable) covers the wait, closing itself the instant the data arrives -- whether the load succeeds or fails. This applies both to the dialog's initial open and to the reload that follows a successful worktree delete. This belongs to the Workspace Tree & Navigation category (see `## Categories`); it is numbered last because renumbering existing features is not allowed.
WHERE: `src/local_changes_viewer/gui/worktrees_dialog.py`, `src/local_changes_viewer/gui/workers/worktree_details_worker.py`
TESTS: `tests/gui/test_worktrees_dialog.py::test_reload_shows_busy_dialog_while_pending_then_populates_table`, `tests/gui/test_worktrees_dialog.py::test_reload_error_closes_busy_dialog_and_shows_warning`, `tests/gui/test_worktrees_dialog.py::test_post_delete_reload_also_goes_through_the_worker`

### F93. "Create patch" context-menu action, with a file-selection step, offering clipboard or save-to-disk
WHAT: Right-clicking a changed file, a collapsed untracked-directory row, a plain folder, or a repo root offers "Create patch". This first resolves every tracked (staged and unstaged) and untracked change under that target -- a folder/root's scope never spills into a nested repo underneath it -- and, if any exist, opens a file-selection dialog listing each one (repo-relative path plus its change type) with a checkbox, all checked by default; "Select All"/"Deselect All" buttons are provided, and OK is disabled whenever nothing is checked. The dialog is shown even for a single-file target (a one-row list). Cancelling it aborts the whole action silently -- no patch, no destination chooser. Accepting it builds a raw `git apply`-able unified diff covering only the checked files. An empty result (nothing in scope at all) shows an informational message and skips the dialog entirely; otherwise the usual chooser offers "Copy to Clipboard", "Save to Disk..." (a native save dialog pre-filled with `<name>.patch`, `*.patch` filter), or Cancel. A collapsed untracked directory (e.g. `node_modules/`) is offered as a single row, not expanded to its individual files -- listing tens of thousands of files would make opening the dialog itself slow; the existing expand-to-real-files logic still runs later, at patch-build time, if that row stays checked. This belongs to the Workspace Tree & Navigation category (see `## Categories`); it is numbered last because renumbering existing features is not allowed.
WHERE: `src/local_changes_viewer/gui/main_window.py`, `src/local_changes_viewer/gui/patch_file_selection_dialog.py`, `src/local_changes_viewer/core/services/patch_service.py`, `src/local_changes_viewer/core/infra/git_repo_adapter.py`
TESTS: `tests/core/infra/test_git_repo_adapter.py::test_build_patch_covers_modified_tracked_file`, `tests/core/infra/test_git_repo_adapter.py::test_build_patch_covers_staged_change`, `tests/core/infra/test_git_repo_adapter.py::test_build_patch_covers_untracked_new_file`, `tests/core/infra/test_git_repo_adapter.py::test_build_patch_for_folder_covers_every_changed_file_under_it`, `tests/core/infra/test_git_repo_adapter.py::test_build_patch_with_a_subset_of_tracked_paths_excludes_the_rest_and_applies_cleanly`, `tests/core/infra/test_git_repo_adapter.py::test_build_patch_expands_a_collapsed_untracked_directory_entry`, `tests/core/infra/test_git_repo_adapter.py::test_build_patch_returns_empty_string_when_nothing_to_patch`, `tests/core/infra/test_git_repo_adapter.py::test_build_patch_skips_binary_untracked_file_without_raising`, `tests/core/infra/test_git_repo_adapter.py::test_build_patch_output_applies_cleanly_with_git_apply_check`, `tests/core/infra/test_git_repo_adapter.py::test_build_patch_for_a_single_folder_applies_cleanly_with_git_apply_check`, `tests/core/services/test_patch_service.py::test_files_in_scope_for_whole_repo_includes_every_change_sorted_by_path`, `tests/core/services/test_patch_service.py::test_files_in_scope_excludes_changes_outside_the_target_folder`, `tests/core/services/test_patch_service.py::test_files_in_scope_excludes_changes_outside_a_single_file_target`, `tests/core/services/test_patch_service.py::test_files_in_scope_excludes_ignored_paths`, `tests/core/services/test_patch_service.py::test_build_patch_includes_only_the_selected_paths`, `tests/core/services/test_patch_service.py::test_build_patch_can_include_both_a_selected_tracked_and_untracked_file`, `tests/core/services/test_patch_service.py::test_build_patch_returns_empty_string_when_selection_is_empty`, `tests/core/services/test_patch_service.py::test_build_patch_returns_empty_string_for_a_clean_repo`, `tests/gui/test_patch_file_selection_dialog.py::test_all_rows_checked_on_open`, `tests/gui/test_patch_file_selection_dialog.py::test_unchecking_a_row_excludes_it_from_the_selection`, `tests/gui/test_patch_file_selection_dialog.py::test_deselect_all_button_unchecks_every_row_and_disables_ok`, `tests/gui/test_patch_file_selection_dialog.py::test_select_all_button_rechecks_every_row_and_reenables_ok`, `tests/gui/test_patch_file_selection_dialog.py::test_ok_disabled_the_moment_the_last_checked_row_is_unchecked`, `tests/gui/test_patch_file_selection_dialog.py::test_cancel_button_rejects_the_dialog`, `tests/gui/test_patch_file_selection_dialog.py::test_single_file_target_still_shows_one_checked_row`, `tests/gui/test_main_window.py::test_create_patch_action_reachable_and_wired_for_a_file_row`, `tests/gui/test_main_window.py::test_create_patch_action_reachable_and_wired_for_a_plain_folder_row`, `tests/gui/test_main_window.py::test_create_patch_action_reachable_and_wired_for_a_repo_root_row`, `tests/gui/test_main_window.py::test_create_patch_deselecting_a_file_keeps_its_hunks_out_of_the_clipboard_patch`, `tests/gui/test_main_window.py::test_create_patch_cancel_in_selection_dialog_aborts_with_no_clipboard_write_and_no_file_picker`, `tests/gui/test_main_window.py::test_present_patch_copy_to_clipboard_sets_clipboard_and_status`, `tests/gui/test_main_window.py::test_present_patch_save_to_disk_writes_file_at_chosen_path`, `tests/gui/test_main_window.py::test_present_patch_save_dialog_cancelled_writes_nothing`, `tests/gui/test_main_window.py::test_present_patch_cancel_writes_nothing_and_leaves_clipboard_untouched`, `tests/gui/test_main_window.py::test_present_patch_empty_patch_shows_info_and_never_opens_chooser`

### F94. Collapse-all/expand-all folder icon buttons beside the filter box
WHAT: Two small icon buttons -- sized to match the filter box's height -- sit to the right of the "Filter by path…" box, above the Folder Tree/All Changes tabs: an up-arrow ("Collapse all folders") and a down-arrow ("Expand all folders"). Both call the same `RepoTreeView.collapse_all()`/`expand_all()` methods the View menu's Collapse All/Expand All actions use, so a click here persists into the same collapsed-node-key settings the tree replays on the next rebuild instead of being silently undone by it. They only affect the Folder Tree tab's tree -- the "All Changes" tab is a flat list with no folder concept. This belongs to the Workspace Tree & Navigation category (see `## Categories`); it is numbered last because renumbering existing features is not allowed.
WHERE: `src/local_changes_viewer/gui/main_window.py`
TESTS: `tests/gui/test_main_window.py::test_collapse_and_expand_all_folders_buttons_toggle_real_tree_rows`

### F95. "Hide empty worktrees" checkbox hides worktrees with no changes, independent of "Hide repos without changes"
WHAT: A checkbox in the folder-tree filter row (between the path filter box and the collapse/expand-all arrows), off by default so a user who never touches it sees no change from before this feature existed. Checked, it hides any worktree/nested repo (`logical_parent_path` set) that has no changes anywhere in its own subtree, using the same has-changes predicate as "Hide repos without changes" (F35). It acts only on worktrees -- a regular top-level repo with no changes is untouched by this checkbox and keeps obeying F35 exactly as before, and F35's own worktree exemption is untouched by this checkbox being off. Toggling it re-applies the current display filters to the already-scanned workspace and updates the tree immediately, with no rescan. This belongs to the Filtering category (see `## Categories`); it is numbered last because renumbering existing features is not allowed.
WHERE: `src/local_changes_viewer/gui/main_window.py:216`, `src/local_changes_viewer/core/services/workspace_filter.py:156`
TESTS: `tests/core/services/test_workspace_filter.py::test_hide_changeless_worktrees_defaults_to_off_and_changes_nothing`, `tests/core/services/test_workspace_filter.py::test_hide_changeless_worktrees_hides_only_the_changeless_worktree`, `tests/core/services/test_workspace_filter.py::test_hide_changeless_worktrees_composes_with_hide_repos_without_changes`, `tests/core/services/test_workspace_filter.py::test_hide_changeless_worktrees_keeps_worktree_with_changed_descendant`, `tests/core/services/test_workspace_filter.py::test_hide_changeless_worktrees_logs_worktree_specific_hidden_reason`, `tests/gui/test_main_window.py::test_hide_empty_worktrees_checkbox_toggles_changeless_worktree_visibility`, `tests/gui/test_main_window.py::test_hide_empty_worktrees_checkbox_composes_with_hide_repos_without_changes`, `tests/gui/test_main_window.py::test_hide_empty_worktrees_checkbox_persists_across_restart`

### F96. Diff panes' right-click menu: "Copy Location" copies `<absolute path>:<line>`
WHAT: Right-clicking inside any diff pane -- the unified view, or either side of the side-by-side view -- adds a "Copy Location" item below a separator, after the stock Copy/Select All items `QPlainTextEdit.createStandardContextMenu()` already provides. Triggering it puts `<absolute file path>:<line>` on the clipboard, with no quotes, no "line" word, and no trailing punctuation, so it can be pasted straight into a Claude session as a code anchor. The line number always comes from the clicked row's own `DiffLine.old_lineno`/`new_lineno`, never the visual row index: in the unified view, a REMOVED row reports `old_lineno` and an ADDED or CONTEXT row reports `new_lineno`; in the side-by-side view, the left (old) pane always reports `old_lineno` and the right (new) pane always `new_lineno`, whatever the row's kind. The action stays visible but disabled -- never copying a wrong or empty value -- for a row with no real file line at all: a unified-view hunk-header row, a collapsed "N unchanged lines" fold marker in either view, a side-by-side blank filler row left opposite an unpaired ADDED/REMOVED line on the other side, or any diff for which no absolute file path was resolved (the same condition that disables "Edit", see F87). This belongs to the Diff Viewing & Editing category (see `## Categories`); it is numbered last because renumbering existing features is not allowed.
WHERE: `src/local_changes_viewer/gui/diff_view/unified_view.py`, `src/local_changes_viewer/gui/diff_view/side_by_side_view.py`, `src/local_changes_viewer/gui/diff_view/diff_view_widget.py`
TESTS: `tests/gui/test_diff_view.py::test_unified_view_copy_location_reports_added_and_removed_real_line_numbers`, `tests/gui/test_diff_view.py::test_unified_view_copy_location_disabled_for_collapsed_fold_marker_row`, `tests/gui/test_diff_view.py::test_side_by_side_left_pane_copy_location_reports_old_lineno_and_disables_for_blank_row`, `tests/gui/test_diff_view.py::test_side_by_side_right_pane_copy_location_reports_new_lineno_and_disables_for_blank_row`

### F97. "Apply patch..." repo-root context-menu action, from a file or the clipboard, with a file-selection step
WHAT: Right-clicking a repo root offers "Apply patch...", the inverse of "Create patch". It first asks the source: "From File…" (a native open dialog filtered to `*.patch *.diff`) or "From Clipboard" (a small editable box, pre-filled from the system clipboard, so the pasted text can be corrected before use) -- or Cancel, which aborts silently. The chosen text is parsed for every `diff --git` file header it contains (mapping `new file mode`/`deleted file mode`/plain headers to added/deleted/modified, and collapsing a `rename from`/`rename to` pair to one modified entry on the new path; an unparseable header is skipped rather than aborting the whole patch), then shown in the same file-selection dialog "Create patch" uses -- one row per file, all checked by default, Select All/Deselect All, OK disabled with nothing checked. An empty parse result (nothing recognized in the text) shows an informational message and skips the dialog entirely. Accepting the dialog applies the patch restricted to only the checked files (`git apply --include=<path>` per selection, fed on stdin, never via a temp file), after a `--check` dry run confirms it would apply cleanly -- a patch that fails the dry run raises before touching the working tree, reported to the user as an error dialog. A successful apply shows how many files were applied and refreshes the repo the same way the existing "Refresh Repo" action does. This belongs to the Workspace Tree & Navigation category (see `## Categories`); it is numbered last because renumbering existing features is not allowed.
WHERE: `src/local_changes_viewer/gui/main_window.py`, `src/local_changes_viewer/gui/patch_text_input_dialog.py`, `src/local_changes_viewer/gui/patch_file_selection_dialog.py`, `src/local_changes_viewer/core/services/patch_service.py`, `src/local_changes_viewer/core/infra/git_repo_adapter.py`
TESTS: `tests/core/services/test_patch_service.py::test_parse_patch_modified_file`, `tests/core/services/test_patch_service.py::test_parse_patch_new_file`, `tests/core/services/test_patch_service.py::test_parse_patch_deleted_file`, `tests/core/services/test_patch_service.py::test_parse_patch_renamed_file`, `tests/core/services/test_patch_service.py::test_parse_patch_multiple_files_sorted_and_deduplicated`, `tests/core/services/test_patch_service.py::test_parse_patch_malformed_header_is_skipped_not_raised`, `tests/core/services/test_patch_service.py::test_parse_patch_empty_input_returns_empty_list`, `tests/core/services/test_patch_service.py::test_parse_patch_garbage_input_returns_empty_list`, `tests/core/services/test_patch_service.py::test_apply_patch_delegates_to_the_adapter_for_the_repo_path`, `tests/core/infra/test_git_repo_adapter.py::test_apply_patch_restricted_to_one_selected_path_touches_only_that_file`, `tests/core/infra/test_git_repo_adapter.py::test_apply_patch_raises_and_leaves_the_working_tree_untouched_on_a_bad_patch`, `tests/core/infra/test_git_repo_adapter.py::test_apply_patch_raises_when_the_check_dry_run_would_fail_to_apply_cleanly`, `tests/gui/test_patch_text_input_dialog.py::test_prefilled_from_clipboard_text`, `tests/gui/test_patch_text_input_dialog.py::test_ok_disabled_when_clipboard_text_is_blank`, `tests/gui/test_patch_text_input_dialog.py::test_ok_disabled_when_prefilled_text_is_cleared`, `tests/gui/test_patch_text_input_dialog.py::test_patch_text_returns_edited_text`, `tests/gui/test_patch_text_input_dialog.py::test_ok_enabled_once_blank_text_is_filled_in`, `tests/gui/test_main_window.py::test_apply_patch_action_present_only_on_repo_root_menu`, `tests/gui/test_main_window.py::test_apply_patch_action_absent_on_non_repo_root_folder_menu`, `tests/gui/test_main_window.py::test_apply_patch_from_file_applies_only_the_selected_files_and_refreshes`, `tests/gui/test_main_window.py::test_apply_patch_from_clipboard_reads_editable_text_and_applies_it`, `tests/gui/test_main_window.py::test_apply_patch_cancel_at_source_chooser_never_opens_file_picker_or_parses`, `tests/gui/test_main_window.py::test_apply_patch_empty_parse_result_shows_info_and_never_opens_selection_dialog`, `tests/gui/test_main_window.py::test_apply_patch_failure_shows_critical_and_never_refreshes`

### F98. "Show stashes..." repo-root context-menu action, with an apply/pop restore
WHAT: Right-clicking a repo root offers "Show stashes...", next to "Show Log". It opens a dialog listing that repo's `git stash` entries (newest first), each showing its ref, message, and date. Selecting an entry lazily loads its diff (including untracked files added by the stash, when the installed git supports `--include-untracked`, falling back to a tracked-only diff otherwise) and splits it into one row per changed file in a list on the left, each labeled with its change-type letter (e.g. `[M] path/to/file.ts`); the first file auto-selects so the pane is never blank. Selecting a file row renders that file's diff in the app's own side-by-side view on the right -- the same panes every other diff uses, not raw patch text -- with edit mode always disabled (a stash diff describes the stashed state, not the file currently on disk, so editing it would target the wrong content). A stash whose diff touches no files, or a `stash_diff` failure, shows a readable message in place of the file list/diff panes rather than an empty pane. "Apply" restores the selected stash's changes into the working tree while keeping the stash entry; "Pop" does the same and then deletes the entry, but first asks for Yes/No confirmation worded to make clear the entry is deleted. Both buttons stay disabled until a row is selected. A repo with no stashes shows "No stashes in this repository." instead of an empty list, with both buttons disabled. A successful Apply/Pop shows a brief confirmation and refreshes the dialog's own list; a failure (e.g. a restore that would conflict) shows the git error in a critical message box and leaves the working tree and stash list untouched. Closing the dialog after any successful restore refreshes the repo the same way the existing "Refresh Repo" action does; closing it without a restore does not. Both the stash table and the file list also offer right-click context menus, covered separately in F99 and F100. This belongs to the Workspace Tree & Navigation category (see `## Categories`); it is numbered last because renumbering existing features is not allowed.
WHERE: `src/local_changes_viewer/gui/main_window.py`, `src/local_changes_viewer/gui/stashes_dialog.py`, `src/local_changes_viewer/gui/diff_view/side_by_side_view.py`, `src/local_changes_viewer/core/infra/git_repo_adapter.py`, `src/local_changes_viewer/core/services/patch_service.py`, `src/local_changes_viewer/core/domain/stash_entry.py`, `src/local_changes_viewer/core/domain/file_change.py`
TESTS: `tests/core/infra/test_git_repo_adapter.py::test_list_stashes_returns_empty_list_for_a_repo_with_no_stashes`, `tests/core/infra/test_git_repo_adapter.py::test_list_stashes_returns_newest_first_with_real_fields`, `tests/core/infra/test_git_repo_adapter.py::test_list_stashes_message_with_colon_and_pipe_parses_intact`, `tests/core/infra/test_git_repo_adapter.py::test_stash_diff_contains_the_changed_file_name`, `tests/core/infra/test_git_repo_adapter.py::test_apply_stash_restores_file_content_and_keeps_the_stash_entry`, `tests/core/infra/test_git_repo_adapter.py::test_pop_stash_restores_file_content_and_removes_the_stash_entry`, `tests/core/infra/test_git_repo_adapter.py::test_stash_operations_reject_a_malformed_ref_without_invoking_git`, `tests/core/infra/test_git_repo_adapter.py::test_parse_unified_diff_is_public_and_parses_a_single_file_chunk`, `tests/core/services/test_patch_service.py::test_split_patch_multiple_files_each_chunk_contains_only_its_own_file`, `tests/core/services/test_patch_service.py::test_split_patch_and_parse_patch_agree_on_paths_and_change_types`, `tests/gui/test_stashes_dialog.py::test_rows_render_with_ref_message_and_date`, `tests/gui/test_stashes_dialog.py::test_buttons_disabled_with_no_selection`, `tests/gui/test_stashes_dialog.py::test_selecting_a_row_loads_its_diff_and_enables_buttons`, `tests/gui/test_stashes_dialog.py::test_selecting_a_stash_with_multiple_files_lists_every_file_exactly_once`, `tests/gui/test_stashes_dialog.py::test_selecting_a_second_file_swaps_the_side_by_side_content`, `tests/gui/test_stashes_dialog.py::test_stash_with_no_changed_files_shows_a_message_not_a_blank_pane`, `tests/gui/test_stashes_dialog.py::test_stash_diff_failure_shows_error_instead_of_raising`, `tests/gui/test_stashes_dialog.py::test_apply_calls_the_injected_adapter_with_the_selected_ref`, `tests/gui/test_stashes_dialog.py::test_pop_confirms_before_deleting_the_stash_entry`, `tests/gui/test_stashes_dialog.py::test_apply_failure_shows_critical_and_does_not_mark_restored`, `tests/gui/test_stashes_dialog.py::test_empty_state_shows_message_and_disables_buttons`, `tests/gui/test_main_window.py::test_show_stashes_action_present_only_on_repo_root_menu`, `tests/gui/test_main_window.py::test_show_stashes_action_absent_on_non_repo_root_folder_menu`, `tests/gui/test_main_window.py::test_show_stashes_refreshes_the_repo_when_dialog_reports_a_restore`, `tests/gui/test_main_window.py::test_show_stashes_does_not_refresh_when_dialog_reports_no_restore`

### F99. Stashes dialog stash-table row context menu: "Restore stash" / "Delete stash"
WHAT: Right-clicking a row in the "Show stashes..." dialog's stash table (F98) first selects that row -- mirroring the folder tree's own row context menu -- then offers "Restore stash" and "Delete stash"; right-clicking empty space below the last row shows no menu. "Restore stash" is the exact same action as the dialog's Apply button (same handler, same restore-then-refresh path) rather than a second implementation of it. "Delete stash" runs `git stash drop` on that entry, but only after a Yes/No confirmation defaulting to No that names both the stash's ref and its message; accepting drops the stash and then fully reloads the stash table from git, because dropping a stash renumbers every remaining `stash@{N}` -- a partial (row-only) removal would leave the table pointing later actions at the wrong stash -- which also clears the file list/diff pane. A `GitCommandError` while dropping shows a critical message box instead of raising.
WHERE: `src/local_changes_viewer/gui/stashes_dialog.py`, `src/local_changes_viewer/core/infra/git_repo_adapter.py`
TESTS: `tests/gui/test_stashes_dialog.py::test_table_context_menu_offers_restore_and_delete_and_selects_the_row`, `tests/gui/test_stashes_dialog.py::test_table_context_menu_on_empty_space_shows_no_menu`, `tests/gui/test_stashes_dialog.py::test_restore_stash_context_action_reuses_the_apply_handler`, `tests/gui/test_stashes_dialog.py::test_delete_stash_declined_does_not_call_git`, `tests/gui/test_stashes_dialog.py::test_delete_stash_accepted_drops_and_fully_reloads_the_table`, `tests/gui/test_stashes_dialog.py::test_delete_stash_failure_shows_critical_instead_of_raising`, `tests/core/infra/test_git_repo_adapter.py::test_drop_stash_removes_the_entry_without_touching_the_working_tree`, `tests/core/infra/test_git_repo_adapter.py::test_drop_stash_renumbers_remaining_stashes`

### F100. Stashes dialog file-list context menu: "Restore file" from a stash
WHAT: Right-clicking a file in the selected stash's file list (F98) first selects that row, then offers "Restore file"; right-clicking empty space shows no menu. Confirms via a Yes/No dialog defaulting to No that names the exact file path and warns the working-tree copy will be overwritten, then runs `git checkout <ref> -- <path>`, restoring just that one file from the stash into the working tree without touching the stash entry itself or reloading the stash table. A successful restore marks the dialog as having restored something, so closing it refreshes the repo the same way the existing Apply/Pop restores do. A `GitCommandError` shows a critical message box instead of raising.
WHERE: `src/local_changes_viewer/gui/stashes_dialog.py`, `src/local_changes_viewer/core/infra/git_repo_adapter.py`
TESTS: `tests/gui/test_stashes_dialog.py::test_file_list_context_menu_offers_restore_file`, `tests/gui/test_stashes_dialog.py::test_file_list_context_menu_on_empty_space_shows_no_menu`, `tests/gui/test_stashes_dialog.py::test_restore_file_declined_does_not_call_git`, `tests/gui/test_stashes_dialog.py::test_restore_file_accepted_calls_adapter_with_ref_and_path`, `tests/gui/test_stashes_dialog.py::test_restore_file_failure_shows_critical_instead_of_raising`, `tests/core/infra/test_git_repo_adapter.py::test_restore_file_from_stash_overwrites_only_that_file`
