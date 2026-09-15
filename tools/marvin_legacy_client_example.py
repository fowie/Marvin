"""In-memory example only: python -m tools.marvin_legacy_client_example.

All replies and timing are synthetic. This adapter has no device, file, serial,
USB, subprocess or network access. It is not a template for timestamping live RX.
"""

import json
import struct

from tools import marvin_legacy_protocol as protocol
from tools.marvin_legacy_client import LegacyClient, Received


class SyntheticTransport:
    def __init__(self):
        self.now = 1.0
        self.pending = None
        self.closed = False

    def clock(self):
        return self.now

    def revalidate(self, *, deadline):
        self.closed = False
        self.pending = None
        return self.identity(deadline=deadline)

    def identity(self, *, deadline):
        if self.closed:
            raise OSError("Synthetic transport is closed.")
        return b"synthetic-example-generation-1"

    def write(self, data, *, deadline):
        packet = protocol.decode_packet(data)
        spec = next(spec for spec in protocol.GETTERS.values() if spec.command == packet.command)
        if data != spec.encode(packet.sequence) or self.pending is not None:
            raise ValueError("Expected one reviewed empty synthetic request.")
        self.now += 0.01
        payload = bytes(spec.payload_bytes)
        body = b"S" + struct.pack("<HBBH", packet.sequence, packet.command, 0x80, len(payload)) + payload
        raw = body + struct.pack("<H", protocol.crc16(body)) + b"E"
        self.pending = Received(raw, self.now + 0.01, self.now + 0.01)
        return len(data)

    def read(self, max_bytes, *, deadline):
        if self.pending is None:
            self.now = deadline
            return None
        chunk = self.pending
        self.now = chunk.ended_at
        data = chunk.data[:max_bytes]
        self.pending = (Received(chunk.data[max_bytes:], chunk.started_at, chunk.ended_at)
                        if len(chunk.data) > max_bytes else None)
        return Received(data, chunk.started_at, chunk.ended_at)

    def close(self, *, deadline):
        self.closed = True


def main():
    transport = SyntheticTransport()
    session = LegacyClient(
        transport, ownership_key=b"synthetic-example",
        expected_identity=b"synthetic-example-generation-1", session_timeout=2,
        clock=transport.clock, evidence_kind="synthetic",
    )
    with session:
        config = session.request("get-config", timeout=0.5)
        power = session.request("get-power-state", timeout=0.5)
    print(json.dumps({
        "offline_only": True, "evidence_kind": "synthetic", "state": session.state,
        "accepted_bytes": session.accepted_bytes, "uncertain_bytes": session.uncertain_bytes,
        "profile": config.profile, "confidence": config.confidence,
        "application_acknowledgment": config.application_acknowledgment,
        "replies": [item.stream.packet.to_dict() for item in (config, power)],
    }, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
