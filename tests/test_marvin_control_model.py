from dataclasses import FrozenInstanceError, fields, replace
import json
import math
import sys
from threading import Thread
import unittest

from tools import marvin_control_model as m


class ControlModelTests(unittest.TestCase):
    def make_model(self, *, policy=None, scope=None, reviews=None, armed=True):
        policy = policy or m.Policy("SYNTHETIC-policy", 1, 2, 3, 2, 0.5, 0.25)
        scope = scope or m.Scope("SYNTHETIC-generation-1", m.PROFILE, "SYNTHETIC-cal", "m/s", "m/s^2")
        simulation = m.Model(policy, scope)
        simulation.step(m.Event(0, "connect", scope=scope))
        reviews = reviews if reviews is not None else tuple(
            m.Review(kind, "SYNTHETIC-measurement", "SYNTHETIC-reviewer",
                     scope, policy.reference, True, True, 0, 20)
            for kind in sorted(m.REVIEW_KINDS)
        )
        simulation.step(m.Event(
            0, "authorize", owner="SYNTHETIC-owner", reference="SYNTHETIC-approval", reviews=reviews))
        if armed and simulation.state.token is not None:
            self.authorized(simulation, 0, "arm")
        return simulation

    def authorized(self, simulation, at, kind, **payload):
        return simulation.step(m.Event(at, kind, scope=simulation.state.scope,
                                       token=simulation.state.token, **payload))

    def submit(self, simulation, *, at=0.5, **changes):
        intent = m.Intent(simulation.state.commands, at, 0.25, -0.25, 1, 1)
        return self.authorized(simulation, at, "intent", intent=replace(intent, **changes))

    def write(self, simulation, *, at=0.6, **changes):
        value = m.Write(0, b"SYNTHETIC", 9, 0)
        return self.authorized(simulation, at, "request_write", write=replace(value, **changes))

    def reply(self, simulation, *, at=0.7, **changes):
        value = m.Reply(0, b"SYNTHETIC-REPLY", ("matched_candidate",), "matched")
        return self.authorized(simulation, at, "application_reply", reply=replace(value, **changes))

    def fault(self, simulation, code):
        self.assertEqual(simulation.state.mode, "fault")
        self.assertEqual(simulation.state.fault, code)
        self.assertIsNone(simulation.state.token)
        self.assertEqual(simulation.state.physical_stop, "not_established")
        self.assertEqual(simulation.state.physical_authorization, "not_granted")

    def test_pure_transitions_immutable_evidence_and_stop_categories(self):
        simulation = self.make_model()
        old = simulation.state
        event = m.Event(0.1, "keepalive", scope=old.scope, token=old.token)
        first, result = m.transition(old, event)
        self.assertEqual((first, result), m.transition(old, event))
        self.assertEqual(simulation.state, old)
        self.submit(simulation)
        self.write(simulation)
        write_snapshot = simulation.records
        self.reply(simulation)
        simulation.step(m.Event(0.8, "stop"))
        simulation.step(m.Event(0.9, "external_stop", observation=m.ExternalStop(
            "SYNTHETIC-measurement", "SYNTHETIC-reviewer", 0.1, 0.2, "pass")))
        self.assertEqual(simulation.state.mode, "disarmed")
        self.assertIsNone(simulation.state.token)
        self.assertEqual(simulation.state.physical_stop, "not_established")
        self.assertEqual(write_snapshot[-1].after.pending.status, "submitted")
        self.assertEqual(simulation.records[-3].after.software_intention, "correlated_reply_only")
        self.assertEqual({record.category for record in simulation.records[-4:]}, {
            "request_write_declaration", "correlated_application_reply_declaration",
            "software_intention", "external_physical_observation_declaration",
        })
        with self.assertRaises(FrozenInstanceError):
            old.mode = "armed"
        for record in simulation.records:
            self.assertEqual(record.application_acknowledgment, "not_established")
        self.assertEqual(write_snapshot[-1].event.write.raw, b"SYNTHETIC")
        for previous, current in zip(simulation.records, simulation.records[1:]):
            self.assertIs(current.before, previous.after)
        stopped = simulation.records[-2]
        self.assertIs(stopped.before.token, old.token)
        self.assertEqual(stopped.before.reviews, old.reviews)
        self.assertEqual(stopped.before.deadman_deadline, old.deadman_deadline)
        self.assertEqual(stopped.before.commands, 1)
        self.assertIsNone(stopped.after.token)
        self.assertEqual(stopped.after.reviews, ())
        encoded = m.to_json(stopped)
        self.assertEqual(encoded["before"]["mode"], "armed")
        self.assertEqual(encoded["before"]["token"]["owner"], old.token.owner)
        self.assertEqual(encoded["after"]["mode"], "disarmed")
        json.dumps(m.to_json(simulation.records), allow_nan=False)

    def test_missing_unreviewed_stale_mismatched_prerequisites(self):
        baseline = self.make_model(armed=False)
        reviews, scope = baseline.state.reviews, baseline.state.scope
        cases = [
            ((), "missing_reviews"),
            (reviews[:2], "missing_reviews"),
            ((reviews[0], reviews[0], reviews[2]), "missing_reviews"),
        ]
        for field, value, code in (
            ("reviewed", False, "unreviewed_evidence"),
            ("measured", False, "unreviewed_evidence"),
            ("valid_from", 1, "stale_evidence"),
            ("policy_reference", "different", "policy_mismatch"),
            ("scope", replace(scope, calibration="different"), "scope_mismatch"),
            ("scope", replace(scope, identity="different"), "identity_changed"),
        ):
            for index in range(3):
                changed = list(reviews)
                changed[index] = replace(changed[index], **{field: value})
                cases.append((tuple(changed), code))
        for values, code in cases:
            with self.subTest(reviews=values):
                self.fault(self.make_model(reviews=values), code)
        for field, value, code in (
            ("velocity_unit", "unknown", "unknown_units"),
            ("acceleration_unit", "ticks", "unknown_units"),
            ("profile", "modern", "profile_mismatch"),
        ):
            simulation = m.Model(baseline.state.policy, replace(scope, **{field: value}))
            simulation.step(m.Event(0, "connect", scope=simulation.state.scope))
            self.fault(simulation, code)

    def test_review_schema_is_distinct_from_current_approval(self):
        baseline = self.make_model(armed=False)
        review = baseline.state.reviews[0]
        invalid = [
            replace(review, **{field: value})
            for field, value in (
                ("kind", "unknown"), ("kind", True), ("reviewed", 1),
                ("measured", None), ("source", "recorded"),
                ("reference", ""), ("reviewer", " "), ("policy_reference", ""),
                ("valid_from", True), ("valid_from", -1), ("valid_from", float("nan")),
                ("expires_at", float("inf")), ("expires_at", 1e13),
                ("expires_at", 0), ("valid_from", 20), ("valid_from", 21),
                ("scope", None),
            )
        ]
        for field in fields(m.Scope):
            for value in ("", True, None):
                invalid.append(replace(review, scope=replace(
                    review.scope, **{field.name: value})))
        for index, value in enumerate(invalid):
            with self.subTest(case=index):
                event = m.Event(0, "authorize", owner="SYNTHETIC", reference="SYNTHETIC",
                                reviews=(value,))
                target = m.Model(baseline.state.policy, baseline.state.scope)
                before = target.state
                with self.assertRaises(m.ModelError):
                    m.validate_review(value)
                with self.assertRaises(m.ModelError):
                    m.validate_event(event)
                with self.assertRaises(m.ModelError):
                    m.transition(before, event)
                self.assertIs(target.state, before)
                with self.assertRaises(m.ModelError):
                    target.step(event)
                self.fault(target, "invalid_event")
                self.assertEqual(target.records, ())
        for changed, code in (
            (replace(review, measured=False, reviewed=False), "unreviewed_evidence"),
            (replace(review, valid_from=2, expires_at=3), "stale_evidence"),
            (replace(review, valid_from=0, expires_at=1), "stale_evidence"),
            (replace(review, policy_reference="SYNTHETIC-other"), "policy_mismatch"),
            (replace(review, scope=replace(review.scope, profile="SYNTHETIC-other")),
             "profile_mismatch"),
            (replace(review, scope=replace(review.scope, velocity_unit="SYNTHETIC-other")),
             "unknown_units"),
        ):
            with self.subTest(code=code):
                m.validate_review(changed)
                target = m.Model(baseline.state.policy, baseline.state.scope)
                target.step(m.Event(1, "connect", scope=target.state.scope))
                event = m.Event(1, "authorize", owner="SYNTHETIC", reference="SYNTHETIC",
                                reviews=(changed, *baseline.state.reviews[1:]))
                m.validate_event(event)
                self.assertEqual(target.step(event).result, code)
                self.assertIs(target.records[-1].event, event)

    def test_owner_identity_profile_changes_and_cross_model_tokens(self):
        for field, value, code in (
            ("identity", "SYNTHETIC-generation-2", "identity_changed"),
            ("profile", "modern", "profile_mismatch"),
            ("calibration", "different", "scope_mismatch"),
            ("velocity_unit", "unknown", "unknown_units"),
        ):
            simulation = self.make_model()
            simulation.step(m.Event(
                0.1, "keepalive", token=simulation.state.token,
                scope=replace(simulation.state.scope, **{field: value})))
            self.fault(simulation, code)
        simulation, other = self.make_model(), self.make_model()
        for token in (other.state.token, replace(simulation.state.token)):
            target = self.make_model()
            target.step(m.Event(0.1, "arm", token=token, scope=target.state.scope))
            self.fault(target, "ownership")
        simulation.step(m.Event(
            0.1, "authorize", owner="different", reference="new", reviews=simulation.state.reviews))
        self.fault(simulation, "owner_changed")

    def test_constructing_thread_and_reentrant_lifecycle(self):
        simulation = self.make_model()
        before = simulation.state
        errors = []

        def foreign():
            try:
                simulation.step(m.Event(0.1, "stop"))
            except m.OwnershipError as error:
                errors.append(error)

        thread = Thread(target=foreign)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 1)
        self.assertIs(simulation.state, before)
        with simulation._busy:
            with self.assertRaises(m.OwnershipError):
                simulation.step(m.Event(0.1, "stop"))

    def test_deadline_boundaries_no_catchup_or_automatic_resume(self):
        for at, fault in ((1.999, None), (2, "deadman_expired"), (2.001, "deadman_expired")):
            simulation = self.make_model()
            token = simulation.state.token
            self.authorized(simulation, at, "keepalive")
            if fault:
                self.fault(simulation, fault)
                simulation.step(m.Event(at, "keepalive", token=token, scope=simulation.state.scope))
                self.fault(simulation, fault)
            else:
                self.assertEqual(simulation.state.deadman_deadline, at + 2)
        for at, fault in ((0.999, None), (1, "deadline"), (1.001, "deadline")):
            simulation = self.make_model()
            self.reply(simulation, labels=("unmatched",))
            self.fault(simulation, "unmatched")
            simulation = self.make_model()
            self.submit(simulation)
            self.write(simulation)
            self.reply(simulation, at=at)
            if fault:
                self.fault(simulation, fault)
                self.assertEqual(simulation.records[-1].event.reply.labels, ("matched_candidate",))
                self.assertEqual(simulation.state.pending.status, "failed")
            else:
                self.assertIsNone(simulation.state.pending)
        simulation = self.make_model()
        self.submit(simulation)
        self.write(simulation)
        self.reply(simulation)
        self.authorized(simulation, 1, "keepalive")
        simulation.step(m.Event(1.5, "tick"))
        self.fault(simulation, "intent_expired")
        simulation = self.make_model()
        self.authorized(simulation, 0.5, "keepalive")
        simulation.step(m.Event(0.4, "tick"))
        self.fault(simulation, "clock_regressed")

    def test_review_expiry_at_arm_and_while_armed(self):
        for armed in (False, True):
            simulation = self.make_model(armed=False)
            changed = tuple(replace(r, expires_at=1) for r in simulation.state.reviews)
            simulation = self.make_model(reviews=changed, armed=armed)
            if armed:
                simulation.step(m.Event(1, "tick"))
            else:
                self.authorized(simulation, 1, "arm")
            self.fault(simulation, "stale_evidence")

    def test_intent_numeric_sequence_freshness_and_acceleration_bounds(self):
        cases = []
        for field in ("left_velocity", "right_velocity", "acceleration", "duration", "issued_at"):
            for value in (True, False, float("nan"), float("inf"), -float("inf"), 10**30):
                cases.append(({field: value}, "invalid_number"))
        cases += [
            ({"left_velocity": 1.001}, "invalid_number"),
            ({"right_velocity": -1.001}, "invalid_number"),
            ({"acceleration": 2.001}, "invalid_number"),
            ({"duration": 3.001}, "invalid_number"),
            ({"duration": 0}, "invalid_number"),
            ({"acceleration": 0}, "invalid_number"),
            ({"sequence": True}, "sequence_error"),
            ({"sequence": 65536}, "sequence_error"),
            ({"sequence": 1}, "sequence_error"),
            ({"issued_at": 0.25}, "stale_intent"),
            ({"issued_at": 0.6}, "stale_intent"),
            ({"issued_at": 0.4, "duration": 0.1}, "intent_expired"),
            ({"left_velocity": 0.50001}, "acceleration_limit"),
        ]
        for changes, code in cases:
            with self.subTest(changes=changes):
                simulation = self.make_model()
                self.submit(simulation, **changes)
                self.fault(simulation, code)
                self.assertEqual(simulation.state.commands, 0)
                json.dumps(m.to_json(simulation.records), allow_nan=False)
        simulation = self.make_model()
        self.submit(simulation, left_velocity=1, right_velocity=-1, acceleration=2, duration=3)
        self.assertEqual(simulation.state.pending.intent.left_velocity, 1)
        self.assertEqual(simulation.state.mode, "armed")

    def test_write_uncertainty_and_one_outstanding_request(self):
        for changes, code in (
            ({"accepted_bytes": 4, "uncertain_bytes": 5}, "partial_write"),
            ({"accepted_bytes": 0, "uncertain_bytes": 9}, "uncertain_write"),
            ({"accepted_bytes": True, "uncertain_bytes": 8}, "write_contract"),
            ({"accepted_bytes": 10}, "write_contract"),
            ({"accepted_bytes": 0, "uncertain_bytes": 0}, "write_contract"),
            ({"sequence": 1}, "sequence_error"),
        ):
            with self.subTest(changes=changes):
                simulation = self.make_model()
                self.submit(simulation)
                record = self.write(simulation, **changes)
                self.fault(simulation, code)
                self.assertEqual(record.event.write.raw, b"SYNTHETIC")
                self.assertEqual(simulation.state.pending.status, "failed")
        simulation = self.make_model()
        self.submit(simulation)
        self.submit(simulation, at=0.6)
        self.fault(simulation, "outstanding_work")
        simulation = self.make_model()
        self.submit(simulation)
        self.write(simulation)
        self.write(simulation, at=0.7)
        self.fault(simulation, "duplicate_write")

    def test_intents_require_strict_latest_arm_boundary_without_resuming_queued_work(self):
        paths = ((), ("disarm",), ("transport_lost", "reset", "connect"),
                 ("host_crash", "restart", "connect"))
        for path in paths:
            for issued_at in (0.9, 1, math.nextafter(1, math.inf)):
                for old_token in (False, True):
                    with self.subTest(path=path, issued_at=issued_at, old_token=old_token):
                        target = self.make_model(armed=bool(path))
                        scope, reviews, token = (
                            target.state.scope, target.state.reviews, target.state.token)
                        queued = m.Intent(0, issued_at, 0, 0, 1, 1)
                        if path:
                            for kind in path:
                                target.step(m.Event(1, kind, **(
                                    {"scope": scope} if kind == "connect" else {})))
                                self.assertIsNone(target.state.armed_at)
                            target.step(m.Event(
                                1, "authorize", owner="SYNTHETIC-owner",
                                reference="SYNTHETIC-fresh", reviews=reviews))
                            self.assertIsNot(target.state.token, token)
                        self.authorized(target, 1, "arm")
                        self.assertEqual(target.state.armed_at, 1)
                        before = target.state
                        event = m.Event(1.1, "intent", scope=scope,
                                        token=token if old_token else before.token, intent=queued)
                        expected = ("ownership" if old_token and path else
                                    "stale_intent" if issued_at <= 1 else "accepted")
                        reduced, result = m.transition(before, event)
                        self.assertEqual(result, expected)
                        self.assertIs(target.state, before)
                        record = target.step(event)
                        self.assertEqual(record.after, reduced)
                        self.assertIs(record.event.intent, queued)
                        self.assertIs(record.before, before)
                        self.assertEqual(target.state.commands, int(expected == "accepted"))
                        self.assertEqual(target.state.pending is not None, expected == "accepted")

        for issued_at in (1, math.nextafter(1, math.inf)):
            target = self.make_model(armed=False)
            self.authorized(target, 1, "arm")
            # All lifecycle events share a timestamp: the old intention is still ambiguous.
            reviews, old = target.state.reviews, target.state.token
            queued = m.Intent(0, issued_at, 0, 0, 1, 1)
            target.step(m.Event(1, "disarm"))
            target.step(m.Event(1, "authorize", owner="SYNTHETIC-owner",
                                reference="SYNTHETIC-fresh", reviews=reviews))
            self.authorized(target, 1, "arm")
            self.assertIsNot(target.state.token, old)
            record = self.authorized(target, issued_at, "intent", intent=queued)
            self.assertEqual(record.result, "stale_intent" if issued_at == 1 else "accepted")

        target = self.make_model()
        self.submit(target)
        self.write(target)
        self.reply(target)
        record = self.submit(target, at=0.71, issued_at=0.49)
        self.assertEqual(record.result, "accepted")
        self.assertEqual(target.state.armed_at, 0)
        self.assertEqual(target.state.commands, 2)
        self.assertEqual(target.state.last_intent_at, 0.71)

    def test_reply_labels_status_candidate_and_duplicate_preserved(self):
        cases = [({"labels": (label,)}, label) for label in sorted(m.REPLY_FAULTS)]
        cases += [
            ({"request_status": "failed"}, "reply_not_delivered"),
            ({"request_status": "submitted"}, "reply_not_delivered"),
            ({"labels": ("unknown",)}, "reply_not_delivered"),
            ({"sequence": 1}, "sequence_error"),
            ({"sequence": True}, "sequence_error"),
            ({"raw": b""}, "reply_contract"),
        ]
        for changes, code in cases:
            with self.subTest(changes=changes):
                simulation = self.make_model()
                self.submit(simulation)
                self.write(simulation)
                record = self.reply(simulation, **changes)
                self.fault(simulation, code)
                self.assertEqual(record.event.reply.labels, changes.get("labels", ("matched_candidate",)))
                self.assertEqual(record.application_acknowledgment, "not_established")
        for written in (False, True):
            simulation = self.make_model()
            self.submit(simulation)
            if written:
                self.write(simulation)
            self.reply(simulation, at=0.6)
            self.fault(simulation, "ambiguous_boundary")
        simulation = self.make_model()
        self.submit(simulation)
        self.write(simulation)
        self.reply(simulation)
        self.reply(simulation, at=0.8)
        self.fault(simulation, "duplicate")

    def test_host_faults_exit_crash_stop_uncertainty_and_external_observation(self):
        for kind in (
            "host_silence", "transport_lost", "session_invalid", "watchdog_hypothesis",
            "host_exit", "host_crash", "stop", "disarm",
        ):
            with self.subTest(kind=kind):
                simulation = self.make_model()
                self.submit(simulation)
                self.write(simulation)
                token = simulation.state.token
                before = simulation.state
                record = simulation.step(m.Event(0.7, kind))
                self.assertIs(record.before, before)
                self.assertEqual(record.before.pending.status, "submitted")
                self.assertEqual(record.before.pending.write.raw, b"SYNTHETIC")
                self.assertEqual(record.before.pending.deadline, 1)
                self.assertIs(record.before.token, token)
                self.assertEqual(len(record.before.reviews), 3)
                expected = {"host_exit": "exited", "host_crash": "crashed"}.get(kind, "fault")
                self.assertEqual(simulation.state.mode, expected)
                self.assertIsNone(simulation.state.token)
                self.assertEqual(simulation.state.pending.status, "failed")
                self.assertEqual(simulation.state.physical_stop, "not_established")
                if kind == "host_crash":
                    self.assertEqual(simulation.state.cleanup, "cannot_execute_after_host_crash")
                simulation.step(m.Event(0.8, "external_stop", observation=m.ExternalStop(
                    "SYNTHETIC", "SYNTHETIC-reviewer", 0.1, 0.2, "pass")))
                self.assertEqual(simulation.state.mode, expected)
                self.assertEqual(simulation.state.physical_stop, "not_established")
                simulation.step(m.Event(0.9, "arm", scope=simulation.state.scope, token=token))
                self.assertEqual(simulation.state.mode, expected)
        for initial in ("transport_lost", "host_crash", "host_exit"):
            for later in ("host_crash", "host_exit"):
                for pending in (False, True):
                    with self.subTest(initial=initial, later=later, pending=pending):
                        target = self.make_model()
                        if pending:
                            self.submit(target)
                            self.write(target)
                        target.step(m.Event(0.7, initial))
                        terminal = target.state
                        event = m.Event(0.8, later)
                        reduced, result = m.transition(terminal, event)
                        self.assertEqual(reduced, replace(terminal, last_at=event.at))
                        self.assertNotEqual(result, "accepted")
                        record = target.step(event)
                        self.assertEqual(record.after, reduced)
                        self.assertIs(record.before, terminal)
                        self.assertIs(record.event, event)
                        self.assertEqual(record.result, result)
                        target.step(m.Event(0.9, "restart"))
                        self.assertEqual(target.state.mode, "new")
                        self.assertIsNone(target.state.token)
                        self.assertIsNone(target.state.pending)
                        self.assertEqual(target.records[-1].before.mode, terminal.mode)
        simulation = self.make_model()
        simulation.step(m.Event(2, "host_crash"))
        self.assertEqual(simulation.state.mode, "crashed")
        self.assertEqual(simulation.state.cleanup, "cannot_execute_after_host_crash")
        for outcome, elapsed, limit, code in (
            ("fail", 0.3, 0.2, "external_stop_failed"),
            ("block", None, None, "external_stop_blocked"),
            ("pass", 0.3, 0.2, "observation_limit"),
            ("pass", True, 0.2, "invalid_number"),
        ):
            target = self.make_model()
            target.step(m.Event(0.1, "external_stop", observation=m.ExternalStop(
                "SYNTHETIC-observation", "SYNTHETIC-reviewer", elapsed, limit, outcome)))
            self.fault(target, code)

    def test_explicit_reset_reconnect_restart_reauthorize_no_token_or_sequence_reuse(self):
        simulation = self.make_model()
        scope, reviews = simulation.state.scope, simulation.state.reviews
        token = simulation.state.token
        self.submit(simulation)
        simulation.step(m.Event(0.6, "transport_lost"))
        simulation.step(m.Event(0.7, "connect", scope=scope))
        self.fault(simulation, "transport_lost")
        simulation.step(m.Event(0.8, "reset"))
        self.assertEqual(simulation.state.mode, "new")
        self.assertIsNone(simulation.state.pending)
        simulation.step(m.Event(0.9, "connect", scope=scope))
        self.assertEqual(simulation.state.mode, "disarmed")
        simulation.step(m.Event(1, "arm", token=token, scope=scope))
        self.fault(simulation, "ownership")
        simulation.step(m.Event(1.1, "restart"))
        simulation.step(m.Event(1.2, "connect", scope=scope))
        simulation.step(m.Event(
            1.2, "authorize", owner="SYNTHETIC-owner", reference="SYNTHETIC-fresh", reviews=reviews))
        self.assertIsNot(simulation.state.token, token)
        self.authorized(simulation, 1.2, "arm")
        self.submit(simulation, at=1.7, sequence=0)
        self.fault(simulation, "sequence_error")
        self.assertEqual(simulation.state.commands, 1)
        self.assertEqual(simulation.records[4].event.kind, "transport_lost")
        for signal in ("stop", "disarm"):
            target = m.Model(simulation.state.policy, scope)
            target.step(m.Event(0, signal))
            self.assertEqual(target.state.mode, "new")

    def test_lifetime_budgets_invalid_schema_and_policy_numbers(self):
        policy = m.Policy("SYNTHETIC", 1, 2, 3, 2, 0.5, 0.25, max_events=5)
        simulation = self.make_model(policy=policy)
        self.authorized(simulation, 0.1, "keepalive")
        simulation.step(m.Event(0.2, "restart"))
        self.fault(simulation, "event_budget")
        self.assertEqual(len(simulation.records), 5)
        with self.assertRaises(m.ModelError):
            simulation.step(m.Event(0.3, "reset"))
        for signal, mode in (("transport_lost", "fault"), ("host_crash", "crashed"), ("host_exit", "exited")):
            target = self.make_model(policy=policy)
            target.step(m.Event(0.1, signal))
            primary = target.state.fault
            last = target.step(m.Event(0.2, "restart"))
            self.assertEqual(last.result, "event_budget")
            self.assertEqual(target.state.mode, mode)
            self.assertEqual(target.state.fault, primary)
            with self.assertRaises(m.ModelError):
                target.step(m.Event(0.3, "opcode"))
            self.assertEqual(target.state.fault, primary)
        simulation = self.make_model(policy=replace(policy, max_events=32, max_commands=1))
        self.submit(simulation)
        self.write(simulation)
        self.reply(simulation)
        self.submit(simulation, at=0.8)
        self.fault(simulation, "command_budget")
        for event in (
            m.Event(0.1, "opcode"),
            m.Event(0.1, "stop", owner="ignored"),
            m.Event(0.1, "request_write", scope=simulation.state.scope,
                    token=m.Token(0, "owner", "ref"), write=m.Write(0, b"x" * 4097, 4097, 0)),
            m.Event(0.1, "authorize", owner="owner", reference="ref", reviews=[]),
        ):
            target = self.make_model()
            with self.assertRaises(m.ModelError):
                target.step(event)
            self.fault(target, "invalid_event")
        for value in (True, float("nan"), float("inf"), -1, 0, 10**1000):
            with self.subTest(policy_value=value):
                with self.assertRaises(m.ModelError):
                    replace(policy, deadman_timeout=value)
        for at in (True, float("nan"), float("inf"), -1):
            target = self.make_model()
            target.step(m.Event(at, "tick"))
            self.fault(target, "invalid_number")
        target = self.make_model(policy=replace(policy, max_events=32, deadman_timeout=1e-20))
        self.assertEqual(target.state.mode, "armed")
        self.authorized(target, 1e-21, "keepalive")
        target.step(m.Event(target.state.deadman_deadline, "tick"))
        self.fault(target, "deadman_expired")
        for interval in (1e-20, 1e12):
            target = self.make_model(policy=replace(policy, deadman_timeout=interval), armed=False)
            self.authorized(target, 1, "arm")
            self.fault(target, "invalid_deadline")
        target = self.make_model(policy=replace(policy, max_commands=65536))
        state = replace(target.state, commands=65535)
        event = m.Event(0.5, "intent", scope=state.scope, token=state.token,
                        intent=m.Intent(65535, 0.5, 0.25, -0.25, 1, 1))
        state, result = m.transition(state, event)
        self.assertEqual(result, "accepted")
        self.assertEqual(state.commands, 65536)
        state, _ = m.transition(state, m.Event(0.6, "restart"))
        self.assertEqual(state.commands, 65536)

    def test_structural_depth_and_total_work_limits_preserve_primary_failure(self):
        baseline = self.make_model()
        event = m.Event(0.7, "application_reply", scope=baseline.state.scope,
                        token=baseline.state.token,
                        reply=m.Reply(0, b"SYNTHETIC", (), "matched"))
        labels = "matched_candidate"
        for _ in range(m.MAX_INPUT_DEPTH - 2):
            labels = (labels,)
        at_depth = replace(event, reply=replace(event.reply, labels=labels))
        m.validate_event(at_depth)
        too_deep = replace(event, reply=replace(event.reply, labels=(labels,)))

        base_nodes = 1 + sum(len(fields(cls)) for cls in (m.Event, m.Scope, m.Token, m.Reply))
        remaining = m.MAX_INPUT_NODES - base_nodes
        groups, last = divmod(remaining, 33)
        labels = ((None,) * 32,) * groups + ((None,) * (last - 1),)
        at_nodes = replace(event, reply=replace(event.reply, labels=labels))
        m.validate_event(at_nodes)
        too_wide = replace(event, reply=replace(
            event.reply, labels=(*labels[:-1], (*labels[-1], None))))

        deep_tuple, deep_dataclass = "SYNTHETIC", "SYNTHETIC"
        for _ in range(sys.getrecursionlimit() + 100):
            deep_tuple = (deep_tuple,)
            deep_dataclass = replace(baseline.state.scope, identity=deep_dataclass)
        invalid = (too_deep, too_wide,
                   replace(event, reply=replace(event.reply, labels=deep_tuple)),
                   replace(event, scope=deep_dataclass))
        for index, rejected in enumerate(invalid):
            for terminal in (None, "transport_lost", "host_crash", "host_exit"):
                with self.subTest(case=index, terminal=terminal):
                    target = self.make_model()
                    self.submit(target)
                    self.write(target)
                    if terminal is not None:
                        target.step(m.Event(0.65, terminal))
                    before, records = target.state, target.records
                    with self.assertRaises(m.ModelError):
                        m.validate_event(rejected)
                    with self.assertRaises(m.ModelError):
                        m.transition(before, rejected)
                    self.assertIs(target.state, before)
                    with self.assertRaises(m.ModelError):
                        target.step(rejected)
                    self.assertEqual(target.records, records)
                    if terminal is None:
                        self.fault(target, "invalid_event")
                        self.assertEqual(target.state.pending.status, "failed")
                        self.assertIs(target.state.pending.write, before.pending.write)
                    else:
                        self.assertIs(target.state, before)
                    json.dumps(m.to_json(target.records), allow_nan=False)


if __name__ == "__main__":
    unittest.main()
