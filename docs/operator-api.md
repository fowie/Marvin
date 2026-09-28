# Local operator API and dashboard

Layer 2 extends the existing `OperatorRuntime` and its localhost-only HTTP/SSE
server. It does not open a second listener, enable CORS, or expose arbitrary
controller payloads, PWM values, durations, media options, colors, intensity,
animation, playback, upload, or transcription.

The application injects managers under the exact names `drive`, `leds`,
`microphone`, and `camera`. Controller callbacks must be bound to the same
controller owner as the sensor source. `OperatorRuntime` invokes manager actions
on its polling thread, so sensor polling pauses around every controller action
and resumes only after the action returns. A second drive request is rejected
rather than queued. Pending stop and release requests have priority.
`marvin_managers.build_managers(...)` constructs those exact names around the
accepted owner-bound drive/stop and full-state LED callbacks, so layer 3 does
not duplicate legacy protocol logic.

Layer 3 serves `/`, `/style.css`, and `/app.js` from that same listener. The
assets are packaged Python constants: there is no CDN, external asset, frontend
framework, telemetry, wildcard CORS, or second process. The server adds a
self-only CSP, `nosniff`, no-referrer, and no-store headers. Browser POSTs must
be same-origin, JSON, no larger than 4096 bytes, and match an exact route/body
schema.

Offline launch is hardware-free and leaves every live control disabled:

```sh
marvin operator serve --port 8765
```

The installed CLI starts live mode through the existing validated
preflight/ordinary-user-usbmon/private-evidence coordinator. It creates one
`ProductionControllerOwner` that provides sensor polling plus `drive_step`,
`stop`, `read_led_state`, and `write_led_state`; `managers_for_owner()` binds
those callbacks without opening a second controller path. Dependency injection
of that same interface remains available for tests. Live configuration is:

```text
--run
--expected-physical-port PORT
--evidence-root PRIVATE_DIR
--unprivileged-usbmon
--authorize-unvalidated-drive-step
--physical-left-motor-connected-to-robot-right-motor-l-connector
--physical-right-motor-connected-to-robot-left-motor-r-connector
--motor-left-connected
--motor-right-connected
--servos-isolated
--both-encoder-feedback-connected
--robot-secured-on-blocks
--operator-at-external-cutoff
--media-directory PRIVATE_DIR
--microphone-usb-path CURRENT_EXACT_PATH
--lifecam-usb-path CURRENT_EXACT_PATH
```

The evidence root and media directory must already exist, be owned by the
operator, and have no group/other permissions. Missing controller callbacks
prevent live startup. Missing media paths omit those managers, so their controls
remain visibly disabled. The CLI opens no device until all required live
configuration is present and does not fall back to a weaker serial path.
After the exclusive tty open, production observes two quiet seconds before the
single LED-baseline request. The localhost listener is not bound until that
request and manager startup succeed. A missing response still fails closed
without retry or reconnect; clients see connection refusal rather than a
startup socket that is later reset.

The production coordinator has an eight-hour wall-clock ceiling inside an
eight-hour-plus-30-second serial evidence envelope. Live polling is never
faster than two seconds. Eight hours is sustainable for idle four-getter
polling, not a guarantee of eight hours of continuous driving: sensor getters,
each three-transaction drive pulse, stops, and LED operations share one
non-wrapping sequence budget. The exact allowlist and sequence cap also bound
application bytes without a redundant second admission counter. Runtime status and the dashboard show normal and
cleanup requests remaining. Exhaustion fails closed without wrapping while
reserving 64 final transactions for mandatory stop and LED baseline restore.
The bounded adapter journal permits 256 MiB/500,000 records
and separately reserves 1 MiB/2,048 records for that cleanup. Its private binary
usbmon recorder has a further ten-second coordinated tail/close allowance. At
the session limit it shuts down the HTTP server and runtime, leaving 30 seconds
for priority stop, LED baseline restore, transport close, and evidence
finalization. Motion admission expiry, recorder/session-guard or evidence-clock
failure, stale serial/USB evidence, the normal prewrite hygiene gate, and normal
journal/application budgets cannot suppress the reserved exact all-zero write
attempt. The attempt still requires the current owner, open fd, unchanged tty
generation, and pinned USB identity; those identity failures suppress a write
to the wrong device. Every other timing/evidence fault is reported after the
attempt. Any
runtime/cleanup failure closes the listener and seals a failed result rather
than leaving a success-like dashboard running. If nonzero motor output may
have applied or its required zero cleanup is uncertain, the owner prints
`CUT_POWER_REQUIRED` directly on the parent operator terminal; the private
usbmon stderr artifact is not the only warning. Pre-motion startup and idle
failures do not print a motor-activation warning.

