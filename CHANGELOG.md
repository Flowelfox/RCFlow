# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Backend and client are versioned independently. Entries are grouped by release date
and note which component is affected where it matters.

---

## [Unreleased]

### Added
- **Right-click a pull request in the sidebar** — a context menu with "AI review" and "Open on GitHub" (Client)
- **AI review a pull request** — the PR header's AI button is now "AI review this PR": it starts an agent (on the selected or default worker that has the repo cloned) that produces a readable Markdown report — findings with severity levels, a table of recommended reviewer actions (e.g. inline comment on lines X–Y, include in the global comment), and one overall recommendation (Approve / Comment / Request changes). Nothing is posted automatically: it shows the report and asks before applying, and warns that any GitHub actions are made as you (Backend + Client)
- **Remember window and layout between restarts** — the desktop app now restores its last window size, position, and maximized/full-screen state, and the sidebar width, when you reopen it (Client)
- **The same pull request is shown once across workers** — if several workers point at the same GitHub account, a PR that previously appeared once per worker now shows as a single entry with a "Worker / Project" badge for each worker that backs it. When you resolve conflicts or run an agent on such a PR, RCFlow picks the right worker automatically (the one with the repo cloned), or asks which to use when there's a genuine choice — with a "remember for this repo" option you can manage per worker in GitHub settings (Backend + Client)
- **Filter pull requests by state** — the Pull Requests tab now has Open / Merged / Closed filter chips (any combination), and syncing pulls recent merged and closed PRs too so you can actually browse them — not just open ones (Backend + Client)
- **At-a-glance pull request status** — each PR now shows a coloured status icon in the list and header: Approved, Changes requested, Review required, Can't merge (conflicts), Draft, Merged, or Closed — so you can tell a PR's state without opening it (Backend + Client)
- **See and post a pull request's conversation** — the PR review screen now has a conversation panel docked beneath the diff (resizable and collapsible) showing the PR's general (non-inline) comments alongside review summaries (approve / request-changes notes) as a timeline, with their Markdown rendered, plus a box to post your own comment. Previously only inline, line-anchored review comments were visible (Backend + Client)
- **See and copy a pull request's branches** — the PR review header now shows which branch the PR merges from and into (`head → base`), and clicking either branch name copies it to the clipboard (Client)
- **See merge conflicts before merging a pull request** — when a PR can't be merged because it conflicts with its target branch, the review screen now shows a banner listing exactly which files conflict, and the Merge button is disabled with a tooltip explaining why. Previously merging just failed with a cryptic server error that gave no hint of what was wrong or which files to fix (Backend + Client)
- **Resolve PR conflicts with an agent** — the conflict banner now has a "Resolve with agent" button that starts a coding-agent session in your local checkout, merges in the target branch, and resolves the conflicts. It then shows a report of what it fixed, how, and why, and asks for your permission before committing and pushing (Backend + Client)
- **See when a pull request is blocked by repository rules** — PRs that are conflict-free but can't be merged yet because they need a review or are waiting on required status checks (GitHub's "Review required") now show a clear warning and a disabled Merge button, instead of appearing mergeable and then failing (Backend + Client)
- **Control the worker service from the command line** — you can now manage the background worker the same way the desktop app does, without opening the GUI: install it as a service, start, stop, restart, turn login auto-start on or off, check whether it's running, and stream its logs. This works on machines with no graphical interface, and if you later open the desktop app it controls the very same worker (macOS; the Linux and Windows equivalents are in progress) (Backend)
- **Live monitoring of background watches** — when Claude Code starts a long-running watch on a build log, deploy stream, or any backgrounded script, you now see a dedicated card with the watch description, a live elapsed-time counter, an expandable list of the events (stdout lines) it has captured, and a Stop button to end the watch from the chat. Multiple watches at once each get their own card, and a strip above the input shows the running count and individual elapsed times so you always know what's still being observed. The card switches to a clear "stopped", "timed out", or "exit code N" state when the watch finishes (Backend + Client)

