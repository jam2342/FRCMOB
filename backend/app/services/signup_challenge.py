"""Verify a one-use human challenge before consuming the public signup budget."""
from __future__ import annotations

from fastapi import HTTPException
import requests

from app.core.config import settings

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
SIGNUP_ACTION = "workspace_create"


def challenge_required() -> bool:
    return bool(settings.is_production_like or settings.workspace_signup_challenge_required)


def signup_policy() -> dict:
    return {"required": challenge_required(), "site_key": str(settings.turnstile_site_key or "").strip() or None}


def verify_signup_challenge(token: str | None) -> None:
    if not challenge_required():
        return
    secret = str(settings.turnstile_secret_key or "").strip()
    allowed = {value.strip().lower() for value in settings.turnstile_allowed_hostnames.split(",") if value.strip()}
    if not secret or not allowed or not str(settings.turnstile_site_key or "").strip():
        raise HTTPException(status_code=503, detail="New team signup is temporarily unavailable. Existing teams can still join.")
    if not token or not token.strip() or len(token) > 2048:
        raise HTTPException(status_code=403, detail="Complete the verification before creating your team.")
    try:
        # The fixed URL and no redirects keep the secret away from arbitrary hosts.
        # Do not trust client-supplied forwarded addresses or log the token/response.
        response = requests.post(
            SITEVERIFY_URL, data={"secret": secret, "response": token},
            timeout=(3, 5), allow_redirects=False,
        )
        if response.status_code != 200:
            raise ValueError("Verification unavailable")
        result = response.json()
    except (requests.RequestException, ValueError):
        raise HTTPException(status_code=503, detail="Verification is temporarily unavailable. Try again.") from None
    if not isinstance(result, dict) or result.get("success") is not True or result.get("action") != SIGNUP_ACTION or str(result.get("hostname") or "").lower() not in allowed:
        raise HTTPException(status_code=403, detail="Verification expired or failed. Please try again.")
