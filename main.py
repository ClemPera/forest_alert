#!/usr/bin/env python3
"""
Forest Alert — Emergency notification service for solo outdoor trips.

Architecture:
  MeshtasticWatcher  — TCP connection to T-Beam, decodes messages
  DeadManSwitch      — fires alert if no heartbeat within configured interval
  alerting.fire_all  — sends Signal + email in parallel

Usage:
  python main.py                  # uses ./config.toml
  python main.py /path/to/config.toml
"""

import logging
import signal as _signal
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Event
from typing import Optional

import requests

from alerting import fire_all
from config import Config, load_config
from deadman import DeadManSwitch
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
        self._last_alert: float = 0.0
        self._shutdown = Event()

        self._watcher = MeshtasticWatcher(config)
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

        self._watcher.start()
        self._dead_man.start()

        logger.info("🌲 Forest Alert en cours d'exécution — Ctrl+C pour arrêter")
        self._shutdown.wait()

        logger.info("Arrêt en cours…")
        self._watcher.stop()
        self._dead_man.stop()
        logger.info("Arrêt complet")

    def _handle_signal(self, sig, frame):
        logger.info(f"Signal {sig} reçu, arrêt…")
        self._shutdown.set()

    # ─── Alert callbacks ──────────────────────────────────────────────────

    def _on_trigger(self, keyword: str, message: str, position: Optional[Position]):
        if not self._check_cooldown("SOS trigger"):
            return
        pos = position or self._watcher.get_last_position()
        logger.warning(f"🆘 ALERTE URGENCE — keyword='{keyword}' message='{message}'")
        fire_all(self._config, f"SOS Meshtastic [{keyword}]", message, pos)

    def _on_dead_man(self, reason: str):
        if not self._check_cooldown("dead man switch"):
            return
        pos = self._watcher.get_last_position()
        logger.warning(f"💀 DEAD MAN SWITCH — {reason}")
        fire_all(self._config, "Dead Man Switch", reason, pos)

    def _check_cooldown(self, label: str) -> bool:
        now = time.time()
        remaining = self._config.cooldown.seconds - (now - self._last_alert)
        if remaining > 0:
            logger.warning(
                f"Alerte '{label}' supprimée — cooldown actif ({remaining:.0f}s restantes)"
            )
            return False
        self._last_alert = now
        return True

    # ─── Preflight checks ─────────────────────────────────────────────────

    def _preflight(self) -> bool:
        ok = True
        cfg = self._config

        logger.info("── Vérifications pré-démarrage ──────────────────")

        # Signal contacts
        if not cfg.signal.contacts:
            logger.critical("❌ [signal] contacts est vide — les alertes Signal ne fonctionneront pas")
            ok = False

        # signal-cli daemon
        if not self._ping_signal_daemon():
            ok = False

        # Meshtastic node ID
        if cfg.trigger.require_from_my_node and not cfg.trigger.my_node_id:
            logger.warning(
                "⚠️  require_from_my_node=true mais my_node_id non défini "
                "→ réagira aux messages de TOUS les nœuds"
            )

        # Dead man config sanity
        if cfg.dead_man.enabled:
            overlap = any(
                kw.upper() == cfg.dead_man.heartbeat_keyword.upper()
                for kw in cfg.trigger.keywords
            )
            if overlap:
                logger.critical(
                    f"❌ Le keyword heartbeat '{cfg.dead_man.heartbeat_keyword}' "
                    f"est aussi un keyword d'urgence — conflit, corrigez la config"
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
                f"❌ signal-cli daemon injoignable à {self._config.signal.rpc_url}: {e}\n"
                f"   Démarrez-le avec: "
                f"signal-cli -a VOTRE_NUMERO daemon --http=127.0.0.1:8080"
            )
            return False


# ─── Entry point ────────────────────────────────────────────────────────────

def main():
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config.toml")

    setup_logging(config_path.parent / "forest_alert.log")

    if not config_path.exists():
        print(
            f"ERREUR: {config_path} introuvable.\n"
            f"Copiez config.toml.example → config.toml et remplissez-le."
        )
        sys.exit(1)

    try:
        config = load_config(config_path)
    except Exception as e:
        print(f"ERREUR config: {e}")
        sys.exit(1)

    ForestAlertApp(config).run()


if __name__ == "__main__":
    main()
