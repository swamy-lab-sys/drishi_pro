#!/usr/bin/env python3
"""
Drishi Enterprise - Production Pipeline

INTERVIEW-ONLY MODE for real interviews (YouTube/Zoom/Meet/Teams).

DESIGN PRINCIPLES:
1. NEVER block or modify system microphone
2. Accept any system playback audio
3. Hard question boundaries (no overlap)
4. Fast responses (< 4s target)
5. Graceful degradation on all errors
6. Silence > wrong answer

PIPELINE ORDER:
Audio -> VAD -> ASR -> Validate -> LOCK -> Answer -> UI -> UNLOCK -> Cooldown
"""

import os
import sys
import time
import signal
import subprocess
import warnings

# Suppress all warnings for pure output
warnings.filterwarnings("ignore")
os.environ["PYTHONWARNINGS"] = "ignore"

import state
from state import PipelineState
import config
import answer_cache
import qa_database
from app.core.product import PRODUCT_NAME, product_banner
from llm_client import (
    get_interview_answer,
    get_coding_answer,
    get_streaming_interview_answer,
    correct_question_intent,
    clear_session,
    humanize_response,
)
from app.services.settings_service import get_server_ip
from app.services.user_service import _load_active_user_from_file
from resume_loader import load_resume, load_job_description
from user_manager import is_introduction_question, build_resume_context_for_llm
from stt import transcribe, load_model as load_stt_model
import output_manager
import answer_storage
import fragment_context
from question_validator import clean_and_validate, is_code_request, split_merged_questions, _is_whisper_hallucination
import performance_logger
import threading
import queue

# Debug logging
import debug_logger as dlog
import event_bus

# =============================================================================
# AUTO-LEARN BACKGROUND WORKER
# =============================================================================

# Queue for async DB learning (never blocks main pipeline)
_learn_queue: queue.Queue = queue.Queue(maxsize=20)

def _auto_learn_worker():
    """
    Background daemon thread: validates new Q&A pairs via LLM and stores to DB.
    Runs entirely outside the main pipeline so it never slows user responses.
    """
    from llm_client import generate_qa_payload
    while True:
        try:
            item = _learn_queue.get(timeout=5.0)
        except queue.Empty:
            continue
        try:
            question, answer, wants_code = item
            payload = generate_qa_payload(question, answer, wants_code)
            if not payload or not payload.get("valid"):
                reason = (payload or {}).get("reason", "filtered")
                dlog.log(f"[AutoLearn] Rejected: {reason}", "DEBUG")
                continue
            # Store with structured tags + keywords
            kw_str  = ",".join(payload.get("keywords", []))
            tag_str = ",".join(payload.get("tags", []))
            code    = payload.get("code", "")
            clean_q = payload.get("question", question)
            clean_a = payload.get("answer", answer)

            qa_type = "both" if code else ("coding" if wants_code else "theory")
            all_tags = "auto," + tag_str if tag_str else "auto"
            row_id = qa_database.add_qa(
                question=clean_q,
                answer_theory=clean_a if not wants_code or code else "",
                answer_coding=code if code else (clean_a if wants_code else ""),
                qa_type=qa_type,
                keywords=kw_str,
                tags=all_tags,
            )

            if row_id and row_id > 0:
                print(f"\n{'─'*50}")
                print(f"[DB] NEW QUESTION SAVED  (id={row_id})")
                print(f"   Question : {clean_q}")
                print(f"   Type     : {qa_type}")
                print(f"   Tags     : {all_tags}")
                print(f"   Keywords : {kw_str or '(none)'}")
                print(f"{'─'*50}\n")
                dlog.log(f"[AutoLearn] Stored id={row_id}: {clean_q[:60]}", "INFO")
        except Exception as e:
            dlog.log_error("[AutoLearn] Worker error", e)
        finally:
            _learn_queue.task_done()


def _submit_for_learning(question: str, answer: str, wants_code: bool = False):
    """Submit a Q&A pair to the async auto-learn queue (non-blocking)."""
    try:
        _learn_queue.put_nowait((question, answer, wants_code))
    except queue.Full:
        dlog.log("[AutoLearn] Queue full, skipping", "WARN")


# Start the background worker once on import
_learn_thread = threading.Thread(target=_auto_learn_worker, daemon=True, name="auto-learn")
_learn_thread.start()

# Thread-safe queue for audio segments (bounded: ~1.5s of audio max backlog)
audio_queue = queue.Queue(maxsize=50)


# =============================================================================
# CONFIGURATION (Synced with config.py)
# =============================================================================

MAX_QUESTION_DURATION = config.MAX_RECORDING_DURATION
MIN_AUDIO_DURATION = config.MIN_AUDIO_LENGTH
VAD_AGGRESSIVENESS = config.VAD_AGGRESSIVENESS


# =============================================================================
# GLOBAL STATE
# =============================================================================

resume = ""
job_description = ""
should_exit = False


