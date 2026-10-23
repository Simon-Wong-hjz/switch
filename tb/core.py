"""Read local sessions and serve the installed taskboard. All writes stay in paths.DATA."""
import datetime as dt
import glob
import json
import os
import re
import threading
import time

from tb import claude_source, paths, replies
from tb.codex_read_state import ReadTracker
from tb.excerpt import excerpt
from tb.board_server import create_server

CODEX = str(paths.CODEX_HOME)
UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
INJECTED = ('# AGENTS.md', '<environment_context', '<skill>', '<app-context', '<user_instructions',
            '<external_codex', '<turn_aborted', '<subagent', '<permissions', '<in-app-browser-context')
STALE_MINUTES = 15


def parse_ts(s):
    return dt.datetime.fromisoformat(s.replace('Z', '+00:00'))


def thread_id(path):
    m = re.search(r'rollout-\d{4}-\d\d-\d\dT\d\d-\d\d-\d\d-(' + UUID + ')', os.path.basename(path))
    return m.group(1) if m else None


def load_titles():
    titles = {}
    try:
        with open(os.path.join(CODEX, 'session_index.jsonl'), encoding='utf-8', errors='ignore') as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                    titles[o['id']] = o.get('thread_name') or titles.get(o['id'])
                except (ValueError, KeyError):
                    continue
    except OSError:
        pass
    return titles


def archived_ids():
    ids = set()
    for p in glob.glob(os.path.join(CODEX, 'archived_sessions', '*.jsonl')):
        t = thread_id(p)
        if t:
            ids.add(t)
    return ids


def user_text(payload):
    parts = [c.get('text', '') for c in payload.get('content') or [] if isinstance(c, dict)]
    return '\n'.join(parts).strip()


QUESTION_REPLY = re.compile(r'^<send_user_message_question_reply>\s*(.*?)\s*</send_user_message_question_reply>', re.S)


def mark_desktop_answers(facts, text):
    """Codex Desktop 的提问框是异步的（request_user_input_async）：工具立即返回 accepted，
    回答之后作为一条带标签的用户消息回来，每项用 questionItemId = [工具名, call_id, 题号] 指明答的是哪一题。"""
    m = QUESTION_REPLY.match(text)
    if not m:
        return
    try:
        items = json.loads(m.group(1))
    except ValueError:
        return
    got = {}
    for it in items if isinstance(items, list) else []:
        try:
            tool, call_id, idx = json.loads(it['questionItemId'])
        except (KeyError, TypeError, ValueError):
            continue
        if str(tool).startswith('request_user_input') and str(it.get('answer') or '').strip():
            got.setdefault(call_id, set()).add(idx)
    for q in facts['questions']:
        if q['call_id'] in got and got[q['call_id']] >= set(range(q.get('n', 0))):
            facts['replied'].add(q['call_id'])


