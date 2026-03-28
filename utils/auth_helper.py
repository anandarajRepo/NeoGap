"""
NeoGap — Kotak Neo authentication helper.

Handles the new two-step login flow:
  Step 2a: POST /tradeApiLogin  — TOTP verification → VIEW_TOKEN + VIEW_SID
  Step 2b: POST /tradeApiValidate — MPIN validation  → TRADING_TOKEN + TRADING_SID + BASE_URL

Credentials required (via env or interactive prompt):
  NEO_ACCESS_TOKEN  Developer API access token (Authorization header in auth requests)
  NEO_MOBILE        Registered mobile with country code, e.g. +91XXXXXXXXXX
  NEO_UCC           5-character client code from the developer portal
  NEO_MPIN          6-digit MPIN (optional; prompted interactively if absent)

Trading session values (TRADING_TOKEN, TRADING_SID, BASE_URL) are persisted to
.neo_token.json and reused for up to 20 hours.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

from config.settings import settings
from utils.logger import get_logger

logger = get_logger("auth_helper", settings.ops.log_level, settings.ops.log_file)

_TOKEN_FILE = Path(".neo_token.json")
_TOKEN_EXPIRY_HOURS = 20  # Conservative: Neo tokens valid ~24h

_LOGIN_URL = "https://mis.kotaksecurities.com/login/1.0/tradeApiLogin"
_VALIDATE_URL = "https://mis.kotaksecurities.com/login/1.0/tradeApiValidate"
_NEO_FIN_KEY = "neotradeapi"


# ---------------------------------------------------------------------------
# Token cache helpers
# ---------------------------------------------------------------------------

def _load_cached_token() -> Optional[dict]:
    """Return cached session dict if still valid, else None.

    Returns a dict with keys: trading_token, trading_sid, base_url
    """
    if not _TOKEN_FILE.exists():
        return None
    try:
        data = json.loads(_TOKEN_FILE.read_text())
        saved_at = datetime.fromisoformat(data["saved_at"])
        if datetime.now() - saved_at < timedelta(hours=_TOKEN_EXPIRY_HOURS):
            logger.info("Using cached Neo session (saved %s)", saved_at.strftime("%H:%M"))
            return {
                "trading_token": data["trading_token"],
                "trading_sid": data["trading_sid"],
                "base_url": data["base_url"],
            }
        logger.info("Cached token expired — re-authenticating")
    except Exception as exc:
        logger.warning("Failed to read cached token: %s", exc)
    return None


def _save_token(trading_token: str, trading_sid: str, base_url: str) -> None:
    try:
        _TOKEN_FILE.write_text(json.dumps({
            "trading_token": trading_token,
            "trading_sid": trading_sid,
            "base_url": base_url,
            "saved_at": datetime.now().isoformat(),
        }))
    except Exception as exc:
        logger.warning("Could not persist token: %s", exc)


# ---------------------------------------------------------------------------
# Step 2a: TOTP login
# ---------------------------------------------------------------------------

def _do_totp_login(mobile: str, ucc: str, totp: str, access_token: str) -> tuple[str, str]:
    """POST /tradeApiLogin — returns (view_token, view_sid)."""
    resp = requests.post(
        _LOGIN_URL,
        headers={
            "Authorization": access_token,
            "neo-fin-key": _NEO_FIN_KEY,
            "Content-Type": "application/json",
        },
        json={
            "mobileNumber": mobile,
            "ucc": ucc,
            "totp": totp,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    if data.get("status") != "success":
        raise RuntimeError(f"TOTP login failed: {data}")
    logger.info("TOTP login successful (kType=%s)", data.get("kType"))
    return data["token"], data["sid"]


# ---------------------------------------------------------------------------
# Step 2b: MPIN validation
# ---------------------------------------------------------------------------

def _do_mpin_validate(
    mpin: str,
    view_token: str,
    view_sid: str,
    access_token: str,
) -> tuple[str, str, str]:
    """POST /tradeApiValidate — returns (trading_token, trading_sid, base_url)."""
    resp = requests.post(
        _VALIDATE_URL,
        headers={
            "Authorization": access_token,
            "neo-fin-key": _NEO_FIN_KEY,
            "sid": view_sid,
            "Auth": view_token,
            "Content-Type": "application/json",
        },
        json={"mpin": mpin},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    if data.get("status") != "success":
        raise RuntimeError(f"MPIN validation failed: {data}")
    logger.info("MPIN validation successful (kType=%s)", data.get("kType"))
    return data["token"], data["sid"], data["baseUrl"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_neo_client():
    """
    Return an authenticated Kotak Neo API client.

    Performs the two-step authentication (TOTP → MPIN) on first run or after
    token expiry, then caches the trading session for subsequent runs.

    Returns a neo_api_client.NeoAPI instance with access_token, sid, and
    base_url set from the authenticated trading session.
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
        client.access_token = cached["trading_token"]
        client.sid = cached["trading_sid"]
        client.base_url = cached["base_url"]
        return client

    # ── Gather credentials ────────────────────────────────────────────────
    mobile = os.getenv("NEO_MOBILE", "").strip()
    ucc = (cfg.ucc or os.getenv("NEO_UCC", "")).strip()
    mpin = os.getenv("NEO_MPIN", "").strip()

    if not mobile:
        mobile = input("Registered mobile number (+91XXXXXXXXXX): ").strip()
    if not ucc:
        ucc = input("5-character client code (UCC): ").strip()

    totp = input("TOTP from authenticator app: ").strip()

    # ── Step 2a: TOTP login ───────────────────────────────────────────────
    logger.info("Step 2a: TOTP login…")
    view_token, view_sid = _do_totp_login(mobile, ucc, totp, cfg.access_token)

    # ── Step 2b: MPIN validation ──────────────────────────────────────────
    if not mpin:
        mpin = input("6-digit MPIN: ").strip()

    logger.info("Step 2b: MPIN validation…")
    trading_token, trading_sid, base_url = _do_mpin_validate(
        mpin, view_token, view_sid, cfg.access_token
    )

    _save_token(trading_token, trading_sid, base_url)
    logger.info("Authentication successful. Trading session cached.")

    client.access_token = trading_token
    client.sid = trading_sid
    client.base_url = base_url
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
