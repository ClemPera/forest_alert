#!/usr/bin/env python3
"""
Forest Alert — Emergency notification service for solo outdoor trips.

Architecture:
  MeshtasticWatcher   — TCP connection to the Meshtastic node, decodes messages
  DeadManSwitch       — fires alert if no heartbeat within configured interval
  AlertDispatcher     — sends Signal + email off the receive thread
  alerting.fire_all   — fans an alert out to all channels in parallel

Usage:
  python main.py                  # uses ./config.toml
  python main.py /path/to/config.toml
"""

import logging
import signal as _signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Event
from typing import Optional

import requests

from config import Config, load_config
from deadman import DeadManSwitch
from dispatcher import AlertDispatcher
from gps import Position
from meshtastic_watcher import MeshtasticWatcher


# ─── Logging ────────────────────────────────────────────────────────────────

def setup_logging(log_file: Path):
    fmt = logging.Formatter("%(asctime)s [%(levelname)-8s] %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    fh = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5)
    fh.setFormatter(fmt)
    root.addHandler(fh)


logger = logging.getLogger("forest_alert")


# ─── App ────────────────────────────────────────────────────────────────────

class ForestAlertApp:
    def __init__(self, config: Config):
        self._config = config
        self._shutdown = Event()

        self._watcher = MeshtasticWatcher(config)
        self._dispatcher = AlertDispatcher(config, self._watcher.get_last_position)
        self._dead_man = DeadManSwitch(config.dead_man, self._on_dead_man)

        # Wire callbacks
        self._watcher.on_trigger   = self._on_trigger
        self._watcher.on_heartbeat = self._dead_man.record_heartbeat

    # ─── Lifecycle ────────────────────────────────────────────────────────

    def run(self):
        if not self._preflight():
            sys.exit(1)

        for sig in (_signal.SIGINT, _signal.SIGTERM):
            _signal.signal(sig, self._handle_signal)

        self._dispatcher.start()
        self._watcher.start()
        self._dead_man.start()

        logger.info("🌲 Forest Alert running — Ctrl+C to stop")
        self._shutdown.wait()

        logger.info("Shutting down...")
        self._watcher.stop()
        self._dead_man.stop()
        self._dispatcher.stop()
        logger.info("Shutdown complete")

    def _handle_signal(self, sig, frame):
        logger.info(f"Signal {sig} received, shutting down...")
        self._shutdown.set()

    # ─── Alert callbacks (non-blocking — just enqueue) ─────────────────────

    def _on_trigger(self, keyword: str, message: str, position: Optional[Position]):
        logger.warning(f"🆘 EMERGENCY ALERT — keyword='{keyword}' message='{message}'")
        self._dispatcher.submit(f"SOS Meshtastic [{keyword}]", message, position)

    def _on_dead_man(self, reason: str):
        logger.warning(f"💀 DEAD MAN SWITCH — {reason}")
        self._dispatcher.submit("Dead Man Switch", reason)

    # ─── Preflight checks ─────────────────────────────────────────────────

    def _preflight(self) -> bool:
        ok = True
        cfg = self._config

        logger.info("── Pre-flight checks ──────────────────")

        # Signal contacts
        if not cfg.signal.contacts:
            logger.critical("❌ [signal] contacts is empty — Signal alerts will not work")
            ok = False

        # signal-cli daemon
        if not self._ping_signal_daemon():
            ok = False

        # Meshtastic node ID
        if cfg.trigger.require_from_my_node and not cfg.trigger.my_node_id:
            logger.warning(
                "⚠️  require_from_my_node=true but my_node_id is not set "
                "→ will react to messages from ALL nodes"
            )

        # Dead man config sanity
        if cfg.dead_man.enabled:
            overlap = any(
                kw.upper() == cfg.dead_man.heartbeat_keyword.upper()
                for kw in cfg.trigger.keywords
            )
            if overlap:
                logger.critical(
                    f"❌ Heartbeat keyword '{cfg.dead_man.heartbeat_keyword}' "
                    f"is also an emergency keyword — conflict, fix config"
                )
                ok = False

        logger.info("─────────────────────────────────────────────────")
        return ok

    def _ping_signal_daemon(self) -> bool:
        try:
            r = requests.post(
                self._config.signal.rpc_url,
                json={"jsonrpc": "2.0", "method": "version", "id": "preflight"},
                timeout=5,
            )
            r.raise_for_status()
            data = r.json()
            version = data.get("result", {}).get("version", "?")
            logger.info(f"✅ signal-cli daemon OK (version {version})")
            return True
        except Exception as e:
            logger.critical(
                f"❌ signal-cli daemon unreachable at {self._config.signal.rpc_url}: {e}\n"
                f"   Start it with: "
                f"signal-cli -a YOUR_NUMBER daemon --http=127.0.0.1:8080"
            )
            return False


# ─── Entry point ────────────────────────────────────────────────────────────

def main():
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config.toml")

    setup_logging(config_path.parent / "forest_alert.log")

    if not config_path.exists():
        print(
            f"ERROR: {config_path} not found.\n"
            f"Copy config.toml.example → config.toml and fill it in."
        )
        sys.exit(1)

    try:
        config = load_config(config_path)
    except Exception as e:
        print(f"Config error: {e}")
        sys.exit(1)

    ForestAlertApp(config).run()


if __name__ == "__main__":
    main()