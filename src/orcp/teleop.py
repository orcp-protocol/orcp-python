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
    Esc         Quit

Uses CMD_VEL (linear + angular velocity).
Sends commands at ~20Hz while a key is held.
In NORMAL mode, sends heartbeat every 100ms via background thread.
"""

import curses
import sys
import time

from orcp import ORCP, CommandError, ConnectionError, StreamData, TimeoutError

PORT = None  # set from the command line — see main()

# ── Speed settings ────────────────────────────────────────────────────────────
SLOW_SPEEDS  = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
SLOW_LABELS  = ['Crawl', 'Slow', 'Steady', 'Brisk', 'Fast', 'Max']

NORMAL_SPEEDS = [0.10, 0.20, 0.30, 0.50, 0.75, 1.00]
NORMAL_LABELS = ['Gentle', 'Easy', 'Cruise', 'Quick', 'Fast', 'Full']

SLOW_TURN   = 2.0   # rad/s spin in SLOW
SLOW_ARC    = 1.0   # rad/s arc  in SLOW
NORMAL_TURN = 4.0   # rad/s spin in NORMAL
NORMAL_ARC  = 2.0   # rad/s arc  in NORMAL

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

    # Live battery string updated by stream callback (background thread)
    battery_str = ""

    def on_telemetry(data: StreamData) -> None:
        nonlocal battery_str
        battery_str = f"{data.vbat:.2f}V ({data.battery})"

    robot.preset('SLOW')
    robot.stream_on(rate=5, callback=on_telemetry)
    normal_mode = False

    # ── State ─────────────────────────────────────────────────────────────────
    v = 0.0
    w = 0.0
    last_v: float | None = None
    last_w: float | None = None
    last_send = 0.0
    mode_msg = ""
    mode_msg_time = 0.0

    def speeds():
        return NORMAL_SPEEDS if normal_mode else SLOW_SPEEDS

    def labels():
        return NORMAL_LABELS if normal_mode else SLOW_LABELS

    def turn_rate():
        return NORMAL_TURN if normal_mode else SLOW_TURN

    def arc_rate():
        return NORMAL_ARC if normal_mode else SLOW_ARC

    def draw():
        stdscr.clear()
        h, _ = stdscr.getmaxyx()

        mode_str = "NORMAL (100%)" if normal_mode else "SLOW (30%)"
        stdscr.addstr(0, 0, f"═══ ORCP Drive Demo  [{mode_str}] ═══", curses.A_BOLD)
        stdscr.addstr(1, 0, f"Speed: {labels()[speed_idx]} ({speeds()[speed_idx]:.2f} m/s)    [+/-] change  [M] toggle mode")

        stdscr.addstr(3, 0, "Controls:")
        stdscr.addstr(4, 4, "W / ↑      Forward")
        stdscr.addstr(5, 4, "S / ↓      Reverse")
        stdscr.addstr(6, 4, "A / ←      Spin left")
        stdscr.addstr(7, 4, "D / →      Spin right")
        stdscr.addstr(8, 4, "Q          Arc forward-left")
        stdscr.addstr(9, 4, "E          Arc forward-right")
        stdscr.addstr(10, 4, "Space      Stop")
        stdscr.addstr(11, 4, "M          Toggle SLOW / NORMAL")
        stdscr.addstr(12, 4, "Esc        Quit")

        dir_str = "STOPPED"
        if   v > 0 and w == 0: dir_str = "▲ FORWARD"
        elif v < 0 and w == 0: dir_str = "▼ REVERSE"
        elif v == 0 and w > 0: dir_str = "◄ SPIN LEFT"
        elif v == 0 and w < 0: dir_str = "► SPIN RIGHT"
        elif v > 0 and w > 0:  dir_str = "◄▲ ARC LEFT"
        elif v > 0 and w < 0:  dir_str = "▲► ARC RIGHT"

        stdscr.addstr(14, 0, f"Direction: {dir_str}", curses.A_BOLD)
        stdscr.addstr(15, 0, f"CMD_VEL:   v={v:.3f} m/s  w={w:.2f} rad/s")

        if battery_str:
            stdscr.addstr(17, 0, f"Battery:   {battery_str}")

        if normal_mode:
            stdscr.addstr(18, 0, "Heartbeat: active (100ms)", curses.A_DIM)

        if mode_msg and time.time() - mode_msg_time < 2.0:
            stdscr.addstr(20, 0, mode_msg, curses.A_BOLD)

        stdscr.addstr(h - 1, 0, "Press Esc to quit")
        stdscr.refresh()

    # ── Main loop ─────────────────────────────────────────────────────────────
    try:
        running = True
        while running:
            key = stdscr.getch()
            now = time.time()

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
                w = arc_rate()
            elif key == ord('e'):
                v = speeds()[speed_idx]
                w = -arc_rate()
            elif key == ord(' '):
                robot.stop()
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
            "  USB:        orcp-drive /dev/tty.usbmodemXXXX   (macOS)\n"
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
