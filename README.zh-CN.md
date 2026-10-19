# Switch

**管理委托给 Agent 的工作，不必为每个会话留一个窗口。**

[English](README.md) · 简体中文

Switch 是一个本地任务看板，用于管理 Codex 和 Claude Code 会话。按优先级组织工作，查看哪些任务正在运行、哪些需要处理，读完最新回复后再决定是否回到原会话。只用一个客户端、同时处理多个任务，也能用上它；跨客户端只是额外的便利。

![Switch 浅色主题与示例会话](docs/images/switch-light-zh.png)

*截图使用虚构数据，不包含真实会话。*

## 能做什么

- **集中查看任务。** “重要”“稍后”“未分组”三栏，支持拖动排序，每栏独立滚动。
- **读完再切换。** 多张卡片可同时展开，阅读最新回复全文。新回复自动加载，同一条回复保留阅读位置。
- **决定哪些事值得提醒。** 只有“重要”栏会发送可选的桌面通知、计入 App 角标。
- **回到原会话。** Desktop 会话可直接打开，CLI 会话可复制恢复命令。创建任务、回复和授权仍在原客户端完成。
- **按习惯使用。** 中英文、明暗主题切换，保存分组和排序，支持导入、导出设置。

Switch 读取本地会话记录，不调用模型，也不上传会话。Python 后端只使用标准库，网页需要的 Markdown 渲染器随仓库提供。

## 开始使用

需要 **Python 3.9+**、浏览器，以及本机的 Codex 或 Claude Code 会话。日常运行不需要 `pip install`、Node.js 或桌面框架。

```sh
git clone https://github.com/Simon-Wong-hjz/switch.git
cd switch
python3 taskboard.py
```

