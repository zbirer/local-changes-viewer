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
- **Added a feature?** Add a block, with a test. The ratchet will not accept a
  new `TESTS: NONE`.
- **Moved a file?** Update its `WHERE:` anchor.
- **Renamed a test?** Update every block citing it.

`WHERE:` line numbers drift and are advisory — the gate checks the *file* exists,
not the line. `TESTS:` entries are checked exactly and must name real tests.

---

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

### F4. Repo row tooltip shows status, branches, PR summary, absolute path
WHAT: Hovering a repo row shows name, change/ahead-behind summary, branch, parent/default branch, PR summary, and absolute path.
WHERE: `src/local_changes_viewer/gui/workspace_tree/tree_model.py:314`
TESTS: `tests/gui/test_workspace_tree_model.py::test_repo_row_tooltip_includes_absolute_path`

### F5. Changed files colored by change type; unpushed commits styled distinctly
WHAT: Modified/added/deleted/renamed/untracked/ignored files render in different colors; unpushed-commit files get a distinct color, a "(unpushed commit)" suffix, and a commit-message tooltip.
WHERE: `src/local_changes_viewer/gui/workspace_tree/tree_model.py:392`
TESTS: NONE

### F6. Nested repos/worktrees render as sub-trees, hidden when empty
WHAT: A worktree or nested repo appears as its own sub-branch inside its parent's tree, but only if it (or a descendant) has changes.
WHERE: `src/local_changes_viewer/gui/workspace_tree/tree_model.py:95`
TESTS: NONE

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
WHAT: Hovering a repo-root row reveals inline R/+/− buttons that refresh, expand, or collapse just that repo.
WHERE: `src/local_changes_viewer/gui/workspace_tree/tree_view.py:129`
TESTS: `tests/gui/test_tree_view.py::test_refresh_button_sits_left_of_expand_and_collapse`, `tests/gui/test_tree_view.py::test_refresh_button_click_emits_signal_with_repo_path`, `tests/gui/test_tree_view.py::test_row_actions_overlay_shown_for_repo_root_hidden_otherwise`

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

### F15. File-row right-click menu: Copy Path/Name, Refresh Diff, Filter Out This File
WHAT: Right-clicking a changed file offers path/name copy, a forced diff reload, and a one-file filter shortcut.
WHERE: `src/local_changes_viewer/gui/main_window.py:1214`
TESTS: NONE

### F16. Folder-row right-click menu, with repo-root-only Refresh/Show Log/Add to Profile
WHAT: Right-clicking a folder or repo-root row offers folder-scoped actions; only repo roots get Refresh Repo, Show Log, and Add to Profile.
WHERE: `src/local_changes_viewer/gui/main_window.py:1225`
TESTS: NONE

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
WHAT: Worktrees of a discovered repo are scanned and shown as their own tree rows, tracked back to their parent repo.
WHERE: `src/local_changes_viewer/core/services/workspace_scanner_service.py:270`
TESTS: `tests/core/services/test_workspace_scanner_service.py::test_scan_includes_linked_worktrees_as_separate_repos`, `tests/core/services/test_workspace_scanner_service.py::test_scan_records_logical_parent_for_sibling_directory_worktree`, `tests/core/services/test_workspace_scanner_service.py::test_scan_skips_repo_when_listing_worktrees_fails`, `tests/core/services/test_workspace_scanner_service.py::test_scan_skips_stale_worktree_paths_that_no_longer_exist`

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

### F35. "Hide repos without changes" hides empty repos, keeping a parent with a changed nested worktree
WHAT: Repos with zero changes drop out of the tree, unless a nested worktree beneath them still has changes.
WHERE: `src/local_changes_viewer/core/services/workspace_filter.py:57`
TESTS: `tests/core/services/test_workspace_filter.py::test_hide_repos_without_changes_drops_empty_repos`, `tests/core/services/test_workspace_filter.py::test_hide_repos_without_changes_after_ignoring_md_files`

### F36. Last-Commit-Time filter (toolbar slider / Ctrl+D toggle) hides files older than N minutes
WHAT: Limits the visible changes to files whose mtime is within the last N minutes; a missing file is treated as still-recent.
WHERE: `src/local_changes_viewer/core/services/workspace_filter.py:36`
TESTS: `tests/core/services/test_workspace_filter.py::test_max_age_minutes_zero_shows_all_changes`, `tests/core/services/test_workspace_filter.py::test_max_age_minutes_filters_out_old_files`, `tests/core/services/test_workspace_filter.py::test_max_age_minutes_includes_change_when_file_missing`

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

