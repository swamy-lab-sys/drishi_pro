"""
Fragment Context - Cross-source question merging.

Handles: Interviewer types "find even numbers" in chat, then speaks
"using slicing method" → merged into "find even numbers using slicing method"

Uses shared file for cross-process communication (main.py + web/server.py).
"""

import json
import re
import threading
import time
from pathlib import Path

CONTEXT_FILE = Path.home() / ".drishi" / "fragment_context.json"
MERGE_WINDOW = 10  # seconds - allow slow speakers

# In-memory cache to avoid disk reads in same process
_context_cache = None
_dir_ensured = False
_disk_write_timer: threading.Timer = None  # Debounce timer for disk writes

# Incomplete fragment cache — saves rejected slow-speaker fragments for merging
# e.g. "What is the difference between" → rejected → saved here →
# next chunk "list and tuple" arrives → merged into full question
_incomplete_cache = None
_incomplete_time = 0.0
INCOMPLETE_MERGE_WINDOW = 10  # seconds - interviewer may pause mid-sentence

FILLER_WORDS = frozenset({
    'the', 'a', 'an', 'is', 'are', 'in', 'of', 'to', 'and', 'or',
    'for', 'with', 'by', 'on', 'at', 'it', 'this', 'that', 'do',
    'does', 'can', 'you', 'me', 'i', 'we', 'my', 'your', 'how',
    'what', 'which', 'from', 'be', 'have', 'has', 'was', 'were',
    'will', 'would', 'could', 'should', 'not', 'no', 'so', 'given',
})

# Incomplete question prefixes — STT catches these when interviewer pauses mid-sentence
# e.g. "What is the difference between" (pause) → "list and tuple in Python"
INCOMPLETE_QUESTION_SUFFIXES = (
    # Trailing prepositions / articles that signal more is coming
    ' between', ' about', ' of', ' for', ' in', ' on', ' with', ' by',
    ' the', ' a', ' an', ' and', ' or', ' to',
    # Trailing verbs that need an object (short sentence only)
    ' does', ' do', ' is', ' are', ' you', ' we', ' i',
    # Trailing question openers without an object yet
    ' difference', ' comparison', ' example', ' explain', ' describe', ' how',
    # Common interview question starters that get cut off mid-sentence
    ' can', ' would', ' could', ' should', ' will',
)

# Words that start a continuation fragment (not a new question)
CONTINUATION_STARTERS = (
    # Method / approach qualifiers
    "using ", "with ", "by ", "without ", "instead of ", "rather than ", "not using ",
    # Chaining
    "and then ", "then ", "also ", "and ", "but ", "for ", "like ", "such as ", "via ",
    # "Now do X" — very common follow-up in interviews
    "now ", "now do ", "now write ", "now find ", "now sort ", "now create ",
    "now implement ", "now make ", "now add ", "now change ", "now modify ",
    # Modification / conversion
    "optimize ", "improve ", "refactor ", "change it", "modify ", "update ",
    "make it ", "make this ", "convert it", "convert this ",
    # Same-topic follow-up
    "do the same", "same but ", "same thing", "what about ",
    # Language qualifiers
    "in python", "in java", "in javascript", "in ",
    # Example requests
    "give me an example", "give an example", "can you give", "can you show",
    "show me an example", "show me how", "for example", "any example",
)

# Pronoun-first starters — "it", "this", "that" at position 0 means referring to prior context
PRONOUN_STARTERS = frozenset({'it', 'its', 'this', 'that', 'these', 'those'})

# Pre-compiled regex for continuation detection — replaces O(n) startswith loop
_CONTINUATION_RE = re.compile(
    r'^(?:' + '|'.join(re.escape(s.rstrip()) for s in sorted(CONTINUATION_STARTERS, key=len, reverse=True)) + r')',
    re.IGNORECASE
)

