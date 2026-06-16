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


class SerialTransport(Transport):
    """Transport over USB serial or TCP socket (via pyserial serial_for_url)."""

    def __init__(self, url: str, baudrate: int = 115200) -> None:
        self._url = url
        self._baudrate = baudrate
        self._serial: Optional[serial.Serial] = None
        self._write_lock = threading.Lock()

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
        if not self._serial or not self._serial.is_open:
            raise ConnectionError("Not connected")
        self._serial.timeout = timeout
        raw = self._serial.readline()
        if not raw:
            raise TimeoutError("No data received")
        return raw.decode(errors="replace").strip()

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
