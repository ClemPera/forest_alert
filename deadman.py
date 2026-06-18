import logging
import threading
import time
from typing import Callable, Optional

from config import DeadManConfig

logger = logging.getLogger(__name__)


class DeadManSwitch:
    """
    Arms itself on start(). Expects the user to regularly send a heartbeat
    message via Meshtastic. If no heartbeat is received within
    (heartbeat_interval_hours + grace_period_hours), fires the alert callback.

    The alert fires only once per incident — it resets when a heartbeat arrives.
    """

    def __init__(self, config: DeadManConfig, on_alert: Callable[[str], None]):
        self._config = config
        self._on_alert = on_alert

        self._last_heartbeat: Optional[float] = None
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None
        self._running = False
        self._already_fired = False  # avoid repeated alerts for the same incident

    def start(self):
        if not self._config.enabled:
            logger.info("Dead man's switch disabled (config)")
            return
        self._running = True
        with self._lock:
            self._last_heartbeat = time.time()  # arm from now
        self._schedule_next_check()
        logger.info(
            f"🔒 Dead man's switch armed — "
            f"keyword='{self._config.heartbeat_keyword}' "
            f"interval={self._config.heartbeat_interval_hours}h "
            f"grace={self._config.grace_period_hours}h"
        )

    def stop(self):
        self._running = False
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def record_heartbeat(self):
        """Call this when the user's heartbeat message is received."""
        with self._lock:
            self._last_heartbeat = time.time()
            self._already_fired = False
        logger.info("💓 Heartbeat received — dead man's switch reset")

    # ─── Internal ─────────────────────────────────────────────────────────

    def _schedule_next_check(self):
        if not self._running:
            return
        interval = self._config.check_interval_minutes * 60
        self._timer = threading.Timer(interval, self._check)
        self._timer.daemon = True
        self._timer.start()

    def _check(self):
        if not self._running:
            return

        with self._lock:
            last = self._last_heartbeat
            already_fired = self._already_fired

        if last is None:
            self._schedule_next_check()
            return

        elapsed = time.time() - last
        max_secs = (
            self._config.heartbeat_interval_hours + self._config.grace_period_hours
        ) * 3600

        if elapsed > max_secs:
            if not already_fired:
                with self._lock:
                    self._already_fired = True
                hours = elapsed / 3600
                reason = (
                    f"No heartbeat since {hours:.1f}h "
                    f"(limit: {self._config.heartbeat_interval_hours + self._config.grace_period_hours}h). "
                    f"Expected keyword: '{self._config.heartbeat_keyword}'"
                )
                logger.warning(f"💀 Dead man's switch triggered: {reason}")
                self._on_alert(reason)
            else:
                logger.debug("Dead man's switch already triggered, no duplicate alert")
        else:
            # Back in safe window (shouldn't happen without heartbeat, but reset just in case)
            with self._lock:
                self._already_fired = False
            remaining = (max_secs - elapsed) / 3600
            logger.debug(f"Dead man's switch OK — {remaining:.1f}h remaining")

        self._schedule_next_check()