# Method/technique keywords that indicate a continuation when in short fragments
METHOD_KEYWORDS = {
    "slicing", "slice", "list comprehension", "comprehension",
    "lambda", "recursion", "recursive", "iteration", "iterative",
    "sorting", "filtering", "mapping", "reduce",
    "generator", "decorator", "class based", "function based",
    "brute force", "dynamic programming", "binary search",
    "two pointer", "sliding window", "stack", "queue",
    "hashmap", "hash map", "linked list", "dictionary",
    "regex", "built in", "builtin",
    "one liner", "one line", "loop", "for loop", "while loop",
    "try except", "exception handling",
    # NOTE: "set" and "tuple" intentionally excluded — too common in English
    # ("set up", "set a", "tuple of" etc.) causing false continuation merges
}


def _build_merged_text(prev_q: str, new_text: str) -> str:
    """
    Build a clean LLM-readable merged question from two fragments.

    Handles:
    - Strips redundant "now" prefix (implicit after concatenation)
    - Resolves "make this list / it / that" pronoun phrases
    - Joins with ". " so the LLM sees a single coherent task
    """
    cleaned = new_text.strip()

    # Remove leading "now" (redundant once we append to previous sentence)
    cleaned = re.sub(r'^now\s+', '', cleaned, flags=re.I).strip()

    # "make/use/take this/that/the <noun>" → "write a function for the <noun>"
    cleaned = re.sub(
        r'\b(make|use|take|apply)\s+(this|that|the|it)\s+(list|array|function|code|result|string|dict|set)\b',
        r'write a function for the \3',
        cleaned, flags=re.I
    )

    # Lone pronoun at start: "it " / "its " → "the result "
    cleaned = re.sub(r'^(it|its)\s+', 'the result ', cleaned, flags=re.I).strip()
    # "this" / "that" at start → remove (context is already in prev_q)
    cleaned = re.sub(r'^(this|that)\s+', '', cleaned, flags=re.I).strip()

    # Capitalise first letter
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]

    prev_clean = prev_q.rstrip('.,;:?! ')
    return f"{prev_clean}. {cleaned}"


def save_incomplete_context(text: str):
    """
    Save a fragment that was REJECTED because it was incomplete/too short.
    e.g. interviewer says "What is the difference between" then pauses 3s.
    The next fragment "list and tuple" can merge with this.
    """
    global _incomplete_cache, _incomplete_time
    text = text.strip()
    if not text:
        return
    lower = text.lower()
    # Only save if it ends with an incomplete-question suffix (worth merging)
    for suffix in INCOMPLETE_QUESTION_SUFFIXES:
        if lower.endswith(suffix):
            _incomplete_cache = text
            _incomplete_time = time.time()
            return
    # Also save short question-like starts (< 6 words) that could be a prefix
    words = text.split()
    if len(words) <= 6 and any(lower.startswith(s) for s in
                                ('what', 'how', 'why', 'when', 'where', 'which',
                                 'explain', 'define', 'describe', 'write', 'can you')):
        _incomplete_cache = text
        _incomplete_time = time.time()


def get_incomplete_context() -> str:
    """Return incomplete fragment if still within merge window, else None."""
    global _incomplete_cache, _incomplete_time
    if _incomplete_cache and (time.time() - _incomplete_time) <= INCOMPLETE_MERGE_WINDOW:
        return _incomplete_cache
    return None


def clear_incomplete_context():
    global _incomplete_cache, _incomplete_time
    _incomplete_cache = None
    _incomplete_time = 0.0


def _write_context_to_disk(data: dict):
    """Write context to disk for cross-process merging (Chrome extension → server)."""
    global _dir_ensured
    try:
        if not _dir_ensured:
            CONTEXT_FILE.parent.mkdir(parents=True, exist_ok=True)
            _dir_ensured = True
        with open(CONTEXT_FILE, 'w') as f:
            json.dump(data, f)
    except Exception:
        pass


def save_context(question: str, source: str):
    """Save processed question as context for future merging.
    In-memory update is immediate; disk write is debounced by 200ms to avoid
    blocking the pipeline on every question (~5-10ms saved per call).
    """
    global _context_cache, _disk_write_timer
    data = {
        'question': question.strip(),
        'source': source,
        'timestamp': time.time(),
    }
    _context_cache = data

    # Debounce disk write — cancel any pending timer and schedule a new one
    if _disk_write_timer is not None and _disk_write_timer.is_alive():
        _disk_write_timer.cancel()
    _disk_write_timer = threading.Timer(0.2, _write_context_to_disk, args=(data,))
    _disk_write_timer.daemon = True
    _disk_write_timer.start()


