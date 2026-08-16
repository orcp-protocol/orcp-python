"""Parse ORCP v1.1 protocol lines into Python objects."""
import re
from typing import Any, Dict, Optional, Tuple, Union

from .exceptions import CommandError, ORCPError
from .models import (
    FaultEvent,
    InfoResponse,
    StatusResponse,
    StreamData,
    WarnEvent,
)

_KV_RE = re.compile(r'([\w.]+)=(?:"([^"]*)"|(\S*))')

# Field names handled explicitly (so anything else lands in `extra`).
_INFO_KNOWN = {"fw", "hw", "proto", "level", "vendor", "model"}
_STATUS_KNOWN = {"preset", "mode", "en", "fault", "estop", "tl", "tr",
                 "vl", "vr", "dl", "dr", "lim", "vbat", "battery",
                 # Vendor extensions promoted to typed fields; absent on
                 # devices that do not implement them, which is why the models
                 # default to None rather than to 0/False.
                 "coast", "hold"}
_STREAM_KNOWN = {"tl", "tr", "vl", "vr", "dl", "dr", "vbat", "battery"}


def _parse_kv(text: str) -> Dict[str, str]:
    """Parse ``key=value`` pairs (keys may contain dots), handling quoted values."""
    result: Dict[str, str] = {}
    for m in _KV_RE.finditer(text):
        result[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)
    return result


def _f(value: Optional[str], default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _int_field(value: Optional[str]) -> Optional[int]:
    """Parse an optional integer STATUS field, preserving absent as ``None``.

    ⚠️ Absent must NOT collapse to 0: for ``hold=``, 0 means "has the feature,
    not holding" and absent means "no such feature"."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _bool_field(value: Optional[str]) -> Optional[bool]:
    """Parse an optional 0/1 STATUS field; absent stays ``None`` (see above)."""
    if value is None:
        return None
    return value == "1"


def _maybe_number(value: str) -> Union[float, str]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def parse_response(line: str) -> Any:
    """Parse an OK or ERR response line.

    Returns a Python object for OK responses; raises :class:`CommandError` for
    ERR responses and :class:`ORCPError` for anything else.
    """
    line = line.strip()
    if line.startswith("OK"):
        return _parse_ok(line)
    if line.startswith("ERR"):
        _parse_err(line)  # always raises
    raise ORCPError(f"Unexpected response: {line!r}")


def _parse_ok(line: str) -> Any:
    parts = line.split(None, 2)
    if len(parts) < 2:
        return True
    command = parts[1]
    kv = _parse_kv(parts[2]) if len(parts) > 2 else {}

    if command == "INFO":
        return InfoResponse(
            fw=kv.get("fw", ""),
            hw=kv.get("hw", ""),
            proto=kv.get("proto", ""),
            level=int(kv["level"]) if kv.get("level", "").isdigit() else None,
            vendor=kv.get("vendor"),
            model=kv.get("model"),
            extra={k: v for k, v in kv.items() if k not in _INFO_KNOWN},
        )
    if command == "STATUS":
        fault = kv.get("fault", "OK")
        return StatusResponse(
            preset=kv.get("preset", ""),
            mode=kv.get("mode", ""),
            enabled=kv.get("en", "0") == "1",
            fault=None if fault == "OK" else fault,
            estop=kv.get("estop", "0") == "1",
            target_l=_f(kv.get("tl")),
            target_r=_f(kv.get("tr")),
            vl=_f(kv.get("vl")),
            vr=_f(kv.get("vr")),
            dl=_f(kv.get("dl")),
            dr=_f(kv.get("dr")),
            duty_limit=_f(kv.get("lim")),
            vbat=_f(kv.get("vbat")),
            battery=kv.get("battery", ""),
            coast=_bool_field(kv.get("coast")),
            hold=_int_field(kv.get("hold")),
            extra={k: v for k, v in kv.items() if k not in _STATUS_KNOWN},
        )
    if command == "GET":
        # v1.1: ``OK GET <param>=<value>`` — or many pairs for ``GET ALL``.
        # A single key returns the bare value; ``GET ALL`` returns a dict.
        if not kv:
            return {}
        values = {k: _maybe_number(v) for k, v in kv.items()}
        if len(values) == 1:
            return next(iter(values.values()))
        return values
    # PING, CMD_VEL, WHEEL, STOP, PRESET, ENABLE, SET, SAVE, … → acknowledged.
    return True


def hold_refusal(line: str) -> Optional[str]:
    """Return the refusal reason from an ``OK STOP … hold=refused reason=X`` line.

    ``None`` if the line is not a refused hold. Lives here rather than in the
    client because it is a protocol detail: a declined hold is reported inside a
    SUCCESSFUL response, since ORCP v1.1 §STOP forbids STOP from failing.
    """
    kv = _parse_kv(line)
    if kv.get("hold") != "refused":
        return None
    return kv.get("reason", "UNKNOWN")


def _parse_err(line: str) -> None:
    kv = _parse_kv(line)
    raise CommandError(code=kv.get("code", "UNKNOWN"), message=kv.get("msg", ""))


def parse_push(line: str) -> Optional[Tuple[str, Any]]:
    """Parse a push message (line starting with ``!``).

    Returns ``(kind, payload)`` or ``None`` if unrecognised:
      - ``("stream", StreamData)``
      - ``("warn", WarnEvent)``     — e.g. ``! WARN BATT level=LOW vbat=10.1``
      - ``("fault", FaultEvent)``   — e.g. ``! FAULT HEARTBEAT``
    """
    parts = line.split(None, 2)
    if len(parts) < 2:
        return None
    push_type = parts[1]
    rest = parts[2] if len(parts) > 2 else ""

    if push_type == "STREAM":
        kv = _parse_kv(rest)
        return ("stream", StreamData(
            target_l=_f(kv.get("tl")),
            target_r=_f(kv.get("tr")),
            vl=_f(kv.get("vl")),
            vr=_f(kv.get("vr")),
            dl=_f(kv.get("dl")),
            dr=_f(kv.get("dr")),
            vbat=_f(kv.get("vbat")),
            battery=kv.get("battery", ""),
            extra={k: v for k, v in kv.items() if k not in _STREAM_KNOWN},
        ))
    if push_type == "WARN":
        # ``! WARN <type> [field=value …]``
        sub = rest.split(None, 1)
        wtype = sub[0] if sub else ""
        fields = _parse_kv(sub[1]) if len(sub) > 1 else {}
        return ("warn", WarnEvent(type=wtype, fields=fields))
    if push_type == "FAULT":
        # ``! FAULT <fault_code>``
        code = rest.split(None, 1)[0] if rest else ""
        return ("fault", FaultEvent(code=code))
    return None
