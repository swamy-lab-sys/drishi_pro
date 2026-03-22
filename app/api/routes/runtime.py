"""Runtime and health routes for Drishi Enterprise."""

from __future__ import annotations

from flask import Blueprint, jsonify

import config
from app.core.product import PRODUCT_NAME
from app.services.runtime_service import get_runtime_profile

runtime_bp = Blueprint("runtime", __name__)


@runtime_bp.route("/health")
def health_check():
    """Render.com health check — must return 200 quickly."""
    return jsonify({
        "status": "ok",
        "product": PRODUCT_NAME,
        "app_mode": config.APP_MODE,
        "cloud": config.CLOUD_MODE,
    })


@runtime_bp.route("/api/runtime_profile")
def runtime_profile():
    """Stable runtime profile for UI, diagnostics, and deployment verification."""
    return jsonify(get_runtime_profile())
