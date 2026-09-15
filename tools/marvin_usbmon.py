"""Bounded, target-filtered Linux usbmon recording and offline text analysis.

Only cached sysfs attributes/descriptors and a USB monitor interface are read;
no USB device is opened and
no libpcap, descriptor request, reset, driver, or power operation is used.
The bus-wide debugfs stream transiently enters this process BEFORE filtering.
Only retained output is device-scoped; this is not hardware isolation.

Public APIs: read_identity(path), parse_record(bytes), analyze_file(path), and
capture(usb_path, output, *, seconds, actuators_isolated, ...). Capture must run
in the main thread because it temporarily handles SIGINT/SIGTERM. Failed
captures with an evidence directory raise CaptureError carrying .metadata;
interruptions and limits return metadata, with distinct non-success statuses.
"""

import argparse
from collections import OrderedDict
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import pwd
import re
import select
import signal
import stat
import sys
import time

if __package__ in (None, ""):
    import marvin_usbmon_binary as binary
else:
    from . import marvin_usbmon_binary as binary

DEFAULT_MAX_BYTES = 1024 * 1024
DEFAULT_MAX_RECORDS = 10000
DEFAULT_MAX_LINE_BYTES = 4096
DEFAULT_MAX_PENDING = 4096
READ_SIZE = 4096
IDENTITY_INTERVAL = 0.2
COORDINATOR_STOP_FILE = "coordinator-stop"
USBMON_ROOT = Path("/sys/kernel/debug/usb/usbmon")
LIMITATIONS = [
    "Bus-wide events transiently enter memory before filtering; only output is target-scoped.",
    "usbmon is a host URB trace, not wire-level ACK/NAK evidence.",
    "Both backends retain at most 32 payload bytes per event; omitted bytes cannot be recovered from these files.",
    "Successful USB completions and zero-length IN are not serial/application acknowledgments.",
    "Submission -115 means in progress; pending IN at trace end is not a timeout.",
    "Cancellation/shutdown statuses do not by themselves establish a device failure.",
    "Status histograms count the primary status; colon-separated scheduling details remain in the text.",
    "Pairing covers only this trace; bounded pending storage may evict submissions.",
    "Identity checks are snapshots, not an atomic guarantee against removal/address reuse between checks.",
    "No guarantee of lossless capture: kernel buffering, text truncation and capture boundaries can omit events.",
]


class UsbmonError(ValueError):
    """An evidence-safe diagnostic; messages must never include record contents."""


class IdentityError(UsbmonError):
    pass


class ParseError(UsbmonError):
    pass


class CaptureError(UsbmonError):
    def __init__(self, metadata):
        self.metadata = metadata
        super().__init__(metadata["error"])


