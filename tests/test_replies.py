"""Reply identity, exact on-demand reads, and collected-session boundaries."""
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tb import core, claude_source, replies
from tb.excerpt import excerpt

NOW = dt.datetime.now(dt.timezone.utc)
SID = '00000000-0000-0000-0000-000000000001'
TEXT = '  # 完整回答\n\n```python\nprint("<script> & 中文")\n```\n\n' + 'Keep this exact line.  \n' * 30 + '\n  '


def codex(kind, payload, second):
    return {'timestamp': f'2026-10-15T09:00:{second:02d}Z', 'type': kind, 'payload': payload}


def final(text=TEXT, second=3, **extra):
    return codex('response_item', {'type': 'message', 'role': 'assistant', 'channel': 'final',
                 'content': [{'type': 'output_text', 'text': text}], **extra}, second)


def codex_rows():
    return [codex('session_meta', {'source': 'appServer', 'cwd': '/project'}, 0),
            codex('event_msg', {'type': 'task_started', 'turn_id': 'turn-1'}, 1),
            codex('response_item', {'type': 'message', 'role': 'user', 'content': [{'text': 'Build it'}]}, 2),
            final(), codex('event_msg', {'type': 'task_complete'}, 4)]


def claude_record(role, content, second, stop=None, **extra):
    return {'timestamp': f'2026-10-15T09:00:{second:02d}Z', 'type': role, 'sessionId': 'claude-1',
            'entrypoint': 'cli', 'cwd': '/project', 'message': {'role': role, 'content': content,
            'stop_reason': stop}, **extra}


class ReplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'session.jsonl'
        self.store = replies.ReplyStore()

    def write(self, rows, append=False):
        with self.path.open('ab' if append else 'wb') as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False).encode() + b'\n')

    def publish(self, kind='codex'):
        facts = core.scan(self.path) if kind == 'codex' else claude_source.scan(self.path)
        sid = SID if kind == 'codex' else 'claude-1'
        self.store.update({sid: (self.path, kind, facts['reply'])})
        task = (core.derive(sid, self.path, facts, None, NOW) if kind == 'codex'
                else claude_source.derive(self.path, facts, NOW))
        return task

    def test_codex_final_channel_exact_text_and_excerpt_share_identity(self):
        self.write(codex_rows())
        task = self.publish()
        result = self.store.read(SID, task['reply_id'])
        self.assertEqual(result, {'session_id': SID, 'reply_id': task['reply_id'],
                                  'timestamp': task['last_reply'], 'text': TEXT})
        self.assertEqual(task['reply_head'], excerpt(TEXT.strip()))
        self.assertNotIn(TEXT, json.dumps(task, ensure_ascii=False))
        self.assertNotIn('text', task)
        self.assertNotIn('text', core.scan(self.path)['reply'])

    def test_codex_phase_legacy_and_multiple_visible_blocks(self):
        record = final('  first\n', channel=None, phase='final_answer')
        record['payload']['content'] += [{'type': 'output_text', 'text': '\nsecond  '},
                                        {'type': 'image', 'text': 'not visible text'}]
        self.write(codex_rows()[:3] + [record])
        task = self.publish()
        self.assertEqual(self.store.read(SID, task['reply_id'])['text'], '  first\n\n\nsecond  ')

    def test_new_round_progress_tools_and_partial_record_keep_last_reply(self):
        self.write(codex_rows())
        first = self.publish()
        self.write([codex('event_msg', {'type': 'task_started'}, 5),
                    codex('response_item', {'type': 'message', 'role': 'user', 'content': [{'text': 'Continue'}]}, 6),
                    final('progress mentioning final_answer', 7, channel='commentary'),
                    codex('response_item', {'type': 'function_call', 'name': 'run', 'arguments': '{}'}, 8),
                    codex('response_item', {'type': 'function_call_output', 'output': 'secret tool result'}, 9)], append=True)
        with self.path.open('ab') as fh:
            fh.write(b'{"unfinished":')
        running = self.publish()
        self.assertIn('running', running['states'])
        self.assertEqual(running['reply_id'], first['reply_id'])
        self.assertEqual(running['last_reply'], first['last_reply'])
        self.assertEqual(self.store.read(SID, running['reply_id'])['text'], TEXT)

    def test_codex_running_reply_label_tracks_current_final_before_task_complete(self):
        self.write(codex_rows())
        self.assertFalse(self.publish()['reply_from_previous_turn'])
        self.write([codex('event_msg', {'type': 'task_started'}, 5),
                    codex('response_item', {'type': 'message', 'role': 'user',
                          'content': [{'text': 'Next task'}]}, 6)], append=True)
        old = self.publish()
        self.assertIn('running', old['states'])
        self.assertTrue(old['reply_from_previous_turn'])
        self.write([final('Current completed final message', 7)], append=True)
        current = self.publish()
        self.assertIn('running', current['states'])
        self.assertFalse(current['reply_from_previous_turn'])

    def test_claude_running_reply_label_tracks_prompt_and_submit_hook_timestamps(self):
        self.write([claude_record('user', 'Build it', 1),
                    claude_record('assistant', [{'type': 'text', 'text': TEXT}], 2, 'end_turn')])
        facts = claude_source.scan(self.path)
        submitted = {'event': 'UserPromptSubmit', 'ts': '2026-10-15T09:00:03Z'}
        old = claude_source.derive(self.path, facts, NOW, hook=submitted)
        self.assertIn('running', old['states'])
        self.assertTrue(old['reply_from_previous_turn'])
        self.write([claude_record('user', 'Next task', 3)], append=True)
        old = claude_source.derive(self.path, claude_source.scan(self.path), NOW)
        self.assertIn('running', old['states'])
        self.assertTrue(old['reply_from_previous_turn'])
        self.write([claude_record('assistant', [{'type': 'text', 'text': 'Current final'}], 4, 'end_turn')], append=True)
        current = claude_source.derive(self.path, claude_source.scan(self.path), NOW,
                                       hook=submitted, live={'status': 'busy'})
        self.assertIn('running', current['states'])
        self.assertFalse(current['reply_from_previous_turn'])

    def test_claude_cli_and_desktop_final_blocks_exclude_thinking_tools_sidechain(self):
        for entry in ('cli', 'claude-desktop'):
            with self.subTest(entry=entry):
                rows = [claude_record('user', 'Build it', 1, entrypoint=entry),
                        claude_record('assistant', [{'type': 'text', 'text': 'Working'}], 2, 'tool_use'),
                        claude_record('assistant', [{'type': 'thinking', 'thinking': 'private'},
                            {'type': 'text', 'text': TEXT}, {'type': 'tool_use', 'name': 'Bash'}], 3, 'end_turn'),
                        claude_record('assistant', [{'type': 'text', 'text': 'sidechain'}], 4, 'end_turn', isSidechain=True)]
                self.write(rows)
                task = self.publish('claude')
                self.assertEqual(self.store.read('claude-1', task['reply_id'])['text'], TEXT)
                self.assertEqual(task['reply_head'], excerpt(TEXT.strip()))
                self.assertEqual(task['last_reply'], rows[2]['timestamp'])
                self.assertNotIn(TEXT, json.dumps(task, ensure_ascii=False))
                self.write([claude_record('user', 'Next task', 5),
                            claude_record('assistant', [{'type': 'text', 'text': 'Still working'}], 6)], append=True)
                running = self.publish('claude')
                self.assertEqual(running['reply_id'], task['reply_id'])
                self.assertIn('running', running['states'])

    def test_append_and_republish_preserve_requested_historical_snapshot(self):
        self.write(codex_rows())
        old = self.publish()
        self.write([final('New final answer', 6)], append=True)
        latest = self.publish()
        self.assertNotEqual(old['reply_id'], latest['reply_id'])
        self.assertEqual(self.store.read(SID, old['reply_id'])['text'], TEXT)
        self.assertEqual(self.store.read(SID, latest['reply_id'])['text'], 'New final answer')

    def test_append_during_read_never_substitutes_new_reply(self):
        self.write(codex_rows())
        old = self.publish()
        original_open = open
        source_path = self.path
        def open_and_append(path, *args, **kwargs):
            result = original_open(path, *args, **kwargs)
            if str(path) == str(source_path) and args == ('rb',):
                self.write([final('Racing final answer', 6)], append=True)
            return result
        with patch('builtins.open', side_effect=open_and_append):
            self.assertEqual(self.store.read(SID, old['reply_id'])['text'], TEXT)

    def test_replaced_source_returns_gone_instead_of_another_final(self):
        self.write(codex_rows())
        old = self.publish()
        self.write(codex_rows()[:3] + [final('Different reply now', 3)])
        with self.assertRaises(replies.ReplyError) as failure:
            self.store.read(SID, old['reply_id'])
        self.assertEqual(failure.exception.status, 410)
        self.assertNotIn(str(self.path), str(failure.exception))

    def test_unknown_sessions_ids_missing_source_and_exclusion(self):
        self.write(codex_rows())
        task = self.publish()
        for sid, rid in (('../../etc/passwd', task['reply_id']), (str(self.path), task['reply_id']),
                         (SID, '../../etc/passwd'), ('unknown', task['reply_id'])):
            with self.assertRaises(replies.ReplyError) as failure:
                self.store.read(sid, rid)
            self.assertEqual(failure.exception.status, 404)
        self.path.unlink()
        with self.assertRaises(replies.ReplyError) as failure:
            self.store.read(SID, task['reply_id'])
        self.assertEqual(failure.exception.status, 503)
        self.assertNotIn(str(self.path), str(failure.exception))
        self.store.update({})
        with self.assertRaises(replies.ReplyError) as failure:
            self.store.read(SID, task['reply_id'])
        self.assertEqual(failure.exception.status, 404)

    def test_no_completed_reply_has_no_fetchable_id(self):
        self.write(codex_rows()[:3])
        task = self.publish()
        self.assertIsNone(task['reply_id'])
        self.assertIsNone(task['last_reply'])
        self.assertFalse(task['reply_from_previous_turn'])
        with self.assertRaises(replies.ReplyError) as failure:
            self.store.read(SID, 'anything')
        self.assertEqual(failure.exception.status, 404)

    def test_claude_collection_registry_excludes_programmatic_archived_and_sidechain_sessions(self):
        projects = Path(self.tmp.name) / 'projects'
        folder = projects / 'project'
        folder.mkdir(parents=True)
        for sid, entry in [('cli', 'cli'), ('desktop', 'claude-desktop'),
                           ('programmatic', 'sdk-cli'), ('archived', 'claude-desktop'), ('child', 'cli')]:
            rows = [claude_record('user', 'Build it', 1, sessionId=sid, entrypoint=entry,
                                 isSidechain=sid == 'child'),
                    claude_record('assistant', [{'type': 'text', 'text': TEXT}], 2, 'end_turn',
                                  sessionId=sid, entrypoint=entry, isSidechain=sid == 'child')]
            self.path = folder / (sid + '.jsonl')
            self.write(rows)
        sources = {}
        with patch.object(claude_source, 'PROJECTS', str(projects)), \
             patch.object(claude_source, 'live_status', return_value={}), \
             patch.object(claude_source, 'desktop_index', return_value={'archived': {'isArchived': True}}):
            tasks = claude_source.collect(30, NOW, {}, reply_sources=sources)
        self.store.update(sources)
        self.assertEqual(set(sources), {'cli', 'desktop'})
        for task in tasks:
            self.assertEqual(self.store.read(task['id'], task['reply_id'])['text'], TEXT)
        for sid in ('programmatic', 'archived', 'child'):
            with self.assertRaises(replies.ReplyError) as failure:
                self.store.read(sid, tasks[0]['reply_id'])
            self.assertEqual(failure.exception.status, 404)

    def test_collection_publishes_only_user_owned_active_sessions(self):
        codex_home = Path(self.tmp.name) / 'codex'
        sessions = codex_home / 'sessions' / '2026' / '10' / '15'
        sessions.mkdir(parents=True)
        self.path = sessions / f'rollout-2026-10-15T09-00-00-{SID}.jsonl'
        self.write(codex_rows())
        store = replies.ReplyStore()
        with patch.object(core, 'CODEX', str(codex_home)), patch.object(core, 'REPLIES', store), \
             patch.object(core.claude_source, 'collect', return_value=[]):
            task = core.collect(30)['tasks'][0]
            self.assertEqual(store.read(SID, task['reply_id'])['text'], TEXT)
            archive = codex_home / 'archived_sessions'
            archive.mkdir()
            (archive / self.path.name).write_text('')
            self.assertEqual(core.collect(30)['tasks'], [])
            with self.assertRaises(replies.ReplyError):
                store.read(SID, task['reply_id'])
            (archive / self.path.name).unlink()
            rows = codex_rows()
            rows[0]['payload']['source'] = {'subagent': {'parent_thread_id': 'parent'}}
            self.write(rows)
            self.assertEqual(core.collect(30)['tasks'], [])


if __name__ == '__main__':
    unittest.main()
