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


def normalize_manual_question(raw_question: str) -> str:
    """Expand short keyword-style manual asks into full questions."""
    question = (raw_question or "").strip()
    if not question:
        return ""

    words = question.split()
    has_question_word = re.search(
        r"\b(what|how|why|when|where|which|who|whom|whose|explain|describe|write|"
        r"define|tell|can|could|is|are|does|do|did)\b",
        question.lower(),
    )
    if len(words) <= 3 and not has_question_word and not question.endswith("?"):
        return f"What is {question}? Explain in detail with examples."
    return question


def ask_question_payload(data: dict | None) -> tuple[dict, int]:
    """Manual ask endpoint payload handling."""
    data = data or {}
    original_question = (data.get("question") or "").strip()
    question = original_question
    db_only = bool(data.get("db_only", False))
    _t0 = time.time()

    if not question:
        return {"error": "question is required"}, 400

    from user_manager import is_introduction_question

    if is_introduction_question(question):
        active_user = state.get_selected_user()
        if active_user and (active_user.get("self_introduction") or "").strip():
            intro = active_user["self_introduction"].strip()
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

    # Priority 1: DB match (instant, <5ms)
    db_result = qa_database.find_answer(question, want_code=wants_code, user_role=user_role)
    if db_result:
        db_answer, db_score, db_id = db_result
        answer_storage.set_complete_answer(
            question,
            db_answer,
            {"source": f"db-{db_id}", "db_score": round(db_score, 2)},
        )
        state.record_answer_latency((time.time() - _t0) * 1000)
        return {
            "answer": db_answer,
            "source": "db",
            "score": db_score,
            "question": question,
            "original_question": original_question,
        }, 200

    # Priority 2: Answer cache (LLM-generated answers from previous calls, <1ms)
    import answer_cache as _ac
    cached = _ac.get_cached_answer(question)
    if cached:
        answer_storage.set_complete_answer(question, cached, {"source": "cache"})
        state.record_answer_latency((time.time() - _t0) * 1000)
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

            if wants_code:
                answer = llm_client.get_coding_answer(question)
            else:
                user_ctx = build_resume_context_for_llm()
                # Stream chunks to UI in real-time via answer_storage
                raw_chunks = []
                gen = (
                    llm_client.get_streaming_interview_answer(question, "", "", user_ctx)
                    if user_ctx
                    else llm_client.get_streaming_interview_answer(
                        question, get_resume_text(UPLOADED_RESUME_PATH), get_jd_text()
                    )
                )
                for chunk in gen:
                    if chunk:
                        raw_chunks.append(chunk)
                        answer_storage.append_answer_chunk(chunk)
                answer = llm_client.humanize_response("".join(raw_chunks))

            if answer:
                src_tag = "api-code" if wants_code else "api"
                answer_storage.set_complete_answer(question, answer, {"source": src_tag})
                answer_cache.cache_answer(question, answer)
                state.record_answer_latency((time.time() - _t0) * 1000)
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

                if now - last_ping >= 20:
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


