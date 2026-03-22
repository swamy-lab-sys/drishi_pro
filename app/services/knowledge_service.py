"""Knowledge-base and Q&A management services."""

from __future__ import annotations

from pathlib import Path

import llm_client
import qa_database
import answer_storage
import config
from app.services.document_service import UPLOADED_RESUME_PATH


def add_qa_payload(data: dict | None) -> tuple[dict, int]:
    if not data or not data.get("question"):
        return {"error": "question is required"}, 400

    # ── Duplicate check: warn if a similar question already exists ────────────
    # Skip check if caller explicitly passes force=true
    if not data.get("force"):
        try:
            similar = qa_database.find_answer(data["question"], want_code=False)
            if similar and similar[1] >= 0.80:
                _, score, similar_id = similar
                similar_row = qa_database.get_qa(similar_id)
                return {
                    "warning": "similar_exists",
                    "message": f"A similar question exists (score {score:.0%}). Pass force=true to save anyway.",
                    "similar_id": similar_id,
                    "similar_question": (similar_row or {}).get("question", ""),
                    "score": round(score, 3),
                }, 409
        except Exception:
            pass  # Never let duplicate check block saves

    qa_id = qa_database.add_qa(
        question=data["question"],
        answer_theory=data.get("answer_theory", ""),
        answer_coding=data.get("answer_coding", ""),
        qa_type=data.get("type", "theory"),
        keywords=data.get("keywords", ""),
        aliases=data.get("aliases", ""),
        tags=data.get("tags", ""),
        company=data.get("company", ""),
        role_tag=data.get("role_tag", ""),
    )
    return {"id": qa_id, "status": "created"}, 201


def get_qa_payload(qa_id: int) -> tuple[dict, int]:
    row = qa_database.get_qa(qa_id)
    if not row:
        return {"error": "Not found"}, 404
    return row, 200


def update_qa_payload(qa_id: int, data: dict | None) -> tuple[dict, int]:
    if not data:
        return {"error": "No data provided"}, 400
    ok = qa_database.update_qa(
        qa_id=qa_id,
        question=data.get("question"),
        answer_theory=data.get("answer_theory"),
        answer_coding=data.get("answer_coding"),
        qa_type=data.get("type"),
        keywords=data.get("keywords"),
        aliases=data.get("aliases"),
        tags=data.get("tags"),
    )
    if not ok:
        return {"error": "Not found"}, 404
    return {"status": "updated"}, 200


def delete_qa_payload(qa_id: int) -> tuple[dict, int]:
    ok = qa_database.delete_qa(qa_id)
    if not ok:
        return {"error": "Not found"}, 404
    return {"status": "deleted"}, 200


def qa_tags_payload() -> dict:
    stats = qa_database.get_stats()
    return stats.get("tags_breakdown", {})


def qa_auto_tag_payload() -> tuple[dict, int]:
    try:
        updated = qa_database.apply_auto_tags()
        return {"status": "ok", "updated": updated}, 200
    except Exception as exc:
        return {"error": str(exc)}, 500


def qa_list_payload(search: str, tag: str) -> dict:
    rows = qa_database.get_all_qa(search, tag=tag)
    stats = qa_database.get_stats()
    return {"items": rows, "stats": stats}


def qa_test_payload(data: dict | None) -> tuple[dict, int]:
    if not data or not data.get("question"):
        return {"error": "question required"}, 400
    want_code = data.get("want_code", False)
    result = qa_database.find_answer(data["question"], want_code=want_code)
    if result:
        answer, score, qa_id = result
        return {"found": True, "score": round(score, 3), "qa_id": qa_id, "answer": answer}, 200
    return {"found": False, "score": 0}, 200


