"""User and profile management services."""

from __future__ import annotations

import json
from pathlib import Path

import qa_database
import state

# Shared file so main.py (audio process) can read the active user set via the UI
_ACTIVE_USER_FILE = Path.home() / ".drishi" / "active_user.json"


def _persist_active_user(user: dict) -> None:
    """Write active user to shared file so main.py can read it across the process boundary."""
    try:
        _ACTIVE_USER_FILE.parent.mkdir(parents=True, exist_ok=True)
        _ACTIVE_USER_FILE.write_text(json.dumps(dict(user)), encoding="utf-8")
    except Exception:
        pass


def _load_active_user_from_file() -> dict | None:
    """Read active user written by web/server.py."""
    try:
        if _ACTIVE_USER_FILE.exists():
            return json.loads(_ACTIVE_USER_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def get_user_profile_payload(user_id: int):
    user = qa_database.get_user(user_id)
    if not user:
        return None
    return dict(user)


def update_user_profile_payload(user_id: int, data: dict) -> tuple[dict, int]:
    ok = qa_database.update_user(
        user_id,
        name=data.get("name"),
        role=data.get("role"),
        experience_years=data.get("experience_years"),
        resume_text=data.get("resume_text"),
        job_description=data.get("job_description"),
        self_introduction=data.get("self_introduction"),
        key_skills=data.get("key_skills"),
        custom_instructions=data.get("custom_instructions"),
        domain=data.get("domain"),
    )
    if not ok:
        return {"error": "Update failed"}, 500

    active = state.get_selected_user()
    if active and active.get("id") == user_id:
        updated = qa_database.get_user(user_id)
        if updated:
            state.set_selected_user(dict(updated))
            _persist_active_user(dict(updated))
    return {"ok": True}, 200


def activate_user_payload(user_id: int) -> tuple[dict, int]:
    user = qa_database.get_user(user_id)
    if not user:
        return {"error": "User not found"}, 404
    state.set_selected_user(user)
    _persist_active_user(user)
    # Prime semantic engine topic prediction for the new role
    try:
        import semantic_engine
        semantic_engine.engine.set_role_topics(user.get("role", ""))
    except Exception:
        pass
    return {"status": "activated", "name": user["name"]}, 200


def list_users_payload():
    return qa_database.get_all_users()


def create_user_payload(data: dict) -> tuple[dict, int]:
    if not data or "name" not in data or "role" not in data:
        return {"error": "name and role are required"}, 400

    domain = data.get("domain", "")
    user_id = qa_database.add_user(
        name=data["name"],
        role=data["role"],
        experience_years=int(data.get("experience_years", 0)),
        resume_text=data.get("resume_text", ""),
        job_description=data.get("job_description", ""),
        self_introduction=data.get("self_introduction", ""),
        domain=domain,
    )
    return {"id": user_id, "status": "created"}, 201


def get_user_payload(user_id: int) -> tuple[dict, int]:
    user = qa_database.get_user(user_id)
    if not user:
        return {"error": "User not found"}, 404
    return user, 200


def update_user_payload(user_id: int, data: dict) -> tuple[dict, int]:
    if not data:
        return {"error": "No data provided"}, 400

    ok = qa_database.update_user(
        user_id=user_id,
        name=data.get("name"),
        role=data.get("role"),
        experience_years=data.get("experience_years"),
        resume_text=data.get("resume_text"),
        job_description=data.get("job_description"),
        self_introduction=data.get("self_introduction"),
    )
    if not ok:
        return {"error": "User not found"}, 404
    return {"status": "updated"}, 200


def delete_user_payload(user_id: int) -> tuple[dict, int]:
    ok = qa_database.delete_user(user_id)
    if not ok:
        return {"error": "User not found"}, 404
    return {"status": "deleted"}, 200


def list_prepared_questions_payload():
    return qa_database.get_all_questions()


def create_prepared_question_payload(data: dict) -> tuple[dict, int]:
    if not data or "question" not in data or "prepared_answer" not in data or "role" not in data:
        return {"error": "question, prepared_answer, and role are required"}, 400

    q_id = qa_database.add_prepared_question(
        role=data["role"],
        question=data["question"],
        prepared_answer=data["prepared_answer"],
    )
    return {"id": q_id, "status": "created"}, 201


def delete_prepared_question_payload(q_id: int) -> tuple[dict, int]:
    ok = qa_database.delete_prepared_question(q_id)
    if not ok:
        return {"error": "Question not found"}, 404
    return {"status": "deleted"}, 200


def get_user_resume_file(user_id: int) -> tuple[Path | None, dict | None, int]:
    user = qa_database.get_user(user_id)
    if not user:
        return None, {"error": "User not found"}, 404

    resume_path = (user.get("resume_path") or "").strip()
    if not resume_path or not Path(resume_path).is_file():
        return None, {"error": "No resume file on record for this user"}, 404

    return Path(resume_path), None, 200


def get_user_profile_snapshot_payload(user_id: int) -> tuple[dict, int]:
    user = qa_database.get_user(user_id)
    if not user:
        return {"error": "User not found"}, 404

    return {
        "id": user["id"],
        "name": user.get("name", ""),
        "key_skills": user.get("key_skills", ""),
        "domain": user.get("domain", ""),
        "custom_instructions": user.get("custom_instructions", ""),
        "resume_file": user.get("resume_file", ""),
        "resume_path": user.get("resume_path", ""),
        "resume_summary": user.get("resume_summary", ""),
        "updated_at": user.get("updated_at", ""),
    }, 200
