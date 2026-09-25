import asyncio
import logging
import threading
import time
from typing import Callable, Optional

from meshcore import EventType, MeshCore

from config import Config
from gps import Position, extract_position_from_contact
from matching import contains_keyword, first_matching_keyword

logger = logging.getLogger(__name__)

# A connection that survives this long is considered healthy and resets the
# reconnect backoff. Anything shorter is a "flap" and doubles the delay.
_FLAP_WINDOW = 60
_MAX_RECONNECT_DELAY = 300


class MeshCoreWatcher:
    """
    Maintains a TCP connection to the MeshCore companion node.
    Reconnects automatically if the connection drops.
    Calls on_trigger(keyword, full_message, position) on emergency keyword.
    Calls on_heartbeat() on dead-man-switch heartbeat keyword.

    Only listens to direct messages from the paired contact configured as
    meshcore.contact_public_key — MeshCore channel broadcasts carry no
    sender identity, so they can't support require_from_my_node filtering.

    The meshcore library is asyncio-native; this class runs its own event
    loop on a background thread so callers get the same synchronous
    start()/stop() interface as MeshtasticWatcher.
    """

    def __init__(self, config: Config):
        self._config = config

        # Set these before calling start()
        self.on_trigger: Optional[Callable[[str, str, Optional[Position]], None]] = None
        self.on_heartbeat: Optional[Callable[[], None]] = None

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._mc: Optional[MeshCore] = None
        self._running = False

        self._position_lock = threading.Lock()
        self._last_position: Optional[Position] = None

        # Reconnect backoff state (see _next_reconnect_delay)
        self._connected_at: Optional[float] = None
        self._consecutive_flaps = 0

    # ─── Public API ───────────────────────────────────────────────────────

    def start(self):
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="mesh-watcher"
        )
        self._thread.start()
        logger.info("MeshCoreWatcher started")

    def stop(self):
        self._running = False
        loop = self._loop
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(self._disconnect(), loop)
        if self._thread:
            self._thread.join(timeout=5)

    def get_last_position(self) -> Optional[Position]:
        with self._position_lock:
            return self._last_position

    # ─── Event loop thread ─────────────────────────────────────────────────

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connection_loop())
        finally:
            self._loop.close()

    async def _disconnect(self):
        if self._mc:
            try:
                await self._mc.disconnect()
            except Exception:
                pass

    # ─── Connection loop ──────────────────────────────────────────────────

    async def _connection_loop(self):
        """Keeps reconnecting indefinitely until stop() is called."""
        while self._running:
            try:
                await self._connect_and_wait()
            except Exception as e:
                logger.error(f"MeshCore connection error: {e}")

            if self._running:
                delay = self._next_reconnect_delay()
                logger.info(f"Reconnecting in {delay}s...")
                await asyncio.sleep(delay)

    def _next_reconnect_delay(self) -> float:
        """Base delay after a healthy connection, doubling (capped) on flaps."""
        base = self._config.meshcore.reconnect_delay
        was_healthy = (
            self._connected_at is not None
            and time.time() - self._connected_at >= _FLAP_WINDOW
        )
        self._connected_at = None

        if was_healthy:
            self._consecutive_flaps = 0
            return base

        self._consecutive_flaps += 1
        delay = base * (2 ** (self._consecutive_flaps - 1))
        return min(delay, _MAX_RECONNECT_DELAY)

    async def _connect_and_wait(self):
        cfg = self._config.meshcore
        logger.info(f"Connecting to MeshCore node {cfg.host}:{cfg.port}...")

        disconnected = asyncio.Event()

        mc = await MeshCore.create_tcp(cfg.host, cfg.port)
        if mc is None:
            raise ConnectionError("No response from MeshCore node")
        self._mc = mc

        mc.subscribe(EventType.CONTACT_MSG_RECV, self._on_message)
        mc.subscribe(EventType.DISCONNECTED, lambda e: disconnected.set())

        self._connected_at = time.time()
        logger.info("✓ Connected to MeshCore node")
        await self._seed_position_from_contacts()

        # Block here — the DISCONNECTED handler above sets this on link drop
        await disconnected.wait()
        logger.warning("MeshCore link lost")

    async def _seed_position_from_contacts(self):
        """On connection, populate last_position from the contact DB."""
        try:
            await self._mc.commands.get_contacts()
        except Exception as e:
            logger.warning(f"Could not fetch MeshCore contacts: {e}")
            return

        key = self._config.meshcore.contact_public_key
        if not key:
            return

        contact = self._mc.get_contact_by_key_prefix(key)
        pos = extract_position_from_contact(contact)
        if pos:
            with self._position_lock:
                self._last_position = pos
            logger.info(f"Initial position from contact DB: {pos}")

    # ─── Message handling (called from the MeshCore event loop) ───────────

    async def _on_message(self, event):
        try:
            self._handle_message(event.payload)
        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)

    def _handle_message(self, payload: dict):
        text: str = payload.get("text", "").strip()
        pubkey_prefix: str = payload.get("pubkey_prefix", "")

        logger.info(f"Message received — from={pubkey_prefix} : '{text}'")

        # Filter by source contact if configured
        cfg = self._config.trigger
        mc_cfg = self._config.meshcore
        if cfg.require_from_my_node and mc_cfg.contact_public_key:
            if not mc_cfg.contact_public_key.lower().startswith(pubkey_prefix.lower()):
                logger.debug(f"Ignored (not my contact): from={pubkey_prefix}")
                return

        position = self._best_position(pubkey_prefix)

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
                self.on_trigger(keyword, text, position)
            return  # first match is enough

    # ─── Position helpers ─────────────────────────────────────────────────

    def _best_position(self, pubkey_prefix: str) -> Optional[Position]:
        """Try the sender's advertised position first, else the cache."""
        if self._mc:
            contact = self._mc.get_contact_by_key_prefix(pubkey_prefix)
            pos = extract_position_from_contact(contact)
            if pos:
                with self._position_lock:
                    self._last_position = pos
                return pos

        with self._position_lock:
            return self._last_position