class AnalysisError(UsbmonError):
    def __init__(self, summary):
        self.summary = summary
        super().__init__(summary["error"])


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _integer_limit(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise UsbmonError(f"{name} must be an integer from {minimum} to {maximum}.")


def _safe_error(exc):
    if isinstance(exc, (UsbmonError, binary.BinaryError)):
        return str(exc)
    if isinstance(exc, OSError):
        return f"{type(exc).__name__} (errno {exc.errno}); operation failed."
    return f"Unexpected {type(exc).__name__}; operation failed."


def _read_attribute(path):
    with path.open("rb") as stream:
        raw = stream.read(129)
    if len(raw) > 128:
        raise IdentityError("Cached identity attribute exceeds its bound.")
    try:
        return raw.decode("ascii").strip()
    except UnicodeError:
        raise IdentityError("Cached identity attribute is not ASCII.") from None


def read_identity(usb_path):
    """Fresh cached sysfs snapshot, including descriptor SHA-256 and byte count.

    Accept a /sys/bus/usb/devices physical-device symlink or its canonical
    /sys/devices path, never a tty/interface/root-hub path. Re-reading the inode
    helps detect re-enumeration even when the numerical address is reused.
    The JSON dictionary contains no observation timestamps: equality is suitable
    for a parent's repeated guard checks. Descriptor reads use only sysfs's
    cached bytes, never a device request.
    """
    try:
        supplied = Path(usb_path)
        canonical = supplied.resolve(strict=True)
        if not canonical.is_relative_to("/sys/devices"):
            raise IdentityError("USB identity must resolve beneath /sys/devices.")
        port = re.fullmatch(r"([0-9]+)-[0-9]+(?:\.[0-9]+)*", canonical.name)
        if port is None:
            raise IdentityError("USB path must name a physical device, not an interface/root hub.")
        before = canonical.stat()
        if not stat.S_ISDIR(before.st_mode):
            raise IdentityError("Canonical USB path is not a directory.")

        def attributes():
            return tuple(_read_attribute(canonical / key)
                         for key in ("idVendor", "idProduct", "busnum", "devnum"))

        values = attributes()
        vendor, product, bus, device = values
        if vendor.lower() != "045e" or product.lower() != "4444":
            raise IdentityError("Cached VID/PID does not identify Microsoft Marvin (045e:4444).")
        if not re.fullmatch(r"[0-9]{1,5}", bus) or not re.fullmatch(r"[0-9]{1,3}", device):
            raise IdentityError("Cached USB bus/device numbers are not numeric.")
        busnum, devnum = int(bus), int(device)
        if not 1 <= busnum <= 65535 or not 1 <= devnum <= 127 or int(port[1]) != busnum:
            raise IdentityError("Cached bus/device numbers do not match a valid physical USB path.")
        descriptors = _cached_descriptors({"usb_path": str(canonical)})
        if values != attributes():
            raise IdentityError("Cached USB identity changed during validation.")
        after = canonical.stat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise IdentityError("USB device directory changed during validation.")
        if supplied.resolve(strict=True) != canonical:
            raise IdentityError("USB physical path changed during validation.")
        return {
            "usb_path": str(canonical),
            "physical_port": canonical.name,
            "idVendor": "045e",
            "idProduct": "4444",
            "busnum": busnum,
            "devnum": devnum,
            "sysfs_device": before.st_dev,
            "sysfs_inode": before.st_ino,
            "descriptors_sha256": hashlib.sha256(descriptors).hexdigest(),
            "descriptors_bytes": len(descriptors),
        }
    except OSError:
        raise IdentityError("Cannot read cached USB identity; device may be removed or inaccessible.") from None


def _check_identity(usb_path, baseline):
    if read_identity(usb_path) != baseline:
        raise IdentityError("USB physical identity/address changed; capture stopped without following it.")


def _cached_descriptors(identity):
    try:
        with (Path(identity["usb_path"]) / "descriptors").open("rb") as stream:
            data = stream.read(DEFAULT_MAX_BYTES + 1)
    except OSError:
        raise IdentityError("Cannot read cached sysfs descriptors.") from None
    if not 18 <= len(data) <= DEFAULT_MAX_BYTES:
        raise IdentityError("Cached descriptors are empty, short, or exceed the size bound.")
    if data[:2] != b"\x12\x01" or data[8:12] != b"\x5e\x04\x44\x44":
        raise IdentityError("Cached device descriptor does not match Marvin's VID/PID.")
    return data


def validate_privilege_drop(requested, *, euid=None, environ=None):
    """Validate sudo's invoking account before any capture file/device opens.

    Root must opt in; plain root and incomplete sudo environments are rejected.
    Like sudo itself, this assumes the root environment is trusted: these
    variables are not cryptographic proof of ancestry against a malicious root.
    """
    euid = os.geteuid() if euid is None else euid
    environ = os.environ if environ is None else environ
    if euid != 0:
        if requested:
            raise UsbmonError("--drop-to-invoking-user requires a root sudo invocation.")
        return None
    if not requested:
        raise UsbmonError("Root capture requires --drop-to-invoking-user under sudo.")
    values = []
    for key in ("SUDO_UID", "SUDO_GID"):
        raw = environ.get(key, "")
        if not re.fullmatch(r"[0-9]{1,10}", raw) or not 1 <= int(raw) <= 2**32 - 2:
            raise UsbmonError("A numeric nonzero SUDO_UID and SUDO_GID are required.")
        values.append(int(raw))
    uid, gid = values
    if not environ.get("SUDO_COMMAND") or not environ.get("SUDO_USER"):
        raise UsbmonError("A complete invoking-user sudo environment is required.")
    try:
        account = pwd.getpwuid(uid)
    except KeyError:
        raise UsbmonError("Sudo's invoking UID has no local account.") from None
    if account.pw_name != environ["SUDO_USER"] or account.pw_gid != gid or account.pw_name == "root":
        raise UsbmonError("Sudo's invoking account and UID/GID do not agree.")
    return uid, gid


def _drop_privileges(ids):
    if ids is None:
        return
    uid, gid = ids
    try:
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
        if (os.getuid(), os.geteuid(), os.getgid(), os.getegid()) != (uid, uid, gid, gid):
            raise UsbmonError("Privilege drop did not establish the invoking UID/GID.")
        if os.getgroups():
            raise UsbmonError("Supplementary groups remain after privilege drop.")
    except OSError:
        raise UsbmonError("Privilege drop failed; no recording will start.") from None


_ADDRESS = re.compile(rb"([CBIZ][io]):([0-9]{1,5}):([0-9]{1,3}):([0-9]{1,2})")


def _address(raw):
    fields = raw.split(None, 4)
    if len(fields) < 4:
        raise ParseError("Malformed potentially-target usbmon header.")
    address = _ADDRESS.fullmatch(fields[3])
    if address is None:
        raise ParseError("Malformed potentially-target usbmon address.")
    transfer = address[1].decode("ascii")
    bus, device, endpoint = (int(address[i]) for i in (2, 3, 4))
    if not 1 <= bus <= 65535 or not 0 <= device <= 127 or not 0 <= endpoint <= 15:
        raise ParseError("Out-of-range potentially-target usbmon address.")
    return transfer, bus, device, endpoint


def _other_device(raw, target):
    _, bus, device, _ = _address(raw)
    return (bus, device) != target


@dataclass(frozen=True)
class Record:
    urb_id: str
    timestamp_us: int
    event: str
    transfer: str
    busnum: int
    devnum: int
    endpoint: int
    status: int | None
    status_details: tuple
    length: int
    payload: bytes
    data_flag: str | None
    setup: tuple | None

    @property
    def endpoint_key(self):
        return f"{self.transfer}:{self.busnum}:{self.devnum:03d}:{self.endpoint}"

    @property
    def key(self):
        return self.busnum, self.devnum, self.urb_id


def parse_record(raw, *, max_line_bytes=DEFAULT_MAX_LINE_BYTES):
    """Parse one standard Ci/Co/Bi/Bo/Ii/Io text event without echoing input.

    Isochronous packet-descriptor syntax is deliberately unsupported: a target
    isochronous event is an explicit error, not silently misinterpreted evidence.
    """
    if len(raw) > max_line_bytes:
        raise ParseError("Overlong usbmon record.")
    transfer, bus, device, endpoint = _address(raw)
    if transfer[0] == "Z":
        raise ParseError("Target isochronous packet-descriptor syntax is unsupported.")
    try:
        tokens = raw.decode("ascii").split()
    except UnicodeError:
        raise ParseError("Non-ASCII potentially-target usbmon record.") from None
    if len(tokens) < 6 or not re.fullmatch(r"(?:0x)?[0-9a-fA-F]{1,32}", tokens[0]):
        raise ParseError("Malformed usbmon URB identifier or missing fields.")
    if not re.fullmatch(r"[0-9]{1,20}", tokens[1]) or tokens[2] not in ("S", "C", "E"):
        raise ParseError("Malformed usbmon timestamp/event.")
    event = tokens[2]
    setup = None
    status = None
    details = ()
    index = 5
    if tokens[4] == "s":
        if event != "S" or transfer[0] != "C" or len(tokens) < 11:
            raise ParseError("Malformed usbmon control setup.")
        widths = (2, 2, 4, 4, 4)
        if not all(re.fullmatch(rf"[0-9a-fA-F]{{{width}}}", value)
                   for width, value in zip(widths, tokens[5:10])):
            raise ParseError("Malformed usbmon control setup fields.")
        setup = tuple(int(value, 16) for value in tokens[5:10])
        index = 10
    else:
        if not re.fullmatch(r"-?[0-9]{1,10}(?::-?[0-9]{1,10}){0,8}", tokens[4]):
            raise ParseError("Malformed usbmon status/details.")
        numbers = tuple(int(value) for value in tokens[4].split(":"))
        if any(not -(2**31) <= number < 2**31 for number in numbers):
            raise ParseError("Out-of-range usbmon status/details.")
        status, details = numbers[0], numbers[1:]
    if not re.fullmatch(r"[0-9]{1,10}", tokens[index]) or int(tokens[index]) >= 2**31:
        raise ParseError("Malformed usbmon transfer length.")
    length = int(tokens[index])
    suffix = tokens[index + 1:]
    flag = None
    payload = b""
    if suffix:
        flag = suffix[0]
        if flag == "=":
            if len(suffix) < 2 or any(not re.fullmatch(r"(?:[0-9a-fA-F]{2}){1,4}", item)
                                      for item in suffix[1:]):
                raise ParseError("Malformed usbmon hexadecimal payload.")
            payload = bytes.fromhex("".join(suffix[1:]))
            if len(payload) > 32 or len(payload) > length:
                raise ParseError("usbmon payload exceeds text or reported-length bounds.")
        elif flag not in ("<", ">", "Z", "D", "E") or len(suffix) != 1:
            raise ParseError("Malformed usbmon data flag.")
    return Record(
        format(int(tokens[0], 16), "x"), int(tokens[1]), event, transfer,
        bus, device, endpoint, status, details, length, payload, flag, setup,
    )


def _increment(mapping, key, amount=1, *, max_keys=128):
    if key not in mapping and len(mapping) >= max_keys:
        key = "__other__"
    mapping[key] = mapping.get(key, 0) + amount


class Analyzer:
    """Streaming counters with bounded pending URBs, endpoint and status keys."""

    def __init__(self, *, max_pending=DEFAULT_MAX_PENDING):
        _integer_limit(max_pending, "max_pending", 1, 65536)
        self.max_pending = max_pending
        self.pending = OrderedDict()
        self.records = 0
        self.events = {"S": 0, "C": 0, "E": 0}
        self.endpoints = {}
        self.pairs = {
            key: 0 for key in (
                "matched_completions", "matched_submission_errors",
                "unmatched_completions", "unmatched_submission_errors",
                "evicted_pending_submissions", "duplicate_submission_ids",
                "endpoint_mismatches", "completion_exceeds_requested",
                "matched_requested_bytes", "matched_completed_bytes",
            )
        }
        self.payload = {
            key: 0 for key in (
                "records_with_captured_payload", "captured_bytes",
                "records_with_uncaptured_bytes", "uncaptured_bytes",
                "text_32_byte_truncation_records", "zero_length_in_completions",
            )
        }
        self.completion_status = {"success": 0, "cancellation_or_shutdown": 0, "other_nonzero": 0}
        self.submission_status = {"in_progress": 0, "other_negative": 0}
        self.endpoint_overflow_records = 0

    def add(self, record):
        self.records += 1
        self.events[record.event] += 1
        key = record.endpoint_key
        if key not in self.endpoints and len(self.endpoints) >= 128:
            key = "__other__"
            self.endpoint_overflow_records += 1
        endpoint = self.endpoints.setdefault(key, {
            "records": 0, "submissions": 0, "completions": 0, "submission_errors": 0,
            "matched_completions": 0, "submitted_requested_bytes": 0,
            "completed_reported_bytes": 0, "submission_error_reported_bytes": 0,
            "matched_submission_errors": 0, "matched_requested_bytes": 0,
            "matched_completed_bytes": 0, "matched_completion_statuses": {},
            "captured_payload_bytes": 0, "statuses": {"S": {}, "C": {}, "E": {}},
        })
        endpoint["records"] += 1
        endpoint["captured_payload_bytes"] += len(record.payload)
        _increment(endpoint["statuses"][record.event],
                   "setup" if record.status is None else str(record.status))
        if record.event == "S":
            endpoint["submissions"] += 1
            endpoint["submitted_requested_bytes"] += record.length
            if record.status == -115:
                self.submission_status["in_progress"] += 1
            elif record.status is not None and record.status < 0:
                self.submission_status["other_negative"] += 1
            if record.key in self.pending:
                self.pairs["duplicate_submission_ids"] += 1
                del self.pending[record.key]
            elif len(self.pending) >= self.max_pending:
                self.pending.popitem(last=False)
                self.pairs["evicted_pending_submissions"] += 1
            self.pending[record.key] = record
        else:
            completion = record.event == "C"
            label = "completions" if completion else "submission_errors"
            endpoint[label] += 1
            endpoint["completed_reported_bytes" if completion else
                     "submission_error_reported_bytes"] += record.length
            submitted = self.pending.pop(record.key, None)
            if submitted is not None and submitted.endpoint_key != record.endpoint_key:
                self.pairs["endpoint_mismatches"] += 1
                # Do not claim a match, or erase the original pending endpoint.
                self.pending[record.key] = submitted
                submitted = None
            if submitted is None:
                self.pairs["unmatched_" + label] += 1
            else:
                self.pairs["matched_" + label] += 1
                if completion:
                    endpoint["matched_completions"] += 1
                    endpoint["matched_requested_bytes"] += submitted.length
                    endpoint["matched_completed_bytes"] += record.length
                    _increment(endpoint["matched_completion_statuses"], str(record.status))
                    self.pairs["matched_requested_bytes"] += submitted.length
                    self.pairs["matched_completed_bytes"] += record.length
                    if record.length > submitted.length:
                        self.pairs["completion_exceeds_requested"] += 1
                else:
                    endpoint["matched_submission_errors"] += 1
            if completion:
                category = ("success" if record.status == 0 else
                            "cancellation_or_shutdown" if record.status in (-2, -104, -108)
                            else "other_nonzero")
                self.completion_status[category] += 1
        if record.payload:
            self.payload["records_with_captured_payload"] += 1
            self.payload["captured_bytes"] += len(record.payload)
        data_phase = ((record.event == "S" and record.transfer[1] == "o")
                      or (record.event == "C" and record.transfer[1] == "i"))
        if data_phase and record.length > len(record.payload):
            self.payload["records_with_uncaptured_bytes"] += 1
            self.payload["uncaptured_bytes"] += record.length - len(record.payload)
        if record.data_flag == "=" and len(record.payload) == 32 and record.length > 32:
            self.payload["text_32_byte_truncation_records"] += 1
        if record.event == "C" and record.transfer[1] == "i" and record.length == 0:
            self.payload["zero_length_in_completions"] += 1

    def summary(self):
        pending_in = sum(record.transfer[1] == "i" for record in self.pending.values())
        return {
            "schema_version": 1, "records": self.records, "events": dict(self.events),
            "endpoints": self.endpoints, "pairing": dict(
                self.pairs, pending_submissions_retained=len(self.pending),
                pending_in_retained=pending_in,
                pending_out_retained=len(self.pending) - pending_in,
                matched_submissions=(self.pairs["matched_completions"]
                                     + self.pairs["matched_submission_errors"]),
            ),
            "payload": dict(self.payload),
            "completion_status": dict(self.completion_status),
            "submission_status": dict(self.submission_status),
            "bookkeeping": {
                "max_pending": self.max_pending, "max_endpoint_keys": 128,
                "max_status_keys_per_event": 128,
                "overflow_bucket": "__other__ (additional to the key bound)",
                "endpoint_overflow_records": self.endpoint_overflow_records,
            },
            "limitations": list(LIMITATIONS),
        }


def analyze_file(path, *, max_line_bytes=DEFAULT_MAX_LINE_BYTES, max_pending=DEFAULT_MAX_PENDING):
    """Read ONLY a regular evidence file, return JSON-serializable analysis.

    Parse/IO failures raise AnalysisError with a failed, partial .summary.
    No sysfs, privilege checks, signals, or usbmon opens occur in this API.
    """
    _integer_limit(max_line_bytes, "max_line_bytes", 64, 16384)
    analyzer = Analyzer(max_pending=max_pending)
    line_number = 0
    try:
        if not stat.S_ISREG(Path(path).stat().st_mode):
            raise UsbmonError("Offline evidence must be a regular file.")
        with open(path, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise UsbmonError("Offline evidence changed to a non-regular file.")
            while True:
                raw = stream.readline(max_line_bytes + 1)
                if not raw:
                    break
                line_number += 1
                if not raw.endswith(b"\n"):
                    raise ParseError("Overlong or incomplete final usbmon record.")
                analyzer.add(parse_record(raw, max_line_bytes=max_line_bytes))
    except (OSError, UsbmonError, RuntimeError) as exc:
        summary = dict(analyzer.summary(), status="failed", error=_safe_error(exc),
                       error_line=line_number)
        raise AnalysisError(summary) from exc
    return dict(analyzer.summary(), status="completed", error=None)


class _Framer:
    def __init__(self, max_line_bytes, target):
        self.maximum = max_line_bytes
        self.target = target
        self.partial = b""
        self.discarding = False
        self.ignored_overlong = 0

    def feed(self, chunk):
        pieces = chunk.split(b"\n")
        for index, piece in enumerate(pieces):
            complete = index < len(pieces) - 1
            if self.discarding:
                if complete:
                    self.discarding = False
                continue
            raw = self.partial + piece + (b"\n" if complete else b"")
            self.partial = b""
            if len(raw) > self.maximum:
                if not _other_device(raw, self.target):
                    raise ParseError("Overlong potentially-target usbmon record.")
                self.ignored_overlong += 1
                self.discarding = not complete
            elif complete:
                yield raw
            else:
                self.partial = raw


def _private_file(path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    stream = None
    try:
        os.fchmod(fd, 0o600)
        stream = os.fdopen(fd, "wb", buffering=0)
        return stream
    finally:
        if stream is None:
            os.close(fd)


def _write_all(stream, data):
    remaining = memoryview(data)
    while remaining:
        written = stream.write(remaining)
        if written is None or written <= 0:
            raise UsbmonError("Evidence write made no progress.")
        remaining = remaining[written:]


def _write_json(path, value, *, replace=False):
    staging = path.with_name("." + path.name + ".writing")
    created = False
    try:
        with _private_file(staging) as stream:
            created = True
            _write_all(stream, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"))
            os.fsync(stream.fileno())
        if replace:
            os.replace(staging, path)
        else:
            os.link(staging, path)  # Atomic publication, refusing an existing destination.
    finally:
        if created:
            staging.unlink(missing_ok=True)


def _open_usbmon(busnum):
    _integer_limit(busnum, "busnum", 1, 65535)
    path = USBMON_ROOT / f"{busnum}u"
    try:
        return os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as error:
        raise UsbmonError(
            f"Cannot open debugfs USB monitor {path} (errno {error.errno}). "
            "Kernel lockdown can deny debugfs even to root. The binary backend "
            "uses the separate /dev/usbmon interface without changing security policy."
        ) from error


@contextmanager
def _interrupts():
    state = {"signal": None}

    def received(signum, frame):
        state["signal"] = signum

    previous = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, received)
        yield state
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


class _Stop(Exception):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason
        self.monotonic = time.monotonic()


def _coordinator_stop_requested(output):
    # Inspect only the fixed entry's metadata; never open or follow the request.
    try:
        info = (output / COORDINATOR_STOP_FILE).lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_size != 0 or info.st_nlink != 1:
        raise UsbmonError("Coordinator stop request must be an empty, singly linked regular file, not a symlink.")
    return True


def capture(usb_path, output, *, seconds, actuators_isolated=False,
            drop_to_invoking_user=False, max_bytes=DEFAULT_MAX_BYTES,
            max_records=DEFAULT_MAX_RECORDS, max_line_bytes=DEFAULT_MAX_LINE_BYTES,
            max_pending=DEFAULT_MAX_PENDING, backend="text", coordinator_stop=False):
    """Capture a new private evidence directory; never follows address changes.

    Opt-in coordinator_stop accepts only an empty regular COORDINATOR_STOP_FILE
    in the new private output directory. It never extends the duration limit.
    Status/CLI exits: completed/duration|coordinator_stop=0; interrupted/signal=130;
    limit_reached/max_bytes|max_records=2; failed=1 (CaptureError).
    Startup failures before directory creation raise without creating evidence.
    """
    ids = validate_privilege_drop(drop_to_invoking_user)
    if type(coordinator_stop) is not bool:
        raise UsbmonError("coordinator_stop must be a boolean.")
    if backend not in ("text", "binary"):
        raise UsbmonError("USB monitor backend must be text or binary.")
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not math.isfinite(seconds) or not 0 < seconds <= 120:
        raise UsbmonError("seconds must be finite, greater than zero and at most 120.")
    if actuators_isolated is not True:
        raise UsbmonError("Explicit --actuators-isolated confirmation is required.")
    _integer_limit(max_bytes, "max_bytes", 1, 64 * DEFAULT_MAX_BYTES)
    if backend == "binary" and max_bytes < len(binary.FILE_MAGIC):
        raise UsbmonError("Binary evidence byte limit is smaller than its file header.")
    _integer_limit(max_records, "max_records", 1, 1000000)
    _integer_limit(max_line_bytes, "max_line_bytes", 64, 16384)
    analyzer = Analyzer(max_pending=max_pending)
    output = Path(output)
    if os.path.lexists(output):
        raise FileExistsError("Output already exists; refusing to overwrite evidence.")
    fd = None
    with _interrupts() as interrupt:
        try:
            identity = read_identity(usb_path)
            descriptors = _cached_descriptors(identity)
            if (len(descriptors) != identity["descriptors_bytes"]
                    or hashlib.sha256(descriptors).hexdigest() != identity["descriptors_sha256"]):
                raise IdentityError("Cached descriptors changed before capture; refusing a mismatched snapshot.")
            _check_identity(usb_path, identity)
            fd = (binary.open_monitor(identity["busnum"]) if backend == "binary"
                  else _open_usbmon(identity["busnum"]))
            _drop_privileges(ids)
            output.mkdir(mode=0o700)
            output.chmod(0o700)
            metadata = {
                "schema_version": 1, "status": "starting", "started_at": _utc(),
                "finished_at": None, "identity": identity, "stop_reason": None,
                "error": None, "limitations": list(LIMITATIONS),
                "backend": backend,
                "coordinator_stop": coordinator_stop,
                "coordinator_stop_file": COORDINATOR_STOP_FILE if coordinator_stop else None,
                "started_monotonic": None, "deadline_monotonic": None,
                "stopped_monotonic": None,
                "source": (f'/dev/usbmon{identity["busnum"]}' if backend == "binary"
                           else str(USBMON_ROOT / f'{identity["busnum"]}u')),
                "text_is_normalized_from_binary": backend == "binary",
                "timestamp_basis": "Unix realtime microseconds" if backend == "binary" else "kernel usbmon text clock",
                "retained_binary_bytes": 0,
                "binary_payload_limit": binary.PAYLOAD_LIMIT if backend == "binary" else None,
                "seconds": seconds, "max_bytes": max_bytes, "max_records": max_records,
                "max_line_bytes": max_line_bytes, "retained_bytes": 0,
                "retained_records": 0, "ignored_records": 0,
                "descriptors": {"file": "descriptors.bin", "bytes": len(descriptors),
                                "sha256": hashlib.sha256(descriptors).hexdigest(),
                                "source": "cached sysfs descriptors"},
                "privileges_dropped": ids is not None,
            }
            framer = _Framer(max_line_bytes, (identity["busnum"], identity["devnum"]))
            failure = None
            started = None
            try:
                _write_json(output / "metadata.json", metadata)
                with _private_file(output / "descriptors.bin") as stream:
                    _write_all(stream, descriptors)
                    os.fsync(stream.fileno())
                with _private_file(output / "usbmon.txt") as evidence, ExitStack() as extra:
                    original = None
                    if backend == "binary":
                        original = extra.enter_context(_private_file(output / "binary-events.bin"))
                        _write_all(original, binary.FILE_MAGIC)
                        metadata["retained_binary_bytes"] = len(binary.FILE_MAGIC)
                        metadata["monitor_initial_stats"] = binary.read_stats(fd)
                    try:
                        _check_identity(usb_path, identity)
                        if interrupt["signal"] is not None:
                            raise _Stop("interrupted", "signal")
                        started = time.monotonic()
                        deadline = started + seconds
                        metadata.update(started_monotonic=started, deadline_monotonic=deadline)
                        last_check = started
                        _write_json(output / "ready.json", {
                            "pid": os.getpid(), "busnum": identity["busnum"],
                            "devnum": identity["devnum"], "usb_path": identity["usb_path"],
                            "monotonic": started, "utc": _utc(),
                            "seconds": seconds, "deadline_monotonic": deadline,
                            "coordinator_stop": coordinator_stop,
                            "coordinator_stop_file": COORDINATOR_STOP_FILE if coordinator_stop else None,
                        })
                        metadata["status"] = "recording"
                        _write_json(output / "metadata.json", metadata, replace=True)

                        def boundary():
                            nonlocal last_check
                            if interrupt["signal"] is not None:
                                raise _Stop("interrupted", "signal")
                            now = time.monotonic()
                            if now >= deadline:
                                raise _Stop("completed", "duration")
                            if now - last_check >= IDENTITY_INTERVAL:
                                _check_identity(usb_path, identity)
                                last_check = time.monotonic()
                                now = last_check
                                if now >= deadline:
                                    raise _Stop("completed", "duration")
                                if interrupt["signal"] is not None:
                                    raise _Stop("interrupted", "signal")
                            if coordinator_stop and _coordinator_stop_requested(output):
                                # A request cannot turn an expired deadline or a signal into
                                # cooperative success, including time spent inspecting it.
                                if interrupt["signal"] is not None:
                                    raise _Stop("interrupted", "signal")
                                if time.monotonic() >= deadline:
                                    raise _Stop("completed", "duration")
                                raise _Stop("completed", "coordinator_stop")
                            return now

                        while True:
                            now = boundary()
                            timeout = max(0, min(deadline - now, IDENTITY_INTERVAL - (now - last_check)))
                            readable, _, _ = select.select([fd], [], [], timeout)
                            boundary()
                            if not readable:
                                continue
                            source_frame = b""
                            if backend == "binary":
                                try:
                                    header, payload = binary.read_event(fd)
                                except (BlockingIOError, InterruptedError):
                                    continue
                                if binary.address(header) != framer.target:
                                    metadata["ignored_records"] += 1
                                    continue
                                records = (binary.to_text(header, payload),)
                                source_frame = binary.evidence_frame(header, payload)
                            else:
                                try:
                                    chunk = os.read(fd, READ_SIZE)
                                except (BlockingIOError, InterruptedError):
                                    continue
                                if not chunk:
                                    raise UsbmonError("Unexpected EOF from usbmon; trace ended before its bound.")
                                records = framer.feed(chunk)
                            for raw in records:
                                boundary()
                                if _other_device(raw, framer.target):
                                    metadata["ignored_records"] += 1
                                    continue
                                record = parse_record(raw, max_line_bytes=max_line_bytes)
                                _check_identity(usb_path, identity)
                                boundary()
                                combined_size = (metadata["retained_bytes"] + len(raw)
                                                 + metadata["retained_binary_bytes"] + len(source_frame))
                                if combined_size > max_bytes:
                                    raise _Stop("limit_reached", "max_bytes")
                                _write_all(evidence, raw)
                                if original is not None:
                                    _write_all(original, source_frame)
                                    metadata["retained_binary_bytes"] += len(source_frame)
                                analyzer.add(record)
                                metadata["retained_bytes"] += len(raw)
                                metadata["retained_records"] += 1
                                _check_identity(usb_path, identity)
                                last_check = time.monotonic()
                                if metadata["retained_records"] >= max_records:
                                    raise _Stop("limit_reached", "max_records")
                                if combined_size >= max_bytes:
                                    raise _Stop("limit_reached", "max_bytes")
                    finally:
                        actual_bytes = evidence.tell()
                        metadata["unaccounted_retained_bytes"] = max(
                            0, actual_bytes - metadata["retained_bytes"])
                        metadata["retained_bytes"] = actual_bytes
                        os.fsync(evidence.fileno())
                        if original is not None:
                            metadata["retained_binary_bytes"] = original.tell()
                            os.fsync(original.fileno())
            except _Stop as stopped:
                metadata["status"], metadata["stop_reason"] = stopped.status, stopped.reason
                metadata["stopped_monotonic"] = stopped.monotonic
            except (OSError, UsbmonError, binary.BinaryError, RuntimeError) as exc:
                failure = exc
            if backend == "binary":
                try:
                    stats = binary.read_stats(fd)
                    metadata["monitor_final_stats"] = stats
                    dropped = stats["dropped"] + metadata.get("monitor_initial_stats", {}).get("dropped", 0)
                    if dropped:
                        failure = UsbmonError("USB monitor dropped events; capture is incomplete.")
                except (OSError, binary.BinaryError) as exc:
                    failure = exc
            try:
                _check_identity(usb_path, identity)
            except (OSError, UsbmonError) as exc:
                failure = exc
            if failure is not None:
                metadata.update(status="failed", stop_reason=(
                    "identity_unavailable_or_changed" if isinstance(failure, IdentityError)
                    else "capture_error"), error=_safe_error(failure))
            metadata.update(
                finished_at=_utc(), signal=interrupt["signal"],
                elapsed_seconds=None if started is None else time.monotonic() - started,
                ignored_overlong_records=framer.ignored_overlong,
                unretained_partial_line_bytes=len(framer.partial),
                discarding_unrelated_overlong_line=framer.discarding,
            )
            try:
                _write_json(output / "summary.json", dict(
                    analyzer.summary(), status=metadata["status"], error=metadata["error"],
                ))
            except (OSError, UsbmonError) as exc:
                failure = exc
                metadata.update(status="failed", stop_reason="summary_write_error", error=_safe_error(exc))
            _write_json(output / "metadata.json", metadata, replace=True)
            if failure is not None:
                raise CaptureError(metadata) from failure
            return metadata
        finally:
            if fd is not None:
                os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analyze", metavar="FILE", help="Offline evidence-only analysis to stdout as JSON.")
    parser.add_argument("--usb-path", type=Path)
    parser.add_argument("--output", type=Path, help="New private directory; must not exist.")
    parser.add_argument("--seconds", type=float, default=90)
    parser.add_argument("--actuators-isolated", action="store_true")
    parser.add_argument("--drop-to-invoking-user", action="store_true")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--max-records", type=int, default=DEFAULT_MAX_RECORDS)
    parser.add_argument("--max-line-bytes", type=int, default=DEFAULT_MAX_LINE_BYTES)
    parser.add_argument("--max-pending", type=int, default=DEFAULT_MAX_PENDING)
    parser.add_argument("--backend", choices=("text", "binary"), default="text")
    parser.add_argument("--coordinator-stop", action="store_true",
                        help=f"Allow an empty regular {COORDINATOR_STOP_FILE} file in the output directory to stop capture")
    args = parser.parse_args(argv)
    if args.analyze:
        if args.usb_path or args.output or args.drop_to_invoking_user or args.actuators_isolated or args.coordinator_stop:
            parser.error("--analyze cannot be combined with capture paths or privilege/isolation flags.")
        try:
            result = analyze_file(args.analyze, max_line_bytes=args.max_line_bytes, max_pending=args.max_pending)
        except AnalysisError as exc:
            result = exc.summary
        except (OSError, UsbmonError, RuntimeError) as exc:
            result = {"status": "failed", "error": _safe_error(exc)}
        print(json.dumps(result, indent=2, sort_keys=True), flush=True)
        return 0 if result["status"] == "completed" else 1
    if args.usb_path is None or args.output is None:
        parser.error("Capture requires --usb-path and --output.")
    try:
        result = capture(
            args.usb_path, args.output, seconds=args.seconds,
            actuators_isolated=args.actuators_isolated,
            drop_to_invoking_user=args.drop_to_invoking_user, max_bytes=args.max_bytes,
            max_records=args.max_records, max_line_bytes=args.max_line_bytes, max_pending=args.max_pending,
            backend=args.backend, coordinator_stop=args.coordinator_stop,
        )
    except CaptureError as exc:
        result = exc.metadata
    except (OSError, UsbmonError, binary.BinaryError, RuntimeError) as exc:
        result = {"status": "failed", "error": _safe_error(exc)}
    print(json.dumps(result, indent=2, sort_keys=True),
          file=sys.stderr if result["status"] == "failed" else sys.stdout, flush=True)
    return {"completed": 0, "interrupted": 130, "limit_reached": 2}.get(result["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
