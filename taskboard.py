#!/usr/bin/env python3
"""任务看板：在一页里查看本机 Codex 和 Claude 会话的当前状态。

只需要 Python 3.9+，不装任何第三方包。本工具写的文件都在仓库下的 data/；
仓库之外可选添加客户端 hooks 和 macOS 登录启动项；status 列出，uninstall 撤销。

    python3 taskboard.py            启动看板并打开浏览器（Ctrl-C 停止）
    python3 taskboard.py setup      接入 hooks，让状态更及时（可选）
    python3 taskboard.py status     列出本工具在仓库之外的全部写入
    python3 taskboard.py autostart on   启用 macOS 登录自启，并在后台启动
    python3 taskboard.py autostart off  停止服务并关闭登录自启
    python3 taskboard.py stop       停止正在运行的看板
    python3 taskboard.py uninstall  撤销 hooks；之后删除整个目录即可
"""
import argparse
import hashlib
import http.client
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import webbrowser

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tb import autostart, hooks, paths  # noqa: E402

PID = paths.DATA / 'server.pid'


def _pid_info():
    try:
        info = json.loads(PID.read_text(encoding='utf-8'))
        if (not isinstance(info, dict) or set(info) != {'pid', 'port', 'token'}
                or type(info['pid']) is not int or info['pid'] <= 0
                or type(info['port']) is not int or not 0 < info['port'] < 65536
                or not isinstance(info['token'], str) or not info['token']):
            return None
        return info
    except (OSError, ValueError):
        return None


def _request(info, method, path):
    """直接请求本机，忽略代理；只有关闭请求携带实例的 token。"""
    connection = http.client.HTTPConnection('127.0.0.1', info['port'], timeout=1)
    try:
        headers = {'X-Taskboard-Token': info['token']} if method == 'POST' else {}
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        data = response.read(65537)
        if response.status != 200 or len(data) > 65536:
            raise ValueError('看板服务返回了无效响应。')
        return json.loads(data)
    finally:
        connection.close()


def _running(info=None):
    """只认可 PID、端口和 token 指纹均匹配的本工具实例。"""
    info = info if info is not None else _pid_info()
    if not info:
        return None
    try:
        health = _request(info, 'GET', '/health')
        instance = hashlib.sha256(info['token'].encode('utf-8')).hexdigest()
        if (isinstance(health, dict) and health.get('app') == 'taskboard'
                and health.get('pid') == info['pid'] and health.get('instance') == instance):
            return info
    except (OSError, ValueError, http.client.HTTPException):
        pass
    return None


def _remove_pid(info):
    if _pid_info() == info:
        PID.unlink(missing_ok=True)


def cmd_run(args):
    info = _running()
    if info:
        url = f'http://127.0.0.1:{info["port"]}/'
        print(f'看板已在运行：{url}')
        if not args.no_open:
            webbrowser.open(url)
        return
    from tb import core
    owned = None

    def ready(httpd):
        nonlocal owned
        # 绑定失败不会到这里，也不会覆盖另一实例的记录。
        if _running():
            raise RuntimeError('另一看板实例已经启动，请使用 status 查看。')
        info = {'pid': os.getpid(), 'port': httpd.server_port, 'token': httpd.token}
        PID.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        hooks._write(PID, info)
        owned = info
        url = f'http://127.0.0.1:{httpd.server_port}/'
        print(f'看板：{url}（Ctrl-C 停止）', flush=True)
        if not args.no_open:
            webbrowser.open(url)

    # 系统在注销、关机或撤销登录启动时发 SIGTERM：按 Ctrl-C 的路径正常收尾，进程记录不会残留
    def terminate(_signum, _frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, terminate) if hasattr(signal, 'SIGTERM') else None
    try:
        core.serve(args.port, on_ready=ready)
    except KeyboardInterrupt:
        pass
    finally:
        if owned:
            _remove_pid(owned)
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)


def cmd_stop(_args):
    info = _running()
    if not info:
        print('看板没有在运行，或当前记录无法确认实例身份。')
        return
    _request(info, 'POST', '/shutdown')
    deadline = time.monotonic() + 5
    # 等到服务关闭、启动进程撤销自己的记录后，才允许卸载/删除数据。
    while time.monotonic() < deadline:
        if _pid_info() != info and not _running(info):
            print(f'已停止看板（进程 {info["pid"]}）。')
            return
        time.sleep(0.05)
    raise RuntimeError('看板尚未完成关闭；保留记录和数据，请稍后再试。')


def _confirm(prompt, yes):
    if yes:
        return True
    try:
        return input(prompt + ' [y/N] ').strip().lower() in ('y', 'yes')
    except EOFError:
        return False


def _autostart_port(port):
    if port is not None:
        return port
    startup = autostart.state()
    info = _running()
    return startup['port'] if startup and startup['installed'] else info['port'] if info else 8765


def _enable_autostart(port):
    port = _autostart_port(port)
    autostart.plan(port)  # Validate ownership before stopping an existing server.
    active = autostart.loaded()
    info = _running()
    if info and (not active or active['pid'] != info['pid']):
        cmd_stop(None)
    target = autostart.on(port)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        info = _running()
        active = autostart.loaded()
        if info and active and info['pid'] == active['pid'] and info['port'] == port:
            print(f'登录自启已启用：{target}')
            print(f'看板：http://127.0.0.1:{port}/（后台运行）')
            return
        time.sleep(0.2)
    raise RuntimeError(f'启动项已保存，但服务尚未就绪；请查看 {paths.DATA / "server.log"}，或用 autostart off 撤销。')


