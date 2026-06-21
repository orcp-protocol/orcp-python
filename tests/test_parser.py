import pytest
from orcp.exceptions import CommandError
from orcp.models import (
    FaultEvent,
    InfoResponse,
    StatusResponse,
    StreamData,
    WarnEvent,
)
from orcp.parser import parse_push, parse_response


class TestParseOk:
    def test_ping(self):
        assert parse_response("OK PING") is True

    def test_info(self):
        r = parse_response(
            "OK INFO fw=1.8.0 bl=1.1.1 hw=MC1 proto=ORCP/1.1 level=2 uuid=DEADBEEF"
        )
        assert isinstance(r, InfoResponse)
        assert r.fw == "1.8.0"
        assert r.hw == "MC1"
        assert r.proto == "ORCP/1.1"
        assert r.level == 2
        assert r.extra["bl"] == "1.1.1"
        assert r.extra["uuid"] == "DEADBEEF"

    def test_info_optional_fields_absent(self):
        r = parse_response("OK INFO fw=1.1.0 hw=ORCP-SIM proto=ORCP/1.1")
        assert r.level is None
        assert r.vendor is None
        assert r.extra == {}

    def test_status_no_fault(self):
        r = parse_response(
            "OK STATUS preset=SLOW mode=IDLE en=1 fault=OK estop=0 "
            "tl=0.000 tr=0.000 vl=0.000 vr=0.000 dl=0.000 dr=0.000 "
            "lim=0.300 vbat=12.45 battery=OK"
        )
        assert isinstance(r, StatusResponse)
        assert r.preset == "SLOW"
        assert r.mode == "IDLE"
        assert r.enabled is True
        assert r.fault is None              # fault=OK → None
        assert r.estop is False
        assert r.vbat == pytest.approx(12.45)
        assert r.battery == "OK"
        assert r.duty_limit == pytest.approx(0.30)

    def test_status_with_fault_and_band_percent(self):
        r = parse_response(
            "OK STATUS preset=NORMAL mode=VELOCITY en=0 fault=LOWBATT estop=1 "
            "tl=5.0 tr=5.0 vl=4.9 vr=5.1 dl=0.2 dr=0.2 lim=0.9 vbat=9.4 battery=93%"
        )
        assert r.enabled is False
        assert r.fault == "LOWBATT"
        assert r.estop is True
        assert r.vl == pytest.approx(4.9)
        assert r.battery == "93%"          # percentage form preserved verbatim

    def test_generic_ok_returns_true(self):
        assert parse_response("OK CMD_VEL v=0.1 w=0.0") is True
        assert parse_response("OK STOP mode=BRAKE") is True
        assert parse_response("OK SET pid.kp=0.080") is True

    def test_get_single(self):
        assert parse_response("OK GET pid.kp=0.050") == pytest.approx(0.05)

    def test_get_dotted_name(self):
        assert parse_response("OK GET batt.hyst_v=0.200") == pytest.approx(0.2)

    def test_get_all_returns_dict(self):
        r = parse_response("OK GET pid.kp=0.050 batt.hyst_v=0.200 kin.counts_per_rev=2249.000")
        assert isinstance(r, dict)
        assert len(r) == 3
        assert r["pid.kp"] == pytest.approx(0.05)
        assert r["kin.counts_per_rev"] == pytest.approx(2249.0)

    def test_strips_whitespace(self):
        assert parse_response("  OK PING  ") is True


class TestParseErr:
    def test_raises_command_error(self):
        with pytest.raises(CommandError) as exc:
            parse_response('ERR code=NOT_ENABLED msg="Motors not enabled"')
        assert exc.value.code == "NOT_ENABLED"
        assert "Motors not enabled" in exc.value.message

    def test_no_feedback_code(self):
        with pytest.raises(CommandError) as exc:
            parse_response('ERR code=NO_FEEDBACK msg="needs encoders"')
        assert exc.value.code == "NO_FEEDBACK"


class TestParsePush:
    def test_stream(self):
        r = parse_push(
            "! STREAM tl=5.0 tr=5.0 vl=1.5 vr=-0.5 dl=0.1 dr=0.1 vbat=7.4 battery=80%"
        )
        assert r[0] == "stream"
        d = r[1]
        assert isinstance(d, StreamData)
        assert d.vl == pytest.approx(1.5)
        assert d.vr == pytest.approx(-0.5)
        assert d.vbat == pytest.approx(7.4)
        assert d.battery == "80%"

    def test_warn_with_type_and_fields(self):
        r = parse_push("! WARN AUX5V state=warn i=6.000 v=5.000")
        assert r[0] == "warn"
        ev = r[1]
        assert isinstance(ev, WarnEvent)
        assert ev.type == "AUX5V"
        assert ev.fields["state"] == "warn"
        assert ev.fields["i"] == "6.000"

    def test_warn_batt(self):
        r = parse_push("! WARN BATT level=LOW vbat=10.100 battery=45%")
        assert r[1].type == "BATT"
        assert r[1].fields["level"] == "LOW"

    def test_fault_bare_code(self):
        r = parse_push("! FAULT HEARTBEAT")
        assert r[0] == "fault"
        assert isinstance(r[1], FaultEvent)
        assert r[1].code == "HEARTBEAT"

    def test_unknown_push_type(self):
        assert parse_push("! UNKNOWN foo=bar") is None

    def test_malformed_push(self):
        assert parse_push("!") is None