def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully."""
    global should_exit
    # print("\n\nShutting down...")
    should_exit = True
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)


# =============================================================================
# RESUME LOADING
# =============================================================================

_last_resume_check = 0
_UPLOADED_RESUME_PATH = os.path.expanduser("~/.drishi/uploaded_resume.txt")

def load_resume_context():
    """Load resume ONLY if uploaded via UI. Never load built-in resume.txt."""
    global resume, _last_resume_check
    now = time.time()
    if resume and now - _last_resume_check < _CONTEXT_CHECK_INTERVAL:
        return True
    _last_resume_check = now
    try:
        if os.path.exists(_UPLOADED_RESUME_PATH):
            resume = load_resume(_UPLOADED_RESUME_PATH)
            return bool(resume.strip())
        resume = ""
        return False
    except Exception:
        resume = ""
        return False


_jd_mtime = 0
_last_context_check = 0
_CONTEXT_CHECK_INTERVAL = 30.0  # Only check file changes every 30s

def load_jd_context():
    """Load job description from file (cached, checks mtime at most every 30s)."""
    global job_description, _jd_mtime, _last_context_check
    now = time.time()
    if now - _last_context_check < _CONTEXT_CHECK_INTERVAL:
        return bool(job_description)
    _last_context_check = now
    try:
        jd_path = config.JD_PATH
        if os.path.exists(jd_path):
            mtime = os.path.getmtime(jd_path)
            if mtime != _jd_mtime:
                with open(jd_path, 'r') as f:
                    job_description = f.read()
                _jd_mtime = mtime
            return True
    except Exception:
        pass
    return False


# =============================================================================
# CLARIFICATION INTERCEPT
# =============================================================================

import re as _re

_CLARIFY_PATTERNS = [
    # "I am/was asking about X"
    _re.compile(r"^i\s+(?:am|was|were)\s+asking\s+(?:about|for|regarding)\s+(.+)", _re.I),
    # "I meant X" / "I mean X"
    _re.compile(r"^i\s+meant?\s+(.+)", _re.I),
    # "I'm asking about X"
    _re.compile(r"^i'm\s+asking\s+(?:about|for)\s+(.+)", _re.I),
    # "Actually I want to ask about X"
    _re.compile(r"^actually\s+(?:i\s+)?(?:want|wanted)\s+to\s+ask\s+(?:about\s+)?(.+)", _re.I),
    # "The question is about X"
    _re.compile(r"^the\s+question\s+is\s+(?:about|regarding)\s+(.+)", _re.I),
    # "It's about X" / "This is about X"
    _re.compile(r"^(?:it'?s|this\s+is)\s+about\s+(.+)", _re.I),
    # "I said X" — rare but useful
    _re.compile(r"^i\s+said\s+(.+)", _re.I),
]
_CLARIFY_STRIP = _re.compile(r"[.!?]+$")


def _extract_clarification(text: str):
    """
    Detect candidate clarifications like 'I am asking about decorators.'
    Returns the corrected topic string, or None if not a clarification.
    """
    t = text.strip()
    for pat in _CLARIFY_PATTERNS:
        m = pat.match(t)
        if m:
            topic = _CLARIFY_STRIP.sub("", m.group(1).strip())
            # Reject if too short or looks like noise
            if len(topic.split()) >= 1 and len(topic) >= 3:
                # Prefix with "What is" only if topic looks like a bare noun phrase
                # (no question word, no verb at start)
                lower = topic.lower()
                has_q = any(lower.startswith(w) for w in
                            ('what', 'how', 'why', 'when', 'where', 'explain', 'tell', 'define'))
                if not has_q:
                    topic = f"What is {topic}?"
                return topic
    return None


# =============================================================================
# QUESTION HANDLER (SINGLE-SHOT MODE)
# =============================================================================

def handle_question(question_text: str) -> bool:
    """
    Process a validated interview question.

    PIPELINE:
    1. FIRST GATE: Check if blocked
    2. Check cache
    3. Acquire lock (LOGGING ONLY AFTER LOCK)
    4. Generate answer (single-shot, 10s timeout)
    5. Cache + UI update
    6. Release lock + adaptive cooldown

    Returns:
        bool: True if answered
    """
    dlog.start_request()
    dlog.log(f"Processing question: {question_text}", "INFO")

    # Dynamic context reload
    load_jd_context()
    load_resume_context()

    # Step 1: FIRST GATE - check before any work
    if state.should_block_input():
        dlog.log("Blocked by state gate", "WARN")
        return False

    # Step 1b: Introduction question shortcut — return stored self_introduction instantly
    # No DB lookup or LLM call needed. Pure instant response.
    if is_introduction_question(question_text):
        _active_user = state.get_selected_user()
        # Cross-process fallback: web/server.py writes active_user.json when user is
        # selected from the UI. Read it here so main.py picks up runtime user changes.
        if not _active_user or not ((_active_user.get('self_introduction') or '').strip()):
            try:
                _file_user = _load_active_user_from_file()
                if _file_user:
                    state.set_selected_user(_file_user)
                    _active_user = _file_user
            except Exception:
                pass
        if _active_user and (_active_user.get('self_introduction') or '').strip():
            _intro = _active_user['self_introduction'].strip()
            print(f"[INTRO] Returning stored intro for {_active_user.get('name', 'user')}")
            dlog.log(f"[INTRO] Introduction question detected — using stored self_introduction", "INFO")
            if state.should_block_input():
                return False
            state.start_generation()
            try:
                state.mark_llm_start()
                state.mark_llm_end()
                state.mark_ui_start()
                output_manager.write_header(question_text)
                output_manager.write_answer_chunk(_intro)
                output_manager.write_footer()
                state.mark_ui_end()
                _metrics = state.finalize_metrics() or {}
                _metrics['source'] = 'intro'
                answer_storage.set_complete_answer(question_text, _intro, _metrics)
                answer_cache.cache_answer(question_text, _intro)
                dlog.end_request(question_text, len(_intro))
                return True
            finally:
                state.stop_generation()
                state.start_cooldown(answer_length=len(_intro))
                state.set_last_question(question_text)

    answer = ""
    wants_code = False

    # Step 2: Check cache
    cache_start = time.time()
    cached_answer = answer_cache.get_cached_answer(question_text)
    cache_time = time.time() - cache_start

    if cached_answer is not None:
        dlog.log_cache_hit(question_text)
        dlog.log_timing("cache_lookup", cache_time, "HIT")

        if state.should_block_input():
            return False

        state.start_generation()
        try:
            state.mark_llm_start()
            state.mark_llm_end()

            ui_start = time.time()
            state.mark_ui_start()
            output_manager.write_header(question_text)
            output_manager.write_answer_chunk(cached_answer)
            output_manager.write_footer()
            state.mark_ui_end()
            dlog.log_ui_update(time.time() - ui_start, "cached_answer")

            metrics = state.finalize_metrics()
            answer_storage.set_complete_answer(question_text, cached_answer, metrics)

            answer = cached_answer
            dlog.end_request(question_text, len(answer))
            return True
        finally:
            # ALWAYS release lock
            state.stop_generation()
            state.start_cooldown(answer_length=len(answer), is_code='```' in answer)
            state.set_last_question(question_text)

    # Step 3: Acquire lock FIRST
    dlog.log_cache_miss(question_text)
    dlog.log_timing("cache_lookup", cache_time, "MISS")

    if state.should_block_input():
        dlog.log("Blocked by state gate (after cache)", "WARN")
        return False

    state.start_generation()
    dlog.log_state_change("IDLE", "GENERATING")

    try:
        output_manager.write_header(question_text)

        wants_code = is_code_request(question_text)
        dlog.log(f"Code request: {wants_code}", "DEBUG")

        # Notify UI we are processing (Thinking state)
        answer_storage.set_processing_question(question_text)

        # Step 3b: Check Q&A database BEFORE calling LLM
        _active_user = state.get_selected_user()
        # Cross-process sync: if web UI changed the user, pick it up from shared file
        if not _active_user:
            try:
                _fu = _load_active_user_from_file()
                if _fu:
                    state.set_selected_user(_fu)
                    _active_user = _fu
            except Exception:
                pass
        _user_role = (_active_user or {}).get("role", "") if _active_user else ""
        _db_t0 = time.time()
        db_result = qa_database.find_answer(question_text, want_code=wants_code, user_role=_user_role)
        _db_ms = (time.time() - _db_t0) * 1000
        if db_result:
            db_answer, db_score, db_id = db_result
            print(f"[PERF] DB lookup:  {_db_ms:.0f}ms → HIT (score={db_score:.2f}, id={db_id})")
            dlog.log(f"DB hit score={db_score:.2f} id={db_id}", "INFO")
            state.mark_llm_start()
            state.mark_llm_end()
            state.mark_ui_start()
            output_manager.write_answer_chunk(db_answer)
            output_manager.write_footer()
            state.mark_ui_end()
            metrics = state.finalize_metrics()
            if metrics is None:
                metrics = {}
            metrics['source'] = f'db-{db_id}'
            metrics['db_score'] = round(db_score, 2)
            answer_storage.set_complete_answer(question_text, db_answer, metrics)
            answer_cache.cache_answer(question_text, db_answer)
            dlog.end_request(question_text, len(db_answer))
            return True

        # Step 3c: DB miss — try intent correction before calling LLM.
        # This catches cases where STT produced a recognizable but garbled term
        # that wasn't caught by the static STT_CORRECTIONS map.
        print(f"[PERF] DB lookup:  {_db_ms:.0f}ms → MISS → calling LLM")
        from question_validator import _has_tech_term as _htc
        _has_tech = _htc(question_text.lower())
        _corrected_q = question_text
        # Skip intent correction if question is long enough to be well-formed (≥8 words)
        # or has a tech term — saves ~500ms LLM round-trip on valid DB misses
        _q_word_count = len(question_text.split())
        if not _has_tech and _q_word_count < 8:
            # Skip intent correction if a tech term was already detected — trust it's valid
            # and go straight to LLM (~500ms saved per tech DB-miss)
            try:
                _corrected = correct_question_intent(question_text)
                if _corrected and _corrected.lower() != question_text.lower():
                    print(f"[INTENT] Corrected: '{question_text}' → '{_corrected}'")
                    dlog.log(f"[INTENT] '{question_text}' → '{_corrected}'", "INFO")
                    # Auto-learn: store this correction so future STT catches it without LLM
                    try:
                        import stt_learner as _sl
                        _sl.submit_correction(question_text, _corrected)
                    except Exception:
                        pass
                    _corrected_q = _corrected
                    # Re-check DB with corrected question
                    _db2 = qa_database.find_answer(_corrected_q, want_code=wants_code, user_role=_user_role)
                    if _db2:
                        db_answer, db_score, db_id = _db2
                        print(f"[DB] Hit after correction (score={db_score:.2f}, id={db_id})")
                        state.mark_llm_start()
                        state.mark_llm_end()
                        state.mark_ui_start()
                        output_manager.write_answer_chunk(db_answer)
                        output_manager.write_footer()
                        state.mark_ui_end()
                        metrics = state.finalize_metrics() or {}
                        metrics['source'] = f'db-{db_id}'
                        metrics['db_score'] = round(db_score, 2)
                        answer_storage.set_complete_answer(_corrected_q, db_answer, metrics)
                        answer_cache.cache_answer(question_text, db_answer)
                        # Update question display to show the corrected version
                        answer_cache.cache_answer(_corrected_q, db_answer)
                        dlog.end_request(_corrected_q, len(db_answer))
                        return True
                    
                    # Use corrected question for LLM call
                    question_text = _corrected_q
                    # Update placeholder card so its key matches the corrected question
                    answer_storage.update_current_question(_corrected_q)
            except Exception as _ie:
                dlog.log_error("[INTENT] correction error", _ie)

        # Step 4: Generate answer (SINGLE-SHOT, 10s timeout handled by llm_client)
        state.mark_llm_start()
        llm_start = time.time()
        dlog.log_llm_start(question_text)

        if wants_code:
            # Coding questions: single-shot for clean code blocks
            dlog.log("Using single-shot mode for code", "DEBUG")
            answer = get_coding_answer(question_text)
            llm_time = (time.time() - llm_start) * 1000
            print(f"[PERF] LLM Generation (Code): {llm_time:.0f}ms")

            answer_storage.set_complete_answer(question_text, answer, {'source': 'api-code'})
            output_manager.write_answer_chunk(answer)
            dlog.log_llm_complete(time.time() - llm_start, len(answer), False)
        else:
            # Interview answers: STREAM chunks to UI so user sees bullets appear live
            dlog.log("Using streaming mode for interview", "DEBUG")
            state.mark_ui_start()
            raw_chunks = []
            _user_ctx = build_resume_context_for_llm()
            for chunk in get_streaming_interview_answer(question_text, resume, job_description, _user_ctx):
                raw_chunks.append(chunk)
                answer_storage.append_answer_chunk(chunk)
            # Humanize the full streamed text and finalize
            answer = humanize_response("".join(raw_chunks))
            llm_time = (time.time() - llm_start) * 1000
            print(f"[PERF] LLM Stream: {llm_time:.0f}ms")
            output_manager.write_answer_chunk(answer)
            dlog.log_llm_complete(time.time() - llm_start, len(answer), False)

        state.mark_llm_end()

        # Step 5: Cache answer and trigger background learning
        # Always cache and submit to learning; let the validator decide if it's a quality pair.
        answer_cache.cache_answer(question_text, answer)
        dlog.log("Answer cached", "DEBUG")
        
        # Auto-learn: submit to background worker for LLM validation + DB storage.
        # Fully async — never blocks the main pipeline.
        _submit_for_learning(question_text, answer, wants_code)

        # Step 6: Finalize UI
        ui_start = time.time()
        output_manager.write_footer()
        state.mark_ui_end()

        metrics = state.finalize_metrics()
        if metrics is None:
            metrics = {}
        metrics['source'] = 'api'
        answer_storage.set_complete_answer(question_text, answer, metrics)
        dlog.log_ui_update(time.time() - ui_start, "finalize")

        # Log performance
        if metrics:
            perf_summary = performance_logger.get_console_summary(metrics)
            dlog.log(f"Performance: {perf_summary}", "INFO")
            print(f"{perf_summary}")

        dlog.end_request(question_text, len(answer))
        return True

    except KeyboardInterrupt:
        raise
    except Exception as e:
        dlog.log_error(f"Answer generation failed", e)
        import traceback
        traceback.print_exc()
        return False
    finally:
        # ALWAYS release lock and start cooldown
        state.stop_generation()
        dlog.log_state_change("GENERATING", "COOLDOWN")
        state.start_cooldown(answer_length=len(answer), is_code=wants_code or '```' in answer)
        state.set_last_question(question_text)


# =============================================================================
# AUDIO CAPTURE + TRANSCRIPTION
# =============================================================================

def capture_worker():
    """
    Producer thread: Continuously captures audio and puts it in the queue.
    This ensures we NEVER miss a question while processing the previous one.
    """
    from professional_audio import capture_question
    print("\n--- Audio Capture Thread Started ---")
    dlog.log("Audio capture worker started", "INFO")

    _first_capture = True          # Flush once on startup to clear stale audio
    _last_capture_got_audio = True # Track whether last call returned audio

    while not should_exit:
        try:
            capture_start = time.time()

            # Only flush the audio stream on startup or after a long cooldown
            # where stale audio has built up.  Never flush between normal captures —
            # that would discard the beginning of the next question.
            should_flush = _first_capture or (
                not _last_capture_got_audio and state.is_in_cooldown()
            )

            audio = capture_question(
                max_duration=config.MAX_RECORDING_DURATION,
                silence_duration=config.SILENCE_DEFAULT,
                verbose=False,
                flush_stream=should_flush,
            )
            _first_capture = False
            capture_time = time.time() - capture_start

            if audio is not None and len(audio) >= int(16000 * MIN_AUDIO_DURATION):
                _last_capture_got_audio = True
                audio_length = len(audio) / 16000
                dlog.log_audio_capture(capture_time, audio_length, len(audio))
                try:
                    audio_queue.put_nowait(audio)
                except queue.Full:
                    # Drop oldest to make room for freshest
                    try:
                        audio_queue.get_nowait()
                    except queue.Empty:
                        pass
                    audio_queue.put_nowait(audio)
                dlog.log_queue_status(audio_queue.qsize(), "audio_added")
            else:
                _last_capture_got_audio = False
                # No speech detected — yield briefly to avoid CPU spin,
                # but NOT 100ms; 20ms keeps us responsive.
                time.sleep(0.02)

        except Exception as e:
            dlog.log_error("Capture worker error", e)
            time.sleep(1)

def processing_worker():
    """
    Consumer thread: Processes captured audio segments sequentially.
    """
    print("\n--- Processing Worker Started ---")
    dlog.log("Processing worker started", "INFO")

    # Smart Buffer State
    partial_transcription = ""
    last_partial_time = 0

    # Short-term rejection cache — prevents the same rejected phrase from
    # being re-processed endlessly (e.g. "What is autosis?" looping every few seconds)
    _recent_rejections: dict = {}   # normalized_text → last_rejected_time
    _REJECTION_COOLDOWN = 20.0      # ignore same rejected text for 20 seconds

    while not should_exit:
        try:
            # Get next audio segment
            try:
                audio = audio_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            dlog.log_queue_status(audio_queue.qsize(), "processing_audio")

            # 1. Transcribe
            _pipeline_start = time.time()
            stt_start = time.time()
            state.mark_transcription_start()
            event_bus.push_stt_event(config.STT_BACKEND, 0, 'start')
            transcription, score = transcribe(audio)
            transcription = transcription.strip()
            state.mark_transcription_end()
            stt_time = time.time() - stt_start

            if not transcription or len(transcription) < 5:
                event_bus.push_stt_event(config.STT_BACKEND, stt_time * 1000, 'silent')
                if config.VERBOSE:
                    print(f"[PERF] Transcribe: {stt_time*1000:.0f}ms | skipped (empty/short)")
                audio_queue.task_done()
                continue

            # Always log STT result + timing
            event_bus.push_stt_event(config.STT_BACKEND, stt_time * 1000, 'done')
            print(f"\n[PERF] STT:      {stt_time*1000:.0f}ms")
            print(f"[TEXT] '{transcription}'")

            # Show live transcription in UI immediately after STT
            answer_storage.set_transcribing(transcription)

            # 2. Confidence Filter
            if score < 0.25:
                dlog.log(f"Low confidence ({score:.2f}), skipping", "DEBUG")
                audio_queue.task_done()
                continue

            # 2b. EARLY HALLUCINATION CHECK - Reject Whisper hallucinations immediately
            if _is_whisper_hallucination(transcription):
                if config.VERBOSE:
                    print(f"[HALLUCINATION] Rejected: '{transcription[:50]}...'")
                dlog.log(f"Hallucination rejected: '{transcription[:50]}'", "DEBUG")
                audio_queue.task_done()
                continue

            # 3. Smart Validation & Aggregation
            state.mark_validation_start()

            # Logic: Always buffer deeply until a distinct "gap" of silence is detected
            # or the combined buffer forms a very strong complete question.

            # 1. Add current chunk to buffer
            if config.VERBOSE:
                print(f"[SMART] Received chunk: '{transcription}'")

            # Simple list-based buffer to ensure clean joining
            if not hasattr(processing_worker, "text_buffer"):
                processing_worker.text_buffer = []
                processing_worker.buffer_ts = 0.0     # timestamp of last chunk added

            # If the system is currently generating/in cooldown, clear any stale buffer
            # so we don't merge the incoming question with leftover fragments from before.
            if state.should_block_input() and processing_worker.text_buffer:
                dlog.log("Clearing stale buffer (cooldown/generating active)", "DEBUG")
                processing_worker.text_buffer = []

            # Pre-buffer garbage filter: don't accumulate obviously garbled chunks
            # (short fragment + no recognizable English structure = garbled STT)
            _words = transcription.split()
            _is_garbage = (
                len(_words) <= 4
                and score < 0.70
                and not transcription.endswith('?')
                and not any(w.lower() in ('what', 'how', 'why', 'when', 'write', 'explain',
                                          'define', 'create', 'list', 'is', 'are', 'difference')
                            for w in _words)
            )
            if _is_garbage and not processing_worker.text_buffer:
                dlog.log(f"Pre-buffer garbage skip: '{transcription}' (conf={score:.2f})", "DEBUG")
                state.mark_validation_end()
                audio_queue.task_done()
                continue

            processing_worker.text_buffer.append(transcription)
            processing_worker.buffer_ts = time.time()
            last_partial_time = processing_worker.buffer_ts
            dlog.log(f"Buffer now has {len(processing_worker.text_buffer)} chunks", "DEBUG")

            # Fragment merge window: if the current buffer does NOT yet look like a
            # complete question (no '?', very short, ends mid-phrase), wait up to
            # 2.5s for a follow-up chunk before finalizing.
            # This handles slow interviewers who pause mid-sentence:
            #   "What is..."  [1s pause]  "...the difference between list and tuple?"
            current_text = " ".join(processing_worker.text_buffer).strip()
            _looks_complete = (
                current_text.endswith('?')
                or len(current_text.split()) >= 6
                or score >= 0.82
            )
            if not _looks_complete and len(processing_worker.text_buffer) == 1:
                # Peek: wait up to 1.0s for the next audio chunk (was 2.5s — too slow)
                _MERGE_WAIT = 1.0
                _merge_start = time.time()
                try:
                    next_audio = audio_queue.get(timeout=_MERGE_WAIT)
                    # Got a follow-up chunk — transcribe and add to buffer
                    _next_text, _next_score = transcribe(next_audio)
                    _next_text = _next_text.strip()
                    if _next_text and len(_next_text) >= 3:
                        processing_worker.text_buffer.append(_next_text)
                        print(f"[PERF] Merge:   {(time.time()-_merge_start)*1000:.0f}ms (got follow-up: '{_next_text[:40]}')")
                        dlog.log(f"Merged follow-up: '{_next_text}'", "DEBUG")
                    audio_queue.task_done()
                except queue.Empty:
                    _mw = (time.time()-_merge_start)*1000
                    print(f"[PERF] Merge:   {_mw:.0f}ms (no follow-up — finalizing)")

            current_text = " ".join(processing_worker.text_buffer).strip()

            # Finalize
            full_text = current_text

            if config.VERBOSE:
                print(f"[SMART] Finalizing buffer: '{full_text}'")

            dlog.log(f"Buffer finalized: '{full_text}'", "DEBUG")

            # Reset buffer
            processing_worker.text_buffer = []

            # SPLIT MERGED QUESTIONS: Extract the best question if multiple are merged
            original_text = full_text
            full_text = split_merged_questions(full_text)
            if full_text != original_text:
                if config.VERBOSE:
                    print(f"[SMART] Extracted question: '{full_text}'")
                dlog.log(f"Split merged: '{original_text}' -> '{full_text}'", "DEBUG")

            # FRAGMENT MERGING: Merge with recent chat/voice context
            # Pre-merge guard: only skip merging for definite garbage/noise.
            # Do NOT skip for fragments like "CP and MV" (no_question_pattern) or
            # "CP and MV" (incomplete) — these may complete a pending incomplete context
            # e.g. "What is the difference between" [incomplete] + "CP and MV" → valid question
            _pre_merge_valid, _, _pre_reason = clean_and_validate(full_text)
            _HARD_SKIP_REASONS = frozenset({
                'ignore_pattern', 'youtube_tutorial', 'hallucination',
                'gibberish_number_start', 'too_short',
            })
            _skip_merge = _pre_reason in _HARD_SKIP_REASONS
            merged_text, was_merged = (full_text, False) if _skip_merge else fragment_context.merge_with_context(full_text)
            if was_merged:
                dlog.log(f"Fragment merged: '{full_text}' -> '{merged_text}'", "INFO")
                if config.VERBOSE:
                    print(f"[MERGE] '{full_text}' + context -> '{merged_text}'")
                full_text = merged_text

            # Intro shortcut: bypass validation for "introduce yourself" commands
            # Validator rejects these with no_question_pattern but they're valid for us
            if is_introduction_question(full_text):
                state.wait_until_idle(timeout=10.0)
                handle_question(full_text)
                fragment_context.save_context(full_text, "voice")
                audio_queue.task_done()
                continue

            # Validate the COMBINED text
            validate_start = time.time()
            is_valid, cleaned, reason = clean_and_validate(full_text)
            validate_time = time.time() - validate_start
            state.mark_validation_end()

            dlog.log_validation(validate_time, is_valid, reason, cleaned)

            if not is_valid:
                if config.VERBOSE:
                    print(f"[DEBUG] Validation rejected: '{cleaned}' ({reason})")

                # Rejection dedup: if same text was recently rejected, skip silently
                _norm_key = full_text.lower().strip()
                _now = time.time()
                if _recent_rejections.get(_norm_key, 0) + _REJECTION_COOLDOWN > _now:
                    dlog.log(f"Dedup-skip repeated rejection: '{full_text[:50]}'", "DEBUG")
                    answer_storage.set_transcribing("")  # clear hearing indicator
                    audio_queue.task_done()
                    continue
                _recent_rejections[_norm_key] = _now
                # Trim rejection cache so it doesn't grow unbounded
                if len(_recent_rejections) > 50:
                    oldest = min(_recent_rejections, key=_recent_rejections.get)
                    del _recent_rejections[oldest]

                # Slow-speaker fix: save incomplete fragments so the next chunk can merge
                _ends_incomplete = full_text.rstrip('.?!').lower().split()[-1] in (
                    'for', 'to', 'in', 'of', 'with', 'by', 'and', 'or', 'the', 'a', 'an'
                ) if full_text.split() else False
                if (reason in ('incomplete', 'too_short') or _ends_incomplete) and len(full_text.split()) >= 3:
                    fragment_context.save_incomplete_context(full_text)
                    dlog.log(f"Saved incomplete fragment for merging: '{full_text}'", "DEBUG")
                audio_queue.task_done()
                continue

            validated = cleaned

            # Clear live transcription — show validated question instead
            answer_storage.set_transcribing("")
            # Show question in UI IMMEDIATELY — before gate wait so user sees it right away
            answer_storage.set_processing_question(validated)

            # 4. Action Gate (Concurrency Protection)
            gate_start = time.time()
            state.wait_until_idle(timeout=10.0)
            gate_wait = time.time() - gate_start

            if gate_wait > 0.05:
                print(f"[PERF] Gate wait: {gate_wait*1000:.0f}ms")

            if should_exit:
                audio_queue.task_done()
                break

            # 5a. Clarification intercept — "I am asking about X" / "I meant X"
            # Candidate is correcting a mis-transcribed/wrong previous answer.
            # Re-route to the corrected topic and auto-learn the STT correction.
            _clarified = _extract_clarification(validated)
            if _clarified:
                _prev_q = (fragment_context.get_recent_context() or {}).get('question', '')
                print(f"[CLARIFY] Candidate corrected: '{_prev_q}' → '{_clarified}'")
                if _prev_q and _prev_q.lower() != _clarified.lower():
                    try:
                        import stt_learner as _sl
                        _sl.submit_correction(_prev_q, _clarified)
                    except Exception:
                        pass
                validated = _clarified

            # 5. Process
            dlog.log(f"Passing to handle_question: '{validated}'", "INFO")
            _t0 = time.time()
            handle_question(validated)
            _answer_ms = (time.time()-_t0)*1000
            _pipeline_ms = (time.time()-_pipeline_start)*1000
            print(f"[PERF] Total answer: {_answer_ms:.0f}ms")
            print(f"[PERF] ── PIPELINE: STT→DB/LLM→UI = {_pipeline_ms:.0f}ms total ──")
            state.record_answer_latency(_pipeline_ms)

            # 6. Save context for cross-source fragment merging
            fragment_context.save_context(validated, "voice")

            audio_queue.task_done()

        except Exception as e:
            dlog.log_error("Processing worker error", e)
            time.sleep(1)


# =============================================================================
# MAIN INTERVIEW LOOP
# =============================================================================

def start_concurrent_pipeline():
    """Starts the producer and consumer threads."""
    producer = threading.Thread(target=capture_worker, daemon=True)
    consumer = threading.Thread(target=processing_worker, daemon=True)
    
    producer.start()
    consumer.start()
    
    # Keep main thread alive
    while not should_exit:
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            break


# =============================================================================
# STARTUP
# =============================================================================

def start(boot_start_time: float = None):
    """
    Production startup.

    ALWAYS SUCCEEDS - never crashes due to audio/device issues.
    """
    print("\n" + "=" * 60)
    print(PRODUCT_NAME.upper())
    print("=" * 60)
    print(f"  STT Model: {config.STT_MODEL}")
    print(f"  LLM Model: {os.environ.get('LLM_MODEL_OVERRIDE', config.LLM_MODEL)}")

    # Clear runtime state (locks, cooldowns, etc.)
    state.force_clear_all()
    answer_cache.clear_cache()
    fragment_context.clear_context()
    dlog.clear_logs()

    # Clear all previous Q&A data for fresh interview
    # (run.sh already deletes files, this ensures in-memory is also clear)
    answer_storage.clear_all(force_clear=True)
    print("✓ Fresh interview session started")

    # Load selected user profile from USER_ID_OVERRIDE env var (set by run.sh)
    _uid_str = os.environ.get('USER_ID_OVERRIDE', '').strip()
    if _uid_str.isdigit():
        _uid = int(_uid_str)
        _user = qa_database.get_user(_uid)
        if _user:
            state.set_selected_user(_user)
            _intro_status = "✓ intro loaded" if (_user.get('self_introduction') or '').strip() else "no intro set"
            print(f"✓ Active user: {_user['name']} ({_user.get('role','')}) [{_intro_status}]")
            try:
                import semantic_engine
                semantic_engine.engine.set_role_topics(_user.get('role', ''))
            except Exception:
                pass
        else:
            print(f"⚠ User ID {_uid} not found — no user profile active")

    # Log startup (to file only)
    dlog.log("=" * 60, "INFO")
    dlog.log(f"{PRODUCT_NAME.upper()} STARTING", "INFO")
    dlog.log(f"Log files: {dlog.get_log_paths()}", "INFO")

    # Start Web UI (Silent)
    try:
        subprocess.run(["fuser", "-k", "8000/tcp"], capture_output=True, timeout=2)
        subprocess.Popen(
            [sys.executable, "web/server.py"],
            start_new_session=True
        )
        # Display LAN IP prominently for Extension and Mobile
        _lan_ip = get_server_ip()
        _public_domain = os.environ.get("NGROK_DOMAIN")
        _server_url = f"https://{_public_domain}" if _public_domain else f"http://{_lan_ip}:{config.WEB_PORT}"
        _mobile_url = f"https://{_public_domain}" if _public_domain else f"http://{_lan_ip}:{config.WEB_PORT}"
        
        print(f"✓ {product_banner()}")
        print(f"✓ Web UI: http://localhost:{config.WEB_PORT}")
        print(f"✓ Extension Server URL: {_server_url}")
        print(f"✓ Mobile: {_mobile_url}  ← open on phone/tablet")
    except Exception:
        pass

    # Pre-load STT + warm up cloud connections
    print("Loading models...")
    import stt
    import numpy as np
    if config.STT_BACKEND == "deepgram":
        print("  Warming up Deepgram session (TLS handshake)...")
        _dg_ok = stt._get_deepgram_session()
        print("  ✓ Deepgram session ready")
    elif config.STT_BACKEND == "sarvam":
        # Real connectivity test — send 1s of silence to verify Sarvam API responds
        print("  Testing Sarvam API connection...")
        import io as _io
        try:
            import soundfile as _sf
            _buf = _io.BytesIO()
            _test_audio = np.zeros(16000, dtype=np.float32) + 0.02  # above RMS gate
            _sf.write(_buf, _test_audio, 16000, format='WAV', subtype='PCM_16')
            _session = stt._get_sarvam_session()
            _resp = _session.post(
                "https://api.sarvam.ai/speech-to-text",
                files={"file": ("test.wav", _buf.getvalue(), "audio/wav")},
                data={"model": "saarika:v2.5", "language_code": "en-IN",
                      "with_timestamps": "false", "with_disfluencies": "false"},
                timeout=8,
            )
            _resp.raise_for_status()
            print("  ✓ Sarvam API: connected (saarika:v2.5)")
        except Exception as _e:
            print(f"  ⚠ Sarvam API failed: {_e}")
            print("  ⚠ FALLING BACK TO LOCAL WHISPER — change STT in settings or check SARVAM_API_KEY")
    else:
        stt.transcribe(np.zeros(16000, dtype=np.float32))

    # Pre-warm DB cache (avoids 30-40ms cold-start on first question)
    try:
        qa_database.find_answer("what is python", want_code=False)
    except Exception:
        pass

    # Update state with active model names (shown in /api/session-info)
    _stt_info = stt.get_model_info()
    state.set_active_models(
        stt=_stt_info.get('backend', config.STT_BACKEND) + '/' + _stt_info.get('name', config.STT_MODEL),
        llm=config.LLM_MODEL,
    )

    print("✓ System Ready")

    output_manager.clear_answer_buffer()

    # Print log file locations
    log_paths = dlog.get_log_paths()
    print(f"✓ Logs: {log_paths['debug']}")

    print("\nListening for system audio...")
    dlog.log("System ready, listening for audio", "INFO")

    # Start Concurrent Pipeline
    start_concurrent_pipeline()


def main():
    """Main entry point - silent startup."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[ERROR] ANTHROPIC_API_KEY not set.")
        sys.exit(1)

    # Resume only loaded if uploaded via UI (not built-in resume.txt)
    load_resume_context()
    if resume:
        print(f"✓ Resume loaded ({len(resume)} chars)")
    else:
        print("  No resume uploaded (upload via UI if needed)")

    # Load job description
    global job_description
    try:
        job_description = load_job_description(config.JD_PATH)
        if job_description.strip():
            print(f"✓ Job Description loaded ({len(job_description)} chars)")
    except:
        pass
    
    start()


