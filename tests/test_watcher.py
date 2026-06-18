import pytest

# Skip the whole module if the meshtastic package is not installed (e.g. a
# minimal CI runner). When dev dependencies are installed these run for real.
pytest.importorskip("meshtastic")

from meshtastic_watcher import MeshtasticWatcher


def text_packet(text, from_id="!abcdef01", to_id="^all", portnum="TEXT_MESSAGE_APP"):
    return {
        "decoded": {"portnum": portnum, "text": text},
        "fromId": from_id,
        "toId": to_id,
    }


class FakeInterface:
    def __init__(self, nodes=None):
        self.nodes = nodes or {}


class TestTrigger:
    def test_trigger_fires_callback(self, make_config):
        w = MeshtasticWatcher(make_config(my_node_id="!abcdef01"))
        captured = []
        w.on_trigger = lambda kw, msg, pos: captured.append((kw, msg))
        w._handle_packet(text_packet("SOS"), FakeInterface())
        assert captured == [("SOS", "SOS")]

    def test_word_boundary_match(self, make_config):
        w = MeshtasticWatcher(make_config(my_node_id="!abcdef01"))
        captured = []
        w.on_trigger = lambda kw, msg, pos: captured.append(kw)
        w._handle_packet(text_packet("need HELP!"), FakeInterface())
        assert captured == ["HELP"]

    def test_substring_keyword_does_not_match(self, make_config):
        w = MeshtasticWatcher(make_config(my_node_id="!abcdef01"))
        captured = []
        w.on_trigger = lambda kw, msg, pos: captured.append(kw)
        w._handle_packet(text_packet("sossisson is tasty"), FakeInterface())
        assert captured == []

    def test_case_insensitive(self, make_config):
        w = MeshtasticWatcher(make_config(my_node_id="!abcdef01"))
        captured = []
        w.on_trigger = lambda kw, msg, pos: captured.append(kw)
        w._handle_packet(text_packet("mayday"), FakeInterface())
        assert captured == ["MAYDAY"]

    def test_empty_text_ignored(self, make_config):
        w = MeshtasticWatcher(make_config(my_node_id="!abcdef01"))
        captured = []
        w.on_trigger = lambda kw, msg, pos: captured.append(kw)
        w._handle_packet(text_packet("   "), FakeInterface())
        assert captured == []


class TestHeartbeat:
    def test_heartbeat_calls_callback(self, make_config):
        w = MeshtasticWatcher(make_config(my_node_id="!abcdef01"))
        beats = []
        w.on_heartbeat = lambda: beats.append(1)
        w._handle_packet(text_packet("CHECKIN"), FakeInterface())
        assert beats == [1]

    def test_heartbeat_does_not_trigger_alert(self, make_config):
        w = MeshtasticWatcher(make_config(my_node_id="!abcdef01"))
        triggered = []
        w.on_trigger = lambda kw, msg, pos: triggered.append(kw)
        w.on_heartbeat = lambda: None
        w._handle_packet(text_packet("CHECKIN"), FakeInterface())
        assert triggered == []


class TestSourceFilter:
    def test_ignored_from_other_node(self, make_config):
        w = MeshtasticWatcher(make_config(my_node_id="!abcdef01"))
        captured = []
        w.on_trigger = lambda kw, msg, pos: captured.append(kw)
        w._handle_packet(text_packet("SOS", from_id="!abcdef01"), FakeInterface())
        w._handle_packet(text_packet("SOS", from_id="!zzzz9999"), FakeInterface())
        assert captured == ["SOS"]

    def test_require_false_allows_any_node(self, make_config):
        w = MeshtasticWatcher(
            make_config(my_node_id="!abcdef01", require_from_my_node=False)
        )
        captured = []
        w.on_trigger = lambda kw, msg, pos: captured.append(kw)
        w._handle_packet(text_packet("SOS", from_id="!zzzz9999"), FakeInterface())
        assert captured == ["SOS"]

    def test_no_filter_when_my_node_id_unset(self, make_config):
        w = MeshtasticWatcher(
            make_config(my_node_id=None, require_from_my_node=True)
        )
        captured = []
        w.on_trigger = lambda kw, msg, pos: captured.append(kw)
        w._handle_packet(text_packet("SOS", from_id="!anyone"), FakeInterface())
        assert captured == ["SOS"]


class TestPositionHandling:
    def test_position_packet_updates_cache(self, make_config):
        w = MeshtasticWatcher(make_config())
        pkt = {
            "decoded": {
                "portnum": "POSITION_APP",
                "position": {"latitude": 10.0, "longitude": 20.0},
            }
        }
        w._handle_packet(pkt, FakeInterface())
        p = w.get_last_position()
        assert p is not None
        assert p.latitude == 10.0
        assert p.longitude == 20.0

    def test_uses_position_from_node_db(self, make_config):
        nodes = {
            "n1": {"user": {"id": "!abcdef01"},
                   "position": {"latitude": 1.0, "longitude": 2.0}},
        }
        w = MeshtasticWatcher(make_config(my_node_id="!abcdef01"))
        captured = []
        w.on_trigger = lambda kw, msg, pos: captured.append(pos)
        w._handle_packet(text_packet("SOS", from_id="!abcdef01"), FakeInterface(nodes))
        assert captured and captured[0] is not None
        assert captured[0].latitude == 1.0
        assert captured[0].longitude == 2.0

    def test_falls_back_to_cached_position(self, make_config):
        w = MeshtasticWatcher(make_config(my_node_id="!abcdef01"))
        # Seed the cache with a POSITION packet.
        w._handle_packet(
            {"decoded": {"portnum": "POSITION_APP",
                         "position": {"latitude": 7.0, "longitude": 8.0}}},
            FakeInterface(),
        )
        captured = []
        w.on_trigger = lambda kw, msg, pos: captured.append(pos)
        # A text packet from the same node with no embedded position and no
        # matching node DB entry should fall back to the cache.
        w._handle_packet(text_packet("SOS", from_id="!abcdef01"), FakeInterface())
        assert captured and captured[0] is not None
        assert captured[0].latitude == 7.0


class TestPortnumFilter:
    def test_telemetry_ignored(self, make_config):
        w = MeshtasticWatcher(make_config())
        captured = []
        w.on_trigger = lambda kw, msg, pos: captured.append(kw)
        w._handle_packet(text_packet("", portnum="TELEMETRY_APP"), FakeInterface())
        assert captured == []