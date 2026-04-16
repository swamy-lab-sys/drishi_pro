"""
Scenario tests — real interview situation simulations.

Covers:
  A. Validator: technical questions (Python, AWS, Django, SQL, System Design, HR)
  B. Validator: garbage / noise / interviewer narration
  C. Validator: STT mishears (sarvam/whisper corrections)
  D. Fragment merge: continuation, pronoun, project narration
  E. Incomplete context merge (slow speaker)
  F. Split merged questions
  G. /api/ask end-to-end (DB hit, cache, LLM async)
  H. DB lookup: python, cloud, SQL, system design roles
"""

import sys, time, re, json, os, urllib.request, urllib.parse, http.cookiejar
sys.path.insert(0, "/home/venkat/Drishi")
from dotenv import load_dotenv
load_dotenv("/home/venkat/Drishi/.env")

G = "\033[32m✓\033[0m"
R = "\033[31m✗\033[0m"
W = "\033[33m⚠\033[0m"
B = "\033[1;36m"
E = "\033[0m"

_results = []

def ok(label, cond, detail=""):
    _results.append(cond)
    sym = G if cond else R
    print(f"  {sym} {label}" + (f"  [{detail}]" if detail else ""))
    return cond

def sec(title):
    print(f"\n{B}{'─'*62}{E}\n{B}  {title}{E}\n{B}{'─'*62}{E}")

# ──────────────────────────────────────────────────────────────────────────────
# A. TECHNICAL QUESTIONS — must PASS
# ──────────────────────────────────────────────────────────────────────────────
sec("A. TECHNICAL QUESTIONS — must PASS")

from question_validator import validate_question

TECHNICAL = [
    # Python
    ("What is a decorator in Python?",                          "python"),
    ("Explain *args and **kwargs.",                             "python"),
    ("How does the GIL work?",                                  "python"),
    ("What is a generator vs iterator?",                        "python"),
    ("Write a function to find duplicates in a list.",          "python"),
    ("What is the difference between deepcopy and copy?",       "python"),
    ("How does asyncio event loop work?",                       "python"),
    ("Explain metaclass in Python.",                            "python"),
    ("What is a context manager?",                              "python"),
    ("How does list comprehension differ from a generator?",    "python"),
    # Django / DRF
    ("How does Django ORM handle N+1 queries?",                 "django"),
    ("What is select_related vs prefetch_related?",             "django"),
    ("How does DRF serializer validation work?",                "django"),
    ("What is Django middleware?",                              "django"),
    ("Explain Django signals.",                                 "django"),
    # AWS / Cloud
    ("What is AWS Lambda?",                                     "aws"),
    ("Explain the difference between SQS and SNS.",             "aws"),
    ("How does S3 lifecycle policy work?",                      "aws"),
    ("What is an IAM role vs IAM user?",                        "aws"),
    ("How does EC2 auto scaling work?",                         "aws"),
    ("Write a boto3 script to list S3 buckets.",                "aws"),
    # SQL
    ("What is the difference between INNER JOIN and LEFT JOIN?","sql"),
    ("Explain window functions in SQL.",                        "sql"),
    ("What is a CTE?",                                          "sql"),
    ("How does indexing improve query performance?",            "sql"),
    ("What is ACID compliance?",                                "sql"),
    # System Design
    ("How would you design a rate limiter?",                    "system"),
    ("Explain consistent hashing.",                             "system"),
    ("What is the CAP theorem?",                                "system"),
    ("How does a message queue improve scalability?",           "system"),
    ("Design a URL shortener like bit.ly.",                     "system"),
    # HR / Behavioral
    ("Tell me about yourself.",                                 "hr"),
    ("What are your strengths and weaknesses?",                 "hr"),
    ("Tell me about a challenging project.",                    "hr"),
    ("Why are you looking for a change?",                       "hr"),
    ("How do you handle production incidents?",                 "hr"),
    ("Tell me about your experience with FastAPI.",             "hr"),
]

fn = 0
for q, category in TECHNICAL:
    valid, _, reason = validate_question(q)
    passed = ok(f"[{category:7s}] {q[:55]}", valid, reason if not valid else "")
    if not valid:
        fn += 1

print(f"\n  Technical pass rate: {len(TECHNICAL)-fn}/{len(TECHNICAL)}  (FN={fn})")

# ──────────────────────────────────────────────────────────────────────────────
# B. NOISE / GARBAGE / INTERVIEWER NARRATION — must REJECT
# ──────────────────────────────────────────────────────────────────────────────
sec("B. NOISE / GARBAGE / INTERVIEWER NARRATION — must REJECT")

