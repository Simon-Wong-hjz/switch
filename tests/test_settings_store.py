"""Persistence, import and failure checks for the local settings store."""

import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tb.settings_store import InvalidSettings, MAX_SETTINGS_BYTES, SettingsConflict, SettingsStore, validate_settings


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state" / "settings.json"
        self.store = SettingsStore(self.path)

    def test_missing_read_does_not_create_files_or_share_defaults(self):
        state = self.store.read()
        self.assertEqual((state["version"], state["revision"]), (1, 0))
        self.assertEqual(state["settings"], {
            "prio": {}, "seen": {}, "seenAt": {}, "order": [], "members": {}, "removed": {},
            "initAt": None, "lang": None, "theme": None, "notify": False, "muteWhenFocused": False,
        })
        state["settings"]["members"]["new"] = True
        self.assertEqual(self.store.read()["settings"]["members"], {})
        self.assertFalse(self.path.parent.exists())

    def test_roundtrip_restart_preserves_state_and_migrates_legacy(self):
        settings = {
            "prio": {"任务": "high", "paused": "later", "other": "other"},
            "seen": {"任务": "2026-10-14T12:34:56.123Z"},
            "seenAt": {"任务": "2026-10-14T12:35:00Z"},
            "members": {"任务": True, "inactive": False},
            "removed": {"old": False}, "ended": {"legacy": True, "restored": False},
            "include": {"legacy": True}, "baseline": "2026-10-13T00:00:00Z",
            "order": ["任务", "paused", "任务"], "initAt": "2026-10-14T12:30:00+08:00",
            "lang": "zh", "theme": "dark",
        }
        result = self.store.write(settings, 0)
        self.assertEqual(result["revision"], 1)
        self.assertEqual(SettingsStore(self.path).read(), result)
        self.assertEqual(result["settings"]["seenAt"], settings["seenAt"])
        self.assertEqual(result["settings"]["order"], ["任务", "paused"])
        self.assertEqual(result["settings"]["removed"], {"old": False, "legacy": True, "restored": False})
        self.assertNotIn("baseline", result["settings"])
        self.assertNotIn("include", result["settings"])
        if os.name == "posix":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        settings["prio"]["任务"] = "later"
        self.assertEqual(self.store.read()["settings"]["prio"]["任务"], "high")
        self.assertEqual(self.store.write({"theme": "light"}, 1)["revision"], 2)

    def test_conflict_leaves_newer_settings_untouched(self):
        newer = self.store.write({"lang": "en"}, 0)
        contents = self.path.read_bytes()
        with self.assertRaises(SettingsConflict) as caught:
            self.store.write({"lang": "zh"}, 0)
        self.assertEqual(caught.exception.current_revision, 1)
        self.assertEqual(self.path.read_bytes(), contents)
        self.assertEqual(self.store.read(), newer)

    def test_focused_mute_is_opt_in_and_survives_restart_and_export(self):
        self.assertFalse(validate_settings({"notify": True})["muteWhenFocused"])
        saved = self.store.write({"notify": True, "muteWhenFocused": True}, 0)
        self.assertTrue(SettingsStore(self.path).read()["settings"]["muteWhenFocused"])
        self.assertTrue(validate_settings(saved)["muteWhenFocused"])
        saved = self.store.write({"notify": True, "muteWhenFocused": False}, 1)
        self.assertFalse(saved["settings"]["muteWhenFocused"])

    def test_concurrent_instances_cannot_both_replace_same_revision(self):
        barrier = threading.Barrier(2)
        results = []

        def write(theme):
            barrier.wait()
            try:
                results.append(SettingsStore(self.path).write({"theme": theme}, 0))
            except SettingsConflict:
                results.append("conflict")

        threads = [threading.Thread(target=write, args=(theme,)) for theme in ("dark", "light")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(results.count("conflict"), 1)
        self.assertEqual(self.store.read()["revision"], 1)

    def test_malformed_existing_data_cannot_be_reset_by_write(self):
        invalid = [
            b"{", b"\xff", b"{}", b"null",
            b'{"version":2,"revision":0,"settings":{}}',
            b'{"version":1,"revision":true,"settings":{}}',
            b'{"version":1,"revision":0,"settings":{"members":{"a":1}}}',
            b'{"version":1,"version":1,"revision":0,"settings":{}}',
            b" " * (MAX_SETTINGS_BYTES + 1),
        ]
        self.path.parent.mkdir()
        for data in invalid:
            with self.subTest(data=data[:80]):
                self.path.write_bytes(data)
                with self.assertRaises(InvalidSettings):
                    self.store.read()
                with self.assertRaises(InvalidSettings):
                    self.store.write({}, 0)
                self.assertEqual(self.path.read_bytes(), data)
                self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_import_accepts_plain_and_export_without_importing_revision(self):
        plain = {"members": {"a": True}, "order": ["a", "a"]}
        expected = validate_settings(plain)
        self.assertEqual(expected["seenAt"], {})
        for envelope in ({"version": 1, "settings": plain}, {"version": 1, "revision": 99, "settings": plain}):
            self.assertEqual(validate_settings(envelope), expected)
        self.assertEqual(self.store.write(expected, 0)["revision"], 1)

    def test_invalid_shapes_and_values_are_rejected_before_writing(self):
        invalid = [
            None, [], {"unknown": True}, {"prio": []}, {"prio": {"a": "urgent"}},
            {"members": {"a": 1}}, {"removed": {"a": None}}, {"ended": {"a": "yes"}},
            {"include": []}, {"seen": {"a": "2026-02-30T00:00:00Z"}},
            {"seen": {"a": "2026-10-14T00:00:00+00:99"}},
            {"seen": {"a": "2026-10-14"}}, {"seen": {"a": 123}},
            {"seenAt": []}, {"seenAt": {"a": "yesterday"}}, {"seenAt": {"a": 123}},
            {"initAt": False}, {"initAt": "yesterday"}, {"order": {}}, {"order": [1]},
            {"members": {1: True}}, {"lang": "de"}, {"lang": []}, {"theme": "auto"},
            {"settings": {}, "version": 2}, {"settings": {}, "version": True},
            {"settings": {}, "version": 1, "revision": -1},
            {"baseline": "x" * MAX_SETTINGS_BYTES},
            {"muteWhenFocused": "false"}, {"muteWhenFocused": 1}, {"muteWhenFocused": None},
        ]
        for obj in invalid:
            with self.subTest(obj=repr(obj)[:100]):
                with self.assertRaises(InvalidSettings):
                    validate_settings(obj)
                with self.assertRaises(InvalidSettings):
                    self.store.write(obj, 0)
                self.assertFalse(self.path.exists())
        for revision in (-1, True, "0", None):
            with self.subTest(revision=revision), self.assertRaises(InvalidSettings):
                self.store.write({}, revision)

    def test_atomic_failure_preserves_previous_file_and_cleans_temp(self):
        self.store.write({"lang": "zh"}, 0)
        original = self.path.read_bytes()
        for operation in ("tb.settings_store.os.replace", "tb.settings_store.os.fsync"):
            with self.subTest(operation=operation):
                with patch(operation, side_effect=OSError("simulated disk failure")):
                    with self.assertRaises(OSError):
                        self.store.write({"lang": "en"}, 1)
                self.assertEqual(self.path.read_bytes(), original)
                self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        self.assertEqual(self.store.write({"lang": "en"}, 1)["revision"], 2)


if __name__ == "__main__":
    unittest.main()
