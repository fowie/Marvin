"""Offline-default access to Marvin's exact supported microphone array."""

import argparse
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys

from tools import marvin_paths


USB_ID = "045e:fff0"
USB_PATH = "1-1.1.2.4"
ALSA_DEVICE = "hw:CARD=Array,DEV=0"
RATE = 16000
CHANNELS = 8
SAMPLE_BYTES = 2
MAX_DURATION_SECONDS = 5
FORMAT = "S16_LE"
CHANNEL_MAP = "FL FR FC LFE RL RR FLC FRC"
KERNEL_FIX = "d0199ae1666ff9ae2d1d568d64c3430d4c47f0e5"


class MicrophoneError(RuntimeError):
    pass


def _module_path(kernel_release):
    return Path("/lib/modules") / kernel_release / "updates/marvin/snd-usb-audio.ko"


def offline_status(*, kernel_release=None, path_exists=None):
    """Describe prerequisites without enumerating or opening audio hardware."""
    release = kernel_release or platform.release()
    exists = path_exists or Path.is_file
    module = _module_path(release)
    installed = bool(exists(module))
    return {
        "status": "offline",
        "hardware_accessed": False,
        "device_readiness": "not_checked",
        "supported_usb_id": USB_ID,
        "supported_usb_path": USB_PATH,
        "supported_alsa_device": ALSA_DEVICE,
        "native_profile": {
            "format": FORMAT,
            "rate_hz": RATE,
            "channels": CHANNELS,
            "channel_map": CHANNEL_MAP.split(),
        },
        "maximum_duration_seconds": MAX_DURATION_SECONDS,
        "kernel_release": release,
        "override_module": str(module),
        "override_module_installed": installed,
        "module_readiness": (
            "override_present_signature_and_loaded_image_not_checked"
            if installed else "override_missing"
        ),
        "kernel_prerequisite": (
            f"snd-usb-audio with {KERNEL_FIX} and the exact {USB_ID} "
            "UAC_EP_CS_ATTR_FILL_MAX quirk; the quirk alone is unsafe"
        ),
        "controller_commands": "never used; legacy opcode collisions prohibit successor power/enable commands",
    }


def _read(path, read_text):
    try:
        return read_text(Path(path)).strip()
    except OSError as error:
        raise MicrophoneError(f"Cannot read {path}: {error}") from error


def _resolve(path, resolve_path):
    try:
        return (resolve_path or (lambda value: value.resolve(strict=True)))(Path(path))
    except OSError as error:
        raise MicrophoneError(f"Cannot resolve {path}: {error}") from error


