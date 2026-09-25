"""Local-only continuous Marvin sensor runtime and JSON API."""

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import itertools
import os
from pathlib import Path
import queue
import signal
import stat
from threading import Condition, Event, Lock, RLock, Thread, current_thread, main_thread
import time
from typing import Protocol

import marvin_sensors
import marvin_dashboard
import marvin_managers
from tools.marvin_legacy_client import LegacyClient, Limits


DEFAULT_POLL_SECONDS = 2.0
MIN_POLL_SECONDS = 0.5
MAX_POLL_SECONDS = 60.0
DEFAULT_CHUNK_SECONDS = 300
MIN_CHUNK_SECONDS = 10
MAX_CHUNK_SECONDS = 3600
MAX_BODY_BYTES = 4096
MAX_REQUESTS = 65536
STARTUP_SECONDS = 30.0


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


class SensorSource(Protocol):
    def start(self) -> None: ...
    def read(self) -> dict: ...
    def close(self) -> None: ...


class RuntimeManager(Protocol):
    def start(self) -> None: ...
    def status(self) -> dict: ...
    def close(self) -> None: ...


class ActionRuntimeManager(RuntimeManager, Protocol):
    def action(self, name: str, values: dict) -> dict: ...
    def tick(self, now: float) -> bool: ...


def managers_for_owner(owner, *, microphone=False, camera=False):
    """Bind accepted controller callbacks to the same owner as sensor polling."""
    required = ("drive_step", "stop", "read_led_state", "write_led_state")
    missing = [name for name in required if not callable(getattr(owner, name, None))]
    if missing:
        raise ValueError(
            "Controller owner is missing accepted callbacks: " + ", ".join(missing))
    managers = {
        "drive": marvin_managers.DriveManager(owner.drive_step, owner.stop),
        "leds": marvin_managers.LedManager(
            owner.read_led_state, owner.write_led_state),
    }
    if microphone:
        managers["microphone"] = marvin_managers.MicrophoneManager()
    if camera:
        managers["camera"] = marvin_managers.CameraManager()
    return managers


class PersistentSensorSource:
    """Repeated exact four-getter snapshots over one injected transport."""

    def __init__(self, transport, *, ownership_key, expected_identity,
                 session_timeout=86400, startup_timeout=STARTUP_SECONDS):
        if type(ownership_key) is not bytes or type(expected_identity) is not bytes:
            raise ValueError("ownership_key and expected_identity must be immutable bytes.")
        self.transport = transport
        self.ownership_key = ownership_key
        self.expected_identity = expected_identity
        self.session_timeout = session_timeout
        self.startup_timeout = startup_timeout
        self.client = None

    def start(self):
        if self.client is not None:
            raise RuntimeError("Sensor source already started.")
        self.client = LegacyClient(
            self.transport,
            ownership_key=self.ownership_key,
            expected_identity=self.expected_identity,
            session_timeout=self.session_timeout,
            startup_timeout=self.startup_timeout,
            cleanup_timeout=5,
            limits=Limits(
                max_requests=MAX_REQUESTS, max_rx_bytes=16 * 1024 * 1024,
                max_events=65536, max_reads=65536, read_size=256,
            ),
            evidence_kind="recorded",
        )
        self.client.start()

    def read(self):
        if self.client is None:
            raise RuntimeError("Sensor source is not active.")
        return marvin_sensors.read_session_snapshot(
            self.client, expected_identity=self.expected_identity)

    def close(self):
        if self.client is not None:
            client, self.client = self.client, None
            client.close()


