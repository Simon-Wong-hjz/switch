"""Login service ownership and lifecycle checks without real launchctl calls."""
import json
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from tb import autostart


class Module(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.home = self.root / 'home'
        self.repo = self.root / 'repo with spaces'
        self.data = self.repo / 'data'
        self.repo.mkdir()
        self.home.mkdir()
        self.calls = []
        self.active = False
        self.pid = 12345
        self.bootstrap_code = 0
        self.bootout_code = 0
        self.activate_on_failed_bootstrap = False
        for name, value in {'HOME': self.home, 'REPO': self.repo, 'DATA': self.data,
                            'CODEX_HOME': self.home / 'codex custom',
                            'CLAUDE_HOME': self.home / 'claude custom'}.items():
            setting = patch.object(autostart.paths, name, value)
            setting.start()
            self.addCleanup(setting.stop)
        for setting in (patch.object(autostart.sys, 'platform', 'darwin'),
                        patch.object(autostart, '_launchctl', side_effect=self.launchctl)):
            setting.start()
            self.addCleanup(setting.stop)

    def launchctl(self, *args):
        self.calls.append(args)
        code, output = 0, ''
        if args[0] == 'print':
            if self.active:
                output = f'path = {autostart.target()}\n'
                if self.pid is not None:
                    output += f'pid = {self.pid}\n'
            else:
                code = 113
        elif args[0] == 'bootstrap':
            code = self.bootstrap_code
            self.active = not code or self.activate_on_failed_bootstrap
        elif args[0] == 'bootout':
            code = self.bootout_code
            if not code:
                self.active = False
        elif args[0] == 'kickstart':
            self.pid = 12346
        else:
            self.fail(f'Unexpected launchctl command: {args}')
        return subprocess.CompletedProcess(['/bin/launchctl', *args], code, output,
                                           'system rejected operation' if code and code != 113 else '')

    def install(self):
        self.assertEqual(autostart.on(8766), autostart.target())
        return autostart.target().read_bytes(), autostart.receipt_path().read_bytes()

    def test_config_pins_paths_and_restarts_only_abnormal_exits(self):
        planned = autostart.plan(8766)
        self.assertFalse(self.data.exists(), 'Planning must not write files')
        value = plistlib.loads(planned['content'].encode())
        self.assertEqual(value['Label'], autostart.label())
        self.assertTrue(value['RunAtLoad'])
        self.assertEqual(value['KeepAlive'], {'SuccessfulExit': False})
        for key in ('WorkingDirectory', 'StandardOutPath', 'StandardErrorPath', 'Program'):
            self.assertNotIn(key, value)
        self.assertEqual(value['ProgramArguments'][:2], ['/bin/sh', '-c'])
        self.assertEqual(value['ProgramArguments'][4:],
                         [sys.executable, str(self.repo / 'taskboard.py'), str(self.data.resolve()), '8766'])
        env = value['EnvironmentVariables']
        self.assertEqual(env['TASKBOARD_DATA'], str(self.data.resolve()))
        self.assertEqual(env['CODEX_HOME'], str(autostart.paths.CODEX_HOME.resolve()))
        self.assertEqual(env['CLAUDE_CONFIG_DIR'], str(autostart.paths.CLAUDE_HOME.resolve()))
        original, receipt = self.install()
        self.assertEqual(original, planned['content'].encode())
        self.assertEqual(json.loads(receipt), planned)
        state = autostart.state()
        self.assertTrue(state['installed'])
        self.assertTrue(state['loaded'])
        self.assertEqual(state['port'], 8766)
        self.assertFalse(state['changed'])
        self.assertFalse(state['missing'])

    def test_repeated_enable_preserves_files_and_running_job(self):
        original, receipt = self.install()
        self.calls.clear()
        autostart.on(8766)
        self.assertEqual(autostart.target().read_bytes(), original)
        self.assertEqual(autostart.receipt_path().read_bytes(), receipt)
        self.assertEqual([call[0] for call in self.calls], ['print'])

    def test_loaded_job_accepts_system_canonicalized_path(self):
        alias = self.root / 'home-alias'
        alias.symlink_to(self.home, target_is_directory=True)
        with patch.object(autostart.paths, 'HOME', alias):
            self.install()
            output = f'path = {autostart.target().resolve()}\npid = 12345\n'
            with patch.object(autostart, '_launchctl', return_value=subprocess.CompletedProcess([], 0, output, '')):
                self.assertEqual(autostart.loaded(), {'pid': 12345})

    def test_stopped_loaded_job_can_be_resumed(self):
        self.install()
        self.pid = None
        self.calls.clear()
        autostart.on(8766)
        self.assertEqual([call[0] for call in self.calls], ['print', 'kickstart'])

    def test_suspended_job_keeps_login_config_and_can_be_reenabled(self):
        original, receipt = self.install()
        self.assertEqual(autostart.suspend(), 12345)
        self.assertFalse(self.active)
        self.assertEqual(autostart.target().read_bytes(), original)
        self.assertEqual(autostart.receipt_path().read_bytes(), receipt)
        self.calls.clear()
        autostart.on(8766)
        self.assertEqual([call[0] for call in self.calls], ['print', 'bootstrap'])

    def test_disable_is_idempotent_and_preserves_other_launchagents(self):
        self.install()
        other = autostart.target().parent / 'com.example.other.plist'
        other.write_bytes(b'keep this unrelated file')
        self.assertTrue(autostart.off())
        self.assertFalse(self.active)
        self.assertFalse(autostart.target().exists())
        self.assertFalse(autostart.receipt_path().exists())
        self.assertEqual(other.read_bytes(), b'keep this unrelated file')
        self.calls.clear()
        self.assertFalse(autostart.off())
        self.assertEqual(self.calls, [])

    def test_unowned_target_is_never_replaced_or_unloaded(self):
        autostart.target().parent.mkdir(parents=True)
        autostart.target().write_bytes(b'foreign configuration')
        for operation in (lambda: autostart.on(8766), autostart.off):
            with self.assertRaises(RuntimeError):
                operation()
        self.assertEqual(autostart.target().read_bytes(), b'foreign configuration')
        self.assertFalse(autostart.receipt_path().exists())
        self.assertEqual(self.calls, [])

    def test_modified_owned_target_is_preserved(self):
        _, receipt = self.install()
        autostart.target().write_bytes(b'user modified configuration')
        self.calls.clear()
        for operation in (lambda: autostart.on(8766), autostart.off):
            with self.assertRaises(RuntimeError):
                operation()
        self.assertEqual(autostart.target().read_bytes(), b'user modified configuration')
        self.assertEqual(autostart.receipt_path().read_bytes(), receipt)
        self.assertEqual(self.calls, [])

    def test_invalid_receipt_preserves_target(self):
        original, _ = self.install()
        autostart.receipt_path().write_text('{"version": 99}')
        self.calls.clear()
        with self.assertRaises(RuntimeError):
            autostart.off()
        self.assertEqual(autostart.target().read_bytes(), original)
        self.assertEqual(self.calls, [])

    def test_port_change_requires_explicit_disable(self):
        original, receipt = self.install()
        self.calls.clear()
        with self.assertRaises(RuntimeError):
            autostart.on(8767)
        self.assertEqual(autostart.target().read_bytes(), original)
        self.assertEqual(autostart.receipt_path().read_bytes(), receipt)
        self.assertEqual(self.calls, [])

    def test_foreign_loaded_job_is_not_unloaded(self):
        original, receipt = self.install()
        with patch.object(autostart, '_launchctl', return_value=subprocess.CompletedProcess(
                [], 0, f'path = {self.root / "foreign.plist"}\npid = 12345\n', '')) as launchctl:
            with self.assertRaises(RuntimeError):
                autostart.off()
            self.assertEqual([call.args[0] for call in launchctl.call_args_list], ['print'])
        self.assertEqual(autostart.target().read_bytes(), original)
        self.assertEqual(autostart.receipt_path().read_bytes(), receipt)

    def test_rejected_new_bootstrap_rolls_back_exact_files(self):
        self.bootstrap_code = 5
        with self.assertRaises(RuntimeError):
            autostart.on(8766)
        self.assertFalse(autostart.target().exists())
        self.assertFalse(autostart.receipt_path().exists())

    def test_failed_bootout_after_failed_bootstrap_retains_cleanup_receipt(self):
        self.bootstrap_code = 5
        self.activate_on_failed_bootstrap = True
        self.bootout_code = 5
        with self.assertRaises(RuntimeError):
            autostart.on(8766)
        self.assertTrue(self.active)
        self.assertTrue(autostart.target().exists())
        self.assertTrue(autostart.receipt_path().exists())

    def test_rejected_reenable_keeps_existing_installation(self):
        original, receipt = self.install()
        self.active = False
        self.bootstrap_code = 5
        with self.assertRaises(RuntimeError):
            autostart.on(8766)
        self.assertEqual(autostart.target().read_bytes(), original)
        self.assertEqual(autostart.receipt_path().read_bytes(), receipt)

    def test_failed_disable_retains_configuration(self):
        original, receipt = self.install()
        self.bootout_code = 5
        with self.assertRaises(RuntimeError):
            autostart.off()
        self.assertEqual(autostart.target().read_bytes(), original)
        self.assertEqual(autostart.receipt_path().read_bytes(), receipt)

    def test_unsupported_platform_does_not_write(self):
        with patch.object(autostart.sys, 'platform', 'linux'):
            self.assertIsNone(autostart.state())
            self.assertFalse(autostart.off())
            with self.assertRaises(RuntimeError):
                autostart.on(8766)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.data.exists())
        self.assertFalse((self.home / 'Library').exists())


