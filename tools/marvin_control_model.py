"""Deterministic SYNTHETIC control-policy model. No transport or wire encoding.

All authorization, measurements and states here are modeled declarations, not
physical authority or proof. See docs/offline-control-policy.md.
"""

from dataclasses import dataclass, fields, replace
import math
from threading import Lock, current_thread
from types import MappingProxyType


PROFILE = "marvin-legacy-se"
MAX_RAW_BYTES = 4096
MAX_TEXT = 256
MAX_TIME = 1e12
MAX_INPUT_DEPTH = 16
MAX_INPUT_NODES = 1024
REVIEW_KINDS = frozenset(("calibration", "physical_stop", "limits"))
REPLY_FAULTS = frozenset((
    "late", "stale", "duplicate", "unmatched", "unsolicited", "pre_request",
    "request_echo", "error_status", "unexpected_command", "unexpected_payload",
    "malformed", "noise", "partial", "ambiguous_boundary", "unverified",
    "sequence_error", "status_error",
))
SIGNALS = frozenset((
    "tick", "stop", "disarm", "reset", "restart", "host_exit", "host_crash",
    "host_silence", "transport_lost", "session_invalid", "watchdog_hypothesis",
))


class ModelError(ValueError):
    """Invalid model construction, structural schema or unretainable input."""


class OwnershipError(RuntimeError):
    """The constructing thread exclusively owns this model's lifecycle."""


class _Fault(Exception):
    def __init__(self, code):
        self.code = code


def _number(value, *, lower=0, upper=MAX_TIME, positive=False):
    if (type(value) not in (int, float) or not lower <= value <= upper
            or (positive and value == 0)):
        raise _Fault("invalid_number")
    return value


def _text(value):
    if type(value) is not str or not 1 <= len(value) <= MAX_TEXT or not value.strip():
        raise _Fault("invalid_text")


@dataclass(frozen=True)
class Scope:
    identity: str
    profile: str
    calibration: str
    velocity_unit: str
    acceleration_unit: str


@dataclass(frozen=True)
class Policy:
    """Explicit hypothetical inputs; there are no physical-safe defaults."""

    reference: str
    max_velocity: float
    max_acceleration: float
    max_duration: float
    deadman_timeout: float
    reply_timeout: float
    max_command_age: float
    max_commands: int = 128
    max_events: int = 256

    def __post_init__(self):
        try:
            _text(self.reference)
            for name in (
                "max_velocity", "max_acceleration", "max_duration",
                "deadman_timeout", "reply_timeout", "max_command_age",
            ):
                _number(getattr(self, name), positive=True)
            for name, lower, upper in (("max_commands", 1, 65536), ("max_events", 2, 1024)):
                value = getattr(self, name)
                if type(value) is not int or not lower <= value <= upper:
                    raise _Fault("invalid_resource_limit")
        except _Fault as error:
            raise ModelError(f"Invalid policy: {error.code}") from error


@dataclass(frozen=True)
class Review:
    kind: str
    reference: str
    reviewer: str
    scope: Scope
    policy_reference: str
    measured: bool
    reviewed: bool
    valid_from: float
    expires_at: float
    source: str = "SYNTHETIC"


@dataclass(frozen=True)
class Token:
    """In-memory model capability, never robot authentication/authorization."""

    generation: int
    owner: str
    reference: str


@dataclass(frozen=True)
class Intent:
    sequence: int
    issued_at: float
    left_velocity: float
    right_velocity: float
    acceleration: float
    duration: float


@dataclass(frozen=True)
class Write:
    sequence: int
    raw: bytes
    accepted_bytes: int
    uncertain_bytes: int


@dataclass(frozen=True)
class Reply:
    sequence: int
    raw: bytes
    labels: tuple[str, ...]
    request_status: str


@dataclass(frozen=True)
class ExternalStop:
    """A synthetic external observer declaration, never a measured robot stop."""

    reference: str
    reviewer: str
    elapsed: float | None
    reviewed_limit: float | None
    outcome: str


@dataclass(frozen=True)
class Event:
    at: float
    kind: str
    scope: Scope | None = None
    token: Token | None = None
    owner: str | None = None
    reference: str | None = None
    reviews: tuple[Review, ...] = ()
    intent: Intent | None = None
    write: Write | None = None
    reply: Reply | None = None
    observation: ExternalStop | None = None


