from dataclasses import replace
import json
import subprocess
import sys
from threading import Thread
import unittest
from unittest.mock import patch

from tools import marvin_legacy_client as client
from tools import marvin_legacy_protocol as protocol
from tests.test_marvin_legacy_protocol import frame


class Clock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now


class FakeTransport:
    """Synthetic, in-memory adapter; never imports or touches device interfaces."""

    def __init__(self, clock):
        self.clock = clock
        self.token = b"synthetic-connection-generation-1"
        self.incoming = []
        self.writes = []
        self.calls = []
        self.on_write = self.reply
        self.on_read = None
        self.on_identity = None
        self.on_close = None
        self.count = 10
        self.empty_advances = True

    def revalidate(self, *, deadline):
        self.calls.append(("revalidate", deadline))
        return self.token

    def identity(self, *, deadline):
        self.calls.append(("identity", deadline))
        if self.on_identity:
            self.on_identity()
        return self.token

    def enqueue(self, data, *, at=None):
        at = self.clock.now + 0.01 if at is None else at
        self.incoming.append(client.Received(data, at, at))

    def reply(self, packet):
        spec = next(spec for spec in protocol.GETTERS.values() if spec.command == packet.command)
        self.enqueue(frame(bytes(spec.payload_bytes), command=packet.command, sequence=packet.sequence))

    def write(self, data, *, deadline):
        self.calls.append(("write", deadline))
        self.writes.append(data)
        self.clock.now += 0.01
        if self.on_write:
            self.on_write(protocol.decode_packet(data))
        return self.count

    def read(self, max_bytes, *, deadline):
        self.calls.append(("read", deadline))
        if self.on_read:
            return self.on_read(max_bytes, deadline)
        if not self.incoming:
            if self.empty_advances:
                self.clock.now = min(deadline, self.clock.now + 0.1)
            return None
        chunk = self.incoming.pop(0)
        self.clock.now = max(self.clock.now, chunk.ended_at)
        if len(chunk.data) > max_bytes:
            self.incoming.insert(0, replace(chunk, data=chunk.data[max_bytes:]))
            chunk = replace(chunk, data=chunk.data[:max_bytes])
        return chunk

    def close(self, *, deadline):
        self.calls.append(("close", deadline))
        if self.on_close:
            self.on_close()


