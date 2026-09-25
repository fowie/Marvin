"""Evidence-bounded end-user LED controls for Marvin."""

from dataclasses import dataclass
from pathlib import Path

from tools import marvin_legacy_led_mapper as mapper
from tools import marvin_legacy_wheel_led_blink as wheel_blink
from tools import marvin_motor_power_off_consent as consent


@dataclass(frozen=True)
class Led:
    index: int
    observed_effect: str


LEDS = {
    "left-position-0-red": Led(0, "robot-left position 0 red"),
    "left-position-0-blue": Led(1, "robot-left position 0 blue"),
    "left-position-1-red": Led(2, "robot-left position 1 red"),
    "left-position-1-blue": Led(3, "robot-left position 1 blue"),
    "left-position-2-red": Led(4, "robot-left position 2 red"),
    "left-position-2-blue": Led(5, "robot-left position 2 blue"),
    "right-position-0-red": Led(6, "robot-right position 0 red"),
    "right-position-0-blue": Led(7, "robot-right position 0 blue"),
    "right-position-1-red": Led(8, "robot-right position 1 red"),
    "right-position-1-blue": Led(9, "robot-right position 1 blue"),
    "right-position-2-red": Led(10, "robot-right position 2 red"),
    "right-position-2-blue": Led(11, "robot-right position 2 blue"),
    "wheels": Led(12, "wheel LEDs"),
    "front-left-blue": Led(13, "blue LED on the robot front-left"),
    "front-right-red": Led(14, "red LED on the robot front-right"),
    "bottom-green": Led(16, "bottom bar solid green"),
    "bottom-blue": Led(17, "bottom bar solid blue"),
}


class MarvinLEDs:
    """Fixed LED plans plus the one cleanup-owning live-proven action."""

    def __init__(self, *, run=False, expected_physical_port=None, output=None,
                 safety_confirmed=False):
        if type(run) is not bool or type(safety_confirmed) is not bool:
            raise ValueError("run and safety_confirmed must be literal booleans.")
        self.run = run
        self.expected_physical_port = expected_physical_port
        self.output = Path(output) if output is not None else None
        self.safety_confirmed = safety_confirmed

    def status(self):
        return {
            "status": "offline_evidence",
            "hardware_access": False,
            "named_full_intensity_plans": {
                name: {"index": led.index, "observed_effect": led.observed_effect}
                for name, led in LEDS.items()
            },
            "live_actions": ["wheel-blink"],
            "unsupported": [
                "arbitrary payloads", "arbitrary intensity", "arbitrary color",
                "arbitrary animation or timing", "standalone off",
                "live named steady LEDs",
            ],
            "evidence_boundary": (
                "Named effects were observed only for exclusive index-at-255 mapping "
                "rounds. Raw 0x82 is opaque and does not prove application acknowledgment."
            ),
            "missing_live_proof": (
                "One fixed named steady-LED smoke in a single process: verify the "
                "captured command-0x17 baseline, set one mapped index to 255, then "
                "restore that exact baseline once from finally after every possible "
                "setter submission, with visible effect and restoration recorded."
            ),
        }

    def full_intensity_plan(self, name):
        """Plan one exclusive mapped channel at the only tested intensity."""
        try:
            led = LEDS[name]
        except KeyError:
            raise ValueError(f"LED must be one of: {', '.join(LEDS)}.") from None
        if self.run:
            raise ValueError(
                "Live named steady LEDs are not exposed: the completed mapper used "
                "a separate restore process and does not own cleanup.")
        plan = mapper.prepare_set(led.index)
        return {
            **plan,
            "operation": "exclusive_full_intensity_plan",
            "led": name,
            "observed_effect": led.observed_effect,
            "other_payload_indices": "forced_to_zero",
            "live_execution_authorized": False,
            "off_supported": False,
        }

    def wheel_blink(self):
        """Run or plan the exact proved three-second wheel blink pilot."""
        if not self.run:
            return wheel_blink.prepare()
        if not self.expected_physical_port or self.output is None:
            raise ValueError(
                "Live wheel blink requires expected_physical_port and a new output directory.")
        if not self.safety_confirmed:
            raise ValueError(
                "Live wheel blink requires safety_confirmed=True after confirming "
                "the reviewed disconnected-load setup and external cutoff.")
        return wheel_blink.run_diagnostic(
            self.output,
            expected_physical_port=self.expected_physical_port,
            run=True,
            **dict.fromkeys(consent.WHEEL_LED_BLINK_FLAGS, True),
        )