def get_recent_context():
    """Get recent question context if within merge window."""
    global _context_cache
    # Fast path: use in-memory cache (same process)
    if _context_cache and time.time() - _context_cache.get('timestamp', 0) <= MERGE_WINDOW:
        return _context_cache
    # Slow path: read from disk (cross-process)
    try:
        if not CONTEXT_FILE.exists():
            return None
        with open(CONTEXT_FILE, 'r') as f:
            data = json.load(f)
        if time.time() - data.get('timestamp', 0) <= MERGE_WINDOW:
            _context_cache = data
            return data
        return None
    except Exception:
        return None


def is_continuation(text: str) -> bool:
    """Check if text is a continuation fragment, not a standalone question."""
    if not text:
        return False

    lower = text.lower().strip()
    words = lower.split()

    if len(words) > 10 or len(words) < 2:
        return False

    # Starts with continuation word
    if _CONTINUATION_RE.match(lower):
        return True

    # Short fragment with method keyword but no question starter
    if len(words) <= 6:
        from question_validator import QUESTION_STARTERS
        has_q_starter = any(lower.startswith(s) for s in QUESTION_STARTERS)
        if not has_q_starter and not lower.endswith('?'):
            for kw in METHOD_KEYWORDS:
                if kw in lower:
                    return True

    return False


