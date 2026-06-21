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

    with ORCP(mc1_sim, timeout=2.0) as r:
        assert r.ping() is True

        info = r.info()
        assert info.hw == "MC1"
        assert info.proto == "ORCP/1.1"
        assert info.level == 2
        assert info.extra.get("bl") == "1.1.1"

        st = r.status()
        assert st.preset == "SLOW"
        assert st.battery == "OK"        # MC1 reports a band label

        # The config round-trip that originally failed against a generic sim:
        assert r.get("batt.hyst_v") == pytest.approx(0.2)
        r.set("batt.hyst_v", 0.3)
        assert r.get("batt.hyst_v") == pytest.approx(0.3)

        assert len(r.get_all()) == 43

        # A fault push (NORMAL + motion, then go silent → controller faults).
        faults = []
        r.on_fault(lambda e: faults.append(e.code))
        r.preset("NORMAL")
        r.enable()
        r.cmd_vel(0.1, 0.0)
        time.sleep(0.5)                  # no heartbeat → command-timeout fault
        assert faults, "expected a ! FAULT push"
        assert r.fault in ("TIMEOUT", "HEARTBEAT")
