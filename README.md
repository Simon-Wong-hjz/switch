# Switch

**Keep track of delegated work without keeping a window open for every conversation.**

English · [简体中文](README.zh-CN.md)

Switch is a local task board for Codex and Claude Code sessions. Group work by priority, see what is running or waiting, and read the latest reply before deciding whether to return to the original conversation. It is useful with one client and many tasks; mixing clients is optional.

![Switch with example sessions in the dark theme](docs/images/switch-dark.png)

*Example data. No real conversations are shown.*

## What it does

- **One view of your work.** Priority, Later and Unsorted columns, drag ordering, and independent scrolling.
- **Read before switching.** Expand multiple cards to read their latest complete replies. New replies load automatically; unchanged replies keep their reading position.
- **Choose what interrupts you.** Only Priority sessions send optional desktop alerts and count toward the App badge.
- **Return to the conversation.** Open Desktop sessions directly, or copy a CLI resume command. Creating tasks, replying and approving actions stay in the original client.
- **Make it yours.** Chinese/English and light/dark switches, saved groups and ordering, and settings import/export.

Switch reads local session records. It makes no model calls and does not upload conversations. The Python backend uses only the standard library; the browser renderer is bundled in the repository.

## Get started

You need **Python 3.9+**, a browser, and local Codex or Claude Code sessions. There is no `pip install`, Node.js requirement, or desktop framework to install for normal use.

```sh
git clone https://github.com/simwong98/switch.git
cd switch
python3 taskboard.py
```

You can also [download the source ZIP](https://github.com/simwong98/switch/archive/refs/heads/main.zip), extract it and run the last command there. On Windows, use `python` if that is your Python command.

The command opens `http://127.0.0.1:8765/`. Choose the sessions to add, then drag them into a group. New sessions created after the board is initialized are added automatically unless previously removed. The service refreshes data every five seconds; the refresh button requests an immediate scan.

Keep the terminal running, or enable the optional macOS login service below. Press Ctrl-C to stop, or run `python3 taskboard.py stop` from another terminal. For another port, use `python3 taskboard.py run --port 8766`; add `--no-open` to avoid opening a browser.

**Platform status:** used and tested on macOS with Python 3.12 and Edge. On Windows with Python 3.14, Codex session discovery, UTF-8 titles and replies, native read-state synchronization, HTTP endpoints, service start/stop, and isolated hook installation/execution/removal have been verified. Windows browser interactions, App installation/removal, notifications, real-client hook delivery and Claude Code remain unverified; Windows support is still experimental. Chrome supports the browser App entry, but its complete installation and removal flow has not been manually verified here. The interface is bilingual; command-line messages are currently Chinese.

## Install as an App

Click **Install app** in the board's top bar. It explains the benefits, then opens the browser's install dialog when available, or shows Edge/Chrome instructions.

Installing gives Switch its own Dock or taskbar entry and a separate window. The icon can show the number of Priority sessions needing attention where badges are supported. The App and browser page share the same board settings.

The App **does not start the Python backend** and is not always on top. Keep its window and the backend running for alerts and badge updates. Minimized is fine, but a suspended page cannot keep data current. Use the same host and port: `localhost`, `127.0.0.1`, and different ports are separate browser App addresses.

## Optional setup

### Start at login on macOS

```sh
python3 taskboard.py autostart on
```

This starts Switch in the background now and at future logins. It keeps an existing configured port, or uses 8765. macOS restarts it after an abnormal exit. `stop` shuts it down normally until the next login; `autostart on` resumes it. Use `autostart off` to stop it and disable login startup. Windows login startup is not implemented yet.

### Add client hooks

The board works by reading local records without hooks. Hooks provide additional lifecycle events:

```sh
python3 taskboard.py setup
python3 taskboard.py status
```

Setup lists proposed changes and asks before applying them. Use `--only codex` or `--only claude` to choose a client. Codex may require trusting the hooks through `/hooks`. Hooks record events; they do not answer questions or approve actions. `setup --yes` skips the interactive confirmation for an installation you have already authorized. It does not enable login startup; that requires `--autostart` explicitly.

You can give an Agent this installation request:

> Install Switch from https://github.com/simwong98/switch in a directory I choose. Follow its README, check Python, start it, and report the URL and uninstall command. Ask before enabling client hooks or login startup. Do not change my clients' notification settings.

## Alerts and read state

Enable alerts from the Priority column header. **Only Priority sessions notify and count toward the App badge.** Later and Unsorted cards still display their states. Switch does not change the clients' own notifications. Use **Test alert** to check browser and operating-system permissions; **Mute while board has focus** is optional and off by default.

Alerts cover new final replies, new questions and confirmed Claude Code approval waits. Existing states do not notify on page load or when moving a card into Priority. Alerts require an open board page and a running backend.

Codex Desktop conversations follow Codex's current read state on every refresh: listed as unread keeps the new-reply flag, otherwise the reply shows as read, including a reply that finished in a conversation you already had open. Marking a reply unread in Codex restores its flag; reading it again clears it. If Codex's state file is missing, unreadable, in an unknown format, signed out, or older than the reply, Switch keeps what it last knew and does not assume the reply was read. CLI sessions have no reliable read signal. Use **Mark this reply as read**, **Open**, or **Copy resume command** to acknowledge the reply in Switch. Expanding a card alone does not mark it read. Reading an older reply does not clear a newer one.

## Local files and clean removal

| Location | What Switch writes |
| --- | --- |
| `data/` inside this directory | Settings, cached board data, bounded hook logs, service records and startup logs |
| Codex `hooks.json` / Claude `settings.json` | Only optional hook entries owned by this installation |
| `~/Library/LaunchAgents/local.switch.<installation-id>.plist` | Only when macOS login startup is enabled |
| Browser-managed storage | Browser App registration, history, permissions and temporary page state |

Client conversations are read-only. Switch does not modify PATH or shell profiles. `status` lists the external integrations. Settings live in `data/settings.json`; export/import them from **Settings file** to move a board between installations.

To remove Switch:

1. If installed as an App, uninstall it from the App menu or browser's app manager.
2. Run the command below. It stops the backend, disables its login service, removes its unchanged hook entries and deletes its data.
3. Delete the source directory.

```sh
python3 taskboard.py uninstall --purge
```

Omit `--purge` to keep board data. Unrelated client settings and conversations are preserved. Modified hook entries or startup files are reported for review rather than overwritten. Disable login startup before moving the directory or changing Python, then enable it again at the new location.

## Scope and limitations

- Supports local interactive Codex Desktop/CLI and Claude Desktop **Code** / Claude Code CLI sessions. Programmatic/subagent sessions, Claude cloud chat and Cowork are outside scope.
- Reads the latest final reply, not full conversation history, tool output or file diffs. Review changes and continue the task in the original client or IDE.
- Relies on client-local formats that may change. Unknown approval state is not presented as a confirmed wait; Codex approval waits cannot currently be reliably detected.
- Codex Desktop read state mirrors the client's own classification, including when it marks a previously visible conversation read while opening another. Switch does not add an unread flag when the current native state classifies the reply as read.
- The backend listens on loopback only. It is a personal local tool, not a multi-user service; do not expose its port on a network. See [Security](SECURITY.md).

## Contribute

Bug reports and contributions in English or Chinese are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for tests and the repository layout, [CHANGELOG.md](CHANGELOG.md) for changes, and [SECURITY.md](SECURITY.md) for private security reports.

## License

Switch is licensed under the [MIT License](LICENSE). The bundled Markdown renderer and its dependencies retain their [upstream notices](web/vendor/THIRD-PARTY-NOTICES).