def merge_with_context(new_text: str) -> tuple:
    """
    Try to merge new_text with the most recent answered question.

    Multi-turn interview scenarios handled:
      Case 0 — Slow speaker: prev fragment is incomplete (short + trailing preposition)
      Case 1 — Explicit continuation starter ("now", "using", "optimize", "make it", …)
      Case 2 — Pronoun reference: new text starts with "it/this/that" referring to prev
      Case 3 — Method keyword in short fragment (recursion, slicing, etc.)
      Case 4 — Significant topic overlap (rephrasing / refinement)
      Case 5 — Data + instruction (list/array context + filter/find/sort task)

    Returns (merged_text, was_merged)
    """
    if not new_text:
        return new_text, False

    new_lower_check = new_text.lower().strip()

    # ── Case 0b: Slow speaker incomplete fragment ─────────────────────────────
    # Interviewer paused mid-sentence — the first half was REJECTED (not answered)
    # e.g. "What is difference between" [rejected] + "list and tuple" → merge
    # Also: "How do you handle?" [dangling verb, rejected] + "Production issues." → merge
    incomplete = get_incomplete_context()
    if incomplete:
        inc_lower = incomplete.lower().strip()
        inc_words = incomplete.split()
        if len(inc_words) <= 8:
            try:
                from question_validator import QUESTION_STARTERS
                is_standalone = any(new_lower_check.startswith(s.lower())
                                    for s in QUESTION_STARTERS)
            except Exception:
                is_standalone = False
            merged_via_suffix = False
            for suffix in INCOMPLETE_QUESTION_SUFFIXES:
                if inc_lower.endswith(suffix):
                    if not is_standalone:
                        merged = f"{incomplete} {new_text}"
                        clear_incomplete_context()
                        return merged, True
                    merged_via_suffix = True
                    break
            # Fallback: short question-like start saved by save_incomplete_context
            # e.g. "How do you handle?" (starts with 'how', ≤ 6 words) + "Production issues."
            if not merged_via_suffix and not is_standalone and len(inc_words) <= 6:
                # Strip trailing ? from the incomplete so join reads naturally
                merged = f"{incomplete.rstrip('? ')} {new_text}"
                clear_incomplete_context()
                return merged, True

    context = get_recent_context()
    if not context:
        return new_text, False

    prev_q = context.get('question', '').strip()
    if not prev_q:
        return new_text, False

    new_lower = new_text.lower().strip()
    prev_lower = prev_q.lower().strip()

    prev_words = set(prev_lower.split()) - FILLER_WORDS
    new_words  = set(new_lower.split()) - FILLER_WORDS

    # Guard: if new text is clearly a standalone question (long + ends with "?"),
    # only merge via high-confidence cases (0 and 2), not the weaker heuristics.
    new_word_count = len(new_text.split())
    is_long_question = new_word_count > 10 and new_lower.endswith('?')

    # ── Case 0: Slow speaker — incomplete previous fragment ───────────────────
    # e.g. prev="What is the difference between" + new="list and tuple in Python"
    prev_word_count = len(prev_q.split())
    if prev_word_count <= 8:
        for suffix in INCOMPLETE_QUESTION_SUFFIXES:
            if prev_lower.endswith(suffix):
                try:
                    from question_validator import QUESTION_STARTERS
                    is_standalone = any(new_lower.startswith(s.lower()) for s in QUESTION_STARTERS)
                except Exception:
                    is_standalone = False
                if not is_standalone:
                    return f"{prev_q} {new_text}", True
                break

    # ── Case 1: Explicit continuation starter ────────────────────────────────
    # e.g. "now sort it", "using slicing", "optimize it", "make it iterative"
    if _CONTINUATION_RE.match(new_lower):
        return _build_merged_text(prev_q, new_text), True

    # ── Case 2: Pronoun reference — "it", "this", "that" at position 0 ───────
    # e.g. "it should also handle negatives", "this list needs to be sorted"
    if not is_long_question:
        first_word = new_lower.split()[0] if new_lower.split() else ''
        if first_word in PRONOUN_STARTERS:
            return _build_merged_text(prev_q, new_text), True

    # Guard: if new text starts with an explicit action verb, it's a standalone request
    _is_standalone_verb = bool(re.match(r'^(write|create|implement|define|build|generate|explain)\b', new_lower))

    if not is_long_question:
        # ── Case 3: Method/technique keyword in a short fragment ──────────────
        # e.g. "recursion", "using list comprehension"
        # Guard: "difference between Tuple and List?" ends with "?" → standalone
        # Guard: starts with explicit verb (e.g. "Write a code for decorators") → standalone
        if new_word_count < 7 and not _is_standalone_verb:
            for kw in METHOD_KEYWORDS:
                if kw in new_lower and not new_lower.endswith('?'):
                    needs_using = ("using" not in new_lower
                                   and not prev_lower.endswith((' to', ' by', ' with')))
                    base = f"{prev_q} using {new_text}" if needs_using else f"{prev_q} {new_text}"
                    return base, True

        # ── Case 4: Significant topic overlap (rephrasing / elaboration) ──────
        # e.g. "Find longest substring" → "Longest substring without repeating chars"
        if prev_words and new_words:
            shared = prev_words & new_words
            overlap_ratio = len(shared) / max(len(prev_words), 1)
            if overlap_ratio > 0.5:
                return new_text, True  # new phrasing is better; use it

        # ── Case 5: Data context + instruction task ───────────────────────────
        # e.g. "Create a list 1 to 10" → "find the odd numbers using slicing"
        # Guard: if new text is a standalone write/create request, don't merge
        if not _is_standalone_verb:
            is_prev_data = (any(c in prev_q for c in "[]{}()")
                            or "list" in prev_lower or "numbers" in prev_lower
                            or "array" in prev_lower or "string" in prev_lower)
            is_task = ("filter" in new_lower or "find" in new_lower or "sort" in new_lower
                       or "remove" in new_lower or "keep" in new_lower
                       or "odd" in new_lower or "even" in new_lower
                       or "calculate" in new_lower or "count" in new_lower
                       or "write" in new_lower or "implement" in new_lower)
            if is_prev_data and is_task:
                return _build_merged_text(prev_q, new_text), True

    return new_text, False


def clear_context():
    """Clear fragment context (fresh start)."""
    global _context_cache
    _context_cache = None
    try:
        if CONTEXT_FILE.exists():
            CONTEXT_FILE.unlink()
    except Exception:
        pass
