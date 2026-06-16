import time

import pytest
from orcp.heartbeat import HeartbeatThread


class TestHeartbeatThread:
    def test_starts_and_is_running(self):
        calls = []
        hb = HeartbeatThread(lambda: calls.append(1), interval=0.05)
        hb.start()
        assert hb.is_running
        time.sleep(0.15)
        hb.stop()
        assert not hb.is_running
        assert len(calls) >= 2

    def test_sends_at_correct_interval(self):
        calls = []
        hb = HeartbeatThread(lambda: calls.append(time.monotonic()), interval=0.05)
        hb.start()
        time.sleep(0.25)
        hb.stop()
        # Should have fired ~4-5 times in 0.25s at 0.05s interval
        assert 3 <= len(calls) <= 7

    def test_stop_halts_sending(self):
        calls = []
        hb = HeartbeatThread(lambda: calls.append(1), interval=0.02)
        hb.start()
        time.sleep(0.1)
        hb.stop()
        count_at_stop = len(calls)
        time.sleep(0.1)
        assert len(calls) == count_at_stop  # No more calls after stop

    def test_start_is_idempotent(self):
        hb = HeartbeatThread(lambda: None, interval=0.05)
        hb.start()
        thread = hb._thread
        hb.start()  # Should not create a second thread
        assert hb._thread is thread
        hb.stop()

    def test_exception_in_callback_does_not_crash(self):
        def bad_fn():
            raise RuntimeError("simulated failure")

        hb = HeartbeatThread(bad_fn, interval=0.02)
        hb.start()
        time.sleep(0.1)
        assert hb.is_running  # Thread should still be alive
        hb.stop()

    def test_thread_is_daemon(self):
        hb = HeartbeatThread(lambda: None, interval=0.05)
        hb.start()
        assert hb._thread is not None
        assert hb._thread.daemon
        hb.stop()
