"""Semantic search fallback for the Q&A database.

Uses sentence-transformers (all-MiniLM-L6-v2, 384-dim) to embed questions
and find semantically similar entries when Jaccard similarity fails.

Architecture:
- In-memory numpy array of all embeddings (loaded lazily on first call)
- Cosine similarity against the query embedding
- Falls back gracefully if sentence-transformers is not installed
- Background worker embeds newly added rows so the index stays fresh

Threshold: 0.80 cosine similarity (tuned to avoid false positives).
"""

from __future__ import annotations

import threading
import time
from typing import Optional, Tuple

# Cosine similarity threshold — below this we return None (no match)
SEMANTIC_THRESHOLD = 0.80

# Lazy globals — populated on first call to find_semantic_answer()
_model = None
_model_lock = threading.Lock()
_index_lock = threading.Lock()

# Embedding index — parallel lists (same order)
_qa_ids: list[int] = []
_embeddings = None   # numpy array shape (N, 384) once loaded

# Flag: False until index has been built at least once
_index_ready = False

# Queue for newly inserted Q&A ids to embed in background
import queue as _queue
_embed_queue: _queue.Queue = _queue.Queue()


# ── Model loading ──────────────────────────────────────────────────────────────

def _load_model():
    """Load the sentence-transformers model (cached after first load)."""
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        try:
            from sentence_transformers import SentenceTransformer
            print("[SEMANTIC] Loading all-MiniLM-L6-v2…")
            _model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
            print("[SEMANTIC] Model loaded.")
        except Exception:
            _model = None
    return _model


def _is_available() -> bool:
    return _load_model() is not None


# ── Index building ─────────────────────────────────────────────────────────────

def _build_index():
    """Load all QA questions from DB, embed them, store in memory."""
    global _qa_ids, _embeddings, _index_ready

    model = _load_model()
    if model is None:
        return

    try:
        import qa_database
        import numpy as np

        conn = qa_database._get_read_conn()
        rows = conn.execute(
            "SELECT id, question FROM qa_pairs ORDER BY id"
        ).fetchall()
        if not rows:
            return

        ids = [r["id"] for r in rows]
        questions = [r["question"] for r in rows]

        t0 = time.time()
        vecs = model.encode(questions, batch_size=64, show_progress_bar=False,
                            normalize_embeddings=True)
        elapsed = time.time() - t0
        print(f"[SEMANTIC] Indexed {len(ids)} Q&A pairs in {elapsed:.1f}s")

        with _index_lock:
            _qa_ids = ids
            _embeddings = vecs
            _index_ready = True
    except Exception as exc:
        print(f"[SEMANTIC] _build_index failed: {exc}")


def _ensure_index():
    """Build index lazily on first search. Thread-safe."""
    global _index_ready
    if not _index_ready:
        _build_index()


# ── Background embed worker ────────────────────────────────────────────────────

def _embed_worker():
    """Daemon thread: embed newly added Q&A rows and append to in-memory index."""
    while True:
        try:
            qa_id = _embed_queue.get(timeout=5)
        except _queue.Empty:
            continue

        model = _load_model()
        if model is None or not _index_ready:
            continue

        try:
            import qa_database
            import numpy as np

            conn = qa_database._get_read_conn()
            row = conn.execute(
                "SELECT question FROM qa_pairs WHERE id=?", (qa_id,)
            ).fetchone()
            if not row:
                continue
            vec = model.encode([row["question"]], normalize_embeddings=True)
            with _index_lock:
                _qa_ids.append(qa_id)
                _embeddings = np.vstack([_embeddings, vec]) if _embeddings is not None else vec
        except Exception as exc:
            print(f"[SEMANTIC] embed_worker failed for id={qa_id}: {exc}")


def _start_embed_worker():
    t = threading.Thread(target=_embed_worker, daemon=True, name="semantic-embed")
    t.start()


def queue_new_entry(qa_id: int):
    """Call this after inserting a new Q&A row to keep the index fresh."""
    _embed_queue.put(qa_id)


# ── Search ─────────────────────────────────────────────────────────────────────

def find_semantic_answer(
    question: str,
    want_code: bool = False,
    threshold: float = SEMANTIC_THRESHOLD,
) -> Optional[Tuple[str, float, int]]:
    """Search the embedding index for a semantically similar question.

    Returns (answer_text, score, qa_id) or None if no match above threshold.
    """
    if not question:
        return None

    model = _load_model()
    if model is None:
        return None

    _ensure_index()

    with _index_lock:
        if not _index_ready or _embeddings is None or len(_qa_ids) == 0:
            return None
        ids_snap = list(_qa_ids)
        emb_snap = _embeddings  # numpy array — reads are safe without copy

    try:
        import numpy as np

        q_vec = model.encode([question], normalize_embeddings=True)  # shape (1, 384)
        scores = (emb_snap @ q_vec.T).flatten()  # cosine similarity (already normalized)
        best_idx = int(np.argmax(scores))
        best_score = float(scores[best_idx])

        if best_score < threshold:
            return None

        best_id = ids_snap[best_idx]
    except Exception as exc:
        print(f"[SEMANTIC] search failed: {exc}")
        return None

    # Fetch answer from DB
    try:
        import qa_database

        conn = qa_database._get_read_conn()
        row = conn.execute(
            "SELECT answer_theory, answer_coding, answer_humanized FROM qa_pairs WHERE id=?",
            (best_id,),
        ).fetchone()
        if not row:
            return None

        humanized  = (row["answer_humanized"] or "").strip()
        theory_ans = (row["answer_theory"]    or "").strip()
        coding_ans = (row["answer_coding"]    or "").strip()

        if want_code and coding_ans:
            answer = coding_ans
        elif humanized:
            answer = humanized
        else:
            answer = theory_ans or coding_ans

        if not answer:
            return None

        return answer, best_score, best_id
    except Exception as exc:
        print(f"[SEMANTIC] DB fetch failed: {exc}")
        return None


# ── Startup ────────────────────────────────────────────────────────────────────

def init_async():
    """Build the embedding index in a background thread at server startup."""
    if not _is_available():
        return
    t = threading.Thread(target=_build_index, daemon=True, name="semantic-init")
    t.start()
    _start_embed_worker()
