"""Offline-default LifeCam inventory and one-frame capture."""

import os
import re
from pathlib import Path
import subprocess
import tempfile

from tools.marvin_paths import new_output_path


USB_ID = ("045e", "0721")
PIXEL_FORMAT = "MJPG"
FFMPEG_FORMAT = "mjpeg"
FRAME_SIZE = "352x288"
INVENTORY_TIMEOUT_SECONDS = 5
CAPTURE_TIMEOUT_SECONDS = 10
MAX_CAPTURE_BYTES = 1024 * 1024
_USB_PATH = re.compile(r"[0-9]+-[0-9]+(?:\.[0-9]+)*\Z")


def plan(output=None):
    """Return the fixed capture contract without accessing the host."""
    return {
        "status": "offline_ready",
        "hardware_access": False,
        "usb_identity": ":".join(USB_ID),
        "topology": "operator-supplied exact physical USB path through Marvin SPARE/TI hub",
        "format": PIXEL_FORMAT,
        "size": FRAME_SIZE,
        "frames": 1,
        "output": str(output) if output is not None else None,
        "live_requirements": [
            "--run",
            "--expected-camera-usb-path",
            "--confirm-privacy",
            "new .jpg/.jpeg output path",
        ],
    }


def inventory(expected_usb_path, *, sys_usb_root=Path("/sys/bus/usb/devices"),
              sys_video_root=Path("/sys/class/video4linux"),
              dev_root=Path("/dev"), runner=subprocess.run):
    """Inspect V4L2 metadata only for nodes below the exact LifeCam USB device."""
    usb_path = _validated_usb_path(expected_usb_path)
    usb = Path(sys_usb_root) / usb_path
    identity = (_read(usb / "idVendor"), _read(usb / "idProduct"))
    if identity != USB_ID:
        raise OSError(
            f"USB {usb_path} is {identity[0]}:{identity[1]}, not the proved "
            f"{USB_ID[0]}:{USB_ID[1]} LifeCam.")
    resolved_usb = usb.resolve(strict=True)
    devices = []
    for video in sorted(Path(sys_video_root).glob("video*")):
        try:
            resolved_device = (video / "device").resolve(strict=True)
        except FileNotFoundError:
            continue
        if resolved_device != resolved_usb and resolved_usb not in resolved_device.parents:
            continue
        node = Path(dev_root) / video.name
        info = _v4l2(
            ["v4l2-ctl", "--device", str(node), "--all"], runner)
        formats = _v4l2(
            ["v4l2-ctl", "--device", str(node), "--list-formats-ext"], runner)
        metadata = _metadata(info)
        driver = metadata.get("driver name", "")
        card = metadata.get("card type", "")
        bus_info = metadata.get("bus info", "")
        rejected = (
            "rear" in card.lower()
            or "depth" in card.lower()
            or "ipu3" in (driver + " " + card).lower()
        )
        devices.append({
            "node": str(node),
            "driver": driver,
            "card": card,
            "bus_info": bus_info,
            "sysfs_usb_path": usb_path,
            "metadata_complete": bool(driver and card and bus_info),
            "matches_topology": bus_info.endswith("-" + usb_path.split("-", 1)[1]),
            "supports_capture": _supports_fixed_frame(formats),
            "rejected": rejected,
        })
    return devices


def status(*, run=False, expected_usb_path=None, **inventory_options):
    if not run:
        return plan()
    device = _select(
        inventory(expected_usb_path, **inventory_options), expected_usb_path)
    return {
        "status": "connected_read_only",
        "hardware_access": "read_only_v4l2_inventory",
        "usb_identity": ":".join(USB_ID),
        "device": device,
    }


