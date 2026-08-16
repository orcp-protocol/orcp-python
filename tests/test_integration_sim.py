"""End-to-end integration test against the ORCP reference simulator.

Spawns ``orcp-sim --profile mc1`` over a PTY and drives it with the real client
(no mocks, no hardware). Skipped automatically if ``orcp-sim`` is not installed
in the environment, so the unit suite stays self-contained.

    pip install orcp-sim   # to enable these tests
"""
import importlib.util
import os
import subprocess
import sys
import time

import pytest

_HAS_SIM = importlib.util.find_spec("orcp_sim") is not None
pytestmark = pytest.mark.skipif(not _HAS_SIM, reason="orcp-sim not installed")


@pytest.fixture
def mc1_sim(tmp_path):
    """Run the simulator (MC1 profile) over a PTY; yield the device path."""
    link = str(tmp_path / "sim_pty")
    proc = subprocess.Popen(
        [sys.executable, "-m", "orcp_sim", "--profile", "mc1", "--link", link,
         "--config-file", str(tmp_path / "cfg.json")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            if os.path.exists(link):
                break
            time.sleep(0.1)
        assert os.path.exists(link), "simulator did not create the PTY link"
        yield link
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_end_to_end_against_mc1_sim(mc1_sim):
    from orcp import ORCP
    from orcp.exceptions import HoldRefused

    with ORCP(mc1_sim, timeout=2.0) as r:
        assert r.ping() is True

        info = r.info()
        assert info.hw == "MC1"
        assert info.proto == "ORCP/1.1"
        assert info.level == 2
        assert info.extra.get("bl") == "1.4.0"
        assert info.fw == "1.13.0"

        st = r.status()
        assert st.preset == "SLOW"
        assert st.battery.endswith("%")  # MC1 reports a percentage, not a band

        # The config round-trip that originally failed against a generic sim:
        assert r.get("batt.hyst_v") == pytest.approx(0.2)
        r.set("batt.hyst_v", 0.3)
        assert r.get("batt.hyst_v") == pytest.approx(0.3)

        assert len(r.get_all()) == 63    # FW 1.13.0 / CONFIG 25

        # Keys added since the client was last synced. current.scale is the one
        # that matters in the other direction: it was split per-side, so a
        # client still using it would fail only on real hardware.
        cfg = r.get_all()
        for key in ("ff.inertia", "pid.kp_c", "current.scale_left",
                    "coast.park_vel", "hold.kp", "hold.max_ms"):
            assert key in cfg, f"{key} missing from GET ALL"
        assert "current.scale" not in cfg

        # ── Vendor extensions: coast-and-park, STOP HOLD ────────────────
        st = r.status()
        assert st.coast is False         # supported and not coasting …
        assert st.hold == 0              # … as opposed to None, which would
        assert st.is_holding is False    #     mean "no such feature"

        r.hold()                         # STOP HOLD
        assert r.status().is_holding

        r.stop()                         # a plain STOP is a clean exit
        assert r.status().hold == 0

        # ⚠️ A hold the controller cannot honour arrives as a SUCCESSFUL stop
        # carrying hold=refused — ORCP v1.1 §STOP forbids STOP from failing.
        # The library raises anyway, so the caller cannot carry on believing
        # the robot is holding when nothing is.
        r.set("kin.counts_per_rev", 0)
        with pytest.raises(HoldRefused) as exc:
            r.hold()
        assert exc.value.reason == "NO_ENCODERS"
        # …and the stop itself still succeeded.
        assert r.status().hold == 0
        r.set("kin.counts_per_rev", 2249)

        # A fault push (NORMAL + motion, then go silent → controller faults).
        faults = []
        r.on_fault(lambda e: faults.append(e.code))
        r.preset("NORMAL")
        r.enable()
        r.cmd_vel(0.1, 0.0)
        time.sleep(0.5)                  # no heartbeat → command-timeout fault
        assert faults, "expected a ! FAULT push"
        assert r.fault in ("TIMEOUT", "HEARTBEAT")