@dataclass(frozen=True)
class Pending:
    intent: Intent
    deadline: float
    status: str = "intended"
    write: Write | None = None
    written_at: float | None = None


@dataclass(frozen=True)
class State:
    policy: Policy
    scope: Scope
    mode: str = "new"
    generation: int = 0
    token: Token | None = None
    reviews: tuple[Review, ...] = ()
    last_at: float = 0
    armed_at: float | None = None
    deadman_deadline: float | None = None
    intent_deadline: float | None = None
    pending: Pending | None = None
    commands: int = 0
    last_velocity: tuple[float, float] = (0, 0)
    last_intent_at: float | None = None
    fault: str | None = None
    software_intention: str = "remain_disarmed"
    physical_stop: str = "not_established"
    physical_authorization: str = "not_granted"
    cleanup: str = "no_hardware_cleanup_exists"


@dataclass(frozen=True)
class Record:
    index: int
    event: Event
    category: str
    before: State
    after: State
    result: str
    confidence: str = "SYNTHETIC_model_only"
    application_acknowledgment: str = "not_established"


_TYPES = (Scope, Review, Token, Intent, Write, Reply, ExternalStop, Event)
EVENT_FIELDS = MappingProxyType({
    "connect": ("scope",),
    "authorize": ("owner", "reference", "reviews"),
    "arm": ("scope", "token"),
    "keepalive": ("scope", "token"),
    "intent": ("scope", "token", "intent"),
    "request_write": ("scope", "token", "write"),
    "application_reply": ("scope", "token", "reply"),
    "external_stop": ("observation",),
    **{name: () for name in SIGNALS},
})


def _retainable(value):
    """Reject mutable/unbounded objects before they can enter immutable history."""
    pending = [(value, 0)]
    visited = 0
    while pending:
        value, depth = pending.pop()
        visited += 1
        if depth > MAX_INPUT_DEPTH or visited > MAX_INPUT_NODES:
            raise ModelError("Input exceeds the structural depth or node budget.")
        if value is None or type(value) in (bool, float):
            continue
        if type(value) is int and value.bit_length() <= 128:
            continue
        if type(value) is str and len(value) <= MAX_TEXT:
            continue
        if type(value) is bytes and len(value) <= MAX_RAW_BYTES:
            continue
        if type(value) is tuple and len(value) <= 32:
            children = value
        elif type(value) in _TYPES:
            children = tuple(getattr(value, field.name) for field in fields(value))
        else:
            raise ModelError("Input must use bounded immutable model values.")
        pending.extend((item, depth + 1) for item in children)


def validate_event(event):
    """Validate the bounded, immutable event shape without executing it."""
    if type(event) is not Event:
        raise ModelError("Expected an Event.")
    _retainable(event)
    if type(event.kind) is not str or event.kind not in EVENT_FIELDS:
        raise ModelError("Unknown event kind; no opcode/dispatch extension is supported.")
    required = EVENT_FIELDS[event.kind]
    for field in fields(event):
        if field.name in ("at", "kind"):
            continue
        value = getattr(event, field.name)
        present = value != () if field.name == "reviews" else value is not None
        if present and field.name not in required:
            raise ModelError(f"Unexpected {field.name} for {event.kind}.")
        if not present and field.name in required and field.name != "reviews":
            raise ModelError(f"Missing {field.name} for {event.kind}.")
    for name, expected in (
        ("scope", Scope), ("token", Token), ("intent", Intent), ("write", Write),
        ("reply", Reply), ("observation", ExternalStop),
    ):
        value = getattr(event, name)
        if value is not None and type(value) is not expected:
            raise ModelError(f"{name} has the wrong model type.")
    if type(event.reviews) is not tuple or any(type(r) is not Review for r in event.reviews):
        raise ModelError("reviews must be an immutable tuple of Review values.")
    for review in event.reviews:
        _validate_review_schema(review)


def _scope_fields(scope):
    if type(scope) is not Scope:
        raise _Fault("scope_invalid")
    for field in fields(scope):
        _text(getattr(scope, field.name))


def _scope(scope):
    _scope_fields(scope)
    if scope.profile != PROFILE:
        raise _Fault("profile_mismatch")
    if (scope.velocity_unit, scope.acceleration_unit) != ("m/s", "m/s^2"):
        raise _Fault("unknown_units")


