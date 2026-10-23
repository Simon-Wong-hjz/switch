"""接入和撤销本工具记录的完整 hook 条目，保留用户修改及其它安装。"""
import copy
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

from tb import paths

MARK = 'TASKBOARD_HOOK=1'
EVENTS = {
    'codex': ['UserPromptSubmit', 'PermissionRequest', 'PostToolUse', 'Stop', 'Interrupt'],
    'claude': ['UserPromptSubmit', 'Stop', 'Notification'],
}
MATCHER = {'codex': {'PermissionRequest': '*', 'PostToolUse': '*'}, 'claude': {}}


def command(agent):
    hook = paths.REPO / 'tb' / 'hook.py'
    code = (f'import os,runpy,sys;sys.dont_write_bytecode=True;{MARK};p={str(hook)!r};sys.argv=[p,{agent!r}];'
            'os.path.isfile(p) and runpy.run_path(p,run_name="__main__")')
    if os.name == 'nt':  # cmd.exe 包装器；失败仍静默以 0 退出。
        return subprocess.list2cmdline([sys.executable, '-c', code]) + ' >NUL 2>&1 & exit /b 0'
    # 解释器也可能已被卸载；在 shell 层保证任何失败都静默以 0 退出。
    return shlex.quote(sys.executable) + ' -c ' + shlex.quote(code) + ' >/dev/null 2>&1 || :'


def _entry(agent, event):
    hook = {'type': 'command', 'command': command(agent), 'timeout': 5}
    if agent == 'codex':
        hook['async'] = True
    group = {'hooks': [hook]}
    matcher = MATCHER[agent].get(event, '' if agent == 'claude' else None)
    if matcher is not None:
        group = {'matcher': matcher, **group}
    return group


def _config(agent):
    return paths.CODEX_HOOKS if agent == 'codex' else paths.CLAUDE_SETTINGS


def _client_present(agent):
    return (paths.CODEX_HOME if agent == 'codex' else paths.CLAUDE_HOME).is_dir()


def _bytes(path):
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _json(raw):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'JSON 包含重复字段：{key}')
            result[key] = value
        return result

    return json.loads(raw.decode('utf-8'), object_pairs_hook=object_pairs)


def _load_config(path):
    raw = _bytes(path)
    if raw is None:
        return None, None
    data = _json(raw)
    if not isinstance(data, dict) or not isinstance(data.get('hooks', {}), dict):
        raise ValueError(f'配置格式无效，未改动：{path}')
    for groups in data.get('hooks', {}).values():
        if not isinstance(groups, list) or any(
            not isinstance(group, dict) or not isinstance(group.get('hooks'), list)
            or any(not isinstance(hook, dict) for hook in group['hooks']) for group in groups
        ):
            raise ValueError(f'hooks 格式无效，未改动：{path}')
    return raw, data


