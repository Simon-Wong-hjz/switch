# Changelog

## Unreleased

- Fixed duplicate Windows instances sharing an occupied loopback port; the listener now reserves its port exclusively and the real lifecycle regression also runs on Windows.
- 修复 Windows 第二个实例可绑定已占用端口的问题，改用独占监听，并启用真实启动、端口冲突、认证停止回归测试。
- Fixed Windows legacy-encoding failures in Codex session titles, native read-state loading and UTF-8 hook input. Added regressions and made the test suite portable across Windows/POSIX; POSIX-only integration checks remain explicitly skipped on Windows.
- 修复 Windows 默认编码导致的 Codex 中文标题乱码、原生已读状态读取失败、UTF-8 hook 输入丢失；新增回归用例并修正测试的平台假设，Windows 明确跳过 POSIX 专属集成检查。
- Fixed Codex Desktop new-reply flags that stayed after a reply finished in an already open conversation. Switch now follows Codex's current read/unread state on every refresh, and keeps its last known state when that state is unavailable or older than the reply.

Initial public version of Switch:

- Local Codex and Claude Code session board with Priority, Later and Unsorted groups.
- Multiple expanded cards, drag ordering, independent column scrolling and removal undo.
- Latest full-reply reading, automatic updates and Desktop read/unread synchronization.
- Chinese/English interface and light/dark themes.
- Optional Priority-only desktop notifications and browser App badges.
- Browser-managed App installation, optional client hooks and macOS login startup.
- Local settings import/export and ownership-aware uninstall.
- Python standard-library runtime, bundled Markdown rendering and synthetic regression tests.

首个公开版本：本地多任务看板、回复全文阅读、中英文与明暗主题、仅“重要”栏提醒及角标、浏览器 App、可选 hooks 与 macOS 登录自启，以及设置迁移和卸载清理。Windows 验证范围见 README。

修复：在已打开的 Codex Desktop 会话里完成回复后，新回复标记不再残留；现在每次刷新都以 Codex 当前的已读/未读状态为准，状态不可用或早于回复时保持已知状态。
