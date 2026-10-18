#!/usr/bin/env python3
"""Per-user simulator sessions. Python standard library only; macOS runtime.

State changes are serialized with flock and written with atomic replacement.
Slow boot/start work runs in a detached worker, outside the registry lock.
"""
import argparse
import contextlib
import fcntl
import hmac
import http.client
import http.cookies
import json
import math
import os
from pathlib import Path
import plistlib
import re
import secrets
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

VERSION = '0.1.48'
LABEL = 'com.simonbs.remote-simulator'
ROOT = Path(__file__).resolve().parent
DEFAULT_HOME = Path.home() / 'Library/Application Support/sbs-remote-simulator'
ACTIVE = {'starting', 'running', 'stopping', 'cleanup_failed'}
DEFAULTS = {'max_sessions': 2, 'idle_seconds': 600, 'max_age': 28800,
            'retention_days': 7, 'viewer_seconds': 45, 'startup_seconds': 420}
UDID = re.compile(r'^[A-Fa-f0-9]{8}(?:-[A-Fa-f0-9]{4}){3}-[A-Fa-f0-9]{12}$')
SESSION = re.compile(r'^[a-f0-9]{32}$')


class SessionError(RuntimeError):
    pass


def command(argv, timeout=30, check=True, **kwargs):
    result = subprocess.run([str(x) for x in argv], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=timeout, **kwargs)
    if check and result.returncode:
        # Do not echo command arguments: they may contain access tokens.
        raise SessionError(f'{Path(str(argv[0])).name} failed: {result.stderr.strip()[-1500:]}')
    return result


def atomic_json(path, value):
    temp = path.with_suffix('.tmp')
    with temp.open('w') as stream:
        os.chmod(temp, 0o600)
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def expiry_reason(session, now, config):
    if session['status'] not in ACTIVE:
        return None
    if session['status'] in {'stopping', 'cleanup_failed'}:
        return 'unfinished cleanup'
    if now >= session['deadline']:
        return 'maximum lifetime reached'
    if session['status'] == 'starting':
        return 'startup timed out' if now >= session['startup_deadline'] else None
    held_until = max(session.get('leases', {}).values(), default=0)
    viewer_until = session.get('viewer_until', 0)
    # Idle grace starts after the last viewer/operation lease expires.
    last_use = max(session['last_activity'], viewer_until, held_until)
    if now >= last_use + config['idle_seconds']:
        return 'idle lease expired'
    return None


def normalize_fingerprint(value):
    parts = value.strip().split(None, 5)
    if len(parts) != 6:
        return ''
    # macOS's Python launcher execs the framework binary after Popen returns.
    # Birth time and arguments remain stable; the executable prefix does not.
    executable, _, arguments = parts[5].partition(' ')
    name = Path(executable).name.lower()
    if name.startswith('python'):
        name = 'python'
    return ' '.join(parts[:5]) + ' ' + name + ' ' + arguments


def fingerprint(pid):
    result = command(['/bin/ps', '-p', str(pid), '-o', 'lstart=', '-o', 'command='], check=False)
    return normalize_fingerprint(result.stdout) if result.returncode == 0 else ''


def process_record(process):
    return {'pid': process.pid, 'fingerprint': fingerprint(process.pid)}


def process_alive(record):
    return bool(record and record.get('fingerprint') and
                fingerprint(record['pid']) == record['fingerprint'])


def group_members(pgid):
    result = command(['/bin/ps', '-axo', 'pid=,pgid=,lstart=,command='])
    members = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) == 3 and fields[1] == str(pgid):
            members.append({'pid': int(fields[0]), 'fingerprint': normalize_fingerprint(fields[2])})
    return members


