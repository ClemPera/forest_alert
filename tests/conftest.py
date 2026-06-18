import time

import pytest

from config import (
    Config,
    CooldownConfig,
    DeadManConfig,
    EmailConfig,
    MeshtasticConfig,
    SignalConfig,
    TriggerConfig,
)


@pytest.fixture
def make_config():
    """Build a Config with sensible test defaults and easy overrides."""

    def _make(
        *,
        cooldown_seconds=0,
        my_node_id=None,
        require_from_my_node=True,
        keywords=None,
        heartbeat_keyword="CHECKIN",
        email_enabled=False,
        email_recipients=None,
        signal_contacts=None,
    ):
        return Config(
            meshtastic=MeshtasticConfig(host="test"),
            trigger=TriggerConfig(
                keywords=keywords or ["HELP", "URGENCE", "SOS", "MAYDAY"],
                my_node_id=my_node_id,
                require_from_my_node=require_from_my_node,
            ),
            dead_man=DeadManConfig(heartbeat_keyword=heartbeat_keyword),
            signal=SignalConfig(contacts=signal_contacts or []),
            email=EmailConfig(
                enabled=email_enabled,
                recipients=email_recipients or [],
            ),
            cooldown=CooldownConfig(seconds=cooldown_seconds),
        )

    return _make


@pytest.fixture
def wait_for():
    """Poll a predicate until it is truthy or the timeout (seconds) elapses."""

    def _wait_for(predicate, timeout=5.0, interval=0.005):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return bool(predicate())

    return _wait_for