def regenerate_answer_payload(data: dict | None) -> tuple[dict, int]:
    if not data or not data.get("question"):
        return {"error": "question required"}, 400

    question_text = data.get("question", "").strip()
    if not question_text:
        return {"error": "Empty question"}, 400

    print(f"\n[REGEN] Forcing API answer for: {question_text[:60]}...")

    try:
        from resume_loader import load_resume, load_job_description

        resume_text = load_resume(UPLOADED_RESUME_PATH) if UPLOADED_RESUME_PATH.exists() else ""
        jd_text = load_job_description(Path.cwd() / config.JD_PATH)
    except Exception:
        resume_text = ""
        jd_text = ""

    try:
        from question_validator import is_code_request

        wants_code = is_code_request(question_text)
        if wants_code:
            answer = llm_client.get_coding_answer(question_text)
        else:
            answer = llm_client.get_interview_answer(
                question_text,
                resume_text=resume_text,
                job_description=jd_text,
            )

        if answer:
            answer_storage.set_complete_answer(
                question_text=question_text,
                answer_text=answer,
                metrics={"source": "api-regen"},
            )
            print(f"[REGEN] Done ({len(answer)} chars)")
            return {"status": "ok", "answer": answer}, 200
        return {"error": "No answer generated"}, 500
    except Exception as exc:
        print(f"[REGEN] Error: {exc}")
        return {"error": str(exc)}, 500


def save_to_db_payload(data: dict | None) -> tuple[dict, int]:
    if not data or not data.get("question"):
        return {"error": "question required"}, 400

    question = data.get("question", "").strip()
    answer = data.get("answer", "").strip()
    source = data.get("source", "interview").strip()

    if not answer:
        return {"error": "answer required"}, 400

    qa_id = qa_database.save_interview_qa(question, answer, source=source)
    if qa_id == -1:
        return {"status": "exists", "message": "Question already in DB"}, 200

    print(f"[SAVE] Saved interview Q to DB (id={qa_id}): {question[:60]}")
    return {"status": "saved", "id": qa_id}, 200


def keyword_search_payload(query: str) -> dict:
    query = (query or "").strip()
    if not query:
        return {"results": [], "query": ""}

    results = qa_database.get_all_qa(search=query)

    def _score(row):
        q_low = query.lower()
        q_words = set(q_low.split())
        text = " ".join([
            (row.get("question") or ""),
            (row.get("keywords") or ""),
            (row.get("tags") or ""),
            (row.get("aliases") or ""),
        ]).lower()
        hits = sum(1 for word in q_words if word in text)
        exact = q_low in text
        return hits * 10 + (20 if exact else 0) + (row.get("hit_count") or 0)

    results.sort(key=_score, reverse=True)
    top = results[:10]

    out = []
    for row in top:
        answer = row.get("answer_humanized") or row.get("answer_theory") or row.get("answer_coding") or ""
        coding = row.get("answer_coding") or ""
        out.append({
            "id": row.get("id"),
            "question": row.get("question") or "",
            "answer": answer,
            "coding": coding,
            "tags": row.get("tags") or "",
            "keywords": row.get("keywords") or "",
            "type": row.get("type") or "theory",
            "hits": row.get("hit_count") or 0,
        })

    return {"results": out, "query": query, "total": len(results)}


def bulk_save_to_db_payload(data: dict | None) -> tuple[dict, int]:
    if not data or "items" not in data:
        return {"error": "No items provided"}, 400

    saved = 0
    skipped = 0
    errors = []

    for item in data["items"]:
        question = (item.get("question") or "").strip()
        answer = (item.get("answer") or "").strip()
        if not question or not answer:
            skipped += 1
            continue
        try:
            qa_id = qa_database.save_interview_qa(question, answer)
            if qa_id and qa_id > 0:
                saved += 1
            else:
                skipped += 1
        except Exception as exc:
            errors.append(str(exc))
            skipped += 1

    print(f"[BULK-SAVE] Saved {saved}, skipped {skipped}")
    return {"saved": saved, "skipped": skipped, "errors": errors[:5]}, 200
