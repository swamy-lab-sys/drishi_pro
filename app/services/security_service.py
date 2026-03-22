"""Security and mode-profile helpers."""

from __future__ import annotations

import config
import state


def set_mode_profile_payload(data: dict | None) -> dict:
    profile = (data or {}).get("profile", "interview")
    state.set_mode_profile(profile)
    return {"status": "updated", "mode": profile}


def authenticate_payload(code: str) -> tuple[dict, int]:
    if config.SECRET_CODE and code != config.SECRET_CODE:
        return {"ok": False, "error": "Wrong secret code"}, 401
    return {"ok": True, "token": config.SECRET_CODE or "local"}, 200
