"""Claude Code 会话：只读 ~/.claude/projects/*/*.jsonl，产出和 Codex 会话同样结构的任务。

只收你自己开的会话：终端里的（entrypoint == "cli"）和 Claude Desktop 里的（"claude-desktop"）；
`claude -p`、SDK、Parley 这类程序调用的会话会被排除，和 Codex 一侧排除 codex_exec 一样。
运行状态以对话记录为准；hook 事件（hook.py claude 记下的 UserPromptSubmit / Stop）只用来更早地确认一轮的开始和结束。
"""
import calendar
import datetime as dt
import glob
import json
import os
import re
import shlex
import subprocess
import time

from tb.excerpt import excerpt
from tb import paths, replies

PROJECTS = str(paths.CLAUDE_HOME / 'projects')
REGISTRY = str(paths.CLAUDE_HOME / 'sessions')   # 正在运行的 Claude Code 进程会在这里登记自己的状态（busy / idle）
DESKTOP = str(paths.CLAUDE_DESKTOP / 'claude-code-sessions')  # Claude Desktop 自己的 Code 会话列表
# 对话记录里的 entrypoint -> 会话从哪开的；不在这里的（sdk-cli、local-agent 等程序调用）不收
ORIGINS = {'cli': 'cli', 'claude-desktop': 'desktop', 'claude-desktop-3p': 'desktop'}
DESKTOP_ID = re.compile(r'^local_[A-Za-z0-9-]{1,64}$')  # Desktop 深链接只接受这种 id


def desktop_index():
    """{cliSessionId: Desktop 索引条目}。条目里有 Desktop 显示的标题、是否归档、你最后一次点开它的时刻。"""
    out = {}
    for p in glob.glob(os.path.join(DESKTOP, '*', '*', 'local_*.json')):
        try:
            with open(p) as fh:
                d = json.load(fh)
        except Exception:
            continue
        if d.get('cliSessionId'):
            out[d['cliSessionId']] = d
    return out