### F41. Detects modified/untracked/added/deleted/renamed/ignored files via git status
WHAT: The core change detection: every git status code maps to a ChangeType shown in the tree.
WHERE: `src/local_changes_viewer/core/infra/git_repo_adapter.py:29`
TESTS: `tests/core/infra/test_git_repo_adapter.py::test_list_changes_detects_modified_file`, `tests/core/infra/test_git_repo_adapter.py::test_list_changes_detects_untracked_file`, `tests/core/infra/test_git_repo_adapter.py::test_list_changes_detects_added_staged_file`, `tests/core/infra/test_git_repo_adapter.py::test_list_changes_detects_deleted_file`, `tests/core/infra/test_git_repo_adapter.py::test_list_changes_detects_renamed_staged_file`, `tests/core/infra/test_git_repo_adapter.py::test_list_changes_empty_for_clean_repo`

### F42. Untracked/ignored directories, including symlinked ones, report as a single directory entry
WHAT: An untracked or ignored folder — even a symlink pointing at one — shows as one directory row instead of being descended into.
WHERE: `src/local_changes_viewer/core/infra/git_repo_adapter.py:43`
TESTS: `tests/core/infra/test_git_repo_adapter.py::test_list_changes_detects_untracked_directory_as_single_directory_entry`, `tests/core/infra/test_git_repo_adapter.py::test_list_changes_classifies_symlinked_directory_as_directory`

### F43. Branch name plus ahead/behind-vs-upstream counts shown per repo
WHAT: Each repo reports its current branch and how many commits it is ahead of and behind its upstream.
WHERE: `src/local_changes_viewer/core/infra/git_repo_adapter.py:121`
TESTS: `tests/core/infra/test_git_repo_adapter.py::test_branch_status_with_no_upstream`, `tests/core/infra/test_git_repo_adapter.py::test_branch_status_ahead_and_behind`

### F44. Repo tooltip shows a guessed local parent branch and the remote default branch
WHAT: Best-effort "parent of branch" (nearest merge-base among local branches) and the remote's default branch name.
WHERE: `src/local_changes_viewer/core/infra/git_repo_adapter.py:245`
TESTS: `tests/core/infra/test_git_repo_adapter.py::test_branch_status_finds_local_parent_branch`, `tests/core/infra/test_git_repo_adapter.py::test_branch_status_parent_branch_none_when_no_other_branches`, `tests/core/infra/test_git_repo_adapter.py::test_branch_status_default_branch_falls_back_to_init_default_branch_config`, `tests/core/infra/test_git_repo_adapter.py::test_branch_status_default_branch_queried_live_from_remote`

### F45. Full-context unified diff computed for any change type
WHAT: Selecting a modified, deleted, untracked, renamed, or unpushed-commit file computes its diff against the right ref.
WHERE: `src/local_changes_viewer/core/infra/git_repo_adapter.py:269`
TESTS: `tests/core/infra/test_git_repo_adapter.py::test_compute_diff_for_modified_file`, `tests/core/infra/test_git_repo_adapter.py::test_compute_diff_for_deleted_file`, `tests/core/infra/test_git_repo_adapter.py::test_compute_diff_for_untracked_file`, `tests/core/infra/test_git_repo_adapter.py::test_compute_diff_for_renamed_file`, `tests/core/infra/test_git_repo_adapter.py::test_compute_diff_for_unpushed_commit_diffs_against_upstream`

### F46. "Ignore whitespace" setting recomputes the diff ignoring whitespace-only changes
WHAT: Toggling this setting re-diffs the current file with git's --ignore-all-space.
WHERE: `src/local_changes_viewer/core/infra/git_repo_adapter.py:273`
TESTS: `tests/core/infra/test_git_repo_adapter.py::test_compute_diff_ignore_whitespace`

