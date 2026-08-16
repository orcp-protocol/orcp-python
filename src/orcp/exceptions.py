class ErrorCode:
    """Standard ORCP v1.1 error codes (§2.6). Implementations MAY define more."""

    BAD_CMD = "BAD_CMD"
    BAD_ARG = "BAD_ARG"
    BAD_VAL = "BAD_VAL"
    TOO_LONG = "TOO_LONG"
    NOT_ENABLED = "NOT_ENABLED"
    ESTOP = "ESTOP"
    LOWBATT = "LOWBATT"
    HEARTBEAT = "HEARTBEAT"
    TIMEOUT = "TIMEOUT"
    NO_FEEDBACK = "NO_FEEDBACK"
    BUSY = "BUSY"
    CONFIG_CONFLICT = "CONFIG_CONFLICT"
    FLASH_ERR = "FLASH_ERR"


class ORCPError(Exception):
    """Base exception for all ORCP errors."""


class CommandError(ORCPError):
    """Raised when the controller returns an ERR response."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class ConnectionError(ORCPError):
    """Raised when the transport cannot connect or loses connection."""


class TimeoutError(ORCPError):
    """Raised when a command does not receive a response in time."""


class HoldRefused(ORCPError):
    """Raised when a controller declines a requested position hold.

    ⚠️ **The stop still happened.** This says the *hold* did not engage, not
    that the robot is still moving.

    ⚠️ **It is not a wire error.** ORCP v1.1 §STOP requires STOP to be accepted
    regardless of safety state — it never fails — so a controller reports a
    declined hold as ``OK STOP … hold=refused reason=<CODE>``, not as ``ERR``.
    The library raises here so that calling code cannot quietly carry on
    believing the robot is holding position when nothing is holding it. The
    protocol keeps its guarantee; the library keeps you honest.

    ``reason`` is the controller's code — commonly ``NOT_ENABLED``,
    ``NO_ENCODERS`` or ``UNSUPPORTED``, but vendors MAY define others, so treat
    an unrecognised value as a refusal rather than assuming it is not one.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"hold refused: {reason}")
