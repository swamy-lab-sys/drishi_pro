"""Interview ask/stream/CC orchestration services."""

from __future__ import annotations

import json
import queue as _queue
import re
import threading
import time
from pathlib import Path

from flask import Response

import answer_storage
import config
import fragment_context
import llm_client
import qa_database
import state
from app.services.document_service import UPLOADED_RESUME_PATH
from app.services.live_capture_service import append_chat_question, cc_capture_state
from question_validator import is_code_request, validate_question

try:
    import debug_logger as dlog
except ImportError:  # pragma: no cover - fallback for stripped environments
    class _DlogStub:
        def log(self, *args, **kwargs):
            pass

    dlog = _DlogStub()


_jd_cache = {"text": "", "mtime": 0.0}
_resume_cache = {"text": "", "mtime": 0.0}


def get_jd_text() -> str:
    """Return JD text, re-reading only when the file changes."""
    try:
        path = Path.cwd() / config.JD_PATH
        if not path.exists():
            return ""
        mtime = path.stat().st_mtime
        if mtime != _jd_cache["mtime"]:
            _jd_cache["text"] = path.read_text(encoding="utf-8")
            _jd_cache["mtime"] = mtime
    except Exception:
        pass
    return _jd_cache["text"]


def get_resume_text(resume_path: Path) -> str:
    """Return resume text, re-reading only when the file changes."""
    try:
        if not resume_path.exists():
            return ""
        mtime = resume_path.stat().st_mtime
        if mtime != _resume_cache["mtime"]:
            from resume_loader import load_resume

            _resume_cache["text"] = load_resume(resume_path)
            _resume_cache["mtime"] = mtime
    except Exception:
        pass
    return _resume_cache["text"]


_HAS_QUESTION_RE = re.compile(
    r"\b(what|how|why|when|where|which|who|whom|whose|explain|describe|write|"
    r"define|tell|can|could|is|are|does|do|did)\b",
    re.IGNORECASE,
)


def normalize_manual_question(raw_question: str) -> str:
    """Expand short keyword-style manual asks into full questions.
    Also tries the DB for known keywords to return the canonical question form."""
    question = (raw_question or "").strip()
    if not question:
        return ""

    words = question.split()
    has_question_word = _HAS_QUESTION_RE.search(question)
    if len(words) <= 3 and not has_question_word and not question.endswith("?"):
        # Try DB lookup for well-known short keywords first
        db_hit = qa_database.find_answer(question, want_code=False)
        if db_hit:
            return question  # DB knows it — pass as-is
        return f"What is {question}? Explain in detail with examples."
    return question


def _get_intro_answer(question: str) -> str | None:
    """Return self-introduction text for the active user, or None if not applicable."""
    from user_manager import is_introduction_question
    if not is_introduction_question(question):
        return None
    active_user = state.get_selected_user()
    if not active_user or not (active_user.get("self_introduction") or "").strip():
        try:
            from app.services.user_service import _load_active_user_from_file
            loaded = _load_active_user_from_file()
            if loaded:
                state.set_selected_user(loaded)
                active_user = loaded
        except Exception:
            pass
    if active_user and (active_user.get("self_introduction") or "").strip():
        return active_user["self_introduction"].strip()
    return None