def _proc_starts(pids):
    """{pid: 启动时刻（epoch 秒）}，只含仍在运行的进程。"""
    if not pids:
        return {}
    try:
        out = subprocess.run(['ps', '-o', 'pid=,lstart=', '-p', ','.join(map(str, pids))],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return {}
    starts = {}
    for line in out.splitlines():
        parts = line.split(None, 1)
        try:
            starts[int(parts[0])] = time.mktime(time.strptime(parts[1].strip(), '%a %b %d %H:%M:%S %Y'))
        except (IndexError, ValueError):
            continue
    return starts


def _epoch(v):
    if isinstance(v, (int, float)):
        return v / 1000 if v > 1e11 else v
    try:
        return _ts(v).timestamp()
    except Exception:
        return None


def live_status():
    """{session_id: {'status': 'busy' | 'idle' | 'waiting', 'since': ISO 时间}}，只取进程仍在运行的登记。

    登记里的 procStart 是 UTC，和 ps 看到的启动时刻对上，才认为不是被别的进程复用了的 PID。
    """
    regs = []
    for p in glob.glob(os.path.join(REGISTRY, '*.json')):
        try:
            with open(p) as fh:
                d = json.load(fh)
            regs.append((int(d['pid']), d))
        except Exception:
            continue
    starts, out = _proc_starts([pid for pid, _ in regs]), {}
    for pid, d in regs:
        try:
            want = calendar.timegm(time.strptime(d['procStart'], '%a %b %d %H:%M:%S %Y'))
        except (KeyError, ValueError):
            continue
        if pid not in starts or abs(starts[pid] - want) > 2 or not d.get('sessionId') or not d.get('status'):
            continue
        since = _epoch(d.get('statusUpdatedAt') or d.get('updatedAt'))
        out[d['sessionId']] = {'status': d['status'],
                               'since': dt.datetime.fromtimestamp(since, dt.timezone.utc).isoformat() if since else None}
    return out
SKIP_PREFIX = ('<command-', '<local-command', 'Caveat:', '<system-reminder', '<bash-', '<user-memory-input')
REMINDER = re.compile(r'<system-reminder>.*?</system-reminder>', re.S)
SCRATCH = str(paths.CLAUDE_DESKTOP / 'scratch-workspaces') + os.sep  # Desktop 无文件夹会话的工作目录
STALE_MINUTES = 15


def _ts(s):
    return dt.datetime.fromisoformat(s.replace('Z', '+00:00'))


def _later(a, b):
    return a is not None and (b is None or _ts(a) > _ts(b))


def scan(path):
    f = {'entry': None, 'sid': None, 'cwd': '', 'custom': None, 'ai': None, 'first_user': '', 'first_user_ts': None,
         'last_user': None, 'last_final': None, 'final_head': '', 'interrupt': None, 'asks': {}, 'answered': set(),
         'last_kind': None, 'reply': None}
    with open(path, 'rb') as fh:
        offset = 0
        for raw in fh:
            position, offset = offset, offset + len(raw)
            try:
                o = json.loads(raw)
            except ValueError:
                continue
            t = o.get('type')
            if t == 'custom-title':
                f['custom'] = o.get('customTitle') or f['custom']
                continue
            if t == 'ai-title':
                f['ai'] = o.get('aiTitle') or f['ai']
                continue
            if t not in ('user', 'assistant') or o.get('isSidechain'):
                continue
            f['entry'] = f['entry'] or o.get('entrypoint')
            f['sid'] = f['sid'] or o.get('sessionId')
            f['cwd'] = o.get('cwd') or f['cwd']
            ts, msg = o.get('timestamp'), o.get('message') or {}
            content = msg.get('content')
            if t == 'user':
                if o.get('isMeta'):
                    continue
                texts, has_result = [], False
                if isinstance(content, str):
                    texts = [content]
                else:
                    for c in content or []:
                        if not isinstance(c, dict):
                            continue
                        if c.get('type') == 'tool_result':
                            has_result = True
                            f['answered'].add(c.get('tool_use_id'))
                        elif c.get('type') == 'text':
                            texts.append(c.get('text', ''))
                # 逐块去掉注入的内容：Desktop 无文件夹会话会在你的第一条消息前面插一个 <system-reminder> 块
                texts = [REMINDER.sub('', x).strip() for x in texts]
                text = '\n'.join(x for x in texts if x and not x.startswith(SKIP_PREFIX)).strip()
                if '[Request interrupted by user' in text:
                    f['interrupt'], f['last_kind'] = ts, 'interrupt'
                elif has_result and not text:
                    f['last_kind'] = 'tool_result'
                elif text:
                    f['last_user'], f['last_kind'] = ts, 'prompt'
                    if not f['first_user']:
                        f['first_user'], f['first_user_ts'] = text, ts
            else:
                stop = msg.get('stop_reason')
                for c in content or []:
                    if isinstance(c, dict) and c.get('type') == 'tool_use' and c.get('name') == 'AskUserQuestion':
                        qs = (c.get('input') or {}).get('questions') or []
                        title = ' / '.join(q.get('question', '') for q in qs if isinstance(q, dict)) or '（提问）'
                        f['asks'][c.get('id')] = {'ts': ts, 'title': title}
                descriptor = replies.describe(o, raw, position, 'claude')
                if descriptor:
                    f['last_final'], f['final_head'], f['last_kind'] = ts, excerpt(replies.final_text(o, 'claude').strip()), 'final'
                    f['reply'] = descriptor
                elif stop not in ('end_turn', 'stop_sequence'):
                    f['last_kind'] = 'working'
    return f


def derive(path, f, now, hook=None, live=None, desk=None):
    """hook: 这个会话最近一次 UserPromptSubmit / Stop 事件（hook.py claude 记录）；desk: Desktop 索引条目。都可为 None。"""
    mtime = dt.datetime.fromtimestamp(os.path.getmtime(path), dt.timezone.utc)
    # 记录里最后一件事是提问、工具结果或中间回复，说明这一轮还没结束
    running = f['last_kind'] in ('prompt', 'tool_result', 'working')
    if hook:
        if hook['event'] == 'Stop' and _later(hook['ts'], f['last_user']):
            running = False
        # 中断不会触发 Stop，所以“提交之后还没结束”要和回复、中断两种结束方式都比
        elif hook['event'] == 'UserPromptSubmit' and _later(hook['ts'], f['last_final']) and _later(hook['ts'], f['interrupt']):
            running = True
    # 进程自己报告的状态以它为准：busy 在处理，waiting 停在一个等你确认的对话框上，idle 这一轮结束了
    status = (live or {}).get('status')
    if status in ('busy', 'waiting'):
        running = True
    elif status == 'idle':
        running = False
    states = []
    if running:
        states.append('running')
        if status != 'waiting' and (now - mtime).total_seconds() / 60 > STALE_MINUTES:
            states.append('stale')
    pending = [q for k, q in f['asks'].items() if k not in f['answered'] and not _later(f['last_user'], q['ts'])]
    if pending:
        states.append('question')
    elif status == 'waiting':
        states.append('approval')               # 不是提问框，那就是授权、计划确认这类确认框
    if not running and _later(f['last_final'], f['last_user']):
        states.append('reply')
    if not running and f['last_kind'] == 'interrupt':
        states.append('aborted')
    since = [q['ts'] for q in pending] + ([f['last_final']] if 'reply' in states else []) + ([f['interrupt']] if 'aborted' in states else [])
    waiting_at = (live or {}).get('since')
    if 'approval' in states and waiting_at:
        since.append(waiting_at)
    cwd = f['cwd']
    sid = f['sid'] or os.path.splitext(os.path.basename(path))[0]
    desk = desk or {}
    local_id = desk.get('sessionId') if DESKTOP_ID.match(str(desk.get('sessionId'))) else None
    # Desktop 只在会话从隐藏变为显示时写 lastFocusedAt；取消归档也会写，但那时不一定显示，
    # 它会把 resumedAt 设成同一个值，据此排除
    focused = None if desk.get('lastFocusedAt') == desk.get('resumedAt') else _epoch(desk.get('lastFocusedAt'))
    return {
        'id': sid,
        'agent': 'claude',
        'origin': ORIGINS.get(f['entry']),
        'title': f['custom'] or desk.get('title') or f['ai'] or (f['first_user'][:60] if f['first_user'] else '(无标题)'),
        'project': '' if cwd.startswith(SCRATCH) else (os.path.basename(cwd.rstrip('/')) or cwd),
        'no_folder': cwd.startswith(SCRATCH),   # Desktop 无文件夹会话；显示文字由页面按语言给出
        'states': states,
        'questions': [q['title'] for q in pending],
        'questions_maybe': [],
        'approval_request': None,
        'maybe_ts': None,
        'reply_head': f['final_head'] if 'reply' in states else '',
        'last_reply': f['last_final'],
        'reply_id': (f.get('reply') or {}).get('id'),
        'reply_from_previous_turn': bool(f['last_final'] and (
            _later(f['last_user'], f['last_final']) or (hook and hook.get('event') == 'UserPromptSubmit'
                                                      and _later(hook['ts'], f['last_final'])))),
        'waiting_since': min(since) if since else None,
        'started': f['first_user_ts'],
        'last_activity': mtime.isoformat(),
        'idle_min': round((now - mtime).total_seconds() / 60),
        'resume': f'cd {shlex.quote(cwd)} && claude --resume {sid}' if cwd else f'claude --resume {sid}',
        # 只有 Desktop 里建的会话能从 Desktop 打开；终端会话不在它的列表里
        'open_url': f'claude://code/continue?session={local_id}' if local_id else None,
        # 你最后一次在 Desktop 里点开这个会话的时刻，晚于最新回复就算看过
        'focused_at': dt.datetime.fromtimestamp(focused, dt.timezone.utc).isoformat() if focused else None,
    }


_CACHE = {}
def collect(days, now, hooks, reply_sources=None):
    """hooks: {session_id: 最近一次 UserPromptSubmit/Stop 事件}"""
    cutoff = now.timestamp() - days * 86400
    tasks, lives, desks = [], live_status(), desktop_index()
    for p in glob.glob(os.path.join(PROJECTS, '*', '*.jsonl')):
        st = os.stat(p)
        if st.st_mtime < cutoff:
            continue
        key = (st.st_mtime, st.st_size)
        hit = _CACHE.get(p)
        f = hit[1] if hit and hit[0] == key else scan(p)
        _CACHE[p] = (key, f)
        if f['entry'] not in ORIGINS or not f['first_user']:
            continue
        desk = desks.get(f['sid'])
        if desk and desk.get('isArchived'):
            continue                            # 在 Desktop 里归档了，和 Codex 归档的会话一样不收
        task = derive(p, f, now, hooks.get(f['sid']), lives.get(f['sid']), desk)
        tasks.append(task)
        if reply_sources is not None:
            reply_sources[task['id']] = (p, 'claude', f.get('reply'))
    return tasks
