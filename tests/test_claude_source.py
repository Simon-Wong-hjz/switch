"""claude_source 的回归用例：用合成的对话记录重放各种状态。运行：python3 test_claude_source.py"""
import calendar
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, 'tb')]
import claude_source as cs  # noqa: E402

NOW = dt.datetime.now(dt.timezone.utc)


def ts(sec):
    return (NOW - dt.timedelta(seconds=600 - sec)).isoformat().replace('+00:00', 'Z')


def user(sec, content, **kw):
    return {'type': 'user', 'sessionId': 'S', 'entrypoint': 'cli', 'cwd': '/w/proj', 'timestamp': ts(sec),
            'message': {'role': 'user', 'content': content}, **kw}


def asst(sec, stop, content):
    return {'type': 'assistant', 'sessionId': 'S', 'entrypoint': 'cli', 'cwd': '/w/proj', 'timestamp': ts(sec),
            'message': {'role': 'assistant', 'stop_reason': stop, 'content': content}}


PROMPT = user(1, '做这件事')
TOOL = asst(2, 'tool_use', [{'type': 'tool_use', 'id': 't1', 'name': 'Bash', 'input': {}}])
RESULT = user(3, [{'type': 'tool_result', 'tool_use_id': 't1'}])
FINAL = asst(4, 'end_turn', [{'type': 'text', 'text': '**做完了**'}])
ASK = asst(5, 'tool_use', [{'type': 'tool_use', 'id': 'q1', 'name': 'AskUserQuestion',
                            'input': {'questions': [{'question': '选哪个'}]}}])
ANSWER = user(6, [{'type': 'tool_result', 'tool_use_id': 'q1'}])
PROMPT2 = user(7, '再写一段')
INTERRUPT = user(8, [{'type': 'text', 'text': '[Request interrupted by user]'}])


class Derive(unittest.TestCase):
    def states(self, rows, hook=None, live=None):
        with tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False) as fh:
            fh.write('\n'.join(json.dumps(r) for r in rows))
        try:
            return cs.derive(fh.name, cs.scan(fh.name), NOW, hook, live)['states']
        finally:
            os.unlink(fh.name)

    def test_transcript_only(self):
        self.assertEqual(self.states([PROMPT]), ['running'])
        self.assertEqual(self.states([PROMPT, TOOL]), ['running'])
        self.assertEqual(self.states([PROMPT, TOOL, RESULT]), ['running'])
        self.assertEqual(self.states([PROMPT, TOOL, RESULT, FINAL]), ['reply'])
        self.assertEqual(self.states([PROMPT, FINAL, ASK]), ['running', 'question'])
        self.assertEqual(self.states([PROMPT, FINAL, ASK, ANSWER]), ['running'])
        self.assertEqual(self.states([PROMPT, FINAL, PROMPT2, INTERRUPT]), ['aborted'])

    def test_injected_blocks_are_dropped_per_block(self):
        reminder = {'type': 'text', 'text': '<system-reminder>\n环境说明\n</system-reminder>'}
        # Desktop 无文件夹会话：第一条消息前插了一个 system-reminder 块，后面才是你写的
        first = user(1, [reminder, {'type': 'text', 'text': '真正的问题'}])
        self.assertEqual(self.states([first]), ['running'])
        self.assertEqual(self.states([first, FINAL]), ['reply'])
        with tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False) as fh:
            fh.write(json.dumps(first))
        try:
            self.assertEqual(cs.scan(fh.name)['first_user'], '真正的问题')
        finally:
            os.unlink(fh.name)
        # 只有注入内容的消息不算你说的话；工具结果附带提醒时仍算工具结果
        self.assertEqual(self.states([PROMPT, FINAL, user(5, [reminder])]), ['reply'])
        result_with_reminder = user(3, [{'type': 'tool_result', 'tool_use_id': 't1'}, reminder])
        self.assertEqual(self.states([PROMPT, TOOL, result_with_reminder]), ['running'])

    def test_hooks(self):
        stop = {'event': 'Stop', 'ts': ts(5)}
        self.assertEqual(self.states([PROMPT, TOOL], hook=stop), [])
        submit = {'event': 'UserPromptSubmit', 'ts': ts(6)}
        self.assertEqual(self.states([PROMPT, FINAL], hook=submit), ['running'])
        # 中断不触发 Stop：最后一条 hook 是被中断那一轮的提交，不能把它当成还在运行
        late_submit = {'event': 'UserPromptSubmit', 'ts': ts(7.5)}
        self.assertEqual(self.states([PROMPT, FINAL, PROMPT2, INTERRUPT], hook=late_submit), ['aborted'])

    def test_live_registry_wins(self):
        self.assertEqual(self.states([PROMPT, FINAL], live={'status': 'busy'}), ['running'])
        self.assertEqual(self.states([PROMPT, TOOL], live={'status': 'idle'}), [])
        self.assertEqual(self.states([PROMPT, TOOL], live={'status': 'waiting', 'since': ts(3)}), ['running', 'approval'])
        # 提问框也会让进程报告 waiting，这时只算待回答
        self.assertEqual(self.states([PROMPT, FINAL, ASK], live={'status': 'waiting'}), ['running', 'question'])


