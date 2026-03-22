"""Resume and document handling services."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import qa_database
import state

UPLOADED_RESUME_PATH = Path.home() / ".drishi" / "uploaded_resume.txt"
RESUME_STORE = Path.home() / ".drishi" / "resumes"


def upload_resume_payload(file_storage) -> tuple[dict, int]:
    """Upload a generic resume file and save extracted text to shared storage."""
    if file_storage is None:
        return {"error": "No selected file"}, 400

    try:
        from resume_loader import invalidate_resume_cache

        UPLOADED_RESUME_PATH.parent.mkdir(parents=True, exist_ok=True)
        content = file_storage.read()
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("latin-1")

        if text.startswith("%PDF"):
            import subprocess
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            try:
                result = subprocess.run(
                    ["pdftotext", tmp_path, "-"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                text = result.stdout.strip()
            except Exception:
                text = ""
            finally:
                os.unlink(tmp_path)

        if not text.strip():
            return {"error": "Could not extract text from file"}, 400

        with open(UPLOADED_RESUME_PATH, "w", encoding="utf-8") as handle:
            handle.write(text)

        invalidate_resume_cache()
        print(f"[SERVER] Resume uploaded: {len(text)} chars")
        return {"success": True, "message": f"Resume uploaded ({len(text)} chars)"}, 200
    except Exception as exc:
        return {"error": str(exc)}, 500


def resume_status_payload() -> dict:
    uploaded = UPLOADED_RESUME_PATH.exists() and UPLOADED_RESUME_PATH.stat().st_size > 0
    return {"uploaded": uploaded}


def upload_user_resume_payload(user_id: int, file_storage) -> tuple[dict, int]:
    """Upload and store a PDF/text resume for a specific user profile."""
    user = qa_database.get_user(user_id)
    if not user:
        return {"error": "User not found"}, 404
    if file_storage is None:
        return {"error": "No file selected"}, 400

    try:
        from user_manager import extract_pdf_text, summarize_resume
        from werkzeug.utils import secure_filename

        orig_name = secure_filename(file_storage.filename or "resume")
        ext = Path(orig_name).suffix.lower()
        if ext not in (".pdf", ".txt", ".doc", ".docx"):
            ext = ".pdf"
            orig_name = Path(orig_name).stem + ext

        user_dir = RESUME_STORE / str(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        saved_path = user_dir / orig_name
        file_storage.save(str(saved_path))

        text = extract_pdf_text(str(saved_path))
        if not text.strip():
            saved_path.unlink(missing_ok=True)
            return {"error": "Could not extract text from file"}, 400

        summary = summarize_resume(text)

        qa_database.update_user(
            user_id=user_id,
            resume_text=text,
            resume_file=orig_name,
            resume_path=str(saved_path),
            resume_summary=summary,
        )

        active = state.get_selected_user()
        if active and active.get("id") == user_id:
            updated = qa_database.get_user(user_id)
            if updated:
                state.set_selected_user(updated)

        print(f"[SERVER] Resume saved for user {user_id}: {saved_path} ({len(text)} chars)")

        def _enrich_profile_async(uid, resume_text, active_user_ref):
            try:
                from llm_client import extract_profile_from_resume

                profile = extract_profile_from_resume(resume_text)
                if not profile:
                    return
                qa_database.update_user(uid, **profile)
                print(f"[SERVER] LLM profile enriched for user {uid}: {list(profile.keys())}")
                cur = active_user_ref.get("id")
                if cur and cur == uid:
                    updated = qa_database.get_user(uid)
                    if updated:
                        state.set_selected_user(updated)
            except Exception as exc:
                print(f"[SERVER] Profile enrichment failed: {exc}")

        threading.Thread(
            target=_enrich_profile_async,
            args=(user_id, text, state.get_selected_user() or {}),
            daemon=True,
        ).start()

        return {
            "success": True,
            "message": f"Resume saved ({len(text)} chars)",
            "summary": summary,
            "filename": orig_name,
            "path": str(saved_path),
            "llm_enrichment": "started",
        }, 200
    except Exception as exc:
        return {"error": str(exc)}, 500
