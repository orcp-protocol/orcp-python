"""Transport layer: serial (USB/WiFi) and mock implementations."""
import threading
import time
from abc import ABC, abstractmethod
from typing import Optional

import serial

from .exceptions import ConnectionError, TimeoutError


class Transport(ABC):
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def send(self, line: str) -> None: ...

    @abstractmethod
    def readline(self, timeout: float = 2.0) -> str: ...

    @property
    @abstractmethod
    def is_connected(self) -> bool: ...


# Cap on unparsed received bytes. Generous next to the longest real line
# (ORCP §2.2 caps commands at 256 bytes; a GET ALL response is ~1.5 kB on a
# 63-key device) while still bounding a device that never sends a newline.
_RX_MAX = 65536


class SerialTransport(Transport):
    """Transport over USB serial or TCP socket (via pyserial serial_for_url)."""

    def __init__(self, url: str, baudrate: int = 115200) -> None:
        self._url = url
        self._baudrate = baudrate
        self._serial: Optional[serial.Serial] = None
        self._write_lock = threading.Lock()
        # Receive buffer. ⚠️ REQUIRED FOR CORRECTNESS, not an optimisation —
        # see readline().
        self._rx = bytearray()

    def connect(self) -> None:
        try:
            self._serial = serial.serial_for_url(
                self._url,
                baudrate=self._baudrate,
                timeout=0.1,
            )
            if "://" not in self._url:
                # USB serial open triggers a DTR reset on the microcontroller.
                # Wait for it to boot before sending any commands.
                time.sleep(2.0)
                self._serial.reset_input_buffer()
            self._rx.clear()
        except serial.SerialException as exc:
            raise ConnectionError(str(exc)) from exc

    def disconnect(self) -> None:
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._serial = None

    def send(self, line: str) -> None:
        if not self._serial or not self._serial.is_open:
            raise ConnectionError("Not connected")
        with self._write_lock:
            self._serial.write((line + "\n").encode())
            self._serial.flush()

    def readline(self, timeout: float = 2.0) -> str:
        """Return one complete line, or raise TimeoutError if none arrives.

        ⚠️ **Do not replace this with ``serial.readline()``.** pyserial returns
        whatever bytes it has when its timeout expires, *including a partial
        line*. The reader thread polls with a short timeout while a device is
        streaming telemetry, so a push message gets split mid-line — and the
        second half does not start with ``!``, so it is taken for a command
        response and handed to whichever command is waiting. Observed against
        the simulator with ``STREAM ON`` and a 0.1 s poll::

            ORCPError: Unexpected response: 'ttery=94% t=15764 el=0 er=0'

        (the tail of ``! STREAM … battery=94% …``, split inside "battery").

        It is timing-dependent, so it survives light testing and gets worse with
        latency — i.e. worst over WiFi, on a real robot, under streaming load.
        Buffering here makes a timeout mean "no COMPLETE line yet", which is
        what every caller already assumes it means.
        """
        if not self._serial or not self._serial.is_open:
            raise ConnectionError("Not connected")

        deadline = time.monotonic() + timeout
        while True:
            nl = self._rx.find(b"\n")
            if nl >= 0:
                line = bytes(self._rx[:nl])
                del self._rx[:nl + 1]
                return line.decode(errors="replace").strip()

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("No data received")

            # Block for one byte, then drain whatever else has landed.
            self._serial.timeout = remaining
            chunk = self._serial.read(1)
            if chunk:
                waiting = getattr(self._serial, "in_waiting", 0) or 0
                if waiting:
                    chunk += self._serial.read(waiting)
                self._rx += chunk
                # ⚠️ Bound the buffer. A device emitting no newline at all must
                # not grow this without limit; drop the oldest bytes, which are
                # the least likely to still be useful.
                if len(self._rx) > _RX_MAX:
                    del self._rx[:len(self._rx) - _RX_MAX]

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open


class MockTransport(Transport):
    """In-memory transport for unit testing — no hardware required."""

    def __init__(self) -> None:
        self._connected = False
        self._responses: list = []
        self._sent: list = []
        self._lock = threading.Lock()

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def send(self, line: str) -> None:
        if not self._connected:
            raise ConnectionError("Not connected")
        with self._lock:
            self._sent.append(line)

    def readline(self, timeout: float = 2.0) -> str:
        if not self._connected:
            raise ConnectionError("Not connected")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._responses:
                    return self._responses.pop(0)
            time.sleep(0.01)
        raise TimeoutError("No response queued")

    def queue_response(self, line: str) -> None:
        """Enqueue a line to be returned by the next readline() call."""
        with self._lock:
            self._responses.append(line)

    @property
    def sent(self) -> list:
        """Return a copy of all lines sent so far."""
        with self._lock:
            return list(self._sent)

    @property
    def is_connected(self) -> bool:
        return self._connected
