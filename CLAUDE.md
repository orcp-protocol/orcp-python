# ORCP Python Client Library

## Project Context

This is the official Python client library for the Open Robot Control Protocol (ORCP).
ORCP is an open standard for robot control — spec published at https://github.com/orcp-protocol/orcp

The library provides a clean API for communicating with ORCP-compliant motor controllers
over USB (serial), WiFi (TCP socket), and CAN bus transports.

## Protocol Summary

ORCP uses ASCII line-based messages over serial/TCP:
- Commands: `COMMAND param=value param=value\n`
- Success: `OK COMMAND field=value field=value\n`
- Error: `ERR code=ERROR_CODE msg="description"\n`
- Push messages: `! TYPE field=value field=value\n`

### Commands (15 total)

**Motion:** CMD_VEL v=<float> w=<float>, WHEEL l=<float> r=<float>, STOP [BRAKE|COAST] [HOLD]
**Safety:** PRESET <SLOW|NORMAL>, ENABLE <ON|OFF>, HB
**System:** PING, INFO, STATUS
**Telemetry:** STREAM <ON|OFF> [rate_hz]
**Config:** GET <param>, SET <param>=<value>, SAVE, LOAD, DEFAULTS

### Safety System

- Two presets: SLOW (30% power, auto-enabled, 1000ms timeout) and NORMAL (100%, requires ENABLE ON + HB heartbeat every 100ms, 250ms timeout)
- Faults: ESTOP, LOWBATT, NOT_ENABLED, HEARTBEAT, TIMEOUT
- Push messages: `! STREAM ...` (telemetry), `! WARN ...`, `! FAULT ...`

### Units

- Linear velocity: m/s
- Angular velocity: rad/s
- Wheel velocities: rad/s (output shaft)
- Battery: V
- Distances (config): mm
- Timeouts: ms
- Stream rate: Hz

## Library Design Goals

1. **Pip-installable** — `pip install orcp` (package name on PyPI)
2. **Transport-agnostic API** — same methods regardless of USB/WiFi/CAN
3. **Thread-safe** — heartbeat and streaming run in background threads
4. **Pythonic** — context manager support, exceptions for errors, dataclasses for responses
5. **Minimal dependencies** — pyserial only, no heavy frameworks
6. **Testable** — mock transport for unit testing without hardware

## API Design

```python
from orcp import ORCP

# Connection options
robot = ORCP('/dev/cu.usbmodemXXXX')                    # USB direct
robot = ORCP('socket://192.168.4.1:3333')                 # WiFi bridge
robot = ORCP('/dev/cu.usbmodemXXXX', baudrate=115200)    # Explicit baud

# Context manager
with ORCP('socket://192.168.4.1:3333') as robot:
    robot.ping()
    robot.cmd_vel(v=0.2, w=0.0)
    time.sleep(2)
    robot.stop()

# System
robot.ping()                      # Returns True or raises ConnectionError
info = robot.info()               # Returns InfoResponse dataclass
status = robot.status()           # Returns StatusResponse dataclass

# Motion
robot.cmd_vel(v=0.2, w=0.5)      # Unicycle model
robot.wheel(l=5.0, r=5.0)        # Direct wheel control (rad/s)
robot.stop()                      # Immediate stop (brake); never raises
robot.stop('COAST')               # Coast to rest        [vendor extension]
robot.stop(hold=True)             # Stop, then hold position — raises HoldRefused
robot.hold()                      # Shorthand for stop(hold=True)

# Safety
robot.preset('SLOW')              # Switch preset
robot.preset('NORMAL')
robot.enable()                    # Enable motors (ENABLE ON)
robot.disable()                   # Disable motors (ENABLE OFF)
robot.heartbeat()                 # Send single HB

# Auto-heartbeat (background thread)
robot.start_heartbeat(interval=0.1)   # Send HB every 100ms
robot.stop_heartbeat()

# Streaming telemetry
def on_telemetry(data):
    print(f"Left: {data.vl:.2f} rad/s, Right: {data.vr:.2f} rad/s")

robot.stream_on(rate=10, callback=on_telemetry)   # 10 Hz
robot.stream_off()

# Configuration
value = robot.get('kp')           # Returns float
robot.set('kp', 0.08)
robot.save()                      # Persist to flash
robot.load()                      # Load from flash
robot.defaults()                  # Reset to factory

# Properties from last status
print(robot.battery_voltage)
print(robot.battery_level)
print(robot.is_enabled)
print(robot.fault)
```