def _atomic_bytes(path, payload, mode=None):
    if mode is None and path.exists():
        mode = path.stat().st_mode & 0o777
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write(path, data):
    _atomic_bytes(path, (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


def _transaction(changes):
    """所有文件先验证，再写入；中途失败按原始字节恢复已完成的写入。"""
    for path, before, after in changes:
        if _bytes(path) != before:
            raise RuntimeError(f'配置在准备期间发生变化，未改动：{path}')
        if after is not None:
            json.dumps(after, ensure_ascii=False, allow_nan=False)
    modes = {path: path.stat().st_mode & 0o777 for path, before, _ in changes if before is not None}
    applied = []
    try:
        for path, before, after in changes:
            if after is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                _write(path, after)
            applied.append((path, before))
    except BaseException as original:
        failed = []
        for path, before in reversed(applied):
            try:
                if before is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic_bytes(path, before, mode=modes[path])
            except OSError:
                failed.append(str(path))
        if failed:
            raise RuntimeError('操作失败，以下配置未能恢复：' + ', '.join(failed)) from original
        raise


def _manifest():
    raw = _bytes(paths.DATA / 'install.json')
    if raw is None:
        return {}
    data = _json(raw)
    if not isinstance(data, dict) or set(data) - set(EVENTS):
        raise ValueError('安装记录格式无效；保留客户端配置。')
    for agent, record in data.items():
        if (not isinstance(record, dict) or set(record) != {'file', 'created', 'entries'}
                or not isinstance(record['file'], str) or not record['file']
                or type(record['created']) is not bool or not isinstance(record['entries'], list)):
            raise ValueError('安装记录缺少完整 hook 条目；保留客户端配置。')
        for entry in record['entries']:
            if (not isinstance(entry, dict) or set(entry) != {'event', 'group'}
                    or entry['event'] not in EVENTS[agent] or not isinstance(entry['group'], dict)
                    or not isinstance(entry['group'].get('hooks'), list)
                    or not entry['group']['hooks']
                    or any(not isinstance(hook, dict) or hook.get('type') != 'command'
                           or not isinstance(hook.get('command'), str) or MARK not in hook['command']
                           for hook in entry['group']['hooks'])):
                raise ValueError('安装记录包含无效 hook 条目；保留客户端配置。')
    return data


def _present(data, entries):
    available = copy.deepcopy(data.get('hooks', {}))
    for entry in entries:
        groups = available.get(entry['event'], [])
        if entry['group'] not in groups:
            return False
        groups.remove(entry['group'])
    return True


def _strip_owned(data, entries):
    removed = 0
    hooks = data.get('hooks', {})
    for entry in entries:
        groups = hooks.get(entry['event'], [])
        if entry['group'] in groups:
            groups.remove(entry['group'])
            removed += len(entry['group']['hooks'])
            if not groups:
                del hooks[entry['event']]
    if removed and not hooks:
        data.pop('hooks', None)
    return removed


def found(agent):
    """当前仍与本安装记录完全匹配的条目：[(事件, 命令)]。"""
    record = _manifest().get(agent)
    if not record:
        return []
    _, data = _load_config(Path(record['file']))
    if data is None:
        return []
    return [(entry['event'], hook['command']) for entry in record['entries']
            if entry['group'] in data.get('hooks', {}).get(entry['event'], [])
            for hook in entry['group']['hooks']]


def _prepare_install(agents=None):
    manifest = _manifest()
    changes, todo = [], {}
    for agent in agents or EVENTS:
        if not _client_present(agent):
            continue
        path = _config(agent)
        before, data = _load_config(path)
        previous = manifest.get(agent)
        if previous and Path(previous['file']) != path:
            raise ValueError(f'{agent} 的配置路径已变更，请先卸载原路径的 hooks。')
        entries = [{'event': event, 'group': _entry(agent, event)} for event in EVENTS[agent]]
        if previous and previous['entries'] == entries and data is not None and _present(data, entries):
            continue
        data = data if data is not None else ({'description': 'Added by agent-taskboard'} if agent == 'codex' else {})
        if previous:
            _strip_owned(data, previous['entries'])
        hooks = data.setdefault('hooks', {})
        for entry in entries:
            hooks.setdefault(entry['event'], []).append(entry['group'])
        manifest[agent] = {'file': str(path), 'created': previous['created'] if previous else before is None,
                           'entries': entries}
        changes.append((path, before, data))
        todo[agent] = list(EVENTS[agent])
    return changes, manifest, todo


def plan(agents=None):
    """仅预览需改动的客户端，不产生写入。"""
    return _prepare_install(agents)[2]


def install(agents=None):
    changes, manifest, todo = _prepare_install(agents)
    if changes:
        file = paths.DATA / 'install.json'
        changes.append((file, _bytes(file), manifest))
        _transaction(changes)
    return {agent: str(_config(agent)) for agent in todo}


def uninstall():
    """只撤销本安装记录且未被修改的条目；所有配置先读取验证。"""
    manifest = _manifest()
    changes, report = [], {}
    for agent, record in manifest.items():
        path = Path(record['file'])
        before, data = _load_config(path)
        if data is None:
            continue
        unmatched = sum(entry['group'] not in data.get('hooks', {}).get(entry['event'], [])
                        for entry in record['entries'])
        removed = _strip_owned(data, record['entries'])
        delete = record['created'] and (
            (agent == 'codex' and data == {'description': 'Added by agent-taskboard'})
            or (agent == 'claude' and data == {}))
        if removed:
            changes.append((path, before, None if delete else data))
        if removed or unmatched:
            details = f'移除 {removed} 条'
            if removed and delete:
                details += '，删除本工具新建的文件'
            if unmatched:
                details += f'；{unmatched} 条安装记录不匹配（可能已修改或移除），非匹配 hooks 原样保留'
            report[agent] = f'{path}（{details}）'
    file = paths.DATA / 'install.json'
    raw = _bytes(file)
    if raw is not None:
        changes.append((file, raw, None))
    if changes:
        _transaction(changes)
    return report