def _disable_autostart():
    startup = autostart.state()
    if not startup or not (startup['installed'] or startup['changed']):
        return False
    info = _pid_info()
    autostart.off()  # Refuses changed/foreign files before unloading or deleting.
    if info and info['pid'] == startup['pid']:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if _pid_info() != info and not _running(info):
                break
            time.sleep(0.05)
        else:
            raise RuntimeError('启动项已移除，但服务尚未完成退出；保留数据，请稍后再试。')
    print('登录自启已关闭。')
    return True


def cmd_autostart(args):
    if args.action == 'on':
        _enable_autostart(args.port)
    else:
        disabled = _disable_autostart()
        if _running():
            cmd_stop(args)
        if not disabled:
            print('登录自启未启用。')


def cmd_setup(args):
    todo = hooks.plan(args.only)
    startup = None
    if args.autostart:
        startup = autostart.plan(_autostart_port(args.port))
        print(f'将启用登录自启：{startup["path"]}，端口 {startup["port"]}')
    if not todo and not startup:
        print('无需改动：已接入，或本机没有 Codex / Claude 的配置目录。')
        print('可选登录自启：python3 taskboard.py autostart on')
        return
    for agent in todo:
        target = paths.CODEX_HOOKS if agent == 'codex' else paths.CLAUDE_SETTINGS
        print(f'将在 {target} 添加本工具的 hook：{", ".join(hooks.EVENTS[agent])}')
    if todo:
        print('这些 hook 只记录事件，不输出、不做任何决定；uninstall 会只撤销它们。')
    if not _confirm('继续？', args.yes):
        print('未做任何改动。')
        return
    for agent, path in hooks.install(args.only).items():
        print(f'已接入 {agent}：{path}')
    if 'codex' in todo:
        print('提示：Codex 需要在会话里运行 /hooks 信任新添加的 hook 后才会生效。')
    if startup:
        _enable_autostart(startup['port'])


def cmd_status(_args):
    print(f'本工具目录：{paths.REPO}')
    print(f'数据目录：  {paths.DATA}{"" if paths.DATA.exists() else "（尚未创建）"}')
    info = _running()
    print(f'看板进程：  {"运行中，端口 " + str(info["port"]) if info else "未运行"}')
    print('仓库之外的写入：')
    any_found = False
    for agent in hooks.EVENTS:
        entries = hooks.found(agent)
        if entries:
            any_found = True
            path = paths.CODEX_HOOKS if agent == 'codex' else paths.CLAUDE_SETTINGS
            print(f'  {path}：{len(entries)} 条 hook（{", ".join(ev for ev, _ in entries)}）')
    startup = autostart.state()
    if startup and (startup['installed'] or startup['changed']):
        any_found = True
        detail = ('内容已变更，自动卸载会保留' if startup['changed'] else
                  '文件缺失' if startup['missing'] else
                  '系统已加载' if startup['loaded'] else '下次登录时启动')
        print(f'  {startup["path"]}：登录自启，端口 {startup["port"]}（{detail}）')
    if not any_found:
        print('  无')


def cmd_uninstall(args):
    _disable_autostart()
    if _running():
        cmd_stop(args)
    elif args.purge and PID.exists():
        raise RuntimeError('无法确认进程记录对应的服务已退出，保留数据。确认退出后可移除 server.pid 再重试。')
    report = hooks.uninstall()
    for agent, what in report.items():
        print(f'{agent} 的 hooks：{what}')
    if not report:
        print('没有找到本工具的 hook。')
    if args.purge and paths.DATA.exists():
        shutil.rmtree(paths.DATA)
        print(f'已删除数据目录：{paths.DATA}')
    print(f'现在可以删除整个目录：{paths.REPO}')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd')
    run = sub.add_parser('run', help='启动看板（默认）')
    for p in (ap, run):
        p.add_argument('--port', type=int, default=8765)
        p.add_argument('--no-open', action='store_true', help='不自动打开浏览器')
    sub.add_parser('stop', help='停止正在运行的看板')
    startup = sub.add_parser('autostart', help='启用或关闭 macOS 登录自启')
    startup.add_argument('action', choices=('on', 'off'))
    startup.add_argument('--port', type=int, default=None, help='默认沿用已配置或正在运行的端口，否则为 8765')
    setup = sub.add_parser('setup', help='接入 hooks（可选）')
    setup.add_argument('--yes', action='store_true', help='不询问，直接接入（适合交给 Agent 执行）')
    setup.add_argument('--only', choices=sorted(hooks.EVENTS), action='append', help='只接入指定客户端')
    setup.add_argument('--autostart', action='store_true', help='同时启用 macOS 登录自启，默认不启用')
    setup.add_argument('--port', type=int, default=None, help='自启端口；默认沿用当前端口')
    sub.add_parser('status', help='列出本工具在仓库之外的全部写入')
    un = sub.add_parser('uninstall', help='撤销 hooks 和登录启动项')
    un.add_argument('--purge', action='store_true', help='同时删除 data/ 里的设置和缓存')
    args = ap.parse_args(argv)
    try:
        {'stop': cmd_stop, 'setup': cmd_setup, 'status': cmd_status, 'uninstall': cmd_uninstall,
         'autostart': cmd_autostart}.get(args.cmd, cmd_run)(args)
    except (OSError, ValueError, RuntimeError, http.client.HTTPException, subprocess.TimeoutExpired) as error:
        print(f'未完成：{error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
