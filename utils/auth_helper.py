"""
NeoGap — Kotak Neo authentication helper.

Handles the two-step login flow:
  1. neo_api_client.NeoAPI(consumer_key, consumer_secret, environment)
  2. client.login(mobilenumber, password)
  3. client.session_2fa(OTP)

Tokens are persisted to .neo_token.json for reuse across runs (valid ~24h).
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config.settings import settings
from utils.logger import get_logger

logger = get_logger("auth_helper", settings.ops.log_level, settings.ops.log_file)

_TOKEN_FILE = Path(".neo_token.json")
_TOKEN_EXPIRY_HOURS = 20  # Conservative: Neo tokens valid ~24h


def _load_cached_token() -> Optional[str]:
    """Return cached access token if still valid, else None."""
    if not _TOKEN_FILE.exists():
        return None
    try:
        data = json.loads(_TOKEN_FILE.read_text())
        saved_at = datetime.fromisoformat(data["saved_at"])
        if datetime.now() - saved_at < timedelta(hours=_TOKEN_EXPIRY_HOURS):
            logger.info("Using cached Neo access token (saved %s)", saved_at.strftime("%H:%M"))
            return data["access_token"]
        logger.info("Cached token expired — re-authenticating")
    except Exception as exc:
        logger.warning("Failed to read cached token: %s", exc)
    return None


def _save_token(access_token: str) -> None:
    try:
        _TOKEN_FILE.write_text(json.dumps({
            "access_token": access_token,
            "saved_at": datetime.now().isoformat(),
        }))
    except Exception as exc:
        logger.warning("Could not persist token: %s", exc)


def get_neo_client():
    """
    Return an authenticated Kotak Neo API client.

    On first run (or after token expiry) prompts for OTP interactively.
    Subsequent runs reuse the cached token.
    """
    try:
        import neo_api_client
    except ImportError as exc:
        raise RuntimeError(
            "neo-api-client not installed. Run: pip install neo-api-client"
        ) from exc

    cfg = settings.broker
    client = neo_api_client.NeoAPI(
        consumer_key=cfg.consumer_key,
        consumer_secret=cfg.consumer_secret,
        environment=cfg.environment,
        access_token=None,
        neo_fin_key=None,
    )

    cached = _load_cached_token()
    if cached:
        client.access_token = cached
        return client

    # Interactive login
    mobile = os.getenv("NEO_MOBILE", "")
    password = os.getenv("NEO_PASSWORD", "")
    if not mobile or not password:
        mobile = input("Kotak Neo mobile number: ").strip()
        password = input("Kotak Neo password: ").strip()

    logger.info("Logging in to Kotak Neo…")
    client.login(mobilenumber=mobile, password=password)

    otp = input("Enter OTP sent to your mobile/email: ").strip()
    client.session_2fa(OTP=otp)

    if client.access_token:
        _save_token(client.access_token)
        logger.info("Authentication successful. Token cached.")
    else:
        raise RuntimeError("Authentication failed — no access token received.")

    return client


def refresh_if_needed(client) -> None:
    """
    Re-authenticate if token is nearing expiry.
    Call this at the start of each trading day.
    """
    cached = _load_cached_token()
    if cached is None:
        logger.info("Token refresh required")
        get_neo_client()
