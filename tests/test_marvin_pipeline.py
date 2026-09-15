"""Process-level recording test using a pipe and synthetic USB/serial traffic."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from tools import marvin_probe, marvin_protocol, marvin_replay, marvin_session


DESCRIPTORS = bytes.fromhex("12011001020000405e044444000101020301")
IDENTITY = {
    "usb_path": "/fake/sys/1-3.3",
    "busnum": 1,
    "devnum": 10,
    "descriptors_bytes": len(DESCRIPTORS),
    "descriptors_sha256": hashlib.sha256(DESCRIPTORS).hexdigest(),
}

# Patching exists only in the test child; production exposes no bypass flags.
CHILD = """
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
from tools import marvin_usbmon
fd, output, identity_json, descriptor_hex, seconds, coordinator_stop = sys.argv[1:]
identity = json.loads(identity_json)
with patch.object(marvin_usbmon, 'read_identity', return_value=identity), \\
     patch.object(marvin_usbmon, '_cached_descriptors', return_value=bytes.fromhex(descriptor_hex)), \\
     patch.object(marvin_usbmon, '_open_usbmon', return_value=int(fd)):
    result = marvin_usbmon.capture(
        identity['usb_path'], Path(output), seconds=float(seconds), actuators_isolated=True,
        coordinator_stop=coordinator_stop == '1',
    )
