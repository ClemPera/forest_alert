import textwrap
from pathlib import Path

import pytest

from config import load_config


def write_config(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(textwrap.dedent(content))
    return p


class TestLoadConfig:
    def test_minimal_valid_uses_defaults(self, tmp_path):
        p = write_config(tmp_path, """
            [meshtastic]
            host = "192.168.1.10"
        """)
        cfg = load_config(p)
        assert cfg.meshtastic.host == "192.168.1.10"
        assert cfg.meshtastic.port == 4403
        assert cfg.meshtastic.reconnect_delay == 10
        assert cfg.trigger.keywords == ["HELP", "URGENCE", "SOS", "MAYDAY"]
        assert cfg.trigger.require_from_my_node is True
        assert cfg.trigger.my_node_id is None
        assert cfg.dead_man.heartbeat_keyword == "CHECKIN"
        assert cfg.dead_man.enabled is True
        assert cfg.signal.rpc_url.startswith("http://")
        assert cfg.signal.contacts == []
        assert cfg.email.enabled is False
        assert cfg.cooldown.seconds == 300

    def test_missing_host_raises(self, tmp_path):
        p = write_config(tmp_path, """
            [trigger]
            keywords = ["SOS"]
        """)
        with pytest.raises(ValueError, match="host"):
            load_config(p)

    def test_empty_file_raises(self, tmp_path):
        p = write_config(tmp_path, "")
        with pytest.raises(ValueError, match="host"):
            load_config(p)

    def test_override_defaults(self, tmp_path):
        p = write_config(tmp_path, """
            [meshtastic]
            host = "10.0.0.1"
            port = 9999
            reconnect_delay = 3

            [trigger]
            keywords = ["MAYDAY"]
            my_node_id = "!abcd1234"
            require_from_my_node = false

            [dead_man]
            heartbeat_keyword = "ALIVE"
            heartbeat_interval_hours = 2.0

            [cooldown]
            seconds = 60
        """)
        cfg = load_config(p)
        assert cfg.meshtastic.port == 9999
        assert cfg.meshtastic.reconnect_delay == 3
        assert cfg.trigger.keywords == ["MAYDAY"]
        assert cfg.trigger.my_node_id == "!abcd1234"
        assert cfg.trigger.require_from_my_node is False
        assert cfg.dead_man.heartbeat_keyword == "ALIVE"
        assert cfg.dead_man.heartbeat_interval_hours == 2.0
        assert cfg.cooldown.seconds == 60

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.toml")