def _validate_review_schema(review):
    try:
        if type(review.kind) is not str or review.kind not in REVIEW_KINDS:
            raise _Fault("invalid_review_kind")
        for value in (review.reference, review.reviewer, review.policy_reference):
            _text(value)
        _scope_fields(review.scope)
        if type(review.measured) is not bool or type(review.reviewed) is not bool:
            raise _Fault("invalid_review_flags")
        if type(review.source) is not str or review.source != "SYNTHETIC":
            raise _Fault("non_synthetic_review")
        _number(review.valid_from)
        _number(review.expires_at)
        if review.valid_from >= review.expires_at:
            raise _Fault("invalid_review_interval")
    except _Fault as error:
        raise ModelError(f"Invalid review: {error.code}") from error


def validate_review(review):
    """Validate a declaration's schema, not its approval or current applicability."""
    if type(review) is not Review:
        raise ModelError("Expected a Review.")
    _retainable(review)
    _validate_review_schema(review)


def _same_scope(expected, actual):
    _scope(actual)
    if actual.identity != expected.identity:
        raise _Fault("identity_changed")
    if actual != expected:
        raise _Fault("scope_mismatch")


def _reviews(state, reviews, at):
    if len(reviews) != 3 or {r.kind for r in reviews} != REVIEW_KINDS:
        raise _Fault("missing_reviews")
    for review in reviews:
        _same_scope(state.scope, review.scope)
        if review.policy_reference != state.policy.reference:
            raise _Fault("policy_mismatch")
        if review.measured is not True or review.reviewed is not True:
            raise _Fault("unreviewed_evidence")
        if not review.valid_from <= at < review.expires_at:
            raise _Fault("stale_evidence")


def _revoke(state, mode, *, fault=None, intention="disarm_intent"):
    # Keep pending uncertain work for inspection. Only explicit reset/restart
    # removes it from working state; the bounded immutable audit still holds it.
    return replace(
        state, mode=mode, generation=state.generation + 1, token=None, reviews=(),
        armed_at=None, deadman_deadline=None, intent_deadline=None, fault=fault,
        software_intention=intention,
        pending=replace(state.pending, status="failed") if state.pending else None,
    )


def _authority(state, event):
    # Identity comparison prevents token reconstruction and cross-model reuse;
    # it is a process-local model capability, not a security boundary.
    if state.token is None or event.token is not state.token:
        raise _Fault("ownership")
    _same_scope(state.scope, event.scope)
    _reviews(state, state.reviews, event.at)


def _sequence(value):
    if type(value) is not int or not 0 <= value <= 65535:
        raise _Fault("sequence_error")


def _deadline(at, interval):
    deadline = at + interval
    if not at < deadline <= MAX_TIME:
        raise _Fault("invalid_deadline")
    return deadline