_KEYWORD_EXPAND = {
    "encapsulation": "What is encapsulation?",
    "polymorphism": "What is polymorphism?",
    "inheritance": "What is inheritance?",
    "abstraction": "What is abstraction?",
    "oops": "What are the four pillars of OOP?",
    "oop": "What are the four pillars of OOP?",
    "oops concepts": "What are the four pillars of OOP?",
    "solid": "What are SOLID principles?",
    "solid principles": "What are SOLID principles?",
    "generators": "What are generators in Python?",
    "generator": "What are generators in Python?",
    "decorators": "What are decorators in Python?",
    "decorator": "What are decorators in Python?",
    "metaclass": "What is a metaclass in Python?",
    "gil": "What is the GIL in Python?",
    "global interpreter lock": "What is the GIL in Python?",
    "list comprehension": "What is list comprehension in Python?",
    "lambda": "What is a lambda function in Python?",
    "mutable immutable": "What is the difference between mutable and immutable in Python?",
    "args kwargs": "What are *args and **kwargs in Python?",
    "*args **kwargs": "What are *args and **kwargs in Python?",
    "pickling": "What is pickling in Python?",
    "shallow deep copy": "What is the difference between shallow copy and deep copy?",
    "iterator": "What is an iterator in Python?",
    "context manager": "What is a context manager in Python?",
    "palindrome": "Write a function to check if a string is a palindrome.",
    "fibonacci": "Write a function to generate Fibonacci numbers.",
    "fibonacci series": "Write a function to generate the Fibonacci series.",
    "factorial": "Write a function to calculate factorial of a number.",
    "even numbers": "Write a function to find all even numbers in a list.",
    "odd numbers": "Write a function to find all odd numbers in a list.",
    "prime numbers": "Write a function to find all prime numbers up to N.",
    "prime": "Write a function to check if a number is prime.",
    "anagram": "Write a function to check if two strings are anagrams.",
    "reverse string": "Write a function to reverse a string.",
    "bubble sort": "Write a bubble sort algorithm.",
    "merge sort": "Write a merge sort algorithm.",
    "binary search": "Write a binary search algorithm.",
    "linked list": "Write a singly linked list implementation.",
    "stack": "Write a stack implementation in Python.",
    "queue": "Write a queue implementation in Python.",
    "orm": "What is Django ORM?",
    "django orm": "What is Django ORM?",
    "migrations": "What are Django migrations?",
    "django migrations": "What are Django migrations?",
    "signals": "What are Django signals?",
    "django signals": "What are Django signals?",
    "middleware": "What is Django middleware?",
    "django middleware": "What is Django middleware?",
    "rest framework": "What is Django REST Framework?",
    "drf": "What is Django REST Framework?",
    "serializer": "What are serializers in DRF?",
    "viewsets": "What are ViewSets in DRF?",
    "authentication": "What are authentication methods in Django?",
    "jwt": "What is JWT authentication?",
    "celery": "What is Celery and how is it used with Django?",
    "docker": "What is Docker and how does it work?",
    "kubernetes": "What is Kubernetes?",
    "k8s": "What is Kubernetes?",
    "terraform": "What is Terraform?",
    "ansible": "What is Ansible?",
    "ci cd": "What is CI/CD?",
    "cicd": "What is CI/CD?",
    "jenkins": "What is Jenkins?",
    "nginx": "What is Nginx?",
    "load balancer": "What is a load balancer?",
    "load balancing": "What is load balancing?",
    "microservices": "What are microservices?",
    "kafka": "What is Apache Kafka?",
    "redis": "What is Redis?",
    "aws": "What are the core AWS services?",
    "s3": "What is AWS S3?",
    "ec2": "What is AWS EC2?",
    "lambda function": "What is AWS Lambda?",
    "terraform script": "Write a basic Terraform configuration to create an EC2 instance.",
    "ansible script": "Write an Ansible playbook to install and start Nginx.",
    "ansible playbook": "Write an Ansible playbook to install and start Nginx.",
    "dockerfile": "Write a Dockerfile for a Python Flask application.",
    "docker compose": "Write a Docker Compose file for a web app with a database.",
    "sql": "What is SQL and what are its key commands?",
    "nosql": "What is NoSQL and how does it differ from SQL?",
    "sql nosql": "What is the difference between SQL and NoSQL databases?",
    "indexing": "What is database indexing?",
    "caching": "What is caching and how does it improve performance?",
    "rest api": "What is a REST API?",
    "restful": "What is a RESTful API?",
    "http methods": "What are HTTP methods?",
    "status codes": "What are common HTTP status codes?",
    "git": "What is Git and what are its core commands?",
    "git merge rebase": "What is the difference between git merge and git rebase?",
    "threading": "What is multithreading in Python?",
    "multiprocessing": "What is multiprocessing in Python?",
    "async await": "What is async/await in Python?",
    "cors": "What is CORS?",
}


def expand_short_keyword(text: str) -> str:
    """Expand short captured keywords into interview-ready questions."""
    stripped = text.strip()
    if len(stripped.split()) > 6:
        return stripped
    key = stripped.lower().rstrip("?.! ")
    expanded = _KEYWORD_EXPAND.get(key)
    if expanded:
        print(f"[CC] Keyword expanded: '{stripped}' -> '{expanded}'")
        return expanded
    return stripped


def cc_question_payload(data: dict | None) -> tuple[dict, int]:
    """Process a question captured from Meet/Teams captions or chat."""
    data = data or {}
    if "question" not in data:
        return {"error": "No question provided"}, 400

    question_text = data.get("question", "").strip()
    source = data.get("source", "cc")

    if not question_text:
        return {"error": "Empty question"}, 400

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

    existing = answer_storage.is_already_answered(question_text)
    if existing:
        print(f"[CC] Already answered, showing existing: {question_text[:40]}...")
        return {
            "status": "already_answered",
            "question": question_text[:50],
            "answer_preview": existing.get("answer", "")[:100],
        }, 200

    from user_manager import get_active_user_context, is_introduction_question

    if is_introduction_question(question_text):
        active_user = state.get_selected_user()
        if active_user and (active_user.get("self_introduction") or "").strip():
            intro = active_user["self_introduction"].strip()
            answer_storage.set_complete_answer(question_text, intro, {"source": "intro"})
            fragment_context.save_context(question_text, f"chat-{source}")
            return {
                "status": "answered",
                "question": question_text[:50],
                "answer": intro,
                "source": "intro",
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

        db_result = qa_database.find_answer(question_text, want_code=wants_code,
                                             user_role=(state.get_selected_user() or {}).get("role", ""))
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
