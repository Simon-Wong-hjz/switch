"""安装与卸载的足迹审计。全部在临时目录里跑：TASKBOARD_HOME 替换用户主目录，TASKBOARD_DATA 替换数据目录，不碰真实配置。

运行：python3 tests/test_install.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from tb import hooks, paths
USER_SETTINGS = {
    'model': 'opus',
    'permissions': {'allow': ['Bash']},
    'hooks': {'Stop': [{'matcher': '', 'hooks': [{'type': 'command', 'command': 'echo user-own-hook'}]}]},
}


def snapshot(root):
    """目录下每个文件的相对路径 -> 内容。"""
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(Path(root).rglob('*')) if p.is_file()}


class Install(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'home'
        self.data = Path(self.tmp.name) / 'data'
        (self.home / '.codex').mkdir(parents=True)                       # 装了 Codex，但还没有 hooks.json
        (self.home / '.claude').mkdir()
        (self.home / '.claude' / 'settings.json').write_text(json.dumps(USER_SETTINGS, indent=2) + '\n', encoding='utf-8')
        for name, value in {
            'DATA': self.data, 'CODEX_HOME': self.home / '.codex', 'CLAUDE_HOME': self.home / '.claude',
            'CODEX_HOOKS': self.home / '.codex' / 'hooks.json',
            'CLAUDE_SETTINGS': self.home / '.claude' / 'settings.json',
        }.items():
            setting = patch.object(paths, name, value)
            setting.start()
            self.addCleanup(setting.stop)

    def cli(self, *args, repo=REPO):
        env = dict(os.environ, TASKBOARD_HOME=str(self.home), TASKBOARD_DATA=str(self.data))
        r = subprocess.run([sys.executable, str(repo / 'taskboard.py'), *args], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def claude_settings(self):
        return json.loads((self.home / '.claude' / 'settings.json').read_text(encoding='utf-8'))

    def test_setup_only_adds_marked_entries_and_is_idempotent(self):
        before = snapshot(self.home)
        self.cli('setup', '--yes')
        after = snapshot(self.home)
        # 主目录里只多了 Codex 的 hooks.json、只改了 Claude 的 settings.json
        self.assertEqual(set(after) - set(before), {'.codex/hooks.json'})
        self.assertEqual({k for k in before if before[k] != after[k]}, {'.claude/settings.json'})
        s = self.claude_settings()
        self.assertEqual({k: v for k, v in s.items() if k != 'hooks'}, {k: v for k, v in USER_SETTINGS.items() if k != 'hooks'})
        self.assertIn({'matcher': '', 'hooks': [{'type': 'command', 'command': 'echo user-own-hook'}]}, s['hooks']['Stop'])
        ours = [h for groups in s['hooks'].values() for g in groups for h in g['hooks'] if 'TASKBOARD_HOOK=1' in h['command']]
        self.assertEqual(len(ours), 3)
        # 再跑一次：不重复追加，文件一个字节都不变
        self.cli('setup', '--yes')
        self.assertEqual(snapshot(self.home), after)

    def test_uninstall_removes_only_ours_and_keeps_later_user_changes(self):
        before = snapshot(self.home)
        self.cli('setup', '--yes')
        # 安装之后用户自己又改了配置
        s = self.claude_settings()
        s['theme'] = 'dark'
        s['hooks'].setdefault('Notification', []).append({'matcher': '', 'hooks': [{'type': 'command', 'command': 'echo later'}]})
        (self.home / '.claude' / 'settings.json').write_text(json.dumps(s, indent=2), encoding='utf-8')
        self.cli('uninstall')
        s = self.claude_settings()
        self.assertEqual(s['theme'], 'dark', '用户安装后新增的配置必须保留')
        self.assertIn({'matcher': '', 'hooks': [{'type': 'command', 'command': 'echo later'}]}, s['hooks']['Notification'])
        self.assertNotIn('TASKBOARD_HOOK=1', json.dumps(s))
        self.assertFalse((self.home / '.codex' / 'hooks.json').exists(), '本工具新建的 hooks.json 撤销后应删除')
        # 除用户自己的那两处改动，主目录回到安装前
        s.pop('theme'); s['hooks']['Notification'].pop()
        if not s['hooks']['Notification']:
            del s['hooks']['Notification']
        self.assertEqual(s, json.loads(before['.claude/settings.json']))
        self.assertEqual(set(snapshot(self.home)), set(before))
        self.assertIn('没有找到', self.cli('uninstall'))   # 重复卸载无害

    def test_existing_codex_hooks_file_is_kept_on_uninstall(self):
        existing = {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'echo mine'}]}]}}
        (self.home / '.codex' / 'hooks.json').write_text(json.dumps(existing), encoding='utf-8')
        self.cli('setup', '--yes')
        self.cli('uninstall')
        self.assertEqual(json.loads((self.home / '.codex' / 'hooks.json').read_text(encoding='utf-8')), existing)

    def test_client_not_installed_means_no_writes(self):
        shutil.rmtree(self.home / '.codex')
        self.cli('setup', '--yes')
        self.assertFalse((self.home / '.codex').exists(), '没装 Codex 就不应创建它的目录或配置')

    def test_hooks_record_events_into_data_dir(self):
        self.cli('setup', '--yes')
        cmd = next(h['command'] for g in self.claude_settings()['hooks']['Stop'] for h in g['hooks'] if 'TASKBOARD_HOOK=1' in h['command'])
        env = dict(os.environ, TASKBOARD_HOME=str(self.home), TASKBOARD_DATA=str(self.data))
        r = subprocess.run(cmd, shell=True, input='{"hook_event_name":"Stop","session_id":"s1"}', env=env, capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, '', ''), 'hook 不输出、以 0 退出')
        rec = json.loads((self.data / 'events.jsonl').read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual((rec['event'], rec['session_id'], rec['agent']), ('Stop', 's1', 'claude'))

    def test_deleting_the_directory_without_uninstall_is_harmless(self):
        # 从一份临时副本安装，然后直接删掉副本：残留的 hook 必须静默以 0 退出（否则 Claude Code 会拦截用户的每条消息）
        copy = Path(self.tmp.name) / 'copy'
        shutil.copytree(REPO, copy, ignore=shutil.ignore_patterns('data', '__pycache__', 'tests'))
        self.cli('setup', '--yes', repo=copy)
        shutil.rmtree(copy)
        commands = [h['command'] for g in self.claude_settings()['hooks'].values() for gg in g for h in gg['hooks'] if 'TASKBOARD_HOOK=1' in h['command']]
        self.assertTrue(commands)
        for cmd in commands:
            r = subprocess.run(cmd, shell=True, input='{"hook_event_name":"UserPromptSubmit"}', capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, '', ''))

    @unittest.skipIf(os.name == 'nt', 'Windows runtime checks deferred until release')
    def test_missing_interpreter_is_silent_and_succeeds(self):
        with patch.object(hooks.sys, 'executable', str(Path(self.tmp.name) / 'missing-python')):
            command = hooks.command('claude')
        result = subprocess.run(command, shell=True, input='{}', capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))

    def test_uninstall_preserves_user_modified_entry_and_codex_description(self):
        self.cli('setup', '--yes')
        data = self.claude_settings()
        owned = next(group for group in data['hooks']['Stop'] if 'TASKBOARD_HOOK=1' in json.dumps(group))
        owned['hooks'][0]['timeout'] = 20
        paths.CLAUDE_SETTINGS.write_text(json.dumps(data), encoding='utf-8')
        codex = json.loads(paths.CODEX_HOOKS.read_text(encoding='utf-8'))
        codex['description'] = 'User edited this file'
        paths.CODEX_HOOKS.write_text(json.dumps(codex), encoding='utf-8')
        output = self.cli('uninstall')
        self.assertIn(owned, self.claude_settings()['hooks']['Stop'])
        self.assertIn('不匹配', output)
        self.assertEqual(json.loads(paths.CODEX_HOOKS.read_text(encoding='utf-8')), {'description': 'User edited this file'})

    def test_only_modified_hooks_are_reported_and_left_byte_for_byte_intact(self):
        self.cli('setup', '--yes', '--only', 'claude')
        data = self.claude_settings()
        for groups in data['hooks'].values():
            for group in groups:
                if 'TASKBOARD_HOOK=1' in json.dumps(group):
                    group['hooks'][0]['timeout'] = 20
        paths.CLAUDE_SETTINGS.write_text(json.dumps(data), encoding='utf-8')
        before = paths.CLAUDE_SETTINGS.read_bytes()
        output = self.cli('uninstall')
        self.assertIn('移除 0 条', output)
        self.assertIn('3 条安装记录不匹配', output)
        self.assertEqual(paths.CLAUDE_SETTINGS.read_bytes(), before)

    def test_created_claude_settings_are_deleted_when_empty_after_uninstall(self):
        paths.CLAUDE_SETTINGS.unlink()
        before = snapshot(self.home)
        self.cli('setup', '--yes')
        self.assertTrue(paths.CLAUDE_SETTINGS.exists())
        self.cli('uninstall')
        self.assertFalse(paths.CLAUDE_SETTINGS.exists())
        self.assertEqual(snapshot(self.home), before)

    def test_preexisting_empty_claude_settings_are_kept(self):
        paths.CLAUDE_SETTINGS.write_text('{}', encoding='utf-8')
        self.cli('setup', '--yes')
        self.cli('uninstall')
        self.assertTrue(paths.CLAUDE_SETTINGS.exists())
        self.assertEqual(self.claude_settings(), {})

    def test_other_installation_keeps_its_hooks(self):
        other = Path(self.tmp.name) / 'other installation'
        shutil.copytree(REPO, other, ignore=shutil.ignore_patterns('data', '__pycache__', 'tests'))
        other_data = Path(self.tmp.name) / 'other-data'
        self.cli('setup', '--yes')
        env = dict(os.environ, TASKBOARD_HOME=str(self.home), TASKBOARD_DATA=str(other_data))
        result = subprocess.run([sys.executable, str(other / 'taskboard.py'), 'setup', '--yes'],
                                env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        foreign = json.loads((other_data / 'install.json').read_text(encoding='utf-8'))
        self.cli('setup', '--yes')  # A repeat setup must also preserve B's entries.
        self.cli('uninstall')
        remaining = self.claude_settings()['hooks']
        for entry in foreign['claude']['entries']:
            self.assertIn(entry['group'], remaining[entry['event']])
        self.assertIn(USER_SETTINGS['hooks']['Stop'][0], remaining['Stop'])

    def test_without_manifest_marked_entries_are_not_claimed(self):
        data = self.claude_settings()
        data['hooks']['Stop'].append({'hooks': [{'type': 'command', 'command': 'echo TASKBOARD_HOOK=1'}]})
        paths.CLAUDE_SETTINGS.write_text(json.dumps(data), encoding='utf-8')
        before = snapshot(self.home)
        self.cli('uninstall')
        self.assertEqual(snapshot(self.home), before)

    def test_malformed_manifest_or_config_prevents_all_writes(self):
        self.cli('setup', '--yes')
        file = self.data / 'install.json'
        for raw in ('{', '{"codex":{"file":"untracked","created":true}}',
                    '{"codex":{},"codex":{}}'):
            with self.subTest(raw=raw):
                file.write_text(raw, encoding='utf-8')
                before = snapshot(self.home)
                for operation in (hooks.install, hooks.uninstall):
                    with self.assertRaises(ValueError):
                        operation()
                    self.assertEqual(snapshot(self.home), before)
                    self.assertEqual(file.read_text(encoding='utf-8'), raw)
        file.unlink()
        paths.CLAUDE_SETTINGS.write_text('{"hooks":null}', encoding='utf-8')
        before = snapshot(self.home)
        with self.assertRaises(ValueError):
            hooks.install()
        self.assertEqual(snapshot(self.home), before)

    def test_late_install_failure_restores_exact_prior_config_bytes(self):
        paths.CODEX_HOOKS.write_text('{ "user": "existing", "hooks": {} }\n', encoding='utf-8')
        original_write = hooks._write
        for failure in (paths.CLAUDE_SETTINGS, self.data / 'install.json'):
            with self.subTest(failure=failure):
                before = snapshot(self.home)

                def write(path, data):
                    if path == failure:
                        raise OSError('simulated failure')
                    original_write(path, data)

                with patch.object(hooks, '_write', side_effect=write), self.assertRaises(OSError):
                    hooks.install()
                self.assertEqual(snapshot(self.home), before)
                self.assertEqual(snapshot(self.data), {})

    def test_install_failure_removes_newly_created_config(self):
        original_write = hooks._write

        def write(path, data):
            if path == paths.CLAUDE_SETTINGS:
                raise OSError('simulated failure')
            original_write(path, data)

        before = snapshot(self.home)
        with patch.object(hooks, '_write', side_effect=write), self.assertRaises(OSError):
            hooks.install()
        self.assertEqual(snapshot(self.home), before)
        self.assertFalse(paths.CODEX_HOOKS.exists())

    def test_uninstall_failure_restores_deleted_config_and_manifest(self):
        self.cli('setup', '--yes')
        before_home, before_data = snapshot(self.home), snapshot(self.data)
        with patch.object(hooks, '_write', side_effect=OSError('simulated failure')), self.assertRaises(OSError):
            hooks.uninstall()
        self.assertEqual(snapshot(self.home), before_home)
        self.assertEqual(snapshot(self.data), before_data)


if __name__ == '__main__':
    unittest.main(verbosity=1)
