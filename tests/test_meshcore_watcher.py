import time

import pytest

# Skip the whole module if the meshcore package is not installed (e.g. a
# minimal CI runner). When dev dependencies are installed these run for real.
pytest.importorskip("meshcore")

from gps import Position
from watchers.meshcore import MeshCoreWatcher

_MY_KEY = "aabbccddeeff00112233"


def contact_message(text, pubkey_prefix="aabbccddeeff"):
    return {"text": text, "pubkey_prefix": pubkey_prefix}


class FakeMeshCore:
    """Stands in for the parts of MeshCore this watcher touches after connect."""

    def __init__(self, contacts=None):
        self._contacts = contacts or {}

    def get_contact_by_key_prefix(self, prefix):
        prefix = prefix.lower()
        for contact in self._contacts.values():
            if contact.get("public_key", "").lower().startswith(prefix):
                return contact
        return None


class TestTrigger:
    def test_trigger_fires_callback(self, make_config):
        w = MeshCoreWatcher(make_config(contact_public_key=_MY_KEY))
        captured = []
        w.on_trigger = lambda kw, msg, pos: captured.append((kw, msg))
        w._handle_message(contact_message("SOS"))
        assert captured == [("SOS", "SOS")]

    def test_word_boundary_match(self, make_config):
        w = MeshCoreWatcher(make_config(contact_public_key=_MY_KEY))
        captured = []
        w.on_trigger = lambda kw, msg, pos: captured.append(kw)
        w._handle_message(contact_message("need HELP!"))
        assert captured == ["HELP"]

    def test_substring_keyword_does_not_match(self, make_config):
        w = MeshCoreWatcher(make_config(contact_public_key=_MY_KEY))
        captured = []
        w.on_trigger = lambda kw, msg, pos: captured.append(kw)
        w._handle_message(contact_message("sossisson is tasty"))
        assert captured == []

    def test_case_insensitive(self, make_config):
        w = MeshCoreWatcher(make_config(contact_public_key=_MY_KEY))
        captured = []
        w.on_trigger = lambda kw, msg, pos: captured.append(kw)
        w._handle_message(contact_message("mayday"))
        assert captured == ["MAYDAY"]

    def test_empty_text_ignored(self, make_config):
        w = MeshCoreWatcher(make_config(contact_public_key=_MY_KEY))
        captured = []
        w.on_trigger = lambda kw, msg, pos: captured.append(kw)
        w._handle_message(contact_message("   "))
        assert captured == []


class TestHeartbeat:
    def test_heartbeat_calls_callback(self, make_config):
        w = MeshCoreWatcher(make_config(contact_public_key=_MY_KEY))
        beats = []
        w.on_heartbeat = lambda: beats.append(1)
        w._handle_message(contact_message("CHECKIN"))
        assert beats == [1]

    def test_heartbeat_does_not_trigger_alert(self, make_config):
        w = MeshCoreWatcher(make_config(contact_public_key=_MY_KEY))
        triggered = []
        w.on_trigger = lambda kw, msg, pos: triggered.append(kw)
        w.on_heartbeat = lambda: None
        w._handle_message(contact_message("CHECKIN"))
        assert triggered == []


class TestSourceFilter:
    def test_ignored_from_other_contact(self, make_config):
        w = MeshCoreWatcher(make_config(contact_public_key=_MY_KEY))
        captured = []
        w.on_trigger = lambda kw, msg, pos: captured.append(kw)
        w._handle_message(contact_message("SOS", pubkey_prefix="aabbccddeeff"))
        w._handle_message(contact_message("SOS", pubkey_prefix="ffffffffffff"))
        assert captured == ["SOS"]

    def test_require_false_allows_any_contact(self, make_config):
        w = MeshCoreWatcher(
            make_config(contact_public_key=_MY_KEY, require_from_my_node=False)
        )
        captured = []
        w.on_trigger = lambda kw, msg, pos: captured.append(kw)
        w._handle_message(contact_message("SOS", pubkey_prefix="ffffffffffff"))
        assert captured == ["SOS"]

    def test_no_filter_when_public_key_unset(self, make_config):
        w = MeshCoreWatcher(
            make_config(contact_public_key=None, require_from_my_node=True)
        )
        captured = []
        w.on_trigger = lambda kw, msg, pos: captured.append(kw)
        w._handle_message(contact_message("SOS", pubkey_prefix="ffffffffffff"))
        assert captured == ["SOS"]


class TestPositionHandling:
    def test_uses_position_from_contact(self, make_config):
        w = MeshCoreWatcher(make_config(contact_public_key=_MY_KEY))
        w._mc = FakeMeshCore(
            {_MY_KEY: {"public_key": _MY_KEY, "adv_lat": 1.0, "adv_lon": 2.0}}
        )
        captured = []
        w.on_trigger = lambda kw, msg, pos: captured.append(pos)
        w._handle_message(contact_message("SOS"))
        assert captured and captured[0] is not None
        assert captured[0].latitude == 1.0
        assert captured[0].longitude == 2.0

    def test_falls_back_to_cached_position(self, make_config):
        w = MeshCoreWatcher(make_config(contact_public_key=_MY_KEY))
        # No contact DB available (e.g. get_contacts hasn't run yet) — the
        # watcher should fall back to whatever it last cached.
        w._last_position = Position(latitude=7.0, longitude=8.0)
        captured = []
        w.on_trigger = lambda kw, msg, pos: captured.append(pos)
        w._handle_message(contact_message("SOS"))
        assert captured and captured[0] is not None
        assert captured[0].latitude == 7.0

    def test_unset_contact_position_ignored(self, make_config):
        # adv_lat/adv_lon default to 0,0 on a node that has never advertised
        # a position — that's "unset", not a real coordinate at (0, 0).
        w = MeshCoreWatcher(make_config(contact_public_key=_MY_KEY))
        w._mc = FakeMeshCore(
            {_MY_KEY: {"public_key": _MY_KEY, "adv_lat": 0.0, "adv_lon": 0.0}}
        )
        captured = []
        w.on_trigger = lambda kw, msg, pos: captured.append(pos)
        w._handle_message(contact_message("SOS"))
        assert captured == [None]


class TestReconnectBackoff:
    def test_first_failure_uses_base_delay(self, make_config):
        w = MeshCoreWatcher(make_config())
        assert w._next_reconnect_delay() == 10

    def test_flapping_doubles_delay_each_time(self, make_config):
        w = MeshCoreWatcher(make_config())
        assert w._next_reconnect_delay() == 10
        assert w._next_reconnect_delay() == 20
        assert w._next_reconnect_delay() == 40

    def test_delay_caps_at_max(self, make_config):
        w = MeshCoreWatcher(make_config())
        for _ in range(10):
            delay = w._next_reconnect_delay()
        assert delay == 300

    def test_healthy_connection_resets_backoff(self, make_config):
        w = MeshCoreWatcher(make_config())
        w._next_reconnect_delay()
        w._next_reconnect_delay()
        # Simulate a connection that stayed up past the flap window.
        w._connected_at = time.time() - 61
        assert w._next_reconnect_delay() == 10