def list_device(*, device, run=False, read_text=None, runner=None,
                kernel_release=None, path_exists=None, resolve_path=None):
    """Verify and return the one supported live device without opening PCM."""
    if run is not True:
        raise ValueError("Literal run=True is required for live device enumeration.")
    if device != ALSA_DEVICE:
        raise ValueError(f"Only {ALSA_DEVICE} is supported.")
    module = offline_status(
        kernel_release=kernel_release, path_exists=path_exists)
    if not module["override_module_installed"]:
        raise MicrophoneError(
            f"Required kernel override is missing: {module['override_module']}")
    read_text = read_text or (lambda path: path.read_text())
    runner = runner or subprocess.run
    root = Path("/sys/bus/usb/devices") / USB_PATH
    identity = f"{_read(root / 'idVendor', read_text)}:{_read(root / 'idProduct', read_text)}"
    if identity.lower() != USB_ID:
        raise MicrophoneError(f"Expected {USB_ID} at {USB_PATH}; found {identity}.")
    cards = _read("/proc/asound/cards", read_text)
    card_indices = re.findall(r"(?m)^\s*(\d+)\s+\[Array\s*\]:", cards)
    if len(card_indices) != 1:
        raise MicrophoneError(
            f"Expected exactly one ALSA card named Array; found {len(card_indices)}.")
    card_index = int(card_indices[0])
    resolved_usb = _resolve(root, resolve_path)
    for sound_path in (
            Path("/sys/class/sound") / f"card{card_index}",
            Path("/sys/class/sound") / f"pcmC{card_index}D0c"):
        resolved_sound = _resolve(sound_path, resolve_path)
        if resolved_sound != resolved_usb and resolved_usb not in resolved_sound.parents:
            raise MicrophoneError(
                f"ALSA {sound_path.name} is not below reviewed USB path {USB_PATH}.")
    stream = _read("/proc/asound/Array/stream0", read_text)
    required = (
        "Format: S16_LE",
        "Channels: 8",
        "Rates: 16000",
        "Endpoint: 0x82",
    )
    missing = [item for item in required if item not in stream]
    if missing:
        raise MicrophoneError("ALSA stream does not match the supported profile: " + ", ".join(missing))
    try:
        result = runner(
            ["arecord", "-l"], capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MicrophoneError(f"Cannot enumerate ALSA capture devices: {error}") from error
    if result.returncode:
        raise MicrophoneError(result.stderr.strip() or f"arecord -l exited {result.returncode}")
    listing = re.findall(
        rf"(?m)^card\s+{card_index}:\s+Array\s+\[Microphone Array\],\s+"
        r"device\s+0:\s+",
        result.stdout,
    )
    if len(listing) != 1:
        raise MicrophoneError(
            "ALSA did not uniquely enumerate device 0 of the USB-bound "
            "Microsoft Microphone Array card.")
    return {
        "status": "ready",
        "usb_id": identity.lower(),
        "usb_path": USB_PATH,
        "alsa_device": device,
        "alsa_card_index": card_index,
        "format": FORMAT,
        "rate_hz": RATE,
        "channels": CHANNELS,
        "channel_map": CHANNEL_MAP.split(),
    }


def expected_bytes(duration_seconds, file_type):
    if type(duration_seconds) is not int or not 1 <= duration_seconds <= MAX_DURATION_SECONDS:
        raise ValueError(f"Duration must be an integer from 1 through {MAX_DURATION_SECONDS} seconds.")
    if file_type not in ("raw", "wav"):
        raise ValueError("File type must be raw or wav.")
    payload = duration_seconds * RATE * CHANNELS * SAMPLE_BYTES
    return payload + (44 if file_type == "wav" else 0)


def capture(output, *, device, duration_seconds, max_bytes, file_type,
            run=False, authorize_audio_capture=False, read_text=None, runner=None,
            kernel_release=None, path_exists=None, resolve_path=None):
    """Capture one bounded native-profile file after exact live verification."""
    if run is not True or authorize_audio_capture is not True:
        raise ValueError("Literal run=True and authorize_audio_capture=True are required.")
    limit = expected_bytes(duration_seconds, file_type)
    if type(max_bytes) is not int or max_bytes != limit:
        raise ValueError(f"max_bytes must equal the exact bound {limit}.")
    destination = marvin_paths.new_output_path(output)
    device_info = list_device(
        device=device, run=True, read_text=read_text, runner=runner,
        kernel_release=kernel_release, path_exists=path_exists,
        resolve_path=resolve_path)
    runner = runner or subprocess.run
    command = [
        "arecord", "--quiet", f"--device={device}", f"--file-type={file_type}",
        f"--format={FORMAT}", f"--rate={RATE}", f"--channels={CHANNELS}",
        f"--duration={duration_seconds}", "-",
    ]
    try:
        result = runner(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=duration_seconds + 2, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MicrophoneError(f"Bounded capture failed: {error}") from error
    if result.returncode:
        message = result.stderr.decode(errors="replace").strip()
        raise MicrophoneError(message or f"arecord exited {result.returncode}")
    if len(result.stdout) != limit:
        raise MicrophoneError(f"Capture returned {len(result.stdout)} bytes; expected exactly {limit}.")
    owned = {"output_fd": None, "output_path": None}
    finalized = False
    try:
        marvin_paths.create_private_output(destination, owned, "output")
        stream = os.fdopen(owned["output_fd"], "wb")
        owned["output_fd"] = None
        with stream:
            stream.write(result.stdout)
        finalized = True
    finally:
        if owned["output_fd"] is not None:
            os.close(owned["output_fd"])
        if owned["output_path"] is not None and not finalized:
            owned["output_path"].unlink(missing_ok=True)
    return device_info | {
        "status": "captured",
        "output": str(destination),
        "file_type": file_type,
        "duration_seconds": duration_seconds,
        "bytes": limit,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Uses ALSA only; never sends robot controller power or enable commands.")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("status", help="show offline prerequisites (default)")
    list_parser = subparsers.add_parser("list", help="verify the exact live device without opening PCM")
    list_parser.add_argument("--run", action="store_true")
    list_parser.add_argument("--device", required=True)
    capture_parser = subparsers.add_parser("capture", help="capture one bounded native-profile file")
    capture_parser.add_argument("--run", action="store_true")
    capture_parser.add_argument("--authorize-audio-capture", action="store_true")
    capture_parser.add_argument("--device", required=True)
    capture_parser.add_argument("--duration", type=int, required=True)
    capture_parser.add_argument("--max-bytes", type=int, required=True)
    capture_parser.add_argument("--type", choices=("raw", "wav"), required=True)
    capture_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command in (None, "status"):
            result = offline_status()
        elif args.command == "list":
            result = list_device(device=args.device, run=args.run)
        else:
            result = capture(
                args.output, device=args.device, duration_seconds=args.duration,
                max_bytes=args.max_bytes, file_type=args.type, run=args.run,
                authorize_audio_capture=args.authorize_audio_capture)
    except (ValueError, MicrophoneError, FileExistsError, FileNotFoundError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