def scan(path):
    """Read one rollout and return the facts the page needs, or None if it is not the user's own session."""
    facts = {'turn_start': None, 'turn_id': None, 'turn_end': None, 'aborted': None, 'questions': [], 'replied': set(),
             'last_user': None, 'last_final': None, 'final_head': '', 'first_user': '', 'first_user_ts': None,
             'reply': None, 'last_activity': None}
    latest_activity = None
    with open(path, 'rb') as fh:
        first = fh.readline()
        try:
            meta = json.loads(first)['payload']
        except (ValueError, KeyError):
            return None
        src = meta.get('source')
        if meta.get('originator') == 'codex_exec' or (isinstance(src, dict) and 'subagent' in src):
            return None
        facts['cwd'] = meta.get('cwd', '')
        # Desktop also uses source="vscode"; that value alone includes other clients.
        facts['origin'] = ('cli' if src == 'cli' else
                           'desktop' if meta.get('originator') == 'Codex Desktop' else 'unknown')
        offset = len(first)
        for raw in fh:
            position, offset = offset, offset + len(raw)
            try:
                o = json.loads(raw)
            except ValueError:
                continue  # A writer may not have finished the last JSONL record yet.
            # Windows can leave mtime unchanged while the rollout writer is open.
            # All timestamped records count as activity, including tool updates.
            try:
                activity = parse_ts(o.get('timestamp'))
                if activity.utcoffset() is not None and (latest_activity is None or activity > latest_activity):
                    latest_activity = activity
            except (AttributeError, TypeError, ValueError):
                pass
            p = o.get('payload') or {}
            if o.get('type') == 'event_msg':
                event_type = p.get('type')
                if event_type == 'task_started':
                    facts['turn_start'] = o['timestamp']
                    facts['turn_id'] = o['payload'].get('turn_id')
                elif event_type == 'task_complete':
                    facts['turn_end'] = o['timestamp']
                    facts['aborted'] = None
                elif event_type == 'turn_aborted':
                    facts['turn_end'] = o['timestamp']
                    facts['aborted'] = (o['payload'].get('reason') or 'interrupted', o['timestamp'])
                continue
            if o.get('type') != 'response_item':
                continue
            if p.get('type') == 'function_call' and 'request_user_input' in str(p.get('name', '')):
                if not str(p.get('name', '')).startswith('request_user_input'):
                    continue
                try:
                    qs = json.loads(p.get('arguments') or '{}').get('questions') or []
                    title = ' / '.join(q.get('question') or q.get('title') or q.get('header') or '' for q in qs)
                except ValueError:
                    qs, title = [], ''
                facts['questions'].append({'call_id': p.get('call_id'), 'title': title, 'ts': o['timestamp'],
                                           'ids': [q['id'] for q in qs if q.get('id')], 'n': len(qs)})
            elif p.get('type') == 'function_call_output':
                try:
                    result = json.loads(p.get('output') or '{}')
                except (ValueError, TypeError):
                    continue
                answers = result.get('answers', {}) if isinstance(result, dict) else {}
                question = next((q for q in facts['questions'] if q['call_id'] == p.get('call_id')), None)
                if question and question['ids'] and isinstance(answers, dict) and all(
                        isinstance(answers.get(qid), dict) and answers[qid].get('answers')
                        for qid in question['ids']):
                    facts['replied'].add(p['call_id'])
            elif p.get('role') == 'user':
                text = user_text(o['payload'])
                if not text or text.startswith(INJECTED):
                    continue
                mark_desktop_answers(facts, text)
                facts['last_user'] = o['timestamp']
                if not facts['first_user']:
                    facts['first_user'] = text
                    facts['first_user_ts'] = o['timestamp']
            elif p.get('role') == 'assistant':
                descriptor = replies.describe(o, raw, position, 'codex')
                if descriptor:
                    facts['last_final'] = descriptor['timestamp']
                    facts['final_head'] = excerpt(replies.final_text(o, 'codex').strip())
                    facts['reply'] = descriptor
    if latest_activity is not None:
        facts['last_activity'] = latest_activity.isoformat()
    return facts


EVENTS = str(paths.DATA / 'events.jsonl')


def load_events():
    """每个会话、轮次保留最近的授权请求记录，不据此推断请求仍待处理。"""
    last = {}
    try:
        with open(EVENTS, encoding='utf-8', errors='ignore') as fh:
            for line in fh.readlines()[-20000:]:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get('session_id') and e.get('turn_id') and e.get('event') == 'PermissionRequest':
                    key = (e['session_id'], e['turn_id'])
                    if key not in last or e.get('ts', '') > last[key].get('ts', ''):
                        last[key] = e
    except OSError:
        pass
    return last


def derive(tid, path, facts, title, now, event=None):
    mtime = dt.datetime.fromtimestamp(os.path.getmtime(path), dt.timezone.utc)
    activity = max(mtime, parse_ts(facts['last_activity'])) if facts.get('last_activity') else mtime
    later = lambda a, b: a is not None and (b is None or parse_ts(a) > parse_ts(b))
    running = later(facts['turn_start'], facts['turn_end'])
    states, pending = [], []
    if running:
        states.append('running')
        idle = (now - activity).total_seconds() / 60
        if idle > STALE_MINUTES:
            states.append('stale')
    maybe = []
    for q in facts['questions']:
        if q['call_id'] in facts['replied']:
            continue                      # 找到了对应这条提问的回答
        if later(facts['last_user'], q['ts']):
            maybe.append(q)               # 之后你发过消息，但没有对应的回答，无法确认
        else:
            pending.append(q)
    approval = None
    if (event and event.get('event') == 'PermissionRequest' and running
            and event.get('turn_id') == facts.get('turn_id') and facts.get('turn_id')):
        approval = event
    if pending:
        states.append('question')
    if maybe:
        states.append('question_maybe')
    if not running and later(facts['last_final'], facts['last_user']):
        states.append('reply')
    if not running and facts['aborted']:
        states.append('aborted')
    since_candidates = [q['ts'] for q in pending]
    if 'reply' in states:
        since_candidates.append(facts['last_final'])
    if 'aborted' in states:
        since_candidates.append(facts['aborted'][1])
    cwd = facts.get('cwd', '')
    project = os.path.basename(cwd.rstrip('/')) or cwd
    return {
        'id': tid,
        'agent': 'codex',
        'origin': facts.get('origin', 'unknown'),
        'open_url': f'codex://threads/{tid}' if facts.get('origin') == 'desktop' else None,
        'resume': f'codex resume {tid}',
        'title': title or (facts['first_user'][:60] if facts['first_user'] else '(无标题)'),
        'project': project,
        'states': states,
        'questions': [q['title'] for q in pending],
        'questions_maybe': [q['title'] for q in maybe],
        'approval_request': {'at': approval['ts'], 'detail': approval.get('detail') or approval.get('tool') or ''} if approval else None,
        'maybe_ts': max((q['ts'] for q in maybe), default=None),
        'reply_head': facts['final_head'] if 'reply' in states else '',
        'last_reply': facts['last_final'],
        'reply_id': (facts.get('reply') or {}).get('id'),
        'reply_from_previous_turn': bool(facts['last_final'] and (
            later(facts['turn_start'], facts['last_final']) or later(facts['last_user'], facts['last_final']))),
        'waiting_since': min(since_candidates) if since_candidates else None,
        'started': facts['first_user_ts'],
        'last_activity': activity.isoformat(),
        'idle_min': round((now - activity).total_seconds() / 60),
    }