class Script(unittest.TestCase):
    """Execute only the generated /bin/sh guard, with a tiny local Python file."""

    setUp = Module.setUp
    launchctl = Module.launchctl

    def run_script(self, executable=None):
        with patch.object(autostart.sys, 'executable', executable or sys.executable):
            value = plistlib.loads(autostart.plan(8799)['content'].encode())
        return subprocess.run(value['ProgramArguments'], capture_output=True, text=True, timeout=10)

    def test_missing_repo_exits_normally_without_creating_data(self):
        self.repo.rmdir()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.data.exists())

    def test_missing_interpreter_exits_normally_without_launching_script(self):
        self.data.mkdir()
        (self.repo / 'taskboard.py').write_text('raise RuntimeError("must not run")\n')
        result = self.run_script(str(self.root / 'missing python'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.data / 'server.log').exists())

    def test_present_install_preserves_child_exit_status_and_quotes_paths(self):
        self.data.mkdir()
        (self.repo / 'taskboard.py').write_text(
            'import sys\nprint("started", sys.argv[1:])\nsys.exit(3)\n')
        result = self.run_script()
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("started ['run', '--port', '8799', '--no-open']",
                      (self.data / 'server.log').read_text())


class Cli(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'home').mkdir()
        self.env = dict(os.environ, TASKBOARD_HOME=str(self.root / 'home'),
                        TASKBOARD_DATA=str(self.root / 'data'))

    def test_setup_yes_does_not_enable_autostart(self):
        result = subprocess.run([sys.executable, str(REPO / 'taskboard.py'), 'setup', '--yes'],
                                env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / 'home' / 'Library' / 'LaunchAgents').exists())
        self.assertFalse((self.root / 'data' / 'autostart.json').exists())

    def finish(self, process):
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)

    @unittest.skipUnless(hasattr(signal, 'SIGTERM') and os.name != 'nt', 'POSIX signals')
    def test_sigterm_shuts_down_cleanly_and_removes_process_record(self):
        probe = ('import faulthandler,runpy,sys; faulthandler.dump_traceback_later(5); '
                 'sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name="__main__")')
        process = subprocess.Popen([sys.executable, '-c', probe, str(REPO / 'taskboard.py'), 'run', '--port', '0', '--no-open'],
                                   env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.finish, process)
        record = self.root / 'data' / 'server.pid'
        deadline = time.monotonic() + 15
        while not record.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                output, error = process.communicate()
                self.fail(f'Server failed to start: {output} {error}')
            time.sleep(0.05)
        if not record.exists():
            process.kill()
            output, error = process.communicate(timeout=5)
            self.fail(f'Server did not record readiness: {output} {error}')
        self.assertEqual(json.loads(record.read_text())['pid'], process.pid)
        process.send_signal(signal.SIGTERM)
        self.assertEqual(process.wait(timeout=10), 0, 'Normal stop must not trigger launchd recovery')
        self.assertFalse(record.exists())


if __name__ == '__main__':
    unittest.main()