### F47. Linked worktrees are discovered and excluded from their parent's own change list
WHAT: A worktree checkout under a repo does not show as a spurious untracked-directory change on the parent; it is shown separately instead.
WHERE: `src/local_changes_viewer/core/infra/git_repo_adapter.py:217`
TESTS: `tests/core/infra/test_git_repo_adapter.py::test_list_worktrees_returns_linked_worktree_paths`, `tests/core/infra/test_git_repo_adapter.py::test_list_worktrees_returns_empty_list_when_no_linked_worktrees`

### F48. "Show Log" dialog: browse recent commits, per-commit changed files, per-file diff
WHAT: A repo-root context action opens a dialog listing recent commits (count adjustable via slider), the files each commit touched, and each file's diff.
WHERE: `src/local_changes_viewer/gui/commit_log_dialog.py`
TESTS: `tests/core/infra/test_git_repo_adapter.py::test_get_recent_commits_returns_newest_first_with_limit`, `tests/core/infra/test_git_repo_adapter.py::test_get_commit_files_lists_changed_paths`, `tests/core/infra/test_git_repo_adapter.py::test_get_commit_file_diff_shows_added_content`

### F49. Repo discovery is limited to immediate children and never descends into a found repo
WHAT: Scanning a root treats the root itself as a repo if it is one, otherwise scans only its direct subdirectories, and never looks for repos within repos.
WHERE: `src/local_changes_viewer/core/infra/filesystem_scanner.py`
TESTS: `tests/core/infra/test_filesystem_scanner.py::test_finds_repo_at_root`, `tests/core/infra/test_filesystem_scanner.py::test_root_itself_is_a_repo`, `tests/core/infra/test_filesystem_scanner.py::test_does_not_descend_past_immediate_children`, `tests/core/infra/test_filesystem_scanner.py::test_finds_multiple_sibling_repos`, `tests/core/infra/test_filesystem_scanner.py::test_does_not_look_inside_a_found_repo_for_further_repos`, `tests/core/infra/test_filesystem_scanner.py::test_ignores_folders_without_git`, `tests/core/infra/test_filesystem_scanner.py::test_detects_git_as_file_for_submodules`, `tests/core/infra/test_filesystem_scanner.py::test_returns_empty_list_when_no_repos_found`

### F50. Connect to GitHub… dialog authenticates and stores credentials
WHAT: A dialog collects a username and personal access token, validates it against GitHub's own reported login, and stores it for reuse.
WHERE: `src/local_changes_viewer/gui/github_connect_dialog.py`
TESTS: NONE

### F51. Disconnect GitHub clears stored credentials
WHAT: GitHub > Disconnect GitHub removes the stored token and username.
WHERE: `src/local_changes_viewer/gui/main_window.py:879`
TESTS: NONE

### F52. Auto-reconnects to GitHub on launch using stored credentials
WHAT: If a username and token were previously saved, the app reconnects silently at startup and shows a status message.
WHERE: `src/local_changes_viewer/gui/main_window.py:833`
TESTS: NONE

### F53. Repo row and tooltip can show its associated GitHub PR, resolved by branch name
WHAT: If the current branch has an open, closed, or merged PR on GitHub, its number and state show in the row and tooltip.
WHERE: `src/local_changes_viewer/core/infra/github_client.py:344`
TESTS: `tests/core/infra/test_github_client.py::test_find_pull_request_returns_none_when_no_matches`, `tests/core/infra/test_github_client.py::test_find_pull_request_returns_info_when_found`, `tests/core/infra/test_github_client.py::test_find_pull_request_lowercases_merged_state`, `tests/core/infra/test_github_client.py::test_find_pull_request_raises_github_error_on_graphql_errors`

### F54. PR lookups are cached with a TTL, and a terminal-state PR is reused across refreshes
WHAT: A branch's PR result is cached for 60s to avoid re-querying GitHub every refresh; a merged or closed PR for an unchanged branch is reused indefinitely.
WHERE: `src/local_changes_viewer/core/services/workspace_scanner_service.py:469`
TESTS: `tests/core/services/test_workspace_scanner_service.py::test_scan_reuses_cached_pr_for_terminal_state_and_unchanged_branch`, `tests/core/services/test_workspace_scanner_service.py::test_scan_still_fetches_when_previous_pr_is_open`, `tests/core/services/test_workspace_scanner_service.py::test_scan_still_fetches_when_branch_changed`, `tests/core/services/test_workspace_scanner_service.py::test_scan_reuses_open_pr_within_ttl_window`, `tests/core/services/test_workspace_scanner_service.py::test_scan_refetches_pr_once_ttl_expires`, `tests/core/services/test_workspace_scanner_service.py::test_scan_refetches_pr_immediately_when_branch_changes_within_ttl`

