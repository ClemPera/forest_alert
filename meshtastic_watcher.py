import logging
import threading
import time
from typing import Callable, Optional

import meshtastic.tcp_interface
from pubsub import pub

from config import Config
from gps import Position, extract_position_from_node, extract_position_from_packet
from matching import contains_keyword, first_matching_keyword

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

        # Cooldown for on-demand position requests (the node rate-limits).
        self._last_refresh_time: float = 0.0
        self._refresh_cooldown: float = 30.0  # seconds

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
                logger.debug(
                    "Position packet received: %s | quality: %s",
                    pos, pos.quality_description(),
                )
            else:
                logger.debug(
                    "Position packet without usable coordinates: %s",
                    decoded.get("position", {}),
                )
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

        # Dead man's switch heartbeat check (takes priority over trigger keywords)
        dm_cfg = self._config.dead_man
        if dm_cfg.enabled and contains_keyword(text, dm_cfg.heartbeat_keyword):
            logger.info(f"💓 Heartbeat detected: '{text}'")
            if self.on_heartbeat:
                self.on_heartbeat()
            return  # heartbeat never triggers an alert

        # Emergency keyword check (whole-word match, first match wins)
        keyword = first_matching_keyword(text, cfg.keywords)
        if keyword:
            logger.warning(f"🆘 TRIGGER detected: '{keyword}' in '{text}'")
            if self.on_trigger:
                self._dispatch_trigger(keyword, text, from_id, packet, interface)
            return  # first match is enough

    # ─── Trigger dispatch ─────────────────────────────────────────────────

    def _dispatch_trigger(
        self,
        keyword: str,
        text: str,
        from_id: str,
        packet: dict,
        interface,
    ):
        """Fire on_trigger with the freshest position available.

        If the interface supports sendPosition (real Meshtastic connection),
        we request a fresh position response from the local node to get
        quality metadata (locationSource, precisionBits, etc.) that the
        node DB entry lacks.  This runs in a separate thread so the
        Meshtastic reader thread stays free to receive the response.
        """
        my_id = self._config.trigger.my_node_id
        can_refresh = bool(
            my_id
            and interface
            and hasattr(interface, "sendPosition")
        )

        if not can_refresh:
            # No on-demand refresh possible (test interface or no my_node_id).
            position = self._best_position(from_id, packet, interface)
            self.on_trigger(keyword, text, position)
            return

        t = threading.Thread(
            target=self._refresh_and_trigger,
            args=(keyword, text, from_id, packet, interface, my_id),
            daemon=True,
            name="pos-refresh",
        )
        t.start()

    def _refresh_and_trigger(
        self,
        keyword: str,
        text: str,
        from_id: str,
        packet: dict,
        interface,
        my_id: str,
    ):
        """Refresh position from the local node, then fire on_trigger.

        Runs in a background thread so the reader thread can process the
        position response that sendPosition(wantResponse=True) triggers.
        """
        position = self._refresh_position(interface, my_id)
        if position is None:
            position = self._best_position(from_id, packet, interface)
        self.on_trigger(keyword, text, position)

    def _refresh_position(self, interface, my_id: str) -> Optional[Position]:
        """Request a fresh position from the local node and return it.

        The node DB entry for the local node is set directly by firmware
        and lacks quality fields (locationSource, precisionBits, ...).
        Calling sendPosition(wantResponse=True) makes the node respond
        with a full Position protobuf that carries those fields.

        Rate-limited: the node stops responding if we request too often,
        so we enforce a cooldown between requests.
        """
        now = time.time()
        elapsed = now - self._last_refresh_time
        if elapsed < self._refresh_cooldown:
            logger.debug(
                "Position refresh skipped (cooldown: %.0fs remaining)",
                self._refresh_cooldown - elapsed,
            )
            return None

        self._last_refresh_time = now
        try:
            logger.info("Requesting fresh position from local node...")
            interface.sendPosition(wantResponse=True, destinationId=my_id)

            # The response has been processed by _onPositionReceive which
            # updated the node DB.  Extract the refreshed position.
            for node_info in interface.nodes.values():
                node_id = node_info.get("user", {}).get("id", "")
                if node_id.lower() == my_id.lower():
                    pos = extract_position_from_node(node_info)
                    if pos:
                        with self._position_lock:
                            self._last_position = pos
                        logger.info(
                            "Fresh position: %s | quality: %s",
                            pos, pos.quality_description(),
                        )
                        return pos
            logger.warning("Position refresh succeeded but no node DB entry found")
        except Exception as e:
            logger.warning(f"Position refresh failed: {e}")
        return None

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
                logger.info(
                    "Initial position from node DB %s: %s | quality: %s",
                    node_id, pos, pos.quality_description(),
                )
                logger.debug(
                    "Raw position dict for %s: %s",
                    node_id, node_info.get("position", {}),
                )
                if my_id:
                    break  # found the right node, stop