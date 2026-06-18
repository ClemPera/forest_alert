import logging
import smtplib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.mime.text import MIMEText
from typing import Optional

import requests

from config import Config
from gps import Position

logger = logging.getLogger(__name__)


# ─── Message builder ────────────────────────────────────────────────────────

def build_message(trigger_type: str, trigger_msg: str, position: Optional[Position]) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    lines = [
        "🆘 FOREST ALERT 🆘",
        f"Type    : {trigger_type}",
        f"Message : {trigger_msg}",
        f"Heure   : {ts}",
        "",
    ]

    if position:
        lines += [
            f"Position: {position}",
            f"GMaps   : {position.maps_url()}",
            f"OSM     : {position.osm_url()}",
        ]
        age = position.age_seconds()
        if age > 3600:
            lines.append(f"⚠️  Position ancienne ({age / 3600:.1f}h) — peut ne pas être exacte")
    else:
        lines += [
            "⚠️  Aucune position GPS disponible",
            "    Vérifiez le dernier check-in connu",
        ]

    lines += [
        "",
        "Contactez votre personne de confiance.",
    ]
    return "\n".join(lines)


# ─── Signal ─────────────────────────────────────────────────────────────────

def send_signal(config: Config, body: str) -> bool:
    if not config.signal.contacts:
        logger.warning("Signal: aucun contact configuré, skip")
        return False

    payload = {
        "jsonrpc": "2.0",
        "method": "send",
        "id": f"alert-{int(time.time())}",
        "params": {
            "message": body,
            "recipient": config.signal.contacts,
        },
    }

    try:
        r = requests.post(config.signal.rpc_url, json=payload, timeout=15)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            logger.error(f"Signal RPC error: {data['error']}")
            return False
        logger.info("✅ Signal envoyé")
        return True
    except Exception as e:
        logger.error(f"Signal échec: {e}")
        return False


# ─── Email ──────────────────────────────────────────────────────────────────

def send_email(config: Config, body: str, subject: str) -> bool:
    if not config.email.enabled or not config.email.recipients:
        return False

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = config.email.sender
    msg["To"] = ", ".join(config.email.recipients)

    try:
        with smtplib.SMTP(config.email.smtp_host, config.email.smtp_port, timeout=15) as s:
            s.ehlo()
            s.starttls()
            s.login(config.email.sender, config.email.password)
            s.sendmail(config.email.sender, config.email.recipients, msg.as_string())
        logger.info("✅ Email envoyé")
        return True
    except Exception as e:
        logger.error(f"Email échec: {e}")
        return False


# ─── Fire all ───────────────────────────────────────────────────────────────

def fire_all(
    config: Config,
    trigger_type: str,
    trigger_msg: str,
    position: Optional[Position],
) -> dict[str, bool]:
    """Send alerts on all channels simultaneously. Returns per-channel results."""
    body = build_message(trigger_type, trigger_msg, position)
    title = f"🆘 FOREST ALERT: {trigger_type}"

    tasks: dict[str, object] = {
        "signal": lambda: send_signal(config, body),
        "email":  lambda: send_email(config, body, title),
    }

    results: dict[str, bool] = {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {pool.submit(fn): name for name, fn in tasks.items()}  # type: ignore[arg-type]
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as e:
                logger.error(f"Channel '{name}' a levé une exception: {e}")
                results[name] = False

    ok = [k for k, v in results.items() if v]
    fail = [k for k, v in results.items() if not v]

    if not ok:
        logger.critical("💀 TOUS LES CANAUX D'ALERTE ONT ÉCHOUÉ — vérifiez les logs immédiatement")
    else:
        logger.info(
            f"Alertes envoyées via: {ok}"
            + (f" | Échecs: {fail}" if fail else "")
        )

    return results
