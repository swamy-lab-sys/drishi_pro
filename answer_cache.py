"""
Answer Cache for Drishi Pro

REQUIRED: Cache answers by normalized question to avoid repeated LLM calls.

RULES:
1. Normalize question before lookup (lowercase, strip punctuation, whitespace)
2. Cache hit = instant return (<1s)
3. Cache miss = LLM call (2-4s)
4. No expiration (session-scoped)
5. No persistence (memory only)
"""

import re
import time
import atexit
import threading
from collections import OrderedDict
from typing import Optional, Dict

import config

import json
from pathlib import Path

# Thread-safe LRU cache using OrderedDict
_cache: OrderedDict = OrderedDict()
_cache_lock = threading.Lock()
_max_size: int = getattr(config, 'CACHE_MAX_SIZE', 1000)

# Persistent Cache File
# Fix: config.ANSWERS_DIR is a string, so we must wrap it in Path() and expand user (~)
CACHE_FILE = Path(config.ANSWERS_DIR).expanduser() / "answer_cache.json"

# Cache stats
_hits = 0
_misses = 0

# Batched write control
_dirty = False
_last_save_time = 0.0
_SAVE_INTERVAL = 30.0  # Save to disk at most every 30 seconds


def load_cache_from_disk():
    """Load cache from disk on startup."""
    global _cache
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if CACHE_FILE.exists():
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                with _cache_lock:
                    _cache.clear()
                    # Convert dict back to OrderedDict (insertion order not guaranteed in JSON, but useful enough)
                    for k, v in data.items():
                        _cache[k] = v
            print(f"[CACHE] Loaded {len(_cache)} answers from disk.")
    except Exception as e:
        print(f"[CACHE] Failed to load cache: {e}")

# NOTE: Do not auto-load at import. main.py calls clear_cache() immediately after import.
# Call load_cache_from_disk() explicitly if you need persistence across restarts.


def _flush_on_exit():
    """Save dirty cache to disk on shutdown."""
    if _dirty:
        save_cache_to_disk()

atexit.register(_flush_on_exit)

def save_cache_to_disk():
    """Save cache to disk."""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _cache_lock:
            # OrderedDict to dict
            data = dict(_cache)
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[CACHE] Failed to save cache: {e}")


# Common spoken contractions → expanded form (compiled once at module load)
_CONTRACTIONS = [
    (re.compile(r"\bwhat's\b",    re.IGNORECASE), "what is"),
    (re.compile(r"\bhow's\b",     re.IGNORECASE), "how is"),
    (re.compile(r"\bwhere's\b",   re.IGNORECASE), "where is"),
    (re.compile(r"\bwhen's\b",    re.IGNORECASE), "when is"),
    (re.compile(r"\bwho's\b",     re.IGNORECASE), "who is"),
    (re.compile(r"\bthat's\b",    re.IGNORECASE), "that is"),
    (re.compile(r"\bit's\b",      re.IGNORECASE), "it is"),
    (re.compile(r"\bhe's\b",      re.IGNORECASE), "he is"),
    (re.compile(r"\bshe's\b",     re.IGNORECASE), "she is"),
    (re.compile(r"\bisn't\b",     re.IGNORECASE), "is not"),
    (re.compile(r"\baren't\b",    re.IGNORECASE), "are not"),
    (re.compile(r"\bwasn't\b",    re.IGNORECASE), "was not"),
    (re.compile(r"\bweren't\b",   re.IGNORECASE), "were not"),
    (re.compile(r"\bdoesn't\b",   re.IGNORECASE), "does not"),
    (re.compile(r"\bdon't\b",     re.IGNORECASE), "do not"),
    (re.compile(r"\bdidn't\b",    re.IGNORECASE), "did not"),
    (re.compile(r"\bcan't\b",     re.IGNORECASE), "cannot"),
    (re.compile(r"\bcouldn't\b",  re.IGNORECASE), "could not"),
    (re.compile(r"\bwon't\b",     re.IGNORECASE), "will not"),
    (re.compile(r"\bwouldn't\b",  re.IGNORECASE), "would not"),
    (re.compile(r"\bshouldn't\b", re.IGNORECASE), "should not"),
    (re.compile(r"\bhaven't\b",   re.IGNORECASE), "have not"),
    (re.compile(r"\bhasn't\b",    re.IGNORECASE), "has not"),
    (re.compile(r"\bhadn't\b",    re.IGNORECASE), "had not"),
]

