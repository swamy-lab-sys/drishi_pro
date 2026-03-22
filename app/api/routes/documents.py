"""Resume and document upload routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.services.document_service import (
    resume_status_payload,
    upload_resume_payload,
    upload_user_resume_payload,
)

documents_bp = Blueprint("documents", __name__)


@documents_bp.route("/api/upload_resume", methods=["POST"])
def upload_resume():
    """Upload resume file. Saves as plain text to shared location."""
    if "resume" not in request.files:
        return jsonify({"error": "No file part"}), 400

    file_storage = request.files["resume"]
    if file_storage.filename == "":
        return jsonify({"error": "No selected file"}), 400

    payload, status = upload_resume_payload(file_storage)
    return jsonify(payload), status


@documents_bp.route("/api/resume_status")
def resume_status():
    """Check if resume was uploaded via UI."""
    return jsonify(resume_status_payload())


@documents_bp.route("/api/users/<int:user_id>/upload_resume", methods=["POST"])
def upload_user_resume(user_id: int):
    """Upload and store a PDF/text resume for a specific user profile."""
    if "resume" not in request.files:
        return jsonify({"error": "No file part"}), 400

    file_storage = request.files["resume"]
    if not file_storage or file_storage.filename == "":
        return jsonify({"error": "No file selected"}), 400

    payload, status = upload_user_resume_payload(user_id, file_storage)
    return jsonify(payload), status
