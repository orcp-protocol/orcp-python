#!/usr/bin/env python3
"""
ORCP Keyboard Teleop Demo (``orcp-drive``)

A keyboard tele-operation demo for any ORCP-compliant controller. Works against
real hardware (USB or WiFi) or the reference simulator — run
``orcp-sim --link /tmp/orcp`` and connect with ``orcp-drive /tmp/orcp``.

Installed as the ``orcp-drive`` command (``pip install "orcp[teleop]"``); also
runnable as ``python -m orcp.teleop <port>``.

Drive the robot around using keyboard keys.

Controls:
    W / ↑       Forward
    S / ↓       Reverse
    A / ←       Spin left
    D / →       Spin right
    Q           Arc forward-left
    E           Arc forward-right
    Space       Stop
    +/-         Increase/decrease speed
    M           Toggle SLOW / NORMAL mode
    R           Re-enable motors after a fault is cleared
    Esc         Quit

Uses CMD_VEL (linear + angular velocity).
Sends commands at ~20Hz while a key is held.
In NORMAL mode, sends heartbeat every 100ms via background thread.
"""

import curses
import sys
import time

from orcp import (ORCP, CommandError, ConnectionError, HoldRefused, StreamData,
                  TimeoutError)

PORT = None  # set from the command line — see main()

# ── Speed settings ────────────────────────────────────────────────────────────
SLOW_SPEEDS  = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
SLOW_LABELS  = ['Crawl', 'Slow', 'Steady', 'Brisk', 'Fast', 'Max']

NORMAL_SPEEDS = [0.10, 0.20, 0.30, 0.50, 0.75, 1.00]
NORMAL_LABELS = ['Gentle', 'Easy', 'Cruise', 'Quick', 'Fast', 'Full']

SLOW_TURN   = 2.0   # rad/s spin-in-place in SLOW
NORMAL_TURN = 4.0   # rad/s spin-in-place in NORMAL

# Forward arcs (Q/E) follow a fixed turning RADIUS rather than a fixed turn rate,
# so the arc has the same gentle shape at any speed (instead of pivoting on one
# wheel at low speed). Larger = gentler. Track width is ~0.175 m, so keeping this
# well above ~0.1 m guarantees both wheels keep rolling forward.
ARC_RADIUS  = 0.40  # m

CMD_INTERVAL = 0.05  # 20 Hz command rate


