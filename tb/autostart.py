"""Opt-in macOS login service. Own one LaunchAgent; keep its receipt in DATA."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys

from tb import hooks, paths


def receipt_path():
    return paths.DATA / 'autostart.json'


def label():
    identity = str(paths.REPO.resolve()) + '\n' + str(paths.DATA.resolve())
    return 'local.switch.' + hashlib.sha256(identity.encode()).hexdigest()[:16]


def target():
    return paths.HOME / 'Library' / 'LaunchAgents' / (label() + '.plist')


def _require_mac():
    if sys.platform != 'darwin':
        raise RuntimeError('登录自启目前支持 macOS；Windows 接入留待 Windows 验证阶段。')


def _read():
    raw = hooks._bytes(receipt_path())
    if raw is None:
        return None
    data = json.loads(raw)
    if (not isinstance(data, dict) or set(data) != {'version', 'label', 'path', 'port', 'content'}
            or data['version'] != 1 or data['label'] != label() or data['path'] != str(target())
            or type(data['port']) is not int or not 0 < data['port'] < 65536
            or not isinstance(data['content'], str)):
        raise RuntimeError('自启记录无效，保留现有启动项。')
    return data


def describe():
    record = _read()
    current = hooks._bytes(target())
    return {'installed': record is not None, 'path': target(),
            'port': record['port'] if record else None,
            'changed': current is not None and (not record or current != record['content'].encode()),
            'missing': record is not None and current is None}


def state():
    if sys.platform != 'darwin':
        return None
    result = describe()
    active = loaded() if result['installed'] and not result['changed'] else None
    return {**result, 'loaded': active is not None, 'pid': active['pid'] if active else None}


def _owned():
    record = _read()
    current = hooks._bytes(target())
    if current is not None and (record is None or current != record['content'].encode()):
        raise RuntimeError(f'启动项不是本工具创建的原始内容，未改动：{target()}')
    return record


def _launchctl(*args):
    _require_mac()
    return subprocess.run(['/bin/launchctl', *args], capture_output=True, text=True, timeout=15)


def _service():
    return f'gui/{os.getuid()}/{label()}'


def loaded():
    if _owned() is None:
        return None
    result = _launchctl('print', _service())
    if result.returncode == 113:
        return None
    if result.returncode:
        raise RuntimeError('无法查询登录服务：' + result.stderr.strip())
    match = re.search(r'^\s*path = (.+)$', result.stdout, re.MULTILINE)
    if not match or Path(match[1]).resolve() != target().resolve():
        raise RuntimeError('同名登录服务来自其它文件，未改动。')
    pid = re.search(r'^\s*pid = (\d+)$', result.stdout, re.MULTILINE)
    return {'pid': int(pid[1]) if pid else None}


def plan(port):
    _require_mac()
    if type(port) is not int or not 0 < port < 65536:
        raise ValueError('自启端口必须在 1–65535 之间。')
    record = _owned()
    if record and record['port'] != port:
        raise RuntimeError('更换自启端口前请先运行 autostart off；已安装的 App 也需要使用相同端口。')
    # No working directory or launchd-managed log path: if the repository is
    # deleted, the shell can still exit normally rather than enter a restart loop.
    guard = ('if [ -x "$1" ] && [ -f "$2" ] && [ -d "$3" ]; then '
             'exec "$1" "$2" run --port "$4" --no-open >>"$3/server.log" 2>&1; fi')
    env = {'TASKBOARD_DATA': str(paths.DATA.resolve()), 'PYTHONDONTWRITEBYTECODE': '1',
           'CODEX_HOME': str(paths.CODEX_HOME.resolve()), 'CLAUDE_CONFIG_DIR': str(paths.CLAUDE_HOME.resolve())}
    if os.environ.get('TASKBOARD_HOME'):
        env['TASKBOARD_HOME'] = str(paths.HOME.resolve())
    value = {'Label': label(), 'ProgramArguments': ['/bin/sh', '-c', guard, 'switch-start',
             sys.executable, str(paths.REPO / 'taskboard.py'), str(paths.DATA.resolve()), str(port)],
             'EnvironmentVariables': env, 'RunAtLoad': True,
             'KeepAlive': {'SuccessfulExit': False}, 'ThrottleInterval': 10}
    content = plistlib.dumps(value).decode()
    if record and record['content'] != content:
        raise RuntimeError('解释器或路径已变化，请先运行 autostart off，再重新配置自启。')
    return {'version': 1, 'label': label(), 'path': str(target()), 'port': port, 'content': content}


def on(port):
    wanted = plan(port)
    old_receipt = hooks._bytes(receipt_path())
    old_file = hooks._bytes(target())
    already_loaded = loaded() if old_receipt else None
    paths.data_dir()
    target().parent.mkdir(parents=True, exist_ok=True)
    try:
        if old_file != wanted['content'].encode():
            hooks._atomic_bytes(target(), wanted['content'].encode(), mode=0o600)
        if old_receipt is None:
            hooks._write(receipt_path(), wanted)
        if already_loaded:
            if already_loaded['pid']:
                return target()
            result = _launchctl('kickstart', _service())
        else:
            result = _launchctl('bootstrap', f'gui/{os.getuid()}', str(target()))
        if result.returncode:
            raise RuntimeError('无法启用登录服务：' + result.stderr.strip())
    except BaseException:
        # Retain existing installations on errors. A new failed bootstrap has no
        # usable service, so remove only the exact files just written by this call.
        if old_receipt is None:
            try:
                active = loaded()
                if active:
                    stopped = _launchctl('bootout', _service())
                    if stopped.returncode or loaded() is not None:
                        raise RuntimeError('启用失败且无法确认服务已卸载，保留自启记录供重试清理。')
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                # Keep the receipt if job state is uncertain, so disable can retry.
                raise
            if hooks._bytes(target()) == wanted['content'].encode():
                target().unlink()
            receipt_path().unlink(missing_ok=True)
        raise
    return target()


def suspend():
    """Stop this login session's job; the file still enables the next login."""
    active = loaded()
    if not active:
        return None
    result = _launchctl('bootout', _service())
    if result.returncode and loaded() is not None:
        raise RuntimeError('无法停止登录服务：' + result.stderr.strip())
    return active['pid']


def off():
    record = _owned()
    if record is None:
        return False
    suspend()
    # Recheck after unloading, so a concurrent user edit is never removed.
    _owned()
    target().unlink(missing_ok=True)
    receipt_path().unlink()
    return True
