"""NORMAL preset example: enable motors, auto-heartbeat, drive, then clean up."""
import time

from orcp import ORCP


def main():
    with ORCP("socket://192.168.4.1:3333") as robot:
        robot.ping()

        # Switch to NORMAL preset (100% power, requires heartbeat)
        print("Switching to NORMAL preset...")
        robot.preset("NORMAL")
        robot.enable()

        # Start background heartbeat (must arrive every 100 ms)
        robot.start_heartbeat(interval=0.08)
        print("Motors enabled, heartbeat running.")

        # Drive
        print("Driving at 0.5 m/s for 3 seconds...")
        robot.cmd_vel(v=0.5, w=0.0)
        time.sleep(3)

        # Clean up
        robot.stop()
        robot.stop_heartbeat()
        robot.disable()
        robot.preset("SLOW")
        print("Stopped and returned to SLOW preset.")


if __name__ == "__main__":
    main()
