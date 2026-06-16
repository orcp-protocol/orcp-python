"""Streaming telemetry example: receive wheel velocities at 10 Hz."""
import time

from orcp import ORCP, StreamData


def on_telemetry(data: StreamData) -> None:
    print(
        f"vl={data.vl:+.3f} rad/s  vr={data.vr:+.3f} rad/s  "
        f"batt={data.vbat:.2f}V ({data.battery})"
    )


def main():
    with ORCP("socket://192.168.4.1:3333") as robot:
        robot.ping()

        print("Starting 10 Hz telemetry stream (press Ctrl-C to stop)...")
        robot.stream_on(rate=10, callback=on_telemetry)

        robot.cmd_vel(v=0.3, w=0.0)

        try:
            time.sleep(10)
        except KeyboardInterrupt:
            pass

        robot.stop()
        robot.stream_off()
        print("Done.")


if __name__ == "__main__":
    main()
