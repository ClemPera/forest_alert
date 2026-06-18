import logging
import threading
import time
from typing import Callable, Optional

import meshtastic.tcp_interface
from pubsub import pub

from config import Config
from gps import Position, extract_position_from_node, extract_position_from_packet

logger = logging.getLogger(__name__)

_TEXT_PORT = "TEXT_MESSAGE_APP"
_POS_PORT = "POSITION_APP"


class MeshtasticWatcher:
    """
    Maintains a TCP connection to the Meshtastic node.
    Reconnects automatically if the connection drops.
    Calls on_trigger(keyword, full_message, position) on emergency keyword.
    Calls on_heartbeat() on dead-man-switch heartbeat keyword.
    """

    def __init__(self, config: Config):
        self._config = config

        # Set these before calling start()
        self.on_trigger: Optional[Callable[[str, str, Optional[Position]], None]] = None
        self.on_heartbeat: Optional[Callable[[], None]] = None

        self._interface: Optional[meshtastic.tcp_interface.TCPInterface] = None
        self._running = False

        # Signals the connection_loop that the link dropped
        self._disconnected = threading.Event()

        self._position_lock = threading.Lock()
        self._last_position: Optional[Position] = None

    # ─── Public API ───────────────────────────────────────────────────────

    def start(self):
        self._running = True
        t = threading.Thread(
            target=self._connection_loop, daemon=True, name="mesh-watcher"
        )
        t.start()
        logger.info("MeshtasticWatcher started")

    def stop(self):
        self._running = False
        self._disconnected.set()
        self._close_interface()

    def get_last_position(self) -> Optional[Position]:
        with self._position_lock:
            return self._last_position

    # ─── Connection loop ──────────────────────────────────────────────────

    def _connection_loop(self):
        """Keeps reconnecting indefinitely until stop() is called."""
        while self._running:
            try:
                self._connect_and_block()
            except Exception as e:
                logger.error(f"Meshtastic connection error: {e}")
            finally:
                self._unsubscribe()
                self._close_interface()

            if self._running:
                delay = self._config.meshtastic.reconnect_delay
                logger.info(f"Reconnecting in {delay}s...")
                time.sleep(delay)

    def _connect_and_block(self):
        cfg = self._config.meshtastic
        logger.info(f"Connecting to Meshtastic node {cfg.host}:{cfg.port}...")

        self._disconnected.clear()
        self._subscribe()

        self._interface = meshtastic.tcp_interface.TCPInterface(
            hostname=cfg.host,
            portNumber=cfg.port,
        )

        # Block here — _on_lost() will set this event when the link drops
        self._disconnected.wait()
        logger.warning("Meshtastic link lost")

    # ─── PubSub subscriptions ─────────────────────────────────────────────

    def _subscribe(self):
        pub.subscribe(self._on_receive,    "meshtastic.receive")
        pub.subscribe(self._on_connected,  "meshtastic.connection.established")
        pub.subscribe(self._on_lost,       "meshtastic.connection.lost")

    def _unsubscribe(self):
        for topic, handler in [
            ("meshtastic.receive",                self._on_receive),
            ("meshtastic.connection.established", self._on_connected),
            ("meshtastic.connection.lost",        self._on_lost),
        ]:
            try:
                pub.unsubscribe(handler, topic)
            except Exception:
                pass

    def _close_interface(self):
        iface = self._interface
        self._interface = None
        if iface:
            try:
                iface.close()
            except Exception:
                pass

    # ─── PubSub callbacks (called from the Meshtastic thread) ─────────────

    def _on_connected(self, interface, topic=pub.AUTO_TOPIC):
        logger.info("✓ Connected to Meshtastic node")
        self._seed_position_from_db(interface)

    def _on_lost(self, interface, topic=pub.AUTO_TOPIC):
        self._disconnected.set()

    def _on_receive(self, packet: dict, interface):
        try:
            self._handle_packet(packet, interface)
        except Exception as e:
            logger.error(f"Error processing packet: {e}", exc_info=True)

    # ─── Packet handling ──────────────────────────────────────────────────

    def _handle_packet(self, packet: dict, interface):
        decoded = packet.get("decoded", {})
        portnum = decoded.get("portnum", "")

        # Always update our position cache from POSITION packets
        if portnum == _POS_PORT:
            pos = extract_position_from_packet(packet)
            if pos:
                with self._position_lock:
                    self._last_position = pos
                logger.debug(f"Position updated: {pos}")
            return

        if portnum != _TEXT_PORT:
            return  # ignore telemetry, nodeinfo, etc.

        text: str = decoded.get("text", "").strip()
        from_id: str = packet.get("fromId", "")
        to_id: str = packet.get("toId", "")

        logger.info(f"Message received — from={from_id} to={to_id} : '{text}'")

        # Filter by source node if configured
        cfg = self._config.trigger
        if cfg.require_from_my_node and cfg.my_node_id:
            if from_id.lower() != cfg.my_node_id.lower():
                logger.debug(f"Ignored (not my node): from={from_id}")
                return

        # Get best available position for this node
        position = self._best_position(from_id, packet, interface)

        # Dead man's switch heartbeat check (takes priority over trigger keywords)
        dm_cfg = self._config.dead_man
        if dm_cfg.enabled and dm_cfg.heartbeat_keyword.upper() in text.upper():
            logger.info(f"💓 Heartbeat detected: '{text}'")
            if self.on_heartbeat:
                self.on_heartbeat()
            return  # heartbeat never triggers an alert

        # Emergency keyword check
        for keyword in cfg.keywords:
            if keyword.upper() in text.upper():
                logger.warning(f"🆘 TRIGGER detected: '{keyword}' in '{text}'")
                if self.on_trigger:
                    self.on_trigger(keyword, text, position)
                return  # first match is enough

    # ─── Position helpers ─────────────────────────────────────────────────

    def _best_position(
        self,
        from_id: str,
        packet: dict,
        interface,
    ) -> Optional[Position]:
        """Try position sources in order of freshness."""
        # 1. Position embedded in the packet itself
        pos = extract_position_from_packet(packet)
        if pos:
            return pos

        # 2. Node DB entry for the sender
        if interface and interface.nodes:
            for node_info in interface.nodes.values():
                node_id = node_info.get("user", {}).get("id", "")
                if node_id.lower() == from_id.lower():
                    pos = extract_position_from_node(node_info)
                    if pos:
                        with self._position_lock:
                            self._last_position = pos
                        return pos

        # 3. Cached last known position
        with self._position_lock:
            return self._last_position

    def _seed_position_from_db(self, interface):
        """On connection, populate last_position from node DB (especially for the source node)."""
        if not interface or not interface.nodes:
            return

        my_id = self._config.trigger.my_node_id
        for node_info in interface.nodes.values():
            node_id = node_info.get("user", {}).get("id", "")
            # If my_id is configured, match it specifically; else take any position we find
            if my_id and node_id.lower() != my_id.lower():
                continue
            pos = extract_position_from_node(node_info)
            if pos:
                with self._position_lock:
                    self._last_position = pos
                logger.info(f"Initial position from node DB {node_id}: {pos}")
                if my_id:
                    break  # found the right node, stop