### F55. GitHub fetch errors degrade gracefully instead of failing the scan
WHAT: A PR-lookup failure (GraphQL or HTTP error) is logged and treated as "no PR", never crashing or aborting the workspace scan.
WHERE: `src/local_changes_viewer/core/services/workspace_scanner_service.py:514`
TESTS: `tests/core/services/test_workspace_scanner_service.py::test_scan_routes_github_pr_fetch_error_through_on_log_not_an_exception`

### F56. "My Open Pull Requests…" dialog and dockable PRs panel list the user's own open PRs
WHAT: Lists every open PR authored by the connected user across every GitHub-remote repo currently in the tree, grouped by repo.
WHERE: `src/local_changes_viewer/gui/my_pull_requests_dialog.py`
TESTS: `tests/core/infra/test_github_client.py::test_list_authored_open_pull_requests_empty_pairs_returns_empty_list`, `tests/core/infra/test_github_client.py::test_list_authored_open_pull_requests_returns_matches_filtered_by_author`, `tests/core/infra/test_github_client.py::test_list_authored_open_pull_requests_skips_repo_on_error`

### F57. Each PR row shows approval, unresolved threads, last reviewer, changed files, checks state
WHAT: Per-PR columns summarizing review status, refreshable individually.
WHERE: `src/local_changes_viewer/core/infra/github_client.py:146`
TESTS: `tests/core/infra/test_github_client.py::test_get_pull_request_review_status_returns_none_reviewer_when_no_reviews`

### F58. Double-click or right-click a PR row opens it in browser, or shows Info / Open Issues
WHAT: Double-click opens the PR URL; right-click offers Refresh, Info, Open Issues, and Copy URL. Info shows title, branches, status, dates, and last commenter; Open Issues lists unresolved review threads plus general comments.
WHERE: `src/local_changes_viewer/core/infra/github_client.py:245`
TESTS: `tests/core/infra/test_github_client.py::test_get_pull_request_details_returns_open_status_and_last_comment_writer`, `tests/core/infra/test_github_client.py::test_get_pull_request_details_reports_draft_status_and_no_comments`, `tests/core/infra/test_github_client.py::test_get_pull_request_open_threads_returns_only_unresolved_review_comments`, `tests/core/infra/test_github_client.py::test_get_pull_request_open_threads_returns_issue_comments`, `tests/core/infra/test_github_client.py::test_get_pull_request_open_threads_maps_review_states_to_comment_types`, `tests/core/infra/test_github_client.py::test_get_pull_request_open_threads_sorts_across_categories_newest_first`, `tests/core/infra/test_github_client.py::test_get_pull_request_open_threads_returns_empty_list_when_nothing_open`

### F59. "Open All" and "Copy All URLs" for a PR repository group
WHAT: Right-clicking a repo group in the PR list opens every one of its PRs in the browser, or copies all their URLs.
WHERE: `src/local_changes_viewer/gui/my_pull_requests_dialog.py:229`
TESTS: NONE

### F60. Recognizes https, ssh, and git@ GitHub remote URL forms
WHAT: Parses an origin remote URL into (owner, repo) across GitHub's various URL styles, including SSH host aliases.
WHERE: `src/local_changes_viewer/core/infra/github_client.py:68`
TESTS: `tests/core/infra/test_github_client.py::test_parse_github_owner_repo`

### F61. Token validation surfaces authentication errors
WHAT: get_authenticated_login confirms a token works and raises a GitHubError with detail on HTTP failure.
WHERE: `src/local_changes_viewer/core/infra/github_client.py:142`
TESTS: `tests/core/infra/test_github_client.py::test_get_authenticated_login`, `tests/core/infra/test_github_client.py::test_get_authenticated_login_raises_github_error_on_http_error`

### F62. Unified diff view with syntax highlighting and a toggleable line-number gutter
WHAT: The default diff view renders +/-/context lines with language-aware syntax coloring and old/new line numbers in a gutter.
WHERE: `src/local_changes_viewer/gui/diff_view/unified_view.py`
TESTS: NONE

