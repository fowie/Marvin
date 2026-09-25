# Local operator API contract

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

## JSON and SSE routes

All routes bind to `127.0.0.1`. Request bodies must contain exactly the fields
listed.

| Method | Route | JSON fields |
|---|---|---|
| GET | `/api/status` | - |
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
`error` event while the runtime or any manager reports an error.

## Drive boundary

A dead-man heartbeat admits at most one fresh proved 250 ms action. Its lease is
0.75 seconds and one owner is allowed. Release, expiry, observable response
disconnect, shutdown, cancellation, or action error requests the accepted
all-zero stop primitive. Fixed-key mode composes exactly four sequential proved
250 ms actions and stops after the first failure. It is four pulses, not
calibrated distance and not uninterrupted exact one-second motion. Each injected
drive callback must retain the accepted primitive's mandatory cleanup.

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
Likewise, arecord exit `1` is accepted only after the manager's SIGINT, only
with the known `pcm_read: Interrupted system call` diagnostic, and only when the
RIFF size and final nonempty `data` chunk exactly consume the staged file.
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
