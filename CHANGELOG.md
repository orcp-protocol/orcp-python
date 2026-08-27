# orcp-python — Changelog

---

## 0.2.0 — 2026-08-27

### Added

* **Stop modes and position hold.** How the robot decelerates and what it does
  afterwards are independent, so they are separate arguments:

  ```python
  robot.stop()                     # brake
  robot.stop("COAST")              # coast to rest
  robot.stop(hold=True)            # brake, then hold position
  robot.stop("COAST", hold=True)   # coast, then hold
  robot.hold()                     # shorthand
  ```

  ⚠️ **`stop()` never raises, but `stop(hold=True)` does.** A plain stop is what
  you call in a `finally:` and must not fail there. A hold can legitimately be
  *refused*, and swallowing that would leave you believing the robot is holding
  position when nothing is holding it.

* **`HoldRefused`**, raised when a controller declines a hold. It arrives two
  ways and both raise it, so you need one `except` clause: a controller that
  *has* the feature but cannot honour it now answers `OK STOP … hold=refused`
  (§STOP requires STOP to be accepted regardless of safety state), while one that
  does *not* have it rejects `hold=1` outright (§4 requires unknown parameters to
  be rejected). `.reason` preserves the difference — `UNSUPPORTED` never comes
  true, `NOT_ENABLED` might.

  ⚠️ It also catches **firmware predating the feature**, which ignores `hold=1`,
  brakes, and answers a perfectly ordinary `OK STOP mode=BRAKE` — no error,
  nothing detectable, and the caller told it is holding when it is merely braked.

* **Typed `coast` and `hold` on `StatusResponse`**, plus `is_holding` /
  `hold_broken`. ⚠️ Both are `Optional`: `hold is None` means the controller has
  no hold feature, `hold == 0` means it has one and is not holding. **Code that
  reads a missing field as "not holding" reads a controller that CANNOT hold as
  one that simply isn't.**

* **Gamepad support for `orcp-drive`** — optional extra (`pip install
  "orcp[gamepad]"`), `--gamepad` flag. Verified against a DualSense driving a
  real robot. Left stick throttles, right stick steers; ✕ stop, ○ coast, □ hold,
  △ re-enable, L1/R1 speed, Options preset.
  ⚠️ A wireless pad is a link that can drop — and **a DualSense sleeps on its own
  after a few minutes idle**. Losing it is handled twice: the loop stops the
  robot on disconnect, and teleop arms the controller's own stale-command
  watchdog for the session (restored on exit).

* `dropped_lines` — count of received lines that were neither a push nor an
  `OK`/`ERR` response.

### Fixed — three link-layer bugs, all found on real hardware

⚠️ None could be caught by the unit suite, because `MockTransport` returned whole
lines on demand. All three get worse with latency, so **worst over WiFi, on a
real robot, under streaming load.**

* **`SerialTransport` used `serial.readline()`**, which returns whatever bytes it
  has when its timeout expires — *including a partial line*. Under streaming a
  telemetry push got split and the tail, not starting with `!`, was taken for a
  command response: `ORCPError: Unexpected response: 'ttery=94% t=15764 …'` —
  the end of `! STREAM … battery=94% …`, split inside the word. Now buffers and
  emits only complete lines.
* **A response arriving before a command was sent was handed to that command.**
  Two sources: a WiFi bridge keeps its link open across host connections, so a
  new client is handed the tail of the last session; and **a command that times
  out and answers late left its reply in the queue** — so *one* slow round-trip
  desynchronised the link permanently, every command thereafter receiving the
  previous one's answer.
* **Lines that were neither a push nor `OK`/`ERR` were enqueued as responses.**
  ORCP §2.3: every response begins `OK` or `ERR`. Anything else is noise and is
  now dropped, resynchronising on the next newline.

### Changed

* Commands are sent in the spec's key=value form (`STOP mode=COAST hold=1`).
  ⚠️ Bare `STOP COAST` is **not** a supported form — MC1 accepted it for one
  release and now rejects it. This library only ever sent key=value.