### F63. Side-by-side diff view with syncable left/right scrolling
WHAT: An alternate two-pane diff view whose scroll can be locked together via "Sync side-by-side scroll".
WHERE: `src/local_changes_viewer/gui/diff_view/side_by_side_view.py`
TESTS: NONE

### F64. Long unchanged context runs fold to a "click to expand" marker
WHAT: A big run of unchanged context lines collapses to one clickable marker line in both diff views; clicking expands it.
WHERE: `src/local_changes_viewer/core/services/context_folding.py`
TESTS: `tests/core/services/test_context_folding.py::test_short_context_run_stays_visible`, `tests/core/services/test_context_folding.py::test_long_context_run_between_changes_folds_middle_keeping_margins`, `tests/core/services/test_context_folding.py::test_long_context_run_at_file_start_has_no_head_margin`, `tests/core/services/test_context_folding.py::test_long_context_run_at_file_end_has_no_tail_margin`

### F65. Side-by-side pairs removed and added lines row-by-row for substitutions
WHAT: In side-by-side view a run of removed lines lines up against a same-length run of added lines row by row; uneven runs leave the shorter side blank.
WHERE: `src/local_changes_viewer/core/services/diff_pairing.py`
TESTS: `tests/core/services/test_diff_pairing.py::test_pairs_context_line_on_both_sides`, `tests/core/services/test_diff_pairing.py::test_pairs_equal_length_removed_and_added_runs_row_by_row`, `tests/core/services/test_diff_pairing.py::test_pairs_unequal_length_runs_leaving_unmatched_side_none`, `tests/core/services/test_diff_pairing.py::test_pair_substitution_indices_matches_same_row_removed_added`

### F66. Intraline diff highlights the exact changed substring within a modified line
WHAT: Within a paired removed/added line, only the actually-changed word or substring is highlighted, not the whole line.
WHERE: `src/local_changes_viewer/core/services/intraline_diff.py`
TESTS: `tests/core/services/test_intraline_diff.py::test_single_word_change_in_long_line_highlights_only_that_word`, `tests/core/services/test_intraline_diff.py::test_identical_text_produces_no_ranges`, `tests/core/services/test_intraline_diff.py::test_completely_different_text_covers_whole_string`

### F67. Diff toolbar: view-mode toggle, prev/next hunk, refresh, line numbers, font size
WHAT: Toggle side-by-side/unified, jump between changed hunks, force-reload the diff, toggle gutters, and zoom in and out, also via View menu and Ctrl+= / Ctrl+-.
WHERE: `src/local_changes_viewer/gui/diff_view/diff_view_widget.py`
TESTS: NONE

### F68. In-place file editing from the side-by-side view, with save and discard-on-navigate
WHAT: An Edit toggle makes the right pane of side-by-side view editable, preserving original encoding and line endings; Save writes it back; navigating away or closing with unsaved edits prompts to discard.
WHERE: `src/local_changes_viewer/gui/diff_view/side_by_side_view.py:236`
TESTS: NONE

### F69. File-info status label shows detected encoding and line-ending
WHAT: Selecting a file shows its detected text encoding (UTF-8, UTF-8 BOM, Latin-1, Binary, Unknown) and line-ending style (LF, CRLF, Mixed, N-A) in the status bar.
WHERE: `src/local_changes_viewer/core/services/file_info.py`
TESTS: `tests/core/services/test_file_info.py::test_detects_lf`, `tests/core/services/test_file_info.py::test_detects_crlf`, `tests/core/services/test_file_info.py::test_detects_mixed_line_endings`, `tests/core/services/test_file_info.py::test_detects_utf8`, `tests/core/services/test_file_info.py::test_detects_utf8_bom`, `tests/core/services/test_file_info.py::test_detects_binary`, `tests/core/services/test_file_info.py::test_detects_latin1_fallback`

### F70. "Copy Diff" copies the unified diff text to the clipboard
WHAT: Copies the selected file's diff, formatted as a standard unified-diff text block, to the clipboard.
WHERE: `src/local_changes_viewer/core/services/diff_formatting.py`
TESTS: `tests/core/services/test_diff_formatting.py::test_formats_hunks_with_correct_prefixes`, `tests/core/services/test_diff_formatting.py::test_includes_file_header_when_file_path_given`

