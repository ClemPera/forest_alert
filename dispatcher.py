"""Asynchronous alert dispatcher with retry.

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
  - If every channel fails, the alert is retried with exponential backoff
    (capped) until at least one channel succeeds or max_attempts is reached
    (None = retry indefinitely). Retries run on Timer threads so they never
    block the worker or other alert types.
  - cancel(type) stops pending retries (used when a heartbeat arrives and
    renders a dead-man alert obsolete).
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

# Exponential backoff for retries: delay = min(base * 2**attempt, cap).
_RETRY_BASE_SECONDS = 30
_RETRY_CAP_SECONDS = 600  # 10 min
# Retry indefinitely by default — for a life-safety alert, giving up silently
# is worse than persistent retrying. Pass a finite number in tests.
_DEFAULT_MAX_ATTEMPTS: Optional[int] = None


class AlertDispatcher:
    def __init__(
        self,
        config: Config,
        get_position: Callable[[], Optional[Position]],
        max_attempts: Optional[int] = _DEFAULT_MAX_ATTEMPTS,
    ):
        self._config = config
        self._get_position = get_position
        self._max_attempts = max_attempts
        self._queue: "queue.Queue[tuple[str, str, Optional[Position], int]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._running = False

        self._lock = threading.Lock()
        # Per-type timestamp of the last SUCCESSFUL alert burst (epoch seconds).
        self._last_success: dict[str, float] = {}
        # Alert types with an attempt currently in progress on the worker.
        self._inflight: set[str] = set()
        # Alert types with a retry timer pending.
        self._retrying: set[str] = set()
        self._retry_timers: dict[str, threading.Timer] = {}

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
        with self._lock:
            for timer in self._retry_timers.values():
                timer.cancel()
            self._retry_timers.clear()
            self._retrying.clear()
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
        an alert of the same type is already being sent or retried.
        """
        # Capture the freshest position now (cheap, no network I/O).
        if position is None:
            position = self._get_position()

        with self._lock:
            if trigger_type in self._inflight or trigger_type in self._retrying:
                logger.info(
                    f"Alert '{trigger_type}' dropped — already being sent/retried"
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
            self._queue.put((trigger_type, message, position, 0))
        return True

    def cancel(self, trigger_type: str) -> None:
        """Cancel any pending retry for the given alert type."""
        with self._lock:
            timer = self._retry_timers.pop(trigger_type, None)
            self._retrying.discard(trigger_type)
        if timer:
            timer.cancel()
            logger.info(f"Cancelled pending retries for '{trigger_type}'")

    # ─── Worker ───────────────────────────────────────────────────────────

    def _worker(self):
        while self._running:
            try:
                trigger_type, message, position, attempt = self._queue.get(timeout=1)
            except queue.Empty:
                continue
            self._attempt(trigger_type, message, position, attempt)

    def _attempt(self, trigger_type: str, message: str, position: Optional[Position], attempt: int):
        try:
            results = fire_all(self._config, trigger_type, message, position)
            delivered = bool(results) and any(results.values())
        except Exception as e:
            logger.error(
                f"Alert '{trigger_type}' attempt {attempt} raised: {e}",
                exc_info=True,
            )
            delivered = False

        with self._lock:
            self._inflight.discard(trigger_type)
            if delivered:
                self._last_success[trigger_type] = time.time()

        if delivered:
            logger.info(f"Alert '{trigger_type}' delivered — cooldown armed")
            return

        # All channels failed — schedule a retry.
        self._schedule_retry(trigger_type, message, attempt)

    def _schedule_retry(self, trigger_type: str, message: str, attempt: int) -> None:
        next_attempt = attempt + 1
        if self._max_attempts is not None and next_attempt >= self._max_attempts:
            logger.critical(
                f"💀 Alert '{trigger_type}' permanently failed after "
                f"{self._max_attempts} attempts — giving up"
            )
            return

        delay = min(_RETRY_BASE_SECONDS * (2 ** attempt), _RETRY_CAP_SECONDS)
        logger.warning(
            f"Alert '{trigger_type}' failed on all channels — "
            f"retry {next_attempt}"
            + (f"/{self._max_attempts}" if self._max_attempts else "")
            + f" in {delay}s"
        )

        timer = threading.Timer(
            delay, self._do_retry, args=(trigger_type, message, next_attempt)
        )
        timer.daemon = True
        with self._lock:
            if not self._running:
                timer.cancel()
                return
            self._retrying.add(trigger_type)
            self._retry_timers[trigger_type] = timer
        timer.start()

    def _do_retry(self, trigger_type: str, message: str, attempt: int) -> None:
        with self._lock:
            if not self._running:
                self._retrying.discard(trigger_type)
                self._retry_timers.pop(trigger_type, None)
                return
            self._retrying.discard(trigger_type)
            self._retry_timers.pop(trigger_type, None)
            self._inflight.add(trigger_type)

        # Refresh the position for this retry (it may be newer than at submit time).
        position = self._get_position()
        self._queue.put((trigger_type, message, position, attempt))