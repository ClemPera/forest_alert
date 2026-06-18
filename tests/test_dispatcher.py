import threading
import time
from unittest.mock import patch

from dispatcher import AlertDispatcher


def _all_fail(*args, **kwargs):
    return {"signal": False, "email": False}


class TestSubmitAndDelivery:
    def test_submit_returns_true_when_accepted(self, make_config):
        with patch("dispatcher.fire_all", return_value={"signal": True, "email": True}):
            d = AlertDispatcher(make_config(), lambda: None)
            d.start()
            try:
                assert d.submit("SOS", "help") is True
            finally:
                d.stop()

    def test_submit_does_not_block_on_slow_fire_all(self, make_config):
        release = threading.Event()

        def slow(*a, **k):
            release.wait(timeout=5)
            return {"signal": True, "email": True}

        with patch("dispatcher.fire_all", side_effect=slow):
            d = AlertDispatcher(make_config(), lambda: None)
            d.start()
            try:
                t0 = time.time()
                assert d.submit("SOS", "help") is True
                assert time.time() - t0 < 1.0
            finally:
                release.set()
                d.stop()


class TestCooldown:
    def test_success_armours_cooldown_and_suppresses_repeat(self, make_config, wait_for):
        cfg = make_config(cooldown_seconds=300)
        with patch("dispatcher.fire_all", return_value={"signal": True, "email": True}):
            d = AlertDispatcher(cfg, lambda: None)
            d.start()
            try:
                assert d.submit("SOS", "help") is True
                assert wait_for(lambda: d._last_success.get("SOS") is not None)
                # Second submit of the same type is suppressed by cooldown.
                assert d.submit("SOS", "help2") is False
            finally:
                d.stop()

    def test_failed_burst_does_not_arm_cooldown(self, make_config, wait_for):
        # Issue #2: a failed burst must NOT block the next attempt.
        cfg = make_config(cooldown_seconds=300)
        calls = {"n": 0}

        def fake(*a, **k):
            calls["n"] += 1
            return {"signal": False, "email": False}

        with patch("dispatcher.fire_all", side_effect=fake):
            d = AlertDispatcher(cfg, lambda: None, max_attempts=1)
            d.start()
            try:
                assert d.submit("SOS", "help") is True
                assert wait_for(lambda: calls["n"] == 1)
                # Cooldown must NOT be armed after a failure.
                assert d._last_success.get("SOS") is None
            finally:
                d.stop()

    def test_per_type_cooldown_independence(self, make_config, wait_for):
        # Issue #1: a dead-man alert must not suppress a later SOS.
        cfg = make_config(cooldown_seconds=300)
        with patch("dispatcher.fire_all", return_value={"signal": True, "email": True}):
            d = AlertDispatcher(cfg, lambda: None)
            d.start()
            try:
                assert d.submit("Dead Man Switch", "no hb") is True
                assert wait_for(lambda: d._last_success.get("Dead Man Switch") is not None)
                # Different type → not suppressed.
                assert d.submit("SOS Meshtastic [SOS]", "help") is True
            finally:
                d.stop()


class TestInFlightDedupe:
    def test_duplicate_while_in_flight_is_dropped(self, make_config, wait_for):
        started = threading.Event()
        release = threading.Event()
        calls = {"n": 0}

        def fake(*a, **k):
            calls["n"] += 1
            started.set()
            release.wait(timeout=5)
            return {"signal": True, "email": True}

        with patch("dispatcher.fire_all", side_effect=fake):
            d = AlertDispatcher(make_config(), lambda: None)
            d.start()
            try:
                assert d.submit("SOS", "first") is True
                assert started.wait(timeout=5)        # first attempt now in flight
                assert d.submit("SOS", "second") is False  # deduped
            finally:
                release.set()
                d.stop()
        assert calls["n"] == 1


class TestRetry:
    def test_retries_until_success(self, make_config, wait_for, monkeypatch):
        monkeypatch.setattr("dispatcher._RETRY_BASE_SECONDS", 0.01)
        monkeypatch.setattr("dispatcher._RETRY_CAP_SECONDS", 0.05)
        cfg = make_config()
        succeeded = threading.Event()
        state = {"n": 0}

        def fake(*a, **k):
            state["n"] += 1
            ok = state["n"] >= 2
            if ok:
                succeeded.set()
            return {"signal": ok, "email": False}

        with patch("dispatcher.fire_all", side_effect=fake):
            d = AlertDispatcher(cfg, lambda: None, max_attempts=10)
            d.start()
            try:
                assert d.submit("SOS", "help") is True
                assert succeeded.wait(timeout=5)
            finally:
                d.stop()
        assert state["n"] >= 2

    def test_gives_up_after_max_attempts(self, make_config, wait_for, monkeypatch):
        monkeypatch.setattr("dispatcher._RETRY_BASE_SECONDS", 0.01)
        monkeypatch.setattr("dispatcher._RETRY_CAP_SECONDS", 0.05)
        cfg = make_config()
        state = {"n": 0}
        done = threading.Event()

        def fake(*a, **k):
            state["n"] += 1
            if state["n"] >= 3:
                done.set()
            return {"signal": False, "email": False}

        with patch("dispatcher.fire_all", side_effect=fake):
            d = AlertDispatcher(cfg, lambda: None, max_attempts=3)
            d.start()
            try:
                assert d.submit("SOS", "help") is True
                assert done.wait(timeout=5)
                # The give-up happens in the same tick as the 3rd attempt;
                # give it a moment so no 4th attempt can sneak in.
                time.sleep(0.1)
            finally:
                d.stop()
        assert state["n"] == 3

    def test_cancel_stops_pending_retry(self, make_config, wait_for, monkeypatch):
        monkeypatch.setattr("dispatcher._RETRY_BASE_SECONDS", 5.0)
        monkeypatch.setattr("dispatcher._RETRY_CAP_SECONDS", 10.0)
        cfg = make_config()
        done = threading.Event()
        state = {"n": 0}

        def fake(*a, **k):
            state["n"] += 1
            done.set()
            return {"signal": False, "email": False}

        with patch("dispatcher.fire_all", side_effect=fake):
            d = AlertDispatcher(cfg, lambda: None, max_attempts=10)
            d.start()
            try:
                assert d.submit("SOS", "help") is True
                assert done.wait(timeout=5)        # first attempt done, retry scheduled
                d.cancel("SOS")                    # cancel the pending retry
            finally:
                d.stop()
        # No retry fired after the cancel.
        assert state["n"] == 1

    def test_cancel_is_noop_when_nothing_pending(self, make_config):
        with patch("dispatcher.fire_all", return_value={"signal": True, "email": True}):
            d = AlertDispatcher(make_config(), lambda: None)
            d.start()
            try:
                d.cancel("SOS")  # should not raise
            finally:
                d.stop()