def capture(output, *, run=False, expected_usb_path=None,
            privacy_confirmed=False, sys_usb_root=Path("/sys/bus/usb/devices"),
            sys_video_root=Path("/sys/class/video4linux"),
            dev_root=Path("/dev"), runner=subprocess.run):
    """Capture exactly one proved MJPEG frame to a new JPEG path."""
    if not run:
        return plan(output)
    if privacy_confirmed is not True:
        raise ValueError(
            "Live camera capture requires privacy_confirmed=True after confirming "
            "no bystanders or unintended private material are in view.")
    destination = new_output_path(output)
    if destination.suffix.lower() not in (".jpg", ".jpeg"):
        raise ValueError("Camera output must be a new .jpg or .jpeg file.")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    reservation = os.open(destination, flags, 0o600)
    try:
        os.close(reservation)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    stage = None
    finalized = False
    try:
        devices = inventory(
            expected_usb_path,
            sys_usb_root=sys_usb_root,
            sys_video_root=sys_video_root,
            dev_root=dev_root,
            runner=runner,
        )
        device = _select(devices, expected_usb_path)
        argv = [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-f", "v4l2", "-input_format", FFMPEG_FORMAT,
            "-video_size", FRAME_SIZE, "-i", device["node"],
            "-frames:v", "1", "-an", "-c:v", "copy",
            "-fs", str(MAX_CAPTURE_BYTES), "-f", "image2pipe", "pipe:1",
        ]
        try:
            completed = runner(
                argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=CAPTURE_TIMEOUT_SECONDS, check=False)
        except subprocess.TimeoutExpired as error:
            raise TimeoutError(
                f"ffmpeg exceeded the {CAPTURE_TIMEOUT_SECONDS}-second capture limit; "
                "no retry was attempted.") from error
        if completed.returncode:
            detail = completed.stderr or b""
            if isinstance(detail, bytes):
                detail = detail.decode(errors="replace")
            raise OSError(
                f"ffmpeg failed with exit {completed.returncode}: "
                f"{detail.strip()[-2000:] or 'no stderr'}")
        frame = completed.stdout
        if not isinstance(frame, bytes) or not frame:
            raise OSError("ffmpeg reported success without a captured frame.")
        if len(frame) > MAX_CAPTURE_BYTES:
            raise OSError(
                f"ffmpeg returned {len(frame)} bytes; limit is {MAX_CAPTURE_BYTES}.")
        stage_fd, stage_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp",
            dir=destination.parent)
        stage = Path(stage_name)
        with os.fdopen(stage_fd, "wb") as stream:
            stream.write(frame)
        os.replace(stage, destination)
        stage = None
        finalized = True
        size = len(frame)
    finally:
        if stage is not None:
            stage.unlink(missing_ok=True)
        if not finalized:
            destination.unlink(missing_ok=True)
    return {
        "status": "captured",
        "output": str(destination),
        "bytes": size,
        "usb_identity": ":".join(USB_ID),
        "usb_path": expected_usb_path,
        "device": device["node"],
        "format": PIXEL_FORMAT,
        "size": FRAME_SIZE,
        "frames": 1,
        "argv": argv,
    }


def _validated_usb_path(value):
    if not isinstance(value, str) or not _USB_PATH.fullmatch(value):
        raise ValueError(
            "Live camera access requires an exact physical USB path such as "
            "1-1.2.3; device nodes and descriptive labels are not accepted.")
    return value


def _read(path):
    try:
        return path.read_text(encoding="ascii").strip().lower()
    except FileNotFoundError as error:
        raise OSError(f"Required USB identity metadata is missing: {path}") from error


def _v4l2(argv, runner):
    try:
        completed = runner(
            argv, capture_output=True, text=True,
            timeout=INVENTORY_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired as error:
        raise TimeoutError(
            f"{argv[0]} exceeded the {INVENTORY_TIMEOUT_SECONDS}-second "
            "inventory limit.") from error
    if completed.returncode:
        detail = (completed.stderr or "").strip()[-2000:]
        raise OSError(
            f"v4l2-ctl failed for {argv[2]} with exit {completed.returncode}: "
            f"{detail or 'no stderr'}")
    return completed.stdout


def _metadata(output):
    result = {}
    for line in output.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip().lower()] = value.strip()
    return result


def _supports_fixed_frame(output):
    current_format = None
    for line in output.splitlines():
        match = re.search(r"\[\d+\]:\s+'([^']+)'", line)
        if match:
            current_format = match.group(1)
        elif current_format == PIXEL_FORMAT and re.search(
                rf"\bSize:\s+Discrete\s+{re.escape(FRAME_SIZE)}\b", line):
            return True
    return False


def _select(devices, expected_usb_path):
    candidates = [
        device for device in devices
        if not device["rejected"]
        and device["metadata_complete"]
        and device["matches_topology"]
        and device["supports_capture"]
    ]
    if len(candidates) != 1:
        raise OSError(
            f"Expected exactly one {USB_ID[0]}:{USB_ID[1]} LifeCam V4L2 node "
            f"at {expected_usb_path} supporting {PIXEL_FORMAT} {FRAME_SIZE}; "
            f"found {len(candidates)}. Refusing ambiguous or unsupported capture.")
    return candidates[0]
