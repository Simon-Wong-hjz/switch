import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tb.codex_read_state import ReadTracker, STATE_KEY


class ReadStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / 'native.json'
        self.cache = Path(self.tmp.name) / 'seen.json'
        self.tracker = ReadTracker(self.source, self.cache)

    def native(self, ids, identity='account', host='local:host'):
        self.source.write_text(json.dumps({STATE_KEY: {'version': 1, 'unreadByIdentity': {identity: {host: ids}}}}))

    def sample(self, reply='reply-1', tid='task'):
        task = {'id': tid, 'agent': 'codex', 'last_reply': reply}
        self.tracker.apply([task])
        return task.get('desktop_seen_reply')

    def snapshot(self, reply='reply-1'):
        task = {'id': 'task', 'agent': 'codex', 'last_reply': reply}
        self.tracker.apply([task])
        return task

    def stamp_source(self, seconds):
        os.utime(self.source, (seconds, seconds))

    def assert_unread(self, task, reply='reply-1', seconds=1_700_000_000):
        self.assertEqual(task.get('desktop_unread_reply'), reply)
        self.assertEqual(datetime.fromisoformat(task['desktop_unread_at'].replace('Z', '+00:00')),
                         datetime.fromtimestamp(seconds, timezone.utc))

    def test_absence_on_first_poll_is_not_read(self):
        self.native([])
        self.assertIsNone(self.sample())

    def test_observed_removal_marks_only_that_reply_and_persists(self):
        self.native(['task'])
        self.assertIsNone(self.sample())
        self.native([])
        self.assertEqual(self.sample(), 'reply-1')
        self.tracker = ReadTracker(self.source, self.cache)
        self.assertEqual(self.sample(), 'reply-1')
        self.assertNotEqual(self.sample('reply-2'), 'reply-2')

    def test_new_reply_racing_with_removal_is_not_marked(self):
        self.native(['task']);self.sample()
        self.native([])
        self.assertIsNone(self.sample('reply-2'))

    def test_missing_or_invalid_file_is_not_read(self):
        for invalid in ('', '{', '{}'):
            self.native(['task']);self.sample()
            self.source.write_text(invalid)
            self.assertIsNone(self.sample())
            self.native([])
            self.assertIsNone(self.sample())

    def test_logout_and_identity_or_host_change_is_not_read(self):
        for identity, host in (('different', 'local:host'), ('account', 'local:other')):
            self.native(['task']);self.sample()
            self.native([], identity, host)
            self.assertIsNone(self.sample())
        self.native(['task']);self.sample()
        self.source.write_text(json.dumps({STATE_KEY: {'version': 1, 'unreadByIdentity': {}}}))
        self.assertIsNone(self.sample())

    def test_remote_state_does_not_clear_local_thread(self):
        self.native(['task'], host='remote:host');self.sample()
        self.native([], host='remote:host')
        self.assertIsNone(self.sample())

    def test_manual_unread_clears_seen_and_native_reread_clears_unread(self):
        self.native(['task']);self.sample()
        self.native([])
        self.assertEqual(self.sample(), 'reply-1')

        self.native(['task'])
        self.stamp_source(1_700_000_000)
        unread = self.snapshot()
        self.assertIsNone(unread['desktop_seen_reply'])
        self.assert_unread(unread)
        self.assertEqual(json.loads(self.cache.read_text()), {
            'version': 2, 'seen': {},
            'unread': {'task': {'reply': 'reply-1', 'at': unread['desktop_unread_at']}}})

        self.native([])
        read = self.snapshot()
        self.assertEqual(read['desktop_seen_reply'], 'reply-1')
        self.assertIsNone(read.get('desktop_unread_reply'))
        self.assertIsNone(read.get('desktop_unread_at'))
        self.assertEqual(json.loads(self.cache.read_text())['unread'], {})

    def test_initial_unread_uses_source_time_and_survives_unrelated_writes_and_restart(self):
        self.native(['task'])
        self.stamp_source(1_700_000_000)
        first = self.snapshot()
        self.assert_unread(first)

        # Another thread changes the whole file, but this thread did not re-enter unread.
        self.native(['task', 'other'])
        self.stamp_source(1_700_000_100)
        self.assert_unread(self.snapshot())
        self.tracker = ReadTracker(self.source, self.cache)
        self.assert_unread(self.snapshot())

    def test_legacy_seen_cache_migrates_without_losing_other_threads(self):
        self.cache.write_text(json.dumps({'task': 'reply-1', 'other': 'reply-other'}))
        self.tracker = ReadTracker(self.source, self.cache)
        self.native([])
        self.assertEqual(self.sample(), 'reply-1')
        self.native(['task'])
        self.stamp_source(1_700_000_000)
        unread = self.snapshot()
        self.assert_unread(unread)
        self.assertEqual(json.loads(self.cache.read_text()), {
            'version': 2, 'seen': {'other': 'reply-other'},
            'unread': {'task': {'reply': 'reply-1', 'at': unread['desktop_unread_at']}}})

    def test_new_reply_does_not_rebind_native_unread_marker(self):
        self.native(['task'])
        self.stamp_source(1_700_000_000)
        self.assert_unread(self.snapshot())
        self.native(['task'])
        self.stamp_source(1_700_000_100)
        self.assert_unread(self.snapshot('reply-2'))
        self.tracker = ReadTracker(self.source, self.cache)
        self.assert_unread(self.snapshot('reply-2'))
        self.native([])
        read = self.snapshot('reply-2')
        self.assertEqual(read['desktop_seen_reply'], 'reply-2')
        self.assertIsNone(read.get('desktop_unread_reply'))

    def test_racing_new_reply_removal_clears_unread_without_marking_new_reply_read(self):
        self.native(['task'])
        self.stamp_source(1_700_000_000)
        self.assert_unread(self.snapshot())
        self.native([])
        read = self.snapshot('reply-2')
        self.assertIsNone(read['desktop_seen_reply'])
        self.assertIsNone(read.get('desktop_unread_reply'))
        self.assertIsNone(read.get('desktop_unread_at'))

    def test_invalid_or_missing_source_preserves_last_known_unread(self):
        for invalid in (None, '', '{', '{}'):
            with self.subTest(source=invalid):
                self.native(['task'])
                self.stamp_source(1_700_000_000)
                self.assert_unread(self.snapshot())
                if invalid is None:
                    self.source.unlink()
                else:
                    self.source.write_text(invalid)
                self.assert_unread(self.snapshot())
                self.native([])
                task = self.snapshot()
                self.assertIsNone(task['desktop_seen_reply'])
                self.assert_unread(task)

    def test_scope_disappearance_preserves_unread_and_does_not_synthesize_read(self):
        for identity, host in (('different', 'local:host'), ('account', 'local:other')):
            with self.subTest(identity=identity, host=host):
                self.native(['task'])
                self.stamp_source(1_700_000_000)
                self.assert_unread(self.snapshot())
                self.native([], identity=identity, host=host)
                task = self.snapshot()
                self.assertIsNone(task['desktop_seen_reply'])
                self.assert_unread(task)
                self.native([])
                task = self.snapshot()
                self.assertIsNone(task['desktop_seen_reply'])
                self.assert_unread(task)

    def test_logout_without_any_remaining_scope_preserves_unread(self):
        self.native(['task'])
        self.stamp_source(1_700_000_000)
        self.assert_unread(self.snapshot())
        self.source.write_text(json.dumps({STATE_KEY: {'version': 1, 'unreadByIdentity': {}}}))
        task = self.snapshot()
        self.assertIsNone(task['desktop_seen_reply'])
        self.assert_unread(task)

    def test_startup_absence_preserves_cached_unread_without_synthesizing_read(self):
        self.native(['task'])
        self.stamp_source(1_700_000_000)
        self.assert_unread(self.snapshot())
        self.tracker = ReadTracker(self.source, self.cache)
        self.native([])
        task = self.snapshot()
        self.assertIsNone(task['desktop_seen_reply'])
        self.assert_unread(task)

    def test_reentering_unread_after_reread_records_new_source_time(self):
        self.native(['task'])
        self.stamp_source(1_700_000_000)
        self.assert_unread(self.snapshot())
        self.native([]);self.sample()
        self.native(['task'])
        self.stamp_source(1_700_000_100)
        self.assert_unread(self.snapshot(), seconds=1_700_000_100)

    def test_adding_existing_unread_to_another_known_scope_preserves_its_marker(self):
        self.source.write_text(json.dumps({STATE_KEY: {'version': 1, 'unreadByIdentity': {
            'account': {'local:host': ['task'], 'local:other': []}}}}))
        self.stamp_source(1_700_000_000)
        self.assert_unread(self.snapshot())

        self.source.write_text(json.dumps({STATE_KEY: {'version': 1, 'unreadByIdentity': {
            'account': {'local:host': ['task'], 'local:other': ['task']}}}}))
        self.stamp_source(1_700_000_100)
        task = self.snapshot()
        self.assertIsNone(task['desktop_seen_reply'])
        self.assert_unread(task)

        # Removing it from just one host also leaves the original unread action intact.
        self.source.write_text(json.dumps({STATE_KEY: {'version': 1, 'unreadByIdentity': {
            'account': {'local:host': [], 'local:other': ['task']}}}}))
        self.stamp_source(1_700_000_200)
        task = self.snapshot()
        self.assertIsNone(task['desktop_seen_reply'])
        self.assert_unread(task)


if __name__ == '__main__':
    unittest.main()
