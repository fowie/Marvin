"""Bounded, transport-independent legacy getter sessions. No device-opening path.

Adapters are trusted boundaries, not raw serial handles: they must honor absolute
deadlines, retain conservative RX ingestion timestamps, and enforce exclusive
external ownership. See docs/legacy-client.md before implementing an adapter.
Importing or invoking this module does not access hardware.
"""

from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, replace
import sys
from threading import Lock, current_thread
import time
from types import MappingProxyType
from typing import Callable, Protocol

from tools import marvin_legacy_protocol as protocol
from tools.marvin_legacy_stream import LegacyStreamDecoder, LegacyStreamEvent


PROFILE = "marvin-legacy-se"
SETTINGS = MappingProxyType({
    "baudrate": 57600, "bytesize": 8, "parity": "N", "stopbits": 1,
    "xonxoff": False, "rtscts": False, "dsrdtr": False, "dtr": False, "rts": False,
})


def _number(name, value, lower, upper):
    # Compare integers before conversion: enormous integers must not overflow.
    if type(value) not in (int, float) or not lower <= value <= upper:
        raise ValueError(f"{name} must be finite and between {lower} and {upper}.")
    return float(value)


def _integer(name, value, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f"{name} must be an integer between {lower} and {upper}.")
    return value


def _identity(name, value):
    if type(value) is not bytes or not 1 <= len(value) <= 1024:
        raise ValueError(f"{name} must be 1..1024 immutable bytes.")
    return value


@dataclass(frozen=True)
class Limits:
    max_requests: int = 256
    max_rx_bytes: int = 65536
    max_events: int = 8192
    max_reads: int = 4096
    read_size: int = 512

    def __post_init__(self):
        for name, upper in (
            ("max_requests", 65536), ("max_rx_bytes", 16 * 1024 * 1024),
            ("max_events", 65536), ("max_reads", 65536), ("read_size", 4096),
        ):
            _integer(name, getattr(self, name), 1, upper)


@dataclass(frozen=True)
class Received:
    """All bytes were ingested within [started_at, ended_at], in clock domain."""

    data: bytes
    started_at: float
    ended_at: float


class Transport(Protocol):
    def revalidate(self, *, deadline: float) -> bytes:
        """Fresh external ownership/settings/identity validation; never cached."""
        ...

    def identity(self, *, deadline: float) -> bytes:
        """Current pinned identity including connection generation; loss raises OSError."""
        ...

    def write(self, data: bytes, *, deadline: float) -> int:
        """Return accepted count; exceptions mean uncertain delivery. Never retry."""
        ...

    def read(self, max_bytes: int, *, deadline: float) -> Received | None:
        """Return bounded timestamped bytes, or None for no input; loss raises OSError."""
        ...

    def close(self, *, deadline: float) -> None:
        """Release resources within deadline, including on failure; errors raise OSError."""
        ...


class SessionError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class OwnershipError(SessionError):
    pass


@dataclass(frozen=True)
class Failure:
    code: str
    message: str


@dataclass(frozen=True)
class Evidence:
    """`unverified` means not yet classified, not an authentication indicator.

Classification replaces this initial label. All confidence/ACK limitations
remain in force, including for a returned `matched_candidate`.
    """

    stream: LegacyStreamEvent
    started_at: float | None
    ended_at: float | None
    labels: tuple[str, ...] = ("unverified",)
    profile: str = PROFILE
    confidence: str = "raw_observation"
    application_acknowledgment: str = "not_established"
    evidence_kind: str = "unspecified"


@dataclass(frozen=True)
class Request:
    query: str
    sequence: int
    raw: bytes
    deadline: float
    input_boundary: int
    submitted_at: float | None = None
    accepted_bytes: int = 0
    uncertain_bytes: int = 0
    status: str = "prepared"
    reply_event: int | None = None


_CLAIM_LOCK = Lock()
_CLAIMS: dict[bytes, "LegacyClient"] = {}
_TRANSPORTS: dict[int, "LegacyClient"] = {}