def _run(stdscr):
    global speed_idx
    speed_idx = 2  # start at middle speed

    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(50)

    # ── Connect ───────────────────────────────────────────────────────────────
    try:
        robot = ORCP(PORT)
    except ConnectionError as e:
        stdscr.addstr(0, 0, f"Cannot open {PORT}: {e}")
        stdscr.addstr(1, 0, "Press any key to exit")
        stdscr.nodelay(False)
        stdscr.getch()
        return

    # Live status, updated on the library's background thread by the telemetry
    # stream (battery) and fault pushes (fault_str).
    battery_str = ""
    fault_str = None          # active fault code, or None when OK

    def on_telemetry(data: StreamData) -> None:
        nonlocal battery_str
        battery_str = f"{data.vbat:.2f}V ({data.battery})"

    def _on_fault(ev) -> None:
        nonlocal fault_str
        fault_str = ev.code   # instant notification the moment a fault trips

    robot.preset('SLOW')
    robot.on_fault(_on_fault)
    robot.stream_on(rate=5, callback=on_telemetry)
    normal_mode = False

    # ── State ─────────────────────────────────────────────────────────────────
    v = 0.0
    w = 0.0
    last_v: float | None = None
    last_w: float | None = None
    last_send = 0.0
    last_status_poll = 0.0
    mode_msg = ""
    mode_msg_time = 0.0
    # Vendor-extension support, discovered from STATUS on first poll: the fields
    # are ABSENT on a controller without the feature, which is why the models
    # parse them to None rather than to 0. None here means "not yet known".
    hold_state: int | None = None
    can_coast: bool | None = None

    def speeds():
        return NORMAL_SPEEDS if normal_mode else SLOW_SPEEDS

    def labels():
        return NORMAL_LABELS if normal_mode else SLOW_LABELS

    def turn_rate():
        return NORMAL_TURN if normal_mode else SLOW_TURN

    def draw():
        stdscr.clear()
        h, wcols = stdscr.getmaxyx()

        def put(row, col, text, attr=curses.A_NORMAL):
            """Write a line, skipping anything the terminal is too small for.

            ⚠️ Rows are absolute, and this panel grew past 24 when the coast and
            hold controls were added. curses raises on an out-of-range addstr,
            which on a classic 24-row terminal would crash the app on the first
            frame — so clip rather than assume the window is tall enough. The
            last row is reserved for the quit hint."""
            if row >= h - 1 or col >= wcols:
                return
            try:
                stdscr.addstr(row, col, text[:wcols - col - 1], attr)
            except curses.error:      # narrow window / bottom-right cell
                pass

        mode_str = "NORMAL (100%)" if normal_mode else "SLOW (30%)"
        put(0, 0, f"═══ ORCP Drive Demo  [{mode_str}] ═══", curses.A_BOLD)
        put(1, 0, f"Speed: {labels()[speed_idx]} ({speeds()[speed_idx]:.2f} m/s)    [+/-] change  [M] toggle mode")

        put(3, 0, "Controls:")
        put(4, 4, "W / ↑      Forward")
        put(5, 4, "S / ↓      Reverse")
        put(6, 4, "A / ←      Spin left")
        put(7, 4, "D / →      Spin right")
        put(8, 4, "Q          Arc forward-left")
        put(9, 4, "E          Arc forward-right")
        put(10, 4, "Space      Stop (brake)")
        put(11, 4, "C          Stop by coasting" +
                   ("" if can_coast is not False else "   (not supported)"))
        put(12, 4, "H          Stop and HOLD position" +
                   ("" if hold_state is not None else "   (not supported)"))
        put(13, 4, "M          Toggle SLOW / NORMAL")
        put(14, 4, "R          Re-enable after fault")
        put(15, 4, "Esc        Quit")

        dir_str = "STOPPED"
        if   v > 0 and w == 0: dir_str = "▲ FORWARD"
        elif v < 0 and w == 0: dir_str = "▼ REVERSE"
        elif v == 0 and w > 0: dir_str = "◄ SPIN LEFT"
        elif v == 0 and w < 0: dir_str = "► SPIN RIGHT"
        elif v > 0 and w > 0:  dir_str = "◄▲ ARC LEFT"
        elif v > 0 and w < 0:  dir_str = "▲► ARC RIGHT"

        put(17, 0, f"Direction: {dir_str}", curses.A_BOLD)
        put(18, 0, f"CMD_VEL:   v={v:.3f} m/s  w={w:.2f} rad/s")

        if battery_str:
            put(20, 0, f"Battery:   {battery_str}")

        if fault_str:
            put(21, 0, f"FAULT:     {fault_str}   — press [R] to re-enable",
                curses.A_BOLD | curses.A_REVERSE)
        else:
            put(21, 0, "Status:    OK", curses.A_DIM)

        # ⚠️ hold=2 means a hold ENDED on a fault or the thermal timeout. The
        # robot was under active position control — possibly on a gradient — and
        # is not any more, so it is shown as loudly as a fault rather than as a
        # quiet status line. This is the state that needs a human to look up.
        if hold_state == 1:
            put(22, 0, "HOLD:      holding position", curses.A_BOLD)
        elif hold_state == 2:
            put(22, 0, "HOLD:      /!\\ RELEASED — hold ended, robot is NOT held",
                curses.A_BOLD | curses.A_REVERSE)

        if normal_mode:
            put(23, 0, "Heartbeat: active (100ms)", curses.A_DIM)

        if mode_msg and time.time() - mode_msg_time < 2.0:
            put(25, 0, mode_msg, curses.A_BOLD)

        stdscr.addstr(h - 1, 0, "Press Esc to quit")
        stdscr.refresh()

    # ── Main loop ─────────────────────────────────────────────────────────────
    try:
        running = True
        while running:
            key = stdscr.getch()
            now = time.time()

            # Refresh fault state for the display (and notice when it clears).
            if now - last_status_poll > 0.3:
                last_status_poll = now
                try:
                    st = robot.status()
                    fault_str = st.fault
                    hold_state = st.hold        # None if unsupported
                    can_coast = st.coast is not None
                except (CommandError, TimeoutError):
                    pass

            v = 0.0
            w = 0.0

            if key == 27:  # Esc
                running = False
                continue
            elif key == ord('w') or key == curses.KEY_UP:
                v = speeds()[speed_idx]
            elif key == ord('s') or key == curses.KEY_DOWN:
                v = -speeds()[speed_idx]
            elif key == ord('a') or key == curses.KEY_LEFT:
                w = turn_rate()
            elif key == ord('d') or key == curses.KEY_RIGHT:
                w = -turn_rate()
            elif key == ord('q'):
                v = speeds()[speed_idx]
                w = v / ARC_RADIUS          # arc forward-left at a fixed radius
            elif key == ord('e'):
                v = speeds()[speed_idx]
                w = -v / ARC_RADIUS         # arc forward-right
            elif key == ord(' '):
                robot.stop()
                last_v = 0.0
                last_w = 0.0
            elif key == ord('c') or key == ord('C'):
                # Coast to rest, then the controller parks itself. Feels very
                # different from braking, which is the point of having the key.
                try:
                    robot.stop('COAST')
                    mode_msg = ">>> COAST — rolling to rest, then parking <<<"
                except (CommandError, TimeoutError):
                    mode_msg = ">>> COAST not supported <<<"
                mode_msg_time = now
                last_v = 0.0
                last_w = 0.0
            elif key == ord('h') or key == ord('H'):
                # Stop and actively hold position. ⚠️ A CONVENIENCE, NOT A
                # SAFETY FUNCTION — it needs power, a live controller and
                # working encoders, and releases on any power-stage fault.
                try:
                    robot.hold()
                    mode_msg = ">>> HOLD — actively holding position <<<"
                except HoldRefused as e:
                    # ⚠️ The stop still happened; only the hold was refused.
                    mode_msg = f">>> Stopped, NOT holding: {e.reason} <<<"
                except (CommandError, TimeoutError):
                    mode_msg = ">>> HOLD: no response <<<"
                mode_msg_time = now
                last_v = 0.0
                last_w = 0.0
            elif key == ord('+') or key == ord('='):
                speed_idx = min(speed_idx + 1, len(speeds()) - 1)
            elif key == ord('-') or key == ord('_'):
                speed_idx = max(speed_idx - 1, 0)
            elif key == ord('m') or key == ord('M'):
                robot.stop()
                if normal_mode:
                    robot.stop_heartbeat()
                    robot.preset('SLOW')
                    normal_mode = False
                    speed_idx = min(speed_idx, len(SLOW_SPEEDS) - 1)
                    mode_msg = ">>> Switched to SLOW mode (30% duty limit) <<<"
                else:
                    robot.preset('NORMAL')
                    robot.enable()
                    robot.start_heartbeat(interval=0.08)
                    normal_mode = True
                    speed_idx = min(speed_idx, len(NORMAL_SPEEDS) - 1)
                    mode_msg = ">>> Switched to NORMAL mode (100% duty) <<<"
                mode_msg_time = now
                last_v = None  # Force resend after mode switch
            elif key == ord('r') or key == ord('R'):
                try:
                    robot.enable()   # ENABLE ON — clears recoverable faults + re-enables
                    fault_str = None
                    mode_msg = ">>> Re-enabled (fault cleared) <<<"
                except CommandError as e:
                    mode_msg = f">>> Re-enable rejected: {e.code} <<<"
                except TimeoutError:
                    mode_msg = ">>> Re-enable: no response <<<"
                mode_msg_time = now
                last_v = None

            # Send command if changed or periodically
            if (v != last_v or w != last_w) or (now - last_send > CMD_INTERVAL):
                try:
                    robot.cmd_vel(v=v, w=w)
                except (CommandError, TimeoutError):
                    pass
                last_v = v
                last_w = w
                last_send = now

            draw()

    finally:
        # Always return to a safe state on exit
        robot.stop()
        robot.stop_heartbeat()
        robot.stream_off()
        try:
            robot.preset('SLOW')
        except Exception:
            pass
        robot.close()


def main() -> None:
    """Console entry point for the ``orcp-drive`` command."""
    global PORT
    if len(sys.argv) < 2:
        sys.stderr.write(
            "Usage: orcp-drive <port>\n"
            "  USB:        orcp-drive /dev/cu.usbmodemXXXX   (macOS)\n"
            "              orcp-drive /dev/ttyACM0            (Linux)\n"
            "              orcp-drive COM3                    (Windows)\n"
            "  WiFi/TCP:   orcp-drive socket://192.168.4.1:3333\n"
            "  Simulator:  orcp-sim --link /tmp/orcp   then   orcp-drive /tmp/orcp\n"
        )
        sys.exit(1)
    PORT = sys.argv[1]
    curses.wrapper(_run)


if __name__ == "__main__":
    main()
