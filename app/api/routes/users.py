"""User, profile, and prepared-question routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request, send_file

from app.services.user_service import (
    activate_user_payload,
    create_prepared_question_payload,
    create_user_payload,
    delete_prepared_question_payload,
    delete_user_payload,
    get_user_payload,
    get_user_profile_payload,
    get_user_profile_snapshot_payload,
    get_user_resume_file,
    list_prepared_questions_payload,
    list_users_payload,
    update_user_payload,
    update_user_profile_payload,
)

users_bp = Blueprint("users", __name__)


@users_bp.route("/api/users/<int:user_id>/profile", methods=["GET"])
def get_user_profile(user_id: int):
    """Get full user profile."""
    payload = get_user_profile_payload(user_id)
    if payload is None:
        return jsonify({"error": "User not found"}), 404
    return jsonify(payload)


@users_bp.route("/api/users/<int:user_id>/profile", methods=["PATCH"])
def update_user_profile(user_id: int):
    """Update user profile fields including key skills and custom instructions."""
    data = request.get_json(force=True, silent=True) or {}
    payload, status = update_user_profile_payload(user_id, data)
    return jsonify(payload), status


@users_bp.route("/api/users/activate/<int:user_id>", methods=["POST"])
def activate_user(user_id: int):
    """Switch active user profile."""
    payload, status = activate_user_payload(user_id)
    return jsonify(payload), status


@users_bp.route("/api/users", methods=["GET"])
def list_users():
    """List all users."""
    return jsonify(list_users_payload())


@users_bp.route("/api/users", methods=["POST"])
def create_user():
    """Create a new user."""
    payload, status = create_user_payload(request.get_json())
    return jsonify(payload), status


@users_bp.route("/api/users/<int:user_id>", methods=["GET"])
def get_user_route(user_id: int):
    """Get a single user."""
    payload, status = get_user_payload(user_id)
    return jsonify(payload), status


@users_bp.route("/api/users/<int:user_id>", methods=["PUT"])
def update_user_route(user_id: int):
    """Update an existing user."""
    payload, status = update_user_payload(user_id, request.get_json())
    return jsonify(payload), status


@users_bp.route("/api/users/<int:user_id>", methods=["DELETE"])
def delete_user_route(user_id: int):
    """Delete a user."""
    payload, status = delete_user_payload(user_id)
    return jsonify(payload), status


@users_bp.route("/api/prepared-questions", methods=["GET"])
def list_prepared_questions():
    """List all prepared questions."""
    return jsonify(list_prepared_questions_payload())


@users_bp.route("/api/prepared-questions", methods=["POST"])
def create_prepared_question():
    """Add a new prepared question."""
    payload, status = create_prepared_question_payload(request.get_json())
    return jsonify(payload), status


@users_bp.route("/api/prepared-questions/<int:q_id>", methods=["DELETE"])
def delete_prepared_question_route(q_id: int):
    """Delete a prepared question."""
    payload, status = delete_prepared_question_payload(q_id)
    return jsonify(payload), status


@users_bp.route("/api/users/<int:user_id>/resume")
def view_user_resume(user_id: int):
    """Serve the stored resume file inline."""
    resume_path, error_payload, status = get_user_resume_file(user_id)
    if error_payload is not None:
        return jsonify(error_payload), status

    mime = "application/pdf" if resume_path.suffix.lower() == ".pdf" else "text/plain"
    return send_file(str(resume_path), mimetype=mime, as_attachment=False, download_name=resume_path.name)


@users_bp.route("/api/users/<int:user_id>/resume/download")
def download_user_resume(user_id: int):
    """Serve the stored resume file as a download attachment."""
    resume_path, error_payload, status = get_user_resume_file(user_id)
    if error_payload is not None:
        return jsonify(error_payload), status

    mime = "application/pdf" if resume_path.suffix.lower() == ".pdf" else "text/plain"
    return send_file(str(resume_path), mimetype=mime, as_attachment=True, download_name=resume_path.name)


@users_bp.route("/api/users/<int:user_id>/profile_snapshot")
def user_profile_snapshot(user_id: int):
    """Return only lightweight profile customization fields for polling."""
    payload, status = get_user_profile_snapshot_payload(user_id)
    return jsonify(payload), status
