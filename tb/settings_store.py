"""Validated, versioned local settings with atomic writes and revision checks."""

import json
import os
from pathlib import Path
import re
import tempfile
import threading
from datetime import datetime


MAX_SETTINGS_BYTES = 2 * 1024 * 1024
_FIELDS = {"prio", "seen", "seenAt", "order", "members", "removed", "initAt", "lang", "theme", "notify", "muteWhenFocused"}
_LEGACY = {"ended", "include", "baseline"}
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])?"
)


class InvalidSettings(ValueError):
    """The settings or persisted envelope cannot be safely used."""


class SettingsConflict(Exception):
    """The caller's settings revision is no longer current."""

    def __init__(self, expected_revision, current_revision):
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(f"Settings revision is {current_revision}, expected {expected_revision}")


def _serialize(obj):
    try:
        data = json.dumps(obj, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise InvalidSettings("Settings must be valid JSON") from exc
    if len(data) > MAX_SETTINGS_BYTES:
        raise InvalidSettings("Settings exceed the 2 MiB limit")
    return data


def _timestamp(value):
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _map(value, field, predicate):
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not predicate(item) for key, item in value.items()
    ):
        raise InvalidSettings(f"Invalid {field}")
    return dict(value)


def _revision(value):
    if type(value) is not int or value < 0:
        raise InvalidSettings("Revision must be a nonnegative integer")
    return value


def _settings(obj):
    if not isinstance(obj, dict) or set(obj) - _FIELDS - _LEGACY:
        raise InvalidSettings("Settings must be an object containing only supported fields")
    result = {
        "prio": _map(obj.get("prio", {}), "prio", lambda x: isinstance(x, str) and x in {"high", "later", "other"}),
        "seen": _map(obj.get("seen", {}), "seen", _timestamp),
        "seenAt": _map(obj.get("seenAt", {}), "seenAt", _timestamp),
        "members": _map(obj.get("members", {}), "members", lambda x: type(x) is bool),
        "removed": _map(obj.get("removed", {}), "removed", lambda x: type(x) is bool),
        "order": obj.get("order", []),
        "initAt": obj.get("initAt"),
        "lang": obj.get("lang"),
        "theme": obj.get("theme"),
        "notify": obj.get("notify", False),   # 只给“重要”栏的会话发浏览器提醒，默认关闭
        "muteWhenFocused": obj.get("muteWhenFocused", False),
    }
    order = result["order"]
    if not isinstance(order, list) or any(not isinstance(x, str) for x in order):
        raise InvalidSettings("Order must be an array of string IDs")
    result["order"] = list(dict.fromkeys(order))
    if result["initAt"] is not None and not _timestamp(result["initAt"]):
        raise InvalidSettings("initAt must be an ISO timestamp or null")
    for field in ("notify", "muteWhenFocused"):
        if type(result[field]) is not bool:
            raise InvalidSettings(f"{field} must be true or false")
    for field, choices in (("lang", (None, "zh", "en")), ("theme", (None, "dark", "light"))):
        if result[field] not in choices:
            raise InvalidSettings(f"Invalid {field}")
    if "ended" in obj:
        ended = _map(obj["ended"], "ended", lambda x: type(x) is bool)
        result["removed"].update(ended)
    if "include" in obj:
        _map(obj["include"], "include", lambda x: type(x) is bool)
    # baseline is obsolete; its historical contents never affect normalized state.
    return result


def validate_settings(obj):
    """Normalize a plain browser object or a version-1 export envelope.

    Missing fields receive defaults. Duplicate order IDs are removed in their
    original order. Legacy ended values migrate to removed; include and baseline
    are discarded. Export revisions are validated but are not imported.
    """
    _serialize(obj)
    if isinstance(obj, dict) and "settings" in obj:
        if set(obj) - {"version", "revision", "settings"} or type(obj.get("version")) is not int or obj["version"] != 1:
            raise InvalidSettings("Unsupported settings export version or fields")
        if "revision" in obj:
            _revision(obj["revision"])
        obj = obj["settings"]
    return _settings(obj)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidSettings(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


class SettingsStore:
    # Shared by instances so two handlers using the same file cannot race.
    _lock = threading.Lock()

    def __init__(self, path):
        self.path = Path(path)

    def _read(self):
        try:
            with self.path.open("rb") as source:
                data = source.read(MAX_SETTINGS_BYTES + 1)
        except FileNotFoundError:
            return {"version": 1, "revision": 0, "settings": _settings({})}
        if len(data) > MAX_SETTINGS_BYTES:
            raise InvalidSettings("Settings exceed the 2 MiB limit")
        try:
            obj = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise InvalidSettings("Settings file is malformed") from exc
        if not isinstance(obj, dict) or set(obj) != {"version", "revision", "settings"}:
            raise InvalidSettings("Settings file must contain a versioned envelope")
        settings = validate_settings(obj)
        return {"version": 1, "revision": _revision(obj["revision"]), "settings": settings}

    def read(self):
        """Read fresh state. A missing file yields defaults without creating it."""
        with self._lock:
            return self._read()

    def write(self, settings, expected_revision):
        """Replace plain settings only if the persisted revision still matches."""
        _revision(expected_revision)
        _serialize(settings)
        normalized = _settings(settings)
        with self._lock:
            current = self._read()
            if current["revision"] != expected_revision:
                raise SettingsConflict(expected_revision, current["revision"])
            envelope = {"version": 1, "revision": expected_revision + 1, "settings": normalized}
            data = _serialize(envelope)
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.path)
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
            return envelope
