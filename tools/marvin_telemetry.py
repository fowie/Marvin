"""Offline, successor-only decoding of the curated packed telemetry catalogue.

Importing this module performs no file I/O. Construct TelemetryDecoder explicitly
to read the bounded local JSON schema. No recovered code or hardware libraries
are imported, and no calibration, motion, unit conversion or transport exists.
"""

import hashlib
import json
import math
from pathlib import Path
import re
import struct

from tools import marvin_protocol as protocol
from tools.marvin_stream import read_regular_file


DEFAULT_CATALOG = Path(__file__).parent.parent / "data/protocol-catalog.json"
MAX_CATALOG_BYTES = 256 * 1024
TYPE_FORMATS = {
    "byte": "B", "sbyte": "b", "ushort": "H", "short": "h",
    "uint": "I", "int": "i", "ulong": "Q", "long": "q", "float": "f", "double": "d",
}
REQUIRED_LAYOUTS = {"UnitInfo": 12, "HeartbeatDataDrive": 157, "HeartbeatDataHead": 36}
DIRECTIONS = ("received", "outgoing", "unknown")


def _reject_constant(value):
    raise ValueError(f"Nonstandard JSON numeric constant in schema: {value}")


class TelemetryDecoder:
    def __init__(self, catalog_path=DEFAULT_CATALOG):
        self.catalog_path = Path(catalog_path)
        raw = read_regular_file(self.catalog_path, max_bytes=MAX_CATALOG_BYTES)
        catalog = json.loads(raw, parse_constant=_reject_constant)
        if (not isinstance(catalog, dict) or catalog.get("byte_order") != "little-endian"
                or type(catalog.get("packing")) is not int or catalog["packing"] != 1):
            raise ValueError("Catalogue must specify little-endian packed layouts.")
        layouts = catalog.get("structs")
        if not isinstance(layouts, dict):
            raise ValueError("Catalogue is missing its struct layouts.")
        for name, expected_size in REQUIRED_LAYOUTS.items():
            layout = layouts.get(name)
            if not isinstance(layout, dict) or type(layout.get("size")) is not int or layout["size"] != expected_size:
                raise ValueError(f"Missing or invalid successor layout size: {name}")
            fields = layout.get("fields")
            if not isinstance(fields, list) or not fields or len(fields) > expected_size:
                raise ValueError(f"Invalid field list: {name}")
            offset = 0
            names = set()
            format_string = "<"
            for field in fields:
                if not isinstance(field, dict):
                    raise ValueError(f"Invalid field entry: {name}")
                field_name, field_type = field.get("name"), field.get("type")
                if not isinstance(field_name, str) or not field_name or len(field_name) > 128 or field_name in names:
                    raise ValueError(f"Invalid or duplicate field name: {name}")
                if not isinstance(field_type, str) or field_type not in TYPE_FORMATS:
                    raise ValueError(f"Unsupported packed field type: {name}.{field_name}")
                code = TYPE_FORMATS[field_type]
                size = struct.calcsize("<" + code)
                if (type(field.get("offset")) is not int or field["offset"] != offset
                        or type(field.get("size")) is not int or field["size"] != size):
                    raise ValueError(f"Invalid field offset/size: {name}.{field_name}")
                offset += size
                names.add(field_name)
                format_string += code
            if offset != expected_size or layout.get("python_struct_format") != format_string:
                raise ValueError(f"Inconsistent packed format: {name}")
            source_path, source_hash = layout.get("source_path"), layout.get("source_sha256")
            if not isinstance(source_path, str) or not source_path:
                raise ValueError(f"Missing source citation: {name}")
            if not isinstance(source_hash, str) or re.fullmatch(r"[0-9a-f]{64}", source_hash) is None:
                raise ValueError(f"Invalid source hash citation: {name}")
        self._layouts = {name: layouts[name] for name in REQUIRED_LAYOUTS}
        self.catalog_sha256 = hashlib.sha256(raw).hexdigest()

    def decode_payload(self, layout_name, payload):
        """Decode exactly one known packed payload; preserve source field spelling."""
        if layout_name not in self._layouts:
            raise ValueError(f"Unsupported telemetry layout: {layout_name}")
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes.")
        layout = self._layouts[layout_name]
        if len(payload) != layout["size"]:
            raise ValueError(f"{layout_name} requires exactly {layout['size']} bytes.")
        values = struct.unpack(layout["python_struct_format"], payload)
        fields = {}
        nonfinite = []
        for field, value in zip(layout["fields"], values):
            if isinstance(value, float) and not math.isfinite(value):
                label = "nan" if math.isnan(value) else ("+infinity" if value > 0 else "-infinity")
                nonfinite.append({
                    "name": field["name"], "value": label,
                    "raw_hex": payload[field["offset"]:field["offset"] + field["size"]].hex(),
                })
                value = label
            fields[field["name"]] = value
        return {
            "layout": layout_name, "confidence": "successor-layout-only",
            "fields": fields, "payload_hex": payload.hex(), "nonfinite_fields": nonfinite,
            "catalog_sha256": self.catalog_sha256,
            "source_path": layout["source_path"], "source_sha256": layout["source_sha256"],
            "limitation": "Source field names/units only; no calibration or live controller compatibility established.",
        }

    def interpret_packet(self, packet, *, direction="unknown"):
        """Interpret a framing-validated Packet without inferring an application ACK.

received means a caller-declared receive stream, not authenticated device origin:
it can still contain echoes. Extended status is ambiguous with host request data.
Only exact known layouts with inline status 0x80 receive telemetry field decoding.
        """
        if not isinstance(packet, protocol.Packet):
            raise TypeError("packet must be a marvin_protocol.Packet.")
        if direction not in DIRECTIONS:
            raise ValueError(f"direction must be one of {DIRECTIONS}.")
        result = {
            "direction": direction, "interpretation": "uninterpreted",
            "application_acknowledgment": "not_established",
            "payload_hex": packet.payload.hex(), "warnings": [],
        }
        if packet.command == 27:
            result["warnings"].append("Opcode 27 sets a successor handshake flag; this is not a passive-query authorization.")
        if packet.command == 29:
            result["warnings"].append("Successor opcode 29 returns synthetic sensor-info bytes and changes handshake state, not a sensor snapshot.")
        if direction != "received":
            result["warnings"].append("No response semantics assigned to outgoing or unknown-direction bytes.")
            return result
        if not packet.response_field & 0x80:
            result["interpretation"] = "request_or_extended_status"
            if len(packet.payload) >= 4:
                result["possible_extended_response_code"] = packet.response_code()
            else:
                result["warnings"].append("Fewer than four bytes for an extended status; an echoed request is possible.")
            result["warnings"].append("A zero/high-bit-clear response field also occurs in requests; no telemetry decoding.")
            return result
        result["response_code"] = packet.response_code()
        result["interpretation"] = "inline_status"
        if packet.response_field != 0x80:
            result["warnings"].append("Nonzero inline status preserved numerically; no successful-layout interpretation.")
            return result
        if packet.command == protocol.GET_CONFIG and len(packet.payload) == 108:
            result["telemetry"] = self.decode_payload("UnitInfo", packet.payload[:12])
            result["metadata_tail_hex"] = packet.payload[12:].hex()
        elif packet.command == protocol.GET_UNIT_INFO and len(packet.payload) == 12:
            result["telemetry"] = self.decode_payload("UnitInfo", packet.payload)
        elif packet.command == 1 and len(packet.payload) in (157, 36):
            name = "HeartbeatDataDrive" if len(packet.payload) == 157 else "HeartbeatDataHead"
            result["telemetry"] = self.decode_payload(name, packet.payload)
        else:
            result["warnings"].append("Unknown command/payload layout; raw bytes retained without guessed fields.")
            return result
        result["interpretation"] = "successor_layout"
        result["warnings"].append("Layout match is not proof of device origin, live compatibility, or a matched request acknowledgment.")
        return result
