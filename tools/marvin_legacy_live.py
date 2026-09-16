"""Explicitly authorized, bounded LIVE ReadRawData collection; offline by default.

No retries, reconnect, input flush, arbitrary commands or privilege escalation.
See docs/legacy-live.md for the operator gates and host-ingress timing boundary.
"""

import argparse
from collections import deque
import ctypes
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import struct
import subprocess
import sys
import termios
from threading import current_thread
import time

from tools.marvin_legacy_client import Received, SETTINGS, SessionError
from tools.marvin_legacy_poll import CollectionError, PollPlan, collect
from tools.marvin_legacy_protocol import decode_packet, read_raw_data_request
from tools.marvin_paths import new_output_path
from tools import marvin_usbmon_binary as binary
from tools import marvin_motor_power_off_consent as consent
from tools.marvin_legacy_telemetry import interpret_packet


USB_PAYLOAD_LIMIT = 4096
MAX_USB_RECORDS = 10000
MAX_USB_BYTES = 1024 * 1024
POWERED_ZERO_FIELDS = (
    ("motorVelocityL", 72), ("motorVelocityR", 74),
    ("motorPwmLeftForward", 90), ("motorPwmLeftReverse", 92),
    ("motorPwmRightForward", 94), ("motorPwmRightReverse", 96),
)


def powered_observation_plan():
    return live_plan(max_requests=1, interval=1, duration=3, first_sequence=2304)


def inspect_powered_evidence(events, telemetry):
    failure = None
    for event in events:
        packet = event.stream.packet
        if packet is None:
            failure = "Unknown/partial response evidence."
            continue
        decoded = interpret_packet(packet, direction="received", evidence=event.evidence_kind)
        telemetry.append(decoded)
        if (event.labels != ("matched_candidate",) or decoded["status"] != "decoded"
                or packet.sequence != 2304 or packet.command != 0 or packet.response_field != 0x80
                or len(packet.payload) != 134 or len(decoded.get("fields", {})) != 82):
            failure = "Unknown/fault response shape or correlation."
            continue
        for name, offset in POWERED_ZERO_FIELDS:
            field = decoded["fields"][name]
            if (field["offset"] != offset or field["size"] != 2
                    or field["raw_hex"] != packet.payload[offset:offset + 2].hex()
                    or field["raw_hex"] != "0000" or field["signed"] != 0 or field["unsigned"] != 0):
                failure = "Nonzero or inconsistent reported velocity/PWM: " + name
    if failure:
        raise SessionError("powered_observation_failed", failure)


