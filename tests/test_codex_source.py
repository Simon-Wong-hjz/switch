"""Regression cases based on Codex 0.162.1 live session records."""
import datetime as dt
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from tb import core as taskboard


class CodexSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'rollout.jsonl'
        self.rows = [{'type': 'session_meta', 'payload': {'source': 'appServer', 'cwd': self.tmp.name}}]
        self.add('event_msg', {'type': 'task_started', 'turn_id': 'turn-1'})
        self.add('response_item', {'type': 'message', 'role': 'user', 'content': [{'text': 'Test task'}]})

    def add(self, kind, payload):
        self.rows.append({'timestamp': f'2026-10-13T09:00:{len(self.rows):02d}Z', 'type': kind, 'payload': payload})

    def question(self, call='call_color', ids=('color',)):
        self.add('response_item', {'type': 'function_call', 'name': 'request_user_input', 'call_id': call,
                  'arguments': json.dumps({'questions': [{'id': qid, 'header': 'Color', 'question': f'Which {qid}?'} for qid in ids]})})

    def output(self, call='call_color', answers=None):
        self.add('response_item', {'type': 'function_call_output', 'call_id': call,
                  'output': json.dumps({'answers': {'color': {'answers': ['Blue']}} if answers is None else answers})})

    def result(self):
        self.path.write_text('\n'.join(json.dumps(r, separators=(',', ':')) for r in self.rows) + '\n')
        facts = taskboard.scan(self.path)
        return taskboard.derive('test', self.path, facts, 'Test', dt.datetime.now(dt.timezone.utc))

    def test_question_text_and_corresponding_answer(self):
        self.question()
        self.assertEqual(self.result()['questions'], ['Which color?'])
        self.assertIn('question', self.result()['states'])
        self.output()
        self.assertNotIn('question', self.result()['states'])
        self.assertNotIn('question_maybe', self.result()['states'])

    def test_other_tool_output_does_not_answer(self):
        self.question()
        self.output('call_other')
        self.assertIn('question', self.result()['states'])

    def test_mentioning_call_id_is_not_an_answer(self):
        self.question()
        self.add('response_item', {'type': 'message', 'role': 'user', 'content': [{'text': 'Please investigate call_color'}]})
        self.assertIn('question_maybe', self.result()['states'])

    def test_old_uncertain_question_does_not_age_current_reminder(self):
        self.question('call_old')
        self.add('response_item', {'type': 'message', 'role': 'user', 'content': [{'text': 'Continue with the work'}]})
        self.question('call_new')
        current_question_time = self.rows[-1]['timestamp']
        result = self.result()
        self.assertEqual(result['waiting_since'], current_question_time)
        self.assertTrue(result['questions_maybe'], 'Keep uncertainty in the data, not an answered verdict')
        self.output('call_new')
        self.assertIsNone(self.result()['waiting_since'])
        self.add('response_item', {'type': 'message', 'role': 'assistant', 'phase': 'final_answer', 'content': [{'text': 'Done'}]})
        reply_time = self.rows[-1]['timestamp']
        self.add('event_msg', {'type': 'task_complete', 'turn_id': 'turn-1'})
        result = self.result()
        self.assertEqual(result['waiting_since'], reply_time)
        self.assertTrue(result['questions_maybe'])

    def test_empty_or_partial_answers_do_not_clear_question(self):
        self.question(ids=('color', 'size'))
        self.output(answers={})
        self.assertIn('question', self.result()['states'])
        self.output()
        self.assertIn('question', self.result()['states'])
        self.output(answers={'color': {'answers': ['Blue']}, 'size': {'answers': ['Small']}})
        self.assertNotIn('question', self.result()['states'])

    def test_tool_error_does_not_count_as_user_answer(self):
        self.question()
        self.add('response_item', {'type': 'function_call_output', 'call_id': 'call_color', 'output': 'tool unavailable'})
        self.assertIn('question', self.result()['states'])

    def desktop_reply(self, items):
        body = json.dumps([{'questionItemId': json.dumps(['request_user_input_async', call, idx]), 'question': 'q', 'answer': ans}
                           for call, idx, ans in items])
        self.add('response_item', {'type': 'message', 'role': 'user',
                  'content': [{'text': f'<send_user_message_question_reply>\n{body}\n</send_user_message_question_reply>'}]})

    def test_desktop_async_question_answered_by_tagged_reply(self):
        # Codex Desktop：工具立即返回 accepted，回答以带标签的用户消息回来（2026-10-12 真实会话的格式）
        self.add('response_item', {'type': 'function_call', 'name': 'request_user_input_async', 'call_id': 'call_a',
                  'arguments': json.dumps({'questions': [{'title': 'Pick one?'}, {'title': 'And this?'}]})})
        self.add('response_item', {'type': 'function_call_output', 'call_id': 'call_a', 'output': json.dumps({'accepted': True})})
        self.assertIn('question', self.result()['states'])
        self.desktop_reply([('call_other', 0, 'yes'), ('call_other', 1, 'yes')])   # 答的是别的提问
        self.assertIn('question_maybe', self.result()['states'])
        self.desktop_reply([('call_a', 0, 'yes'), ('call_a', 1, '')])               # 只答了一题
        self.assertIn('question_maybe', self.result()['states'])
        self.desktop_reply([('call_a', 0, 'yes'), ('call_a', 1, 'no')])
        states = self.result()['states']
        self.assertNotIn('question', states)
        self.assertNotIn('question_maybe', states)

    def test_running_completed_interrupted_and_resumed(self):
        self.assertIn('running', self.result()['states'])
        self.add('response_item', {'type': 'message', 'role': 'assistant', 'phase': 'final_answer', 'content': [{'text': 'Done'}]})
        self.add('event_msg', {'type': 'task_complete', 'turn_id': 'turn-1'})
        self.assertEqual(self.result()['states'], ['reply'])
        self.add('event_msg', {'type': 'task_started', 'turn_id': 'turn-2'})
        self.add('response_item', {'type': 'message', 'role': 'user', 'content': [{'text': 'Next task'}]})
        self.add('event_msg', {'type': 'turn_aborted', 'reason': 'interrupted'})
        self.assertEqual(self.result()['states'], ['aborted'])
        self.add('event_msg', {'type': 'task_started', 'turn_id': 'turn-3'})
        self.assertEqual(self.result()['states'], ['running'])

    def test_client_origin_controls_open_action(self):
        for source, originator, expected in (
                ('cli', 'codex-tui', 'cli'),
                ('vscode', 'Codex Desktop', 'desktop'),
                ('appServer', 'Codex Desktop', 'desktop'),
                ('vscode', 't3code_desktop', 'unknown'),
                ('vscode', 'codex_vscode', 'unknown'),
                ('appServer', None, 'unknown')):
            with self.subTest(source=source, originator=originator):
                self.rows[0]['payload'].update(source=source, originator=originator)
                result = self.result()
                self.assertEqual(result['origin'], expected)
                self.assertEqual(result['open_url'], 'codex://threads/test' if expected == 'desktop' else None)
                self.assertEqual(result['resume'], 'codex resume test')


class ArchiveDuringScanTests(unittest.TestCase):
    def test_file_moved_during_poll_does_not_stop_collection(self):
        filename = '/tmp/rollout-2026-10-13T09-00-00-00000000-0000-0000-0000-000000000001.jsonl'
        with patch.object(taskboard.glob, 'glob', return_value=[filename]), \
             patch.object(taskboard, 'archived_ids', return_value=set()), \
             patch.object(taskboard, 'load_titles', return_value={}), \
             patch.object(taskboard, 'load_events', return_value={}), \
             patch.object(taskboard, 'load_claude_hooks', return_value={}), \
             patch.object(taskboard.claude_source, 'collect', return_value=[]):
            with patch.object(taskboard.os.path, 'getmtime', side_effect=FileNotFoundError):
                self.assertEqual(taskboard.collect(30)['tasks'], [])
            with patch.object(taskboard.os.path, 'getmtime', return_value=taskboard.time.time()), \
                 patch.object(taskboard, 'scan_cached', side_effect=FileNotFoundError):
                self.assertEqual(taskboard.collect(30)['tasks'], [])


if __name__ == '__main__':
    unittest.main()