def ask_question_payload(data: dict | None) -> tuple[dict, int]:
    """Manual ask endpoint payload handling."""
    data = data or {}
    original_question = (data.get("question") or "").strip()
    question = original_question
    db_only = bool(data.get("db_only", False))
    quick_mode = bool(data.get("quick_mode", False))
    _t0 = time.time()

    if not question:
        return {"error": "question is required"}, 400

    intro = _get_intro_answer(question)
    if intro:
        answer_storage.set_complete_answer(question, intro, {"source": "intro"})
        state.record_answer_latency((time.time() - _t0) * 1000)
        return {"answer": intro, "source": "intro"}, 200

    wants_code = False
    try:
        valid, cleaned, reason = validate_question(question)
        if not valid and reason not in ("incomplete", "too_short", "no_question_pattern"):
            return {"error": f"Question rejected: {reason}"}, 422
        if cleaned:
            question = cleaned
        wants_code = is_code_request(question)
    except Exception:
        pass

    question = normalize_manual_question(question)
    if not question:
        return {"error": "question is required"}, 400
    if question != original_question:
        wants_code = False

    active_user = state.get_selected_user()
    user_role = (active_user or {}).get("role", "") if active_user else ""
    import config as _cfg
    _role_tag = getattr(_cfg, "INTERVIEW_ROLE", "") or ""  # e.g. "python", "java", "sql"

    # Priority 1: DB match (instant, <5ms) — skipped in quick_mode (need guaranteed code block)
    db_result = None if quick_mode else qa_database.find_answer(question, want_code=wants_code, user_role=user_role, role_tag=_role_tag)
    if db_result:
        db_answer, db_score, db_id = db_result
        _ms = int((time.time() - _t0) * 1000)
        answer_storage.set_complete_answer(
            question,
            db_answer,
            {"source": f"db-{db_id}", "db_score": round(db_score, 2), "latency_ms": _ms},
        )
        state.record_answer_latency(_ms)
        return {
            "answer": db_answer,
            "source": "db",
            "score": db_score,
            "question": question,
            "original_question": original_question,
        }, 200

    # Priority 1b: Semantic search fallback (sentence-transformers cosine similarity)
    if not db_result and not quick_mode:
        try:
            import semantic_search as _sem
            sem_result = _sem.find_semantic_answer(question, want_code=wants_code)
            if sem_result:
                sem_answer, sem_score, sem_id = sem_result
                _ms = int((time.time() - _t0) * 1000)
                answer_storage.set_complete_answer(
                    question,
                    sem_answer,
                    {"source": f"semantic-{sem_id}", "sem_score": round(sem_score, 2), "latency_ms": _ms},
                )
                state.record_answer_latency(_ms)
                return {
                    "answer": sem_answer,
                    "source": "semantic",
                    "score": sem_score,
                    "question": question,
                    "original_question": original_question,
                }, 200
        except Exception:
            pass

    # Priority 2: Answer cache — skipped in quick_mode (need fresh code-block answer)
    import answer_cache as _ac
    cached = None if quick_mode else _ac.get_cached_answer(question, role=user_role)
    if cached:
        _ms = int((time.time() - _t0) * 1000)
        answer_storage.set_complete_answer(question, cached, {"source": "cache", "latency_ms": _ms})
        state.record_answer_latency(_ms)
        return {
            "answer": cached,
            "source": "cache",
            "question": question,
            "original_question": original_question,
        }, 200

    if db_only:
        return {
            "answer": "",
            "source": "db",
            "score": 0,
            "message": "No DB match - LLM disabled",
            "question": question,
            "original_question": original_question,
        }, 200

    def _run_llm(_t0=_t0) -> None:
        try:
            import answer_cache
            from user_manager import build_resume_context_for_llm

            if quick_mode:
                # Quick ask: short focused answer, no streaming, ~0.8s TTFT
                answer = llm_client.get_quick_answer(question)
                if answer:
                    _ms = int((time.time() - _t0) * 1000)
                    answer_storage.set_complete_answer(question, answer, {"source": "api-quick", "latency_ms": _ms})
                    answer_cache.cache_answer(question, answer, role=user_role)
                    state.record_answer_latency(_ms)
                return

            if wants_code:
                answer = llm_client.get_coding_answer(question)
            else:
                user_ctx = build_resume_context_for_llm()
                # Fallback: if no active user profile, inject raw resume + JD so LLM
                # still has persona context (avoids generic answers)
                resume_txt = "" if user_ctx else get_resume_text(UPLOADED_RESUME_PATH)
                jd_txt = "" if user_ctx else get_jd_text()
                # Stream chunks to UI in real-time via answer_storage
                raw_chunks = []
                gen = llm_client.get_streaming_interview_answer(
                    question, resume_txt, jd_txt, user_ctx
                )
                for chunk in gen:
                    if chunk:
                        raw_chunks.append(chunk)
                        answer_storage.append_answer_chunk(chunk)
                answer = llm_client.humanize_response("".join(raw_chunks))

            if answer:
                src_tag = "api-code" if wants_code else "api"
                _ms = int((time.time() - _t0) * 1000)
                answer_storage.set_complete_answer(question, answer, {"source": src_tag, "latency_ms": _ms})
                answer_cache.cache_answer(question, answer, role=user_role)
                state.record_answer_latency(_ms)
                try:
                    from main import _submit_for_learning
                    _submit_for_learning(question, answer, wants_code)
                except Exception:
                    pass
        except Exception as exc:
            dlog.log(f"[ask endpoint] LLM error: {exc}", "ERROR")

    threading.Thread(target=_run_llm, daemon=True).start()
    answer_storage.set_processing_question(question)
    return {
        "status": "generating",
        "source": "llm",
        "question": question,
        "original_question": original_question,
    }, 200


