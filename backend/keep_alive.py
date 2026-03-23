"""
keep_alive.py — Background thread that pings /health every 4 minutes.

Railway free-tier instances spin down after ~5 minutes of inactivity.
This self-ping keeps the process warm so the first real user request
does not hit a cold start.

Usage: called automatically from main.py's startup_event().
"""

from __future__ import annotations

import logging
import os
import threading
import time

import requests

logger = logging.getLogger("rxbuddy.keep_alive")

_INTERVAL = 240  # seconds (4 minutes — safely under Railway's 5-min idle timeout)
_thread: threading.Thread | None = None


def _ping_loop(base_url: str) -> None:
    url = f"{base_url}/health"
    while True:
        time.sleep(_INTERVAL)
        try:
            resp = requests.get(url, timeout=10)
            logger.debug("[KeepAlive] Ping %s → %s", url, resp.status_code)
        except Exception as exc:
            logger.warning("[KeepAlive] Ping failed: %s", exc)


def start() -> None:
    """Start the keep-alive background thread (idempotent — only starts once)."""
    global _thread
    if _thread and _thread.is_alive():
        return

    # Determine the public URL of this service.
    # Railway injects RAILWAY_PUBLIC_DOMAIN; fall back to localhost for local dev.
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN")
    if domain:
        base_url = f"https://{domain}"
    else:
        port = os.getenv("PORT", "8000")
        base_url = f"http://127.0.0.1:{port}"

    _thread = threading.Thread(
        target=_ping_loop,
        args=(base_url,),
        daemon=True,
        name="keep-alive",
    )
    _thread.start()
    logger.info("[KeepAlive] Started — pinging %s/health every %ds", base_url, _INTERVAL)
