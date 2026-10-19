"""Observe Codex Desktop read/unread changes; never write its state."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path

STATE_KEY = 'electron-thread-read-state-v1'


def read_unread(path):
    try:
        with Path(path).open() as source:
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
        return scopes, datetime.fromtimestamp(after.st_mtime, timezone.utc).isoformat()
    except (OSError, ValueError):
        return None


class ReadTracker:
    def __init__(self, source, cache):
        self.source, self.cache = Path(source), Path(cache)
        self.previous = {}
        self.seen, self.unread = {}, {}
        try:
            saved = json.loads(self.cache.read_text())
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
        by_id = {t['id']: t for t in tasks if t.get('agent') == 'codex'}
        snapshot = read_unread(self.source)
        changed = False
        if snapshot is None:
            self.previous = {}  # Unknown/missing state is not a read event.
        else:
            scopes, modified = snapshot
            all_unread = set().union(*scopes.values()) if scopes else set()
            previous_unread = {tid for scope in self.previous.values() for tid in scope}
            next_previous = {}
            for scope, unread in scopes.items():
                previous = self.previous.get(scope, {})
                for tid in previous.keys() - all_unread:
                    reply = previous[tid]
                    # A concurrent new reply must not be cleared by an older read event.
                    if tid in by_id and reply and by_id[tid].get('last_reply') == reply and self.seen.get(tid) != reply:
                        self.seen[tid] = reply
                        changed = True
                    if tid in self.unread:
                        del self.unread[tid]
                        changed = True
                for tid in unread:
                    reply = by_id.get(tid, {}).get('last_reply')
                    entered = scope in self.previous and tid not in previous_unread
                    if reply and (tid not in self.unread or entered):
                        # Use the native write time, not poll time. Preserve an old
                        # action across unrelated writes, new replies and restarts.
                        self.unread[tid] = {'reply': reply, 'at': modified}
                        self.seen.pop(tid, None)
                        changed = True
                next_previous[scope] = {tid: by_id[tid].get('last_reply') for tid in unread if tid in by_id}
            self.previous = next_previous  # Logout/host removal is not a read event.
        for tid, task in by_id.items():
            task['desktop_seen_reply'] = self.seen.get(tid)
            unread = self.unread.get(tid, {})
            task['desktop_unread_reply'] = unread.get('reply')
            task['desktop_unread_at'] = unread.get('at')
        if changed:
            tmp = self.cache.with_suffix('.tmp')
            tmp.write_text(json.dumps({'version': 2, 'seen': self.seen, 'unread': self.unread}))
            tmp.replace(self.cache)
