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
