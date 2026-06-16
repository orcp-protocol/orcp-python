"""Basic motion example: connect, drive forward, stop."""
import time

from orcp import ORCP


def main():
    with ORCP("socket://192.168.4.1:3333") as robot:
        print("Connecting...")
        robot.ping()
        print("Connected!")

        info = robot.info()
        print(f"Firmware: {info.fw}  Hardware: {info.hw}  Protocol: {info.proto}")

        status = robot.status()
        print(f"Battery: {status.vbat:.1f}V ({status.battery})")

        print("Driving forward at 0.2 m/s for 2 seconds...")
        robot.cmd_vel(v=0.2, w=0.0)
        time.sleep(2)

        print("Turning...")
        robot.cmd_vel(v=0.0, w=0.5)
        time.sleep(1)

        print("Stopping.")
        robot.stop()


if __name__ == "__main__":
    main()
