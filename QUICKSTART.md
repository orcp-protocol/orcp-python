# Getting Started with the ORCP Python Library

## Prerequisites

- Python 3.9 or later
- A USB cable or WiFi connection to your ORCP controller

---

## Installation

Clone the repository and install in development mode so changes to the source take effect immediately:

```bash
git clone https://github.com/orcp-protocol/orcp-python.git
cd orcp-python
pip install -e ".[dev]"
```

Verify the install:

```bash
python -c "from orcp import ORCP; print('orcp installed OK')"
```

---

## Finding your device

**USB (macOS):**

```bash
ls /dev/cu.usbmodem*
```

> Use the **`/dev/cu.*`** device (call-out), **not** `/dev/tty.*` (call-in). They
> are the same physical port, but `tty.*` honours modem carrier-detect signalling
> and is noticeably more sluggish; `cu.*` is the right one for talking to a board.

**USB (Linux):**

```bash
ls /dev/ttyACM*
```

**USB (Windows):**

Open Device Manager (`Win + X` → Device Manager) and expand **Ports (COM & LPT)**. The controller will appear as `USB Serial Device (COMx)`. Note the COM number — e.g. `COM3`.

Alternatively, from PowerShell:

```powershell
Get-PnpDevice -Class Ports | Where-Object Status -eq 'OK' | Select-Object FriendlyName
```

**WiFi:** connect to the `TeachingBase` network — the controller is at `192.168.4.1` port `3333`.

---

## Quick connection test

Before running the drive demo, confirm you can talk to the controller:

```python
from orcp import ORCP

# Pick whichever connection method applies:
with ORCP('/dev/cu.usbmodemXXXX') as robot:       # USB — macOS
# with ORCP('/dev/ttyACM0') as robot:               # USB — Linux
# with ORCP('COM3') as robot:                        # USB — Windows
# with ORCP('socket://192.168.4.1:3333') as robot:  # WiFi (any OS)

    print(robot.ping())    # True
    print(robot.info())    # fw / hw / proto / level
    print(robot.status())  # battery, enabled state, preset, mode, faults
```

If `ping()` returns `True` you're ready to drive.

---

## Running the drive demo

The `orcp-drive` keyboard teleop app is installed with the `teleop` extra:

```bash
pip install -e ".[teleop]"     # adds windows-curses on Windows
```

Then point it at your controller:

```bash
orcp-drive /dev/cu.usbmodemXXXX        # USB — macOS
orcp-drive /dev/ttyACM0                 # USB — Linux
orcp-drive COM3                         # USB — Windows
orcp-drive socket://192.168.4.1:3333    # WiFi (any OS)
```

No hardware? Drive the [reference simulator](https://github.com/orcp-protocol/orcp-sim):

```bash
pip install orcp-sim
orcp-sim --link /tmp/orcp &             # a virtual ORCP controller
orcp-drive /tmp/orcp
```

> **Windows note:** the demo uses `curses`. The `teleop` extra pulls in
> `windows-curses` automatically on Windows; on macOS/Linux it's built in.

### Controls

| Key | Action |
|-----|--------|
| `W` / `↑` | Forward |
| `S` / `↓` | Reverse |
| `A` / `←` | Spin left |
| `D` / `→` | Spin right |
| `Q` | Arc forward-left |
| `E` | Arc forward-right |
| `Space` | Stop |
| `+` / `-` | Increase / decrease speed |
| `M` | Toggle SLOW ↔ NORMAL mode |
| `Esc` | Quit |

### Speed modes

**SLOW** (default on startup) — 30% power limit, no heartbeat required. Safe for confined spaces.

**NORMAL** — 100% power. The library automatically sends a heartbeat every 80 ms in the background. If the connection drops, the controller will stop within 250 ms.

---

## Running the tests

```bash
pytest tests/ -v
```

All tests use `MockTransport` and run without hardware. You should see 55 tests pass.

---

## Troubleshooting

**`Cannot open /dev/cu.usbmodemXXXX` (macOS/Linux)**
Check the port name with `ls /dev/cu.usbmodem*` (macOS) or `ls /dev/ttyACM*` (Linux) and pass the correct one as an argument.

**`Cannot open COM3` (Windows)**
Confirm the COM port number in Device Manager and pass it as an argument, e.g. `python drive.py COM5`. If the port appears briefly then disappears, try a different USB cable.

**`No response to command`**
The controller did not reply within 2 seconds. Check the cable, confirm the firmware is running (the LED should be blinking), and try unplugging and re-plugging the USB cable.

**`NOT_ENABLED` error when switching to NORMAL mode**
This is handled automatically by the drive demo (`robot.enable()` is called as part of the mode switch). If you see it in your own code, call `robot.enable()` after `robot.preset('NORMAL')`.

**Robot stops unexpectedly in NORMAL mode**
The controller requires a heartbeat every 250 ms. Make sure `robot.start_heartbeat()` is called and that no exception killed the background thread.

**Permission denied on Linux**
Add your user to the `dialout` group:
```bash
sudo usermod -aG dialout $USER
# then log out and back in
```

**Permission denied on Windows**
Another application (such as the Arduino IDE serial monitor) may have the port open. Close it and try again.
