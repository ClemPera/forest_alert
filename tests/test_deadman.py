import time

from config import DeadManConfig
from deadman import DeadManSwitch
from state import save_last_heartbeat


def make_cfg(**kw):
    defaults = dict(
        enabled=True,
        heartbeat_keyword="CHECKIN",
        heartbeat_interval_hours=2 / 3600,   # 2 seconds
        grace_period_hours=0,
        check_interval_minutes=0.5 / 60,    # 0.5 seconds
    )
    defaults.update(kw)
    return DeadManConfig(**defaults)


def _wait_for(predicate, timeout=5.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


class TestArming:
    def test_disabled_does_not_fire(self):
        fired = []
        dm = DeadManSwitch(make_cfg(enabled=False), lambda r: fired.append(r))
        dm.start()
        time.sleep(0.3)
        dm.stop()
        assert fired == []

    def test_fresh_start_arms_from_now_no_immediate_fire(self, tmp_path):
        state = tmp_path / "hb.state"
        fired = []
        dm = DeadManSwitch(make_cfg(), lambda r: fired.append(r), state_path=state)
        dm.start()
        time.sleep(0.3)   # well before the 2s interval
        dm.stop()
        assert fired == []

    def test_fires_after_interval(self):
        fired = []
        dm = DeadManSwitch(make_cfg(), lambda r: fired.append(r))
        dm.start()
        time.sleep(3.0)
        dm.stop()
        assert len(fired) == 1
        assert "No heartbeat" in fired[0]

    def test_fires_only_once_per_incident(self):
        fired = []
        dm = DeadManSwitch(make_cfg(), lambda r: fired.append(r))
        dm.start()
        time.sleep(2.5)
        dm.stop()
        assert len(fired) == 1


class TestHeartbeat:
    def test_heartbeat_resets_timer(self):
        fired = []
        dm = DeadManSwitch(make_cfg(), lambda r: fired.append(r))
        dm.start()
        try:
            for _ in range(6):
                time.sleep(0.5)
                dm.record_heartbeat()   # keep it alive across the 2s interval
        finally:
            dm.stop()
        assert fired == []

    def test_heartbeat_after_fire_resets_and_refires_on_new_incident(self):
        fired = []
        dm = DeadManSwitch(make_cfg(), lambda r: fired.append(r))
        dm.start()
        _wait_for(lambda: len(fired) >= 1, timeout=4)
        dm.record_heartbeat()                 # clears _already_fired
        _wait_for(lambda: len(fired) >= 2, timeout=4)  # fires again after interval
        dm.stop()
        assert len(fired) == 2


class TestPersistence:
    def test_records_heartbeat_to_disk(self, tmp_path):
        state = tmp_path / "hb.state"
        dm = DeadManSwitch(make_cfg(), lambda r: None, state_path=state)
        dm.start()
        dm.record_heartbeat()
        assert state.exists()
        dm.stop()

    def test_restores_stale_state_and_fires_immediately(self, tmp_path):
        state = tmp_path / "hb.state"
        # A heartbeat from 10s ago is already overdue.
        save_last_heartbeat(state, time.time() - 10)
        fired = []
        dm = DeadManSwitch(make_cfg(), lambda r: fired.append(r), state_path=state)
        dm.start()   # immediate _check() should fire (overdue)
        time.sleep(0.3)
        dm.stop()
        assert len(fired) == 1

    def test_fresh_start_does_not_overwrite_existing_state(self, tmp_path):
        state = tmp_path / "hb.state"
        old = time.time() - 100
        save_last_heartbeat(state, old)
        dm = DeadManSwitch(make_cfg(), lambda r: None, state_path=state)
        dm.start()
        # Restored value should be the old one, not overwritten with now.
        assert abs(dm._last_heartbeat - old) < 1.0
        dm.stop()