class _Recorder:
    def __init__(self, directory, chunk_seconds):
        path = Path(directory)
        info = path.stat()
        if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
            raise ValueError("Recording destination must be an existing directory.")
        if info.st_mode & 0o077:
            raise ValueError("Recording directory must be private (no group/other permissions).")
        if info.st_uid != os.geteuid():
            raise ValueError("Recording directory must be owned by the current user.")
        self.directory = path
        self.chunk_seconds = chunk_seconds
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        self.directory_fd = os.open(path, flags)
        pinned = os.fstat(self.directory_fd)
        if (pinned.st_dev, pinned.st_ino) != (info.st_dev, info.st_ino):
            os.close(self.directory_fd)
            self.directory_fd = None
            raise OSError("Recording directory changed during validation.")
        self.stream = None
        self.path = None
        self.started = None
        self.chunk = 0
        self.rows = 0
        self.opened_at = None

    def _open(self, now):
        self.chunk += 1
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        path = self.directory / f"marvin-sensors-{stamp}-{self.chunk:04d}.jsonl"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path.name, flags, 0o600, dir_fd=self.directory_fd)
        self.stream = os.fdopen(fd, "w", encoding="ascii", buffering=1)
        self.path = path
        self.started = now
        self.opened_at = _utc_now()

    def append(self, snapshot, now):
        if self.stream is None or now - self.started >= self.chunk_seconds:
            self._close_chunk()
            self._open(now)
        row = json.dumps(
            {"recorded_at": _utc_now(), "snapshot": snapshot},
            ensure_ascii=True, allow_nan=False, separators=(",", ":"),
        )
        self.stream.write(row + "\n")
        self.rows += 1

    def status(self):
        elapsed = (
            None if self.started is None else
            max(0.0, time.monotonic() - self.started)
        )
        return {
            "active": self.directory_fd is not None,
            "chunk_open": self.stream is not None,
            "directory": str(self.directory),
            "file": None if self.path is None else self.path.name,
            "chunk": self.chunk,
            "chunk_seconds": self.chunk_seconds,
            "chunk_elapsed_seconds": elapsed,
            "rows": self.rows,
            "opened_at": self.opened_at,
        }

    def _close_chunk(self):
        if self.stream is not None:
            stream, self.stream = self.stream, None
            try:
                stream.flush()
                os.fsync(stream.fileno())
            finally:
                stream.close()

    def close(self):
        try:
            self._close_chunk()
        finally:
            if self.directory_fd is not None:
                descriptor, self.directory_fd = self.directory_fd, None
                os.close(descriptor)


