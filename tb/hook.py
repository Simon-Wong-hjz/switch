"""Codex / Claude 的 hook 入口：只把事件追加到 data/events.jsonl，不输出任何内容，不做任何决定，总是以 0 退出。

用法：python3 hook.py codex|claude，事件 JSON 从标准输入读入。
客户端配置里的命令带一层保护：本文件不存在时直接以 0 退出（见 tb/hooks.py）。
"""
import json
import os
import sys
from datetime import datetime, timezone

MAX_BYTES = 5 * 1024 * 1024   # 超过就轮转一次，只保留上一份


def main():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tb import paths
    # Clients send UTF-8 JSON even when Windows uses a legacy console encoding.
    e = json.load(sys.stdin.buffer)
    agent = sys.argv[1] if len(sys.argv) > 1 else 'codex'
    rec = {'ts': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
           'event': e.get('hook_event_name'), 'session_id': e.get('session_id'),
           'turn_id': e.get('turn_id'), 'tool': e.get('tool_name'), 'agent': agent}
    if rec['event'] == 'Notification':
        rec['detail'] = str(e.get('message') or '')[:300]
        rec['ntype'] = e.get('notification_type')
    if rec['event'] == 'PermissionRequest':
        ti = e.get('tool_input') if isinstance(e.get('tool_input'), dict) else {}
        rec['detail'] = str(ti.get('description') or ti.get('command') or '')[:300]
    out = paths.data_dir() / 'events.jsonl'
    if out.exists() and out.stat().st_size > MAX_BYTES:
        os.replace(out, out.with_name('events.1.jsonl'))
    with open(out, 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
