"""Asynchronous alert dispatcher.

Decouples alert sending from the Meshtastic receive thread so that slow
network I/O (Signal RPC, SMTP) never stalls packet processing.

Design:
  - submit() is non-blocking: it captures the best known position, checks
    cooldown / in-flight dedupe, enqueues the alert and returns immediately.
  - One background worker calls alerting.fire_all() for each queued alert.
  - Cooldown is tracked per alert type and consumed ONLY when at least one
    channel succeeded. A failed burst does not block the next attempt.
  - In-flight dedupe: only one alert of a given type is processed at a time;
    duplicates arriving while one is already being sent are dropped.
"""
import logging
import queue
import threading
import time
from typing import Callable, Optional

from alerting import fire_all
from config import Config
from gps import Position

logger = logging.getLogger(__name__)


class AlertDispatcher:
    def __init__(
        self,
        config: Config,
        get_position: Callable[[], Optional[Position]],
    ):
        self._config = config
        self._get_position = get_position
        self._queue: "queue.Queue[tuple[str, str, Optional[Position]]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._running = False

        self._lock = threading.Lock()
        # Per-type timestamp of the last SUCCESSFUL alert burst (epoch seconds).
        self._last_success: dict[str, float] = {}
        # Alert types currently being processed by the worker.
        self._inflight: set[str] = set()

    # ─── Lifecycle ────────────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._worker, daemon=True, name="alert-dispatcher"
        )
        self._thread.start()
        logger.info("AlertDispatcher started")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    # ─── Public API ───────────────────────────────────────────────────────

    def submit(
        self,
        trigger_type: str,
        message: str,
        position: Optional[Position] = None,
    ) -> bool:
        """Enqueue an alert for asynchronous sending.

        Returns True if accepted, False if suppressed by cooldown or because
        an alert of the same type is already being sent.
        """
        # Capture the freshest position now (cheap, no network I/O).
        if position is None:
            position = self._get_position()

        with self._lock:
            if trigger_type in self._inflight:
                logger.info(
                    f"Alert '{trigger_type}' dropped — already being sent"
                )
                return False

            last = self._last_success.get(trigger_type, 0.0)
            remaining = self._config.cooldown.seconds - (time.time() - last)
            if remaining > 0:
                logger.warning(
                    f"Alert '{trigger_type}' suppressed — cooldown active "
                    f"({remaining:.0f}s remaining)"
                )
                return False

            self._inflight.add(trigger_type)
            self._queue.put((trigger_type, message, position))
        return True

    # ─── Worker ───────────────────────────────────────────────────────────

    def _worker(self):
        while self._running:
            try:
                trigger_type, message, position = self._queue.get(timeout=1)
            except queue.Empty:
                continue

            try:
                self._send(trigger_type, message, position)
            except Exception as e:
                logger.error(
                    f"Unexpected error sending alert '{trigger_type}': {e}",
                    exc_info=True,
                )
            finally:
                with self._lock:
                    self._inflight.discard(trigger_type)

    def _send(self, trigger_type: str, message: str, position: Optional[Position]):
        results = fire_all(self._config, trigger_type, message, position)
        with self._lock:
            if any(results.values()):
                self._last_success[trigger_type] = time.time()
                logger.info(f"Alert '{trigger_type}' delivered — cooldown armed")
            else:
                logger.error(
                    f"Alert '{trigger_type}' failed on all channels — "
                    f"cooldown NOT armed"
                )