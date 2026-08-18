from PySide6.QtWidgets import QDialog, QPushButton, QTextBrowser, QVBoxLayout

_SETTINGS_HELP = """
<h3>Settings menu</h3>
<ul>
<li><b>Show ignored files</b> — include git-ignored files in the change list.</li>
<li><b>Show committed but not pushed files</b> — include files changed by local commits that
haven't been pushed yet.</li>
<li><b>Ignore whitespace</b> — ignore whitespace-only changes when computing diffs.</li>
<li><b>Ignore MD files</b> — hide changes to Markdown (.md) files.</li>
<li><b>Hide repos without changes</b> — hide repositories that have no changes to display.</li>
<li><b>Sync side-by-side scroll</b> — keep the left and right panes scrolled together in
side-by-side diff view.</li>
<li><b>Always reload fresh diff</b> — re-read a file's diff from disk every time it's selected,
instead of reusing a cached copy.</li>
<li><b>Auto Refresh…</b> — configure automatic periodic re-scanning of the workspace.</li>
<li><b>Watch for File Changes</b> — automatically re-scan a repository when its files change on
disk, instead of relying only on manual or periodic refresh.</li>
<li><b>Log Level…</b> — set the verbosity of the app log (copy it via Actions &gt; App Log).</li>
<li><b>Tooltip Font Size…</b> — set the font size used by diff/file tooltips (0 = system
default).</li>
<li><b>Filtered Folders…</b> — manage rules that hide files under matching folder names.</li>
<li><b>Profiles…</b> — manage profiles, each a named subset of repositories. Switch the active
profile from View &gt; Profile, or add/remove a repo from a profile via its right-click menu in
the folder tree.</li>
</ul>
"""

_ACTIONS_HELP = """
<h3>Actions menu</h3>
<ul>
<li><b>Open Folder…</b> — choose the root folder to scan for git repositories.</li>
<li><b>Verify Changes Against Git…</b> — cross-check the displayed changes against git's own
view of the repository and report any discrepancies.</li>
<li><b>App Log</b> — copy the application's internal log entries to the clipboard (shows a
brief status-bar confirmation).</li>
<li><b>Error Log</b> — open a dialog listing recorded errors, with buttons to copy or clear
them.</li>
<li><b>Copy Diff</b> — copy the diff of the currently selected file to the clipboard.</li>
<li><b>Copy File Path</b> / <b>Copy File Name</b> — copy the selected file's path or name.</li>
<li><b>Open in Default Editor</b> — open the selected file in your system's default editor.</li>
<li><b>Reveal in Finder</b> — show the selected file in the OS file browser.</li>
<li><b>Refresh</b> — re-scan the workspace for changes.</li>
<li><b>Toggle Last Commit Time Filter</b> — toggle filtering files by how recently they changed
(also adjustable via the slider in the diff view toolbar).</li>
</ul>
<p>The folder tree and file rows also have their own right-click menus (copy name/path, refresh
diff, <b>File History…</b> — browse a file's or folder's commit history and diff past versions
against disk — and — on a repository row — add/remove it from a profile).</p>
"""

_PR_HELP = """
<h3>Pull requests (panel and dialog)</h3>
<p>Both the docked <b>PRs panel</b> (View &gt; Open PRs Panel, or the toolbar's <b>PRs</b> button)
and the <b>My Open Pull Requests…</b> dialog (GitHub menu) show your open pull requests grouped
by repository.</p>
<ul>
<li><b>Refresh</b> — re-fetch pull request data from GitHub.</li>
<li><b>Double-click a PR row</b> — open that PR in your browser.</li>
<li><b>Right-click a PR row</b> — Refresh, Info, Open Issues, or Copy URL for that PR.</li>
<li><b>Right-click a repository group</b> — Open All (opens every PR in that repo in the
browser) or Copy All URLs.</li>
<li>Each PR row shows approval status, unresolved review threads, last reviewer/review time,
changed files, and CI checks status.</li>
</ul>
<p>Connect to GitHub first via GitHub &gt; Connect to GitHub… to enable these features.</p>
"""

_TOOLBAR_HELP = """
<h3>Diff view toolbar buttons</h3>
<ul>
<li><b>PRs</b> — open the pull requests panel.</li>
<li><b>Side-by-side / Unified</b> — toggle between side-by-side and unified diff view.</li>
<li><b>Prev change / Next change</b> — scroll to the previous/next changed section of the file.</li>
<li><b>Refresh</b> — re-load the diff for the current file.</li>
<li><b>Edit</b> — edit the file in place (side-by-side view only; disabled for a file whose
changes are already committed).</li>
<li><b>Save</b> — save edits made while in edit mode.</li>
<li><b>Line Numbers</b> — toggle line number gutters.</li>
<li><b>Time filter slider</b> — limit the diff to changes from the last N minutes; "All changes"
means no time filtering.</li>
</ul>
"""


class HelpDialog(QDialog):
    def __init__(self, title: str, html: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        width = int(parent.width() * 0.6) if parent is not None else 520
        self.resize(width, 480)

        browser = QTextBrowser()
        browser.setHtml(html)
        browser.setOpenExternalLinks(True)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(browser)
        layout.addWidget(close_button)


def show_settings_help(parent=None) -> None:
    HelpDialog("Help — Settings", _SETTINGS_HELP, parent).exec()


def show_actions_help(parent=None) -> None:
    HelpDialog("Help — Actions", _ACTIONS_HELP, parent).exec()


def show_pull_requests_help(parent=None) -> None:
    HelpDialog("Help — Pull Requests", _PR_HELP, parent).exec()


def show_toolbar_help(parent=None) -> None:
    HelpDialog("Help — Toolbar Buttons", _TOOLBAR_HELP, parent).exec()
