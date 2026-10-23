"""Mirror Codex Desktop's current read/unread classification; never write its state."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path

STATE_KEY = 'electron-thread-read-state-v1'


def read_unread(path):
    try:
        with Path(path).open(encoding='utf-8') as source:
            before = os.fstat(source.fileno())
            saved = json.load(source)
            after = os.fstat(source.fileno())
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            return None
        state = saved.get(STATE_KEY) if isinstance(saved, dict) else None
        if not isinstance(state, dict) or state.get('version') != 1:
            return None
        identities = state.get('unreadByIdentity')
        if not isinstance(identities, dict):
            return None
        scopes = {}
        for identity, hosts in identities.items():
            if not isinstance(hosts, dict):
                return None
            for host, ids in hosts.items():
                if not host.startswith('local:'):
                    continue
                if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                    return None
                scopes[(identity, host)] = set(ids)
        if not scopes:
            return None  # Signed out or no local host: unknown, not "everything is read".
        return scopes, datetime.fromtimestamp(after.st_mtime, timezone.utc).isoformat()
    except (OSError, ValueError):
        return None


def _not_older(modified, reply):
    """True when a native write is at or after the reply it would acknowledge."""
    try:
        return datetime.fromisoformat(modified) >= datetime.fromisoformat(reply.replace('Z', '+00:00'))
    except (AttributeError, TypeError, ValueError):
        return False


class ReadTracker:
    """Follow Codex's current classification for Desktop tasks on every refresh.

    A usable snapshot is authoritative: listed means unread, absent means read. That
    is Codex's classification, not proof that a person read the text; it also covers
    a reply that completed in the open conversation and was cleared within
    milliseconds. The cache keeps the last known state for when the snapshot is
    unavailable, plus the action time Switch needs to order its own read actions
    against a native mark-unread.
    """

    def __init__(self, source, cache):
        self.source, self.cache = Path(source), Path(cache)
        self.seen, self.unread = {}, {}
        try:
            saved = json.loads(self.cache.read_text(encoding='utf-8'))
            if isinstance(saved, dict):
                seen = saved.get('seen', {}) if saved.get('version') == 2 else saved
                self.seen = {k: v for k, v in seen.items() if isinstance(v, str)} if isinstance(seen, dict) else {}
                unread = saved.get('unread', {}) if saved.get('version') == 2 else {}
                if isinstance(unread, dict):
                    self.unread = {k: v for k, v in unread.items() if isinstance(v, dict)
                                   and isinstance(v.get('reply'), str) and isinstance(v.get('at'), str)}
        except (OSError, ValueError):
            pass

    def apply(self, tasks):
        # CLI and unknown-origin sessions have no native read signal.
        by_id = {t['id']: t for t in tasks if t.get('agent') == 'codex' and t.get('origin') == 'desktop'}
        snapshot = read_unread(self.source)
        changed = False
        if snapshot:
            scopes, modified = snapshot
            listed = set().union(*scopes.values())
            for tid, task in by_id.items():
                reply = task.get('last_reply')
                if tid in listed:
                    if reply and tid not in self.unread:
                        # Use the native write time, not poll time, and keep it while the thread
                        # stays listed: Switch read actions are ordered against it.
                        self.unread[tid] = {'reply': reply, 'at': modified}
                        self.seen.pop(tid, None)
                        changed = True
                    continue
                if tid in self.unread:
                    del self.unread[tid]
                    changed = True
                # A snapshot written before the reply says nothing about that reply.
                if reply and self.seen.get(tid) != reply and _not_older(modified, reply):
                    self.seen[tid] = reply
                    changed = True
        for tid, task in by_id.items():
            task['desktop_seen_reply'] = self.seen.get(tid)
            unread = self.unread.get(tid, {})
            task['desktop_unread_reply'] = unread.get('reply')
            task['desktop_unread_at'] = unread.get('at')
        if changed:
            tmp = self.cache.with_suffix('.tmp')
            tmp.write_text(json.dumps({'version': 2, 'seen': self.seen, 'unread': self.unread}), encoding='utf-8')
            tmp.replace(self.cache)
