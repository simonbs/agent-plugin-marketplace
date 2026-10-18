"""Run with: python3 -m unittest discover -s <skill>/tests -v"""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/remote_simulator.py'
spec = importlib.util.spec_from_file_location('remote_simulator', SCRIPT)
rs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rs)
A = 'AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA'
B = 'BBBBBBBB-BBBB-BBBB-BBBB-BBBBBBBBBBBB'
C = 'CCCCCCCC-CCCC-CCCC-CCCC-CCCCCCCCCCCC'


class FakeBackend:
    def __init__(self):
        self.inventory = {udid: {'udid': udid, 'name': udid[0], 'state': 'Booted',
                         'runtime': 'runtime', 'deviceTypeIdentifier': 'type'} for udid in (A, B, C)}
        self.shut_down, self.deleted, self.removed = [], [], []
        self.fail_shutdown = False

    def devices(self):
        return self.inventory

    def hostname(self):
        return 'host.example.ts.net'

    def routes(self):
        return {}

    def discover_processes(self, _session):
        return []

    def shutdown(self, udid):
        if self.fail_shutdown:
            raise rs.SessionError('simulator service unavailable')
        self.shut_down.append(udid)
        self.inventory[udid]['state'] = 'Shutdown'

    def delete(self, udid):
        self.deleted.append(udid)
        self.inventory.pop(udid)

    def remove_route(self, port, target):
        self.removed.append((port, target))


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / 'home'
        self.project = Path(self.temp.name) / 'project'
        self.project.mkdir()
        self.other = Path(self.temp.name) / 'other-worktree'
        self.other.mkdir()
        self.backend = FakeBackend()
        self.now = 1000
        self.manager = rs.Manager(self.home, self.backend, now=lambda: self.now)
        self.alive = patch.object(rs, 'process_alive', return_value=True)
        self.alive.start()

    def tearDown(self):
        self.alive.stop()
        self.temp.cleanup()

    def allocate(self, device=A, project=None, **kwargs):
        return self.manager.allocate(project or self.project, device=device, local=True, **kwargs)[0]

    def running(self, **kwargs):
        session = self.allocate(**kwargs)
        self.manager.update(session['id'], status='running')
        return self.manager.read(session['id'])

    def managed(self):
        session, _ = self.manager.allocate(self.project, template=A, local=True)
        device = dict(self.backend.inventory[B], name=session['device_name'])
        self.backend.inventory[B] = device
        self.manager.update(session['id'], device=B, status='running')
        return self.manager.read(session['id'])

    def test_parallel_projects_get_distinct_sessions_ports_and_exclusive_device(self):
        with ThreadPoolExecutor(2) as pool:
            sessions = list(pool.map(lambda pair: self.allocate(*pair), [(A, self.project), (B, self.other)]))
        self.assertNotEqual(sessions[0]['id'], sessions[1]['id'])
        self.assertEqual(len({s[k] for s in sessions for k in ('gateway_port', 'backend_port')}), 4)
        self.assertEqual(len(self.manager.status()), 2)

    def test_parallel_starts_same_project_reuse_one_session(self):
        with ThreadPoolExecutor(4) as pool:
            sessions = list(pool.map(lambda _: self.allocate(), range(4)))
        self.assertEqual(len({s['id'] for s in sessions}), 1)

    def test_device_reservation_cannot_cross_worktrees(self):
        self.allocate()
        with self.assertRaisesRegex(rs.SessionError, 'reserved'):
            self.allocate(project=self.other)

    def test_capacity_does_not_evict_active_projects(self):
        self.allocate()
        self.allocate(device=B, project=self.other)
        with self.assertRaisesRegex(rs.SessionError, 'slots are busy'):
            self.allocate(device=C)
        self.assertFalse(self.backend.shut_down)

    def test_idle_timeout_and_disconnect_grace(self):
        session = self.running()
        self.manager.heartbeat(session['id'])
        self.now = 1644
        self.assertEqual(self.manager.reap(), [])
        self.now = 1645
        self.assertEqual(self.manager.reap()[0]['status'], 'stopped')
        self.assertFalse(self.backend.shut_down)  # Borrowed simulator.

    def test_active_lease_then_release_starts_idle_grace(self):
        session = self.running()
        lease = self.manager.hold(session['id'], 2000)
        self.now = 2000
        self.assertEqual(self.manager.reap(), [])
        self.manager.release(session['id'], lease['lease'])
        self.now = 2599
        self.assertEqual(self.manager.reap(), [])
        self.now = 2600
        self.assertEqual(self.manager.reap()[0]['status'], 'stopped')

    def test_abandoned_lease_expires_without_release(self):
        session = self.running()
        self.manager.hold(session['id'], 100)
        self.now = 1700
        self.assertEqual(self.manager.reap()[0]['status'], 'stopped')

    def test_max_age_overrides_viewer_and_operation_leases(self):
        session = self.running(max_age=20)
        self.manager.hold(session['id'], 2000)
        self.now = 1019
        self.manager.heartbeat(session['id'])
        self.now = 1020
        self.assertEqual(self.manager.reap()[0]['stop_reason'], 'maximum lifetime reached')
        with self.assertRaises(rs.SessionError):
            self.manager.heartbeat(session['id'])

    def test_worker_failure_reaps_session_immediately(self):
        session = self.managed()
        with patch.object(rs, 'process_alive', return_value=False):
            self.assertEqual(self.manager.reap()[0]['status'], 'stopped')
        self.assertEqual(self.backend.shut_down, [B])
        self.assertEqual(self.backend.inventory[A]['state'], 'Booted')

    def test_stop_removes_only_its_own_route_and_leases(self):
        session = self.managed()
        self.manager.hold(session['id'], 500)
        self.manager.update(session['id'], route_pending=True, remote_port=8443)
        result = self.manager.stop(session['id'])
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual(self.backend.removed, [(8443, f'http://127.0.0.1:{session["gateway_port"]}')])
        self.assertEqual(self.manager.read(session['id'])['leases'], {})
        self.assertNotIn('token', self.manager.read(session['id']))
        self.assertNotIn('url', result)
        self.manager.stop(session['id'])
        self.assertEqual(self.backend.shut_down, [B])

    def test_failed_cleanup_stays_reserved_and_is_retried(self):
        session = self.managed()
        self.backend.fail_shutdown = True
        self.assertEqual(self.manager.stop(session['id'])['status'], 'cleanup_failed')
        self.allocate(device=C, project=self.other)
        with self.assertRaisesRegex(rs.SessionError, 'slots are busy'):
            self.allocate(device=A)
        self.backend.fail_shutdown = False
        self.manager.reap()
        self.assertEqual(self.manager.read(session['id'])['status'], 'stopped')

    def test_creation_crash_recovers_by_reserved_name(self):
        session, _ = self.manager.allocate(self.project, template=A, local=True)
        self.backend.inventory[B]['name'] = session['device_name']
        self.now = session['startup_deadline']
        self.manager.reap()
        self.assertEqual(self.backend.shut_down, [B])

    def test_retained_managed_device_reused_then_pruned(self):
        session = self.managed()
        self.manager.stop(session['id'])
        next_session, reused = self.manager.allocate(self.project, template=A, local=True)
        self.assertFalse(reused)
        self.assertEqual(next_session['device'], B)
        self.assertEqual(self.manager.prune(0), [])  # Reserved by new session.
        self.manager.stop(next_session['id'])
        self.assertEqual(self.manager.prune(0), [B])
        self.assertEqual(self.backend.deleted, [B])

    def test_repurposed_or_running_retained_device_never_deleted(self):
        session = self.managed()
        self.manager.stop(session['id'])
        self.backend.inventory[B]['name'] = 'My personal simulator'
        self.assertEqual(self.manager.prune(0), [])
        self.backend.inventory[B]['name'] = session['device_name']
        self.backend.inventory[B]['state'] = 'Booted'
        self.assertEqual(self.manager.prune(0), [])
        self.assertEqual(self.backend.deleted, [])

    def test_managed_device_cannot_be_reborrowed(self):
        session = self.managed()
        self.manager.stop(session['id'])
        self.backend.inventory[B]['state'] = 'Booted'
        with self.assertRaisesRegex(rs.SessionError, 'manager-owned'):
            self.allocate(device=B)

    def test_nonfinite_or_unbounded_values_rejected(self):
        session = self.running()
        for value in (float('nan'), float('inf'), -1, 0, 86401):
            with self.subTest(value=value), self.assertRaises(rs.SessionError):
                self.manager.hold(session['id'], value)

    def test_project_symlink_resolves_to_same_identity(self):
        alias = Path(self.temp.name) / 'alias'
        alias.symlink_to(self.project)
        session = self.allocate()
        other = self.allocate(project=alias)
        self.assertEqual(session['id'], other['id'])

    def test_retention_removes_only_inactive_session_artifacts(self):
        active = self.running()
        stopped = self.running(device=B, project=self.other)
        self.manager.stop(stopped['id'])
        for sid in (active['id'], stopped['id']):
            directory = self.home / 'sessions' / sid
            directory.mkdir(parents=True)
            (directory / 'serve.log').write_text('diagnostic')
        self.manager.prune(0)
        self.assertTrue((self.home / 'sessions' / active['id']).exists())
        self.assertFalse((self.home / 'sessions' / stopped['id']).exists())

    def test_creation_pending_failure_not_forgotten_before_creation_timeout(self):
        session, _ = self.manager.allocate(self.project, template=A, local=True)
        self.manager.update(session['id'], creation_pending=True)
        result = self.manager.stop(session['id'])
        self.assertEqual(result['status'], 'cleanup_failed')
        self.backend.inventory[B]['name'] = session['device_name']
        self.manager.reap()
        self.assertEqual(self.backend.shut_down, [B])

    def test_cleanup_fences_worker_before_discovering_unjournaled_children(self):
        session = self.running()
        self.manager.update(session['id'], worker={'pid': 123, 'fingerprint': 'worker'})
        events = []
        def discover(_session):
            events.append('discover')
            return [{'pid': 456, 'fingerprint': 'backend'}]
        with patch.object(self.backend, 'discover_processes', side_effect=discover), \
             patch.object(rs, 'terminate', side_effect=lambda record: events.append(record['pid'])):
            self.manager.stop(session['id'])
        self.assertEqual(events, [123, 'discover', 456])