## Package Structure

```
orcp-python/
├── CLAUDE.md              (this file)
├── README.md
├── LICENSE
├── pyproject.toml         (build config, use setuptools or hatch)
├── src/
│   └── orcp/
│       ├── __init__.py    (exports ORCP class)
│       ├── client.py      (main ORCP class)
│       ├── transport.py   (SerialTransport, SocketTransport, MockTransport)
│       ├── parser.py      (parse OK/ERR/push responses into dataclasses)
│       ├── models.py      (dataclasses: StatusResponse, InfoResponse, StreamData, etc.)
│       ├── exceptions.py  (ORCPError, CommandError, ConnectionError, TimeoutError)
│       └── heartbeat.py   (background heartbeat thread)
├── tests/
│   ├── test_client.py
│   ├── test_parser.py
│   ├── test_transport.py
│   └── test_heartbeat.py
└── examples/
    ├── basic_motion.py
    ├── stream_telemetry.py
    └── normal_mode.py
```

## Hardware Test Environment

There is a working ORCP controller (STM32F103C8T6 Blue Pill) connected:
- USB: /dev/cu.usbmodemXXXX (check `ls /dev/cu.usbmodem*`)
- WiFi: SSID "TeachingBase", IP 192.168.4.1, port 3333
- Firmware responds to all motion, safety, system, and streaming commands
- Config commands (GET/SET/SAVE/LOAD/DEFAULTS) not yet implemented in firmware

## Key Implementation Notes

- pyserial's `serial.serial_for_url()` handles both USB and TCP transparently
  using `socket://host:port` URL format
- Push messages (lines starting with `!`) can arrive at any time, including
  between sending a command and receiving the response. The receive loop must
  buffer push messages and return only the OK/ERR response to the caller.
- ⚠️ **Never use `serial.readline()` directly.** It returns whatever bytes are
  available when its timeout expires, *including a partial line*. Under
  streaming the reader thread splits a push mid-line and the tail — which does
  not start with `!` — gets taken for a command response. `SerialTransport`
  buffers and only ever emits complete newline-terminated lines; a timeout means
  "no COMPLETE line yet".
- ⚠️ **A received line that is neither a push nor `OK`/`ERR` must be dropped**,
  never enqueued as a response. Otherwise noise, or the fragment you read when
  connecting to an already-streaming device, becomes the next command's failure.
  `client.dropped_lines` counts them so "discarded" doesn't mean "invisible".
- Stream data callback should run in a separate thread to avoid blocking
- Heartbeat thread should be daemon so it doesn't prevent program exit
- All float values in responses use key=value format, parse with split('=')
- `STOP` never fails — this is a spec MUST (§STOP: "MUST be accepted regardless
  of safety state"), so never return ERR from a STOP path. A hold that cannot be
  honoured is reported as `OK STOP … hold=refused reason=<CODE>`.
  ⚠️ **`stop(hold=True)` nevertheless raises `HoldRefused`** on that OK response.
  That is deliberate and is the one place the library adds a failure the wire
  does not have: a caller believing the robot is holding position when nothing
  is holding it is the hazard. Never "fix" this to be uniform, and never move it
  back onto the wire as an ERR.
- Send the spec's key=value form (`STOP mode=COAST hold=1`). ⚠️ Bare
  `STOP COAST` is **not** a supported form: MC1 accepted it for one release and
  now rejects it. Historical note worth keeping — firmware that read only bare
  arguments answered `OK STOP mode=BRAKE` to a coast request and braked, and
  because the mode was echoed from the request the response looked correct.
- Vendor-extension STATUS fields (`coast=`, `hold=`) parse to `None` when the
  device omits them. ⚠️ `None` (unsupported) and `0` (supported, inactive) are
  DIFFERENT ANSWERS — never collapse them with `or 0` / `not x`.
- Use Python 3.9+ (match minimum supported by current pip/setuptools)

## Development Commands

```bash
# Install in development mode
pip install -e ".[dev]"

# Run tests
pytest tests/

# Run a specific test
pytest tests/test_parser.py -v

# Check types
mypy src/orcp/

# Format
black src/ tests/

# Lint
ruff check src/ tests/
```