def _operate(state, event):
    at, kind = event.at, event.kind
    if kind == "external_stop":
        observation = event.observation
        _text(observation.reference)
        _text(observation.reviewer)
        if observation.outcome == "block":
            raise _Fault("external_stop_blocked")
        if observation.outcome == "fail":
            raise _Fault("external_stop_failed")
        if observation.outcome != "pass":
            raise _Fault("invalid_observation")
        _number(observation.elapsed)
        _number(observation.reviewed_limit, positive=True)
        if observation.elapsed > observation.reviewed_limit:
            raise _Fault("observation_limit")
        return state
    if kind == "restart":
        return replace(_revoke(state, "new", intention="remain_disarmed"),
                       pending=None, last_intent_at=None, last_velocity=(0, 0),
                       cleanup="no_hardware_cleanup_exists")
    if kind == "reset":
        if state.mode != "fault":
            raise _Fault("invalid_transition")
        return replace(_revoke(state, "new", intention="remain_disarmed"),
                       pending=None, last_intent_at=None, last_velocity=(0, 0),
                       cleanup="no_hardware_cleanup_exists")
    if state.mode in ("fault", "crashed", "exited"):
        raise _Fault(state.fault or "session_invalid")
    if kind == "host_crash":
        return replace(
            _revoke(state, "crashed", fault="host_crash",
                    intention="external_stop_required"),
            cleanup="cannot_execute_after_host_crash",
        )
    if kind == "host_exit":
        return _revoke(state, "exited", fault=(
            "uncertain_delivery" if state.pending else None))
    if kind in ("stop", "disarm"):
        if state.pending:
            raise _Fault("uncertain_delivery")
        return _revoke(state, "new" if state.mode == "new" else "disarmed")
    if kind in ("host_silence", "transport_lost", "session_invalid", "watchdog_hypothesis"):
        raise _Fault(kind)
    if kind == "tick":
        return state
    if kind == "connect":
        if state.mode != "new":
            raise _Fault("invalid_transition")
        _same_scope(state.scope, event.scope)
        return replace(state, mode="disarmed", software_intention="remain_disarmed")
    if kind == "authorize":
        if state.token is not None:
            raise _Fault("owner_changed" if event.owner != state.token.owner else "already_authorized")
        if state.mode != "disarmed":
            raise _Fault("invalid_transition")
        _text(event.owner)
        _text(event.reference)
        _reviews(state, event.reviews, at)
        return replace(state, token=Token(state.generation, event.owner, event.reference),
                       reviews=event.reviews)
    _authority(state, event)
    if kind == "arm":
        if state.mode != "disarmed":
            raise _Fault("invalid_transition")
        return replace(
            state, mode="armed", armed_at=at,
            deadman_deadline=_deadline(at, state.policy.deadman_timeout),
            last_intent_at=at, last_velocity=(0, 0), software_intention="model_armed_only",
        )
    if state.mode != "armed":
        raise _Fault("not_armed")
    if kind == "keepalive":
        return replace(state, deadman_deadline=_deadline(at, state.policy.deadman_timeout))
    if kind == "intent":
        intent, policy = event.intent, state.policy
        if state.pending:
            raise _Fault("outstanding_work")
        if state.commands >= policy.max_commands:
            raise _Fault("command_budget")
        _sequence(intent.sequence)
        if intent.sequence != state.commands:
            raise _Fault("sequence_error")
        _number(intent.issued_at)
        for value in (intent.left_velocity, intent.right_velocity):
            _number(value, lower=-policy.max_velocity, upper=policy.max_velocity)
        _number(intent.acceleration, upper=policy.max_acceleration, positive=True)
        _number(intent.duration, upper=policy.max_duration, positive=True)
        # Equal timestamps cannot establish creation after the arm boundary.
        if (intent.issued_at <= state.armed_at
                or not intent.issued_at <= at < _deadline(intent.issued_at, policy.max_command_age)):
            raise _Fault("stale_intent")
        intent_deadline = _deadline(intent.issued_at, intent.duration)
        if at >= intent_deadline:
            raise _Fault("intent_expired")
        velocities = (intent.left_velocity, intent.right_velocity)
        elapsed = at - state.last_intent_at
        if any(abs(new - old) > intent.acceleration * elapsed
               for new, old in zip(velocities, state.last_velocity)):
            raise _Fault("acceleration_limit")
        deadline = min(_deadline(at, policy.reply_timeout), intent_deadline,
                       state.deadman_deadline, *(r.expires_at for r in state.reviews))
        if deadline <= at:
            raise _Fault("deadline")
        return replace(
            state, pending=Pending(intent, deadline), commands=state.commands + 1,
            intent_deadline=intent_deadline,
            last_velocity=velocities, last_intent_at=at, software_intention="bounded_intent_only",
        )
    pending = state.pending
    if pending is None:
        if kind == "application_reply":
            _sequence(event.reply.sequence)
            if event.reply.sequence < state.commands:
                raise _Fault("duplicate")
        raise _Fault("unmatched")
    if kind == "request_write":
        write = event.write
        _sequence(write.sequence)
        if write.sequence != pending.intent.sequence:
            raise _Fault("sequence_error")
        if pending.status != "intended":
            raise _Fault("duplicate_write")
        if type(write.raw) is not bytes or not write.raw:
            raise _Fault("write_contract")
        if (type(write.accepted_bytes) is not int or type(write.uncertain_bytes) is not int
                or min(write.accepted_bytes, write.uncertain_bytes) < 0
                or write.accepted_bytes + write.uncertain_bytes != len(write.raw)):
            raise _Fault("write_contract")
        if write.accepted_bytes != len(write.raw):
            raise _Fault("partial_write" if write.accepted_bytes else "uncertain_write")
        return replace(state, pending=replace(pending, status="submitted", write=write, written_at=at))
    reply = event.reply
    _sequence(reply.sequence)
    if reply.sequence != pending.intent.sequence:
        raise _Fault("sequence_error")
    if type(reply.raw) is not bytes or not reply.raw:
        raise _Fault("reply_contract")
    if type(reply.labels) is not tuple or any(type(label) is not str for label in reply.labels):
        raise _Fault("reply_contract")
    for label in reply.labels:
        if label in REPLY_FAULTS:
            raise _Fault(label)
    if reply.labels != ("matched_candidate",) or reply.request_status != "matched":
        raise _Fault("reply_not_delivered")
    if pending.status != "submitted" or at <= pending.written_at:
        raise _Fault("ambiguous_boundary")
    return replace(state, pending=None, software_intention="correlated_reply_only")


