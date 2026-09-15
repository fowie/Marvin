"""Screen two local LM3S5B91 flash dumps without contacting any hardware.

This is an acquisition sanity check, not a firmware authenticity check or
permission to flash. Original images are opened read-only and never modified.
Inputs must be finished local regular files, not symlinks, special files or
/dev, /proc, /sys interfaces. Images are limited to the 256-KiB flash region;
register-snapshot JSON retains its 64-KiB limit. Oversized inputs are rejected,
never truncated. Duplicate JSON keys are rejected, even with identical values.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.marvin_json import unique_object
from tools.marvin_stream import read_regular_file


FLASH_BYTES = 262144
MAX_REGISTER_SNAPSHOT_BYTES = 64 * 1024
SRAM_START = 0x20000000
# An empty descending stack may start at the one-past-the-end SRAM address.
INITIAL_STACK_POINTER_MAX = 0x20018000


def inspect_image(path):
    path = Path(path)
    image = read_regular_file(path, max_bytes=FLASH_BYTES)
    report = {
        "path": str(path.resolve()),
        "bytes": len(image),
        "sha256": hashlib.sha256(image).hexdigest(),
        "problems": [],
    }
    if len(image) != FLASH_BYTES:
        report["problems"].append("Image length is not the complete 262144-byte flash region.")
    if not image or not any(image):
        report["problems"].append("Empty or all-zero image; protected reads can return zeros.")
    elif all(byte == 255 for byte in image):
        report["problems"].append("All-FF image; not a plausible installed application.")
    if len(image) >= 8:
        stack, reset = struct.unpack_from("<II", image)
        report["initial_stack_pointer"] = f"0x{stack:08x}"
        report["reset_vector"] = f"0x{reset:08x}"
        if not SRAM_START < stack <= INITIAL_STACK_POINTER_MAX or stack % 4:
            report["problems"].append("Initial stack pointer is outside expected SRAM/alignment.")
        if not reset & 1:
            report["problems"].append("Reset vector does not have the Cortex-M Thumb bit set.")
        if not 8 <= (reset & ~1) < FLASH_BYTES:
            report["problems"].append("Reset handler is not inside the expected main-flash image.")
    else:
        report["problems"].append("Image is too short for the initial vector entries.")
    return report


def inspect_read_protection(path):
    """Interpret a separately acquired register snapshot, never FMPPE alone."""
    if path is None:
        return {
            "status": "unverified",
            "reason": "No recorded FMPRE0-3 snapshot supplied; matching hashes do not prove readability.",
        }
    path = Path(path)
    payload = read_regular_file(path, max_bytes=MAX_REGISTER_SNAPSHOT_BYTES)
    registers = json.loads(payload, object_pairs_hook=unique_object)
    if not isinstance(registers, dict):
        raise ValueError("Register snapshot must be an object containing FMPRE0 through FMPRE3.")
    values = []
    for index in range(4):
        key = f"FMPRE{index}"
        value = registers.get(key)
        if isinstance(value, str):
            try:
                value = int(value, 0)
            except ValueError as error:
                raise ValueError(f"Invalid {key}: expected a 32-bit integer or 0x-prefixed hex.") from error
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
            raise ValueError(f"Missing/invalid {key}; write-protection FMPPE is not a substitute.")
        values.append(value)
    protected = [
        index * 32 + bit
        for index, value in enumerate(values)
        for bit in range(32)
        if not (value >> bit) & 1
    ]
    return {
        "status": "read_protected" if protected else "read_enabled_in_supplied_snapshot",
        "snapshot": str(path.resolve()),
        "snapshot_sha256": hashlib.sha256(payload).hexdigest(),
        "FMPRE": [f"0x{value:08x}" for value in values],
        "protected_2kib_blocks": protected,
        "limitation": "Caller-supplied snapshot must belong to this device and acquisition.",
    }


def audit(first, second, *, registers=None):
    if Path(first).samefile(second):
        raise ValueError("Select two distinct acquisition files, not the same file twice.")
    images = [inspect_image(first), inspect_image(second)]
    protection = inspect_read_protection(registers)
    consistent = images[0]["sha256"] == images[1]["sha256"]
    problems = []
    if not consistent:
        problems.append("The two acquisition files differ; stop and investigate.")
    if any(image["problems"] for image in images):
        problems.append("Image sanity screening failed; inspect the per-image problems.")
    if protection["status"] == "read_protected":
        problems.append("The supplied FMPRE snapshot reports read-protected flash.")
    status = "needs_investigation" if problems else (
        "protection_unverified" if protection["status"] == "unverified"
        else "passed_preliminary_screen"
    )
    return {
        "status": status,
        "images": images,
        "matching_hashes": consistent,
        "read_protection": protection,
        "problems": problems,
        "limitations": [
            "No hardware was accessed; image and register-snapshot provenance are not established.",
            "A pass is not an authenticity check or authorization to program the device.",
            "Vectors are screened at base zero; unusual layouts need manual analysis, not forced disassembly.",
            "Main-flash images do not include separate USER_DBG/USER_REG nonvolatile storage.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    parser.add_argument("--registers", type=Path, help="JSON with recorded FMPRE0, FMPRE1, FMPRE2, FMPRE3")
    parser.add_argument("--output", type=Path, help="Optional new JSON report; never overwrites")
    args = parser.parse_args()
    try:
        result = audit(args.first, args.second, registers=args.registers)
        text = json.dumps(result, indent=2) + "\n"
        if args.output is not None:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(text)
        print(text, end="")
    except (OSError, ValueError, RecursionError) as error:
        print(f"Firmware screening failed: {error}", file=sys.stderr)
        return 1
    return 0 if result["status"] == "passed_preliminary_screen" else 2


if __name__ == "__main__":
    raise SystemExit(main())
