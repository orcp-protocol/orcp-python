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
| `Esc` | Quit (always returns to a safe, stopped state) |

The source — [`src/orcp/teleop.py`](src/orcp/teleop.py) — is a good read for how the
pieces fit together. It's ~200 lines and uses only the public API documented below.

## API reference

### System

```python
robot.ping()      # → True or raises ConnectionError
robot.info()      # → InfoResponse(fw, hw, proto, level, vendor, model, extra)
robot.status()    # → StatusResponse(preset, mode, enabled, fault, estop, vl, vr, vbat, battery, ...)
```

### Motion

```python
robot.cmd_vel(v=0.5, w=0.0)   # Unicycle: linear (m/s), angular (rad/s)
robot.wheel(l=5.0, r=5.0)     # Direct wheel velocities (rad/s)
robot.stop()                   # Immediate stop, never raises
```

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
