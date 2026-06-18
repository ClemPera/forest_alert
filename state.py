"""Persistence of small runtime state to disk.

Used by the dead-man switch to remember the last heartbeat timestamp across
service restarts and host reboots, so a crash does not silently reset the
"no sign of life" timer.
"""
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def load_last_heartbeat(path: Optional[Path]) -> Optional[float]:
    """Return the persisted last-heartbeat timestamp, or None if unavailable."""
    if path is None:
        return None
    try:
        text = path.read_text().strip()
    except FileNotFoundError:
        return None
    except OSError as e:
        logger.warning(f"Could not read state file {path}: {e}")
        return None
    try:
        return float(text)
    except ValueError:
        logger.warning(f"Corrupt state file {path}, ignoring")
        return None


def save_last_heartbeat(path: Optional[Path], timestamp: float) -> None:
    """Persist the last-heartbeat timestamp atomically (write tmp + rename)."""
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(str(timestamp))
        tmp.replace(path)
    except OSError as e:
        logger.warning(f"Could not write state file {path}: {e}")