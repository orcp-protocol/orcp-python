import time

import pytest
from orcp import ORCP, CommandError, HoldRefused
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


    # ── STOP modes and position hold (vendor extensions) ─────────────────

    def test_stop_default_sends_bare_stop(self):
        robot, transport = make_robot("OK STOP mode=BRAKE")
        try:
            robot.stop()
            assert transport.sent[-1].strip() == "STOP"
        finally:
            robot.close()

    def test_stop_coast_sends_the_spec_kv_form(self):
        """⚠️ ORCP v1.1 §STOP documents `STOP [mode=<vendor_mode>]`, matching
        WHEEL's `mode=DUTY`. Firmware that reads only bare arguments answered
        `OK STOP mode=BRAKE` to a coast request and braked, so the client sends
        the documented form."""
        robot, transport = make_robot("OK STOP mode=COAST parking=auto")
        try:
            robot.stop("COAST")
            assert transport.sent[-1].strip() == "STOP mode=COAST"
        finally:
            robot.close()

    def test_stop_hold_and_coast_hold(self):
        """Deceleration and end state are orthogonal, so all four combine."""
        for kwargs, expected in (
            (dict(hold=True), "STOP hold=1"),
            (dict(mode="COAST", hold=True), "STOP mode=COAST hold=1"),
            (dict(mode="BRAKE", hold=True), "STOP mode=BRAKE hold=1"),
        ):
            robot, transport = make_robot("OK STOP mode=BRAKE hold=on",
                                          "OK STATUS preset=SLOW mode=VELOCITY en=1 fault=OK estop=0 hold=1",)
            try:
                robot.stop(**kwargs)
                assert transport.sent[0].strip() == expected
            finally:
                robot.close()

    def test_hold_shorthand(self):
        robot, transport = make_robot("OK STOP mode=BRAKE hold=on",
                                      "OK STATUS preset=SLOW mode=VELOCITY en=1 fault=OK estop=0 hold=1",)
        try:
            robot.hold()
            assert transport.sent[0].strip() == "STOP hold=1"
        finally:
            robot.close()

    def test_stop_rejects_unknown_mode(self):
        robot, transport = make_robot()
        try:
            with pytest.raises(ValueError):
                robot.stop("GENTLY")
        finally:
            robot.close()

    def test_refused_hold_raises_although_the_response_is_OK(self):
        """⚠️ The refusal arrives inside a SUCCESSFUL response.

        ORCP v1.1 §STOP requires STOP to be accepted regardless of safety state
        — it never fails — so a controller declining a hold answers `OK STOP …
        hold=refused reason=X`, not ERR. The library raises anyway, because a
        caller that quietly carries on believing the robot is holding position
        on a gradient is the hazard the feature exists to prevent. Protocol
        keeps its guarantee; library keeps you honest."""
        robot, transport = make_robot("OK STOP mode=BRAKE hold=refused reason=NOT_ENABLED")
        try:
            with pytest.raises(HoldRefused) as exc:
                robot.stop(hold=True)
            assert exc.value.reason == "NOT_ENABLED"
        finally:
            robot.close()

    def test_unsupported_hold_arrives_as_ERR_and_still_raises_HoldRefused(self):
        """⚠️ Two wire forms, one exception.

        A controller WITHOUT the feature rejects `hold=1` (ORCP §4: unknown
        parameters must be rejected, not ignored). One WITH it answers OK
        carrying hold=refused (§STOP: STOP never fails). Both mean "you are not
        holding", so the caller needs one except clause — and .reason keeps the
        difference, because UNSUPPORTED will never come true and NOT_ENABLED
        might."""
        robot, transport = make_robot('ERR code=BAD_ARG msg="unknown parameter: hold"')
        try:
            with pytest.raises(HoldRefused) as exc:
                robot.hold()
            assert exc.value.reason == "UNSUPPORTED"
        finally:
            robot.close()

    def test_other_command_errors_still_surface_as_CommandError(self):
        """Only BAD_ARG is remapped — an ESTOP rejection must not be disguised
        as a refused hold."""
        robot, transport = make_robot('ERR code=ESTOP msg="emergency stop active"')
        try:
            with pytest.raises(CommandError):
                robot.hold()
        finally:
            robot.close()

    def test_successful_hold_does_not_raise(self):
        robot, transport = make_robot(
            "OK STOP mode=BRAKE hold=on",
            "OK STATUS preset=SLOW mode=VELOCITY en=1 fault=OK estop=0 hold=1",
        )
        try:
            robot.stop(hold=True)      # must not raise
        finally:
            robot.close()

    def test_old_firmware_that_ignores_hold_is_caught(self):
        """⚠️ Firmware predating the feature reads only bare STOP arguments and
        IGNORES `hold=1` — so it brakes and answers an ordinary
        `OK STOP mode=BRAKE`. No ERR, no hold=refused, nothing to detect from
        the response alone. The caller would be told it is holding position
        while the robot is merely braked, which on a gradient is precisely the
        hazard this feature exists to prevent. hold() confirms via STATUS."""
        robot, transport = make_robot(
            "OK STOP mode=BRAKE",                                    # hold= ignored
            "OK STATUS preset=SLOW mode=IDLE en=1 fault=OK estop=0",  # no hold field
        )
        try:
            with pytest.raises(HoldRefused) as exc:
                robot.hold()
            assert exc.value.reason == "UNSUPPORTED"
        finally:
            robot.close()

    def test_plain_stop_still_swallows_everything(self):
        """A plain STOP is what you call in a ``finally:`` — it must never raise,
        and that has not changed."""
        robot, transport = make_robot('ERR code=NOT_ENABLED msg="whatever"')
        try:
            robot.stop()
        finally:
            robot.close()


class TestReaderResynchronisation:
    """⚠️ Regression: a line that is neither a push nor OK/ERR must be DROPPED,
    never handed to a waiting command.

    ORCP §2.3 says every response begins OK or ERR, so anything else is noise or
    a fragment — and passing it on turns someone else's garbage into *this*
    command's failure. The common source is connecting to a device that is
    already streaming: the first read starts mid-line, and the remainder looks
    like a response. It landed on whatever command ran first, which for the
    teleop app is an unguarded preset('SLOW') — so it died before drawing a
    frame. Reproduced 5 times in 6 by reconnecting to a live simulator.
    """

    def test_fragment_does_not_become_a_command_response(self):
        robot, transport = make_robot(
            "ttery=94% t=15764 el=0 er=0",   # tail of a split ! STREAM line
            "OK PING t=1",
        )
        try:
            assert robot.ping() is True     # gets the real response, not the fragment
            assert robot.dropped_lines >= 1
        finally:
            robot.close()

    def test_dropped_lines_starts_at_zero_and_is_visible(self):
        """Discarded must not mean invisible — a non-zero count is the clue that
        the link is delivering something unexpected."""
        robot, transport = make_robot("OK PING t=1")
        try:
            assert robot.dropped_lines == 0
            robot.ping()
            assert robot.dropped_lines == 0
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