def stream_response() -> Response:
    """Create the hybrid SSE stream used by the dashboard."""
    import event_bus

    answers_file = Path.home() / ".drishi" / "current_answer.json"
    transcribing_file = Path.home() / ".drishi" / "transcribing.json"
    poll_interval = 0.015
    iq = event_bus.subscribe()

    def _read_file_answers():
        try:
            if not answers_file.exists():
                return None, []
            with open(answers_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data.get("session_id"), data.get("answers", [])
            return None, (data if isinstance(data, list) else [])
        except Exception:
            return None, []

    def event_stream():
        sent = {}
        sent_partial = {}
        last_file_mtime = 0.0
        last_tr_mtime = 0.0
        last_ping = time.time()

        try:
            # Tell browser to reconnect in 1s on disconnect (overrides JS exponential backoff)
            yield "retry: 1000\n\n"
            try:
                session_id, answers = _read_file_answers()
                yield f"event: init\ndata: {json.dumps({'session_id': session_id, 'answers': answers})}\n\n"
                for answer in answers:
                    if answer.get("question"):
                        key = answer["question"].strip().lower()
                        sent[key] = "complete" if answer.get("is_complete") else "thinking"
                try:
                    last_file_mtime = answers_file.stat().st_mtime
                except Exception:
                    pass
                transcribing = answer_storage.get_transcribing()
                if transcribing:
                    yield f"event: transcribing\ndata: {json.dumps({'text': transcribing})}\n\n"
            except Exception:
                yield 'event: init\ndata: {"session_id":null,"answers":[]}\n\n'

            while True:
                try:
                    msg = iq.get(timeout=poll_interval)
                    event_type, data = msg["t"], msg["d"]
                    yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
                    if event_type == "question" and data.get("question"):
                        sent[data["question"].strip().lower()] = "thinking"
                    elif event_type == "answer" and data.get("question"):
                        sent[data["question"].strip().lower()] = "complete"
                    drained = 1
                    while drained < 50:
                        try:
                            msg = iq.get_nowait()
                            event_type, data = msg["t"], msg["d"]
                            yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
                            if event_type == "question" and data.get("question"):
                                sent[data["question"].strip().lower()] = "thinking"
                            elif event_type == "answer" and data.get("question"):
                                sent[data["question"].strip().lower()] = "complete"
                            drained += 1
                        except _queue.Empty:
                            break
                except _queue.Empty:
                    pass

                now = time.time()

                try:
                    cur_mtime = answers_file.stat().st_mtime if answers_file.exists() else 0.0
                    if cur_mtime > last_file_mtime + 0.004:
                        last_file_mtime = cur_mtime
                        _sid, file_answers = _read_file_answers()
                        for ans in file_answers:
                            if not ans.get("question"):
                                continue
                            qk = ans["question"].strip().lower()
                            is_complete = bool(ans.get("is_complete"))
                            prev = sent.get(qk)
                            if prev is None:
                                if is_complete:
                                    sent[qk] = "complete"
                                    yield f"event: answer\ndata: {json.dumps(ans)}\n\n"
                                else:
                                    sent[qk] = "thinking"
                                    yield f"event: question\ndata: {json.dumps({'question': ans['question']})}\n\n"
                            elif prev == "thinking" and not is_complete:
                                cur_answer = ans.get("answer", "")
                                last_len = sent_partial.get(qk, 0)
                                if len(cur_answer) > last_len:
                                    new_chunk = cur_answer[last_len:]
                                    sent_partial[qk] = len(cur_answer)
                                    yield f"event: chunk\ndata: {json.dumps({'q': ans['question'], 'c': new_chunk})}\n\n"
                            elif prev == "thinking" and is_complete:
                                sent[qk] = "complete"
                                sent_partial.pop(qk, None)
                                yield f"event: answer\ndata: {json.dumps(ans)}\n\n"
                except Exception:
                    pass

                try:
                    tr_mtime = transcribing_file.stat().st_mtime if transcribing_file.exists() else 0.0
                    if tr_mtime > last_tr_mtime + 0.004:
                        last_tr_mtime = tr_mtime
                        with open(transcribing_file, "r", encoding="utf-8") as fh:
                            tr_data = json.load(fh)
                        yield f"event: transcribing\ndata: {json.dumps({'text': tr_data.get('text', '')})}\n\n"
                except Exception:
                    pass

                if now - last_ping >= 10:  # every 10s — keeps ngrok alive (was 20s)
                    last_ping = now
                    yield "event: ping\ndata: {}\n\n"
        except GeneratorExit:
            pass
        finally:
            event_bus.unsubscribe(iq)

    response = Response(event_stream(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


_EXPAND_STRIP_PREFIXES = ("explain ", "describe ", "tell me about ", "what about ", "talk about ")


def expand_short_keyword(text: str) -> str:
    """Expand short keywords into full questions using the Q&A database.

    Priority:
    1. Long enough to be a real question (>6 words) — pass through unchanged.
    2. Strip explain-style prefix and recurse once (e.g. 'explain decorators' → 'decorators').
    3. DB probe: if the DB already knows this keyword, pass it as-is for the DB to answer.
    4. Fallback: 'What is <keyword>? Explain with examples.'
    """
    stripped = text.strip()
    if not stripped:
        return stripped
    if len(stripped.split()) > 6:
        return stripped

    key = stripped.lower().rstrip("?.! ")

    for prefix in _EXPAND_STRIP_PREFIXES:
        if key.startswith(prefix):
            bare = key[len(prefix):].strip()
            if bare and bare != key:
                return expand_short_keyword(bare)
            break

    # If DB has an answer for this keyword, let it handle the full lookup
    try:
        if qa_database.find_answer(key, want_code=False):
            return stripped
    except Exception:
        pass

    if _HAS_QUESTION_RE.search(stripped) or stripped.endswith("?"):
        return stripped

    expanded = f"What is {stripped}? Explain with examples."
    print(f"[CC] Keyword expanded: '{stripped}' → '{expanded}'")
    return expanded


def cc_question_payload(data: dict | None) -> tuple[dict, int]:
    """Process a question captured from Meet/Teams captions or chat."""
    data = data or {}
    # Accept both 'question' and 'text' keys (extension sends 'question', UI paste sends 'text')
    question_text = (data.get("question") or data.get("text") or "").strip()
    if not question_text:
        return {"error": "No question provided"}, 400

    source = data.get("source", "cc")

    if (
        question_text == cc_capture_state["last_question"]
        and time.time() - cc_capture_state["last_timestamp"] < 5
    ):
        return {"status": "duplicate", "skipped": True}, 200

    cc_capture_state["last_question"] = question_text
    cc_capture_state["last_timestamp"] = time.time()

    is_chat_source = source in ("google-meet-chat", "teams-chat", "chat", "cc")
    print(f"\n[CC] Question from {source.upper()}: {question_text[:80]}...")

    question_text = expand_short_keyword(question_text)
    merged_text, was_merged = fragment_context.merge_with_context(question_text)
    if was_merged:
        print(f"[CC] Fragment merged: '{question_text[:40]}' -> '{merged_text[:60]}'")
        question_text = merged_text

    is_valid, cleaned_question, rejection_reason = validate_question(question_text)
    if not is_valid:
        print(f"[CC] Question rejected: {rejection_reason}")
        return {
            "status": "rejected",
            "reason": rejection_reason,
            "original": question_text[:50],
        }, 200

    question_text = cleaned_question
    print(f"[CC] Question validated: {question_text[:60]}...")

    if is_chat_source:
        append_chat_question(question_text, source, time.time(), "answered")

    from user_manager import get_active_user_context

    # Intro detection — skip dedup so user can switch profiles mid-session
    intro = _get_intro_answer(question_text)
    if intro:
        answer_storage.set_complete_answer(question_text, intro, {"source": "intro"})
        fragment_context.save_context(question_text, f"chat-{source}")
        return {"status": "answered", "question": question_text[:50], "answer": intro, "source": "intro"}, 200

    # Skip dedup check only for intro; everything else deduplicates
    existing = answer_storage.is_already_answered(question_text)
    if existing:
        print(f"[CC] Already answered, showing existing: {question_text[:40]}...")
        return {
            "status": "already_answered",
            "question": question_text[:50],
            "answer_preview": existing.get("answer", "")[:100],
        }, 200

    resume_summary, _user_role, jd_from_user = get_active_user_context()
    resume_text = resume_summary or get_resume_text(UPLOADED_RESUME_PATH)
    jd_text = jd_from_user or get_jd_text()

    try:
        wants_code = is_code_request(question_text)
        if source == "chat" and not wants_code:
            q_lower = question_text.lower().strip()
            theory_starters = [
                "what is", "what are", "what was", "what does", "explain", "describe",
                "difference between", "why", "when would", "how does", "how do",
                "tell me about", "can you explain",
            ]
            infra_indicators = [
                "ansible", "terraform", "playbook", "pipeline", "dockerfile", "jenkinsfile",
                "yaml", "manifest", "bash script", "shell script", "helm chart",
                "kubernetes manifest", "k8s manifest",
            ]
            is_theory = any(q_lower.startswith(ind) for ind in theory_starters)
            is_infra = any(ind in q_lower for ind in infra_indicators)
            if is_infra:
                wants_code = True
                print("[CC] Infra/script question -> coding mode")
            elif not is_theory:
                wants_code = True
                print("[CC] Chat question -> treating as coding request")

        _active = state.get_selected_user() or {}
        _role_tag = getattr(config, "INTERVIEW_ROLE", "") or ""
        db_result = qa_database.find_answer(
            question_text, want_code=wants_code,
            user_role=_active.get("role", ""),
            role_tag=_role_tag,
        )
        if db_result:
            answer, score, qa_id = db_result
            print(f"[CC] DB hit (score={score:.2f}, id={qa_id}) - skipping API call")
            source_label = f"db-{source}"
            answer_storage.set_complete_answer(
                question_text=question_text,
                answer_text=answer,
                metrics={"source": source_label},
            )
            fragment_context.save_context(question_text, f"chat-{source}")
            return {
                "status": "answered",
                "question": question_text[:50],
                "answer": answer,
                "answer_length": len(answer),
                "source": source_label,
            }, 200

        source_label = f"cc-{source}"
        answer_storage.set_processing_question(question_text)

        def _stream_answer_bg(q=question_text, wc=wants_code, res=resume_text, jd=jd_text, sl=source_label):
            try:
                from user_manager import build_resume_context_for_llm

                if wc:
                    print("[CC] Code request - calling LLM (bg)")
                    answer = llm_client.get_coding_answer(q)
                    answer_storage.set_complete_answer(q, answer, {"source": sl})
                else:
                    print("[CC] Theory question - streaming LLM (bg)")
                    user_ctx = build_resume_context_for_llm()
                    raw_chunks = []
                    for chunk in llm_client.get_streaming_interview_answer(q, res, jd, user_ctx):
                        raw_chunks.append(chunk)
                        answer_storage.append_answer_chunk(chunk)
                    answer = llm_client.humanize_response("".join(raw_chunks))
                    answer_storage.set_complete_answer(q, answer, {"source": sl})
                try:
                    qa_database.save_interview_qa(q, answer)
                except Exception:
                    pass
                fragment_context.save_context(q, f"chat-{sl}")
                print(f"[CC] BG answer ready ({len(answer)} chars)")
            except Exception as exc:
                print(f"[CC] BG stream error: {exc}")

        threading.Thread(target=_stream_answer_bg, daemon=True).start()
        return {
            "status": "processing",
            "question": question_text[:50],
            "source": source_label,
        }, 202
    except Exception as exc:
        print(f"[CC] LLM error: {exc}")
        return {"error": str(exc)}, 500


# ── Interview Tips ──────────────────────────────────────────────────────────────

_GENERAL_TIPS = [
    "Think out loud — interviewers value your reasoning, not just the answer.",
    "Use concrete examples from your resume — 'I used this in my last role to...'",
    "Cover edge cases first: empty input, single element, max value.",
    "If you don't know something, say what you DO know and how you'd find out.",
    "DB answers arrive in <30ms — listen for the question keyword and relax.",
    "Quantify your impact: 'reduced latency by 40%' beats 'improved performance'.",
]

_TIPS_BY_ROLE = {
    "python": [
        "Mention GIL limitations for CPU-bound tasks — use multiprocessing instead.",
        "Know the difference between list/dict/set comprehensions and their performance.",
        "Be ready for Django ORM N+1 problem and select_related/prefetch_related fix.",
        "Decorator and context manager patterns are almost always asked.",
    ],
    "java": [
        "Understand HashMap internals: hashCode, equals, load factor, tree buckets (Java 8+).",
        "Be ready for ConcurrentHashMap vs synchronized HashMap comparison.",
        "Spring Boot auto-configuration and @Bean vs @Component questions are common.",
        "Know checked vs unchecked exceptions and when to use each.",
    ],
    "javascript": [
        "Event loop, microtask queue, and Promise execution order are classic traps.",
        "Explain 'this' binding — arrow functions vs regular functions.",
        "React reconciliation and when to use useMemo/useCallback vs just re-render.",
        "Know closure gotchas in loops (var vs let).",
    ],
    "sql": [
        "Window functions (ROW_NUMBER, RANK, LAG/LEAD) are almost always tested.",
        "Explain query execution plan and when an index scan vs seek is used.",
        "Know ACID properties and isolation levels (READ COMMITTED vs SERIALIZABLE).",
        "Be ready to optimize a slow query: check indexes, avoid SELECT *, use CTEs.",
    ],
    "production_support": [
        "Always start incident response: check logs → metrics → recent deployments.",
        "Know `top`, `htop`, `iotop`, `vmstat`, `netstat -tulpn` from memory.",
        "OOM kill: `dmesg | grep -i 'killed process'` is your first command.",
        "For disk full: `df -h`, then `du -sh /var/log/* | sort -h | tail -20`.",
    ],
    "telecom": [
        "SIP REGISTER → 401 Unauthorized → REGISTER with auth → 200 OK flow.",
        "Know P-CSCF, S-CSCF, I-CSCF roles in IMS architecture.",
        "Diameter AVPs: Origin-Host, Destination-Realm, Session-Id are always checked.",
        "For call drops: Wireshark filter `sip.Method == 'BYE' || sip.Status-Code == 503`.",
    ],
}


def get_interview_tips_payload(role: str = "") -> dict:
    """Return contextual interview tips for the current role."""
    import config as _cfg

    _role = (role or getattr(_cfg, "INTERVIEW_ROLE", "general") or "general").lower()
    role_tips = _TIPS_BY_ROLE.get(_role, [])
    tips = _GENERAL_TIPS + role_tips

    return {
        "role": _role,
        "tips": tips,
        "total": len(tips),
    }


# ── Prep Questions ──────────────────────────────────────────────────────────────

def get_prep_questions_payload(role: str = "", tag: str = "", limit: int = 20) -> dict:
    """Return top questions from the DB for the current role — for pre-interview prep."""
    import config as _cfg

    _role = (role or getattr(_cfg, "INTERVIEW_ROLE", "general") or "general").lower()
    _tag = tag or _role

    try:
        rows = qa_database.get_all_qa(search="", tag=_tag)
        rows.sort(key=lambda r: r.get("hit_count", 0), reverse=True)
        questions = [
            {
                "id": r.get("id"),
                "question": r.get("question", ""),
                "tags": r.get("tags", ""),
                "has_code": bool(r.get("answer_code")),
                "hit_count": r.get("hit_count", 0),
            }
            for r in rows[:limit]
            if r.get("question")
        ]
    except Exception:
        questions = []

    return {
        "role": _role,
        "tag_filter": _tag,
        "count": len(questions),
        "questions": questions,
    }