### F71. Copy File Path / Copy File Name / Open in Default Editor / Reveal in Finder
WHAT: Four Actions-menu commands operating on the currently selected file.
WHERE: `src/local_changes_viewer/gui/main_window.py:1194`
TESTS: NONE

### F72. "Always reload fresh diff" setting bypasses the per-selection diff cache
WHAT: When enabled, selecting a file always re-reads its diff from disk instead of reusing a previously computed one.
WHERE: `src/local_changes_viewer/gui/main_window.py:289`
TESTS: NONE

### F73. Diff view mode, window geometry, and splitter sizes persist across restarts
WHAT: The unified/side-by-side choice, window size and position, and the main splitter's pane sizes are restored on next launch.
WHERE: `src/local_changes_viewer/gui/main_window.py:420`
TESTS: NONE

### F74. Settings-menu toggles persist and restore without re-triggering a scan or refresh
WHAT: All checkable Settings-menu items are saved and restored, and restoring them at startup must not fire a redundant scan or display refresh.
WHERE: `src/local_changes_viewer/gui/settings.py`
TESTS: `tests/gui/test_main_window.py::test_only_one_scan_starts_during_window_init`, `tests/gui/test_main_window.py::test_display_filter_toggle_does_not_refresh_during_settings_restore`

### F75. Log Level… dialog sets and persists app log verbosity
WHAT: Choose ERROR, WARNING, INFO, DEBUG, or VERBOSE; persists and takes effect immediately.
WHERE: `src/local_changes_viewer/gui/applog.py`
TESTS: NONE

### F76. Tooltip Font Size… dialog sets and persists the app-wide tooltip font size
WHAT: Sets a custom point size for all Qt tooltips app-wide; 0 means system default.
WHERE: `src/local_changes_viewer/gui/main_window.py:808`
TESTS: NONE

### F77. Help menu: dialogs documenting Settings, Actions, PR panel, and toolbar buttons
WHAT: Four static help dialogs describing menu items and toolbar buttons.
WHERE: `src/local_changes_viewer/gui/help_dialog.py`
TESTS: NONE

### F78. Profiles… dialog manages named profiles
WHAT: A dialog for creating, renaming, and deleting named profiles and checking which discovered repos belong to each.
WHERE: `src/local_changes_viewer/gui/profile_dialog.py`
TESTS: NONE

### F79. Repo context-menu "Add to Profile" toggle and "New Profile…" shortcut
WHAT: Right-clicking a repo root can add or remove it from any existing profile, or create a new profile seeded with it.
WHERE: `src/local_changes_viewer/gui/main_window.py:1148`
TESTS: NONE

### F80. Active profile switch via View > Profile submenu, shown in the status bar
WHAT: A radio-style submenu of "No Profile" plus each defined profile; the active one's name shows in the status bar.
WHERE: `src/local_changes_viewer/gui/main_window.py:1116`
TESTS: NONE

### F81. Last-opened root folder is remembered and reopened automatically at launch
WHAT: The app reopens whatever folder was open when it last closed.
WHERE: `src/local_changes_viewer/gui/main_window.py:415`
TESTS: NONE

### F82. GitHub credentials are stored in a local token file with restrictive permissions
WHAT: Tokens are written to ~/.local-changes-viewer/github_token.json chmod 0600, keyed by username, and can be deleted per user.
WHERE: `src/local_changes_viewer/gui/github_auth.py`
TESTS: `tests/gui/test_github_auth.py::test_set_and_get_token_round_trips`, `tests/gui/test_github_auth.py::test_get_token_returns_none_when_file_missing`, `tests/gui/test_github_auth.py::test_delete_token_removes_only_that_user`, `tests/gui/test_github_auth.py::test_delete_token_is_noop_when_user_not_present`, `tests/gui/test_github_auth.py::test_set_token_writes_file_with_restrictive_permissions`

### F83. In-memory and on-disk app log, filtered by configured log level
WHAT: Every logged message is kept in memory for the "App Log" copy action and appended to a log file under ~/Library/Logs/local-changes-viewer/, filtered by the current log level.
WHERE: `src/local_changes_viewer/gui/applog.py`
TESTS: NONE

### F84. "App Log" action copies the full in-memory app log to the clipboard
WHAT: Actions > App Log dumps every logged line to the clipboard for bug reports.
WHERE: `src/local_changes_viewer/gui/main_window.py:1181`
TESTS: NONE