def terminate(record):
    """Signal only an identified process group that we started as its leader."""
    if not process_alive(record):
        return
    pid = record['pid']
    try:
        if os.getpgid(pid) != pid:
            raise SessionError('Refusing to stop a process outside its owned group')
        members = group_members(pid)
        os.killpg(pid, signal.SIGTERM)
        end = time.monotonic() + 3
        while time.monotonic() < end and any(process_alive(member) for member in members):
            # As long as an identified member lives, this is still our group.
            members = group_members(pid)
            time.sleep(0.05)
        if any(process_alive(member) for member in members):
            os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class Backend:
    def __init__(self, home, config):
        self.home, self.config = home, config

    def simctl(self, *args, **kwargs):
        return command(['xcrun', 'simctl', *args], **kwargs)

    def devices(self):
        result = json.loads(self.simctl('list', 'devices', '-j').stdout)
        return {device['udid']: dict(device, runtime=runtime)
                for runtime, devices in result['devices'].items() for device in devices}

    def create(self, name, template):
        return self.simctl('create', name, template['deviceTypeIdentifier'],
                           template['runtime'], timeout=60).stdout.strip()

    def request_boot(self, udid):
        if self.devices()[udid]['state'] != 'Booted':
            self.simctl('boot', udid, timeout=90)

    def wait_boot(self, udid):
        self.simctl('bootstatus', udid, timeout=180)

    def shutdown(self, udid):
        device = self.devices().get(udid)
        if device and device['state'] != 'Shutdown':
            self.simctl('shutdown', udid, timeout=45)

    def delete(self, udid):
        self.simctl('delete', udid, timeout=45)

    def tailscale(self, *args):
        binary = self.config.get('tailscale') or shutil.which('tailscale')
        if not binary:
            raise SessionError('Tailscale is required for remote access')
        return command([binary, *args], timeout=20)

    def routes(self):
        return json.loads(self.tailscale('serve', 'status', '--json').stdout or '{}')

    def hostname(self):
        info = json.loads(self.tailscale('status', '--json').stdout)
        if info.get('BackendState') != 'Running':
            raise SessionError('Sign in to Tailscale before starting a remote session')
        name = info.get('Self', {}).get('DNSName', '').rstrip('.')
        if not name:
            raise SessionError('Enable Tailscale DNS and HTTPS before starting a session')
        return name

    def route_target(self, port):
        for key, value in self.routes().get('Web', {}).items():
            if key.rsplit(':', 1)[-1] == str(port):
                return value.get('Handlers', {}).get('/', {}).get('Proxy')
        return None

    def add_route(self, port, target):
        # Never overwrite even a non-HTTP listener on this port.
        routes = self.routes()
        if str(port) in routes.get('TCP', {}) or any(
                key.rsplit(':', 1)[-1] == str(port) for key in routes.get('Web', {})):
            raise SessionError(f'Tailscale port {port} already has a configuration')
        self.tailscale('serve', '--bg', f'--https={port}', '--yes', target)

    def remove_route(self, port, target):
        for key, value in self.routes().get('Web', {}).items():
            if key.rsplit(':', 1)[-1] == str(port) and set(value.get('Handlers', {})) != {'/'}:
                raise SessionError(f'Tailscale route {port} gained other handlers; left untouched')
        actual = self.route_target(port)
        if actual is None:
            return
        if actual.rstrip('/') != target.rstrip('/'):
            raise SessionError(f'Tailscale route {port} changed ownership; left untouched')
        # This port was reserved exclusively. No global reset or service removal.
        self.tailscale('serve', f'--https={port}', 'off')

    def discover_processes(self, session):
        """Recover children if a worker died between spawn and registry write."""
        output = command(['/bin/ps', '-axo', 'pid=,command=']).stdout
        records = []
        marker = f'--session {session["id"]}'
        runner = str(Path(session['runtime']) / 'serve_runner.mjs')
        worker_script = str(Path(session['runtime']) / 'remote_simulator.py')
        for line in output.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2 or marker not in parts[1]:
                continue
            if not (runner in parts[1] or (worker_script in parts[1] and f' worker {marker}' in parts[1])):
                continue
            pid = int(parts[0])
            if pid != os.getpid():
                records.append({'pid': pid, 'fingerprint': fingerprint(pid)})
        return records