def transition(state, event):
    """Pure reducer: (new immutable state, result). No clock reads or side effects.

    Callers supply nondecreasing model times. Exact deadline equality expires.
    Structural errors raise ModelError; semantic errors return a visible fault.
    """
    validate_event(event)
    try:
        _number(event.at)
        if event.at < state.last_at:
            raise _Fault("clock_regressed")
        state = replace(state, last_at=event.at)
        # Crash is an external simulator projection, not code running in a dead
        # host. Reset/restart may acknowledge expiry, but cannot silently skip it.
        if state.mode == "armed" and event.kind != "host_crash":
            _reviews(state, state.reviews, event.at)
            for deadline, code in (
                (state.deadman_deadline, "deadman_expired"),
                (state.intent_deadline, "intent_expired"),
                (state.pending.deadline if state.pending else None, "deadline"),
            ):
                if deadline is not None and event.at >= deadline:
                    raise _Fault(code)
        changed = _operate(state, event)
        return changed, "recorded" if event.kind == "external_stop" else "accepted"
    except _Fault as error:
        if state.mode in ("fault", "crashed", "exited"):
            return state, error.code
        return _revoke(state, "fault", fault=error.code), error.code


def _category(event):
    return {
        "intent": "software_intention",
        "stop": "software_intention",
        "disarm": "software_intention",
        "request_write": "request_write_declaration",
        "application_reply": "correlated_application_reply_declaration",
        "external_stop": "external_physical_observation_declaration",
        "host_crash": "external_simulator_crash_projection",
    }.get(event.kind, "lifecycle_declaration")


class Model:
    """Single-thread facade retaining bounded immutable evidence; never I/O.

    Resource budgets are lifetime-wide, including explicit resets/restarts.
    The final event slot is reserved for a visible budget fault.
    """

    def __init__(self, policy, scope):
        if type(policy) is not Policy or type(scope) is not Scope:
            raise ModelError("Explicit Policy and Scope required.")
        _retainable(scope)
        self._state = State(policy, scope)
        self._records = []
        self._owner, self._busy = current_thread(), Lock()

    @property
    def state(self):
        return self._state

    @property
    def records(self):
        return tuple(self._records)

    def step(self, event):
        if current_thread() is not self._owner or not self._busy.acquire(blocking=False):
            raise OwnershipError("Only the constructing thread may operate, without reentrancy.")
        try:
            if len(self._records) >= self.state.policy.max_events:
                raise ModelError("Evidence budget exhausted; model cannot resume.")
            before = self.state
            try:
                validate_event(event)
            except ModelError:
                if self.state.mode not in ("fault", "crashed", "exited"):
                    self._state = _revoke(self.state, "fault", fault="invalid_event")
                raise
            if len(self._records) == self.state.policy.max_events - 1:
                if self.state.mode not in ("fault", "crashed", "exited"):
                    self._state = _revoke(self.state, "fault", fault="event_budget")
                result = "event_budget"
            else:
                self._state, result = transition(self.state, event)
            record = Record(len(self._records), event, _category(event), before, self.state, result)
            self._records.append(record)
            return record
        finally:
            self._busy.release()


def to_json(value):
    """Lossless model bytes as hex; invalid nonfinite inputs as diagnostics."""
    if type(value) is bytes:
        return {"raw_hex": value.hex(), "bytes": len(value), "source": "SYNTHETIC"}
    if type(value) is float and not math.isfinite(value):
        return {"invalid_number": str(value)}
    if type(value) in (*_TYPES, Policy, State, Pending, Record):
        return {field.name: to_json(getattr(value, field.name)) for field in fields(value)}
    if type(value) is tuple:
        return [to_json(item) for item in value]
    return value
