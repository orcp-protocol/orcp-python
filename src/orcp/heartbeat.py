"""Background heartbeat thread for NORMAL preset safety."""
import threading
from typing import Callable, Optional


class HeartbeatThread:
    """Sends a heartbeat at a fixed interval using a background daemon thread."""

    def __init__(self, send_fn: Callable[[], None], interval: float = 0.1) -> None:
        self._send_fn = send_fn
        self._interval = interval
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="orcp-heartbeat")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self._interval * 3)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval):
            try:
                self._send_fn()
            except Exception:
                pass  # Don't crash on transient errors; next beat will retry

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