def _host_call(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except (termios.error, ValueError, subprocess.SubprocessError) as error:
        name = getattr(operation, "__name__", type(operation).__name__)
        raise OSError(f"{name}: {error}") from error


def _termios_evidence(settings):
    return {"fields": settings[:6],
            "control_chars": [{"bytes_hex": value.hex()} if type(value) is bytes else value
                              for value in settings[6]]}


def _validate_termios(requested, actual):
    if any(type(getattr(termios, name, None)) is not int for name in ("CBAUD", "CIBAUD")):
        raise OSError("Linux termios baud-encoding masks are unavailable.")
    mask = termios.CBAUD | termios.CIBAUD
    expected, returned = list(requested), list(actual)
    expected[2] &= ~mask
    returned[2] &= ~mask
    if actual[4:6] != [termios.B57600, termios.B57600] or returned != expected:
        raise OSError("Kernel did not retain approved non-baud flags/control characters and exact 57600 speeds.")


class _Timespec(ctypes.Structure):
    _fields_ = [("seconds", ctypes.c_long), ("nanoseconds", ctypes.c_long)]


class _Itimerspec(ctypes.Structure):
    _fields_ = [("interval", _Timespec), ("value", _Timespec)]


class IngressClock:
    """Map Linux usbmon realtime stamps to conservative monotonic intervals."""

    def __init__(self):
        self.fd = None
        self.offset = None

    def start(self):
        binary.check_abi()
        libc = ctypes.CDLL(None, use_errno=True)
        try:
            create, arm = libc.timerfd_create, libc.timerfd_settime
        except AttributeError as error:
            raise OSError("Linux timerfd clock-change guard is unavailable.") from error
        create.argtypes, create.restype = [ctypes.c_int, ctypes.c_int], ctypes.c_int
        arm.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(_Itimerspec), ctypes.c_void_p]
        arm.restype = ctypes.c_int
        fd = create(time.CLOCK_REALTIME, os.O_NONBLOCK | os.O_CLOEXEC)
        if fd < 0:
            raise OSError(ctypes.get_errno(), "timerfd clock-change guard unavailable")
        self.fd = fd
        # Absolute realtime timer + CANCEL_ON_SET detects even a jump and reversal.
        value = _Itimerspec(_Timespec(0, 0), _Timespec(time.time_ns() // 10**9 + 86400, 0))
        if arm(fd, 1 | 2, ctypes.byref(value), None) < 0:
            error = OSError(ctypes.get_errno(), "Cannot arm realtime clock-change guard")
            self.close()
            raise error
        self.check()

    def check(self):
        if self.fd is None:
            raise OSError("Ingress clock is not armed.")
        try:
            result = os.read(self.fd, 8)
        except BlockingIOError:
            pass
        else:
            raise OSError(f"Ingress clock guard expired or ended ({len(result)} bytes).")
        before = time.monotonic_ns()
        real = time.time_ns()
        after = time.monotonic_ns()
        interval = (real - after, real - before)
        if self.offset is None:
            self.offset = interval
        elif interval[0] > self.offset[1] or interval[1] < self.offset[0]:
            raise OSError("Realtime/monotonic offset changed (clock change or suspend); no continuation.")
        # Retain the original wider interval, rather than falsely tightening past stamps.
        return interval

    def bounds(self, seconds, micros):
        self.check()
        stamp = seconds * 10**9 + micros * 1000
        low, high = self.offset
        return (math.nextafter((stamp - high) / 10**9, -math.inf),
                math.nextafter((stamp + 1000 - low) / 10**9, math.inf))

    def close(self):
        if self.fd is not None:
            fd, self.fd = self.fd, None
            os.close(fd)


class UsbIngress:
    """Tail the recorder's unbuffered, target-only binary evidence, without devices."""

    def __init__(self, path, baseline, clock):
        self.path, self.baseline, self.clock = Path(path), baseline, clock
        self.stream = None
        self.pending = bytearray()
        self.started = False
        self.records = self.bytes = 0
        self.rx = deque()
        self.rx_bytes = self.rx_consumed = 0
        self.expected_tx = deque()
        self.outstanding_tx = {}
        self.completed_tx = 0
        self.last_rx_end = None
        self.line_states = []
        self.pending_in = {}

    def open(self):
        self.stream = self.path.open("rb", buffering=0)

    def pump(self):
        if self.stream is None:
            raise OSError("USB evidence reader is not open.")
        data = self.stream.read(16384)
        self.bytes += len(data)
        if self.bytes > MAX_USB_BYTES:
            raise OSError("USB evidence byte budget exceeded.")
        self.pending.extend(data)
        if not self.started:
            if len(self.pending) < len(binary.FILE_MAGIC):
                return
            if self.pending[:len(binary.FILE_MAGIC)] != binary.FILE_MAGIC:
                raise OSError("Invalid binary USB evidence header.")
            del self.pending[:len(binary.FILE_MAGIC)]
            self.started = True
        while len(self.pending) >= 2:
            length = struct.unpack_from("<H", self.pending)[0]
            if length > USB_PAYLOAD_LIMIT:
                raise OSError("USB evidence payload exceeds the approved budget.")
            size = 2 + binary.HEADER.size + length
            if len(self.pending) < size:
                break
            header = bytes(self.pending[2:2 + binary.HEADER.size])
            payload = bytes(self.pending[2 + binary.HEADER.size:size])
            del self.pending[:size]
            self.records += 1
            if self.records > MAX_USB_RECORDS:
                raise OSError("USB evidence record budget exceeded.")
            self._event(header, payload)

    def _event(self, header, payload):
        binary.to_text(header, payload, payload_limit=USB_PAYLOAD_LIMIT)
        values = binary.HEADER.unpack(header)
        urb, event, transfer, endpoint, device, bus = values[:6]
        _, data_flag, seconds, micros, status, length, captured, setup = values[6:14]
        if (bus, device) != (self.baseline["busnum"], self.baseline["devnum"]):
            raise OSError("USB evidence contains a different target.")
        if event == ord("E"):
            raise OSError("USB submission error; no continuation.")
        if event == ord("C") and status != 0 and not (length == 0 and status in (-2, -108)):
            raise OSError("USB transfer completion failed.")
        if transfer == 2 and event == ord("S") and values[6] == 0:
            request_type, request, value, _, _ = struct.unpack("<BBHHH", setup)
            if (request_type, request) == (0x21, 0x22):
                self.line_states.append(value)
        if transfer != 3:
            return
        if endpoint & 0x80:
            if event == ord("S"):
                if urb in self.pending_in or len(self.pending_in) >= 128 or status != -115:
                    raise OSError("Duplicate, excessive or failed USB IN submission.")
                self.pending_in[urb] = (endpoint, length)
                return
            submitted = self.pending_in.pop(urb, None)
            if submitted is None or submitted[0] != endpoint or length > submitted[1]:
                raise OSError("Unpaired or inconsistent USB IN completion.")
            if status != 0:
                if length == 0 and status in (-2, -108):
                    return  # Close cancellations are retained, not application replies.
                raise OSError("USB IN completion failed.")
            if not length:
                return
            if data_flag or captured != length or len(payload) != length:
                raise OSError("Incomplete USB IN payload; cannot assign tty ingress bounds.")
            start, end = self.clock.bounds(seconds, micros)
            if self.last_rx_end is not None and end < self.last_rx_end:
                raise OSError("USB IN timestamps regressed.")
            self.last_rx_end = end
            self.rx_bytes += length
            if self.rx_bytes > 8192:
                raise OSError("USB IN byte budget exceeded.")
            self.rx.append((payload, start, end))
        elif event == ord("S"):
            if (status != -115 or not self.expected_tx or payload != self.expected_tx[0]
                    or length != len(payload) or captured != length or data_flag):
                raise OSError("Unexpected, truncated or mismatched USB OUT (including possible tty echo).")
            if urb in self.outstanding_tx:
                raise OSError("Duplicate USB OUT submission.")
            self.expected_tx.popleft()
            self.outstanding_tx[urb] = (endpoint, length)
        elif event == ord("C"):
            expected = self.outstanding_tx.pop(urb, None)
            if expected != (endpoint, length) or status != 0:
                raise OSError("Missing, short or failed USB OUT completion.")
            self.completed_tx += length

    def match(self, data):
        if sum(len(item[0]) for item in self.rx) < len(data):
            return None
        remainder, starts, ends = data, [], []
        while remainder:
            payload, start, end = self.rx.popleft()
            count = min(len(payload), len(remainder))
            if payload[:count] != remainder[:count]:
                raise OSError("Full USB IN and tty bytes disagree; no continuation.")
            starts.append(start)
            ends.append(end)
            remainder = remainder[count:]
            if count < len(payload):
                self.rx.appendleft((payload[count:], start, end))
        self.rx_consumed += len(data)
        return Received(data, min(starts), max(ends))

    def finish(self, expected_tx):
        while self.stream.tell() < self.path.stat().st_size:
            self.pump()
        if (not self.started or self.pending or self.rx or self.expected_tx or self.outstanding_tx or self.pending_in
                or self.completed_tx != expected_tx):
            raise OSError("USB evidence has unmatched input/output or an incomplete tail.")

    def close(self):
        if self.stream is not None:
            stream, self.stream = self.stream, None
            stream.close()


class LiveTransport:
    """Single-use Linux fd owner; constructor is inert, close never drains RX."""

    def __init__(self, port, baseline, output, ingress, *, guard, plan):
        self.port, self.baseline = port, baseline
        self.output, self.ingress, self.guard, self.plan = Path(output), ingress, guard, plan
        self.owner = (os.getpid(), current_thread())
        self.fd = self.journal = None
        self.closed = self.opened = False
        self.journal_bytes = self.journal_records = self.serial_bytes = 0
        self.journal_broken = False
        self.writes = 0
        self.last_write = None
        self.token = hashlib.sha256(json.dumps(baseline, sort_keys=True).encode()).digest()
        self.node_stat = None

    def _owner(self):
        if (os.getpid(), current_thread()) != self.owner:
            raise OSError("Only the constructing process/thread may operate or close this transport.")

    def event(self, name, **fields):
        if self.journal is None or self.journal_broken:
            raise OSError("Adapter evidence journal is not open or has an uncertain write.")
        row = (json.dumps({"event": name, "monotonic": time.monotonic(), **fields}) + "\n").encode()
        if self.journal_bytes + len(row) > 262144 or self.journal_records >= 8192:
            raise OSError("Adapter journal budget exhausted.")
        self.journal_bytes += len(row)
        self.journal_records += 1
        self.journal_broken = True
        if self.journal.write(row) != len(row):
            raise OSError("Uncertain adapter evidence write.")
        self.journal_broken = False

    def _check(self, deadline):
        self._owner()
        if self.closed or time.monotonic() >= deadline:
            raise OSError("Closed transport or operation deadline; no resume.")
        self.guard()
        self.ingress.clock.check()
        from tools import marvin_session
        _host_call(marvin_session.check_identity, self.port, self.baseline)
        if self.fd is not None:
            info = os.fstat(self.fd)
            node = Path(self.baseline["tty"]).stat()
            if (info.st_dev, info.st_ino, info.st_rdev) != self.node_stat or (
                    node.st_dev, node.st_ino, node.st_rdev) != self.node_stat:
                raise OSError("Pinned tty generation changed.")
        if time.monotonic() >= deadline:
            raise OSError("Identity validation exceeded deadline.")

    def revalidate(self, *, deadline):
        self._owner()
        if self.opened or self.closed:
            raise OSError("Live adapter is single-use; explicitly prepare a new session.")
        from tools import marvin_session, marvin_probe, marvin_usbmon
        self.journal = marvin_usbmon._private_file(self.output / "adapter.jsonl")
        if _host_call(marvin_session.preflight, self.port, deadline=deadline) != self.baseline:
            raise OSError("Fresh preflight differs from pinned identity.")
        self._check(deadline)
        self.event("open_attempt", settings=dict(SETTINGS), input_flush=False)
        node = Path(self.baseline["tty"]).stat()
        self.node_stat = (node.st_dev, node.st_ino, node.st_rdev)
        self.fd = os.open(self.baseline["tty"], os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK |
                          os.O_CLOEXEC | os.O_NOFOLLOW)
        self.opened = True
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.ioctl(self.fd, termios.TIOCEXCL)
        self._check(deadline)
        owners = _host_call(subprocess.run, ["fuser", self.baseline["tty"]], capture_output=True, text=True,
                            timeout=marvin_probe.preflight_timeout(deadline))
        if (owners.returncode != 0 or set(owners.stdout.split()) != {str(os.getpid())}
                or owners.stderr.strip() not in ("", self.baseline["tty"] + ":")):
            raise OSError("Cannot establish sole visible tty ownership after exclusive open.")
        settings = _host_call(termios.tcgetattr, self.fd)
        settings[:4] = [0, 0, termios.CS8 | termios.CREAD | termios.CLOCAL, 0]
        settings[4:6] = [termios.B57600, termios.B57600]
        settings[6][termios.VMIN], settings[6][termios.VTIME] = 0, 0
        _host_call(termios.tcsetattr, self.fd, termios.TCSANOW, settings)
        fcntl.ioctl(self.fd, termios.TIOCMBIC, struct.pack("I", termios.TIOCM_DTR | termios.TIOCM_RTS))
        self.settings = _host_call(termios.tcgetattr, self.fd)
        self.event("termios_readback", requested=_termios_evidence(settings),
                   returned=_termios_evidence(self.settings))
        _validate_termios(settings, self.settings)
        self.event("open_completed", input_flush=False)
        quiet_end = time.monotonic() + 1
        while time.monotonic() < quiet_end:
            self.identity(deadline=deadline)
            self.ingress.pump()
            data = self._read_serial(self.plan.read_size)
            if data or self.ingress.rx_bytes:
                raise OSError("Pre-request input observed; query suppressed, evidence retained.")
            time.sleep(min(0.01, max(0, quiet_end - time.monotonic())))
        return self.identity(deadline=deadline)

    def identity(self, *, deadline):
        self._check(deadline)
        if self.fd is not None and hasattr(self, "settings"):
            actual = _host_call(termios.tcgetattr, self.fd)
            if actual != self.settings:
                self.event("termios_changed", expected=_termios_evidence(self.settings),
                           returned=_termios_evidence(actual))
                raise OSError("Serial settings changed during session.")
            lines = struct.unpack("I", fcntl.ioctl(self.fd, termios.TIOCMGET, bytes(4)))[0]
            if lines & (termios.TIOCM_DTR | termios.TIOCM_RTS):
                raise OSError("Driver-reported DTR/RTS are not low.")
        return self.token

    def _read_serial(self, size):
        readable, _, _ = select.select([self.fd], [], [], 0)
        if not readable:
            return None
        data = os.read(self.fd, size)
        if not data:
            raise OSError("Serial EOF/disconnect; no automatic reconnect.")
        self.serial_bytes += len(data)
        self.event("serial_rx", raw_hex=data.hex(), dequeue_monotonic=time.monotonic())
        if self.serial_bytes > self.plan.max_rx_bytes:
            raise OSError("Serial input budget exhausted; bounded last read retained.")
        return data

    def write(self, data, *, deadline):
        self._check(deadline)
        if (type(data) is not bytes or len(data) != 10 or
                data != read_raw_data_request(self.plan.first_sequence + self.writes)
                or self.writes >= self.plan.max_requests):
            raise OSError("Only the next approved ReadRawData request may be written.")
        return self._submit_once(data, deadline=deadline)

    def _submit_once(self, data, *, deadline):
        """Internal submission mechanics; public write retains the getter policy."""
        self._check(deadline)
        self.ingress.pump()
        if self.ingress.pending:
            raise OSError("Incomplete USB evidence publication before request; no write.")
        if self.ingress.rx or self.ingress.rx_bytes != self.serial_bytes:
            raise OSError("Unconsumed USB input before request; no write.")
        if self.ingress.expected_tx or self.ingress.outstanding_tx:
            raise OSError("Previous USB OUT lacks complete evidence; no write.")
        early = self._read_serial(self.plan.read_size)
        if early:
            raise OSError("Queued pre-request serial input; no write.")
        if self.last_write is not None and time.monotonic() < self.last_write + self.plan.interval:
            raise OSError("Live request pacing violation.")
        self.event("write_attempt", raw_hex=data.hex(), sequence=decode_packet(data).sequence)
        self._check(deadline)
        self.ingress.expected_tx.append(data)
        self.writes += 1
        count = os.write(self.fd, data)  # Exactly one syscall; short/error is never retried.
        self.last_write = time.monotonic()
        self.event("write_returned", accepted_bytes=count)
        return count

    def read(self, max_bytes, *, deadline):
        self._check(deadline)
        if type(max_bytes) is not int or not 1 <= max_bytes <= self.plan.read_size:
            raise OSError("Read size exceeds approved adapter budget.")
        self.ingress.pump()
        data = self._read_serial(max_bytes)
        while data is None:
            self._check(deadline)
            select.select([self.fd], [], [], min(0.005, max(0, deadline - time.monotonic())))
            self.ingress.pump()
            data = self._read_serial(max_bytes)
        while True:
            self._check(deadline)
            self.ingress.pump()
            received = self.ingress.match(data)
            if received is not None:
                self.event("usb_correlated_rx", size=len(data), started_at=received.started_at,
                           ended_at=received.ended_at)
                return received
            time.sleep(min(0.001, max(0, deadline - time.monotonic())))

    def wait(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.identity(deadline=deadline + self.plan.max_lateness)
            self.ingress.pump()
            if self.ingress.rx:
                raise OSError("Unsolicited USB input between requests; no continuation.")
            time.sleep(min(0.01, max(0, deadline - time.monotonic())))

    def close(self, *, deadline):
        self._owner()
        if self.closed:
            return
        self.closed = True
        errors = []
        try:
            if self.fd is not None:
                try:
                    self.event("close_attempt")
                except OSError as error:
                    errors.append(error)
                fd, self.fd = self.fd, None
                try:
                    os.close(fd)
                except OSError as error:
                    errors.append(error)
                else:
                    try:
                        self.event("close_completed")
                    except OSError as error:
                        errors.append(error)
        finally:
            if self.journal is not None:
                stream, self.journal = self.journal, None
                try:
                    os.fsync(stream.fileno())
                except OSError as error:
                    errors.append(error)
                finally:
                    try:
                        stream.close()
                    except OSError as error:
                        errors.append(error)
        if time.monotonic() >= deadline:
            errors.append(OSError("Serial close/evidence finalization exceeded cleanup deadline."))
        if errors:
            for error in errors[1:]:
                errors[0].add_note(str(error))
            raise errors[0]


def live_plan(*, max_requests=5, interval=1.0, duration=15.0, first_sequence=512):
    plan = PollPlan(max_requests=max_requests, interval=interval, duration=duration,
                    first_sequence=first_sequence, cleanup_timeout=5,
                    max_rx_bytes=8192, max_reads=4096)
    if interval < 1 or max_requests > 5 or duration > 30:
        raise ValueError("LIVE bounds: at most 5 requests, interval >=1 second, duration <=30 seconds.")
    return plan


@consent.powered_faults
def run_live(output, *, expected_physical_port, actuators_isolated=False,
             unprivileged_usbmon=False, plan=None, run=False,
             left_motor_powered_observation=False, operator_at_external_cutoff=False,
             motor_left_only_connected=False, motor_right_and_servos_isolated=False,
             motor_supply_off=False, authorize_unvalidated_zero_velocity=False, new_boot_declared=False):
    declarations = dict(
        left_motor_powered_observation=left_motor_powered_observation,
        operator_at_external_cutoff=operator_at_external_cutoff,
        motor_left_only_connected=motor_left_only_connected,
        motor_right_and_servos_isolated=motor_right_and_servos_isolated,
        motor_supply_off=motor_supply_off, authorize_unvalidated_zero_velocity=authorize_unvalidated_zero_velocity,
        new_boot_declared=new_boot_declared, unprivileged_usbmon=unprivileged_usbmon)
    if any(type(value) is not bool for value in (run, actuators_isolated, *declarations.values())):
        raise ValueError("Live declarations must be literal booleans.")
    powered = left_motor_powered_observation
    if powered:
        if consent.classify(actuators_isolated=actuators_isolated, **declarations) != "left_motor_powered_observation":
            raise ValueError("Select the complete powered observation scope.")
        if run is not True:
            raise ValueError("Powered observation requires explicit run=True.")
        if plan is not None and (type(plan) is not PollPlan or plan != powered_observation_plan()):
            raise ValueError("Powered observation accepts only its immutable one-getter plan.")
        plan = powered_observation_plan()
    elif any(value for name, value in declarations.items() if name != "unprivileged_usbmon"):
        raise ValueError("Separate declarations require the explicitly selected powered observation scope.")
    return _run_live(output, expected_physical_port=expected_physical_port,
                     actuators_isolated=actuators_isolated, unprivileged_usbmon=unprivileged_usbmon,
                     plan=plan, powered=powered, declarations=declarations)


def _run_live(output, *, expected_physical_port, actuators_isolated,
              unprivileged_usbmon, plan, powered, declarations):
    plan = live_plan() if plan is None else plan
    if not isinstance(plan, PollPlan) or plan != live_plan(
            max_requests=plan.max_requests, interval=plan.interval,
            duration=plan.duration, first_sequence=plan.first_sequence):
        raise ValueError("Only the bounded live plan is supported.")
    if (not powered and actuators_isolated is not True) or unprivileged_usbmon is not True:
        raise ValueError("Explicit isolation and unprivileged USB recording acknowledgments are required.")
    if (not isinstance(expected_physical_port, str) or len(expected_physical_port) > 100 or
            not re.fullmatch(r"[1-9][0-9]*-[1-9][0-9]*(?:\.[1-9][0-9]*)*", expected_physical_port)):
        raise ValueError("Name the reviewed physical USB port.")
    if os.geteuid() == 0:
        raise ValueError("Run as the ordinary user, never sudo.")
    output = new_output_path(output)
    from tools import marvin_campaign, marvin_probe, marvin_session
    from tools.marvin_legacy_probe import _validate_baseline
    baseline = marvin_session.preflight(marvin_probe.DEFAULT_PORT)
    _validate_baseline(baseline, expected_physical_port, marvin_campaign.DESCRIPTOR_HASH)
    clock = IngressClock()
    ingress = UsbIngress(output / "capture" / "usb" / "binary-events.bin", baseline["usb"], clock)
    outcome = None
    output.mkdir(mode=0o700)
    metadata = {"status": "incomplete", "evidence_kind": "recorded", "baseline": baseline,
                "plan": plan.to_dict(), "automatic_reconnect": False, "automatic_retries": False,
                "actuator_power_and_signal_isolation_acknowledged": actuators_isolated,
                "application_acknowledgment": "not_established"}
    if powered:
        metadata.update(**consent.observation_history(declarations), telemetry=[],
                        immutable_application_transcript_hex=["53000900000000bf0445"],
                        maximum_application_bytes=10,
                        collector_deadline_scope="3s operations only; excludes preflight/recorder startup, "
                                                 "separate 5s cleanup and physical power dwell",
                        usb_nominal_seconds=8, usb_hard_seconds=13)

    def capture(port, directory, *, guard, **options):
        nonlocal outcome
        if powered:
            actual = {name: options.get(name, False) for name in declarations}
            if (consent.classify(actuators_isolated=options.get("actuators_isolated", False), **actual)
                    != "left_motor_powered_observation" or actual != declarations
                    or options.get("seconds") != 3 or options.get("baudrate") != 57600
                    or options.get("probe_schedule") != schedule
                    or options.get("dtr") is not False or options.get("rts") is not False
                    or options.get("line_state_at_open") is not True
                    or options.get("probe_profile") != "legacy"
                    or options.get("allow_unknown_command") is not True
                    or options.get("allow_line_state_change") is not False
                    or options.get("allow_line_state_trial") is not False
                    or options.get("allow_telemetry_state_change") is not False
                    or options.get("probe_delay") != 0
                    or options.get("max_bytes") != marvin_probe.MAX_CAPTURE_BYTES
                    or set(options) != set(declarations) | {
                        "actuators_isolated", "seconds", "baudrate", "max_bytes", "dtr", "rts",
                        "line_state_at_open", "allow_line_state_change", "allow_line_state_trial",
                        "allow_telemetry_state_change", "probe_delay", "bytesize", "parity", "stopbits",
                        "probe_profile", "allow_unknown_command", "probe_schedule"}
                    or (options.get("bytesize"), options.get("parity"), options.get("stopbits")) != (8, "N", 1)):
                raise ValueError("Capture boundary changed the fixed powered observation scope.")
        directory.mkdir(mode=0o700)
        ingress.open()
        transport = LiveTransport(port, baseline, directory, ingress, guard=guard, plan=plan)
        try:
            outcome = collect(transport, directory / "poll.jsonl", ownership_key=baseline["tty"].encode(),
                              expected_identity=transport.token, plan=plan, evidence_kind="recorded",
                              wait=transport.wait,
                              **({"on_evidence": lambda events: inspect_powered_evidence(events, metadata["telemetry"]),
                                  "on_failure": consent.notify_cut_power} if powered else {}))
        except CollectionError as error:
            outcome = error.result
            raise OSError(f"LIVE collection failed: {error.code}: {error}") from error
        return {"status": "completed", "collection": outcome.report}

    try:
        clock.start()
        metadata["clock_offset_nanoseconds"] = clock.offset
        marvin_session.write_json(output / "metadata.json", metadata)
        schedule = tuple(marvin_probe.ScheduledWrite(
            index * plan.interval, read_raw_data_request(plan.first_sequence + index), "legacy-read-raw-data"
        ) for index in range(plan.max_requests))
        result = marvin_session.run_session(
            marvin_probe.DEFAULT_PORT, output / "capture", seconds=plan.duration,
            baudrate=57600, actuators_isolated=actuators_isolated, probe_schedule=schedule,
            allow_unknown_command=True, probe_profile="legacy", usbmon_backend="binary",
            expected_usb_identity=baseline["usb"], ready_callback=lambda _: clock.check(),
            usb_tail_seconds=5, usb_close_grace_seconds=5,
            capture_runner=capture, binary_payload_limit=USB_PAYLOAD_LIMIT,
            **(declarations if powered else {}),
        )
        if result.get("status") != "completed":
            raise OSError("Coordinator did not complete successfully.")
        ingress.finish(plan.max_requests * 10)
        clock.check()
        if ingress.rx_consumed != plan.max_requests * 144:
            raise OSError("Unexpected total correlated input size.")
        metadata.update(status="completed", session=result,
                        usb_in_bytes=ingress.rx_bytes, usb_out_completed_bytes=ingress.completed_tx,
                        observed_line_states=ingress.line_states)
    except BaseException as error:
        if powered:
            consent.notify_cut_power(error)
        metadata.update(status="failed", error=f"{type(error).__name__}: {error}"[:1024])
        raise
    finally:
        original = sys.exc_info()[1]
        try:
            cleanup = []
            for release in (ingress.close, clock.close):
                try:
                    release()
                except OSError as error:
                    if powered:
                        consent.notify_cut_power(error)
                    cleanup.append(error)
            if cleanup:
                metadata.update(status="failed", cleanup_error="; ".join(map(str, cleanup)))
                if original is None:
                    raise cleanup[0]
                for error in cleanup:
                    original.add_note(f"Live cleanup also failed: {error}")
        finally:
            if outcome is not None:
                metadata["collection"] = outcome.report
            metadata["finished_at"] = marvin_probe.utc_now()
            marvin_session.seal_evidence(output, metadata)
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Authorize this LIVE batch and possible line effects")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expected-physical-port")
    parser.add_argument("--actuators-isolated", action="store_true")
    consent.add_arguments(parser)
    consent.add_observation_arguments(parser)
    parser.add_argument("--max-requests", type=int)
    parser.add_argument("--interval", type=float)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--first-sequence", type=int)
    args = consent.parse_observation_arguments(parser, argv)
    try:
        powered = args.left_motor_powered_observation
        declarations = {**consent.arguments(args), **consent.observation_arguments(args)}
        if powered:
            consent.classify(actuators_isolated=args.actuators_isolated, **declarations)
        elif any(value for name, value in declarations.items() if name != "unprivileged_usbmon"):
            raise ValueError("Separate declarations require powered observation.")
        defaults = powered_observation_plan() if powered else live_plan()
        knobs = {name: getattr(args, name) if getattr(args, name) is not None else getattr(defaults, name)
                 for name in ("max_requests", "interval", "duration", "first_sequence")}
        plan = live_plan(**knobs)
        if powered and plan != defaults:
            raise ValueError("Conflicting CLI knobs: powered observation is fixed at one request, "
                             "interval 1, duration 3, sequence 2304; no overrides.")
        if not args.run:
            result = {"status": "dry_run", "evidence_kind": "not_captured", "plan": plan.to_dict(),
                      "maximum_application_bytes": 10 * plan.max_requests,
                      "one_persistent_tty_open": True, "usb_payload_limit": USB_PAYLOAD_LIMIT,
                      "usb_nominal_seconds": plan.duration + 5, "usb_hard_seconds": plan.duration + 10,
                      "required": ["--run", "--output NEWDIR", "--expected-physical-port",
                                   "--actuators-isolated", "--unprivileged-usbmon"],
                      "limitations": "Operator isolation/ownership required. Line glitches possible. No safety/ACK proof."}
            if powered:
                result.update(**consent.observation_history(declarations),
                              immutable_application_transcript_hex=["53000900000000bf0445"],
                              required=["--run", "--output NEWDIR", "--expected-physical-port",
                                        *("--" + name.replace("_", "-") for name in consent.OBSERVATION_FLAGS)],
                              limitations="HARDWARE HOLD: fresh future operator confirmation and bounded power plan "
                                          "required. 3s collector operations exclude startup, separate 5s cleanup "
                                          "and power dwell. Host cannot remove energy or detect movement.")
        else:
            if args.output is None:
                raise ValueError("--output NEWDIR is required.")
            result = run_live(args.output, expected_physical_port=args.expected_physical_port,
                              actuators_isolated=args.actuators_isolated,
                              run=args.run, plan=plan, **declarations)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        if args.left_motor_powered_observation:
            consent.notify_cut_power(error)
        print(json.dumps({"status": "failed", "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
