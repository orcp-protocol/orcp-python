import threading
import time

import pytest
from orcp.exceptions import ConnectionError, TimeoutError
from orcp.transport import MockTransport


class TestMockTransport:
    def test_connect_disconnect(self):
        t = MockTransport()
        assert not t.is_connected
        t.connect()
        assert t.is_connected
        t.disconnect()
        assert not t.is_connected

    def test_send_records_lines(self):
        t = MockTransport()
        t.connect()
        t.send("PING")
        t.send("CMD_VEL v=0.5 w=0.0")
        assert t.sent == ["PING", "CMD_VEL v=0.5 w=0.0"]

    def test_readline_returns_queued_response(self):
        t = MockTransport()
        t.connect()
        t.queue_response("OK PING")
        assert t.readline() == "OK PING"

    def test_readline_blocks_then_times_out(self):
        t = MockTransport()
        t.connect()
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            t.readline(timeout=0.05)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.04

    def test_send_when_disconnected_raises(self):
        t = MockTransport()
        with pytest.raises(ConnectionError):
            t.send("PING")

    def test_readline_when_disconnected_raises(self):
        t = MockTransport()
        with pytest.raises(ConnectionError):
            t.readline()

    def test_queue_multiple_responses_in_order(self):
        t = MockTransport()
        t.connect()
        t.queue_response("OK PING")
        t.queue_response("OK INFO firmware=1.0 hardware=BP device_id=ABC")
        assert t.readline() == "OK PING"
        assert t.readline().startswith("OK INFO")

    def test_sent_returns_copy(self):
        t = MockTransport()
        t.connect()
        t.send("PING")
        sent = t.sent
        sent.append("EXTRA")
        assert len(t.sent) == 1  # original is unchanged


class _ChunkedSerial:
    """A fake pyserial port that hands back data in arbitrary chunks.

    Models the behaviour that broke the real client: bytes arrive split at
    positions that have nothing to do with line boundaries.
    """

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.timeout = 0.1
        self.is_open = True

    def read(self, n=1):
        if not self._chunks:
            return b""
        chunk = self._chunks.pop(0)
        return chunk

    @property
    def in_waiting(self):
        return 0

    def close(self):
        self.is_open = False


class TestPartialLineHandling:
    """⚠️ Regression tests for a bug that only appeared under streaming load.

    pyserial's readline() returns whatever it has when its timeout expires,
    INCLUDING a partial line. The client's reader thread polls with a short
    timeout, so a telemetry push got split mid-line — and the tail, which does
    not start with '!', was taken for a command response and handed to whichever
    command was waiting:

        ORCPError: Unexpected response: 'ttery=94% t=15764 el=0 er=0'

    (the end of '! STREAM ... battery=94% ...', split inside "battery").
    Timing-dependent, so it survived the unit suite — MockTransport always
    returns whole lines — and gets worse with latency, i.e. worst over WiFi on
    a real robot under load.
    """

    def test_line_split_across_reads_is_reassembled(self):
        from orcp.transport import SerialTransport

        t = SerialTransport("mock://")
        t._serial = _ChunkedSerial([b"! STREAM tl=0.000 ba", b"ttery=94% t=1\n"])
        assert t.readline(timeout=1.0) == "! STREAM tl=0.000 battery=94% t=1"

    def test_multiple_lines_in_one_chunk_are_split(self):
        from orcp.transport import SerialTransport

        t = SerialTransport("mock://")
        t._serial = _ChunkedSerial([b"OK PING t=1\nOK STATUS preset=SLOW\n"])
        assert t.readline(timeout=1.0) == "OK PING t=1"
        assert t.readline(timeout=1.0) == "OK STATUS preset=SLOW"

    def test_incomplete_line_times_out_rather_than_returning_a_fragment(self):
        """A timeout must mean "no COMPLETE line yet" — which is what every
        caller already assumes it means."""
        from orcp.transport import SerialTransport
        from orcp.exceptions import TimeoutError as ORCPTimeout

        t = SerialTransport("mock://")
        t._serial = _ChunkedSerial([b"OK PING partial, no newline"])
        with pytest.raises(ORCPTimeout):
            t.readline(timeout=0.2)
        # …and the fragment is retained, so the rest of the line still arrives.
        t._serial = _ChunkedSerial([b" t=1\n"])
        assert t.readline(timeout=1.0) == "OK PING partial, no newline t=1"