class LegacyClient:
    """Single-use lifecycle: new -> active -> closed, or permanently invalid.

The constructing thread owns all operations. A new instance and start() with
fresh revalidation are required after close/failure; nothing reconnects/resumes.
Evidence properties are immutable snapshots, not a polling/recording service.
"""

    def __init__(self, transport: Transport, *, ownership_key: bytes,
                 expected_identity: bytes, session_timeout: float,
                 limits: Limits = Limits(), first_sequence: int = 0,
                 cleanup_timeout: float = 1.0, clock: Callable[[], float] = time.monotonic,
                 evidence_kind: str = "unspecified", on_failure=None, on_evidence=None):
        self._key = _identity("ownership_key", ownership_key)
        self._expected_identity = _identity("expected_identity", expected_identity)
        self._session_timeout = _number("session_timeout", session_timeout, 0.001, 600)
        self._cleanup_timeout = _number("cleanup_timeout", cleanup_timeout, 0.001, 30)
        if not isinstance(limits, Limits):
            raise ValueError("limits must be a Limits instance.")
        _integer("first_sequence", first_sequence, 0, 65535)
        if first_sequence + limits.max_requests > 65536:
            raise ValueError("Request budget would reuse/wrap uint16 sequences.")
        if not callable(clock):
            raise ValueError("clock must be callable.")
        if on_failure is not None and not callable(on_failure):
            raise ValueError("on_failure must be callable.")
        self._on_failure = on_failure
        if on_evidence is not None and not callable(on_evidence):
            raise ValueError("on_evidence must be callable.")
        self._on_evidence = on_evidence
        if evidence_kind not in protocol.EVIDENCE_KINDS:
            raise ValueError("Invalid caller-declared evidence_kind.")
        self._evidence_kind = evidence_kind
        self._transport, self._clock, self._limits = transport, clock, limits
        self._first_sequence = first_sequence
        self._owner, self._busy = current_thread(), Lock()
        self._state, self._failure = "new", None
        self._claimed, self._close_attempted = False, False
        self._cleanup_errors: list[Failure] = []
        self._decoder = LegacyStreamDecoder(max_input_bytes=limits.max_rx_bytes)
        self._spans = deque()
        self._last_rx_bounds = None
        self._boundary_uncertain = False
        self._events: list[Evidence] = []
        self._requests: list[Request] = []
        self._reads = 0
        self._rejected_input_bytes = 0
        self._last_now = 0.0
        self._session_deadline = None

    @property
    def state(self):
        return self._state

    @property
    def failure(self):
        return self._failure

    @property
    def cleanup_errors(self):
        return tuple(self._cleanup_errors)

    @property
    def evidence(self):
        return tuple(self._events)

    @property
    def requests(self):
        return tuple(self._requests)

    @property
    def accepted_bytes(self):
        return sum(request.accepted_bytes for request in self._requests)

    @property
    def uncertain_bytes(self):
        return sum(request.uncertain_bytes for request in self._requests)

    @property
    def rejected_input_bytes(self):
        return self._rejected_input_bytes

    def _abort(self, code, message):
        message = message[:1024]
        if self._failure is None:
            self._failure = Failure(code, message)
        raise SessionError(code, message)

    @contextmanager
    def _operation(self):
        if current_thread() is not self._owner or not self._busy.acquire(blocking=False):
            raise OwnershipError("ownership", "Only the owning thread may operate, without reentrancy.")
        try:
            yield
        finally:
            self._busy.release()

    @contextmanager
    def _guard_failure(self):
        completed = False
        try:
            yield
            completed = True
        except (OSError, ValueError, TypeError) as error:
            code = "transport_error" if isinstance(error, OSError) else "adapter_contract"
            self._abort(code, f"{type(error).__name__}: {str(error)[:1024]}")
        finally:
            if not completed:
                self._state = "invalid"
                if self._failure is None:
                    self._failure = Failure("operation_aborted", "Operation interrupted; no resume is permitted.")
                if self._requests and self._requests[-1].status != "matched":
                    self._requests[-1] = replace(self._requests[-1], status="failed")
                if self._on_failure is not None:
                    try:
                        self._on_failure(sys.exc_info()[1] or self._failure.message)
                    except Exception as error:
                        self._cleanup_errors.append(Failure("failure_notification", str(error)[:1024]))
                self._finish_and_cleanup(primary_failure=True)

    def _now(self, *, record_failure=True):
        now = _number("clock", self._clock(), 0, 1e12)
        if now < self._last_now:
            message = "Monotonic clock moved backwards."
            if record_failure:
                self._abort("clock_regressed", message)
            raise ValueError(message)
        self._last_now = now
        return now

    def _check_deadline(self, deadline):
        now = self._now()
        if now >= deadline:
            self._abort("deadline", "Request/session deadline expired; no retry or resume.")
        return now

    def _check_identity(self, deadline):
        self._check_deadline(deadline)
        identity = self._transport.identity(deadline=deadline)
        if _identity("identity", identity) != self._expected_identity:
            self._abort("identity_changed", "Pinned identity or connection generation changed.")
        self._check_deadline(deadline)

    def start(self):
        with self._operation():
            if self._state != "new":
                raise SessionError("lifecycle", "start() requires a new session and fresh revalidation.")
            with _CLAIM_LOCK:
                if self._key in _CLAIMS or id(self._transport) in _TRANSPORTS:
                    raise OwnershipError("ownership", "Transport/resource already has a session owner.")
                _CLAIMS[self._key] = _TRANSPORTS[id(self._transport)] = self
                self._claimed = True
            with self._guard_failure():
                self._session_deadline = self._now() + self._session_timeout
                observed = self._transport.revalidate(deadline=self._session_deadline)
                if _identity("revalidated identity", observed) != self._expected_identity:
                    self._abort("identity_changed", "Fresh revalidation did not match the expected identity.")
                self._check_identity(self._session_deadline)
                self._state = "active"
        return self

    def _append_events(self, events):
        indices = []
        for event in events:
            while self._spans and self._spans[0][0] <= event.offset:
                self._spans.popleft()
            started = self._spans[0][1] if self._spans else None
            ended = next((span[2] for span in self._spans if span[0] >= event.end_offset), None)
            indices.append(len(self._events))
            self._events.append(Evidence(event, started, ended, evidence_kind=self._evidence_kind))
        while self._spans and self._spans[0][0] <= self._decoder.offset:
            self._spans.popleft()
        return indices

    def _read_capacity(self):
        if self._reads >= self._limits.max_reads:
            self._abort("read_limit", "Session read-call budget exhausted.")
        # Reserve worst-case one event per new/pending byte, including finish().
        capacity = min(
            self._limits.read_size, self._limits.max_rx_bytes - self._decoder.input_bytes,
            self._limits.max_events - len(self._events) - self._decoder.buffered_bytes,
        )
        if capacity <= 0:
            self._abort("evidence_limit", "RX byte/event budget exhausted; input was not read or discarded.")
        return capacity

    def _receive(self, deadline):
        capacity = self._read_capacity()
        self._check_identity(deadline)
        self._reads += 1
        chunk = self._transport.read(capacity, deadline=deadline)
        if chunk is None:
            self._check_identity(deadline)
            return
        if not isinstance(chunk, Received) or type(chunk.data) is not bytes or not chunk.data:
            self._abort("adapter_contract", "read() must return nonempty immutable Received bytes or None.")
        if len(chunk.data) > capacity:
            self._rejected_input_bytes += len(chunk.data)
            self._abort("adapter_overread", "Adapter exceeded max_bytes; entire returned chunk rejected, not retained.")
        # Retain bounded bytes even when timing, identity, or a later clock check fails.
        valid_times = all(type(value) in (int, float) and 0 <= value <= 1e12
                          for value in (chunk.started_at, chunk.ended_at))
        start, end = (chunk.started_at, chunk.ended_at) if valid_times else (None, None)
        previous = self._last_rx_bounds
        self._spans.append((self._decoder.input_bytes + len(chunk.data), start, end))
        indices = self._append_events(self._decoder.feed(chunk.data))
        now = self._now()
        if (not valid_times or not start <= end <= now
                or (previous is not None and (start < previous[0] or end < previous[1]))):
            self._abort("adapter_timing", "Invalid or unordered RX ingestion-time bounds.")
        self._last_rx_bounds = (start, end)
        for index in indices:
            self._classify(index, now)
        if self._on_evidence is not None:
            self._on_evidence()
        self._check_identity(deadline)

    def _classify(self, index, now):
        evidence = self._events[index]
        event = evidence.stream
        labels = []
        request = self._requests[-1] if self._requests else None
        if event.follows_corruption or self._boundary_uncertain:
            labels.append("ambiguous_boundary")
        if event.kind in ("noise", "error"):
            self._boundary_uncertain = True
        if request is None:
            labels.append("unsolicited")
        else:
            if (event.offset < request.input_boundary or evidence.started_at is None
                    or request.submitted_at is None or evidence.started_at <= request.submitted_at):
                labels.append("pre_request")
            if now >= request.deadline or (evidence.ended_at is not None and evidence.ended_at >= request.deadline):
                labels.append("late")
        confidence = "raw_observation"
        if event.kind != "frame":
            labels.append({"error": "malformed", "partial": "partial", "noise": "noise"}[event.kind])
        else:
            packet = event.packet
            try:
                if packet is None or protocol.decode_packet(event.raw) != packet:
                    raise ValueError("Stream packet does not match retained raw bytes.")
                # Also validates packet.raw, rather than trusting decoded fields.
                if protocol.decode_packet(packet.raw) != packet:
                    raise ValueError("Packet fields disagree with packet.raw.")
            except (ValueError, TypeError):
                labels.append("malformed")
                self._boundary_uncertain = True
            else:
                confidence = "framing_integrity_only"
                # Sequences are contiguous and never reused within this session.
                previous_index = packet.sequence - self._first_sequence
                previous = (self._requests[previous_index]
                            if 0 <= previous_index < len(self._requests) - 1 else None)
                if previous is not None:
                    labels.append("stale")
                    if previous.reply_event is not None and packet == self._events[previous.reply_event].stream.packet:
                        labels.append("duplicate")
                elif request is None or packet.sequence != request.sequence:
                    labels.append("unsolicited")
                elif packet.command != protocol.GETTERS[request.query].command:
                    labels.append("unexpected_command")
                if packet.response_field != protocol.GETTER_RESPONSE_FIELD:
                    labels.append("request_echo" if packet.response_field == 0 and not packet.payload else "error_status")
                if request is not None and packet.sequence == request.sequence:
                    try:
                        protocol.validate_getter_reply(packet, request.query, request.sequence)
                    except ValueError:
                        if packet.command == protocol.GETTERS[request.query].command:
                            if len(packet.payload) != protocol.GETTERS[request.query].payload_bytes:
                                labels.append("unexpected_payload")
                        if not labels:
                            labels.append("malformed")
                    else:
                        if request.reply_event is not None:
                            labels.append("duplicate")
                        if not labels:
                            labels.append("matched_candidate")
                            confidence = "integrity_and_shape_match_not_authenticated"
                            self._requests[-1] = replace(request, reply_event=index)
        self._events[index] = replace(evidence, labels=tuple(dict.fromkeys(labels)), confidence=confidence)

    def request(self, query, *, timeout, allow_telemetry_state_change=False):
        with self._operation():
            if self._state != "active":
                raise SessionError("lifecycle", "Requests require an active session; no automatic resume.")
            if not isinstance(query, str) or query not in protocol.GETTERS:
                raise ValueError("Select one of the five reviewed legacy getters.")
            timeout = _number("timeout", timeout, 0.001, 120)
            if type(allow_telemetry_state_change) is not bool:
                raise ValueError("allow_telemetry_state_change must be an explicit boolean.")
            if query == "get-unit-info" and not allow_telemetry_state_change:
                raise ValueError("GetUnitInfo requires separate consent to handshake/telemetry state changes.")
            with self._guard_failure():
                self._check_deadline(self._session_deadline)
                if len(self._requests) >= self._limits.max_requests:
                    self._abort("request_limit", "Session request/sequence budget exhausted.")
                if self._boundary_uncertain:
                    self._abort("ambiguous_boundary", "Prior noise/corruption prevents further correlated requests.")
                self._read_capacity()
                needed = protocol.GETTERS[query].payload_bytes + protocol.FRAME_OVERHEAD
                if min(self._limits.max_rx_bytes - self._decoder.input_bytes,
                       self._limits.max_events - len(self._events) - self._decoder.buffered_bytes) < needed:
                    self._abort("evidence_limit", "Insufficient evidence reservation for another complete reply.")
                deadline = min(self._last_now + timeout, self._session_deadline)
                sequence = self._first_sequence + len(self._requests)
                raw = protocol.GETTERS[query].encode(sequence)
                self._requests.append(Request(query, sequence, raw, deadline, self._decoder.input_bytes))
                self._check_identity(deadline)
                self._requests[-1] = replace(self._requests[-1], status="write_attempted", uncertain_bytes=len(raw))
                count = self._transport.write(raw, deadline=deadline)
                if type(count) is not int or not 0 <= count <= len(raw):
                    self._abort("uncertain_write", "Invalid accepted-byte count; delivery is uncertain.")
                self._requests[-1] = replace(
                    self._requests[-1], accepted_bytes=count, uncertain_bytes=len(raw) - count,
                )
                if count != len(raw):
                    self._abort("short_write", "Incomplete request submission; no retry.")
                submitted_at = self._check_deadline(deadline)
                self._requests[-1] = replace(self._requests[-1], submitted_at=submitted_at, status="awaiting_reply")
                self._check_identity(deadline)
                while self._requests[-1].reply_event is None:
                    self._receive(deadline)
                self._check_identity(deadline)
                request = replace(self._requests[-1], status="matched")
                self._requests[-1] = request
                return self._events[request.reply_event]

    def _finish(self):
        for index in self._append_events(self._decoder.finish()):
            self._classify(index, self._last_now)
        if self._on_evidence is not None:
            self._on_evidence()

    def _finish_and_cleanup(self, *, primary_failure):
        primary_error = sys.exc_info()[1] if primary_failure else None
        finished = False
        try:
            try:
                self._finish()
                finished = True
            finally:
                finish_error = None if finished else sys.exc_info()[1]
                if primary_error is None:
                    primary_error = finish_error
                elif finish_error is not None and finish_error is not primary_error:
                    self._cleanup_errors.append(Failure("finish_error", str(finish_error)[:1024]))
                if finish_error is not None and self._on_failure is not None:
                    try:
                        self._on_failure(finish_error)
                    except Exception as error:
                        self._cleanup_errors.append(Failure("failure_notification", str(error)[:1024]))
                self._cleanup(primary_failure=primary_failure or primary_error is not None)
        finally:
            cleanup_error = sys.exc_info()[1]
            if primary_error is not None and cleanup_error is not None and cleanup_error is not primary_error:
                # Retain secondary failures, but never replace the initiating exception.
                if cleanup_error is not finish_error:
                    self._cleanup_errors.append(Failure("cleanup_aborted", str(cleanup_error)[:1024]))
                raise primary_error

    def _cleanup(self, *, primary_failure):
        if not self._claimed or self._close_attempted:
            return
        self._close_attempted = True
        try:
            try:
                now = self._now(record_failure=False)
            except (OSError, ValueError, TypeError, RuntimeError) as error:
                self._cleanup_errors.append(Failure("cleanup_clock", str(error)[:1024]))
                now = self._last_now
            try:
                result = self._transport.close(deadline=now + self._cleanup_timeout)
                if result is not None:
                    self._cleanup_errors.append(Failure("close_result", "Adapter close must return exactly None."))
            except (OSError, ValueError, TypeError, RuntimeError) as error:
                self._cleanup_errors.append(Failure("close_error", str(error)[:1024]))
            finally:
                try:
                    if self._now(record_failure=False) >= now + self._cleanup_timeout:
                        self._cleanup_errors.append(Failure("close_deadline", "Adapter close exceeded its deadline."))
                except (OSError, ValueError, TypeError, RuntimeError) as error:
                    diagnostic = Failure("cleanup_clock", str(error)[:1024])
                    if diagnostic not in self._cleanup_errors:
                        self._cleanup_errors.append(diagnostic)
        finally:
            with _CLAIM_LOCK:
                del _CLAIMS[self._key]
                del _TRANSPORTS[id(self._transport)]
                self._claimed = False
        if self._cleanup_errors:
            self._state = "invalid"
            if not primary_failure:
                self._abort("cleanup_failed", "Close/clock cleanup failed; inspect cleanup_errors.")

    def close(self):
        with self._operation():
            if self._state in ("closed", "invalid"):
                return
            completed = False
            try:
                self._finish_and_cleanup(primary_failure=False)
                self._state = "closed"
                completed = True
            finally:
                if not completed:
                    self._state = "invalid"
                    if self._failure is None:
                        self._failure = Failure("close_aborted", "Close interrupted; no resume is permitted.")

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.close()
        else:
            with self._operation():
                self._state = "invalid"
                if self._failure is None:
                    self._failure = Failure("context_aborted", "Caller failed inside the session context.")
                self._finish_and_cleanup(primary_failure=True)
        return False
