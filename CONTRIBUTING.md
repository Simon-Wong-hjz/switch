# Contributing / 参与贡献

English and Chinese issues and pull requests are welcome. Keep discussions respectful, and keep private conversations, credentials and local runtime data out of reports and patches.

欢迎使用中文或英文提交 Issue 和 Pull Request。请尊重讨论中的其他人，不要提交私人会话、凭据或本地运行数据。

## Develop / 开发

Clone the repository and run `python3 taskboard.py`. There are no Python packages to install. Node.js 22 is used for JavaScript tests only; the application does not require it.

克隆仓库后执行 `python3 taskboard.py`，无需安装 Python 依赖。JavaScript 测试使用 Node.js 22，应用运行不需要它。

```sh
python3 -m unittest discover -s tests
node tests/test_template.js
node tests/test_settings_ui.js
node tests/test_reply_ui.js
node tests/test_reply_render.js
node tests/test_notification_edges.js
```

Installation tests use temporary homes and configuration files. Login-service unit tests mock system registration. Do not install hooks or startup jobs against your real profile as part of automated tests. Real client/browser checks are separate from these tests; state which platform and client version you verified.

安装测试使用临时主目录和配置，登录服务单元测试模拟系统登记。自动测试不应向真实用户配置安装 hooks 或启动项。真实客户端、浏览器验证与这些测试分开，请说明实际验证的平台和客户端版本。

For an isolated manual instance, set `TASKBOARD_HOME` to an empty test home and `TASKBOARD_DATA` to a test data directory, then run on a free port with `--no-open`. The test home overrides client-path environment settings. Do not run `setup --autostart` or `autostart on` in a disposable checkout: those commands register a real operating-system service.

手动验证可设置 `TASKBOARD_HOME` 为测试主目录、`TASKBOARD_DATA` 为测试数据目录，再选空闲端口并加 `--no-open` 启动。测试主目录会覆盖客户端路径环境变量。不要在临时副本运行 `setup --autostart` 或 `autostart on`，它们会登记真实的系统服务。

## Layout / 代码结构

| Path | Responsibility / 职责 |
| --- | --- |
| `taskboard.py` | Command-line entry and lifecycle / 命令行与进程生命周期 |
| `tb/core.py`, `tb/claude_source.py` | Local session discovery and current-state derivation / 本地会话发现与当前状态识别 |
| `tb/codex_read_state.py` | Codex Desktop read/unread observations / Codex Desktop 已读、未读同步 |
| `tb/board_server.py`, `tb/settings_store.py` | Loopback HTTP API and persisted board settings / 本机 HTTP API 与设置持久化 |
| `tb/replies.py`, `tb/excerpt.py` | Reply identity, on-demand text and excerpts / 回复标识、按需读取与摘录 |
| `tb/paths.py`, `tb/hooks.py`, `tb/hook.py`, `tb/autostart.py` | Paths and reversible client/system integration / 路径及可撤销的客户端、系统接入 |
| `web/` | Interface, reply reader, App manifest and bundled assets / 界面、阅读器、App 描述和本地资源 |
| `tests/` | Synthetic regression cases / 使用合成数据的回归测试 |
| `tools/make_icons.py` | Standard-library icon generator / 标准库图标生成器 |

## Changes / 提交改动

- Keep changes focused. Describe the user-visible problem, resulting behavior and verification in the pull request.
- Add a regression case for a bug or a meaningful behavior change. Use synthetic sessions and example domains, never copied personal transcripts.
- Update both README languages and both interface dictionaries when user-facing behavior or text changes.
- Preserve client-owned settings. Uninstall must remove only entries this installation owns and must report modified entries.
- Keep Python runtime dependencies at zero. Discuss changes that add persistent integrations or alter notification/read semantics before implementation.
- Use Conventional Commits, for example `fix: preserve unread state after restart`.

对应要求：改动集中，PR 说明问题、行为和验证；行为修复补相应测试，使用合成数据；用户可见变化同步中英文；保留用户配置，卸载仅移除本次安装拥有的内容；运行时保持 Python 标准库即可使用。新增常驻接入或改变通知、已读语义前，请先讨论。

## Reporting / 反馈

Include operating system, Python, browser, client versions, expected behavior and reproduction steps. Remove titles, paths, IDs and text that identify private work from screenshots or logs. Do not attach the `data/` directory or raw client configuration. Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

请提供系统、Python、浏览器和客户端版本，以及预期行为和复现步骤。截图、日志中应移除能识别私人工作的标题、路径、ID 和正文。不要上传 `data/` 或客户端完整配置。安全漏洞按 [SECURITY.md](SECURITY.md) 私密报告。