### Changed
- **GitHub token access updates as you type** — in Worker Settings → GitHub, the "Token access" checklist now validates the token you're entering live (shortly after you stop typing), so you can see its account and scopes immediately instead of having to save and reopen settings (Backend + Client)
- **Pull requests from archived repositories are no longer shown** — archived repos are read-only, so their PRs can't be reviewed or merged. They are now skipped when syncing, and any that were already listed are removed automatically (Backend)
- **Every way of installing the worker now sets it up identically** — installing from the disk image, the one-line install command, or the developer build used to each register the worker slightly differently (and one of them didn't register it as a background service at all). They now all produce the same setup, so the desktop app and the command line always find and control the same worker (macOS) (Backend)
- **Sessions no longer auto-close by default** — sessions used to be automatically ended after 6 hours of idle time. The auto-close timeout is now configurable from Worker Settings → Session Limits, and **disabled by default**. Set "Inactivity Timeout (minutes)" to a positive number to opt in; set it to 0 (or leave it blank) to keep sessions open indefinitely. Changes take effect without restarting the worker (Backend)

### Fixed
- **Queued messages now send when you pause** — if you typed follow-up messages while the agent was working, pausing now interrupts the current turn and immediately delivers those queued messages (like Claude Code), instead of holding them until you click Resume (Backend)
- **Esc pauses the running session from anywhere in the pane** — pressing Esc while a session is running now pauses it (same as the pause button) regardless of where focus is in the pane, not only when the chat input is focused (Client)
- **AI assist from a pull request now opens in the right project** — starting a summarise / fix / resolve-conflicts session from a PR could land in a different local checkout that happened to share the same folder name. The session now uses the exact repository the PR maps to (Backend + Client)
- **Refreshing the Pull Requests tab no longer errors when a worker has no GitHub token** — the tab syncs every connected worker, but a single worker without a token (or running an older version) made the whole refresh fail with a "GitHub token is not configured" error, even when your other workers were set up correctly. Token-less workers are now skipped, each worker is synced independently, and the refresh reports how many synced (Backend + Client)
- **Stopping the worker from the menu bar now actually stops it** — on macOS, pressing Stop (or quitting the app) used to kill the worker only for it to immediately respawn itself, so it looked like it never stopped. The worker now stays stopped when you stop it, while still recovering on its own if it genuinely crashes. Upgrading from an affected version repairs the setup automatically the first time the app or the new commands run (Backend)
- **Messages you queue while Claude Code is busy are no longer silently lost** — sending a follow-up while a Claude Code session was running could mark the message as delivered in the chat without the agent ever actually receiving it. Asking the agent afterwards would surface this — it had no memory of the queued message. Queued messages now always wait for the current turn to finish and are then delivered as a fresh turn, so the agent reliably sees them with full context (Backend)
- **Clarifying questions from Claude Code are now truly interactive** — when the coding agent asks a question, it genuinely waits: the question appears in the chat, the agent pauses, and the option you pick is handed back to the agent as its answer so it continues in the same turn with the right context. Previously the agent gave up and recorded "no answer" before you could respond. The session also stays responsive while the question is open (Backend)
- **Background watches now finish on their own without needing you to send a message** — when a Claude Code watch ended on its own (script exited, timed out, or stopped from the chat), the watch card kept ticking and the strip above the input kept listing it as live until you typed a follow-up message; only then would the "stopped" / "exit code N" state and any work the agent did after the watch suddenly appear. Updates between turns are now drained continuously while a watch is alive, so the card transitions to its terminal state immediately and any subsequent agent activity streams in real time (Backend)
- **A leftover spinning output card no longer hangs in the chat after a background command finishes** — when Claude Code ran a command in the background and it completed between turns, the completion notice opened an extra output card that kept spinning forever, even though the rest of the agent's work streamed in correctly. That card now closes as soon as the agent continues (Client)
- **Direct tool mode now honours the project badge when launching an agent** — sending `#claude_code`, `#codex`, or `#opencode` without an `@Project` mention used to start the agent in the parent projects folder instead of the project you'd picked in the badge, so the first thing the agent saw was a directory full of unrelated repositories. The agent now starts in the selected project's folder, matching what the badge shows (Backend)
- **Pausing a session no longer shows a spurious "exit code -9" error or causes infinite loading on resume** — pressing pause while the coding agent was actively running could race with the agent's output stream, causing a misleading error message and a duplicate message that left the client stuck in a loading state after unpausing or sending a follow-up message. The session is now marked as paused before any subprocess is killed, so the stream handler always takes the correct clean-up path (Backend)
- **Active sessions keep their title and chat history after a backend crash or kill** — if the worker was killed or crashed mid-conversation, the next startup would mark the session as interrupted but show it with no title and no messages because the in-memory state had never been written to disk. Active sessions are now flushed to the database every 30 seconds, so a crash restores the session with its title, conversation, token counters, and full chat history intact (Backend)
- **Auto-generated session titles could disappear after a worker restart** — auto-generated session titles now save to the database the moment they are assigned, so they remain visible after an unclean restart and after the new inactivity-timeout auto-close. Previously, if the worker process was killed before a session reached a terminal state, its title was lost on the next start (Backend)
- **Coding-agent sessions failed with "model: String should have at least 1 character"** — leaving the Model field blank in Worker Settings used to save an empty value to the agent's configuration, which the CLI then forwarded to the upstream API as an empty model name and the API rejected with a 400 error. The empty value is now dropped on save, so a blank Model field correctly means "use the CLI default" and existing blank entries are healed the next time settings are saved (Backend)

### Performance
- **Much faster Pull Requests refresh** — refresh now fetches only the states you're viewing (open by default) and fully loads detail/status only for those, instead of pulling and enriching dozens of recent merged/closed PRs every time. Merged and closed are listed lightly and fetched only when you turn on their filter (Backend + Client)

### Security
- **Updated dependencies flagged by security advisories** — bumped aiohttp, idna, and starlette/FastAPI to versions that resolve four reported vulnerabilities. No configuration changes required (Backend)
- **Patched four high-severity advisories in upstream dependencies** — picks up fixes for sensitive-header forwarding on proxied redirects, a decompression-bomb safeguard bypass, denial of service via unbounded multipart headers, and a Windows path-traversal in templating. No configuration changes required (Backend)

---

## [Backend 0.43.0 / Client 1.46.0] — 2026-04-29

### Added
- **About RCFlow Worker panel on macOS** — the worker's App menu now opens a populated About panel with the app icon, current version, and a short credits paragraph instead of an empty placeholder dialog. Click the leftmost menu (left of "File") and pick **About RCFlow Worker** (Backend)
- **Status dot in the macOS tray menu** — the first menu row ("RCFlow Worker: Running" / "Stopped") now shows a green or grey dot at a glance, so you can tell whether the daemon is up without reading the text (Backend)

### Changed
- **Branded macOS menu bar icon** — replaces the generic SF Symbol bolt with a monochrome RC + flow-arrows mark. The icon is a template image, so macOS auto-tints it to match the active menu bar theme (light, dark, or transparent backgrounds) (Backend)
- **Redesigned app icon across all platforms** — blocky 5-segment "C", horizontal flow-arrows, and a centered composition, applied to the Worker app's Dock/Finder icon, the Flutter client's icons (Android, iOS, web, macOS, Windows), and the Linux indicator. The macOS App menu, Dock tooltip, and window title now read "RCFlow Worker" rather than "Python3.12" when running unfrozen (Backend + Client)
- **Tray menu entries get leading icons** — Dashboard, Start/Stop server, Copy Token, Add to Client, Update available, Check for Updates, and Quit each show an SF Symbol next to the title for faster scanning. The "Start with macOS" toggle uses an inline checkmark icon instead of macOS's built-in state column, so toggling it no longer shifts the rest of the menu rightward (Backend)

### Fixed
- **macOS worker startup no longer flashes through three icons** — when launching `rcflow gui` on macOS, the Dock used to show the Python launcher icon, then a "killed" gap, then a generic white-document placeholder before settling on the colored RC icon. The dashboard window now appears in one frame with the right Dock icon already painted (Backend)

---

## [Backend 0.42.1] — 2026-04-28

### Fixed
- **macOS worker dashboard no longer crashes on sleep/wake** — on macOS arm64 with Tcl 9.0 the dashboard could crash with a `SIGBUS` or `SIGSEGV` when the machine woke from sleep, because status updates and progress callbacks were touching the UI from background threads. All background-thread UI updates are now marshalled safely through the main thread (Backend)
- **Code blocks pasted into a new prompt now reach the coding agent** — when you started a new session and included a fenced code block in your message, the block was sometimes dropped before being handed off to the coding agent (Claude Code, Codex, or OpenCode). The agent received only the LLM's paraphrased task description without the verbatim code. Code blocks from your message are now always preserved and attached to the agent task under the **Additional Content** section (Backend)

---

## [Backend 0.42.0 / Client 1.45.0] — 2026-04-27

### Added
- **Native Linux worker dashboard window** — `rcflow gui` on Linux now opens the same CustomTkinter dashboard the worker uses on Windows and macOS, with a system tray icon that respects the desktop's light/dark theme and surfaces toast notifications when a new version is available.  Closing the window minimises to the tray; second launches reveal the running window instead of opening a new one.  The dashboard runs under your system's `python3` so it sidesteps the libxcb 1.17 crash that affected the previous bundled-Tk path on Ubuntu 25.04.  Stock GNOME still needs the AppIndicator/KStatusNotifierItem extension for the tray; KDE Plasma, XFCE, Cinnamon, MATE, and Sway/waybar host the tray natively.  When the GUI dependencies are missing the dispatcher falls back to opening the dashboard URL via `xdg-open` and prints an apt-install hint (Backend)
- **RCFlow Client shows up in the Linux app menu** — the client `.deb` now ships an `rcflow-client.desktop` launcher and an icon, plus a postinst that refreshes the desktop / icon caches so the entry surfaces immediately without a logout cycle (Client)

### Changed
- **Headless worker no longer crashes when /home/rcflow is missing** — the bundled systemd unit now sets `HOME=/opt/rcflow` and grants the service write access to the whole install directory, so XDG-style settings paths resolve to a directory the service can actually write to (Backend)

### Fixed
- **macOS worker shows tools and agents after the app is moved** — running the worker straight from a mounted DMG, or copying it from `/Applications` to a different folder, no longer leaves the client showing "no tools" and an empty agent picker. The path to the bundled tools is now resolved each launch from the running app's location instead of being frozen on first launch — and the Linux, macOS, and Windows installers no longer write the path into `settings.json` at install time either, so relocating any install just works. Existing installs stuck in the broken state can recover by deleting the `TOOLS_DIR` line from the worker's `settings.json` and relaunching (Backend)
- **Mention autocomplete no longer blocks dialog buttons** — typing `#`, `@`, `$` or `/` in the chat input opens a suggestion list. Previously the list would stay floating on top of any dialog or settings panel you opened next (End Session, Worker settings, etc.), making the buttons inside that dialog unclickable until you tapped outside to dismiss the list. The list now closes automatically the moment the input loses focus, so opening any dialog or popup just works (Client)
- **Right-click "Copy selection" now actually copies your selection** — right-clicking a selected piece of a chat message used to clear the selection just before the menu opened, so the menu showed "Copy" / "Copy as Markdown" against the whole bubble instead of "Copy selection" / "Copy selection as Markdown" against the bit you'd highlighted. The most recent non-empty selection is now retained for the menu, so the right-click menu and copy actions stay anchored to whatever you had selected (Client)

---

## [Backend 0.41.0 / Client 1.44.0] — 2026-04-26

### Added
- **Worker checks for updates on its own** — the worker dashboard (Windows tray, macOS menu bar) now polls GitHub once a day for new releases. When a newer version is available you'll see an amber banner in the dashboard and an "Update available" entry near the top of the tray/menu-bar menu. Clicking **Download & Install** streams the platform-matched installer to a temp folder and asks if you want to launch it (or just reveal the file in your file manager). A new **Updates** card in the dashboard shows last-checked time and current vs. latest version, lets you trigger a manual check, and includes a toggle to turn off automatic checks entirely. Dismiss the banner and it stays hidden until an even newer version ships. Headless `rcflow run` (systemd, Docker) is unaffected — package managers handle those installs (Backend)
- **Live model dropdowns** — the model picker in worker settings (and in each managed coding agent's settings) now shows the actual list of models your API key can see, fetched from the provider directly. The list refreshes when you change keys or hit the refresh button next to the field, and a small status chip shows whether you're looking at a live result, a cached one, or the bundled fallback. Works for Anthropic, OpenAI, Bedrock, and OpenCode (which lists OpenRouter's catalog). Custom model strings still work — anything you type that isn't in the list is accepted (Backend + Client)
- **Clearer message when LLM key is missing** — if the worker has no API key configured, the client now shows a friendly "API key not configured" message and a yellow banner with a Configure button instead of a confusing 401 error. The banner clears as soon as you save a key (Backend + Client)
- **Warning when a coding agent has no key/login** — when an agent badge (Claude Code, Codex, OpenCode) is on a pane, a yellow banner now appears on top of the chat if that agent has no API key or login configured on the worker, with a Configure button that jumps to the right tool settings. Stops prompts from silently hanging inside the CLI's login screen. If the prompt is sent anyway, the chat shows an actionable error pointing to the same place (Backend + Client)
- **Automatic port forwarding (opt-in)** — the worker can ask your router (via UPnP) to open an external port automatically, so you can share the worker's address with remote clients without configuring your router by hand. Off by default; enable with the GUI checkbox or the `--upnp` flag (Backend)
- **VPN port forwarding for CGNAT users (opt-in)** — when your ISP puts you behind CGNAT and UPnP can't help, the worker can negotiate a public address through your VPN's gateway instead. Works with ProtonVPN Plus on P2P servers (the most common case), Mullvad, and any other provider that speaks NAT-PMP. The dashboard's new "VPN Address" row shows the address external clients can use. Off by default; enable with the GUI checkbox or the `--natpmp` flag. Independent of UPnP — both can be on at once (Backend)
- **"Add to Client" button** — one-click way to add a worker to the client. The button opens the client and prefills the connection details. If the worker is already added, the client says so instead of creating a duplicate (Backend + Client)
- **Start minimised option** — `rcflow gui --minimized` (also used by autostart) launches with only the tray icon visible, so reboots don't pop the dashboard in your face. Click the tray icon to open it (Backend)

### Changed
- **Model dropdowns refresh in real time** — saving a new API key, finishing an Anthropic Login or ChatGPT login, or switching providers now refreshes the model dropdown immediately. The "Restart required" hint is gone from credential and model fields since the worker hot-reloads them on save (Backend + Client)
- **Coding agents now require an explicit provider pick** — the "Global" option has been removed from the Claude Code / Codex / OpenCode provider dropdowns. Each agent has its own auth source (Anthropic key, Anthropic Login, Bedrock, OpenAI, ChatGPT subscription) independent of the worker's LLM provider. Existing setups keep loading; the warning banner stays on until a provider is picked. Save is blocked until a provider is selected (Backend + Client)
- **Coding agents are managed by RCFlow only** — external installs on the system PATH are no longer detected; every coding agent runs under RCFlow's managed copy so the per-tool config dir is the single source of truth. The managed/external toggle has been removed; a "managed" badge stays visible so you know this is a separate copy from any other install you may have. Agents that aren't installed yet now hide all configuration fields and show only the Install button — no more pretending you can pick a model for something that isn't there (Backend + Client)
- **macOS dashboard now opens on launch** — the worker window is visible when you start `rcflow gui` instead of hiding in the menu bar. Closing it still tucks it back to the menu bar (Backend)
- **macOS: clicking the app again opens the dashboard** — double-clicking the app, clicking it in the Dock, or running `open` on it while it's already running now reliably brings the dashboard to the front (Backend)
- **Claude Code "Undercover" toggle marked as coming soon** — the toggle is now greyed out with a "Coming Soon" chip, and the worker rejects attempts to change it. Re-enables once the feature ships (Backend + Client)

### Fixed
- **Switching to direct-tool mode no longer crashes** — changing the LLM provider to "none" while the worker is running used to crash. It now switches cleanly without a restart (Backend)
- **Direct-mode session title no longer shows the agent tag** — sessions started with `#claude_code …` used to be titled `#claude_code …`; the tag is now stripped from the title (Backend)
- **Direct-mode shell tools no longer look stuck** — running `#cmd ls` (or any non-agent tool in direct mode) used to leave the tool block with a static refresh-style icon and no way to expand the output, even after the command had finished. The tool block now finalizes the moment the command exits — the icon flips to the completed state, the output becomes expandable, and the in-progress indicator is now an actual spinner (Backend + Client)
- **Direct-mode shell tools now run in the chosen project folder** — `#cmd ls` (and any shell tool) used to ignore both the `@Project` mention and the session's selected project, running in the worker's cwd instead. Shell tools now use the `@Project` directory when given, falling back to the session's selected project when not (Backend)
- **Claude Code Anthropic login: retrying a typo'd code no longer fails** — a single mistake in the pasted login code used to wipe the session and force a full restart of the browser flow. Mistyped codes can now be retried in place; the session is only retired on a successful login (Backend)
- **Saving tool settings on Windows no longer 500s when the file already exists** — atomic writes for tool settings, plugin state, and managed CLI binaries used a POSIX-only rename that refused to overwrite on Windows. Saves now succeed regardless of platform (Backend)
- **Updating a coding agent right after using it no longer fails with "Access is denied" on Windows** — Windows refuses to overwrite a running ``.exe`` directly. The installer now renames the in-use binary aside, drops the new one in place, and best-effort-deletes the parked copy on the next install. The update completes immediately even if the old binary is still loaded by another process (Backend)
- **Install hint uses the display name** — the "Install … to configure it." line now reads "Install Claude Code …" instead of the internal ``claude_code`` token (Client)
- **Default coding agent dropdown wording is no longer LLM-specific** — the unset option used to read "None (let LLM decide)", which made no sense in direct mode. It now reads "No preference" (Client)
- **"No preference" actually means none** — when a worker's default coding agent is unset, new sessions used to silently fall back to whichever agent you happened to use last, which surprised users. New sessions now start with no agent badge unless you've explicitly picked one in the worker config (Client)
- **Warning banner now disappears immediately after a successful tool-settings save** — used to stay visible until reconnect (Client)
- **Anthropic Login auto-detected: provider switches without manual dropdown change** — opening the Claude Code config when the CLI is already authenticated now stages "Anthropic Login" as a pending edit. A single Save closes the loop instead of needing the user to also pick the matching dropdown option (Client)
- **Direct mode on Windows now streams agent output** — the Claude Code CLI ignores the positional prompt argument under stream-json input on a non-TTY pipe (Windows pipe mode). The worker now delivers the initial turn over stdin instead, so direct-mode prompts produce live output the same way they do on macOS/Linux (Backend)
- **Picking an agent that isn't installed shows the right warning** — used to report "no provider selected" even when the agent CLI was missing entirely. Now says "Claude Code is not installed." / "Codex is not installed." / "OpenCode is not installed." so the Configure button takes you to the install action, not a dropdown that won't help (Backend + Client)
- **"Add to Client" used the wrong address** — when the worker was listening on all network interfaces, the button passed an unusable `0.0.0.0` to the client. It now uses your real LAN address (Backend)
- **"Add to Client" worked only after running the installer on Windows** — clients built without the installer didn't register the URL handler, so clicking the button showed Windows' "You'll need a new app" prompt. The client now registers the handler on every launch (Client)
- **Worker dashboard showed the Python icon on Windows** — the dashboard now displays its own icon in the taskbar instead of borrowing Python's (Backend)
- **Worker dashboard icon looked blurry on high-DPI Windows displays** — the icon is now picked at the correct resolution per display scale, so it looks sharp at 125%+ scaling (Backend)
- **Quit from the macOS menu bar froze the cursor** — clicking Quit no longer leaves the cursor spinning or the menu bar icon hanging around while the worker shuts down (Backend)
- **Launching the worker GUI a second time now opens the existing window** — instead of doing nothing (or relying on a flaky AppleScript fallback), a second launch on macOS or Windows brings the running dashboard to the front (Backend)

### Security
- **Patched dependencies with known vulnerabilities** — bumped Pillow (FITS GZIP decompression bomb), python-multipart (DoS via large preamble/epilogue), pytest (insecure tmpdir handling), Mako (path traversal in TemplateLookup), and python-dotenv (symlink-following file overwrite in `set_key`/`unset_key`) to their patched releases (Backend)

---

## [Backend 0.40.1 / Client 1.43.2] — 2026-04-23

### Added
- **Copy from rendered text** — right-click any rendered message, artifact, task, or plan card to copy as plain text or as Markdown (Client)
- **Markdown in agent-start bubble** — the prompt shown when an agent starts now renders Markdown instead of showing raw `##` and `**` characters (Client)
- **Queued messages** — send a new prompt while the agent is still working and it waits in line at the bottom of the chat. Edit or cancel it before the agent picks it up. The queue survives restarts (Backend + Client)
- **Caveman mode** — strips filler words from agent output to cut tokens by 65–75%. Toggle per session
- **Saved drafts** — your half-typed message is saved per session, so you don't lose it when switching away
- **Client auto-update** — client checks GitHub for new releases and offers to update
- **Drag-and-drop session order** — reorder sessions in the sidebar; order is saved on the server
- **Hide Claude Code identity** — optional setting that hides the "Claude Code" identity from the model. Off by default
- **Android bottom-nav layout** — Sessions / Chat / Settings tabs at the bottom on phones
- **Session identity bar** — shows the active worker, project, and agent at the top of the chat
- **Remove artifact from tracking** — right-click an artifact to remove it from the list (Client)
- **Multi-select artifacts** — Shift+click for ranges, Ctrl/Cmd+click to toggle, right-click to bulk-delete, Esc to clear (Client)
- **Unified session badges** — status, worker, agent, caveman, project, and worktree badges on every session, including archived ones (Backend + Client)
- **Agent badge preview** — picking an agent before sending the first message shows its badge straight away (Client)
- **Edit/Write diff line counts** — collapsed Edit/Write blocks show `+N −M` next to the filename. Auto-expand when loading history (Client)
- **Themed checkboxes** — checkbox lists in messages, artifacts, and tasks render as styled icons matching the app theme (Client)
- **Create worktree from input area** — the worktree chip dropdown has a new "Create worktree" option; the new worktree is selected automatically (Client)
- **Backend version in `/server-info`** — endpoint now reports the worker version (Backend)
- **Smarter task-routing prompt** — agent now picks between answering directly, running a shell command, or delegating to a coding agent based on clearer rules (Backend)

### Changed
- CI push trigger restricted to `main` so PRs don't run twice
- **Tidier diff frames** — Edit/Write diffs sit in a rounded frame with proper padding instead of bleeding to the tool card edge (Client)
- **Session reorder only via the grip icon** — long-pressing anywhere else on a session row no longer starts a drag by accident (Client)
- **Faster session list with many sessions** — only visible session tiles are built. Workers with hundreds of sessions no longer freeze the UI when expanded (Client)

### Performance
- **Android chat no longer lags while streaming** — multiple render-path fixes (notification coalescing, stable list keys, cached message widgets, sessions tab no longer re-runs while you chat) keep the transcript smooth on phones (Client)

### Fixed
- **Android release build failed** — `integration_test` no longer trips the release build (Client)
- **Android bottom navigation didn't show up** — the new bottom-tab layout is now actually wired in on Android (Client)
- **Bash output and Edit/Write diffs missing from Claude Code** — tool results are now read from the right place in the event stream, so Bash stdout is expandable and diffs render again (Backend)
- **Worker kept running after macOS GUI crash** — if the dashboard crashes, the worker now shuts down with it. A relaunch can also adopt and stop a leftover worker, with a "recovered" badge in the status (Backend)
- Squashed migration reference fixed
- Duplicate `targetWorker` field in draft messages
- Dart lint warning about null-aware map elements
- **"Copy Token" said "No API token configured" on a clean install** — token is now generated and written before the worker subprocess starts, and the success/error message stays visible long enough to read (Backend)
- **"Exit code: None" error on follow-up messages** — error paths now record the exit code properly and restart Claude Code via `--resume` when the process has already exited (Backend)
- **Follow-up messages dropped when Claude Code exited mid-turn** — worker now waits for the current turn to finish before sending the next message, and restarts the agent if it has already exited. Also fixes duplicate "Task complete. End this chat?" prompts (Backend)
- **Caveman mode didn't engage for externally-installed Claude Code** — caveman system prompt is now applied regardless of how Claude Code was installed (Backend)
- **Agent group block showed "Claude Code" instead of `claude_code`** — display priority swapped to match the start bubble (Client)
- **Switching to a loaded older session wiped the list** — removed a redundant refresh that was clearing sessions loaded via "Load more" (Client)
- **Sending a message reset the expanded session list** — refreshes now request the same number of sessions you already had loaded (Client)
- **Claude Code ignored messages sent mid-turn** — worker now waits for the current turn to finish instead of cancelling it, so the follow-up reads the right response (Backend)
- **Duplicate "Task complete. End this chat?" prompts** — only the latest turn's completion fires the prompt now; the client also deduplicates as a safety net (Backend + Client)
- **"Load more" pulled sessions in the wrong order** — pagination now starts from the newest session and walks back (Backend)
- **Draft survived after sending** — sending a message now clears the saved draft (Client)
- **Non-JSON Claude Code output broke the message stream** — banners and debug lines no longer corrupt the chat; they're tagged as logs instead (Backend + Client)
- **Diff-only Edit/Write tool results were dropped** — diff blocks render even when there's no text content (Backend)
- **Session order lost across restarts** — custom order is now saved and restored (Backend)
- **`#tool_mention` tags showed up in chat history** — mention markers are stripped before the message is stored or echoed (Backend + Client)
- **Archived sessions showed the internal worker ID instead of the friendly name** — friendly name is now substituted on archived sessions too (Client)
- **Ghost sessions after a crash** — empty sessions left over from a mid-startup crash are now deleted on restart instead of reloaded (Backend)
- **Restored agent mention used the raw key instead of the display name** (Client)
- **Garbled punctuation (`â€—`, `â†’`) in loaded history** — REST responses are now decoded as UTF-8 instead of using the OS locale, so punctuation renders correctly on Windows / non-UTF-8 systems (Client)

---

## [Backend 0.31.4 / Client 1.33.4] — 2026-03-30

### Added
- **Task planning** — agents can produce plan artifacts; the client shows a plan-review card before execution
- **Select text in code blocks** — long-press to select inside Markdown code blocks (Client)
- **macOS menu-bar app** — native menu-bar GUI with a DMG installer; GUI look & feel now shared across platforms
- **Windows client installer** — proper `.exe` installer for the Windows client
- **Collapsible sidebar sections** — sidebar sections fold/unfold per tool
- **Worker remembers last project and agent** — client restores the project and agent you last used per worker
- **Worktree selection sticks** — selected worktree survives session switches and restarts
- **Resume interrupted sessions** — sessions cut off mid-turn are flagged and can be resumed
- **Project picker** — UI for choosing the working project directory per worker
- Unified artifact naming across platforms; macOS x64 builds added to CI

### Changed
- Security hardening pass; added a per-session turn limit; removed sound/summary features (privacy-sensitive)
- Database migrations squashed into a single initial schema (16 → 1)
- README overhauled for public release; coverage badge generated locally

### Fixed
- `cryptography` bumped to 46.0.7 to patch a buffer-overflow CVE
- Missing worktree paths now return 404 instead of crashing
- Async gap warning in the worktree fetch path
- Various CI fixes for Linux Flutter, Kotlin, and Gradle

---

## [Backend 0.21.0 / Client 1.22.0] — 2026-03-18

### Added
- **Linear integration** — issues panel, issue tiles, sync button, and the ability to link tasks to artifacts
- **Telemetry** — per-session token, cost, and turn statistics stored in the database, with a charts pane
- **Project panel** — sidebar pane that tracks the main project directory per session
- **Worktree panel** — sidebar pane for managing git worktrees; max-turns pause card
- **Image and file attachments** — file picker and attachment chips in the input area; vision support via the LLM
- **Slash commands and `#tool` mentions** — pick tools/commands directly from the input area
- WebSocket message routing hardened against missing fields

### Changed
- Pane state refactored to support the multi-panel sidebar and richer pause context
- Client extended with worktree and attachment models

### Fixed
- Worker no longer crashes opening log files as a non-root user
- WSS now on by default; log helpers write to stderr

---

## [Backend 0.11.0 / Client 1.9.0] — 2026-03-16

### Added
- **OpenAI reasoning models** — support for the `o1`/`o3`/`o4` series with reasoning-token tracking
- **Model selector** — provider-aware model dropdowns in settings
- **macOS support** — managed-tool downloads detect macOS; login-time autostart via LaunchAgent
- **Anthropic login/logout** — Claude Code provider is set automatically when credentials change
- Bundle `--install` flag and justfile install/uninstall targets
- `wt` worktree CLI bundled as a project dependency
- Installer guards against running as root and resolves log paths correctly

### Changed
- Tool bubble expand arrow only appears once the tool finishes and has content (less visual noise)

### Fixed
- Skip `systemctl` calls in WSL2 where systemd isn't running

---

## [Backend 0.5.0 / Client 1.5.0] — 2026-03-09

### Added
- **Artifacts panel** — files produced by agents are listed and openable from the UI
- **Task tracking** — in-session task list with status indicators
- **Token usage** — input, output, and cache tokens shown per message
- **Thinking blocks** — Claude's extended thinking rendered inline
- **Keyboard shortcuts** for common actions
- **System notifications** on session events
- **OpenAI provider** — chat completions as an alternative to Claude Code
- **Terminal sessions** — persistent terminal pane inside a session
- **Windows GUI** — Windows system-tray launcher
- **Direct tool mode for Claude Code** — bypass the LLM and run tools directly

### Changed
- Major UI overhaul: redesigned message bubbles, session sidebar, and layout

---

## [Backend 0.1.0 / Client 1.0.0] — 2026-03-02

### Added
- First public release of RCFlow
- FastAPI worker with WebSocket-based agent orchestration
- Flutter client (Linux, Android) with session-based chat
- Claude Code (Anthropic) and Codex (OpenAI) executors
- SQLite database with async ORM and migrations
- Interactive permission approvals for agent tool calls
- Sessions: create, list, switch, delete
- Settings via environment variables and `.env`
- Linux systemd install/uninstall scripts
- `justfile` with dev, test, lint, format, and bundle targets

[Unreleased]: https://github.com/Flowelfox/RCFlow/compare/v0.42.1...HEAD
[Backend 0.42.1]: https://github.com/Flowelfox/RCFlow/compare/v0.42.0...v0.42.1
[Backend 0.42.0 / Client 1.45.0]: https://github.com/Flowelfox/RCFlow/compare/v0.41.0...v0.42.0
[Backend 0.41.0 / Client 1.44.0]: https://github.com/Flowelfox/RCFlow/compare/v0.40.1...v0.41.0
[Backend 0.40.1 / Client 1.43.2]: https://github.com/Flowelfox/RCFlow/compare/v0.31.4...v0.40.1
[Backend 0.31.4 / Client 1.33.4]: https://github.com/Flowelfox/RCFlow/compare/v0.21.0...v0.31.4
[Backend 0.21.0 / Client 1.22.0]: https://github.com/Flowelfox/RCFlow/compare/v0.11.0...v0.21.0
[Backend 0.11.0 / Client 1.9.0]: https://github.com/Flowelfox/RCFlow/compare/v0.5.0...v0.11.0
[Backend 0.5.0 / Client 1.5.0]: https://github.com/Flowelfox/RCFlow/compare/v0.1.0...v0.5.0
[Backend 0.1.0 / Client 1.0.0]: https://github.com/Flowelfox/RCFlow/releases/tag/v0.1.0
