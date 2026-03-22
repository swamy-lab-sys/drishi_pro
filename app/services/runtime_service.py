"""Runtime metadata helpers for UI and operations."""

from __future__ import annotations

import config
from app.core.product import PRODUCT_NAME, TAGLINE


def get_runtime_profile() -> dict:
    profile = config.runtime_profile()
    profile.update({
        "status": "ok",
        "tagline": TAGLINE,
        "product": PRODUCT_NAME,
    })
    return profile
