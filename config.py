import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class MeshtasticConfig:
    host: str
    port: int = 4403
    reconnect_delay: int = 10  # seconds between reconnection attempts


@dataclass
class MeshCoreConfig:
    host: str
    port: int = 4000
    reconnect_delay: int = 10  # seconds between reconnection attempts
    # Public key (hex) of the paired contact allowed to trigger alerts.
    # Required when [trigger].require_from_my_node is true.
    contact_public_key: Optional[str] = None


@dataclass
class TransportConfig:
    type: str = "meshtastic"  # "meshtastic" or "meshcore" — which watcher to run


@dataclass
class TriggerConfig:
    # Keywords that trigger an emergency alert (case-insensitive)
    keywords: list[str] = field(default_factory=lambda: ["HELP", "URGENCE", "SOS", "MAYDAY"])
    # Your M1 node ID (e.g. "!abcdef01") — get it from the Meshtastic app
    my_node_id: Optional[str] = None
    # If True, only react to messages FROM your M1 node
    require_from_my_node: bool = True


@dataclass
class DeadManConfig:
    enabled: bool = True
    # Keyword you send regularly to reset the timer (case-insensitive, whole-word match).
    # Pick something distinctive that won't appear in casual messages.
    heartbeat_keyword: str = "CHECKIN"
    # How often you are expected to send a heartbeat
    heartbeat_interval_hours: float = 4.0
    # Extra buffer before alerting after missed heartbeat
    grace_period_hours: float = 1.0
    # How often the dead man check runs
    check_interval_minutes: float = 15.0


@dataclass
class SignalConfig:
    # URL of the signal-cli JSON-RPC daemon
    # Start it with: signal-cli -a ACCOUNT daemon --http=127.0.0.1:8080
    rpc_url: str = "http://127.0.0.1:8080/api/v1/rpc"
    # Phone numbers to alert (include country code, e.g. "+33612345678")
    contacts: list[str] = field(default_factory=list)


@dataclass
class EmailConfig:
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    sender: str = ""
    password: str = ""
    recipients: list[str] = field(default_factory=list)


@dataclass
class CooldownConfig:
    # Minimum seconds between two alert bursts (avoids spam on repeated messages)
    seconds: int = 300


@dataclass
class Config:
    trigger: TriggerConfig
    dead_man: DeadManConfig
    signal: SignalConfig
    email: EmailConfig
    cooldown: CooldownConfig
    transport: TransportConfig = field(default_factory=TransportConfig)
    # Exactly one of these is set, matching transport.type
    meshtastic: Optional[MeshtasticConfig] = None
    meshcore: Optional[MeshCoreConfig] = None


def load_config(path: Path = Path("config.toml")) -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    transport = TransportConfig(**raw.get("transport", {}))

    meshtastic_cfg = None
    meshcore_cfg = None

    if transport.type == "meshtastic":
        mesh_raw = raw.get("meshtastic", {})
        if "host" not in mesh_raw:
            raise ValueError("[meshtastic] host is required in config.toml")
        meshtastic_cfg = MeshtasticConfig(**mesh_raw)
    elif transport.type == "meshcore":
        mc_raw = raw.get("meshcore", {})
        if "host" not in mc_raw:
            raise ValueError("[meshcore] host is required in config.toml")
        meshcore_cfg = MeshCoreConfig(**mc_raw)
    else:
        raise ValueError(
            f"Unknown [transport] type '{transport.type}' — expected 'meshtastic' or 'meshcore'"
        )

    return Config(
        transport=transport,
        meshtastic=meshtastic_cfg,
        meshcore=meshcore_cfg,
        trigger=TriggerConfig(**raw.get("trigger", {})),
        dead_man=DeadManConfig(**raw.get("dead_man", {})),
        signal=SignalConfig(**raw.get("signal", {})),
        email=EmailConfig(**raw.get("email", {})),
        cooldown=CooldownConfig(**raw.get("cooldown", {})),
    )
