import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tb.codex_read_state import ReadTracker, STATE_KEY


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace('+00:00', 'Z')


# Native writes below are stamped between replies; unstamped writes use the real
# clock, which is later than both.
R1, R2 = iso(1_699_999_900), iso(1_700_000_050)


class ReadStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / 'native.json'
        self.cache = Path(self.tmp.name) / 'seen.json'
        self.tracker = ReadTracker(self.source, self.cache)

    def native(self, ids, identity='account', host='local:host', seconds=None):
        self.source.write_text(json.dumps({STATE_KEY: {'version': 1, 'unreadByIdentity': {identity: {host: ids}}}}))
        if seconds is not None:
            self.stamp_source(seconds)

    def snapshot(self, reply=R1, tid='task', origin='desktop'):
        task = {'id': tid, 'agent': 'codex', 'origin': origin, 'last_reply': reply}
        self.tracker.apply([task])
        return task

    def stamp_source(self, seconds):
        os.utime(self.source, (seconds, seconds))

    def assert_unread(self, task, reply=R1, seconds=1_700_000_000):
        self.assertEqual(task.get('desktop_unread_reply'), reply)
        self.assertEqual(datetime.fromisoformat(task['desktop_unread_at'].replace('Z', '+00:00')),
                         datetime.fromtimestamp(seconds, timezone.utc))
        self.assertIsNone(task.get('desktop_seen_reply'))

    def assert_read(self, task, reply=R1):
        self.assertEqual(task.get('desktop_seen_reply'), reply)
        self.assertIsNone(task.get('desktop_unread_reply'))
        self.assertIsNone(task.get('desktop_unread_at'))

    def test_focused_completion_is_read_on_every_refresh_without_observing_an_unread(self):
        # A reply completed in the open conversation: Codex flipped it unread and read
        # between our refreshes, so the tracker only ever sees it absent.
        self.native([])
        self.assert_read(self.snapshot())
        self.assert_read(self.snapshot())
        self.tracker = ReadTracker(self.source, self.cache)
        self.assert_read(self.snapshot())
        self.assertEqual(json.loads(self.cache.read_text()), {'version': 2, 'seen': {'task': R1}, 'unread': {}})

    def test_background_completion_stays_unread_until_codex_clears_it(self):
        self.native([], seconds=1_700_000_000)
        self.assert_read(self.snapshot())
        self.native(['task'], seconds=1_700_000_100)
        unread = self.snapshot(R2)
        self.assert_unread(unread, R2, 1_700_000_100)
        self.native(['task', 'other'], seconds=1_700_000_200)
        self.assert_unread(self.snapshot(R2), R2, 1_700_000_100)
        self.native([], seconds=1_700_000_300)
        self.assert_read(self.snapshot(R2), R2)

    def test_snapshot_older_than_the_reply_cannot_acknowledge_it(self):
        self.native([], seconds=1_700_000_000)
        self.assert_read(self.snapshot(R1))
        # R2 finished after the last native write, so the absence says nothing about it.
        newer = self.snapshot(R2)
        self.assertEqual(newer['desktop_seen_reply'], R1)
        self.assertNotEqual(newer['desktop_seen_reply'], R2)
        self.native([], seconds=1_700_000_100)
        self.assert_read(self.snapshot(R2), R2)
        for reply in (None, '', 'not-a-time'):
            with self.subTest(reply=reply):
                self.assertIsNone(self.snapshot(reply, tid='odd').get('desktop_seen_reply'))

    def test_only_desktop_tasks_follow_native_state(self):
        for origin in ('cli', 'unknown', None):
            with self.subTest(origin=origin):
                self.native([])
                task = self.snapshot(origin=origin)
                self.assertIsNone(task.get('desktop_seen_reply'))
                self.native(['task'])
                task = self.snapshot(origin=origin)
                self.assertIsNone(task.get('desktop_unread_reply'))
                self.assertFalse(self.cache.exists())

    def test_unavailable_source_keeps_last_known_state_then_resynchronizes(self):
        def write(text):
            return lambda: self.source.write_text(text)

        def versioned(version, scopes):
            return write(json.dumps({STATE_KEY: {'version': version, 'unreadByIdentity': scopes}}))
        breakages = {
            'missing': self.source.unlink,
            'empty': write(''),
            'truncated': write('{'),
            'no state': write('{}'),
            'newer schema': versioned(2, {'account': {'local:host': []}}),
            'bad ids': versioned(1, {'account': {'local:host': [1]}}),
            'signed out': versioned(1, {}),
            'no hosts': versioned(1, {'account': {}}),
            'remote only': versioned(1, {'account': {'remote:host': []}}),
        }
        for name, break_source in breakages.items():
            with self.subTest(source=name):
                self.native(['task'], seconds=1_700_000_000)
                self.assert_unread(self.snapshot())
                break_source()
                self.assert_unread(self.snapshot())
                self.native([])
                self.assert_read(self.snapshot())

    def test_first_refresh_without_a_usable_source_is_not_read(self):
        for text in (None, '', '{', '{}'):
            with self.subTest(source=text):
                if text is not None:
                    self.source.write_text(text)
                task = self.snapshot()
                self.assertIsNone(task.get('desktop_seen_reply'))
                self.assertIsNone(task.get('desktop_unread_reply'))

    def test_restart_resynchronizes_a_stale_cached_unread_marker(self):
        self.native(['task'], seconds=1_700_000_000)
        self.assert_unread(self.snapshot())
        self.tracker = ReadTracker(self.source, self.cache)
        self.native([])
        self.assert_read(self.snapshot())
        self.assertEqual(json.loads(self.cache.read_text()), {'version': 2, 'seen': {'task': R1}, 'unread': {}})

    def test_manual_unread_clears_seen_and_native_reread_clears_unread(self):
        self.native(['task'])
        self.snapshot()
        self.native([])
        self.assert_read(self.snapshot())

        self.native(['task'], seconds=1_700_000_000)
        unread = self.snapshot()
        self.assert_unread(unread)
        self.assertEqual(json.loads(self.cache.read_text()), {
            'version': 2, 'seen': {},
            'unread': {'task': {'reply': R1, 'at': unread['desktop_unread_at']}}})

        self.native([])
        self.assert_read(self.snapshot())
        self.assertEqual(json.loads(self.cache.read_text())['unread'], {})

    def test_initial_unread_uses_source_time_and_survives_unrelated_writes_and_restart(self):
        self.native(['task'], seconds=1_700_000_000)
        self.assert_unread(self.snapshot())

        # Another thread changes the whole file, but this thread did not re-enter unread.
        self.native(['task', 'other'], seconds=1_700_000_100)
        self.assert_unread(self.snapshot())
        self.tracker = ReadTracker(self.source, self.cache)
        self.assert_unread(self.snapshot())

    def test_legacy_seen_cache_migrates_without_losing_other_threads(self):
        self.cache.write_text(json.dumps({'task': R1, 'other': 'reply-other'}))
        self.tracker = ReadTracker(self.source, self.cache)
        self.native([])
        self.assert_read(self.snapshot())
        self.native(['task'], seconds=1_700_000_000)
        unread = self.snapshot()
        self.assert_unread(unread)
        self.assertEqual(json.loads(self.cache.read_text()), {
            'version': 2, 'seen': {'other': 'reply-other'},
            'unread': {'task': {'reply': R1, 'at': unread['desktop_unread_at']}}})

    def test_new_reply_does_not_rebind_native_unread_marker(self):
        self.native(['task'], seconds=1_700_000_000)
        self.assert_unread(self.snapshot())
        self.native(['task'], seconds=1_700_000_100)
        self.assert_unread(self.snapshot(R2))
        self.tracker = ReadTracker(self.source, self.cache)
        self.assert_unread(self.snapshot(R2))
        self.native([])
        self.assert_read(self.snapshot(R2), R2)

    def test_removal_written_before_a_new_reply_clears_unread_without_marking_the_reply_read(self):
        self.native(['task'], seconds=1_700_000_000)
        self.assert_unread(self.snapshot())
        self.native([], seconds=1_700_000_010)
        read = self.snapshot(R2)
        self.assertIsNone(read['desktop_seen_reply'])
        self.assertIsNone(read.get('desktop_unread_reply'))
        self.assertIsNone(read.get('desktop_unread_at'))

    def test_reentering_unread_after_reread_records_new_source_time(self):
        self.native(['task'], seconds=1_700_000_000)
        self.assert_unread(self.snapshot())
        self.native([])
        self.assert_read(self.snapshot())
        self.native(['task'], seconds=1_700_000_100)
        self.assert_unread(self.snapshot(), seconds=1_700_000_100)

    def test_adding_existing_unread_to_another_known_scope_preserves_its_marker(self):
        def scopes(first, other):
            self.source.write_text(json.dumps({STATE_KEY: {'version': 1, 'unreadByIdentity': {
                'account': {'local:host': first, 'local:other': other}}}}))

        scopes(['task'], [])
        self.stamp_source(1_700_000_000)
        self.assert_unread(self.snapshot())

        scopes(['task'], ['task'])
        self.stamp_source(1_700_000_100)
        self.assert_unread(self.snapshot())

        # Removing it from just one host leaves it unread, with its original action time.
        scopes([], ['task'])
        self.stamp_source(1_700_000_200)
        self.assert_unread(self.snapshot())


if __name__ == '__main__':
    unittest.main()