_CACHE = {}
REPLIES = replies.ReplyStore()


def scan_cached(path):
    st = os.stat(path)
    key = (st.st_mtime, st.st_size)
    hit = _CACHE.get(path)
    if hit and hit[0] == key:
        return hit[1]
    facts = scan(path)
    _CACHE[path] = (key, facts)
    return facts


def load_claude_hooks():
    """Claude Code 的 hook 事件：每个会话最近一次 UserPromptSubmit / Stop。"""
    last = {}
    try:
        with open(EVENTS, encoding='utf-8', errors='ignore') as fh:
            for line in fh.readlines()[-20000:]:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get('agent') == 'claude' and e.get('event') in ('UserPromptSubmit', 'Stop') and e.get('session_id'):
                    if e['session_id'] not in last or e.get('ts', '') > last[e['session_id']].get('ts', ''):
                        last[e['session_id']] = e
    except OSError:
        pass
    return last


def collect(days):
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = time.time() - days * 86400
    latest = {}
    for p in glob.glob(os.path.join(CODEX, 'sessions', '*', '*', '*', '*.jsonl')):
        try:
            modified = os.path.getmtime(p)
        except FileNotFoundError:
            continue  # 归档会在轮询期间移动记录文件
        if modified < cutoff:
            continue
        tid = thread_id(p)
        if tid and (tid not in latest or modified > latest[tid][0]):
            latest[tid] = (modified, p)
    titles, archived, events = load_titles(), archived_ids(), load_events()
    tasks, sources = [], {}
    for tid, (_, p) in latest.items():
        if tid in archived:
            continue
        try:
            facts = scan_cached(p)
            if facts is None or not facts['first_user']:
                continue
            tasks.append(derive(tid, p, facts, titles.get(tid), now, events.get((tid, facts.get('turn_id')))))
            sources[tid] = (p, 'codex', facts.get('reply'))
        except FileNotFoundError:
            continue  # 扫描途中归档，只跳过这一条，其他会话照常更新
    tasks += claude_source.collect(days, now, load_claude_hooks(), reply_sources=sources)
    REPLIES.update(sources)
    tasks.sort(key=lambda t: t['last_activity'], reverse=True)
    return {'generated': now.isoformat(), 'days': days, 'tasks': tasks}



def render(data):
    template = (paths.REPO / 'web' / 'index.html').read_text(encoding='utf-8')
    payload = json.dumps(data, ensure_ascii=False).replace('</', '<\\/')
    return template.replace('<!--REFRESH-->', '').replace('/*DATA*/null', payload)


def _replace_file(path, content):
    temporary = path.with_suffix(path.suffix + '.tmp')
    try:
        temporary.write_text(content, encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def serve(port, token=None, on_ready=None):
    """Run in the foreground until Ctrl-C or an authenticated shutdown request.

    on_ready(httpd), if supplied, runs after binding and the initial scan. The CLI
    can record httpd.token / httpd.instance and then open the browser.
    """
    directory = paths.data_dir()
    tracker = ReadTracker(paths.CODEX_HOME / '.codex-global-state.json', directory / 'codex-seen-replies.json')
    scan_lock = threading.Lock()
    stopped = threading.Event()

    def refresh():
        with scan_lock:
            data = collect(30)
            tracker.apply(data['tasks'])
            _replace_file(directory / 'taskboard.html', render(data))
            _replace_file(directory / 'taskboard.json', json.dumps(data, ensure_ascii=False))
            return data

    httpd = create_server(port, directory, refresh, settings_path=directory / 'settings.json',
                          stop=stopped.set, token=token, read_reply=REPLIES.read, assets_dir=paths.REPO / 'web')
    worker = None
    try:
        refresh()
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        if on_ready:
            on_ready(httpd)
        while not stopped.wait(5):
            try:
                refresh()
            except Exception as error:
                # Preserve the last readable scan; manual refresh also reports failure.
                print(f'Scan failed: {error}', flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        stopped.set()
        if worker:
            httpd.shutdown()
            worker.join(timeout=5)
        httpd.server_close()