## JSON and SSE routes

All routes bind to `127.0.0.1`. Request bodies must contain exactly the fields
listed.

| Method | Route | JSON fields |
|---|---|---|
| GET | `/api/status` | - |
| GET | `/` | dependency-free dashboard |
| GET | `/style.css` | packaged stylesheet |
| GET | `/app.js` | packaged browser controller |
| GET | `/api/events` | - |
| GET | `/api/drive` | - |
| POST | `/api/drive/acquire` | none |
| POST | `/api/drive/heartbeat/{forward,backward,rotate-left,rotate-right}` | `lease` |
| POST | `/api/drive/release` | `lease` |
| POST | `/api/drive/fixed/{forward,backward,rotate-left,rotate-right}` | none |
| POST | `/api/drive/stop` | none |
| GET | `/api/leds` | - |
| POST | `/api/leds/{channel}/{on,off}` | none |
| POST | `/api/leds/reset` | none |
| GET | `/api/media/audio` | - |
| POST | `/api/media/audio/start` | `output`, `usb_path`, `privacy_authorized` |
| POST | `/api/media/audio/stop` | none |
| GET | `/api/media/video` | - |
| POST | `/api/media/video/capture` | `output`, `usb_path`, `privacy_authorized` |
| POST | `/api/media/video/start` | `output`, `usb_path`, `privacy_authorized` |
| POST | `/api/media/video/stop` | none |

SSE publishes the complete runtime status as `status` events and switches to an
`error` event only for a runtime failure or drive-manager error. LED and media
errors remain visible in ordinary status events, so they do not falsely signal
a transport failure or release an active dead-man lease. Each newly observed
sensor snapshot is also sent as a `sensor` event.

## Drive boundary

A dead-man heartbeat admits at most one fresh proved 250 ms action. The same
owner runs no getter while nonzero PWM may be applied. After mandatory zero
cleanup it admits at most one getter with a 150 ms failure deadline and checks
the priority queue again. A new pulse discards any incomplete getter cycle.
The browser leaves a 650 ms between-pulse interval inside the fresh 0.75-second
lease, and SSE publishes only when all four correlated getters complete between
two pulses. Each snapshot carries its earliest start, actual completion, and
duration. Proximity and unresolved raw cliff readings therefore keep advancing
without concurrent serial ownership, pulse-straddled samples, or a false
partial-snapshot timestamp. Its lease is 0.75
seconds and one owner is allowed. Release, expiry, observable response
disconnect, shutdown, cancellation, or action error requests the accepted
all-zero stop primitive. Each setter retains the accepted 500 ms response
evidence window, then the nonzero setter receives the proved 250 ms hold before
cleanup submission within the accepted absolute bound. Fixed-key mode composes
exactly four sequential proved 250 ms actions and stops after the first failure.
It is four pulses, not calibrated distance and not uninterrupted exact
one-second motion. Each injected drive callback must retain the accepted
primitive's mandatory cleanup.

## LED boundary

The manager captures one exact 18-byte baseline. A named action changes only its
mapped byte to `0` or `255` and submits the complete payload. It restores the
exact baseline on reset, shutdown, or a setter error. Raw `0x80` and `0x82`
remain opaque. Existing evidence proves exclusive individual mappings, not
combined effects, so only one named channel may be on at a time.

