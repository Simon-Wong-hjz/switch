# Security / 安全

## Report privately / 私密报告

Use [Report a vulnerability](https://github.com/simwong98/switch/security/advisories/new) for security issues. Include the affected revision, environment, impact and a minimal reproduction with synthetic data. Do not post credentials, conversation text or personal configuration in public issues. Reports in English or Chinese are welcome. This is a personal project; there is no guaranteed response time.

安全问题请使用 [Report a vulnerability](https://github.com/simwong98/switch/security/advisories/new) 私密报告。请提供受影响版本、环境、影响和使用合成数据的最小复现。不要在公开 Issue 中发布凭据、会话正文或个人配置。中英文均可；这是个人项目，无法承诺固定响应时间。

## Boundaries / 边界

- Switch reads local client records and can display sensitive conversation content. Its cache, settings, exports and screenshots should be treated as private.
- The server listens on `127.0.0.1`, checks the requested host, and protects write/control and full-reply endpoints with an instance token and origin checks. It is not designed for remote access or untrusted local users/processes. Do not expose it through a public tunnel or reverse proxy.
- The interface makes no model calls and sends no telemetry. Session text and bundled rendering assets stay local. Clicking an external link or opening a client invokes that destination.
- Reply Markdown cannot execute raw HTML; links allow HTTP(S) only, and images are shown as links without automatic external requests.
- Optional hooks and login startup are explicit integrations. Removal preserves unrelated or modified client settings. Switch never approves client actions.

Switch 可以读取并展示敏感会话，因此缓存、设置、导出文件和截图都应视为私人数据。服务仅监听本机，检查 Host，对写入、控制及全文接口检查实例 token 和来源；它不用于远程访问，也不能防御本机已有读取权限的不可信程序或用户。请勿通过公网隧道或反向代理暴露服务。

界面不调用模型、不发送遥测，渲染资源随仓库提供。回复中的原始 HTML 不会执行，只允许 HTTP(S) 链接，图片不会自动联网加载。主动点击外部链接或打开客户端会转到对应目标。Hooks 与登录自启是明确启用的可选项，不会批准客户端操作。

## Supported revisions / 修复范围

Security fixes target the current `main` branch. Keep your checkout current and restart the backend after Python changes. Reload open board/App windows after frontend changes. Bundled third-party code and licenses are listed in [web/vendor/README.md](web/vendor/README.md).

安全修复针对当前 `main` 分支。更新 Python 代码后重启后端，更新前端后重新载入已打开的看板或 App 窗口。第三方资源及许可证见 [web/vendor/README.md](web/vendor/README.md)。
