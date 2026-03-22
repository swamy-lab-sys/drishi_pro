"""Knowledge-base and Q&A management routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.services.knowledge_service import (
    add_qa_payload,
    bulk_save_to_db_payload,
    delete_qa_payload,
    get_qa_payload,
    keyword_search_payload,
    qa_auto_tag_payload,
    qa_list_payload,
    qa_tags_payload,
    qa_test_payload,
    regenerate_answer_payload,
    save_to_db_payload,
    update_qa_payload,
)

knowledge_bp = Blueprint("knowledge", __name__)


@knowledge_bp.route("/api/qa", methods=["POST"])
def qa_add():
    """Add a new Q&A pair."""
    payload, status = add_qa_payload(request.get_json())
    return jsonify(payload), status


@knowledge_bp.route("/api/qa/<int:qa_id>", methods=["GET"])
def qa_get(qa_id: int):
    """Get a single Q&A pair."""
    payload, status = get_qa_payload(qa_id)
    return jsonify(payload), status


@knowledge_bp.route("/api/qa/<int:qa_id>", methods=["PUT"])
def qa_update(qa_id: int):
    """Update an existing Q&A pair."""
    payload, status = update_qa_payload(qa_id, request.get_json())
    return jsonify(payload), status


@knowledge_bp.route("/api/qa/<int:qa_id>", methods=["DELETE"])
def qa_delete(qa_id: int):
    """Delete a Q&A pair."""
    payload, status = delete_qa_payload(qa_id)
    return jsonify(payload), status


@knowledge_bp.route("/api/qa/tags", methods=["GET"])
def qa_tags():
    """Return all unique tags with counts for filter UI."""
    return jsonify(qa_tags_payload())


@knowledge_bp.route("/api/qa/auto-tag", methods=["POST"])
def qa_auto_tag():
    """Re-run auto-tagging on all untagged entries."""
    payload, status = qa_auto_tag_payload()
    return jsonify(payload), status


@knowledge_bp.route("/api/qa", methods=["GET"])
def qa_list_by_tag():
    """List Q&A pairs, optionally filtered by tag and search."""
    search = request.args.get("search", "").strip()
    tag = request.args.get("tag", "").strip()
    return jsonify(qa_list_payload(search, tag))


@knowledge_bp.route("/api/qa/test", methods=["POST"])
def qa_test():
    """Test DB lookup for a given question text."""
    payload, status = qa_test_payload(request.get_json())
    return jsonify(payload), status


@knowledge_bp.route("/api/regenerate", methods=["POST"])
def regenerate_answer():
    """Force a fresh API answer for a question, bypassing DB cache."""
    payload, status = regenerate_answer_payload(request.get_json())
    return jsonify(payload), status


@knowledge_bp.route("/api/save_to_db", methods=["POST"])
def save_to_db():
    """Quick-save a Q&A pair from the current interview session to the DB."""
    payload, status = save_to_db_payload(request.get_json())
    return jsonify(payload), status


@knowledge_bp.route("/api/search")
def keyword_search():
    """Quick keyword search across the Q&A database."""
    return jsonify(keyword_search_payload(request.args.get("q", "")))


@knowledge_bp.route("/api/bulk_save_to_db", methods=["POST"])
def bulk_save_to_db():
    """Bulk-save selected Q&A pairs to the permanent database."""
    payload, status = bulk_save_to_db_payload(request.get_json())
    return jsonify(payload), status
