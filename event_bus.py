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
    with _lock:
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait({"t": event_type, "d": data})
            except queue.Full:
                dead.append(q)   # Slow/disconnected client
        for q in dead:
            _subscribers.remove(q)


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
