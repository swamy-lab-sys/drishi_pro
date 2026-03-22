"""Shared state and helper services for chat and voice mode."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import answer_storage
import llm_client
import qa_database
import config
from app.services.document_service import UPLOADED_RESUME_PATH


cc_capture_state = {
    "enabled": False,
    "last_question": "",
    "last_timestamp": 0,
}

chat_session: list[dict] = []
chat_lock = threading.Lock()


def set_cc_capture_state(action: str) -> dict:
    """Update live caption/chat capture mode."""
    if action == "start":
        cc_capture_state["enabled"] = True
        print("[CC] CC/Chat capture ENABLED")
    elif action == "stop":
        cc_capture_state["enabled"] = False
        print("[CC] CC/Chat capture DISABLED")

    return {
        "enabled": cc_capture_state["enabled"],
        "last_question": cc_capture_state["last_question"][:50]
        if cc_capture_state["last_question"]
        else "",
    }


def cc_status_payload() -> dict:
    """Return current CC capture state."""
    return {
        "enabled": cc_capture_state["enabled"],
        "last_question": cc_capture_state["last_question"][:50]
        if cc_capture_state["last_question"]
        else "",
        "last_timestamp": cc_capture_state["last_timestamp"],
    }


def append_chat_question(question: str, source: str, timestamp: float, status: str = "answered") -> None:
    """Store a validated question captured from chat/CC sources."""
    with chat_lock:
        chat_session.append({
            "question": question,
            "source": source,
            "timestamp": timestamp,
            "status": status,
        })


def chat_questions_payload() -> dict:
    """Return captured chat questions in reverse chronological order."""
    with chat_lock:
        items = list(reversed(chat_session))
    return {"items": items, "count": len(items)}


def transcribe_audio_upload_payload(audio_file) -> tuple[dict, int]:
    """Transcribe browser-recorded audio using the active STT engine."""
    if audio_file is None:
        return {"success": False, "error": "No audio file"}, 400

    try:
        import tempfile
        import numpy as np
        from pydub import AudioSegment
        from stt import transcribe

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            audio_file.save(tmp.name)
            tmp_path = tmp.name

        try:
            audio = AudioSegment.from_file(tmp_path)
            audio = audio.set_frame_rate(16000).set_channels(1)
            samples = np.array(audio.get_array_of_samples()).astype(np.float32) / 32768.0
            transcription, confidence = transcribe(samples)
            os.unlink(tmp_path)

            if transcription and transcription.strip():
                return {
                    "success": True,
                    "transcription": transcription.strip(),
                    "confidence": float(confidence),
                }, 200
            return {"success": False, "error": "Could not transcribe audio"}, 400
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
    except Exception as exc:
        print(f"[VOICE] Transcription error: {exc}")
        import traceback
        traceback.print_exc()
        return {"success": False, "error": f"Transcription failed: {exc}"}, 500


def solve_voice_question_payload(data: dict | None) -> tuple[dict, int]:
    """Solve a question submitted from push-to-talk voice mode."""
    data = data or {}
    if "problem" not in data:
        return {"error": "No question provided"}, 400

    question_text = data.get("problem", "").strip()
    source = data.get("source", "voice")
    if not question_text:
        return {"error": "Empty question"}, 400

    print(f"\n[VOICE] Question received: {question_text}")

    try:
        from resume_loader import load_job_description, load_resume

        resume_text = load_resume(UPLOADED_RESUME_PATH) if UPLOADED_RESUME_PATH.exists() else ""
        jd_text = load_job_description(Path.cwd() / config.JD_PATH)
    except Exception:
        resume_text = ""
        jd_text = ""

    try:
        from question_validator import is_code_request

        wants_code = is_code_request(question_text)
        db_result = qa_database.find_answer(question_text, want_code=wants_code)
        if db_result:
            answer, score, qa_id = db_result
            print(f"[VOICE] DB hit (score={score:.2f}, id={qa_id}) - skipping API call")
            src_label = "db-voice"
        elif wants_code:
            answer = llm_client.get_coding_answer(question_text)
            src_label = source
        else:
            answer = llm_client.get_interview_answer(
                question_text,
                resume_text=resume_text,
                job_description=jd_text,
                include_code=False,
            )
            src_label = source

        if not answer:
            return {"error": "No answer generated"}, 500

        answer_storage.set_complete_answer(
            question_text=question_text,
            answer_text=answer,
            metrics={"source": src_label},
        )
        print(f"[VOICE] Answer ready ({len(answer)} chars)")
        return {"success": True, "solution": answer}, 200
    except Exception as exc:
        print(f"[VOICE] Error: {exc}")
        import traceback
        traceback.print_exc()
        return {"error": str(exc)}, 500
