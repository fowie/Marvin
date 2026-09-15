"""Linux usbmon character-device ABI, without libpcap or USB device requests.

The GETX ABI is documented in drivers/usb/mon/mon_bin.c. Only the first 32
payload bytes are copied by default, matching the initial text-based diagnostic
budget. An explicit 32..4096-byte budget supports full bounded LIVE correlation.
This implementation explicitly supports the current little-endian x86-64 host.
Binary captured-data flags are zero, not the text-format '=' marker. The kernel
currently registers 128 monitor minors (0..127); all-buses minor0 is forbidden
here. Offline event headers still carry the full uint16 bus field.
Reference: https://github.com/torvalds/linux/blob/master/drivers/usb/mon/mon_bin.c
"""

import ctypes
import fcntl
import os
from pathlib import Path
import platform
import stat
import struct
import sys


HEADER = struct.Struct("<QBBBBHBBqiiII8siiII")
GETX = 0x4018920A  # _IOW(0x92, 10, three native 64-bit fields)
STATS = 0x80089203  # _IOR(0x92, 3, two u32 fields)
PAYLOAD_LIMIT = 32
FILE_MAGIC = b"MVUSBBIN1\n"
# Linux mon_bin.c registers MON_BIN_MAX_MINOR=128 character-device minors,
# including the deliberately forbidden all-buses minor 0. The header is wider.
MAX_MONITOR_BUS = 127


class BinaryError(ValueError):
    pass


def check_abi():
    if sys.platform != "linux":
        raise BinaryError("Live binary usbmon capture is supported only on Linux.")
    if (sys.byteorder != "little" or struct.calcsize("P") != 8
            or platform.machine() not in ("x86_64", "AMD64")):
        raise BinaryError("Binary usbmon ABI is implemented only for little-endian x86-64.")


def open_monitor(busnum):
    check_abi()
    if type(busnum) is not int or not 1 <= busnum <= MAX_MONITOR_BUS:
        raise BinaryError("Linux per-bus usbmon nodes support buses 1..127, never usbmon0.")
    path = Path(f"/dev/usbmon{busnum}")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as error:
        raise BinaryError(
            f"Cannot open {path} (errno {error.errno}). Check usbmon module and "
            "capture permissions; security policies will not be changed."
        ) from error
    try:
        info = os.fstat(fd)
        if not stat.S_ISCHR(info.st_mode) or os.minor(info.st_rdev) != busnum:
            raise BinaryError("USB monitor path is not the expected per-bus character device.")
    except (OSError, BinaryError):
        os.close(fd)
        raise
    return fd


def payload_budget(value):
    if type(value) is not int or not 32 <= value <= 4096:
        raise BinaryError("Binary payload budget must be an integer from 32 to 4096.")
    return value


def read_event(fd, *, payload_limit=PAYLOAD_LIMIT):
    payload_budget(payload_limit)
    header = ctypes.create_string_buffer(HEADER.size)
    payload = ctypes.create_string_buffer(payload_limit)
    request = struct.pack("<QQQ", ctypes.addressof(header), ctypes.addressof(payload), payload_limit)
    # GETX copies into the two pointed-to buffers; it does not return their bytes
    # in the ioctl argument itself. Strong local references keep both alive.
    fcntl.ioctl(fd, GETX, request)
    raw_header = header.raw
    # Linux usbmon_packet: length at 32, len_cap at 36, setup/ISO union at 40.
    captured = struct.unpack_from("<I", raw_header, 36)[0]
    return raw_header, payload.raw[:min(captured, payload_limit)]


def read_stats(fd):
    result = bytearray(8)
    fcntl.ioctl(fd, STATS, result, True)
    queued, dropped = struct.unpack("<II", result)
    return {"queued": queued, "dropped": dropped}


def address(header):
    if len(header) != HEADER.size:
        raise BinaryError("Incomplete binary USB event header.")
    return struct.unpack_from("<H", header, 12)[0], header[11]


def to_text(header, payload, *, payload_limit=PAYLOAD_LIMIT):
    """Normalize one target event to the existing analysis format."""
    payload_budget(payload_limit)
    if len(header) != HEADER.size:
        raise BinaryError("Incomplete binary USB event header.")
    (urb, event, transfer, endpoint, device, bus, setup_flag, data_flag,
     seconds, micros, status, length, captured, setup, interval,
     start_frame, transfer_flags, descriptors) = HEADER.unpack(header)
    if (event not in (ord("S"), ord("C"), ord("E")) or transfer not in (1, 2, 3)
            or endpoint & 0x70 or not 1 <= bus <= 65535 or not 1 <= device <= 127):
        raise BinaryError("Unsupported or malformed target binary USB event.")
    if seconds < 0 or not 0 <= micros < 1000000 or length >= 2**31:
        raise BinaryError("Invalid target binary USB timestamp or transfer length.")
    if (descriptors or captured > length
            or len(payload) != min(captured, payload_limit) or len(payload) > length):
        raise BinaryError("Inconsistent target binary USB payload or ISO descriptors.")
    # mon_bin_get_data uses zero for captured bytes; '=' is a text-format marker,
    # not a binary flag (drivers/usb/mon/mon_bin.c).
    if data_flag not in (0, ord("<"), ord(">"), ord("Z"), ord("D"), ord("E")):
        raise BinaryError("Unsupported target binary USB data flag.")
    if data_flag and payload:
        raise BinaryError("Binary data flag disagrees with captured target payload.")
    kind = {1: "I", 2: "C", 3: "B"}[transfer]
    direction = "i" if endpoint & 0x80 else "o"
    prefix = f"{urb:x} {seconds * 1000000 + micros} {chr(event)} {kind}{direction}:{bus}:{device:03d}:{endpoint & 15}"
    if event == ord("S") and transfer == 2 and setup_flag == 0:
        request_type, request, value, index, setup_length = struct.unpack("<BBHHH", setup)
        details = f"s {request_type:02x} {request:02x} {value:04x} {index:04x} {setup_length:04x}"
    else:
        details = str(status)
        if kind == "I":
            details += f":{interval}"
    suffix = ""
    if payload:
        suffix = " = " + " ".join(payload[index:index + 4].hex() for index in range(0, len(payload), 4))
    elif data_flag:
        suffix = " " + chr(data_flag)
    return f"{prefix} {details} {length}{suffix}\n".encode("ascii")


def evidence_frame(header, payload, *, payload_limit=PAYLOAD_LIMIT):
    payload_budget(payload_limit)
    if len(header) != HEADER.size or len(payload) > payload_limit:
        raise BinaryError("Invalid binary evidence frame size.")
    return struct.pack("<H", len(payload)) + header + payload
