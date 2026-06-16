"""Typed representations of ORCP v1.1 responses and push events."""
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class InfoResponse:
    """Response from the INFO command (ORCP v1.1 §4)."""

    fw: str = ""                       # firmware version
    hw: str = ""                       # hardware identifier
    proto: str = ""                    # protocol version, e.g. "ORCP/1.1"
    level: Optional[int] = None        # declared conformance level (1/2/3)
    vendor: Optional[str] = None
    model: Optional[str] = None
    # Any other INFO fields a device emits (e.g. bl=, uuid=, device_id=).
    extra: Dict[str, str] = field(default_factory=dict)


@dataclass
class StatusResponse:
    """Response from the STATUS command (ORCP v1.1 §4)."""

    preset: str = ""
    mode: str = ""                     # IDLE / VELOCITY / OPEN_LOOP
    enabled: bool = False              # en=
    fault: Optional[str] = None        # None when fault=OK
    estop: bool = False
    target_l: float = 0.0              # tl= (rad/s)
    target_r: float = 0.0              # tr=
    vl: float = 0.0                    # measured velocity (rad/s)
    vr: float = 0.0
    dl: float = 0.0                    # duty cycle (-1..1)
    dr: float = 0.0
    duty_limit: float = 0.0            # lim=
    vbat: float = 0.0                  # battery voltage (V)
    battery: str = ""                  # band label (OK/LOW/…) or percentage (e.g. "80%")
    extra: Dict[str, str] = field(default_factory=dict)


@dataclass
class StreamData:
    """Telemetry from a ``! STREAM`` push message (ORCP v1.1 §6.1)."""

    target_l: float = 0.0              # tl=
    target_r: float = 0.0              # tr=
    vl: float = 0.0
    vr: float = 0.0
    dl: float = 0.0
    dr: float = 0.0
    vbat: float = 0.0
    battery: str = ""
    extra: Dict[str, str] = field(default_factory=dict)


@dataclass
class WarnEvent:
    """A ``! WARN <type> [field=value …]`` push message (ORCP v1.1 §6.2)."""

    type: str                          # e.g. "BATT", "AUX5V"
    fields: Dict[str, str] = field(default_factory=dict)


@dataclass
class FaultEvent:
    """A ``! FAULT <fault_code>`` push message (ORCP v1.1 §6.3)."""

    code: str