也可以[下载源码 ZIP](https://github.com/Simon-Wong-hjz/switch/archive/refs/heads/main.zip)，解压后在目录内执行最后一条命令。Windows 上如果 Python 命令名是 `python`，用它替换 `python3`。

命令会打开 `http://127.0.0.1:8765/`。选择要添加的会话，再拖到相应分组。看板初始化后新建的会话会自动加入，之前手动移出的除外。服务每五秒更新数据，也可以点击刷新按钮立即扫描。

使用期间保持终端运行，或启用下方可选的 macOS 登录自启。按 Ctrl-C 停止，也可以在另一终端执行 `python3 taskboard.py stop`。需要更换端口时，执行 `python3 taskboard.py run --port 8766`；加 `--no-open` 可不自动打开浏览器。

**平台状态：** 已在 macOS、Python 3.12 和 Edge 上实际使用并验证。Chrome 支持浏览器 App 入口，但尚未在本项目中人工走完安装、使用及卸载全流程。Windows 路径已适配，真实客户端、安装和卸载验证仍待完成，目前属于实验性支持。网页界面支持双语，命令行提示目前为中文。

## 安装为 App

点击看板顶部的 **安装为 App**。面板会说明作用；浏览器支持直接安装时可打开安装框，否则显示 Edge、Chrome 的手动步骤。

安装后，Switch 有独立窗口和 Dock 或任务栏入口。支持角标的浏览器还会显示“重要”栏待处理会话数。App 和网页共用同一份看板设置。

App **不会启动 Python 后端**，也不会始终置顶。提醒和角标更新需要窗口与后端保持运行。可以最小化窗口，但页面被浏览器暂停后无法及时更新。请固定使用同一主机名和端口：`localhost`、`127.0.0.1` 及不同端口在浏览器看来是不同的 App 地址。

## 可选接入

### macOS 登录自启

```sh
python3 taskboard.py autostart on
```

现在就在后台启动，并在以后登录时启动。命令沿用已配置的端口，否则使用 8765。异常退出后由 macOS 重启；`stop` 正常停止后，会保持停止直到下次登录。再次运行 `autostart on` 可立即恢复。`autostart off` 则停止服务并关闭登录自启。Windows 登录自启尚未实现。

### 客户端 hooks

不安装 hooks 也能读取已有记录。Hooks 可以提供额外的会话生命周期事件：

```sh
python3 taskboard.py setup
python3 taskboard.py status
```

Setup 会列出拟修改的配置，确认后再执行。`--only codex` 或 `--only claude` 可指定客户端。Codex 可能需要通过 `/hooks` 信任新增条目。Hooks 只记录事件，不回答问题，也不批准操作。对于已经授权的安装，`setup --yes` 可以跳过交互确认；它不会顺带开启登录自启，后者需要明确指定 `--autostart`。

也可以把这段安装要求交给 Agent：

> 把 https://github.com/Simon-Wong-hjz/switch 安装到我指定的目录。按 README 检查 Python、启动看板，告诉我访问地址和卸载命令。开启客户端 hooks 或登录自启前先问我。不要修改客户端原有的通知设置。

## 提醒和已读状态

在“重要”栏头开启提醒。**只有“重要”栏会推送通知、计入 App 角标。** “稍后”和“未分组”的卡片仍显示各自状态。Switch 不修改客户端原有通知。可用 **测试提醒** 检查浏览器及系统权限；**看板获得焦点时静音** 是可选项，默认关闭。

新最终回复、新提问，以及确认处于授权等待中的 Claude Code 会话可以触发提醒。打开页面时已有的状态、拖入“重要”时已有的状态，不会补发通知。提醒需要看板页面和后端都在运行。

Desktop 的已读信号会同步到看板。在 Codex Desktop 中重新标为未读会恢复未读标记，再次阅读后会消除。CLI 会话没有可靠的已读信号，可以在 Switch 使用 **将这条回复标为已读**、**打开** 或 **复制恢复命令** 确认已读。仅展开卡片不算已读；阅读旧回复不会清除更新的回复。

## 本地文件和卸载

| 位置 | Switch 写入的内容 |
| --- | --- |
| 本目录下的 `data/` | 设置、看板缓存、有容量限制的 hook 日志、服务记录及启动日志 |
| Codex `hooks.json` / Claude `settings.json` | 仅在接入时添加本次安装拥有的 hook 条目 |
| `~/Library/LaunchAgents/local.switch.<installation-id>.plist` | 仅在启用 macOS 登录自启时添加 |
| 浏览器管理的存储 | 浏览器 App 登记、历史、权限及临时页面状态 |

客户端会话文件只读，不修改 PATH 或 shell 配置。`status` 会列出外部接入。设置保存在 `data/settings.json`，可以从 **设置文件** 导出、导入，在不同安装之间迁移看板。

完整卸载：

1. 如果安装为 App，先从 App 菜单或浏览器的应用管理中卸载。
2. 执行下方命令。它会停止后端、关闭登录自启、移除本次安装中未被修改的 hooks，并删除数据。
3. 删除源码目录。

```sh
python3 taskboard.py uninstall --purge
```

不加 `--purge` 会保留看板数据。其它客户端设置和会话不受影响；安装后被修改过的 hook 条目或启动文件会报告出来供检查，不会覆盖。移动目录或更换 Python 前先关闭登录自启，完成后再在新位置启用。

## 范围和限制

- 支持本地交互式 Codex Desktop/CLI、Claude Desktop 的 **Code** 会话及 Claude Code CLI。程序调用、子 Agent、Claude 云端聊天和 Cowork 不在范围内。
- 阅读器展示最新最终回复，不包含完整对话历史、工具输出或文件差异。审查修改、继续任务仍需回到原客户端或 IDE。
- 依赖客户端本地记录格式，客户端更新可能影响识别。无法确定的授权状态不会显示成确定的等待；目前无法可靠识别 Codex 的授权等待。
- Desktop 已读状态跟随客户端。如果 Codex 在打开另一会话时把之前停留的会话顺带标成已读，Switch 也会同步这一变化。
- 后端只监听本机回环地址。这是个人本地工具，不是多人服务，不应把端口暴露到网络。详见[安全说明](SECURITY.md)。

## 参与贡献

欢迎用中文或英文提交问题和贡献。测试方式及代码结构见 [CONTRIBUTING.md](CONTRIBUTING.md)，改动记录见 [CHANGELOG.md](CHANGELOG.md)，安全问题的私密报告方式见 [SECURITY.md](SECURITY.md)。

## 许可证

Switch 使用 [MIT 许可证](LICENSE)。随项目附带的 Markdown 渲染器及其依赖保留各自的[上游许可声明](web/vendor/THIRD-PARTY-NOTICES)。