NOISE = [
    # Audio artifacts / STT garbage
    ("ping,",                                                    "too_short"),
    ("TURN.",                                                    "too_short"),
    ("TURN. Step on it.",                                        "short_no_content"),
    ("S-C-S-T-E-L-A-N-G-O-M-P-S-T-U-S-T-U-S-T-U",            "hallucination"),
    ("OAM P-CSCF IMS core P-CSCF",                             "short_no_content"),
    # Filler / social
    ("Okay.",                                                    "any"),
    ("Thank you.",                                               "any"),
    ("Can you hear me?",                                         "any"),
    ("One moment.",                                              "any"),
    ("Let me think.",                                            "any"),
    ("Alright.",                                                 "any"),
    ("Hmm.",                                                     "any"),
    # Interview admin / logistics
    ("We will let you know.",                                    "any"),
    ("I'm done from my side.",                                   "any"),
    ("Thank you for your time.",                                 "any"),
    ("Any questions from your side?",                            "any"),
    ("Shall we continue the interview?",                         "any"),
    # Camera / setup
    ("Can you come on to the camera?",                           "any"),
    ("Your video is not clear.",                                 "any"),
    ("Please turn on your camera.",                              "any"),
    # Interviewer project narration (new fixes)
    ("We need to write a script for automation.",                "ignore_pattern"),
    ("We need to build a pricing tool for AWS.",                 "ignore_pattern"),
    ("That is our main A.M.",                                    "ignore_pattern"),
    ("That is our main automation task.",                        "ignore_pattern"),
    ("This is our core project.",                                "ignore_pattern"),
    ("Actually, it is not a project.",                           "ignore_pattern"),
    ("Our team is working on automation.",                       "ignore_pattern"),
    ("Our main requirement is AWS pricing.",                     "ignore_pattern"),
    # Interviewer meta-talk
    ("Let's start with the first question.",                     "any"),
    ("Moving on to the next topic.",                             "any"),
    ("Good answer. Let's continue.",                             "any"),
    ("That's correct.",                                          "any"),
    ("We're going to write a function to find even numbers.",    "any"),
]

fp = 0
for q, expected_reason in NOISE:
    valid, _, reason = validate_question(q)
    passed = ok(f"{q[:60]}", not valid, f"reason={reason}")
    if valid:
        fp += 1

print(f"\n  Noise rejection rate: {len(NOISE)-fp}/{len(NOISE)}  (FP={fp})")

# ──────────────────────────────────────────────────────────────────────────────
# C. STT MISHEAR CORRECTIONS — should correct AND pass
# ──────────────────────────────────────────────────────────────────────────────
sec("C. STT MISHEAR CORRECTIONS — correct then PASS")

from question_validator import apply_stt_corrections

CORRECTIONS = [
    # (raw_stt, expected_substring_after_correction)
    ("What is jungle ORM?",                    "Django"),
    ("Explain arcs and coax in Python.",       "kwargs"),
    ("What is the gill in Python?",            "GIL"),
    ("What is gill in Python?",                "GIL"),
    ("What is python kill?",                   "GIL"),
    ("What is the difference between CI CD?",  "CI/CD"),
    ("Explain kacd pipeline.",                 "CI/CD"),
    ("What is fast ab framework?",             "FastAPI"),
    ("What is fastag?",                        "FastAPI"),
    ("What is salary in Django?",              "Celery"),
    ("What is the advantage of salary?",       "Celery"),
    ("How does post grass work?",              "PostgreSQL"),
    ("What is a degradator in Python?",        "decorator"),
    ("What are degraders?",                    "decorator"),
    ("What is auto seasoning in Kubernetes?",  "autoscaling"),
]

for raw, expected_sub in CORRECTIONS:
    corrected = apply_stt_corrections(raw)
    found = expected_sub.lower() in corrected.lower()
    ok(f"{raw[:50]}", found, f"→ {corrected[:50]}" if not found else f"→ {corrected[:40]}")

# ──────────────────────────────────────────────────────────────────────────────
# D. FRAGMENT MERGE SCENARIOS
# ──────────────────────────────────────────────────────────────────────────────
sec("D. FRAGMENT MERGE SCENARIOS")

import fragment_context

def setup_context(question):
    fragment_context.save_context(question, "voice")
    time.sleep(0.02)

def _run_merge(new_text, should_merge, label, setup_q=None):
    if setup_q:
        setup_context(setup_q)
    merged_text, was_merged = fragment_context.merge_with_context(new_text)
    result = ok(label, was_merged == should_merge,
                f"{'MERGED' if was_merged else 'NOT MERGED'}")
    if was_merged and was_merged == should_merge:
        print(f"       → \"{merged_text[:80]}\"")
    return result

PREV_CODING = "Write a function to find the maximum element in a list."
PREV_AWS    = "Is there any pricing related AWS. We need to write a script for automation."
PREV_LONG   = "Explain how Django ORM select_related works and when to use it over prefetch_related in a production scenario."

