import time

import pytest
from orcp import ORCP, CommandError
from orcp.models import InfoResponse, StatusResponse
from orcp.transport import MockTransport


def make_robot(*responses: str):
    transport = MockTransport()
    for r in responses:
        transport.queue_response(r)
    robot = ORCP("mock://", _transport=transport)
    return robot, transport


class TestSystemCommands:
    def test_ping_returns_true(self):
        robot, _ = make_robot("OK PING")
        try:
            assert robot.ping() is True
        finally:
            robot.close()

    def test_info_returns_info_response(self):
        robot, _ = make_robot("OK INFO fw=1.8.0 bl=1.1.1 hw=MC1 proto=ORCP/1.1 level=2")
        try:
            info = robot.info()
            assert isinstance(info, InfoResponse)
            assert info.fw == "1.8.0"
            assert info.hw == "MC1"
            assert info.level == 2
            assert info.extra["bl"] == "1.1.1"
        finally:
            robot.close()

    def test_status_returns_status_response(self):
        robot, _ = make_robot(
            "OK STATUS preset=SLOW mode=IDLE en=1 fault=OK estop=0 "
            "tl=0 tr=0 vl=0 vr=0 dl=0 dr=0 lim=0.3 vbat=7.4 battery=OK"
        )
        try:
            status = robot.status()
            assert isinstance(status, StatusResponse)
            assert status.enabled is True
            assert status.vbat == pytest.approx(7.4)
            assert status.battery == "OK"
        finally:
            robot.close()

    def test_status_updates_cached_properties(self):
        robot, _ = make_robot(
            "OK STATUS preset=SLOW mode=IDLE en=1 fault=OK estop=0 "
            "tl=0 tr=0 vl=0 vr=0 dl=0 dr=0 lim=0.3 vbat=7.4 battery=80%"
        )
        try:
            robot.status()
            assert robot.battery_voltage == pytest.approx(7.4)
            assert robot.battery == "80%"
            assert robot.is_enabled is True
            assert robot.fault is None
        finally:
            robot.close()

    def test_cached_properties_none_before_status(self):
        robot, transport = make_robot()
        try:
            assert robot.battery_voltage is None
            assert robot.is_enabled is None
            assert robot.fault is None
        finally:
            robot.close()


class TestMotionCommands:
    def test_cmd_vel(self):
        robot, transport = make_robot("OK CMD_VEL")
        try:
            robot.cmd_vel(v=0.5, w=0.1)
            assert any("CMD_VEL" in s for s in transport.sent)
        finally:
            robot.close()

    def test_wheel(self):
        robot, transport = make_robot("OK WHEEL")
        try:
            robot.wheel(l=5.0, r=5.0)
            assert any("WHEEL l=5.0000 r=5.0000" in s for s in transport.sent)
            assert not any("mode=" in s for s in transport.sent)  # default rad/s
        finally:
            robot.close()

    def test_wheel_vendor_duty_mode(self):
        robot, transport = make_robot("OK WHEEL")
        try:
            robot.wheel(l=0.5, r=0.5, mode="DUTY")
            assert any("mode=DUTY" in s for s in transport.sent)
        finally:
            robot.close()

    def test_stop_never_raises(self):
        robot, transport = make_robot("ERR code=ALREADY_STOPPED msg=\"Already stopped\"")
        try:
            robot.stop()  # Should not raise even on ERR
        finally:
            robot.close()

    def test_stop_on_timeout_does_not_raise(self):
        robot, transport = make_robot()  # No response queued
        robot._timeout = 0.05  # Short timeout for test speed
        try:
            robot.stop()  # Should not raise on timeout
        finally:
            robot.close()


class TestSafetyCommands:
    def test_preset_slow(self):
        robot, transport = make_robot("OK PRESET")
        try:
            robot.preset("SLOW")
            assert any("PRESET SLOW" in s for s in transport.sent)
        finally:
            robot.close()

    def test_preset_invalid_raises(self):
        robot, _ = make_robot()
        try:
            with pytest.raises(ValueError, match="SLOW.*NORMAL"):
                robot.preset("FAST")
        finally:
            robot.close()

    def test_enable(self):
        robot, transport = make_robot("OK ENABLE")
        try:
            robot.enable()
            assert any("ENABLE ON" in s for s in transport.sent)
        finally:
            robot.close()

    def test_disable(self):
        robot, transport = make_robot("OK ENABLE")
        try:
            robot.disable()
            assert any("ENABLE OFF" in s for s in transport.sent)
        finally:
            robot.close()


class TestCommandError:
    def test_command_error_propagates(self):
        robot, _ = make_robot('ERR code=NOT_ENABLED msg="Motors not enabled"')
        try:
            with pytest.raises(CommandError) as exc_info:
                robot.cmd_vel(v=0.5, w=0.0)
            assert exc_info.value.code == "NOT_ENABLED"
        finally:
            robot.close()


class TestContextManager:
    def test_context_manager(self):
        transport = MockTransport()
        transport.queue_response("OK PING")
        with ORCP("mock://", _transport=transport) as robot:
            assert robot.ping() is True
        assert not transport.is_connected


class TestConfigCommands:
    def test_get(self):
        robot, _ = make_robot("OK GET pid.kp=0.080")
        try:
            assert robot.get("pid.kp") == pytest.approx(0.08)
        finally:
            robot.close()

    def test_get_all(self):
        robot, _ = make_robot("OK GET pid.kp=0.050 batt.hyst_v=0.200 hb.timeout_ms=500.000")
        try:
            cfg = robot.get_all()
            assert cfg["pid.kp"] == pytest.approx(0.05)
            assert cfg["batt.hyst_v"] == pytest.approx(0.2)
            assert len(cfg) == 3
        finally:
            robot.close()

    def test_set(self):
        robot, transport = make_robot("OK SET pid.kp=0.080")
        try:
            robot.set("pid.kp", 0.08)
            assert any("SET pid.kp=0.08" in s for s in transport.sent)
        finally:
            robot.close()

    def test_save(self):
        robot, transport = make_robot("OK SAVE")
        try:
            robot.save()
            assert any("SAVE" in s for s in transport.sent)
        finally:
            robot.close()


class TestPushEvents:
    def _wait(self, predicate, timeout=0.5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return False

    def test_fault_push_updates_cache_and_callback(self):
        transport = MockTransport()
        robot = ORCP("mock://", _transport=transport)
        try:
            faults = []
            robot.on_fault(lambda e: faults.append(e.code))
            transport.queue_response("! FAULT HEARTBEAT")
            assert self._wait(lambda: bool(faults))
            assert faults == ["HEARTBEAT"]
            assert robot.fault == "HEARTBEAT"   # cached without a STATUS poll
        finally:
            robot.close()

    def test_warn_push_callback(self):
        transport = MockTransport()
        robot = ORCP("mock://", _transport=transport)
        try:
            warns = []
            robot.on_warn(lambda e: warns.append(e))
            transport.queue_response("! WARN AUX5V state=warn i=6.0 v=5.0")
            assert self._wait(lambda: bool(warns))
            assert warns[0].type == "AUX5V"
            assert warns[0].fields["state"] == "warn"
        finally:
            robot.close()


class TestHeartbeat:
    def test_start_stop_heartbeat(self):
        robot, _ = make_robot()
        try:
            robot.start_heartbeat(interval=0.02)
            assert robot._heartbeat.is_running
            time.sleep(0.1)
            robot.stop_heartbeat()
            assert not robot._heartbeat.is_running
        finally:
            robot.close()