class RouteTests(unittest.TestCase):
    def test_changed_proxy_or_added_handler_is_preserved(self):
        backend = rs.Backend(Path('/tmp'), {})
        states = [
            {'Web': {'host:8443': {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:456'}}}}},
            {'Web': {'host:8443': {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:123'},
                                              '/unrelated': {'Text': 'keep'}}}}},
        ]
        for state in states:
            with patch.object(backend, 'routes', return_value=state), patch.object(backend, 'tailscale') as cli:
                with self.assertRaises(rs.SessionError):
                    backend.remove_route(8443, 'http://127.0.0.1:123')
                cli.assert_not_called()

    def test_owned_route_removed_without_global_reset(self):
        backend = rs.Backend(Path('/tmp'), {})
        state = {'Web': {'host:8443': {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:123'}}}}}
        with patch.object(backend, 'routes', return_value=state), patch.object(backend, 'tailscale') as cli:
            backend.remove_route(8443, 'http://127.0.0.1:123')
            cli.assert_called_once_with('serve', '--https=8443', 'off')


class ProcessTests(unittest.TestCase):
    def test_python_framework_launcher_preserves_process_identity(self):
        prefix = 'Sun Oct 18 08:50:00 2026 '
        args = ' /runtime/remote_simulator.py worker --session abc'
        launcher = rs.normalize_fingerprint(prefix + '/opt/homebrew/bin/python3.14' + args)
        framework = rs.normalize_fingerprint(prefix + '/Frameworks/Python.app/MacOS/Python' + args)
        self.assertEqual(launcher, framework)
        self.assertNotEqual(launcher, rs.normalize_fingerprint(prefix + '/bin/other' + args))

    def test_child_ignoring_term_is_killed_even_when_leader_exits(self):
        child_code = 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'
        parent_code = ('import subprocess,sys,time; '
                       f'p=subprocess.Popen([sys.executable,"-c",{child_code!r}]); '
                       'print(p.pid,flush=True); time.sleep(60)')
        process = subprocess.Popen([sys.executable, '-c', parent_code], stdout=subprocess.PIPE,
                                   text=True, start_new_session=True)
        child = int(process.stdout.readline())
        try:
            time.sleep(0.1)
            rs.terminate(rs.process_record(process))
            process.wait(timeout=5)
            end = time.monotonic() + 2
            while time.monotonic() < end:
                stat = rs.command(['/bin/ps', '-p', str(child), '-o', 'stat='], check=False).stdout.strip()
                if not stat or stat.startswith('Z'):
                    break
                time.sleep(0.05)
            else:
                self.fail('Child survived group cleanup after leader exit')
        finally:
            try:
                os.killpg(process.pid, 9)
            except ProcessLookupError:
                pass
            process.wait()
            process.stdout.close()

    def test_pid_identity_mismatch_does_not_kill_process(self):
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
        try:
            rs.terminate({'pid': process.pid, 'fingerprint': 'a previous process'})
            self.assertIsNone(process.poll())
            rs.terminate(rs.process_record(process))
            process.wait(timeout=5)
            self.assertIsNotNone(process.returncode)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()

    def test_run_releases_lease_and_preserves_exit_status(self):
        with tempfile.TemporaryDirectory() as temp:
            backend = FakeBackend()
            manager = rs.Manager(Path(temp) / 'home', backend)
            session, _ = manager.allocate(temp, device=A, local=True)
            manager.update(session['id'], status='running')
            code = rs.operation(manager, session['id'], [sys.executable, '-c', 'raise SystemExit(7)'])
            self.assertEqual(code, 7)
            self.assertEqual(manager.read(session['id'])['leases'], {})


class SetupTests(unittest.TestCase):
    def test_launchagent_uses_stable_runtime_and_periodic_sweep(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            manager = rs.Manager(home / 'state', FakeBackend())
            package = manager.home / 'deps/node_modules/serve-sim'
            package.mkdir(parents=True)
            (package / 'package.json').write_text(json.dumps({'version': rs.VERSION}))
            args = SimpleNamespace(max_sessions=2, idle_seconds=600, max_age=28800, retention_days=7)
            def fake_command(argv, **_kwargs):
                return SimpleNamespace(stdout='v24.0.0' if '--version' in argv else '', returncode=0)
            with patch.object(rs.sys, 'platform', 'darwin'), patch.object(rs.Path, 'home', return_value=home), \
                 patch.object(rs.shutil, 'which', side_effect=lambda name: f'/usr/local/bin/{name}'), \
                 patch.object(rs, 'command', side_effect=fake_command) as commands:
                result = rs.setup(manager, args)
            path = home / 'Library/LaunchAgents' / f'{rs.LABEL}.plist'
            plist = rs.plistlib.loads(path.read_bytes())
            self.assertEqual(plist['StartInterval'], 30)
            self.assertTrue(plist['RunAtLoad'])
            self.assertEqual(plist['ProgramArguments'][0], '/usr/bin/env')
            self.assertEqual(plist['EnvironmentVariables']['HOME'], str(home))
            self.assertEqual(plist['EnvironmentVariables']['CFFIXED_USER_HOME'], str(home))
            self.assertNotIn('ProcessType', plist)
            self.assertEqual(plist['ProgramArguments'][-1], 'reap')
            self.assertIn(str(manager.home / 'runtime/remote_simulator.py'), plist['ProgramArguments'])
            self.assertTrue(Path(result['runtime'], 'viewer.js').exists())
            self.assertTrue(any('bootstrap' in call.args[0] for call in commands.call_args_list))


class UpstreamHandler(BaseHTTPRequestHandler):
    rbufsize = 0
    def log_message(self, *_):
        pass
    def do_GET(self):
        self.server.paths.append(self.path)
        if self.path.endswith('/ws'):
            key = self.headers['Sec-WebSocket-Key']
            accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            self.protocol_version = 'HTTP/1.1'
            self.send_response(101)
            self.send_header('Upgrade', 'websocket')
            self.send_header('Connection', 'Upgrade')
            self.send_header('Sec-WebSocket-Accept', accept)
            self.end_headers()
            # Echo a complete masked input frame as bytes, testing transparent forwarding.
            frame = self.rfile.read(10)
            self.wfile.write(frame)
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=test')
            self.end_headers()
            self.wfile.write(b'--test\r\nContent-Type: image/jpeg\r\n\r\nJPEGFRAME\r\n')


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = rs.Manager(Path(self.temp.name) / 'home', FakeBackend())
        self.session, _ = self.manager.allocate(self.temp.name, device=A, local=True)
        self.manager.update(self.session['id'], status='running')
        self.gateway = rs.Gateway(self.manager, self.session)
        self.upstream = ThreadingHTTPServer(('127.0.0.1', self.session['backend_port']), UpstreamHandler)
        self.upstream.daemon_threads = True
        self.upstream.paths = []
        for server in (self.gateway, self.upstream):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        self.origin = f'http://127.0.0.1:{self.session["gateway_port"]}'
        self.cookie = f'sbs_{self.session["id"]}={self.session["token"]}'

    def tearDown(self):
        for server in (self.gateway, self.upstream):
            server.shutdown()
            server.server_close()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.session['gateway_port'], timeout=3)
        conn.request(method, path, body, headers or {})
        response = conn.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        conn.close()
        return result

    def test_auth_cookie_fragment_bootstrap_and_no_embedded_secret(self):
        status, body, _ = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertNotIn(self.session['token'].encode(), body)
        status, _, headers = self.request('POST', '/auth', json.dumps({'token': self.session['token']}),
                                          {'Origin': self.origin, 'Content-Type': 'application/json'})
        self.assertEqual(status, 204)
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])

    def test_auth_required_host_origin_and_cookie_isolation(self):
        self.assertEqual(self.request('GET', '/stream')[0], 401)
        self.assertEqual(self.request('POST', '/auth', '{}', {'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('GET', '/', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('GET', '/health', headers={'Cookie': 'sbs_another=token'})[0], 401)

    def test_whitelist_prevents_shell_grid_and_other_simulators(self):
        for path in ('/exec', '/exec-ws', '/grid/api/shutdown', f'/helper/{B}/ws'):
            self.assertEqual(self.request('GET', path, headers={'Cookie': self.cookie})[0], 404)
        self.assertEqual(self.upstream.paths, [])

    def test_heartbeat_is_only_activity_and_stream_is_scoped(self):
        before = self.manager.read(self.session['id'])['viewer_until']
        status, body, _ = self.request('GET', '/stream', headers={'Cookie': self.cookie})
        self.assertEqual(status, 200)
        self.assertIn(b'JPEGFRAME', body)
        self.assertEqual(self.upstream.paths, [f'/helper/{A}/stream.mjpeg'])
        self.assertEqual(self.manager.read(self.session['id'])['viewer_until'], before)
        self.assertEqual(self.request('POST', '/heartbeat', '',
                         {'Cookie': self.cookie, 'Origin': self.origin})[0], 200)
        self.assertGreater(self.manager.read(self.session['id'])['viewer_until'], time.time())

    def test_websocket_handshake_and_binary_controls_reach_only_owned_device(self):
        websocket_key = base64.b64encode(os.urandom(16)).decode()
        with socket.create_connection(('127.0.0.1', self.session['gateway_port']), timeout=3) as client:
            client.sendall((f'GET /input HTTP/1.1\r\nHost: 127.0.0.1:{self.session["gateway_port"]}\r\n'
                            f'Origin: {self.origin}\r\nCookie: {self.cookie}\r\n'
                            'Upgrade: websocket\r\nConnection: Upgrade\r\n'
                            f'Sec-WebSocket-Key: {websocket_key}\r\n'
                            'Sec-WebSocket-Version: 13\r\n\r\n').encode())
            response = bytearray()
            while not response.endswith(b'\r\n\r\n'):
                response.extend(client.recv(1))
            self.assertIn(b'101 Switching Protocols', response)
            frame = b'\x82\x84abcd1234'
            client.sendall(frame)
            actual = bytearray()
            while len(actual) < len(frame):
                actual.extend(client.recv(len(frame) - len(actual)))
            self.assertEqual(actual, frame)
        self.assertEqual(self.upstream.paths, [f'/helper/{A}/ws'])

    def test_absolute_expiry_revokes_existing_browser_cookie(self):
        self.manager.update(self.session['id'], deadline=time.time() - 1)
        self.assertEqual(self.request('GET', '/stream', headers={'Cookie': self.cookie})[0], 410)


@unittest.skipUnless(shutil.which('node'), 'Node.js required for worker integration')
class WorkerIntegrationTests(unittest.TestCase):
    """Real worker/stream processes; fake simctl and capture, no Apple devices."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        bin_dir = self.home / 'bin'
        bin_dir.mkdir()
        xcrun = bin_dir / 'xcrun'
        data = {'devices': {'runtime': [{'udid': udid, 'name': udid[0], 'state': 'Booted',
                 'isAvailable': True, 'deviceTypeIdentifier': 'type'} for udid in (A, B)]}}
        xcrun.write_text(f'#!{sys.executable}\nimport json\nprint({json.dumps(data)!r})\n')
        xcrun.chmod(0o700)
        self.path = patch.dict(os.environ, {'PATH': str(bin_dir) + os.pathsep + os.environ['PATH']})
        self.path.start()
        entry = self.home / 'deps/node_modules/serve-sim/dist/serve-sim.js'
        entry.parent.mkdir(parents=True)
        entry.parent.parent.joinpath('package.json').write_text('{"type":"module"}')
        entry.write_text('''import http from 'node:http';
const port = Number(process.argv[process.argv.indexOf('--port') + 1]);
http.createServer((req, res) => { res.writeHead(200); res.end('fake capture'); })
 .listen(port, '127.0.0.1');
''')
        rs.atomic_json(self.home / 'config.json', {'node': shutil.which('node'), 'idle_seconds': 600})
        self.manager = rs.Manager(self.home)
        self.project_a = self.home / 'project-a'
        self.project_b = self.home / 'project-b'
        self.project_a.mkdir()
        self.project_b.mkdir()

    def tearDown(self):
        for session in self.manager.status():
            self.manager.stop(session['id'])
        self.path.stop()
        self.temp.cleanup()

    def test_two_real_workers_survive_other_project_crash_and_reap(self):
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(self.manager.start, project, device=device, local=True)
                       for project, device in ((self.project_a, A), (self.project_b, B))]
            sessions = [future.result(timeout=20) for future in futures]
        first, second = [self.manager.read(session['id']) for session in sessions]
        self.assertTrue(rs.process_alive(first['worker']))
        self.assertTrue(rs.process_alive(second['backend']))
        os.kill(first['worker']['pid'], 9)
        time.sleep(0.1)
        self.manager.reap()
        self.assertEqual(self.manager.read(first['id'])['status'], 'stopped')
        self.assertFalse(rs.process_alive(first['backend']))
        self.assertEqual(self.manager.read(second['id'])['status'], 'running')
        self.assertTrue(rs.process_alive(second['backend']))
        self.manager.update(second['id'], deadline=time.time() - 1)
        self.manager.reap()
        self.assertFalse(rs.process_alive(second['backend']))

    def test_stop_during_startup_does_not_leave_capture_process(self):
        # Delay health, making the startup/stop overlap deterministic.
        entry = self.home / 'deps/node_modules/serve-sim/dist/serve-sim.js'
        entry.write_text(entry.read_text().replace("res.writeHead(200); res.end('fake capture');",
                                                  "setTimeout(() => { res.writeHead(200); res.end('fake'); }, 5000);"))
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(self.manager.start, self.project_a, device=A, local=True)
            end = time.monotonic() + 10
            session = None
            while time.monotonic() < end:
                states = self.manager.status()
                if states:
                    session = self.manager.read(states[0]['id'])
                    if session.get('backend'):
                        break
                time.sleep(0.025)
            self.assertIsNotNone(session.get('backend'))
            self.manager.stop(session['id'])
            with self.assertRaises(rs.SessionError):
                future.result(timeout=15)
        self.assertFalse(rs.process_alive(session['backend']))
        self.assertEqual(self.manager.backend.discover_processes(session), [])


if __name__ == '__main__':
    unittest.main()