# Continuation merges — SHOULD merge
setup_context(PREV_CODING)
_run_merge("using recursion",                  True,  "continuation: 'using recursion'")
setup_context(PREV_CODING)
_run_merge("now sort it in ascending order",   True,  "continuation: 'now sort it'")
setup_context(PREV_CODING)
_run_merge("it should also handle negatives",  True,  "pronoun: 'it should handle'")
setup_context(PREV_CODING)
_run_merge("optimize it for O(n)",             True,  "continuation: 'optimize it'")
setup_context(PREV_CODING)
_run_merge("in Java",                          True,  "language qualifier: 'in Java'")

# Interviewer project narration — must NOT merge
setup_context(PREV_AWS)
_run_merge("That is our main A.M.",            False, "interviewer narration: 'That is our main'")
setup_context(PREV_AWS)
_run_merge("This is our automation task.",     False, "interviewer narration: 'This is our task'")
setup_context(PREV_LONG)
_run_merge("That is our core use case.",       False, "interviewer narration after long answer")

# Standalone new questions — must NOT merge
setup_context(PREV_CODING)
_run_merge("What is the GIL in Python?",       False, "standalone new question")
setup_context(PREV_CODING)
_run_merge("Write a binary search function.",  False, "standalone write request")
setup_context(PREV_CODING)
_run_merge("Explain decorators.",              False, "standalone new topic")

# I/first-person — must NOT merge
setup_context(PREV_CODING)
_run_merge("I meant without built-in functions.", False, "first-person clarification")

# ──────────────────────────────────────────────────────────────────────────────
# E. INCOMPLETE CONTEXT MERGE (slow speaker)
# ──────────────────────────────────────────────────────────────────────────────
sec("E. INCOMPLETE CONTEXT MERGE (slow speaker mid-sentence pause)")

fragment_context.clear_incomplete_context()

SLOW_SPEAKER = [
    # (incomplete_fragment, continuation, expected_contains)
    ("What is the difference between",  "list and tuple in Python?",    "difference between list"),
    ("How does Django ORM",              "handle N+1 queries?",          "Django ORM"),
    ("Explain the concept of",           "polymorphism with an example.", "polymorphism"),
    ("Write a function to",              "find all prime numbers.",       "prime"),
]

for incomplete, cont, expected in SLOW_SPEAKER:
    fragment_context.save_incomplete_context(incomplete)
    merged, was_merged = fragment_context.merge_with_context(cont)
    has_expected = expected.lower() in merged.lower()
    ok(f"'{incomplete}' + '{cont[:25]}'", was_merged and has_expected,
       f"→ {merged[:60]}" if not has_expected else "")
    fragment_context.clear_incomplete_context()

# ──────────────────────────────────────────────────────────────────────────────
# F. SPLIT MERGED QUESTIONS
# ──────────────────────────────────────────────────────────────────────────────
sec("F. SPLIT MERGED QUESTIONS (long STT chunk)")

from question_validator import split_merged_questions

SPLIT_CASES = [
    # (raw_stt_chunk, expected_substring_in_result)
    (
        "Okay so what is a decorator? And how does it work in Python?",
        "decorator"
    ),
    (
        "Actually, it is not a project. It is automation tasks. Is there any AWS pricing script we need?",
        "AWS"
    ),
    (
        "One moment. What is the difference between list and tuple?",
        "difference"
    ),
    (
        "What is the GIL in Python?",
        "GIL"
    ),
    (
        "Hello, good morning. How does asyncio event loop work?",
        "asyncio"
    ),
]

for raw, expected in SPLIT_CASES:
    result = split_merged_questions(raw)
    found = expected.lower() in result.lower()
    ok(f"Split: '{raw[:55]}'", found, f"→ '{result[:60]}'")

# ──────────────────────────────────────────────────────────────────────────────
# G. /api/ask END-TO-END
# ──────────────────────────────────────────────────────────────────────────────
sec("G. /api/ask END-TO-END SCENARIOS")

_cj = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cj))