# Leading filler pattern (compiled once)
_FILLER_PREFIX = re.compile(
    r'^(okay|ok|alright|so|well|um+|uh+|hmm+|ah+|right|now|yeah|yep|sure)\s*,?\s*',
    re.IGNORECASE
)


def normalize_question(question: str) -> str:
    """
    Normalize question for cache lookup.
    Expands contractions so "what's X?" hits the same cache entry as "what is X?".
    """
    if not question:
        return ""

    # Lowercase and strip
    normalized = question.lower().strip()

    # Expand contractions before anything else
    for pattern, replacement in _CONTRACTIONS:
        normalized = pattern.sub(replacement, normalized)

    # Remove enclosing brackets/parens around the whole question
    normalized = re.sub(r'^\[(.+)\]$', r'\1', normalized)
    normalized = re.sub(r'^\((.+)\)$', r'\1', normalized)

    # Remove trailing punctuation
    normalized = re.sub(r'[?.!,;:]+$', '', normalized)

    # Remove leading filler words
    normalized = _FILLER_PREFIX.sub('', normalized)

    # Collapse multiple spaces
    normalized = re.sub(r'\s+', ' ', normalized).strip()

    return normalized


def get_cached_answer(question: str) -> Optional[str]:
    """Get cached answer for question."""
    global _hits, _misses

    key = normalize_question(question)
    if not key:
        return None

    with _cache_lock:
        if key in _cache:
            _hits += 1
            _cache.move_to_end(key)  # Mark as recently used
            return _cache[key]
        _misses += 1
        return None


def cache_answer(question: str, answer: str) -> None:
    """Cache answer for question. Disk writes are batched for performance."""
    global _dirty, _last_save_time
    key = normalize_question(question)
    if not key or not answer:
        return

    with _cache_lock:
        _cache[key] = answer
        _cache.move_to_end(key)
        while len(_cache) > _max_size:
            _cache.popitem(last=False)
        _dirty = True

    # Batched disk write: only save every _SAVE_INTERVAL seconds
    now = time.time()
    if now - _last_save_time >= _SAVE_INTERVAL:
        save_cache_to_disk()
        _last_save_time = now
        _dirty = False


def is_duplicate_question(question: str) -> bool:
    """Check if question is already cached (duplicate)."""
    key = normalize_question(question)
    if not key:
        return False

    with _cache_lock:
        return key in _cache


def clear_cache() -> None:
    """Clear all cached answers (fresh start)."""
    global _hits, _misses

    with _cache_lock:
        _cache.clear()
        _hits = 0
        _misses = 0
    
    save_cache_to_disk()


def get_cache_stats() -> Dict[str, int]:
    """
    Get cache statistics.

    Returns:
        dict with 'hits', 'misses', 'size'
    """
    with _cache_lock:
        return {
            'hits': _hits,
            'misses': _misses,
            'size': len(_cache)
        }


# =============================================================================
# TEST
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("ANSWER CACHE - TEST")
    print("=" * 60)

    # Test normalization
    test_cases = [
        ("What is Python?", "what is python"),
        ("What is Python", "what is python"),
        ("WHAT IS PYTHON?", "what is python"),
        ("Okay, what is Python?", "what is python"),
        ("  What  is   Python?  ", "what is python"),
        ("Um, what is Python", "what is python"),
        ("So, what is Python?", "what is python"),
    ]

    print("\nNormalization tests:")
    for input_q, expected in test_cases:
        result = normalize_question(input_q)
        status = "PASS" if result == expected else "FAIL"
        print(f"  [{status}] '{input_q}' -> '{result}'")
        if result != expected:
            print(f"         Expected: '{expected}'")

    # Test caching
    print("\nCaching tests:")
    clear_cache()

    # Cache miss
    result = get_cached_answer("What is Python?")
    print(f"  Cache miss: {result is None}")

    # Cache answer
    cache_answer("What is Python?", "Python is a programming language.")

    # Cache hit
    result = get_cached_answer("What is Python?")
    print(f"  Cache hit: {result is not None}")
    print(f"  Answer: {result}")

    # Normalized cache hit (different formatting)
    result = get_cached_answer("Okay, what is python")
    print(f"  Normalized hit: {result is not None}")

    # Stats
    stats = get_cache_stats()
    print(f"\n  Stats: {stats}")

    print("\n" + "=" * 60)
