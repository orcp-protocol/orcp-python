# orcp

Python client library for the [Open Robot Control Protocol (ORCP)](https://github.com/orcp-protocol/orcp).

Communicate with ORCP-compliant motor controllers over USB serial or WiFi using a clean, Pythonic API.

## Installation

```bash
pip install orcp
```

## Quick start

```python
from orcp import ORCP

with ORCP('socket://192.168.4.1:3333') as robot:
    robot.ping()
    robot.cmd_vel(v=0.2, w=0.0)   # 0.2 m/s forward
    import time; time.sleep(2)
    robot.stop()
```

## Connection options

```python
ORCP('/dev/cu.usbmodemXXXX')                  # USB serial (auto baud)
ORCP('/dev/cu.usbmodemXXXX', baudrate=115200)  # Explicit baud rate
ORCP('socket://192.168.4.1:3333')              # WiFi bridge
```

## Teleop demo (`orcp-drive`)

`orcp-drive` is a keyboard tele-operation app — drive any ORCP controller around
with the WASD/arrow keys. It's both a handy test tool and a worked example of the
library: it uses `CMD_VEL`, the safety presets, the enable gate, a background
heartbeat, and live telemetry streaming all together.

Install it (the `teleop` extra just adds curses support on Windows — macOS and
Linux need nothing extra):

```bash
pip install "orcp[teleop]"
```

Run it against hardware, a WiFi bridge, or — with **no hardware at all** — the
[reference simulator](https://github.com/orcp-protocol/orcp-sim):

```bash
orcp-drive /dev/cu.usbmodemXXXX        # USB (macOS; /dev/ttyACM0 Linux, COM3 Windows)
orcp-drive socket://192.168.4.1:3333    # WiFi / TCP

# No robot handy? Drive the simulator instead:
pip install orcp-sim
orcp-sim --link /tmp/orcp &             # terminal 1: a virtual ORCP controller
orcp-drive /tmp/orcp                    # terminal 2: drive it
```

### Controls

| Key | Action |
|-----|--------|
| `W` / `↑` | Forward |
| `S` / `↓` | Reverse |
| `A` / `←` | Spin left |
| `D` / `→` | Spin right |
| `Q` / `E` | Arc forward-left / forward-right |
| `Space` | Stop |
| `+` / `-` | Increase / decrease speed |
| `M` | Toggle SLOW ↔ NORMAL (NORMAL auto-enables and starts a background heartbeat) |
| `R` | Re-enable the motors after a fault is cleared (sends `ENABLE ON`) |
| `Esc` | Quit (always returns to a safe, stopped state) |

### Faults & recovery

The teleop shows the controller's **active fault** under the battery line (e.g.
`FAULT: ENCODER_STALL — press [R] to re-enable`). Fault behaviour follows the ORCP
safety model (spec §5): when the controller trips a fault it **stops the motors and
latches the fault** — it does **not** silently auto-recover when the cause goes
away. That's deliberate: a machine must not become ready-to-move again on its own.

Faults you may see on real hardware:

| Fault | Cause | Recover by |
|-------|-------|------------|
| `ESTOP` | Emergency-stop loop opened | Close the loop, then press `R` |
| `ENCODER_STALL` | Driven against an obstacle (wheels commanded but can't turn) — this is motor protection, not a bug | Back the robot off, then press `R` |
| `OVERCURRENT_*` | Per-side current limit exceeded | Reduce the load, then press `R` |
| `LOWBATT` | Battery below the critical threshold | Recharge, then press `R` |
| `HEARTBEAT` / `TIMEOUT` | Host link / command flow lost in NORMAL | Press `R` |

**To recover: clear the cause, then press `R`.** That sends `ENABLE ON`, which
clears recoverable faults and re-enables the motors — matching the ORCP spec
(§5.3): *"after the emergency stop is released the fault MUST persist until the
host explicitly sends ENABLE ON; motors MUST NOT restart automatically."* The same
explicit-re-enable rule applies to every latched fault, not just e-stop. If `R` is
rejected (e.g. the e-stop is still open), the status line tells you — fix the cause
and try again.

The source — [`src/orcp/teleop.py`](src/orcp/teleop.py) — is a good read for how the
pieces fit together. It's ~200 lines and uses only the public API documented below.

## API reference

### System

```python
robot.ping()      # → True or raises ConnectionError
robot.info()      # → InfoResponse(fw, hw, proto, level, vendor, model, extra)
robot.status()    # → StatusResponse(preset, mode, enabled, fault, estop, vl, vr, vbat, battery, coast, hold, ...)
```

### Motion

```python
robot.cmd_vel(v=0.5, w=0.0)   # Unicycle: linear (m/s), angular (rad/s)
robot.wheel(l=5.0, r=5.0)     # Direct wheel velocities (rad/s)
robot.stop()                   # Immediate stop, never raises
```

#### Stop modes and position hold

How the robot decelerates and what it does afterwards are independent, so they
are separate arguments:

```python
robot.stop()                     # brake
robot.stop("COAST")              # coast to rest (gentler than braking from speed)
robot.stop(hold=True)            # brake, then actively HOLD POSITION
robot.stop("COAST", hold=True)   # coast to rest, then hold
robot.hold()                     # shorthand for stop(hold=True)
```

⚠️ **`stop()` never raises, but `stop(hold=True)` does — deliberately.** A plain
stop is what you call in a `finally:` or an exception handler, and it must not
fail there. A hold can legitimately be *refused* (the controller is not enabled,
or has no encoders), and swallowing that would leave you believing the robot is
holding position when nothing is holding it.

⚠️ **The refusal is not a wire error.** ORCP v1.1 §STOP requires STOP to be
accepted regardless of safety state — *it never fails* — so a controller
declining a hold answers `OK STOP … hold=refused reason=<CODE>`, not `ERR`. The
library raises `HoldRefused` anyway, because that is the right layer for it: the
protocol keeps its guarantee, the library keeps you honest. **The stop still
happened** — the exception says the *hold* did not engage, not that the robot is
still moving.

```python
from orcp import HoldRefused

try:
    robot.hold()
except HoldRefused as e:
    print("not holding:", e.reason)   # NOT_ENABLED / NO_ENCODERS / UNSUPPORTED
```

Commands are sent in the spec's key=value form (`STOP mode=COAST hold=1`),
matching `WHEEL`'s `mode=DUTY`. Some firmware also accepts bare `STOP COAST`,
but that is a compatibility form and this library does not use it.

⚠️ **`COAST` and `HOLD` are vendor extensions**, not ORCP v1.1 — which defines
`STOP` alone. Check for support before depending on them:

```python
st = robot.status()
if st.hold is None:
    ...   # controller has no position-hold feature at all
elif st.is_holding:
    ...   # hold=1, actively holding
elif st.hold_broken:
    ...   # hold=2 — the hold ended on a FAULT or the thermal timeout
```

⚠️ **`hold is None` and `hold == 0` are different answers** and must not be
conflated: `None` means "no such feature", `0` means "has one, not holding right
now". Treating a missing field as "not holding" reads a controller that *cannot*
hold as one that simply isn't.

`hold_broken` (`hold=2`) is the state worth alerting a human about — the robot
was under active position control, possibly on a gradient, and is not any more.
It stays set until the next command.

> ⚠️ **A position hold is a convenience, not a safety function.** It requires
> power, a live controller and working encoders, and typically releases on any
> power-stage fault. Do not rely on it to hold a load on a gradient; that needs
> a mechanical or electrically-released brake in the drivetrain.

### Safety

```python
robot.preset('SLOW')    # 30% power, auto-enabled, 1 s watchdog
robot.preset('NORMAL')  # 100% power, requires ENABLE ON + heartbeat
robot.enable()
robot.disable()
robot.heartbeat()                    # Single HB
robot.start_heartbeat(interval=0.1)  # Background thread, 100 ms interval
robot.stop_heartbeat()
```

### Streaming telemetry

```python
def on_telemetry(data):
    print(f"Left: {data.vl:.2f} rad/s, Right: {data.vr:.2f} rad/s")

robot.stream_on(rate=10, callback=on_telemetry)
robot.stream_off()
```

### Configuration

```python
kp = robot.get('pid.kp')         # single parameter
cfg = robot.get_all()            # → {name: value} for every parameter
robot.set('pid.kp', 0.08)
robot.save()      # Persist to flash
robot.load()      # Load from flash
robot.defaults()  # Factory reset
```

Parameter names are **not** validated by this library — it passes whatever you
give it and surfaces the controller's `ERR code=BAD_KEY`. The key set is a
property of the firmware, not of ORCP: v1.1 §7 standardises a core group
(`kin.*`, `pid.*`, `batt.*`, `slow.*`, `normal.*`, `hb.*`) and devices add their
own freely. **Discover rather than assume:**

```python
cfg = robot.get_all()
print(sorted(cfg))               # exactly what this controller exposes
```

⚠️ **A key can be removed or split between firmware versions.** The MC1's
`current.scale` became `current.scale_left` / `current.scale_right`, so code
holding the old name fails against newer firmware. `get_all()` is the reliable
way to find out; a hard-coded key list is not.

⚠️ **Read values back at full precision.** Some controllers format `GET` to
three decimals, which cannot round-trip per-board calibration constants around
0.006 — a saved snapshot then silently degrades the calibration it recorded.
The MC1 moved to six decimals in FW 1.11.0 for exactly this reason.

### Push events

```python
robot.on_fault(lambda e: print("FAULT:", e.code))          # ! FAULT <code>
robot.on_warn(lambda e: print("WARN:", e.type, e.fields))  # ! WARN <type> ...
```

### Cached properties

```python
robot.battery_voltage   # float | None  (volts)
robot.battery           # str | None    (band label "OK"/"LOW"/… or "80%")
robot.is_enabled        # bool | None
robot.is_holding        # bool | None   (None = unsupported OR no STATUS yet)
robot.fault             # str | None    (updated by STATUS and ! FAULT pushes)
```

## Testing without hardware

Use `MockTransport` to test your code without a physical robot:

```python
from orcp import ORCP
from orcp.transport import MockTransport

transport = MockTransport()
transport.queue_response('OK PING')
transport.queue_response('OK CMD_VEL')

with ORCP('mock://', _transport=transport) as robot:
    robot.ping()
    robot.cmd_vel(v=0.5, w=0.0)
```

## Error handling

```python
from orcp import CommandError, ConnectionError, TimeoutError

try:
    robot.cmd_vel(v=0.5, w=0.0)
except CommandError as e:
    print(f"Controller error: {e.code} — {e.message}")
except TimeoutError:
    print("No response from controller")
except ConnectionError:
    print("Lost connection")
```

## Development

```bash
git clone https://github.com/orcp-protocol/orcp-python
cd orcp-python
pip install -e ".[dev]"

pytest tests/
mypy src/orcp/
black src/ tests/
ruff check src/ tests/
```

## Protocol

ORCP uses ASCII line-based messages:

| Direction | Format |
|-----------|--------|
| Command   | `COMMAND param=value\n` |
| Success   | `OK COMMAND field=value\n` |
| Error     | `ERR code=CODE msg="description"\n` |
| Push      | `! TYPE field=value\n` |

See the [ORCP specification](https://github.com/orcp-protocol/orcp) for full details.

## License

MIT — see [LICENSE](LICENSE).
