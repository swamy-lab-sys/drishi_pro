"""Coding workflow and extension support routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.services.coding_service import (
    answer_by_index_payload,
    clear_session_payload,
    code_payload,
    code_payloads,
    coding_state_payload,
    control_pause_payload,
    control_start_payload,
    control_stop_payload,
    control_toggle_mode_payload,
    latest_code_payload,
    performance_payload,
    set_llm_model_payload,
    solve_problem_payload,
)

coding_bp = Blueprint("coding", __name__)


@coding_bp.route("/api/set_llm_model", methods=["POST"])
def set_llm_model():
    payload, status = set_llm_model_payload(request.get_json())
    return jsonify(payload), status


@coding_bp.route("/api/performance")
def get_performance():
    payload, status = performance_payload()
    return jsonify(payload), status


@coding_bp.route("/api/clear_session", methods=["POST"])
def clear_session():
    payload, status = clear_session_payload()
    return jsonify(payload), status


@coding_bp.route("/api/code_payload")
def get_code_payload():
    return jsonify(code_payload())


@coding_bp.route("/api/code_payloads")
def get_code_payloads():
    return jsonify(code_payloads())


@coding_bp.route("/api/coding_state")
def coding_state():
    return jsonify(coding_state_payload())


@coding_bp.route("/api/solve_problem", methods=["POST"])
def solve_problem():
    payload, status = solve_problem_payload(request.get_json())
    return jsonify(payload), status


@coding_bp.route("/api/latest_code")
def get_latest_code():
    return jsonify(latest_code_payload())


@coding_bp.route("/api/control/start", methods=["POST"])
def control_start():
    return jsonify(control_start_payload())


@coding_bp.route("/api/control/pause", methods=["POST"])
def control_pause():
    return jsonify(control_pause_payload())


@coding_bp.route("/api/control/stop", methods=["POST"])
def control_stop():
    return jsonify(control_stop_payload())


@coding_bp.route("/api/control/toggle_mode", methods=["POST"])
def control_toggle_mode():
    return jsonify(control_toggle_mode_payload())


@coding_bp.route("/api/get_answer_by_index", methods=["GET"])
def get_answer_by_index():
    payload, status = answer_by_index_payload(request.args.get("index", "0"))
    return jsonify(payload), status