class LegacyClientTests(unittest.TestCase):
    def make_client(self, **options):
        clock = Clock()
        transport = FakeTransport(clock)
        session = client.LegacyClient(
            transport, ownership_key=b"synthetic-resource", expected_identity=transport.token,
            session_timeout=options.pop("session_timeout", 5), clock=clock,
            evidence_kind="synthetic", **options,
        )
        self.addCleanup(session.close)
        return session, transport, clock

    def assert_failed(self, session, transport, code, *, writes=1):
        self.assertEqual(session.state, "invalid")
        self.assertEqual(session.failure.code, code)
        self.assertEqual(len(transport.writes), writes)
        self.assertEqual(sum(name == "close" for name, _ in transport.calls), 1)
        with self.assertRaises(client.SessionError):
            session.request("get-config", timeout=1)
        with self.assertRaises(client.SessionError):
            session.start()
        self.assertEqual(len(transport.writes), writes)
        session.close()
        self.assertEqual(sum(name == "close" for name, _ in transport.calls), 1)

    def assert_partition(self, session, raw):
        self.assertEqual(b"".join(item.stream.raw for item in session.evidence), raw)
        offset = 0
        for item in session.evidence:
            self.assertEqual(item.stream.offset, offset)
            offset = item.stream.end_offset

    def test_persistent_four_getters_exact_shapes_and_opaque_confidence(self):
        session, transport, clock = self.make_client()
        with session:
            for sequence, (query, spec) in enumerate(protocol.GETTERS.items()):
                result = session.request(query, timeout=1, allow_telemetry_state_change=query == "get-unit-info")
                self.assertEqual(result.stream.packet.raw, frame(
                    bytes(spec.payload_bytes), command=spec.command, sequence=sequence))
                self.assertEqual(result.labels, ("matched_candidate",))
                self.assertEqual(result.profile, "marvin-legacy-se")
                self.assertEqual(result.evidence_kind, "synthetic")
                self.assertEqual(result.application_acknowledgment, "not_established")
                self.assertIn("not_authenticated", result.confidence)
                self.assertEqual(session.requests[-1].status, "matched")
                self.assertEqual(transport.writes[-1], spec.encode(sequence))
        self.assertEqual(session.state, "closed")
        self.assertEqual((session.accepted_bytes, session.uncertain_bytes), (40, 0))
        self.assertEqual(sum(name == "revalidate" for name, _ in transport.calls), 1)
        self.assertEqual(sum(name == "close" for name, _ in transport.calls), 1)
        self.assertEqual(dict(client.SETTINGS), {
            "baudrate": 57600, "bytesize": 8, "parity": "N", "stopbits": 1,
            "xonxoff": False, "rtscts": False, "dsrdtr": False, "dtr": False, "rts": False,
        })

    def test_every_fragment_boundary_and_one_byte_reads(self):
        raw = frame(bytes(108))
        for split in range(1, len(raw)):
            with self.subTest(split=split):
                session, transport, _ = self.make_client()
                transport.on_write = lambda packet: (
                    transport.enqueue(raw[:split]), transport.enqueue(raw[split:]))
                with session:
                    self.assertEqual(session.request("get-config", timeout=1).stream.raw, raw)
                self.assert_partition(session, raw)
        session, transport, _ = self.make_client(limits=client.Limits(read_size=1))
        with session:
            self.assertEqual(session.request("get-config", timeout=1).stream.raw, raw)
        self.assert_partition(session, raw)

    def test_all_coalesced_frames_and_incomplete_tail_are_preserved(self):
        session, transport, _ = self.make_client()
        reply = frame(bytes(108))
        unknown = frame(b"opaque", command=250, sequence=9)
        raw = reply + reply + unknown + b"S\x01"
        transport.on_write = lambda packet: transport.enqueue(raw)
        with session:
            session.request("get-config", timeout=1)
            self.assertEqual(len(session.evidence), 3)
            self.assertIn("duplicate", session.evidence[1].labels)
            self.assertIn("unsolicited", session.evidence[2].labels)
        self.assert_partition(session, raw)
        self.assertIn("partial", session.evidence[-1].labels)

    def test_stale_duplicate_echo_error_opcode_sequence_and_payload_cannot_match(self):
        session, transport, _ = self.make_client()
        with session:
            first = session.request("get-config", timeout=1)
            wrong = [
                first.stream.raw,
                frame(bytes(108), sequence=0, status=0x81),
                protocol.get_config_request(1),
                frame(bytes(108), sequence=1, status=0x81),
                frame(bytes(108), command=0x1B, sequence=1),
                frame(bytes(108), sequence=9),
                frame(bytes(107), sequence=1),
            ]
            good = frame(bytes(108), sequence=1)
            transport.on_write = lambda packet: transport.enqueue(b"".join(wrong) + good)
            result = session.request("get-config", timeout=1)
            self.assertEqual(result.stream.raw, good)
            labels = [set(item.labels) for item in session.evidence[1:-1]]
            for expected, actual in zip((
                {"stale", "duplicate"}, {"stale", "error_status"}, {"request_echo", "unexpected_payload"},
                {"error_status"}, {"unexpected_command"}, {"unsolicited"}, {"unexpected_payload"},
            ), labels):
                self.assertTrue(expected <= actual)
            self.assertEqual(sum("matched_candidate" in item.labels for item in session.evidence), 2)

    def test_pre_request_complete_queued_reply_is_not_accepted(self):
        session, transport, clock = self.make_client()
        old = frame(bytes(108))
        transport.enqueue(old, at=clock.now)
        transport.on_write = None
        with self.assertRaises(client.SessionError):
            with session:
                session.request("get-config", timeout=0.3)
        self.assertIn("pre_request", session.evidence[0].labels)
        self.assert_partition(session, old)
        self.assert_failed(session, transport, "deadline")

    def test_pre_request_partial_prefix_does_not_become_next_reply(self):
        session, transport, _ = self.make_client()
        first = frame(bytes(108))
        second = frame(bytes(108), sequence=1)
        transport.on_write = lambda packet: transport.enqueue(first + second[:6])
        with session:
            session.request("get-config", timeout=1)
            transport.on_write = lambda packet: transport.enqueue(second[6:] + second)
            result = session.request("get-config", timeout=1)
            self.assertIn("pre_request", session.evidence[1].labels)
            self.assertEqual(result.stream.offset, len(first) + len(second))
        self.assert_partition(session, first + second + second)

    def test_coalesced_future_reply_cannot_be_reused_for_a_later_request(self):
        session, transport, _ = self.make_client()
        raw = frame(bytes(108)) + frame(bytes(108), sequence=1)
        transport.on_write = lambda packet: transport.enqueue(raw)
        session.start()
        session.request("get-config", timeout=1)
        self.assertIn("unsolicited", session.evidence[1].labels)
        transport.on_write = None
        with self.assertRaises(client.SessionError):
            session.request("get-config", timeout=0.3)
        self.assert_partition(session, raw)
        self.assertIsNone(session.requests[-1].reply_event)
        self.assert_failed(session, transport, "deadline", writes=2)

    def test_unread_prefix_and_equal_or_during_write_timestamps_fail_closed(self):
        for at in (9, 10, 10.01):
            session, transport, _ = self.make_client()
            raw = frame(bytes(108))
            transport.enqueue(raw[:1], at=at)
            transport.on_write = lambda packet: transport.enqueue(raw[1:])
            with self.subTest(at=at), self.assertRaises(client.SessionError):
                with session:
                    session.request("get-config", timeout=0.3)
            self.assertIn("pre_request", session.evidence[0].labels)
            self.assert_failed(session, transport, "deadline")

    def test_corruption_recovery_and_nested_frames_never_match(self):
        good = frame(bytes(108))
        damaged = bytearray(good)
        damaged[-3] ^= 1
        for prefix in (bytes(damaged), b"S\x00\x00\x04\x80\xff\xff", b"noise"):
            session, transport, _ = self.make_client()
            transport.on_write = lambda packet: transport.enqueue(prefix + good)
            with self.assertRaises(client.SessionError):
                with session:
                    session.request("get-config", timeout=0.3)
            self.assertIn("ambiguous_boundary", session.evidence[-1].labels)
            self.assertFalse(any("matched_candidate" in item.labels for item in session.evidence))
            self.assert_partition(session, prefix + good)
        session, transport, _ = self.make_client()
        nested = b"S\x00\x00\x04\x80\x00\x04" + good
        transport.on_write = lambda packet: transport.enqueue(nested)
        with self.assertRaises(client.SessionError):
            with session:
                session.request("get-config", timeout=0.3)
        self.assert_partition(session, nested)
        self.assertEqual(session.evidence[0].stream.kind, "partial")

    def test_noise_after_good_reply_blocks_next_write_and_keeps_all_bytes(self):
        session, transport, _ = self.make_client()
        raw = frame(bytes(108)) + b"noise"
        transport.on_write = lambda packet: transport.enqueue(raw)
        session.start()
        session.request("get-config", timeout=1)
        with self.assertRaises(client.SessionError):
            session.request("get-config", timeout=1)
        self.assert_partition(session, raw)
        self.assert_failed(session, transport, "ambiguous_boundary")

    def test_raw_inconsistent_packet_object_cannot_match(self):
        session, transport, _ = self.make_client()
        original_feed = session._decoder.feed

        def forged_feed(data):
            return [replace(event, packet=replace(event.packet, raw=protocol.get_config_request()))
                    for event in original_feed(data)]

        with patch.object(session._decoder, "feed", side_effect=forged_feed):
            with self.assertRaises(client.SessionError):
                with session:
                    session.request("get-config", timeout=0.3)
        self.assertIn("malformed", session.evidence[0].labels)
        self.assert_failed(session, transport, "deadline")

    def test_late_reply_deadline_and_total_session_deadline(self):
        for session_timeout, timeout in ((5, 0.2), (0.2, 1)):
            session, transport, clock = self.make_client(session_timeout=session_timeout)
            transport.on_write = lambda packet: transport.enqueue(frame(bytes(108)), at=10.2)
            with self.assertRaises(client.SessionError):
                with session:
                    session.request("get-config", timeout=timeout)
            self.assertIn("late", session.evidence[0].labels)
            self.assert_failed(session, transport, "deadline")
            self.assertTrue(all(deadline <= 10.2 for name, deadline in transport.calls
                                if name in ("read", "write")))
        session, transport, clock = self.make_client(session_timeout=0.5)
        session.start()
        session.request("get-config", timeout=0.4)
        clock.now = 10.5
        with self.assertRaises(client.SessionError):
            session.request("get-config", timeout=0.4)
        self.assert_failed(session, transport, "deadline")

    def test_late_adapter_read_and_write_cannot_report_success(self):
        session, transport, clock = self.make_client()
        transport.on_read = lambda size, deadline: (
            setattr(clock, "now", deadline), client.Received(frame(bytes(108)), 10.02, 10.02))[1]
        with self.assertRaises(client.SessionError):
            with session:
                session.request("get-config", timeout=0.2)
        self.assertIn("late", session.evidence[0].labels)
        self.assert_failed(session, transport, "deadline")
        session, transport, clock = self.make_client()
        transport.on_write = lambda packet: setattr(clock, "now", 12)
        with self.assertRaises(client.SessionError):
            with session:
                session.request("get-config", timeout=1)
        self.assertEqual((session.accepted_bytes, session.uncertain_bytes), (10, 0))
        self.assertFalse(any(name == "read" for name, _ in transport.calls))
        self.assert_failed(session, transport, "deadline")

    def test_short_invalid_and_exception_writes_preserve_accounting(self):
        for count in (0, 1, 9, None, True, 1.0, -1, 11, 10**1000):
            session, transport, _ = self.make_client()
            transport.count = count
            with self.assertRaises(client.SessionError):
                with session:
                    session.request("get-config", timeout=1)
            valid_count = type(count) is int and 0 <= count < 10
            accepted = count if valid_count else 0
            self.assertEqual((session.accepted_bytes, session.uncertain_bytes), (accepted, 10 - accepted))
            self.assert_failed(session, transport, "short_write" if valid_count else "uncertain_write")
        session, transport, _ = self.make_client()
        transport.on_write = lambda packet: (_ for _ in ()).throw(OSError("write disconnected"))
        with self.assertRaisesRegex(client.SessionError, "write disconnected"):
            with session:
                session.request("get-config", timeout=1)
        self.assertEqual((session.accepted_bytes, session.uncertain_bytes), (0, 10))
        self.assert_failed(session, transport, "transport_error")

    def test_identity_changes_before_after_write_and_after_read(self):
        for boundary in ("before", "write", "read"):
            session, transport, _ = self.make_client()
            session.start()
            if boundary == "before":
                transport.token = b"changed-generation"
            elif boundary == "write":
                transport.on_write = lambda packet: setattr(transport, "token", b"changed-generation")
            else:
                def changed_read(size, deadline):
                    transport.token = b"changed-generation"
                    return client.Received(frame(bytes(108)), 10.01, 10.01)
                transport.on_read = changed_read
            with self.assertRaises(client.SessionError):
                session.request("get-config", timeout=1)
            self.assert_failed(session, transport, "identity_changed", writes=0 if boundary == "before" else 1)
            if boundary == "read":
                self.assert_partition(session, frame(bytes(108)))
        session, transport, _ = self.make_client()
        transport.token = b"changed-before-start"
        with self.assertRaises(client.SessionError):
            session.start()
        self.assert_failed(session, transport, "identity_changed", writes=0)

    def test_startup_and_identity_deadline_overruns_do_not_write(self):
        session, transport, clock = self.make_client(session_timeout=1)
        with patch.object(transport, "revalidate", side_effect=lambda **kwargs: (
                setattr(clock, "now", kwargs["deadline"]), transport.token)[1]):
            with self.assertRaises(client.SessionError):
                session.start()
        self.assert_failed(session, transport, "deadline", writes=0)
        session, transport, clock = self.make_client()
        session.start()
        transport.on_identity = lambda: setattr(clock, "now", 15)
        with self.assertRaises(client.SessionError):
            session.request("get-config", timeout=1)
        self.assert_failed(session, transport, "deadline", writes=0)

    def test_transport_loss_and_unexpected_abort_finalize_pending_bytes(self):
        for error in (OSError("read loss"), RuntimeError("adapter bug"), KeyboardInterrupt()):
            session, transport, _ = self.make_client()
            prefix = b"S\x00"
            transport.on_write = lambda packet: transport.enqueue(prefix)
            def fail_after_prefix(size, deadline):
                if transport.incoming:
                    chunk = transport.incoming.pop()
                    transport.clock.now = chunk.ended_at
                    return chunk
                raise error
            transport.on_read = fail_after_prefix
            expected = client.SessionError if isinstance(error, OSError) else type(error)
            with self.assertRaises(expected):
                with session:
                    session.request("get-config", timeout=1)
            self.assert_partition(session, prefix)
            self.assert_failed(session, transport, "transport_error" if isinstance(error, OSError) else "operation_aborted")

    def test_exclusive_resource_and_transport_ownership_and_fresh_revalidation(self):
        session, transport, clock = self.make_client()
        session.start()
        for other_transport, key in ((FakeTransport(clock), b"synthetic-resource"), (transport, b"alias")):
            other = client.LegacyClient(other_transport, ownership_key=key, expected_identity=transport.token,
                                        session_timeout=1, clock=clock)
            with self.assertRaises(client.OwnershipError):
                other.start()
            other.close()
        self.assertFalse(any(name == "close" for name, _ in transport.calls))
        session.close()
        fresh = client.LegacyClient(transport, ownership_key=b"synthetic-resource", expected_identity=transport.token,
                                    session_timeout=1, clock=clock)
        with fresh:
            fresh.request("get-config", timeout=0.5)
        self.assertEqual(sum(name == "revalidate" for name, _ in transport.calls), 2)

    def test_wrong_thread_concurrency_and_reentrant_lifecycle_are_rejected(self):
        session, transport, _ = self.make_client()
        failures = []

        def wrong_thread():
            for operation in (session.start, session.close, lambda: session.request("get-config", timeout=1)):
                try:
                    operation()
                except client.OwnershipError as error:
                    failures.append(error)

        def during_write(packet):
            thread = Thread(target=wrong_thread)
            thread.start()
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive())
            for operation in (session.start, session.close, lambda: session.request("get-config", timeout=1)):
                with self.assertRaises(client.OwnershipError):
                    operation()
            transport.reply(packet)

        transport.on_write = during_write
        with session:
            session.request("get-config", timeout=1)
        self.assertEqual(len(failures), 3)
        self.assertEqual(len(transport.writes), 1)

    def test_sequence_exhaustion_and_bounded_frozen_clock_reads(self):
        session, transport, _ = self.make_client(first_sequence=65535, limits=client.Limits(max_requests=1))
        session.start()
        session.request("get-config", timeout=1)
        self.assertEqual(protocol.decode_packet(transport.writes[0]).sequence, 65535)
        with self.assertRaises(client.SessionError):
            session.request("get-config", timeout=1)
        self.assert_failed(session, transport, "request_limit")
        session, transport, _ = self.make_client(limits=client.Limits(max_reads=3))
        transport.on_write, transport.empty_advances = None, False
        with self.assertRaises(client.SessionError):
            with session:
                session.request("get-config", timeout=1)
        self.assertEqual(sum(name == "read" for name, _ in transport.calls), 3)
        self.assert_failed(session, transport, "read_limit")

    def test_evidence_limits_reserve_tails_and_never_read_past_budget(self):
        for limits in (client.Limits(max_rx_bytes=118), client.Limits(max_events=118)):
            session, transport, _ = self.make_client(limits=limits)
            raw = b"S\x00\x00\x04\x80\x00\x04" + bytes(111)
            transport.on_write = lambda packet: transport.enqueue(raw)
            with self.assertRaises(client.SessionError):
                with session:
                    session.request("get-config", timeout=1)
            self.assert_partition(session, raw)
            self.assertEqual(len(session.evidence), 1)
            self.assert_failed(session, transport, "evidence_limit")
        session, transport, _ = self.make_client(limits=client.Limits(max_rx_bytes=118))
        session.start()
        session.request("get-config", timeout=1)
        with self.assertRaises(client.SessionError):
            session.request("get-config", timeout=1)
        self.assert_failed(session, transport, "evidence_limit")

    def test_oversized_feed_is_explicitly_rejected_without_decoder_consumption(self):
        session, transport, clock = self.make_client()
        transport.on_read = lambda size, deadline: client.Received(b"x" * (size + 1), clock.now, clock.now)
        with self.assertRaises(client.SessionError):
            with session:
                session.request("get-config", timeout=1)
        self.assertEqual(session.rejected_input_bytes, 513)
        self.assertEqual(session.evidence, ())
        self.assert_failed(session, transport, "adapter_overread")

    def test_invalid_and_regressing_timestamps_preserve_raw_but_cannot_match(self):
        raw = frame(bytes(108))
        for start, end in ((True, 10), (float("nan"), 10), (10, float("inf")),
                           (10**1000, 10), (10.02, 10.01), (11, 11)):
            session, transport, _ = self.make_client()
            transport.on_read = lambda size, deadline: client.Received(raw, start, end)
            with self.assertRaises(client.SessionError):
                with session:
                    session.request("get-config", timeout=1)
            self.assert_partition(session, raw)
            self.assertEqual(session.evidence[0].labels, ("unverified",))
            self.assertEqual(session.evidence[0].confidence, "raw_observation")
            self.assert_failed(session, transport, "adapter_timing")
        session, transport, _ = self.make_client()
        with session:
            session.request("get-config", timeout=1)
            transport.on_read = lambda size, deadline: client.Received(frame(bytes(108), sequence=1), 9, 9)
            with self.assertRaises(client.SessionError):
                session.request("get-config", timeout=1)
        self.assert_failed(session, transport, "adapter_timing", writes=2)

    def test_cleanup_diagnostics_preserve_primary_and_release_claims(self):
        def close_error():
            raise OSError("close failed")
        session, transport, _ = self.make_client()
        transport.on_close = close_error
        transport.count = 3
        with self.assertRaisesRegex(client.SessionError, "Incomplete request"):
            with session:
                session.request("get-config", timeout=1)
        self.assert_failed(session, transport, "short_write")
        self.assertEqual(session.cleanup_errors[0].code, "close_error")
        self.assertEqual((session.accepted_bytes, session.uncertain_bytes), (3, 7))
        session, transport, _ = self.make_client()
        transport.on_close = close_error
        session.start()
        with self.assertRaisesRegex(client.SessionError, "cleanup"):
            session.close()
        self.assert_failed(session, transport, "cleanup_failed", writes=0)

    def test_adapter_diagnostics_bound_raised_and_retained_messages(self):
        for error_type in (OSError, ValueError, TypeError):
            prefix = f"{error_type.__name__}: "
            for size in (0, 1023 - len(prefix), 1024 - len(prefix), 1025 - len(prefix), 100_000):
                with self.subTest(error_type=error_type.__name__, size=size):
                    session, transport, _ = self.make_client()
                    detail = "x" * size
                    with patch.object(transport, "write", side_effect=error_type(detail)):
                        with patch.object(transport, "close", side_effect=OSError("y" * 100_000)):
                            session.start()
                            with self.assertRaises(client.SessionError) as raised:
                                session.request("get-config", timeout=1)
                    expected = (prefix + detail)[:1024]
                    self.assertEqual(str(raised.exception), expected)
                    self.assertEqual(raised.exception.args, (expected,))
                    self.assertEqual(session.failure.message, expected)
                    self.assertEqual(raised.exception.code, session.failure.code)
                    self.assertEqual(session.failure.code,
                                     "transport_error" if error_type is OSError else "adapter_contract")
                    self.assertEqual(session.cleanup_errors[0].message, "y" * 1024)
                    self.assertEqual(session.state, "invalid")
                    self.assertEqual((session.accepted_bytes, session.uncertain_bytes), (0, 10))

    def test_invalid_close_results_invalidate_without_masking_primary_failure(self):
        for result in (True, False, 0, 1, "", b"", [], {}, object()):
            for primary_failure in (False, True):
                with self.subTest(result=result, primary_failure=primary_failure):
                    session, transport, _ = self.make_client()
                    with patch.object(transport, "close", return_value=result) as close:
                        session.start()
                        with self.assertRaises(client.SessionError) as raised:
                            if primary_failure:
                                transport.count = 3
                                session.request("get-config", timeout=1)
                            else:
                                session.close()
                        expected_code = "short_write" if primary_failure else "cleanup_failed"
                        self.assertEqual(raised.exception.code, expected_code)
                        self.assertEqual(session.failure.code, expected_code)
                        self.assertEqual(session.state, "invalid")
                        self.assertEqual(session.cleanup_errors, (
                            client.Failure("close_result", "Adapter close must return exactly None."),
                        ))
                        self.assertEqual((session.accepted_bytes, session.uncertain_bytes),
                                         (3, 7) if primary_failure else (0, 0))
                        session.close()
                        close.assert_called_once()
                    # A failed close releases local claims, but a new session must revalidate.
                    fresh, _, _ = self.make_client()
                    with fresh:
                        self.assertEqual(fresh.state, "active")

    def test_cleanup_deadline_and_caller_exception(self):
        session, transport, clock = self.make_client()
        transport.on_close = lambda: setattr(clock, "now", clock.now + 2)
        session.start()
        with self.assertRaises(client.SessionError):
            session.close()
        self.assertEqual(session.cleanup_errors[0].code, "close_deadline")
        session, transport, _ = self.make_client()
        transport.on_close = lambda: (_ for _ in ()).throw(OSError("cleanup failed"))
        with self.assertRaisesRegex(LookupError, "caller failed"):
            with session:
                raise LookupError("caller failed")
        self.assert_failed(session, transport, "context_aborted", writes=0)

    def test_interrupted_close_invalidates_and_attempts_cleanup_once(self):
        for stage in ("finish", "close"):
            for error in (LookupError("cleanup programming error"), KeyboardInterrupt()):
                with self.subTest(stage=stage, error=type(error).__name__):
                    session, transport, _ = self.make_client()
                    session.start()
                    with patch.object(transport, "close", side_effect=error if stage == "close" else None) as close:
                        with patch.object(session, "_finish", side_effect=error if stage == "finish" else None):
                            with self.assertRaises(type(error)) as raised:
                                session.close()
                        self.assertIs(raised.exception, error)
                        self.assertEqual(session.state, "invalid")
                        self.assertEqual(session.failure.code, "close_aborted")
                        session.close()
                        close.assert_called_once()
                        with self.assertRaises(client.SessionError):
                            session.request("get-config", timeout=1)
                    fresh, _, _ = self.make_client()
                    with fresh:
                        self.assertEqual(fresh.state, "active")

    def test_finish_error_preserved_when_adapter_close_reports_failure(self):
        session, transport, _ = self.make_client()
        session.start()
        with patch.object(session, "_finish", side_effect=LookupError("finish bug")):
            with patch.object(transport, "close", side_effect=OSError("close failed")) as close:
                with self.assertRaisesRegex(LookupError, "finish bug"):
                    session.close()
                close.assert_called_once()
        self.assertEqual(session.state, "invalid")
        self.assertEqual(session.failure.code, "close_aborted")
        self.assertEqual(session.cleanup_errors[0].code, "close_error")

    def test_close_failure_after_deadline_retains_both_diagnostics(self):
        for primary_failure in (False, True):
            for error in (OSError("late close"), LookupError("late programming error"), KeyboardInterrupt()):
                with self.subTest(primary_failure=primary_failure, error=type(error).__name__):
                    session, transport, clock = self.make_client()
                    session.start()

                    def late_close(*, deadline):
                        clock.now = deadline
                        raise error

                    with patch.object(transport, "close", side_effect=late_close) as close:
                        expected_type = client.SessionError if primary_failure or isinstance(error, OSError) else type(error)
                        with self.assertRaises(expected_type):
                            if primary_failure:
                                transport.count = 3
                                session.request("get-config", timeout=1)
                            else:
                                session.close()
                        codes = [item.code for item in session.cleanup_errors]
                        self.assertIn("close_deadline", codes)
                        if isinstance(error, OSError):
                            self.assertEqual(codes, ["close_error", "close_deadline"])
                            self.assertEqual(session.cleanup_errors[0].message, "late close")
                        elif primary_failure:
                            self.assertIn("cleanup_aborted", codes)
                        self.assertEqual(session.state, "invalid")
                        self.assertEqual(session.failure.code,
                                         "short_write" if primary_failure else (
                                             "cleanup_failed" if isinstance(error, OSError) else "close_aborted"))
                        session.close()
                        close.assert_called_once()

    def test_finalization_failures_never_replace_an_existing_exception(self):
        session, transport, _ = self.make_client()
        original = LookupError("caller failure")
        with patch.object(session, "_finish", side_effect=RuntimeError("finish failure")):
            with patch.object(transport, "close", side_effect=KeyboardInterrupt("close interrupted")) as close:
                with self.assertRaises(LookupError) as raised:
                    with session:
                        raise original
                close.assert_called_once()
        self.assertIs(raised.exception, original)
        self.assertEqual(session.state, "invalid")
        self.assertEqual(session.failure.code, "context_aborted")
        self.assertEqual([item.code for item in session.cleanup_errors], ["finish_error", "cleanup_aborted"])

    def test_close_failure_inside_handled_exception_still_raises(self):
        session, transport, _ = self.make_client()
        session.start()
        try:
            raise LookupError("already handled")
        except LookupError:
            with patch.object(transport, "close", side_effect=OSError("close failed")):
                with self.assertRaises(client.SessionError) as raised:
                    session.close()
        self.assertEqual(raised.exception.code, "cleanup_failed")
        self.assertEqual(session.state, "invalid")

    def test_stale_sequence_lookup_never_scans_or_copies_history(self):
        class NoHistoryScan(list):
            def __iter__(self):
                raise AssertionError("Request history must not be scanned for correlation.")

            def __getitem__(self, key):
                if isinstance(key, slice):
                    raise AssertionError("Request history must not be copied for correlation.")
                return super().__getitem__(key)

        session, transport, _ = self.make_client(
            first_sequence=2000, session_timeout=60, limits=client.Limits(max_requests=1024),
        )
        session._requests = NoHistoryScan()
        with session:
            for _ in range(1023):
                session.request("get-power-state", timeout=1)
            old = frame(bytes(2), command=0x0E, sequence=2000)
            unrelated = frame(bytes(2), command=0x0E, sequence=1999)
            transport.on_write = lambda packet: (
                transport.enqueue(old + unrelated), transport.reply(packet))
            result = session.request("get-power-state", timeout=1)
            self.assertEqual(result.stream.packet.sequence, 3023)
            self.assertIn("stale", session.evidence[-3].labels)
            self.assertIn("duplicate", session.evidence[-3].labels)
            self.assertIn("unsolicited", session.evidence[-2].labels)

    def test_cleanup_runtime_error_does_not_mask_primary_or_leak_claim(self):
        session, transport, _ = self.make_client()
        transport.count = 1
        transport.on_close = lambda: (_ for _ in ()).throw(RuntimeError("adapter close bug"))
        with self.assertRaisesRegex(client.SessionError, "Incomplete request"):
            with session:
                session.request("get-config", timeout=1)
        self.assert_failed(session, transport, "short_write")
        self.assertEqual(session.cleanup_errors[0].message, "adapter close bug")

    def test_invalid_clock_and_bad_read_contracts_never_deliver_success(self):
        for value in (True, float("nan"), float("inf"), 10**1000):
            session, transport, clock = self.make_client()
            session.start()
            def bad_clock_read(size, deadline):
                clock.now = value
                return client.Received(frame(bytes(108)), 10.02, 10.02)
            transport.on_read = bad_clock_read
            with self.assertRaises(client.SessionError):
                session.request("get-config", timeout=1)
            self.assert_partition(session, frame(bytes(108)))
            self.assertEqual(session.evidence[0].labels, ("unverified",))
            self.assertEqual(session.evidence[0].application_acknowledgment, "not_established")
            self.assert_failed(session, transport, "adapter_contract")
            self.assertTrue(session.cleanup_errors)
        for result in (b"unmarked bytes", client.Received(b"", 10, 10),
                       client.Received(bytearray(b"mutable"), 10, 10)):
            session, transport, _ = self.make_client()
            transport.on_read = lambda size, deadline: result
            with self.assertRaises(client.SessionError):
                with session:
                    session.request("get-config", timeout=1)
            self.assert_failed(session, transport, "adapter_contract")

    def test_offline_default_and_complete_synthetic_example(self):
        result = subprocess.run(
            [sys.executable, "-B", "-m", "tools.marvin_legacy_client"], capture_output=True, text=True, check=True)
        self.assertEqual((result.stdout, result.stderr), ("", ""))
        result = subprocess.run(
            [sys.executable, "-B", "-m", "tools.marvin_legacy_client_example"],
            capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        self.assertTrue(data["offline_only"])
        self.assertEqual(data["state"], "closed")
        self.assertEqual(data["evidence_kind"], "synthetic")
        self.assertEqual((data["accepted_bytes"], data["uncertain_bytes"]), (20, 0))
        self.assertEqual([reply["command"] for reply in data["replies"]], [4, 0x0E])
        self.assertEqual(data["application_acknowledgment"], "not_established")

    def test_all_numeric_validators_and_getunitinfo_consent_fail_before_io(self):
        for bad in (True, False, float("nan"), float("inf"), -float("inf"), 10**1000, -1, 0, "1", None):
            for name in ("session_timeout", "cleanup_timeout"):
                with self.assertRaises(ValueError):
                    self.make_client(**{name: bad})
            for name in ("max_requests", "max_rx_bytes", "max_events", "max_reads", "read_size"):
                with self.assertRaises(ValueError):
                    client.Limits(**{name: bad})
        for bad in (True, -1, 65536, 10**1000, 1.0):
            with self.assertRaises(ValueError):
                self.make_client(first_sequence=bad)
        with self.assertRaisesRegex(ValueError, "reuse"):
            self.make_client(first_sequence=65535)
        session, transport, _ = self.make_client()
        with session:
            calls = len(transport.calls)
            for query in ("reset", "get-sensor-info", "get-log", 4, True, None):
                with self.assertRaises(ValueError):
                    session.request(query, timeout=1)
            for bad in (True, False, float("nan"), float("inf"), 10**1000, -1, 0, "1", None):
                with self.assertRaises(ValueError):
                    session.request("get-config", timeout=bad)
            for consent in (False, None, 1, "yes"):
                with self.assertRaises(ValueError):
                    session.request("get-unit-info", timeout=1, allow_telemetry_state_change=consent)
            self.assertEqual(len(transport.calls), calls)
            session.request("get-unit-info", timeout=1, allow_telemetry_state_change=True)

    def test_clock_regression_invalidates_before_more_writes(self):
        session, transport, clock = self.make_client()
        session.start()
        clock.now = 9
        with self.assertRaises(client.SessionError):
            session.request("get-config", timeout=1)
        self.assert_failed(session, transport, "clock_regressed", writes=0)
        self.assertTrue(session.cleanup_errors)

    def test_close_clock_regressions_keep_raised_and_retained_failure_consistent(self):
        for stage in ("before_close", "after_close", "close_error"):
            with self.subTest(stage=stage):
                session, transport, clock = self.make_client()
                session.start()
                if stage == "before_close":
                    clock.now = 9
                else:
                    def regress_on_close():
                        clock.now = 9
                        if stage == "close_error":
                            raise OSError("close failed")
                    transport.on_close = regress_on_close
                with self.assertRaises(client.SessionError) as raised:
                    session.close()
                self.assertEqual(raised.exception.code, "cleanup_failed")
                self.assertEqual(session.failure.code, raised.exception.code)
                self.assertEqual(session.failure.message, str(raised.exception))
                self.assertEqual(session.cleanup_errors.count(
                    client.Failure("cleanup_clock", "Monotonic clock moved backwards.")), 1)
                if stage == "close_error":
                    self.assertEqual(session.cleanup_errors[0], client.Failure("close_error", "close failed"))
                self.assert_failed(session, transport, "cleanup_failed", writes=0)

    def test_cleanup_clock_regression_preserves_prior_request_failure(self):
        session, transport, clock = self.make_client()
        transport.count = 3
        transport.on_write = lambda packet: setattr(clock, "now", 9)
        with self.assertRaises(client.SessionError) as raised:
            with session:
                session.request("get-config", timeout=1)
        self.assertEqual(raised.exception.code, "short_write")
        self.assertEqual(session.failure.code, raised.exception.code)
        self.assertEqual(session.failure.message, str(raised.exception))
        self.assertEqual(session.cleanup_errors, (
            client.Failure("cleanup_clock", "Monotonic clock moved backwards."),
        ))
        self.assertEqual((session.accepted_bytes, session.uncertain_bytes), (3, 7))
        self.assert_failed(session, transport, "short_write")


if __name__ == "__main__":
    unittest.main()
