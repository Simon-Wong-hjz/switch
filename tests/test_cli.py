"""CLI ownership and lifecycle checks, isolated from actual clients and servers."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
spec = importlib.util.spec_from_file_location('taskboard_cli', REPO / 'taskboard.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
from tb import core


class Lifecycle(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.data = self.root / 'data'
        self.data.mkdir()
        self.pid = self.data / 'server.pid'
        setting = patch.object(cli, 'PID', self.pid)
        setting.start()
        self.addCleanup(setting.stop)
        for setting in (patch.object(cli.autostart, 'state', return_value=None),
                        patch.object(cli.autostart, '_launchctl',
                                     side_effect=AssertionError('Unit tests must not call launchctl'))):
            setting.start()
            self.addCleanup(setting.stop)
        self.env = dict(os.environ, TASKBOARD_HOME=str(self.home), TASKBOARD_DATA=str(self.data))

    def record(self, token='ours', pid=99, port=8767):
        info = {'pid': pid, 'port': port, 'token': token}
        self.pid.write_text(json.dumps(info), encoding='utf-8')
        return info

    def test_unrelated_or_reused_process_is_never_stopped(self):
        info = self.record()
        original = self.pid.read_bytes()
        fingerprint = hashlib.sha256(info['token'].encode()).hexdigest()
        foreign = [
            {'app': 'prototype', 'pid': 99, 'instance': fingerprint},
            {'app': 'taskboard', 'pid': 100, 'instance': fingerprint},
            {'app': 'taskboard', 'pid': 99, 'instance': 'another-installation'},
        ]
        for health in foreign:
            with self.subTest(health=health), patch.object(cli, '_request', return_value=health) as request:
                with patch.object(cli.os, 'kill') as kill, contextlib.redirect_stdout(io.StringIO()):
                    cli.cmd_stop(None)
                self.assertEqual(request.call_args.args[1:], ('GET', '/health'))
                self.assertEqual(request.call_count, 1, 'No shutdown request sent to a foreign instance')
                kill.assert_not_called()
                self.assertEqual(self.pid.read_bytes(), original)

    def test_bind_failure_preserves_prior_record_and_does_not_open_browser(self):
        self.record()
        original = self.pid.read_bytes()
        with patch.object(cli, '_running', return_value=None), patch.object(core, 'serve', side_effect=OSError('occupied')):
            with patch.object(cli.webbrowser, 'open') as browser, self.assertRaises(OSError):
                cli.cmd_run(SimpleNamespace(port=8767, no_open=False))
            browser.assert_not_called()
        self.assertEqual(self.pid.read_bytes(), original)

    def test_pid_record_and_browser_follow_server_ready_callback(self):
        server = SimpleNamespace(server_port=43210, token='ready-token')
        with patch.object(cli, '_running', return_value=None), patch.object(cli.webbrowser, 'open') as browser:
            def serve(port, on_ready=None):
                self.assertFalse(self.pid.exists())
                browser.assert_not_called()
                on_ready(server)
                self.assertEqual(cli._pid_info(), {'pid': os.getpid(), 'port': 43210, 'token': 'ready-token'})
                browser.assert_called_once_with('http://127.0.0.1:43210/')

            with patch.object(core, 'serve', side_effect=serve), contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_run(SimpleNamespace(port=0, no_open=False))
        self.assertFalse(self.pid.exists(), 'Owner removes its record after server exit')

    def test_final_cleanup_preserves_another_instances_record(self):
        foreign = {'pid': 123, 'port': 43211, 'token': 'foreign'}

        def serve(port, on_ready=None):
            on_ready(SimpleNamespace(server_port=43210, token='owned'))
            self.pid.write_text(json.dumps(foreign), encoding='utf-8')

        with patch.object(cli, '_running', return_value=None), patch.object(core, 'serve', side_effect=serve):
            with contextlib.redirect_stdout(io.StringIO()):
                cli.cmd_run(SimpleNamespace(port=0, no_open=True))
        self.assertEqual(cli._pid_info(), foreign)

    def test_stop_timeout_blocks_uninstall_and_purge(self):
        info = self.record()
        with patch.object(cli, '_running', return_value=info), patch.object(cli, '_request', return_value={'stopping': True}):
            with patch.object(cli.time, 'monotonic', side_effect=[0, 0, 6]), patch.object(cli.time, 'sleep'):
                with patch.object(cli.hooks, 'uninstall') as uninstall, patch.object(cli.shutil, 'rmtree') as purge:
                    with self.assertRaises(RuntimeError):
                        cli.cmd_uninstall(SimpleNamespace(purge=True))
                    uninstall.assert_not_called()
                    purge.assert_not_called()
        self.assertEqual(cli._pid_info(), info)

    def test_unknown_instance_record_blocks_purge(self):
        self.record()
        with patch.object(cli, '_running', return_value=None), patch.object(cli.hooks, 'uninstall') as uninstall:
            with self.assertRaises(RuntimeError):
                cli.cmd_uninstall(SimpleNamespace(purge=True))
            uninstall.assert_not_called()
        self.assertTrue(self.pid.exists())

    def test_autostart_plan_failure_does_not_stop_existing_server(self):
        info = self.record()
        with patch.object(cli.autostart, 'plan', side_effect=RuntimeError('modified startup file')):
            with patch.object(cli, '_running', return_value=info), patch.object(cli, 'cmd_stop') as stop:
                with patch.object(cli.autostart, 'on') as enable, patch.object(cli.autostart, 'loaded') as loaded:
                    with self.assertRaisesRegex(RuntimeError, 'modified startup file'):
                        cli._enable_autostart(8767)
                    stop.assert_not_called()
                    enable.assert_not_called()
                    loaded.assert_not_called()
        self.assertEqual(cli._pid_info(), info)

    def test_autostart_transfers_unmanaged_server_only_after_planning(self):
        unmanaged = {'pid': 99, 'port': 8767, 'token': 'foreground'}
        managed = {'pid': 100, 'port': 8767, 'token': 'background'}
        events = []
        with patch.object(cli.autostart, 'plan', side_effect=lambda port: events.append(('plan', port))):
            with patch.object(cli.autostart, 'loaded', side_effect=[None, {'pid': 100}]):
                with patch.object(cli, '_running', side_effect=[unmanaged, managed]):
                    with patch.object(cli, 'cmd_stop', side_effect=lambda _: events.append(('stop', None))):
                        with patch.object(cli.autostart, 'on', side_effect=lambda port: events.append(('on', port))):
                            with contextlib.redirect_stdout(io.StringIO()):
                                cli._enable_autostart(8767)
        self.assertEqual(events, [('plan', 8767), ('stop', None), ('on', 8767)])

    def test_autostart_readiness_requires_managed_pid_and_selected_port(self):
        wrong_pid = {'pid': 100, 'port': 8767, 'token': 'foreign'}
        wrong_port = {'pid': 99, 'port': 8768, 'token': 'ours'}
        ready = {'pid': 99, 'port': 8767, 'token': 'ours'}
        output = io.StringIO()
        with patch.object(cli.autostart, 'plan'), patch.object(cli.autostart, 'on'):
            with patch.object(cli.autostart, 'loaded', side_effect=[None, {'pid': 99}, {'pid': 99}, {'pid': 99}]):
                with patch.object(cli, '_running', side_effect=[None, wrong_pid, wrong_port, ready]):
                    with patch.object(cli, 'cmd_stop') as stop, patch.object(cli.time, 'sleep') as sleep:
                        with patch.object(cli.time, 'monotonic', side_effect=[0, 0, 1, 2]):
                            with contextlib.redirect_stdout(output):
                                cli._enable_autostart(8767)
                    stop.assert_not_called()
                    self.assertEqual(sleep.call_count, 2, 'Neither wrong instance is ready')
        self.assertIn('http://127.0.0.1:8767/', output.getvalue())

    def test_autostart_keeps_existing_managed_server_running(self):
        managed = {'pid': 99, 'port': 8767, 'token': 'background'}
        with patch.object(cli.autostart, 'plan'), patch.object(cli.autostart, 'on') as enable:
            with patch.object(cli.autostart, 'loaded', return_value={'pid': 99}):
                with patch.object(cli, '_running', return_value=managed), patch.object(cli, 'cmd_stop') as stop:
                    with contextlib.redirect_stdout(io.StringIO()):
                        cli._enable_autostart(8767)
                    stop.assert_not_called()
                    enable.assert_called_once_with(8767)

    def test_changed_startup_blocks_uninstall_before_hooks_or_purge(self):
        state = {'installed': True, 'changed': True, 'pid': 99}
        with patch.object(cli.autostart, 'state', return_value=state):
            with patch.object(cli.autostart, 'off', side_effect=RuntimeError('modified startup file')):
                with patch.object(cli, '_running') as running, patch.object(cli.hooks, 'uninstall') as uninstall:
                    with patch.object(cli.shutil, 'rmtree') as purge:
                        with self.assertRaisesRegex(RuntimeError, 'modified startup file'):
                            cli.cmd_uninstall(SimpleNamespace(purge=True))
                        running.assert_not_called()
                        uninstall.assert_not_called()
                        purge.assert_not_called()

    @unittest.skipUnless(hasattr(cli.signal, 'SIGTERM'), 'SIGTERM is unavailable')
    def test_run_restores_previous_sigterm_handler_on_exit_and_failure(self):
        previous = cli.signal.SIG_DFL
        for error in (None, OSError('occupied')):
            with self.subTest(error=error), patch.object(cli, '_running', return_value=None):
                with patch.object(core, 'serve', side_effect=error):
                    with patch.object(cli.signal, 'signal', return_value=previous) as signal:
                        if error:
                            with self.assertRaises(OSError):
                                cli.cmd_run(SimpleNamespace(port=0, no_open=True))
                        else:
                            cli.cmd_run(SimpleNamespace(port=0, no_open=True))
                        self.assertEqual(signal.call_count, 2)
                        self.assertEqual(signal.call_args.args, (cli.signal.SIGTERM, previous))

    def finish(self, process):
        # Only the child created by this test can be terminated here.
        if process.poll() is None:
            process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)

    @unittest.skipIf(os.name == 'nt', 'Windows terminate does not deliver a catchable POSIX SIGTERM')
    def test_real_start_repeat_bind_failure_and_authenticated_stop(self):
        probe = ('import faulthandler,runpy,sys; faulthandler.dump_traceback_later(5); '
                 'sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name="__main__")')
        process = subprocess.Popen([sys.executable, '-c', probe, str(REPO / 'taskboard.py'), 'run', '--port', '0', '--no-open'],
                                   env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.finish, process)
        deadline = time.monotonic() + 10
        while not self.pid.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                self.fail(f'Server failed to start: {stdout} {stderr}')
            time.sleep(0.05)
        if not self.pid.exists():
            process.kill()
            stdout, stderr = process.communicate(timeout=5)
            self.fail(f'Server did not record readiness: {stdout} {stderr}')
        info = cli._pid_info()
        self.assertEqual(info['pid'], process.pid)
        self.assertEqual(cli._running(), info)
        if os.name == 'posix':
            self.assertEqual(self.pid.stat().st_mode & 0o777, 0o600)

        repeat = subprocess.run([sys.executable, str(REPO / 'taskboard.py'), 'run', '--port', '1', '--no-open'],
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(repeat.returncode, 0, repeat.stderr)
        self.assertIn(f'http://127.0.0.1:{info["port"]}/', repeat.stdout)
        self.assertEqual(cli._pid_info(), info)

        # Another data directory has a stale record while this port is occupied.
        other_data = self.root / 'other-data'
        other_data.mkdir()
        stale = {**info, 'token': 'stale-token'}
        other_pid = other_data / 'server.pid'
        other_pid.write_text(json.dumps(stale), encoding='utf-8')
        original = other_pid.read_bytes()
        failure = subprocess.run([sys.executable, str(REPO / 'taskboard.py'), 'run', '--port', str(info['port']), '--no-open'],
                                 env={**self.env, 'TASKBOARD_DATA': str(other_data)}, capture_output=True,
                                 text=True, timeout=10)
        self.assertEqual(failure.returncode, 1)
        self.assertEqual(other_pid.read_bytes(), original)
        self.assertIsNone(process.poll())

        stopped = subprocess.run([sys.executable, str(REPO / 'taskboard.py'), 'stop'],
                                 env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertFalse(self.pid.exists())


if __name__ == '__main__':
    unittest.main()
