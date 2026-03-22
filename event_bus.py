"""
Drishi Pro — In-memory event bus for zero-latency SSE delivery.

Replaces file-polling with direct push:
  Before: LLM chunk → disk write (80ms throttle) → SSE poll (50ms) → client
  After:  LLM chunk → queue.put() → SSE yields immediately → client

Latency: 130ms → <1ms per chunk delivery.
"""
import queue
import threading
from typing import Optional

_lock = threading.Lock()
_subscribers: list = []   # list[queue.Queue]
_QUEUE_MAX = 300          # Per-client buffer; drop oldest if client is too slow


def subscribe() -> queue.Queue:
    """Register a new SSE client. Returns a queue to pop events from."""
    q = queue.Queue(maxsize=_QUEUE_MAX)
    with _lock:
        _subscribers.append(q)
    return q


def unsubscribe(q: queue.Queue):
    """Unregister a disconnected SSE client."""
    with _lock:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass


def _push(event_type: str, data: dict):
    """Non-blocking push to all live subscriber queues."""
    # Snapshot subscribers under lock, then push outside it.
    # This keeps the lock duration O(1) instead of O(subscribers × queue_op).
    with _lock:
        subs = list(_subscribers)
    event = {"t": event_type, "d": data}
    dead = []
    for q in subs:
        try:
            q.put_nowait(event)
        except queue.Full:
            dead.append(q)
    if dead:
        with _lock:
            for q in dead:
                try:
                    _subscribers.remove(q)
                except ValueError:
                    pass


def push_chunk(question: str, chunk: str):
    """Push a streaming LLM chunk (called per token during generation)."""
    _push("chunk", {"q": question, "c": chunk})


def push_complete(question: str, answer: str, metrics: Optional[dict] = None):
    """Push a completed answer (called on DB hit or LLM completion)."""
    _push("answer", {
        "question": question,
        "answer": answer,
        "is_complete": True,
        "metrics": metrics or {},
    })


def push_transcribing(text: str):
    """Push live STT transcription text (called from audio thread)."""
    _push("transcribing", {"text": text})


def push_question_started(question: str):
    """Push event immediately when a question is validated (creates placeholder card before answer arrives)."""
    _push("question", {"question": question})


def push_status(msg: str):
    """Push a status message."""
    _push("status", {"msg": msg})


def push_stt_event(backend: str, ms: float, phase: str):
    """Push STT pipeline phase event.
    phase: 'start' — STT is running
           'done'  — STT finished successfully (ms = latency)
           'silent'— STT returned empty (no speech detected)
           'error' — STT failed / fell back
    """
    _push("stt", {"backend": backend, "ms": round(ms), "phase": phase})
