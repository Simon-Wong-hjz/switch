"""Exact final-message extraction and on-demand reads of collected reply snapshots."""
import hashlib
import json
import threading


class ReplyError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def final_text(record, kind):
    """Return visible final text without normalizing its whitespace, or None."""
    if kind == 'codex':
        message = record.get('payload') or {}
        if (record.get('type') != 'response_item' or message.get('type') != 'message'
                or message.get('role') != 'assistant'
                or not (message.get('channel') == 'final' or message.get('phase') == 'final_answer')):
            return None
        content = message.get('content') or []
        parts = [part['text'] for part in content if isinstance(part, dict)
                 and isinstance(part.get('text'), str) and part.get('type') in (None, 'text', 'output_text')]
    else:
        message = record.get('message') or {}
        if (record.get('type') != 'assistant' or record.get('isSidechain')
                or message.get('stop_reason') not in ('end_turn', 'stop_sequence')):
            return None
        content = message.get('content') or []
        if isinstance(content, str):
            parts = [content]
        else:
            parts = [part['text'] for part in content if isinstance(part, dict)
                     and part.get('type') == 'text' and isinstance(part.get('text'), str)]
    text = '\n'.join(parts)
    return text if text.strip() and record.get('timestamp') else None


def reply_id(timestamp, text):
    return hashlib.sha256((timestamp + '\0' + text).encode('utf-8')).hexdigest()


def describe(record, raw, offset, kind):
    text = final_text(record, kind)
    if text is None:
        return None
    return {'id': reply_id(record['timestamp'], text), 'timestamp': record['timestamp'],
            'offset': offset, 'length': len(raw), 'digest': hashlib.sha256(raw).hexdigest()}


class ReplyStore:
    """Keep metadata for published replies, never the reply bodies themselves."""
    def __init__(self):
        self._sessions = {}
        self._lock = threading.Lock()

    def update(self, sources):
        # Called after a successful collection: excluded/archived sessions lose access.
        with self._lock:
            active = {}
            for sid, (path, kind, descriptor) in sources.items():
                old = self._sessions.get(sid, {})
                snapshots = dict(old.get('snapshots', {}))
                if descriptor:
                    snapshots[descriptor['id']] = (str(path), kind, descriptor)
                active[sid] = {'snapshots': snapshots}
            self._sessions = active

    def read(self, session_id, requested_id):
        with self._lock:
            item = self._sessions.get(session_id, {}).get('snapshots', {}).get(requested_id)
        if item is None:
            raise ReplyError(404, 'Reply not found')
        path, kind, descriptor = item
        try:
            with open(path, 'rb') as source:
                source.seek(descriptor['offset'])
                raw = source.read(descriptor['length'])
        except OSError:
            raise ReplyError(503, 'Reply source is unavailable') from None
        if hashlib.sha256(raw).hexdigest() != descriptor['digest']:
            raise ReplyError(410, 'Requested reply is no longer available')
        try:
            record = json.loads(raw)
            text = final_text(record, kind)
            if text is None or reply_id(record['timestamp'], text) != requested_id:
                raise ValueError('Changed reply')
        except (ValueError, TypeError, KeyError):
            raise ReplyError(410, 'Requested reply is no longer available') from None
        return {'session_id': session_id, 'reply_id': requested_id,
                'timestamp': descriptor['timestamp'], 'text': text}
