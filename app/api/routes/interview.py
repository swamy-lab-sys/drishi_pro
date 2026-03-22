"""Core interview routes: ask, stream, and CC question ingest."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.services.interview_service import (
    ask_question_payload,
    cc_question_payload,
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
