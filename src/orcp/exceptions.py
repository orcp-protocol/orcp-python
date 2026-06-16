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