class Manager:
    def __init__(self, home=DEFAULT_HOME, backend=None, now=time.time):
        self.home = Path(home).expanduser().resolve()
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.home, 0o700)
        self.now = now
        self.config = dict(DEFAULTS)
        if (self.home / 'config.json').exists():
            self.config.update(json.loads((self.home / 'config.json').read_text()))
        self.backend = backend or Backend(self.home, self.config)

    @contextlib.contextmanager
    def locked(self):
        with (self.home / 'registry.lock').open('a') as lock:
            os.chmod(self.home / 'registry.lock', 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.home / 'registry.json'
            state = json.loads(path.read_text()) if path.exists() else {'sessions': {}, 'devices': {}}
            yield state
            # Keep bounded diagnostic history, preserving pending cleanup.
            ended = sorted((s for s in state['sessions'].values() if s['status'] not in ACTIVE),
                           key=lambda s: s.get('stopped_at', 0), reverse=True)
            for session in ended[100:]:
                state['sessions'].pop(session['id'], None)
            atomic_json(path, state)

    def read(self, session_id):
        with self.locked() as state:
            if session_id not in state['sessions']:
                raise SessionError(f'Unknown session: {session_id}')
            return dict(state['sessions'][session_id])

    def update(self, session_id, **fields):
        with self.locked() as state:
            session = state['sessions'][session_id]
            if session['status'] not in {'starting', 'running'}:
                raise SessionError('Session has stopped')
            if fields.get('status') == 'running' and session['deadline'] <= self.now():
                raise SessionError('Session lifetime expired during startup')
            session.update(fields)

    def status(self):
        with self.locked() as state:
            now = self.now()
            return [self.public(s, now) for s in state['sessions'].values()]

    def public(self, session, now=None):
        now = self.now() if now is None else now
        keys = ('id', 'project', 'device', 'managed', 'status', 'url', 'created_at',
                'deadline', 'last_error', 'stop_reason', 'stopped_at', 'gateway_port')
        result = {key: session[key] for key in keys if key in session}
        result['operation_leases'] = sum(end > now for end in session.get('leases', {}).values())
        result['viewer_connected'] = session.get('viewer_until', 0) > now
        return result

    def allocate(self, project, device=None, template=None, local=False, max_age=None):
        if bool(device) == bool(template):
            raise SessionError('Choose exactly one of --device or --template')
        chosen = device or template
        if not UDID.fullmatch(chosen):
            raise SessionError('Use an explicit simulator UDID')
        project = str(Path(project).expanduser().resolve(strict=True))
        if not Path(project).is_dir():
            raise SessionError('Project must be a directory')
        self.reap()
        devices = self.backend.devices()
        if chosen not in devices or devices[chosen].get('isAvailable') is False:
            raise SessionError('Simulator is unavailable in the selected Xcode')
        if device and devices[device]['state'] != 'Booted':
            raise SessionError('Borrowed simulators must already be booted; use --template for managed boot')
        hostname = None if local else self.backend.hostname()
        routes = {} if local else self.backend.routes()
        with self.locked() as state:
            active = [s for s in state['sessions'].values() if s['status'] in ACTIVE]
            for session in active:
                same = (session['project'] == project and session['local'] == local and
                        session.get('template') == template and
                        (not device or session.get('device') == device))
                if same and session['status'] in {'starting', 'running'}:
                    session['last_activity'] = self.now()
                    return dict(session), True
            if len(active) >= self.config['max_sessions']:
                raise SessionError('All simulator slots are busy; stop a session or wait for expiry')
            if device and any(s.get('device') == device for s in active):
                raise SessionError('Simulator is reserved by another project/session')
            if device and device in state['devices']:
                raise SessionError('This device is manager-owned; acquire it through --template')
            # Retained devices are reused only by their original project/template.
            retained = next((d for d in state['devices'].values()
                             if template and d['project'] == project and d['template'] == template
                             and d['udid'] in devices and devices[d['udid']]['name'] == d['name']
                             and devices[d['udid']]['state'] == 'Shutdown'
                             and all(s.get('device') != d['udid'] for s in active)), None)
            sid = secrets.token_hex(16)
            now = self.now()
            used = {s['gateway_port'] for s in active} | {s['backend_port'] for s in active}
            def port():
                for _ in range(100):
                    with socket.socket() as sock:
                        sock.bind(('127.0.0.1', 0))
                        candidate = sock.getsockname()[1]
                    if candidate not in used:
                        used.add(candidate)
                        return candidate
                raise SessionError('Could not reserve local ports')
            remote_used = {s.get('remote_port') for s in active}
            remote_used.update(int(p) for p in routes.get('TCP', {}) if str(p).isdigit())
            remote_used.update(int(k.rsplit(':', 1)[-1]) for k in routes.get('Web', {})
                               if k.rsplit(':', 1)[-1].isdigit())
            remote_port = next((p for p in range(8443, 8543) if p not in remote_used), None)
            if not local and remote_port is None:
                raise SessionError('No free Tailscale HTTPS port')
            age = self.config['max_age'] if max_age is None else max_age
            if not math.isfinite(age) or age <= 0:
                raise SessionError('Maximum lifetime must be positive finite seconds')
            session = {'id': sid, 'project': project, 'device': retained['udid'] if retained else device,
                       'device_name': retained['name'] if retained else f'sbs-remote-{sid}',
                       'managed': bool(template), 'template': template, 'local': local,
                       'template_info': devices[chosen], 'status': 'starting',
                       'created_at': now, 'deadline': now + age,
                       'startup_deadline': now + self.config['startup_seconds'],
                       'last_activity': now, 'viewer_until': 0, 'leases': {},
                       'gateway_port': port(), 'backend_port': port(), 'remote_port': remote_port,
                       'hostname': hostname, 'token': secrets.token_urlsafe(32),
                       'runtime': self.config.get('runtime', str(ROOT)),
                       'worker': None, 'backend': None, 'route_pending': False}
            base = (f'http://127.0.0.1:{session["gateway_port"]}' if local else
                    f'https://{hostname}:{remote_port}')
            session['url'] = f'{base}/#token={session["token"]}'
            state['sessions'][sid] = session
            return dict(session), False

    def start(self, project, **kwargs):
        session, reused = self.allocate(project, **kwargs)
        sid = session['id']
        if not reused:
            try:
                with self.locked() as state:
                    process = subprocess.Popen([sys.executable, str(Path(session['runtime']) / 'remote_simulator.py'),
                                                '--state-dir', str(self.home), 'worker', '--session', sid],
                                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                               stderr=subprocess.DEVNULL, start_new_session=True)
                    state['sessions'][sid]['worker'] = process_record(process)
                    # Retain/reap our direct child while this CLI remains alive.
                    # After CLI exit launchd adopts it; no conversation keepalive.
                    threading.Thread(target=process.wait, daemon=True).start()
            except BaseException:
                self.stop(sid, 'worker launch failed')
                raise
        end = time.monotonic() + self.config['startup_seconds']
        try:
            while time.monotonic() < end:
                session = self.read(sid)
                if session['status'] == 'running':
                    return self.public(session)
                if session['status'] != 'starting':
                    raise SessionError(session.get('last_error') or 'Session failed to start')
                time.sleep(0.25)
            raise SessionError('Simulator startup timed out')
        except BaseException:
            if not reused:
                self.stop(sid, 'start interrupted or failed')
            raise

    def hold(self, sid, seconds=120):
        if not math.isfinite(seconds) or not 0 < seconds <= 86400:
            raise SessionError('Lease duration must be finite and between 0 and 86400 seconds')
        with self.locked() as state:
            session = state['sessions'][sid]
            if session['status'] != 'running' or self.now() >= session['deadline']:
                raise SessionError('Session is not running')
            lease = secrets.token_hex(16)
            session['leases'] = {key: end for key, end in session['leases'].items() if end > self.now()}
            session['leases'][lease] = min(self.now() + seconds, session['deadline'])
            return {'session': sid, 'lease': lease, 'expires_at': session['leases'][lease]}

    def renew(self, sid, lease, seconds=120):
        with self.locked() as state:
            session = state['sessions'][sid]
            if (session['status'] != 'running' or session['deadline'] <= self.now() or
                    session['leases'].get(lease, 0) <= self.now()):
                raise SessionError('Operation lease expired')
            session['leases'][lease] = min(self.now() + seconds, session['deadline'])

    def release(self, sid, lease):
        with self.locked() as state:
            session = state['sessions'][sid]
            if lease in session.get('leases', {}):
                session['leases'].pop(lease)
                session['last_activity'] = self.now()

    def heartbeat(self, sid):
        with self.locked() as state:
            session = state['sessions'][sid]
            if session['status'] != 'running' or self.now() >= session['deadline']:
                raise SessionError('Session expired')
            session['viewer_until'] = min(self.now() + self.config['viewer_seconds'], session['deadline'])
            return {'project': Path(session['project']).name, 'deadline': session['deadline']}

    def stop(self, sid, reason='explicit stop', exclude_pid=None):
        # Keeping the lock through cleanup makes stop/reap/start mutually exclusive.
        with self.locked() as state:
            session = state['sessions'].get(sid)
            if not session or session['status'] not in ACTIVE:
                return {'session': sid, 'status': 'stopped'}
            session.update(status='stopping', stop_reason=reason, leases={}, viewer_until=0)
            session.pop('token', None)
            session.pop('url', None)
            # Journal before effects so a killed reaper can safely retry.
            atomic_json(self.home / 'registry.json', state)
            errors = []
            # Fence the worker before discovering unjournaled children. All spawn,
            # create, boot, and route mutations also acquire this registry lock.
            worker_record = session.get('worker')
            if worker_record and worker_record['pid'] != exclude_pid:
                try:
                    terminate(worker_record)
                except Exception as error:
                    errors.append(str(error))
            records = [session.get('backend')]
            try:
                records += self.backend.discover_processes(session)
            except Exception as error:
                errors.append(str(error))
            for record in records:
                if record and record['pid'] != exclude_pid:
                    try:
                        terminate(record)
                    except Exception as error:
                        errors.append(str(error))
            if session.get('route_pending'):
                try:
                    self.backend.remove_route(session['remote_port'],
                                              f'http://127.0.0.1:{session["gateway_port"]}')
                    session['route_pending'] = False
                except Exception as error:
                    errors.append(str(error))
            if session['managed']:
                try:
                    devices = self.backend.devices()
                    # Recover a device created immediately before a worker crash.
                    if not session['device']:
                        found = next((d for d in devices.values() if d['name'] == session['device_name']), None)
                        session['device'] = found['udid'] if found else None
                        if not found and session.get('creation_pending') and self.now() < session['startup_deadline']:
                            raise SessionError('Waiting for interrupted simulator creation to resolve')
                    device = devices.get(session['device'])
                    if device:
                        if device['name'] != session['device_name']:
                            raise SessionError('Managed simulator was renamed; left untouched')
                        self.backend.shutdown(session['device'])
                        state['devices'][session['device']] = {
                            'udid': session['device'], 'name': session['device_name'],
                            'project': session['project'], 'template': session['template'],
                            'last_used': self.now()}
                except Exception as error:
                    errors.append(str(error))
            if errors:
                session.update(status='cleanup_failed', last_error='; '.join(errors)[-2000:])
            else:
                session.update(status='stopped', stopped_at=self.now())
            return self.public(session)

    def reap(self):
        with self.locked() as state:
            reasons = []
            for session in state['sessions'].values():
                reason = expiry_reason(session, self.now(), self.config)
                if session['status'] == 'running' and not reason:
                    if not process_alive(session.get('worker')) or not process_alive(session.get('backend')):
                        reason = 'worker or streaming process exited'
                if reason:
                    reasons.append((session['id'], reason))
        result = [self.stop(sid, reason) for sid, reason in reasons]
        self.prune(self.config['retention_days'])
        return result

    def prune(self, days=7):
        if not math.isfinite(days) or days < 0:
            raise SessionError('Retention days must be finite and non-negative')
        with self.locked() as state:
            cutoff = self.now() - days * 86400
            sessions_dir = self.home / 'sessions'
            if sessions_dir.exists():
                for path in sessions_dir.iterdir():
                    if not SESSION.fullmatch(path.name) or path.is_symlink() or not path.is_dir():
                        continue
                    session = state['sessions'].get(path.name, {})
                    if session.get('status') in ACTIVE:
                        continue
                    if session.get('stopped_at', path.stat().st_mtime) <= cutoff:
                        shutil.rmtree(path)
            candidates = [d for d in state['devices'].values() if d['last_used'] <= cutoff and
                          all(s.get('device') != d['udid'] or s['status'] not in ACTIVE
                              for s in state['sessions'].values())]
            if not candidates:
                return []
            devices = self.backend.devices()
            deleted = []
            for device in candidates:
                actual = devices.get(device['udid'])
                if actual and (actual['name'] != device['name'] or actual['state'] != 'Shutdown'):
                    continue  # Someone else is using or has repurposed this simulator.
                if actual:
                    self.backend.delete(device['udid'])
                state['devices'].pop(device['udid'])
                deleted.append(device['udid'])
            return deleted


class Gateway(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = 16

    def __init__(self, manager, session):
        self.manager, self.session = manager, session
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(('127.0.0.1', session['gateway_port']), GatewayHandler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class GatewayHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    rbufsize = 0

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *_args):
        pass  # Never log bearer URLs, cookies, or request headers.

    def handle(self):
        try:
            super().handle()
        except (ConnectionError, TimeoutError):
            self.close_connection = True

    def reply(self, status, body=b'', mime='text/plain', headers=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        websocket_origin = self.origin().replace('https://', 'wss://').replace('http://', 'ws://')
        self.send_header('Content-Security-Policy',
                         "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                         f"connect-src 'self' {websocket_origin}; img-src 'self'; "
                         "frame-ancestors 'none'; base-uri 'none'")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    @property
    def session(self):
        return self.server.session

    def origin(self):
        if self.session['local']:
            return f'http://127.0.0.1:{self.session["gateway_port"]}'
        return f'https://{self.session["hostname"]}:{self.session["remote_port"]}'

    def authorized(self):
        cookie = http.cookies.SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            value = cookie.get('sbs_' + self.session['id'])
            return bool(value and hmac.compare_digest(value.value, self.session['token']))
        except (http.cookies.CookieError, TypeError):
            return False

    def do_GET(self):
        self.handle_route()

    def do_POST(self):
        self.handle_route()

    def handle_route(self):
        try:
            path = urlsplit(self.path).path
            # Reject DNS rebinding and cross-site writes/WebSocket handshakes.
            if self.headers.get('Host') != urlsplit(self.origin()).netloc:
                self.close_connection = True
                self.reply(403, 'Unexpected host')
                return
            if (self.command == 'POST' or self.headers.get('Upgrade')) and self.headers.get('Origin') != self.origin():
                self.close_connection = True
                self.reply(403, 'Same-origin request required')
                return
            if self.command == 'GET' and path in {'/', '/viewer.js'}:
                asset = 'viewer.html' if path == '/' else 'viewer.js'
                self.reply(200, (Path(self.session['runtime']) / asset).read_bytes(),
                           'text/html; charset=utf-8' if path == '/' else 'text/javascript; charset=utf-8')
                return
            current = self.server.manager.read(self.session['id'])
            if current['status'] != 'running' or current['deadline'] <= self.server.manager.now():
                self.reply(410, 'Session ended')
                return
            if self.command == 'POST' and path == '/auth':
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096 or self.headers.get('Transfer-Encoding'):
                    self.close_connection = True
                    self.reply(400, 'Invalid auth request')
                    return
                payload = json.loads(self.rfile.read(length))
                token = payload.get('token', '')
                if not isinstance(token, str) or not hmac.compare_digest(token, self.session['token']):
                    self.reply(403, 'Invalid token')
                    return
                secure = '' if self.session['local'] else '; Secure'
                self.reply(204, headers={'Set-Cookie': f'sbs_{self.session["id"]}={token}; '
                           f'Path=/; HttpOnly; SameSite=Strict{secure}'})
                return
            if not self.authorized():
                self.close_connection = True
                self.reply(401, 'Open the complete session link supplied by Codex')
                return
            if self.command == 'POST' and path == '/heartbeat':
                self.close_connection = True
                data = self.server.manager.heartbeat(self.session['id'])
                self.reply(200, json.dumps(data), 'application/json')
            elif self.command == 'GET' and path == '/health':
                self.reply(200, json.dumps({'session': self.session['id'], 'device': self.session['device']}),
                           'application/json')
            elif self.command == 'GET' and path == '/stream':
                self.proxy_stream()
            elif self.command == 'GET' and path == '/input' and self.headers.get('Upgrade', '').lower() == 'websocket':
                self.proxy_input()
            else:
                self.reply(404, 'Unknown route')
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except (SessionError, KeyError):
            self.reply(410, 'Session ended')
        except (ValueError, TypeError):
            self.reply(400, 'Invalid request')
        except OSError:
            self.close_connection = True

    def proxy_stream(self):
        upstream = http.client.HTTPConnection('127.0.0.1', self.session['backend_port'], timeout=30)
        try:
            upstream.request('GET', f'/helper/{self.session["device"]}/stream.mjpeg')
            response = upstream.getresponse()
            self.send_response(response.status)
            self.send_header('Content-Type', response.getheader('Content-Type', 'application/octet-stream'))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            # Bound transient buffering even for arbitrarily long streams.
            while chunk := response.read1(64 * 1024):
                self.wfile.write(chunk)
                self.wfile.flush()
        finally:
            upstream.close()

    def proxy_input(self):
        key = self.headers.get('Sec-WebSocket-Key', '')
        if not re.fullmatch(r'[A-Za-z0-9+/]{22}==', key):
            self.reply(400, 'Invalid WebSocket key')
            return
        with socket.create_connection(('127.0.0.1', self.session['backend_port']), timeout=10) as upstream:
            headers = (f'GET /helper/{self.session["device"]}/ws HTTP/1.1\r\n'
                       f'Host: 127.0.0.1:{self.session["backend_port"]}\r\n'
                       'Upgrade: websocket\r\nConnection: Upgrade\r\n'
                       f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n')
            upstream.sendall(headers.encode())
            response = bytearray()
            while not response.endswith(b'\r\n\r\n') and len(response) < 8192:
                byte = upstream.recv(1)
                if not byte:
                    raise ConnectionResetError()
                response.extend(byte)
            if not response.startswith(b'HTTP/1.1 101 '):
                self.reply(502, 'Simulator input unavailable')
                return
            self.wfile.write(response)
            self.wfile.flush()
            self.close_connection = True
            upstream.settimeout(15)
            while True:
                ready, _, _ = select.select([upstream, self.connection], [], [], 15)
                if not ready:
                    continue
                for source in ready:
                    data = source.recv(64 * 1024)
                    if not data:
                        return
                    destination = self.connection if source is upstream else upstream
                    destination.sendall(data)


def drain_log(pipe, path):
    """At most two 1 MiB diagnostic files per active session."""
    with path.open('ab') as stream:
        os.chmod(path, 0o600)
        while chunk := pipe.read(4096):
            if stream.tell() + len(chunk) > 1024 * 1024:
                stream.close()
                os.replace(path, path.with_suffix('.previous.log'))
                stream = path.open('wb')
                os.chmod(path, 0o600)
            stream.write(chunk)
            stream.flush()
        stream.close()


def worker(manager, sid):
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    server = None
    backend_process = None
    try:
        session = manager.read(sid)
        if session['status'] != 'starting':
            return
        if session['managed']:
            with manager.locked() as state:
                current = state['sessions'][sid]
                if current['status'] != 'starting':
                    return
                if current['deadline'] <= manager.now():
                    raise SessionError('Session lifetime expired during startup')
                if not current['device']:
                    current['creation_pending'] = True
                    atomic_json(manager.home / 'registry.json', state)
                    current['device'] = manager.backend.create(current['device_name'], current['template_info'])
                    current['creation_pending'] = False
                session['device'] = current['device']
                manager.backend.request_boot(session['device'])
            manager.backend.wait_boot(session['device'])
        server = Gateway(manager, session)
        session_dir = manager.home / 'sessions' / sid
        session_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        env = dict(os.environ, TMPDIR=str(session_dir))
        serve_entry = manager.home / 'deps/node_modules/serve-sim/dist/serve-sim.js'
        node = manager.config.get('node') or shutil.which('node')
        if not node or not serve_entry.exists():
            raise SessionError('Run setup to install the pinned simulator runtime')
        with manager.locked() as state:
            if state['sessions'][sid]['status'] != 'starting':
                return
            if state['sessions'][sid]['deadline'] <= manager.now():
                raise SessionError('Session lifetime expired during startup')
            backend_process = subprocess.Popen([node, str(Path(session['runtime']) / 'serve_runner.mjs'), '--session', sid,
                                                '--serve', str(serve_entry), '--device', session['device'],
                                                '--port', str(session['backend_port'])],
                                               env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                               stderr=subprocess.STDOUT, start_new_session=True)
            state['sessions'][sid]['backend'] = process_record(backend_process)
        threading.Thread(target=drain_log, args=(backend_process.stdout, session_dir / 'serve.log'), daemon=True).start()
        end = time.monotonic() + 45
        while time.monotonic() < end:
            if stopping.is_set() or backend_process.poll() is not None:
                raise SessionError('Simulator stream exited during startup; inspect session serve.log')
            connection = http.client.HTTPConnection('127.0.0.1', session['backend_port'], timeout=2)
            try:
                connection.request('GET', f'/helper/{session["device"]}/health')
                if connection.getresponse().status == 200:
                    break
            except OSError:
                pass
            finally:
                connection.close()
            stopping.wait(0.25)
        else:
            raise SessionError('Simulator capture did not become healthy')
        if not session['local']:
            with manager.locked() as state:
                if state['sessions'][sid]['status'] != 'starting':
                    return
                if state['sessions'][sid]['deadline'] <= manager.now():
                    raise SessionError('Session lifetime expired during startup')
                state['sessions'][sid]['route_pending'] = True
                atomic_json(manager.home / 'registry.json', state)
                manager.backend.add_route(session['remote_port'], f'http://127.0.0.1:{session["gateway_port"]}')
        manager.update(sid, status='running', last_activity=manager.now())
        threading.Thread(target=server.serve_forever, daemon=True).start()
        while not stopping.wait(5):
            current = manager.read(sid)
            reason = expiry_reason(current, manager.now(), manager.config)
            if reason or backend_process.poll() is not None or current['status'] != 'running':
                break
    except Exception as error:
        try:
            manager.update(sid, last_error=str(error)[-1500:])
        except SessionError:
            pass
    finally:
        if server:
            # Do not call shutdown if serve_forever never started.
            server.server_close()
        manager.stop(sid, 'worker finished or lease expired', exclude_pid=os.getpid())
        if backend_process:
            try:
                backend_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


def operation(manager, sid, argv):
    if argv and argv[0] == '--':
        argv = argv[1:]
    if not argv:
        raise SessionError('Supply a command after --')
    lease = manager.hold(sid)['lease']
    child = None
    previous = {}
    interrupted = threading.Event()
    try:
        child = subprocess.Popen(argv, start_new_session=True)
        record = process_record(child)
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, lambda *_: interrupted.set())
        while True:
            if interrupted.is_set():
                terminate(record)
                return 130
            try:
                return child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                try:
                    manager.renew(sid, lease)
                except SessionError:
                    terminate(record)
                    raise
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if child and child.poll() is None:
            terminate(process_record(child))
        manager.release(sid, lease)


def setup(manager, args):
    if sys.platform != 'darwin':
        raise SessionError('Simulator setup requires macOS')
    with manager.locked() as state:
        if any(s['status'] in ACTIVE for s in state['sessions'].values()):
            raise SessionError('Stop active sessions before updating the runtime')
        node, npm = shutil.which('node'), shutil.which('npm')
        if not node or not npm:
            raise SessionError('Install a maintained Node.js LTS release and npm first')
        if int(command([node, '--version']).stdout.strip().lstrip('v').split('.')[0]) < 20:
            raise SessionError('Node.js 20 or newer is required')
        # Cache runtime code outside versioned plugin directories for launchd stability.
        runtime = manager.home / 'runtime'
        runtime.mkdir(exist_ok=True, mode=0o700)
        for name in ('remote_simulator.py', 'serve_runner.mjs', 'viewer.html', 'viewer.js'):
            target = runtime / name
            if (ROOT / name).resolve() != target.resolve():
                temp = target.with_suffix('.new')
                shutil.copyfile(ROOT / name, temp)
                os.replace(temp, target)
        config = dict(manager.config, node=node, tailscale=shutil.which('tailscale'), runtime=str(runtime),
                      max_sessions=args.max_sessions, idle_seconds=args.idle_seconds,
                      max_age=args.max_age, retention_days=args.retention_days)
        deps = manager.home / 'deps'
        deps.mkdir(exist_ok=True, mode=0o700)
        installed = deps / 'node_modules/serve-sim/package.json'
        if not installed.exists() or json.loads(installed.read_text())['version'] != VERSION:
            command([npm, 'install', '--prefix', str(deps), '--save-exact', '--ignore-scripts',
                     '--no-audit', '--no-fund', f'serve-sim@{VERSION}'], timeout=180)
        atomic_json(manager.home / 'config.json', config)
        log = manager.home / 'supervisor.log'
        # Reap writes only when cleanup changes occur; rotate on every sweep.
        framework_python = Path(sys.base_prefix) / 'Resources/Python.app/Contents/MacOS/Python'
        interpreter = str(framework_python) if framework_python.exists() else sys.executable
        # A direct Homebrew framework launcher stalled in launchd's startup path
        # on macOS. The system launcher plus an explicit user home avoids that
        # bootstrap dependency; keep these settings covered by the setup test.
        plist = {'Label': LABEL, 'ProgramArguments': ['/usr/bin/env', interpreter,
                 str(runtime / 'remote_simulator.py'), '--state-dir', str(manager.home), 'reap'],
                 'RunAtLoad': True, 'StartInterval': 30,
                 'EnvironmentVariables': {'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                                          'HOME': str(Path.home()), 'CFFIXED_USER_HOME': str(Path.home())},
                 'StandardOutPath': str(log), 'StandardErrorPath': str(log)}
        destination = Path.home() / 'Library/LaunchAgents' / f'{LABEL}.plist'
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('wb') as stream:
            plistlib.dump(plist, stream)
        command(['launchctl', 'bootout', f'gui/{os.getuid()}/{LABEL}'], check=False)
        command(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(destination)])
        return {'runtime': str(runtime), 'supervisor': LABEL, 'config': config,
                'command': f'{sys.executable} {runtime / "remote_simulator.py"}'}


def positive(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError('Use positive finite seconds')
    return value


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--state-dir', type=Path, default=DEFAULT_HOME,
                     help='Per-user state/runtime directory (normally leave unchanged)')
    commands = cli.add_subparsers(dest='command', required=True)
    install = commands.add_parser('setup', help='Install pinned runtime and per-user LaunchAgent')
    install.add_argument('--max-sessions', type=int, default=2)
    install.add_argument('--idle-seconds', type=positive, default=600)
    install.add_argument('--max-age', type=positive, default=28800)
    install.add_argument('--retention-days', type=positive, default=7)
    start = commands.add_parser('start', help='Acquire and stream one isolated simulator session')
    start.add_argument('--project', type=Path, default=Path.cwd())
    choice = start.add_mutually_exclusive_group(required=True)
    choice.add_argument('--template', help='Create/reuse a managed simulator matching this UDID')
    choice.add_argument('--device', help='Borrow this already-booted simulator; never shut it down')
    start.add_argument('--local', action='store_true', help='Loopback preview without a Tailscale route')
    start.add_argument('--max-age', type=positive, help='Absolute lifetime in seconds, including held leases')
    commands.add_parser('status', help='Show sessions and lease state as JSON')
    stop = commands.add_parser('stop', help='End one session and clean up its resources')
    stop.add_argument('--session', required=True)
    hold = commands.add_parser('hold', help='Acquire a bounded operation lease')
    hold.add_argument('--session', required=True)
    hold.add_argument('--seconds', type=positive, default=120)
    release = commands.add_parser('release', help='Release one operation lease')
    release.add_argument('--session', required=True)
    release.add_argument('--lease', required=True)
    run = commands.add_parser('run', help='Keep a session leased while a build/test command runs')
    run.add_argument('--session', required=True)
    run.add_argument('argv', nargs=argparse.REMAINDER)
    prune = commands.add_parser('prune', help='Delete unused, shut-down managed devices after N days')
    prune.add_argument('--days', type=float, default=7)
    commands.add_parser('reap', help='Sweep expired/abandoned sessions (LaunchAgent entrypoint)')
    worker_cli = commands.add_parser('worker', help='Internal session worker, launched by start')
    worker_cli.add_argument('--session', required=True)
    return cli


def main():
    args = parser().parse_args()
    manager = Manager(args.state_dir)
    if hasattr(args, 'session') and not SESSION.fullmatch(args.session):
        raise SessionError('Invalid session ID')
    if args.command == 'setup':
        if not 1 <= args.max_sessions <= 16:
            raise SessionError('Choose between 1 and 16 concurrent sessions')
        result = setup(manager, args)
    elif args.command == 'start':
        if sys.platform != 'darwin':
            raise SessionError('Apple simulators require macOS')
        # Setup is required: relying only on a conversation/terminal exit trap leaks devices.
        if not (manager.home / 'config.json').exists():
            raise SessionError('Run setup before starting simulator sessions')
        watchdog = Path.home() / 'Library/LaunchAgents' / f'{LABEL}.plist'
        if not watchdog.exists() or str(manager.home) not in plistlib.loads(watchdog.read_bytes()).get('ProgramArguments', []):
            raise SessionError('Run setup to register the cleanup supervisor for this state directory')
        command(['launchctl', 'print', f'gui/{os.getuid()}/{LABEL}'])
        result = manager.start(args.project, device=args.device, template=args.template,
                               local=args.local, max_age=args.max_age)
    elif args.command == 'status':
        result = manager.status()
    elif args.command == 'stop':
        result = manager.stop(args.session)
    elif args.command == 'hold':
        result = manager.hold(args.session, args.seconds)
    elif args.command == 'release':
        manager.release(args.session, args.lease)
        result = {'released': args.lease}
    elif args.command == 'run':
        return operation(manager, args.session, args.argv)
    elif args.command == 'prune':
        result = {'deleted': manager.prune(args.days)}
    elif args.command == 'worker':
        worker(manager, args.session)
        return 0
    else:
        log = manager.home / 'supervisor.log'
        if log.exists() and log.stat().st_size > 1024 * 1024:
            with log.open('w'):
                pass
        result = manager.reap()
        if not result:
            return 0
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    os.umask(0o077)
    try:
        sys.exit(main())
    except (SessionError, subprocess.TimeoutExpired, KeyError, OSError) as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr)
        sys.exit(1)
