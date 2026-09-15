# Marvin

Linux tooling and engineering findings for the original Microsoft Marvin robot.

The controller's legacy S/E USB protocol has returned configuration, identity,
power-state, raw telemetry, diagnostic and servo-position replies. Physical
units, actuator behavior and emergency-stop/watchdog behavior remain unverified.

This repository is being populated through reviewed pull requests. It contains
independently written software and derived protocol facts, not recovered vendor
implementations, firmware images, private disk contents or credentials.

Actuator power and signal connections remain isolated during read-only work.
Firmware programming, resets, power switching and actuator motion are not
authorized by routine development or CI.
