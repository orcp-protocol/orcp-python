"""Stop modes and position hold.

How a robot decelerates and what it does once stopped are independent choices.
This example walks all four combinations and shows how to check that the
controller actually supports the vendor extensions before relying on them.

    python examples/stop_modes.py [device]

⚠️ A position hold is a CONVENIENCE, NOT A SAFETY FUNCTION. It needs power, a
live controller and working encoders, and typically releases on any power-stage
fault. Never rely on it to hold a load on a gradient — that needs a mechanical
or electrically-released brake in the drivetrain.
"""
import sys
import time

from orcp import ORCP, CommandError

DEVICE = sys.argv[1] if len(sys.argv) > 1 else "socket://192.168.4.1:3333"


def main():
    with ORCP(DEVICE) as robot:
        robot.ping()
        info = robot.info()
        print(f"Connected: {info.hw} fw={info.fw} ({info.proto})")

        # ── Feature detection ────────────────────────────────────────────
        # COAST and HOLD are vendor extensions; ORCP v1.1 defines STOP alone.
        # STATUS omits the fields entirely on a controller without them, which
        # is why they parse to None rather than to 0.
        st = robot.status()
        can_hold = st.hold is not None
        can_coast = st.coast is not None
        print(f"coast-and-park: {'yes' if can_coast else 'no'}   "
              f"position hold: {'yes' if can_hold else 'no'}")

        # ── Braking stop ─────────────────────────────────────────────────
        print("\nDriving, then braking...")
        robot.cmd_vel(v=0.2, w=0.0)
        time.sleep(1.5)
        robot.stop()                      # equivalently: robot.stop("BRAKE")

        # ── Coasting stop ────────────────────────────────────────────────
        if can_coast:
            print("Driving, then coasting to rest...")
            robot.cmd_vel(v=0.2, w=0.0)
            time.sleep(1.5)
            robot.stop("COAST")
            # It is still rolling: coast= stays 1 until the controller sees the
            # encoders reach rest and applies its parking brake.
            while robot.status().coast:
                time.sleep(0.05)
            print("  ...rolled to rest and parked.")

        # ── Position hold ────────────────────────────────────────────────
        if not can_hold:
            print("\nController has no position hold; nothing further to show.")
            return

        print("\nHolding position for 5 s — try pushing the robot by hand.")
        try:
            robot.hold()                  # or robot.stop("COAST", hold=True)
        except CommandError as e:
            # Refused, not silently ignored: not enabled, or no encoders. This
            # is why hold() raises where a plain stop() never does — believing
            # the robot is holding when nothing is holding it is the danger.
            print(f"  hold refused: {e}")
            return

        deadline = time.time() + 5
        while time.time() < deadline:
            st = robot.status()
            if st.hold_broken:
                # hold=2. The robot was under active position control and is
                # not any more — a fault or the controller's thermal limit on
                # holding a stalled motor. Worth telling a human about.
                print(f"  ⚠️  HOLD ENDED unexpectedly (fault={st.fault})")
                break
            print(f"  holding: vl={st.vl:+.3f} vr={st.vr:+.3f} "
                  f"dl={st.dl:+.3f} dr={st.dr:+.3f}")
            time.sleep(0.5)

        # Any of these end a hold cleanly; a new motion command would too.
        robot.stop()
        print("Released.")


if __name__ == "__main__":
    main()
