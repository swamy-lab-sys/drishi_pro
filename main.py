#!/usr/bin/env python3
"""
Drishi Pro - Production Pipeline

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
from llm_client import (
    get_interview_answer,
    get_coding_answer,
    get_streaming_interview_answer,
    correct_question_intent,
    clear_session,
    humanize_response,
)
from resume_loader import load_resume, load_job_description
from user_manager import is_introduction_question, build_resume_context_for_llm
import audio_listener
from audio_listener import (
    record_until_silence,
    select_audio_device_interactive,
)
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
    # Quality pre-filter before submitting to LLM validation
    _bullet_count = answer.count('\n-') + answer.count('\n•')
    _word_count = len(answer.split())
    if _word_count < 15:
        return  # Skip trivially short answers
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
        _db_t0 = time.time()
        db_result = qa_database.find_answer(question_text, want_code=wants_code)
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
        # Strategy:
        #   1. Ask LLM to correct the question (cheap, 60-token call)
        #   2. Re-run DB lookup on the corrected question
        #   3. If still a miss, proceed to full LLM generation
        print(f"[PERF] DB lookup:  {_db_ms:.0f}ms → MISS → calling LLM")
        from question_validator import _has_tech_term as _htc
        _has_tech = _htc(question_text.lower())
        _corrected_q = question_text
        if not _has_tech:
            # Skip intent correction if a tech term was already detected — trust it's valid
            # and go straight to LLM (~500ms saved per tech DB-miss)
            try:
                _corrected = correct_question_intent(question_text)
                if not _corrected:
                    # LLM said NOT_IT — abort LLM call, return clarification message
                    _clarify = "Question unclear or outside scope. Please repeat the question clearly."
                    print(f"[INTENT] NOT_IT — returning clarification")
                    output_manager.write_answer_chunk(_clarify)
                    output_manager.write_footer()
                    answer_storage.set_complete_answer(question_text, _clarify, {'source': 'rejected'})
                    return True
                elif _corrected.lower() != question_text.lower():
                    print(f"[INTENT] Corrected: '{question_text}' → '{_corrected}'")
                    dlog.log(f"[INTENT] '{question_text}' → '{_corrected}'", "INFO")
                    _corrected_q = _corrected
                    # Re-check DB with corrected question
                    _db2 = qa_database.find_answer(_corrected_q, want_code=wants_code)
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

        # Step 5: Cache answer — skip caching "unclear" responses
        _is_unclear = (
            "question unclear" in answer.lower()
            or "please repeat clearly" in answer.lower()
        ) and len(answer.split('\n')) <= 2
        if _is_unclear:
            # Don't cache or learn from unclear — save question as fragment for next audio
            fragment_context.save_incomplete_context(question_text)
            dlog.log(f"[LLM] Unclear response — saved as fragment, not cached", "WARN")
        else:
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

    while not should_exit:
        try:
            # Capture using professional engine
            capture_start = time.time()
            audio = capture_question(
                max_duration=config.MAX_RECORDING_DURATION,
                silence_duration=config.SILENCE_DEFAULT,  # Used config for better pause handling
                verbose=False
            )
            capture_time = time.time() - capture_start

            if audio is not None and len(audio) >= int(16000 * MIN_AUDIO_DURATION):
                audio_length = len(audio) / 16000  # seconds
                dlog.log_audio_capture(capture_time, audio_length, len(audio))
                try:
                    audio_queue.put_nowait(audio)
                except queue.Full:
                    # Drop oldest chunk to make room for newest
                    try:
                        audio_queue.get_nowait()
                    except queue.Empty:
                        pass
                    audio_queue.put_nowait(audio)
                dlog.log_queue_status(audio_queue.qsize(), "audio_added")

            # Small breath to avoid CPU spin if capture fails immediately
            time.sleep(0.1)

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
            stt_start = time.time()
            state.mark_transcription_start()
            transcription, score = transcribe(audio)
            transcription = transcription.strip()
            state.mark_transcription_end()
            stt_time = time.time() - stt_start

            if not transcription or len(transcription) < 5:
                if config.VERBOSE:
                    print(f"[PERF] Transcribe: {stt_time*1000:.0f}ms | skipped (empty/short)")
                audio_queue.task_done()
                continue

            # Always log STT result + timing
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
            last_partial_time = time.time()
            dlog.log(f"Buffer now has {len(processing_worker.text_buffer)} chunks", "DEBUG")

            # 2. FAST PATH: finalize immediately — no aggregation window.
            # Each audio capture is processed as-is. split_merged_questions and
            # fragment_context handle any cross-capture merging needed.
            current_text = " ".join(processing_worker.text_buffer).strip()
            more_chunks_arrived = False  # Always finalize immediately

            # 3. Time's up! Process what we have.
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
                state.wait_until_idle(timeout=30.0)
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
            state.wait_until_idle(timeout=30.0)
            gate_wait = time.time() - gate_start

            if gate_wait > 0.05:
                print(f"[PERF] Gate wait: {gate_wait*1000:.0f}ms")

            if should_exit:
                audio_queue.task_done()
                break

            # 5. Process
            dlog.log(f"Passing to handle_question: '{validated}'", "INFO")
            _t0 = time.time()
            handle_question(validated)
            print(f"[PERF] Total answer: {(time.time()-_t0)*1000:.0f}ms")

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
    print("DRISHI PRO")
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
        else:
            print(f"⚠ User ID {_uid} not found — no user profile active")

    # Log startup (to file only)
    dlog.log("=" * 60, "INFO")
    dlog.log("DRISHI PRO STARTING", "INFO")
    dlog.log(f"Log files: {dlog.get_log_paths()}", "INFO")

    # Start Web UI (Silent)
    try:
        subprocess.run(["fuser", "-k", "8000/tcp"], capture_output=True, timeout=2)
        subprocess.Popen(
            [sys.executable, "web/server.py"],
            start_new_session=True
        )
        # Print LAN IP for mobile access
        import socket
        try:
            _s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            _s.connect(('10.255.255.255', 1))
            _lan_ip = _s.getsockname()[0]
            _s.close()
        except Exception:
            _lan_ip = 'localhost'
        print(f"✓ Web UI: http://localhost:8000")
        print(f"✓ Mobile: http://{_lan_ip}:8000  ← open on phone/tablet")
    except Exception:
        pass

    # Pre-load STT + warm up cloud connections
    print("Loading models...")
    import stt
    import numpy as np
    stt.transcribe(np.zeros(16000, dtype=np.float32))
    # Eagerly warm up Deepgram/Sarvam session so first real question is fast
    if config.STT_BACKEND == "deepgram":
        import threading as _thr
        _thr.Thread(target=stt._get_deepgram_session, daemon=True).start()
    elif config.STT_BACKEND == "sarvam":
        import threading as _thr
        _thr.Thread(target=stt._get_sarvam_session, daemon=True).start()

    # Pre-warm DB cache (avoids 30-40ms cold-start on first question)
    try:
        qa_database.find_answer("what is python", want_code=False)
    except Exception:
        pass

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
        print(f"[CLOUD MODE] Starting web server on port {port}")
        print("[CLOUD MODE] Audio captured by Chrome extension via WebSocket")
        _sp.run([
            sys.executable, "web/server.py"
        ])
    else:
        main()