class Collect(unittest.TestCase):
    """终端和 Desktop 的会话分开标出；程序调用的、在 Desktop 里归档的不收。"""

    def test_origins(self):
        def session(sid, entry):
            return [dict(r, sessionId=sid, entrypoint=entry) for r in (PROMPT, FINAL)]
        with tempfile.TemporaryDirectory() as root:
            proj, desk, reg = (os.path.join(root, x) for x in ('projects/-w-proj', 'desktop/acct/org', 'registry'))
            for d in (proj, desk, reg):
                os.makedirs(d)
            for sid, entry in (('C', 'cli'), ('D', 'claude-desktop'), ('A', 'claude-desktop'), ('P', 'sdk-cli')):
                with open(os.path.join(proj, sid + '.jsonl'), 'w') as fh:
                    fh.write('\n'.join(json.dumps(r) for r in session(sid, entry)))
            for sid, archived in (('D', False), ('A', True)):
                with open(os.path.join(desk, f'local_{sid.lower()}1.json'), 'w') as fh:
                    json.dump({'sessionId': f'local_{sid.lower()}1', 'cliSessionId': sid, 'title': 'Desktop 里的标题',
                               'isArchived': archived, 'lastFocusedAt': 1791880000000}, fh)
            saved = cs.PROJECTS, cs.DESKTOP, cs.REGISTRY
            cs.PROJECTS, cs.DESKTOP, cs.REGISTRY = os.path.join(root, 'projects'), os.path.join(root, 'desktop'), reg
            cs._CACHE.clear()
            try:
                tasks = {t['id']: t for t in cs.collect(30, NOW, {})}
            finally:
                cs.PROJECTS, cs.DESKTOP, cs.REGISTRY = saved
                cs._CACHE.clear()
        self.assertEqual(set(tasks), {'C', 'D'})
        self.assertEqual((tasks['C']['origin'], tasks['C']['open_url']), ('cli', None))
        d = tasks['D']
        self.assertEqual((d['origin'], d['title'], d['open_url']), ('desktop', 'Desktop 里的标题', 'claude://code/continue?session=local_d1'))
        self.assertTrue(d['focused_at'].startswith('2026-10-13'))

    def test_unarchive_is_not_seen(self):
        # 取消归档时 Desktop 把 lastFocusedAt 和 resumedAt 设成同一个值，那一刻不一定显示过，不算看过
        with tempfile.NamedTemporaryFile('w', suffix='.jsonl', delete=False) as fh:
            fh.write('\n'.join(json.dumps(dict(r, entrypoint='claude-desktop')) for r in (PROMPT, FINAL)))
        try:
            f = cs.scan(fh.name)
            seen = cs.derive(fh.name, f, NOW, desk={'sessionId': 'local_x', 'lastFocusedAt': 1791880000000})
            unarchived = cs.derive(fh.name, f, NOW, desk={'sessionId': 'local_x', 'lastFocusedAt': 1791880000000,
                                                          'resumedAt': 1791880000000})
        finally:
            os.unlink(fh.name)
        self.assertIsNotNone(seen['focused_at'])
        self.assertIsNone(unarchived['focused_at'])


class Registry(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'POSIX process identity check requires ps; Windows Claude testing is separate')
    def test_reused_pid_is_ignored(self):
        lstart = subprocess.run(['ps', '-o', 'lstart=', '-p', str(os.getpid())], capture_output=True, text=True).stdout.strip()
        utc = time.strftime('%a %b %d %H:%M:%S %Y', time.gmtime(time.mktime(time.strptime(lstart, '%a %b %d %H:%M:%S %Y'))))
        with tempfile.TemporaryDirectory() as d:
            for name, start in (('real', utc), ('reused', 'Mon Jan 01 00:00:00 2024')):
                with open(os.path.join(d, name + '.json'), 'w') as fh:
                    json.dump({'pid': os.getpid(), 'sessionId': name, 'procStart': start, 'status': 'busy',
                               'statusUpdatedAt': calendar.timegm(time.gmtime()) * 1000}, fh)
            old, cs.REGISTRY = cs.REGISTRY, d
            try:
                self.assertEqual(set(cs.live_status()), {'real'})
            finally:
                cs.REGISTRY = old


if __name__ == '__main__':
    unittest.main(verbosity=1)
