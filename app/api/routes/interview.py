"""Core interview routes: ask, stream, CC question ingest, and interview utilities."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.services.interview_service import (
    ask_question_payload,
    cc_question_payload,
    get_interview_tips_payload,
    get_prep_questions_payload,
    stream_response,
)


interview_bp = Blueprint("interview", __name__)


@interview_bp.route("/api/ask", methods=["POST"])
def ask_question():
    payload, status = ask_question_payload(request.get_json(force=True, silent=True))
    return jsonify(payload), status


@interview_bp.route("/api/stream")
def stream():
    return stream_response()


@interview_bp.route("/api/cc_question", methods=["POST"])
def cc_question():
    payload, status = cc_question_payload(request.get_json())
    return jsonify(payload), status


@interview_bp.route("/api/interview_tips")
def interview_tips():
    """Return live interview tips for the current role and round.

    Useful to show on the main screen during an interview.
    Query params:
      - role: override current role (optional)
      - round: override current round (optional)
    """
    role = request.args.get("role", "").strip()
    return jsonify(get_interview_tips_payload(role=role))


@interview_bp.route("/api/prep_questions")
def prep_questions():
    """Return the top N likely questions for the current role from the DB.

    Useful for pre-interview practice.
    Query params:
      - role: override current role (optional)
      - limit: max questions to return (default 20, max 50)
      - tag: filter by tag (optional)
    """
    role = request.args.get("role", "").strip()
    tag = request.args.get("tag", "").strip()
    try:
        limit = min(int(request.args.get("limit", 20)), 50)
    except (ValueError, TypeError):
        limit = 20
    return jsonify(get_prep_questions_payload(role=role, tag=tag, limit=limit))