sys.exit(0 if result['status'] == 'completed' else 1)
"""


class PipelineTests(unittest.TestCase):
    def test_real_recorder_process_preserves_target_events_through_serial_close(self):
        self.exercise_pipeline(probe_cr=False)

    def test_approved_byte_is_written_once_while_real_recorder_is_running(self):
        self.exercise_pipeline(probe_cr=True)

    def test_get_config_collects_fragmented_full_reply_despite_usb_snapshot_limit(self):
        self.exercise_pipeline(probe_cr=False, probe_get_config=True)

    def test_delayed_query_is_suppressed_by_early_data_under_real_recorder(self):
        self.exercise_pipeline(probe_cr=False, probe_get_config=True, probe_delay=0.04)

    def test_sensor_info_collects_full_synthetic_reply_without_treating_it_as_sensors(self):
        self.exercise_pipeline(probe_cr=False, probe_get_sensor_info=True)

    def test_sensor_info_is_suppressed_by_early_data_under_real_recorder(self):
        self.exercise_pipeline(probe_cr=False, probe_get_sensor_info=True, probe_delay=0.04)

    def test_persistent_handshake_stops_later_commands_on_rx_under_real_recorder(self):
        self.exercise_pipeline(probe_cr=False, campaign_pair=True)

    def exercise_pipeline(self, *, probe_cr, probe_get_config=False, probe_get_sensor_info=False,
                          probe_delay=0, campaign_pair=False):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "observation"
            read_fd, write_fd = os.pipe()
            os.set_blocking(read_fd, False)
            process = None
            real_popen = subprocess.Popen

            def emit(text):
                os.write(write_fd, text.encode("ascii"))

            def usb_hex(data):
                return " ".join(data[offset:offset + 4].hex() for offset in range(0, len(data), 4))

            def spawn(command, **kwargs):
                nonlocal process
                self.assertIn("--actuators-isolated", command)
                process = real_popen(
                    [sys.executable, "-c", CHILD, str(read_fd),
                     command[command.index("--output") + 1],
                     json.dumps(IDENTITY), DESCRIPTORS.hex(), "2.0", "0"],
                    cwd=Path(__file__).resolve().parent.parent,
                    pass_fds=(read_fd,), **kwargs,
                )
                return process

            query = probe_get_config or probe_get_sensor_info or campaign_pair
            sensor_response = probe_get_sensor_info or campaign_pair
            if sensor_response:
                expected_probe = marvin_protocol.get_sensor_info_request()
            elif probe_get_config:
                expected_probe = marvin_protocol.get_config_request()
            else:
                expected_probe = b"\r"
            expect_write = (probe_cr or query) and not probe_delay
            expected_writes = (
                [marvin_protocol.get_unit_info_request(), expected_probe]
                if campaign_pair else [expected_probe]
            )
            write_count = 0
            received_payload = b"\x01\x02\x03"
            if query:
                payload = bytes(range(128 if sensor_response else 108))
                body = expected_probe[:5] + b"\x80" + len(payload).to_bytes(2, "little") + payload
                received_payload = body + marvin_protocol.crc16(body).to_bytes(2, "little") + b"\xad\xde"
            fragments = [received_payload[:5], received_payload[5:22], received_payload[22:]] if query else [received_payload]
            serial = Mock()
            received = False

            def open_serial():
                self.assertTrue((output / "usb" / "ready.json").is_file())
                stamp = int(time.monotonic() * 1000000)
                emit(
                    f"f1 {stamp} S Co:1:010:0 s 21 20 0000 0000 0007 7 = 00c20100 000008\n"
                    f"f1 {stamp + 1} C Co:1:010:0 0 7 >\n"
                    f"f2 {stamp + 2} S Bi:1:010:2 -115 {256 if sensor_response else 128 if probe_get_config else 64} <\n"
                    f"fa {stamp + 3} C Ii:1:003:1 0 4 = deadbeef\n"
                )

            def read_serial(size):
                nonlocal received
                if campaign_pair and write_count < 2:
                    time.sleep(min(serial.timeout, 0.01))
                    return b""
                if not received:
                    received = True
                    stamp = int(time.monotonic() * 1000000)
                    emit(f"f2 {stamp} C Bi:1:010:2 0 {len(received_payload)} = {usb_hex(received_payload[:32])}\n")
                if fragments:
                    return fragments.pop(0)
                time.sleep(min(serial.timeout, 0.01))
                return b""

            def close_serial():
                stamp = int(time.monotonic() * 1000000)
                emit(
                    f"f3 {stamp} S Ii:1:010:1 -115:10 16 <\n"
                    f"f3 {stamp + 1} C Ii:1:010:1 -2:10 0\n"
                )

            def write_serial(payload):
                nonlocal write_count
                self.assertLess(write_count, len(expected_writes))
                self.assertEqual(payload, expected_writes[write_count])
                write_count += 1
                self.assertTrue((output / "usb" / "ready.json").is_file())
                self.assertIsNone(process.poll())
                stamp = int(time.monotonic() * 1000000)
                emit(
                    f"f4 {stamp} S Bo:1:010:3 -115 {len(payload)} = {usb_hex(payload)}\n"
                    f"f4 {stamp + 1} C Bo:1:010:3 0 {len(payload)} >\n"
                )
                return len(payload)

            serial.open.side_effect = open_serial
            serial.read.side_effect = read_serial
            serial.close.side_effect = close_serial
            serial.write.side_effect = write_serial
            baseline = {"usb": IDENTITY, "tty": "/dev/fake-marvin", "tty_rdev": 123}
            try:
                with patch.object(marvin_session, "preflight", return_value=baseline), \
                     patch.object(marvin_session, "check_identity"), \
                     patch.object(marvin_session.subprocess, "Popen", side_effect=spawn), \
                     patch.object(marvin_probe, "check_device", return_value={}), \
                     patch.object(marvin_probe, "check_port_available"), \
                     patch.object(marvin_probe.serial, "Serial", return_value=serial), \
                     patch.object(marvin_probe.fcntl, "ioctl"):
                    result = marvin_session.run_session(
                        "/dev/fake-marvin", output, seconds=0.1,
                        actuators_isolated=True, dtr=True, rts=True,
                        probe_cr=probe_cr, probe_get_config=probe_get_config,
                        probe_get_sensor_info=probe_get_sensor_info,
                        probe_schedule=(
                            (
                                marvin_probe.ScheduledWrite(0.01, expected_writes[0], "unit-info/0"),
                                marvin_probe.ScheduledWrite(0.04, expected_writes[1], "sensor-info/0"),
                                marvin_probe.ScheduledWrite(0.07, marvin_protocol.get_config_request(), "must-not-send/0"),
                            ) if campaign_pair else None
                        ),
                        allow_unknown_command=probe_cr or query,
                        allow_telemetry_state_change=sensor_response,
                        allow_line_state_trial=campaign_pair,
                        probe_delay=probe_delay,
                    )
                self.assertEqual(result["status"], "completed")
                serial.open.assert_called_once()
                serial.close.assert_called_once()
                if expect_write:
                    self.assertEqual([call.args[0] for call in serial.write.call_args_list], expected_writes)
                    self.assertEqual(result["serial"]["application_bytes_written"], sum(map(len, expected_writes)))
                    if campaign_pair:
                        self.assertEqual(result["serial"]["scheduled_writes_completed"], 2)
                        self.assertEqual(result["serial"]["transmit_status"], "suppressed_schedule_rx")
                else:
                    serial.write.assert_not_called()
                    if probe_delay:
                        self.assertEqual(result["serial"]["transmit_status"], "suppressed_pre_probe_rx")
                        self.assertEqual(result["serial"]["application_bytes_written"], 0)
                self.assertEqual((output / "serial" / "received.bin").read_bytes(), received_payload)
                if query:
                    self.assertEqual(len(received_payload), 140 if sensor_response else 120)
                    packet = marvin_protocol.decode_packet(received_payload)
                    self.assertEqual(packet.command, 29 if sensor_response else 4)
                    self.assertEqual(packet.payload, payload)
                    replay = marvin_replay.replay_capture(
                        output / "serial" / "received.bin",
                        chunks_path=output / "serial" / "chunks.jsonl",
                        evidence="synthetic",
                    )
                    self.assertEqual(replay["counts"], {"frame": 1})
                    self.assertEqual(replay["events"][0]["raw_hex"], received_payload.hex())
                    if probe_get_config:
                        self.assertEqual(replay["events"][0]["interpretation"]["telemetry"]["layout"], "UnitInfo")
                    else:
                        self.assertNotIn("telemetry", replay["events"][0]["interpretation"])
                    self.assertEqual(replay["application_acknowledgment"], "not_established")
                trace = (output / "usb" / "usbmon.txt").read_text()
                self.assertNotIn("deadbeef", trace)
                self.assertNotIn("1:003", trace)
                self.assertIn("C Ii:1:010:1 -2:10 0", trace)
                summary = json.loads((output / "usb" / "summary.json").read_text())
                self.assertEqual(summary["pairing"]["matched_completions"], 3 + (len(expected_writes) if expect_write else 0))
                self.assertEqual(summary["completion_status"]["cancellation_or_shutdown"], 1)
                self.assertEqual(summary["payload"]["text_32_byte_truncation_records"], int(query))
                self.assertEqual(summary["payload"]["uncaptured_bytes"], len(received_payload) - 32 if query else 0)
                for line in (output / "SHA256SUMS").read_text().splitlines():
                    digest, name = line.split("  ", 1)
                    self.assertEqual(hashlib.sha256((output / name).read_bytes()).hexdigest(), digest)
            finally:
                if process is not None and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                os.close(read_fd)
                os.close(write_fd)


class CoordinatedPipelineTests(unittest.TestCase):
    def test_normal_close_stops_real_recorder_at_nominal_not_extended_budget(self):
        self.exercise_close(slow_error=False)

    def test_write_timeout_slow_close_retains_cancellation_beyond_nominal_deadline(self):
        self.exercise_close(slow_error=True)

    def exercise_close(self, *, slow_error):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            output = Path(directory) / "capture"
            read_fd, write_fd = os.pipe()
            os.set_blocking(read_fd, False)
            real_popen = subprocess.Popen
            process = None
            serial = Mock()
            original_error = marvin_probe.serial.SerialTimeoutException("synthetic unknown write")
            baseline = {"usb": IDENTITY, "tty": "/dev/fake-marvin", "tty_rdev": 123}

            def spawn(command, **kwargs):
                nonlocal process
                self.assertIn("--coordinator-stop", command)
                maximum = command[command.index("--seconds") + 1]
                self.assertEqual(float(maximum), 35.1)
                process = real_popen(
                    [sys.executable, "-c", CHILD, str(read_fd),
                     command[command.index("--output") + 1],
                     json.dumps(IDENTITY), DESCRIPTORS.hex(), maximum, "1"],
                    cwd=Path(__file__).resolve().parents[1], pass_fds=(read_fd,), **kwargs,
                )
                return process

            def open_serial():
                self.assertIsNone(process.poll())
                os.write(write_fd, b"f1 1 S Bi:1:010:2 -115 64 <\n")

            def read_serial(size):
                time.sleep(0.01)
                return b""

            def close_serial():
                if slow_error:
                    ready = json.loads((output / "usb/ready.json").read_text())
                    # Return after the original nominal bound, like a blocking tty close.
                    time.sleep(max(0, ready["monotonic"] + 5.4 - time.monotonic()))
                self.assertIsNone(process.poll())
                os.write(write_fd, b"f1 2 C Bi:1:010:2 -2 0\n")

            serial.open.side_effect = open_serial
            serial.read.side_effect = read_serial
            serial.close.side_effect = close_serial
            serial.write.side_effect = original_error
            try:
                with patch.object(marvin_session, "preflight", return_value=baseline), \
                     patch.object(marvin_session, "check_identity"), \
                     patch.object(marvin_session.subprocess, "Popen", side_effect=spawn), \
                     patch.object(marvin_probe, "check_device", return_value={}), \
                     patch.object(marvin_probe, "check_port_available"), \
                     patch.object(marvin_probe.serial, "Serial", return_value=serial), \
                     patch.object(marvin_probe.fcntl, "ioctl"):
                    options = dict(seconds=0.1, actuators_isolated=True,
                                   usb_tail_seconds=5, usb_close_grace_seconds=30,
                                   probe_cr=slow_error, allow_unknown_command=slow_error)
                    if slow_error:
                        with self.assertRaises(type(original_error)) as caught:
                            marvin_session.run_session("/dev/fake-marvin", output, **options)
                        self.assertIs(caught.exception, original_error)
                    else:
                        result = marvin_session.run_session("/dev/fake-marvin", output, **options)
                        self.assertEqual(result["status"], "completed")
                serial.open.assert_called_once()
                serial.close.assert_called_once()
                if slow_error:
                    serial.write.assert_called_once_with(b"\r")
                else:
                    serial.write.assert_not_called()
                metadata = json.loads((output / "metadata.json").read_text())
                usb = json.loads((output / "usb/metadata.json").read_text())
                saved_serial = json.loads((output / "serial/metadata.json").read_text())
                summary = json.loads((output / "usb/summary.json").read_text())
                self.assertEqual(summary["pairing"]["matched_completions"], 1)
                self.assertEqual(summary["pairing"]["pending_submissions_retained"], 0)
                self.assertEqual(summary["completion_status"]["cancellation_or_shutdown"], 1)
                self.assertIn(b"C Bi:1:010:2 -2 0", (output / "usb/usbmon.txt").read_bytes())
                self.assertIn('"close_completed"', (output / "serial/events.jsonl").read_text())
                self.assertGreaterEqual(usb["elapsed_seconds"], 5.1)
                self.assertLess(usb["elapsed_seconds"], 15)
                if slow_error:
                    self.assertEqual(metadata["status"], "failed")
                    self.assertEqual(metadata["error"], str(original_error))
                    self.assertEqual(usb["status"], "interrupted")
                    self.assertEqual(usb["stop_reason"], "signal")
                    self.assertEqual(saved_serial["transmit_status"], "unknown")
                    self.assertIsNone(saved_serial["application_bytes_written"])
                    self.assertGreaterEqual(usb["elapsed_seconds"], 5.4 + marvin_session.USB_POST_CLOSE_DRAIN_SECONDS)
                else:
                    self.assertEqual(usb["status"], "completed")
                    self.assertEqual(usb["stop_reason"], "coordinator_stop")
                self.assertIsNotNone(process.poll())
                for line in (output / "SHA256SUMS").read_text().splitlines():
                    digest, name = line.split("  ", 1)
                    self.assertEqual(hashlib.sha256((output / name).read_bytes()).hexdigest(), digest)
            finally:
                if process is not None and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                os.close(read_fd)
                os.close(write_fd)


if __name__ == "__main__":
    unittest.main()
