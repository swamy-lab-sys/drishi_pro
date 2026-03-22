"""Routes for live caption/chat controls and browser voice capture helpers."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.services.live_capture_service import (
    cc_status_payload,
    chat_questions_payload,
    set_cc_capture_state,
    solve_voice_question_payload,
    transcribe_audio_upload_payload,
)

live_capture_bp = Blueprint("live_capture", __name__)


@live_capture_bp.route("/api/cc_control", methods=["POST"])
def cc_control():
    payload = set_cc_capture_state((request.get_json() or {}).get("action", ""))
    return jsonify(payload)


@live_capture_bp.route("/api/cc_status")
def cc_status():
    return jsonify(cc_status_payload())


@live_capture_bp.route("/voice/transcribe", methods=["POST"])
def transcribe_audio():
    payload, status = transcribe_audio_upload_payload(request.files.get("audio"))
    return jsonify(payload), status


@live_capture_bp.route("/api/solve", methods=["POST"])
def solve_voice_question():
    payload, status = solve_voice_question_payload(request.get_json())
    return jsonify(payload), status


@live_capture_bp.route("/api/chat_questions")
def get_chat_questions():
    return jsonify(chat_questions_payload())