admin_pw = os.environ.get("ADMIN_PASSWORD", "")
if admin_pw:
    data = urllib.parse.urlencode({"password": admin_pw}).encode()
    req = urllib.request.Request("http://localhost:8000/login", data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try: _opener.open(req, timeout=5)
    except: pass

def ask(q, db_only=False, timeout=10):
    body = json.dumps({"question": q, "db_only": db_only}).encode()
    req = urllib.request.Request("http://localhost:8000/api/ask", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with _opener.open(req, timeout=timeout) as r:
            ms = (time.perf_counter() - t0) * 1000
            return json.loads(r.read()), ms
    except Exception as e:
        return {"error": str(e)}, (time.perf_counter() - t0) * 1000

API_SCENARIOS = [
    # (label, question, db_only, max_ms, check_source)
    ("Python DB  — decorator",           "What is a Python decorator?",               True,  100, "db"),
    ("Python DB  — GIL",                 "What is the GIL in Python?",               True,  50,  "db"),
    ("Python DB  — generator",           "What is a generator in Python?",            True,  50,  "db"),
    ("AWS DB     — Lambda",              "Tell me about AWS lambda function.",        True,  50,  "db"),
    ("AWS DB     — S3",                  "What is AWS S3?",                          True,  50,  "db"),
    ("Django DB  — ORM",                 "How does Django ORM work?",                True,  50,  "db"),
    ("SQL DB     — joins",               "What is the difference between inner and left join?", True, 50, "db"),
    ("Cache      — decorator repeat",    "What is a Python decorator?",              False, 50,  "db"),
    ("Cache      — GIL repeat",          "What is the GIL in Python?",               False, 50,  "db"),
    ("LLM async  — novel question",      "How does Python walrus operator work?",    False, 500, "llm"),
]

print()
for label, q, db_only, max_ms, exp_src in API_SCENARIOS:
    resp, ms = ask(q, db_only=db_only)
    src    = resp.get("source", "?")
    ans    = resp.get("answer", "")
    status = resp.get("status", "")
    # LLM async: "generating" is correct behaviour
    has_ans = len(ans.strip()) > 10 or status == "generating"
    fast    = ms < max_ms
    passed  = has_ans and fast
    sym     = G if passed else (W if has_ans and not fast else R)
    _results.append(passed)
    async_note = " [async→SSE]" if status == "generating" else ""
    flag = f" ⚠ slow (>{max_ms}ms)" if not fast else ""
    print(f"  {sym} {label:32s}  {ms:5.0f}ms [{src}]{flag}{async_note}")
    if not has_ans:
        print(f"       ERROR: {resp.get('error', resp)}")

# ──────────────────────────────────────────────────────────────────────────────
# H. DB LOOKUP BY ROLE
# ──────────────────────────────────────────────────────────────────────────────
sec("H. DB LOOKUP — multi-role coverage")

import qa_database

ROLE_QUERIES = [
    ("What is a decorator?",                        "python",        True),
    ("What is the GIL?",                            "python",        True),
    ("Explain generators.",                          "python",        True),
    ("What is AWS Lambda?",                          "general",       True),
    ("What is SQS?",                                "general",       True),
    ("How does Django ORM work?",                   "python",        True),
    ("What is DRF serializer?",                     "python",        True),
    ("What is INNER JOIN?",                         "sql",           True),
    ("What is the difference between list and tuple?","python",      True),
    ("Tell me about polymorphism.",                  "python",        True),
    ("What is a context manager?",                   "python",        True),
    # Should MISS (novel/niche questions not in DB)
    ("How does Python walrus operator work?",        "python",        False),  # might miss
    ("Explain Bezier curve algorithm.",              "python",        False),
]

hits = misses = 0
for q, role, expect_hit in ROLE_QUERIES:
    t0 = time.perf_counter()
    result = qa_database.find_answer(q, want_code=False, user_role=role)
    ms = (time.perf_counter() - t0) * 1000
    hit = result is not None
    if expect_hit:
        passed = ok(f"[{role:7s}] {q[:45]}", hit, f"{'HIT' if hit else 'MISS'} {ms:.1f}ms")
        hits += (1 if hit else 0)
    else:
        # for "expect miss" just report without failing
        sym = W
        _results.append(True)  # don't penalise expected misses
        print(f"  {sym} [expected miss] [{role:7s}] {q[:45]}  [{'HIT' if hit else 'MISS'} {ms:.1f}ms]")

# ──────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ──────────────────────────────────────────────────────────────────────────────
sec("SUMMARY")

total   = len(_results)
passed  = sum(_results)
pct     = passed / total * 100 if total else 0
color   = "\033[32m" if pct >= 95 else ("\033[33m" if pct >= 85 else "\033[31m")

total_v = len(TECHNICAL) + len(NOISE)
v_pass  = (len(TECHNICAL) - fn) + (len(NOISE) - fp)
v_pct   = v_pass / total_v * 100

print(f"""
  Overall:    {color}{passed}/{total} checks ({pct:.0f}%)\033[0m
  Validator:  {v_pass}/{total_v} ({v_pct:.0f}%)  — FP={fp} FN={fn}
  Technical:  {len(TECHNICAL)-fn}/{len(TECHNICAL)} pass
  Noise rej:  {len(NOISE)-fp}/{len(NOISE)} rejected
""")

if pct < 90:
    sys.exit(1)