if __name__ == "__main__":
    # Cloud mode (Render.com): web server handles everything via WebSocket.
    # Skip local audio listener — there is no microphone on Render.
    if config.CLOUD_MODE:
        import subprocess as _sp
        port = int(os.environ.get("PORT", 8000))
        print(f"[CLOUD MODE] Starting {PRODUCT_NAME} web server on port {port}")
        print(f"[CLOUD MODE] {PRODUCT_NAME} audio captured by Chrome extension via WebSocket")
        _sp.run([sys.executable, "web/server.py"])

    elif os.environ.get("AUDIO_SOURCE") == "extension":
        # Chrome Extension mode: extension on another laptop captures audio,
        # does STT, and sends transcripts to /ws/audio WebSocket.
        # The web server's _handle_ws_text() runs the same full pipeline:
        # validate → cache → DB → LLM → answer_storage (identical to local mode).
        # No local audio capture or STT model loading needed here.
        import subprocess as _sp
        port = int(os.environ.get("PORT", 8000))
        print(f"[EXTENSION MODE] Starting {PRODUCT_NAME} web server on port {port}")
        print(f"[EXTENSION MODE] Waiting for Chrome extension to connect and send transcripts...")
        print(f"[EXTENSION MODE] Extension STT → /ws/audio → validate → DB/LLM → dashboard")
        _sp.run([sys.executable, "web/server.py"])

    else:
        main()
