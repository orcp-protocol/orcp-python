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

    # ── Vendor extensions ────────────────────────────────────────────────
    # Not ORCP v1.1. Both are ``None`` when the device does not report the
    # field at all.
    #
    # ⚠️ ``None`` and ``0`` are DIFFERENT ANSWERS and must not be conflated:
    # ``hold is None`` means the controller has no position-hold feature, while
    # ``hold == 0`` means it has one and is not currently holding. Code that
    # treats a missing field as "not holding" will read a controller that cannot
    # hold as one that simply is not holding right now.
    coast: Optional[bool] = None       # coast= — rolling toward a parking brake
    hold: Optional[int] = None         # hold= — 0 not holding · 1 holding ·
                                       # 2 ended by fault or timeout
    extra: Dict[str, str] = field(default_factory=dict)

    @property
    def is_holding(self) -> bool:
        """True only while actively holding position (``hold=1``)."""
        return self.hold == 1

    @property
    def hold_broken(self) -> bool:
        """True when a hold ended on a fault or the thermal timeout (``hold=2``).

        ⚠️ This is the state worth alerting a human about: the robot was under
        active position control — possibly on a gradient — and is not any more.
        It stays true until the next command clears it."""
        return self.hold == 2


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
