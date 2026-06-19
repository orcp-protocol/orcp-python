"""Main ORCP client class."""
import queue
import threading
from typing import Callable, Optional

from .exceptions import CommandError, ConnectionError, ORCPError, TimeoutError
from .heartbeat import HeartbeatThread
from .models import FaultEvent, InfoResponse, StatusResponse, StreamData, WarnEvent
from .parser import parse_push, parse_response
from .transport import SerialTransport, Transport


class ORCP:
    """Client for ORCP-compliant motor controllers.

    Supports USB serial, WiFi (TCP socket), and any custom Transport.

    Usage::

        with ORCP('/dev/cu.usbmodemXXXX') as robot:
            robot.ping()
            robot.cmd_vel(v=0.2, w=0.0)

        with ORCP('socket://192.168.4.1:3333') as robot:
            robot.preset('NORMAL')
            robot.enable()
            robot.start_heartbeat()
            robot.cmd_vel(v=0.5, w=0.0)
    """

    def __init__(
        self,
        url: str,
        baudrate: int = 115200,
        timeout: float = 2.0,
        _transport: Optional[Transport] = None,
    ) -> None:
        self._transport: Transport = _transport or SerialTransport(url, baudrate)
        self._timeout = timeout

        # Two locks: one for the full command cycle, one just for writes
        # so the heartbeat thread can send HB without blocking on a response wait.
        self._cmd_lock = threading.Lock()
        self._write_lock = threading.Lock()

        self._response_queue: queue.Queue = queue.Queue()
        self._stop_reader = threading.Event()
        self._reader_thread: Optional[threading.Thread] = None

        self._heartbeat = HeartbeatThread(self._send_hb_raw)

        self._stream_callback: Optional[Callable[[StreamData], None]] = None
        self._stream_queue: queue.Queue = queue.Queue()
        self._stream_executor: Optional[threading.Thread] = None

        self._last_status: Optional[StatusResponse] = None
        self._last_fault: Optional[str] = None

        self._warn_callback: Optional[Callable[[WarnEvent], None]] = None
        self._fault_callback: Optional[Callable[[FaultEvent], None]] = None

        self._open()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def _open(self) -> None:
        self._transport.connect()
        self._stop_reader.clear()
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True, name="orcp-reader"
        )
        self._reader_thread.start()

    def close(self) -> None:
        """Close the connection and stop all background threads."""
        self._heartbeat.stop()
        self._stream_callback = None
        self._stop_stream_executor()
        self._stop_reader.set()
        if self._reader_thread:
            self._reader_thread.join(timeout=0.5)
        self._transport.disconnect()

    def __enter__(self) -> "ORCP":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Background reader
    # ------------------------------------------------------------------

    def _reader_loop(self) -> None:
        while not self._stop_reader.is_set():
            try:
                line = self._transport.readline(timeout=0.1)
                if not line:
                    continue
                if line.startswith("!"):
                    self._handle_push(line)
                elif line == "OK HB":
                    pass  # Silently discard background heartbeat ACKs
                else:
                    self._response_queue.put(line)
            except TimeoutError:
                continue
            except Exception:
                if not self._stop_reader.is_set():
                    pass  # Could log unexpected errors here
                break

    def _handle_push(self, line: str) -> None:
        result = parse_push(line)
        if result is None:
            return
        kind, payload = result
        if kind == "stream":
            data: StreamData = payload
            if self._last_status:
                self._last_status.vbat = data.vbat
                self._last_status.battery = data.battery
            if self._stream_callback:
                self._stream_queue.put(data)
        elif kind == "warn":
            cb = self._warn_callback
            if cb:
                try:
                    cb(payload)
                except Exception:
                    pass
        elif kind == "fault":
            # Per §6.3, treat a ! FAULT push as authoritative: motors have
            # stopped. Update cached fault state without waiting for STATUS.
            self._last_fault = payload.code
            if self._last_status:
                self._last_status.fault = payload.code
            cb = self._fault_callback
            if cb:
                try:
                    cb(payload)
                except Exception:
                    pass

    def on_warn(self, callback: Optional[Callable[[WarnEvent], None]]) -> None:
        """Register a callback for ``! WARN`` push events (or None to clear).

        Invoked on the background reader thread — keep it quick and non-blocking.
        """
        self._warn_callback = callback

    def on_fault(self, callback: Optional[Callable[[FaultEvent], None]]) -> None:
        """Register a callback for ``! FAULT`` push events (or None to clear).

        Invoked on the background reader thread — keep it quick and non-blocking.
        """
        self._fault_callback = callback

    # ------------------------------------------------------------------
    # Command helpers
    # ------------------------------------------------------------------

    def _send_command(self, command: str) -> str:
        """Send a command and return the raw response line."""
        with self._cmd_lock:
            with self._write_lock:
                self._transport.send(command)
            try:
                return self._response_queue.get(timeout=self._timeout)
            except queue.Empty:
                raise TimeoutError(f"No response to command: {command!r}")

    def _send_hb_raw(self) -> None:
        """Send HB without waiting for a response (used by heartbeat thread)."""
        with self._write_lock:
            self._transport.send("HB")

    # ------------------------------------------------------------------
    # System commands
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        """Ping the controller. Returns True or raises ConnectionError."""
        return bool(parse_response(self._send_command("PING")))

    def info(self) -> InfoResponse:
        """Return firmware/hardware info."""
        result = parse_response(self._send_command("INFO"))
        assert isinstance(result, InfoResponse)
        return result

    def status(self) -> StatusResponse:
        """Return current controller status and update cached properties."""
        result = parse_response(self._send_command("STATUS"))
        assert isinstance(result, StatusResponse)
        self._last_status = result
        self._last_fault = result.fault
        return result

    # ------------------------------------------------------------------
    # Motion commands
    # ------------------------------------------------------------------

    def cmd_vel(self, v: float, w: float) -> None:
        """Send unicycle velocity command (m/s, rad/s)."""
        parse_response(self._send_command(f"CMD_VEL v={v:.4f} w={w:.4f}"))

    def wheel(self, l: float, r: float, mode: Optional[str] = None) -> None:
        """Send direct wheel command.

        Default semantics are rad/s closed-loop velocity (ORCP v1.1). A vendor
        ``mode`` (e.g. ``"DUTY"``) opts into an alternative control mode where
        supported by the controller.
        """
        cmd = f"WHEEL l={l:.4f} r={r:.4f}"
        if mode is not None:
            cmd += f" mode={mode}"
        parse_response(self._send_command(cmd))

    def stop(self) -> None:
        """Immediately stop the robot. Never raises an exception."""
        try:
            parse_response(self._send_command("STOP"))
        except (CommandError, TimeoutError, ORCPError):
            pass

    # ------------------------------------------------------------------
    # Safety commands
    # ------------------------------------------------------------------

    def preset(self, mode: str) -> None:
        """Switch safety preset: 'SLOW' or 'NORMAL'."""
        if mode not in ("SLOW", "NORMAL"):
            raise ValueError(f"preset must be 'SLOW' or 'NORMAL', got {mode!r}")
        parse_response(self._send_command(f"PRESET {mode}"))

    def enable(self) -> None:
        """Enable motors (required for NORMAL preset)."""
        parse_response(self._send_command("ENABLE ON"))

    def disable(self) -> None:
        """Disable motors."""
        parse_response(self._send_command("ENABLE OFF"))

    def heartbeat(self) -> None:
        """Send a single heartbeat (fire-and-forget)."""
        self._send_hb_raw()

    def start_heartbeat(self, interval: float = 0.1) -> None:
        """Start background heartbeat thread (sends HB every *interval* seconds)."""
        self._heartbeat = HeartbeatThread(self._send_hb_raw, interval=interval)
        self._heartbeat.start()

    def stop_heartbeat(self) -> None:
        """Stop the background heartbeat thread."""
        self._heartbeat.stop()

    # ------------------------------------------------------------------
    # Streaming telemetry
    # ------------------------------------------------------------------

    def stream_on(
        self,
        rate: int = 10,
        callback: Optional[Callable[[StreamData], None]] = None,
    ) -> None:
        """Enable telemetry streaming at *rate* Hz with an optional callback."""
        self._stream_callback = callback
        if callback:
            self._start_stream_executor()
        parse_response(self._send_command(f"STREAM ON {rate}"))

    def stream_off(self) -> None:
        """Disable telemetry streaming."""
        self._stream_callback = None
        self._stop_stream_executor()
        try:
            parse_response(self._send_command("STREAM OFF"))
        except Exception:
            pass

    def _start_stream_executor(self) -> None:
        if self._stream_executor and self._stream_executor.is_alive():
            return
        self._stream_executor = threading.Thread(
            target=self._stream_dispatch_loop, daemon=True, name="orcp-stream"
        )
        self._stream_executor.start()

    def _stop_stream_executor(self) -> None:
        if self._stream_executor and self._stream_executor.is_alive():
            self._stream_queue.put(None)  # Poison pill
            self._stream_executor.join(timeout=1.0)
        self._stream_executor = None

    def _stream_dispatch_loop(self) -> None:
        while True:
            item = self._stream_queue.get()
            if item is None:
                break
            cb = self._stream_callback
            if cb:
                try:
                    cb(item)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Configuration commands
    # ------------------------------------------------------------------

    def get(self, param: str) -> float:
        """Get a single configuration parameter value."""
        result = parse_response(self._send_command(f"GET {param}"))
        return float(result)

    def get_all(self) -> dict:
        """Get all configuration parameters as a ``{name: value}`` dict (``GET ALL``)."""
        result = parse_response(self._send_command("GET ALL"))
        return result if isinstance(result, dict) else {}

    def set(self, param: str, value: float) -> None:
        """Set a configuration parameter."""
        parse_response(self._send_command(f"SET {param}={value}"))

    def save(self) -> None:
        """Persist current configuration to flash."""
        parse_response(self._send_command("SAVE"))

    def load(self) -> None:
        """Load configuration from flash."""
        parse_response(self._send_command("LOAD"))

    def defaults(self) -> None:
        """Reset configuration to factory defaults."""
        parse_response(self._send_command("DEFAULTS"))

    # ------------------------------------------------------------------
    # Cached status properties
    # ------------------------------------------------------------------

    @property
    def battery_voltage(self) -> Optional[float]:
        """Battery voltage in volts (from last STATUS or STREAM)."""
        return self._last_status.vbat if self._last_status else None

    @property
    def battery(self) -> Optional[str]:
        """Battery level as the device reports it — a band label (``OK``/``LOW``/
        ``CRITICAL``/``NONE``) or a percentage string (e.g. ``"80%"``)."""
        return self._last_status.battery if self._last_status else None

    @property
    def is_enabled(self) -> Optional[bool]:
        """True if motors are enabled (from last STATUS)."""
        return self._last_status.enabled if self._last_status else None

    @property
    def fault(self) -> Optional[str]:
        """Most recent active fault code, or None — updated by STATUS and by
        ``! FAULT`` push messages."""
        return self._last_fault