The 17 routes are `bottom-green`, `bottom-blue`, `wheels`,
`left-position-{0,1,2}-{red,blue}`, `right-position-{0,1,2}-{red,blue}`,
`front-left-blue`, and `front-right-red`.

## Direct-host media boundary

Audio requires the exact current operator-supplied USB sysfs path for
`045e:fff0`, the fixed `hw:CARD=Array,DEV=0` card and PCM ancestry, native
8-channel `S16_LE` at 16 kHz, WAV output, and literal privacy authorization.
Video requires the exact current path for `045e:0721`, V4L2 ancestry, MJPG
352x288, MKV output, and literal privacy authorization. One-frame capture
delegates to the existing hardened API.

Each recorder owns at most one process, passes an argv list without a shell,
hard-stops at five minutes, and reports only PID, elapsed time, output path,
state, and errors. New private mode-`0600` staged output is atomically finalized
only after graceful SIGINT or natural completion plus a successful
structural/diagnostic check. ffmpeg's documented SIGINT exit `255` is accepted
only when the manager sent that SIGINT; other nonzero exits remain failures.
arecord writes through an inherited descriptor for the already-reserved private
seekable staging file (`/proc/self/fd/N`) rather than unseekable stdout or a
reopened path, so it can rewrite RIFF/data lengths during graceful shutdown
without weakening exclusive creation. Audio publication requires exit `0`,
or exit `1` only after the manager's own graceful SIGINT, plus exact RIFF/file
length and a final nonempty `data` chunk that exactly consumes the staged file.
Video's internal duration is 299 seconds so normal mux finalization can finish
inside the manager's hard 300-second cap. Any terminate/kill escalation,
unexpected nonzero exit, failure, or shutdown removes partial files. Direct-host
video remains planned and unverified until Jetson acceptance.

## Layer 3 integration requirements

Layer 3 must provide the browser UI and the application bootstrap that injects
owner-bound proved drive/stop and full-state LED getter/setter callbacks. It must
keep a single dead-man lease, send heartbeats more frequently than 0.75 seconds,
release on pointer/key loss and page teardown, display four-pulse fixed motion
truthfully, surface every manager/SSE error, require explicit privacy consent and
current sysfs paths, and complete direct-host Jetson camera acceptance before
presenting video recording as verified.

## Supervised acceptance

1. First launch without `--run`; verify the dashboard loads at
   `http://127.0.0.1:8765/`, all controls are disabled, and no device is opened.
2. Review the exact controller, microphone, and LifeCam topology paths. Create
   separate mode-`0700` evidence and media directories.
3. Secure Marvin on blocks with both motors and both encoder-feedback paths
   connected as declared, keep the servos isolated, arrange an operator at the
   external cutoff, and start the installed CLI once with all live flags above.
   Do not retry after an identity, write, cleanup, or evidence failure.
4. Verify sensor freshness and start/stop one JSONL recording. Inspect only its
   private path and sealed evidence outside the dashboard.
5. Exercise each dead-man direction briefly. Confirm proximity and raw cliff
   timestamps continue advancing between pulses, then release by pointer/key
   and test window blur and the priority Stop control. A sensor timeout must
   fail and close the runtime; it must never publish a partial cycle as fresh.
   Treat completed transactions as protocol evidence only, not proof of
   physical stop.
6. Toggle one LED at a time and Reset to the captured baseline. Cut power on a
   mismatch or uncertain restore.
7. With explicit privacy confirmation and no bystanders, record a short WAV.
   LifeCam frame/video remain planned/unverified until separate Jetson
   acceptance; never infer acceptance from a structurally valid file.
8. Shut down once and confirm the runtime reports stopped, the drive stop and
   LED baseline restore were attempted, recorders ended, and no partial media
   remains.