class OperatorRuntime:
    """Own the source, polling, recording, and observable lifecycle."""

    def __init__(self, source: SensorSource | None, *,
                 poll_seconds=DEFAULT_POLL_SECONDS,
                 chunk_seconds=DEFAULT_CHUNK_SECONDS,
                 managers: dict[str, RuntimeManager] | None = None,
                 configuration: dict | None = None,
                 startup_timeout=None):
        if type(poll_seconds) not in (int, float) or not MIN_POLL_SECONDS <= poll_seconds <= MAX_POLL_SECONDS:
            raise ValueError(f"poll_seconds must be {MIN_POLL_SECONDS}..{MAX_POLL_SECONDS}.")
        if type(chunk_seconds) is not int or not MIN_CHUNK_SECONDS <= chunk_seconds <= MAX_CHUNK_SECONDS:
            raise ValueError(f"chunk_seconds must be {MIN_CHUNK_SECONDS}..{MAX_CHUNK_SECONDS}.")
        self.source = source
        self.managers = {} if managers is None else dict(managers)
        self.configuration = {} if configuration is None else dict(configuration)
        if any(type(name) is not str or not name.isidentifier()
               for name in self.managers):
            raise ValueError("Manager names must be identifiers.")
        self.poll_seconds = float(poll_seconds)
        self.chunk_seconds = chunk_seconds
        startup_timeout = (
            getattr(source, "startup_timeout", STARTUP_SECONDS)
            if startup_timeout is None else startup_timeout
        )
        if (type(startup_timeout) not in (int, float)
                or not 0.001 <= startup_timeout <= 120):
            raise ValueError("startup_timeout must be 0.001..120 seconds.")
        self.startup_timeout = float(startup_timeout)
        self._lock = RLock()
        self._changed = Condition(self._lock)
        self._stop = Event()
        self._commands = queue.PriorityQueue()
        self._command_sequence = itertools.count()
        self._drive_pending = Lock()
        self._thread = None
        self._recorder = None
        self._revision = 0
        self._state = "new"
        self._latest = None
        self._latest_monotonic = None
        self._last_error = None
        self._recording = {"active": False, "error": None}

    def start(self):
        with self._lock:
            if self._state != "new":
                raise RuntimeError("Runtime start requires new state.")
            self._state = "starting"
        self._thread = Thread(target=self._run, name="marvin-operator", daemon=False)
        self._thread.start()
        failure = None
        with self._changed:
            self._changed.wait_for(
                lambda: self._state != "starting", timeout=self.startup_timeout)
            if self._state == "starting":
                failure = TimeoutError("Operator runtime startup did not complete.")
            elif self._state == "failed":
                failure = RuntimeError(self._last_error)
        if failure is not None:
            self.close()
            raise failure
        return self

    def _publish(self):
        self._revision += 1
        self._changed.notify_all()

    def _run(self):
        try:
            if self.source is not None:
                self.source.start()
            for manager in self.managers.values():
                manager.start()
            with self._changed:
                self._state = "running"
                self._publish()
            deadline = 0.0
            while not self._stop.is_set():
                self._drain_commands()
                now = time.monotonic()
                changed = False
                for manager in self.managers.values():
                    tick = getattr(manager, "tick", None)
                    changed = (tick(now) if tick is not None else False) or changed
                if changed:
                    with self._changed:
                        self._publish()
                if self.source is not None and now >= deadline:
                    try:
                        snapshot = self.source.read()
                    except Exception as error:
                        with self._changed:
                            self._state = "failed"
                            self._last_error = f"{type(error).__name__}: {error}"[:1024]
                            self._publish()
                        break
                    completed = time.monotonic()
                    with self._changed:
                        self._latest = {
                            "observed_at": _utc_now(),
                            "monotonic": completed,
                            "snapshot": snapshot,
                        }
                        self._latest_monotonic = completed
                        self._publish()
                    self._record(snapshot, completed)
                    deadline = now + self.poll_seconds
                delay = 0.1 if self.source is None else max(
                    0.0, deadline - time.monotonic())
                self._stop.wait(min(0.1, delay))
        except Exception as error:
            with self._changed:
                self._state = "failed"
                self._last_error = f"{type(error).__name__}: {error}"[:1024]
                self._publish()
        finally:
            errors = []
            if self._recorder is not None:
                try:
                    self._recorder.close()
                except Exception as error:
                    errors.append(error)
                self._recorder = None
            for manager in reversed(tuple(self.managers.values())):
                try:
                    manager.close()
                except Exception as error:
                    errors.append(error)
            if self.source is not None:
                try:
                    self.source.close()
                except Exception as error:
                    errors.append(error)
            with self._changed:
                if errors:
                    self._state = "failed"
                    self._last_error = "; ".join(
                        f"{type(error).__name__}: {error}" for error in errors)[:1024]
                elif self._state != "failed":
                    self._state = "stopped"
                self._recording["active"] = False
                self._publish()

    def _drain_commands(self):
        while True:
            try:
                _priority, _sequence, command, value, result = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                if command.startswith("manager:"):
                    _, manager_name, action = command.split(":", 2)
                    try:
                        manager = self.managers[manager_name]
                    except KeyError:
                        raise ValueError("Unknown runtime manager.") from None
                    manager_action = getattr(manager, "action", None)
                    if manager_action is None:
                        raise ValueError("Runtime manager does not expose actions.")
                    response = manager_action(action, value)
                    with self._changed:
                        self._publish()
                    result.put((True, response))
                elif command == "start_recording":
                    if self.source is None:
                        raise ValueError("Recording requires a configured sensor source.")
                    if self._recorder is not None:
                        raise ValueError("Recording is already active.")
                    self._recorder = _Recorder(value, self.chunk_seconds)
                    self._recording = {**self._recorder.status(), "error": None}
                else:
                    if self._recorder is None:
                        raise ValueError("Recording is not active.")
                    self._recorder.close()
                    self._recording = {**self._recorder.status(), "active": False, "error": None}
                    self._recorder = None
                with self._changed:
                    self._publish()
                result.put((True, self._recording))
            except Exception as error:
                if command.startswith("manager:"):
                    with self._changed:
                        self._publish()
                result.put((False, error))

    def _record(self, snapshot, now):
        if self._recorder is None:
            return
        try:
            self._recorder.append(snapshot, now)
            with self._changed:
                self._recording = {**self._recorder.status(), "error": None}
                self._publish()
        except Exception as error:
            try:
                self._recorder.close()
            except Exception as close_error:
                error.add_note(str(close_error))
            self._recorder = None
            with self._changed:
                self._recording = {
                    "active": False,
                    "error": f"{type(error).__name__}: {error}"[:1024],
                }
                self._publish()

    def recording(self, command, directory=None):
        if command not in ("start_recording", "stop_recording"):
            raise ValueError("Unknown recording command.")
        configured = self.configuration.get("evidence_root")
        if command == "start_recording" and configured is not None:
            if Path(directory).resolve() != Path(configured).resolve():
                raise ValueError("Recording must use the configured private evidence root.")
        result = queue.Queue(maxsize=1)
        self._commands.put((10, next(self._command_sequence), command, directory, result))
        try:
            success, value = result.get(timeout=5)
        except queue.Empty as error:
            raise TimeoutError("Recording command timed out.") from error
        if not success:
            raise value
        return value

    def manager_action(self, manager, action, values):
        if type(manager) is not str or type(action) is not str or type(values) is not dict:
            raise ValueError("Manager action requires names and an object payload.")
        if self._state != "running":
            raise RuntimeError("Operator runtime is not running.")
        if manager in ("microphone", "camera") and action in ("start", "capture"):
            directory = self.configuration.get("media_directory")
            expected_path = self.configuration.get(
                "microphone_usb_path" if manager == "microphone"
                else "camera_usb_path")
            if directory is None or expected_path is None:
                raise ValueError(f"{manager} route is not configured.")
            output = Path(values.get("output", "")).resolve()
            if output.parent != Path(directory).resolve():
                raise ValueError("Media output must be directly inside the configured private directory.")
            if values.get("usb_path") != expected_path:
                raise ValueError("Media request does not match the configured exact USB path.")
        movement = manager == "drive" and (
            action.startswith("heartbeat_") or action.startswith("fixed_"))
        if movement and not self._drive_pending.acquire(blocking=False):
            raise ValueError("A bounded drive action is already pending; no movement backlog is allowed.")
        result = queue.Queue(maxsize=1)
        priority = 0 if action == "stop" else 1 if action == "release" else 10
        try:
            try:
                drive_manager = self.managers["drive"] if manager == "drive" else None
            except KeyError:
                raise ValueError("Drive manager is not configured.") from None
            if manager == "drive" and action == "stop":
                values = {"_stop_generation": drive_manager.request_stop()}
            elif movement:
                values = {**values,
                          "_stop_generation": drive_manager.movement_token()}
            self._commands.put((
                priority, next(self._command_sequence),
                f"manager:{manager}:{action}", values, result))
            try:
                success, value = result.get(timeout=120)
            except queue.Empty as error:
                raise TimeoutError("Manager command timed out.") from error
        finally:
            if movement:
                self._drive_pending.release()
        if not success:
            raise value
        return value

    def status(self):
        with self._lock:
            age = None if self._latest_monotonic is None else max(
                0.0, time.monotonic() - self._latest_monotonic)
            return {
                "state": self._state,
                "connection": {
                    "configured": self.source is not None,
                    "status": (
                        "connected" if self._state == "running" and self.source is not None
                        else "disabled" if self.source is None else self._state
                    ),
                },
                "poll_seconds": self.poll_seconds,
                "latest_observed_at": None if self._latest is None else self._latest["observed_at"],
                "freshness": {
                    "age_seconds": age,
                    "fresh": age is not None and age <= self.poll_seconds * 2.5,
                },
                "error": self._last_error,
                "recording": dict(self._recording),
                "managers": {
                    name: manager.status()
                    for name, manager in self.managers.items()
                },
                "configuration": dict(self.configuration),
                "revision": self._revision,
            }

    def latest(self):
        with self._lock:
            return self._latest

    def wait_event(self, revision, timeout=15):
        with self._changed:
            self._changed.wait_for(
                lambda: self._revision > revision or self._state in ("failed", "stopped"),
                timeout=timeout,
            )
            return self.status()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()


class OperatorServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, runtime):
        if address[0] != "127.0.0.1":
            raise ValueError("Operator API binds only to 127.0.0.1.")
        self.runtime = runtime
        super().__init__(address, _Handler)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        return

    def _headers(self, content_type, length):
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self'; img-src 'self'; "
            "script-src 'self'; style-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'")

    def _json(self, status, value):
        data = json.dumps(value, ensure_ascii=True, allow_nan=False).encode("ascii")
        self.send_response(status)
        self._headers("application/json", len(data))
        self.end_headers()
        self.wfile.write(data)

    def _require_loopback_host(self):
        host = self.headers.get("Host", "")
        if host.split(":", 1)[0] != "127.0.0.1":
            self._json(400, {
                "status": "failed",
                "error": "Host must be the loopback address.",
            })
            return False
        return True

    @staticmethod
    def _event_type(status):
        if status.get("state") == "failed" or status.get("error"):
            return "error"
        drive = status.get("managers", {}).get("drive", {})
        return "error" if drive.get("error") else "status"

    def do_GET(self):
        if not self._require_loopback_host():
            return
        if self.path in ("/", "/index.html", "/style.css", "/app.js"):
            value, content_type = {
                "/": (marvin_dashboard.HTML, "text/html; charset=utf-8"),
                "/index.html": (marvin_dashboard.HTML, "text/html; charset=utf-8"),
                "/style.css": (marvin_dashboard.CSS, "text/css; charset=utf-8"),
                "/app.js": (marvin_dashboard.JS, "text/javascript; charset=utf-8"),
            }[self.path]
            data = value.encode("utf-8")
            self.send_response(200)
            self._headers(content_type, len(data))
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/api/status":
            self._json(200, self.server.runtime.status())
        elif self.path == "/api/sensors/latest":
            latest = self.server.runtime.latest()
            self._json(200 if latest is not None else 503, latest or {
                "status": "unavailable", "error": "No sensor snapshot is available."})
        elif self.path == "/api/recording":
            self._json(200, self.server.runtime.status()["recording"])
        elif self.path in (
                "/api/drive", "/api/leds", "/api/media/audio",
                "/api/media/video"):
            manager = {
                "/api/drive": "drive",
                "/api/leds": "leds",
                "/api/media/audio": "microphone",
                "/api/media/video": "camera",
            }[self.path]
            managers = self.server.runtime.status()["managers"]
            self._json(
                200 if manager in managers else 404,
                managers.get(manager, {"status": "not_configured"}))
        elif self.path == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            revision = -1
            sensor_at = None
            try:
                while True:
                    status = self.server.runtime.wait_event(revision)
                    revision = status["revision"]
                    data = json.dumps(status, ensure_ascii=True, allow_nan=False)
                    event = self._event_type(status)
                    self.wfile.write(
                        f"id: {revision}\nevent: {event}\ndata: {data}\n\n".encode("ascii"))
                    latest = self.server.runtime.latest()
                    if latest is not None and latest["observed_at"] != sensor_at:
                        sensor_at = latest["observed_at"]
                        sensor = json.dumps(
                            latest, ensure_ascii=True, allow_nan=False,
                            separators=(",", ":"))
                        self.wfile.write(
                            f"event: sensor\ndata: {sensor}\n\n".encode("ascii"))
                    self.wfile.flush()
                    if status["state"] in ("failed", "stopped"):
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self._json(404, {"status": "not_found"})

    def do_POST(self):
        routes = {
            "/api/recording/start": ("recording", "start_recording"),
            "/api/recording/stop": ("recording", "stop_recording"),
            "/api/drive/acquire": ("drive", "acquire"),
            "/api/drive/release": ("drive", "release"),
            "/api/drive/stop": ("drive", "stop"),
            "/api/leds/reset": ("leds", "reset"),
            "/api/media/audio/start": ("microphone", "start"),
            "/api/media/audio/stop": ("microphone", "stop"),
            "/api/media/video/start": ("camera", "start"),
            "/api/media/video/stop": ("camera", "stop"),
            "/api/media/video/capture": ("camera", "capture"),
        }
        route = routes.get(self.path)
        if route is None:
            parts = self.path.split("/")
            if len(parts) == 5 and parts[:3] == ["", "api", "drive"]:
                mode, direction = parts[3:]
                if mode in ("heartbeat", "fixed") and direction in (
                        "forward", "backward", "rotate-left", "rotate-right"):
                    route = ("drive", f"{mode}_{direction}")
            elif len(parts) == 5 and parts[:3] == ["", "api", "leds"]:
                channel, state = parts[3:]
                if channel in (
                        "left-position-0-red", "left-position-0-blue",
                        "left-position-1-red", "left-position-1-blue",
                        "left-position-2-red", "left-position-2-blue",
                        "right-position-0-red", "right-position-0-blue",
                        "right-position-1-red", "right-position-1-blue",
                        "right-position-2-red", "right-position-2-blue",
                        "wheels", "front-left-blue", "front-right-red",
                        "bottom-green", "bottom-blue") and state in ("on", "off"):
                    route = ("leds", f"{state}_{channel.replace('-', '_')}")
        if route is None:
            self._json(404, {"status": "not_found"})
            return
        if not self._require_loopback_host():
            return
        try:
            host = self.headers.get("Host", "")
            if host.split(":", 1)[0] != "127.0.0.1":
                raise ValueError("Host must be the loopback address.")
            origin = self.headers.get("Origin")
            if origin is not None and origin != f"http://{host}":
                raise ValueError("Cross-origin requests are not accepted.")
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Content-Type must be application/json.")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= MAX_BODY_BYTES:
                raise ValueError("Request body is too large.")
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("JSON body must be an object.")
            manager, command = route
            if manager == "recording":
                allowed = {"directory"} if command == "start_recording" else set()
                if set(body) != allowed:
                    raise ValueError("Unexpected or missing JSON fields.")
                result = self.server.runtime.recording(command, body.get("directory"))
            else:
                allowed = (
                    {"lease"} if command in ("release",) or command.startswith("heartbeat_")
                    else {"output", "usb_path", "privacy_authorized"}
                    if command in ("start", "capture") else set()
                )
                if set(body) != allowed:
                    raise ValueError("Unexpected or missing JSON fields.")
                if command.startswith("heartbeat_"):
                    body["requested_at"] = time.monotonic()
                result = self.server.runtime.manager_action(manager, command, body)
        except (OSError, ValueError, TypeError, RuntimeError, TimeoutError,
                json.JSONDecodeError) as error:
            self._json(400, {"status": "failed", "error": str(error)[:1024]})
        else:
            try:
                self._json(200, {"status": "ok", manager: result})
            except (BrokenPipeError, ConnectionResetError):
                if manager == "drive" and (
                        command == "acquire" or command.startswith("heartbeat_")):
                    lease = result.get("lease") or body.get("lease")
                    if lease:
                        try:
                            self.server.runtime.manager_action(
                                "drive", "release", {"lease": lease})
                        except Exception:
                            pass
                elif manager == "leds" and command.startswith(("on_", "off_")):
                    try:
                        self.server.runtime.manager_action("leds", "reset", {})
                    except Exception:
                        pass

    def do_OPTIONS(self):
        self._json(405, {"status": "method_not_allowed"})

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS


def serve(runtime, *, port=8765, ready=None):
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("port must be an integer between 0 and 65535.")
    server = OperatorServer(("127.0.0.1", port), runtime)
    try:
        runtime.start()
    except Exception:
        try:
            runtime.close()
        finally:
            server.server_close()
        raise
    if ready is not None:
        ready(server.server_address)
    previous = {}
    try:
        if current_thread() is main_thread():
            def stop_server(_signum, _frame):
                Thread(target=server.shutdown, daemon=True).start()

            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.signal(signum, stop_server)
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        runtime.close()
        for signum, handler in previous.items():
            signal.signal(signum, handler)
