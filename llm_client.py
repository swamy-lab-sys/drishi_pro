"""
LLM Client for Drishi Pro

Optimized for:
- Minimal token usage (short system prompt)
- No conversation history (fresh session per question)
- Fast responses via streaming
- 10 second hard timeout
"""

import os
import re
import time
from anthropic import Anthropic

# Only override ANTHROPIC_API_KEY from .env — do NOT override other env vars
# (PULSE_SOURCE and others are set by run.sh and must not be overwritten)
try:
    from dotenv import dotenv_values as _dotenv_values
    _env_vals = _dotenv_values()
    if _env_vals.get('ANTHROPIC_API_KEY'):
        os.environ['ANTHROPIC_API_KEY'] = _env_vals['ANTHROPIC_API_KEY']
except ImportError:
    pass

try:
    import debug_logger as dlog
except ImportError:
    class DlogStub:
        def log(self, *args, **kwargs): pass
        def log_error(self, *args, **kwargs): pass
    dlog = DlogStub()

client = Anthropic(
    api_key=os.environ.get("ANTHROPIC_API_KEY"),
    max_retries=1,   # 1 retry on transient 5xx — adds at most one extra attempt
    timeout=10.0     # 10s per attempt; 1 retry = 20s max total (acceptable)
)

import config as _cfg
MODEL = os.environ.get("LLM_MODEL_OVERRIDE", _cfg.LLM_MODEL)

MAX_TOKENS_INTERVIEW = 90     # 3 bullets × ~15 words — baseline
MAX_TOKENS_INTERVIEW_SIMPLE = 65  # Short factual questions: definition only, no code example needed
MAX_TOKENS_INTERVIEW_COMMANDS = 120  # Production support / Linux roles: commands need more space
MAX_TOKENS_INTERVIEW_EXPLAIN = 180   # Concept questions: definition + code example + personal use
MAX_TOKENS_INTERVIEW_COMPLEX = 220   # System design / architecture questions
MAX_TOKENS_CODING = 700       # Python/Java/JS code — bumped for multi-method Java answers
MAX_TOKENS_CODING_INFRA = 950 # Ansible/Terraform/Dockerfile/Jenkinsfile/K8s manifests
MAX_TOKENS_PLATFORM = 2500

# Roles that typically answer with commands/code in bullets — need slightly more tokens
_COMMAND_HEAVY_ROLES = {
    'production support', 'prod support', 'support engineer', 'linux admin', 'sysadmin',
    'system administrator', 'unix admin', 'infrastructure support', 'openstack',
    'devops', 'sre', 'site reliability', 'platform engineer',
    'autosys', 'batch', 'etl', 'java developer', 'java engineer',
}
# Pre-compiled regex — replaces O(n) loop over role keys per question
_COMMAND_HEAVY_RE = re.compile(
    '|'.join(re.escape(r) for r in _COMMAND_HEAVY_ROLES), re.IGNORECASE
)

# Question prefixes that indicate "explain this concept" — need definition + code example
_EXPLAIN_PREFIXES = (
    'what is ', 'what are ', 'explain ', 'describe ', 'how does ', 'how do ',
    'what does ', 'define ', 'tell me about ', 'can you explain ',
)

# Keywords that signal a complex, multi-part answer is needed
_COMPLEX_KEYWORDS_RE = re.compile(
    r'\b(design|architect|system design|scalab|distributed|microservice|'
    r'difference between|compare|vs\b|trade.?off|pros.{0,5}cons|'
    r'when would you|how would you implement|walk me through)\b',
    re.IGNORECASE
)

def _is_explain_question(question: str) -> bool:
    """True if the question is asking for an explanation/definition rather than a task."""
    q = question.lower().strip()
    return any(q.startswith(p) for p in _EXPLAIN_PREFIXES)

# Interview round parameters — override token budget and temperature
_ROUND_PARAMS = {
    "hr":     {"max_tokens": 80,  "temperature": 0.30,
               "style": "Answer conversationally in first person. No bullet points. Use STAR method for behavioral questions. Keep under 80 words."},
    "tech":   {"max_tokens": None, "temperature": None, "style": None},  # default logic
    "design": {"max_tokens": 500, "temperature": 0.20,
               "style": "Structure your answer: 1) Architecture overview, 2) Key design decision with trade-off, 3) Scalability approach. Each point up to 40 words."},
    "code":   {"max_tokens": 700, "temperature": 0.15, "style": None},   # uses coding prompt
}


def _get_round_params() -> dict:
    """Return current round parameters from config."""
    try:
        import config as _cfg
        return _ROUND_PARAMS.get(getattr(_cfg, 'INTERVIEW_ROUND', 'tech'), _ROUND_PARAMS['tech'])
    except Exception:
        return _ROUND_PARAMS['tech']


def _get_interview_token_budget(active_user_context: str = "", question: str = "") -> int:
    """Return the right token budget for interview answers based on round and question type."""
    # Round override takes priority
    rp = _get_round_params()
    if rp.get("max_tokens") is not None:
        return rp["max_tokens"]

    if not question:
        return MAX_TOKENS_INTERVIEW

    q = question.strip()
    word_count = len(q.split())

    # Complex/design questions need longer answers
    if _COMPLEX_KEYWORDS_RE.search(q):
        return MAX_TOKENS_INTERVIEW_COMPLEX

    # Concept/explain questions get more tokens for code examples
    if _is_explain_question(q):
        return MAX_TOKENS_INTERVIEW_EXPLAIN

    # Short direct questions (≤6 words, e.g. "What is a decorator?") — 3 tight bullets
    if word_count <= 6:
        return MAX_TOKENS_INTERVIEW_SIMPLE

    if active_user_context and _COMMAND_HEAVY_RE.search(active_user_context):
        return MAX_TOKENS_INTERVIEW_COMMANDS

    return MAX_TOKENS_INTERVIEW

# Keywords that identify infra/script requests needing higher token budget
_INFRA_KEYWORDS = {
    # Infrastructure as code
    "ansible", "playbook", "terraform", "jenkinsfile", "pipeline",
    "dockerfile", "docker-compose", "docker compose", "helm", "kubernetes",
    "manifest", "yaml", "k8s", "groovy", "bash script", "shell script",
    # Java — verbose, needs more tokens
    "spring boot", "springboot", "hibernate", "jpa", "servlet",
    "executorservice", "threadpoolexecutor", "completablefuture",
    "design pattern", "singleton", "factory", "builder pattern",
    # JavaScript/Node — also verbose
    "express", "middleware", "node.js", "nodejs", "react component",
    "redux", "next.js", "webpack config", "eslint config",
    # SQL — complex queries
    "window function", "cte", "common table", "materialized view",
    "stored procedure", "trigger", "pl/pgsql", "plpgsql",
    # Django / Flask
    "django model", "serializer", "viewset", "migration",
    "flask blueprint", "sqlalchemy model",
    # K8s / OpenStack
    "kubernetes manifest", "k8s manifest", "deployment yaml",
    "openstack", "nova", "neutron", "terraform module",
    # Cloud
    "cloudformation", "aws lambda function", "iam policy",
}

TEMP_INTERVIEW = 0.15   # Lower = faster sampling + more deterministic answers
TEMP_CODING = 0.1

INTERVIEW_PROMPT = """You are a senior engineer answering live in a job interview. Speak as the person from the RESUME.

FORMAT: Exactly 3 bullet points. Each bullet = one direct sentence, max 16 words. Total under 75 words.
At least one bullet must start with "I" — your real personal usage or experience.

CONCEPT QUESTIONS (what is / explain / how does):
  Bullet 1: What it IS — one clear sentence definition
  Bullet 2: Code example inline in backticks (short, real, practical)
  Bullet 3: When/why YOU use it — personal experience

TONE: Sound like a real person speaking, not writing. Use contractions (it's, I've, you'd, don't, that's).

━━━ LINUX / SHELL COMMAND RULE ━━━
For any Linux command question, put the actual command in backticks in the FIRST bullet.

━━━ RULES ━━━
- EXACTLY 3 bullets. NO sub-bullets. NO numbered lists.
- NO colons inside bullets. Say it directly.
- NO markdown bold. NO code blocks. Use inline backticks for code snippets and shell commands.
- BANNED WORDS: essentially, fundamentally, primarily, utilize, moreover, furthermore,
  additionally, consequently, thus, therefore, leverages, facilitates, notwithstanding.
- USE INSTEAD: basically, mainly, use, also, so, but, to, lets you, helps.
- Only claim hands-on experience with tech in YOUR RESUME. For other tech say "I've read about it".
- If the question is HR/general (strengths, weaknesses, salary, notice period), answer naturally from the resume.
- NEVER reveal you are an AI."""

# All Q&A examples live in qa_database (tagged by role/domain).
# DB is checked BEFORE the LLM — common questions get instant answers (< 5ms).
# LLM is only called for novel questions not in the DB.



PLATFORM_PROMPT = """You are an expert competitive programmer. Solve the coding problem completely.

LANGUAGE DETECTION (check EDITOR_CONTENT):
- If editor has Java (class Solution, public static, int[], etc.) → output Java
- If editor has JavaScript/TypeScript (const, let, function, =>) → output JavaScript
- If editor has C++ (#include, vector<, auto) → output C++
- Otherwise → output Python 3

THINK BEFORE CODING (internal only, do NOT output):
- Read constraints carefully (N<10^5 = O(N log N), N<10^3 = O(N^2) ok)
- Identify algorithm: DP, BFS, DFS, Two-pointer, Binary search, Greedy, etc.
- Handle ALL edge cases: empty input, single element, min/max values, duplicates
- For HackerRank/stdin problems: read input with input()/sys.stdin, print() for output
- Test mentally against all given examples before writing

STRICT OUTPUT RULES:
1. ONLY raw code — zero explanations, zero markdown, zero backticks
2. FIRST CHARACTER must be the start of actual code (def/class/import/public)
3. NO comments of any kind — no #, no //, no /* */
4. NO debug print statements
5. Use EXACT function/class name from EDITOR_CONTENT if provided
6. 4-space indentation for Python; standard for Java/JS
7. LeetCode/Codewars: function/class only, no main()
8. HackerRank/Codility/Programiz: include stdin reading + print output
9. Solution must pass ALL test cases including edge cases

WORD-TO-NUMBER CONVERSION (parse_int, words_to_int, etc.):
Use this EXACT pattern with PROPER INDENTATION:

def parse_int(string):
    nums = {'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
            'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
            'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14,
            'fifteen': 15, 'sixteen': 16, 'seventeen': 17, 'eighteen': 18,
            'nineteen': 19, 'twenty': 20, 'thirty': 30, 'forty': 40,
            'fifty': 50, 'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90}
    words = string.replace('-', ' ').replace(' and ', ' ').split()
    total, current = 0, 0
    for word in words:
        if word in nums:
            current += nums[word]
        elif word == 'hundred':
            current *= 100
        elif word == 'thousand':
            current *= 1000
            total += current
            current = 0
        elif word == 'million':
            current *= 1000000
            total += current
            current = 0
        elif word == 'billion':
            current *= 1000000000
            total += current
            current = 0
    return total + current

KEY ALGORITHM INSIGHTS:
- "hundred": MULTIPLY current (current *= 100), do NOT add to total
- "thousand"/"million": SCALE then ADD to total, then RESET current
- This handles: "seven hundred eighty-three thousand" = (7*100+83)*1000 = 783000

OTHER PATTERNS:
- Array/List problems: Handle empty arrays, single element, negative numbers
- String problems: Handle empty string, single char, whitespace
- Math problems: Handle zero, negative, large numbers

REMEMBER: Every line inside a function MUST start with 4 spaces of indentation!
Output clean Python code only."""


import re

# Pre-compiled AI opener patterns
_OPENER_PATTERNS = [
    re.compile(r"^Sure,?\s*", re.IGNORECASE),
    re.compile(r"^(Great|Good|Excellent|Nice) question[.!,]?\s*", re.IGNORECASE),
    re.compile(r"^That's a (great|good|excellent) question[.!,]?\s*", re.IGNORECASE),
    re.compile(r"^Let me explain[.!,:]?\s*", re.IGNORECASE),
    re.compile(r"^(Certainly|Absolutely|Of course)[.!,]?\s*", re.IGNORECASE),
    re.compile(r"^Here'?s?\s*(the|my|a)?\s*(answer|explanation|breakdown|overview)?[.!,:]?\s*", re.IGNORECASE),
    re.compile(r"^(So|Well|Okay|Ok),?\s+", re.IGNORECASE),
    re.compile(r"^(I'd be happy to|Let me share|Allow me to|I'd like to)[.!,:]?\s*", re.IGNORECASE),
    re.compile(r"^Okay,?\s*(got it|understood|sure)[.!,]?\s*", re.IGNORECASE),
    re.compile(r"^(Ah,?\s*)?(I apologize|I'm sorry|My apologies)[^.]*\.\s*", re.IGNORECASE),
    re.compile(r"^Here are (some of )?(the )?(main |key )?(features|characteristics|benefits|advantages)[^:]*:\s*", re.IGNORECASE),
    re.compile(r"^(Let me|I'll) (provide|give) (you )?(a |an )?(general )?(overview|explanation|breakdown)[^.]*[.:]\s*", re.IGNORECASE),
    re.compile(r"^(Let'?s|Let me) explain[^:]*[:.]?\s*", re.IGNORECASE),
    re.compile(r"^(A |Here'?s a )?(concise|brief|short|quick) (explanation|overview|summary)[^:]*[:.]?\s*", re.IGNORECASE),
    re.compile(r"^Imagine[^.]*[.:]\s*", re.IGNORECASE),
    re.compile(r"^Think of[^.]*[.:]\s*", re.IGNORECASE),
    re.compile(r"^In (simple|plain) terms[,:]?\s*", re.IGNORECASE),
]

# Pre-compiled formatting patterns
_BOLD_RE = re.compile(r'\*\*(.+?)\*\*')
_HEADER_RE = re.compile(r'^#{1,4}\s+', re.MULTILINE)
_BULLET_RE = re.compile(r'^\s*[-*•]\s+', re.MULTILINE)
_NUMBERED_RE = re.compile(r'^\s*\d+\.\s+', re.MULTILINE)
_BOLD_HEADER_RE = re.compile(r'^\s*\*\*[^*]+\*\*\s*:?\s*$', re.MULTILINE)

_CODE_BLOCK_RE = re.compile(r'```[\s\S]*?(?:```|$)')
_INLINE_CODE_RE = re.compile(r'`([^`]+)`')
_HERES_EXAMPLE_RE = re.compile(r"Here'?s?\s*(an?\s*)?(simple\s*)?(example|code)[^:]*:\s*", re.IGNORECASE)
_FOR_EXAMPLE_RE = re.compile(r"For example[,:]?\s*", re.IGNORECASE)
_KEY_POINTS_RE = re.compile(r"Here are (the |some )?(key |main )?(points|things|features|characteristics)[^:]*:\s*", re.IGNORECASE)

# Pre-compiled word-level humanization replacements
# Applied after bullet stripping so they target spoken-style output
_HUMANIZE_WORDS = [
    # Overly formal → conversational
    (re.compile(r'\bessentially\b', re.IGNORECASE), 'basically'),
    (re.compile(r'\bfundamentally\b', re.IGNORECASE), 'at its core'),
    (re.compile(r'\bprimarily\b', re.IGNORECASE), 'mainly'),
    (re.compile(r'\butilize\b', re.IGNORECASE), 'use'),
    (re.compile(r'\butilization\b', re.IGNORECASE), 'usage'),
    (re.compile(r'\butilized\b', re.IGNORECASE), 'used'),
    (re.compile(r'\bfacilitate[sd]?\b', re.IGNORECASE), 'help'),
    (re.compile(r'\bwhereas\b', re.IGNORECASE), 'while'),
    (re.compile(r'\bthus\b', re.IGNORECASE), 'so'),
    (re.compile(r'\btherefore\b', re.IGNORECASE), 'so'),
    (re.compile(r'\bmoreover\b', re.IGNORECASE), 'also'),
    (re.compile(r'\bfurthermore\b', re.IGNORECASE), 'also'),
    (re.compile(r'\badditionally\b', re.IGNORECASE), 'also'),
    (re.compile(r'\bconsequently\b', re.IGNORECASE), 'so'),
    (re.compile(r'\bnevertheless\b', re.IGNORECASE), 'but'),
    (re.compile(r'\bleverages?\b', re.IGNORECASE), 'uses'),
    (re.compile(r'\bleveraging\b', re.IGNORECASE), 'using'),
    (re.compile(r'\bin order to\b', re.IGNORECASE), 'to'),
    (re.compile(r'\bso as to\b', re.IGNORECASE), 'to'),
    (re.compile(r'\bdue to the fact that\b', re.IGNORECASE), 'because'),
    (re.compile(r'\bprovides the ability to\b', re.IGNORECASE), 'lets you'),
    (re.compile(r'\ballows you to\b', re.IGNORECASE), 'lets you'),
    (re.compile(r'\ballows for\b', re.IGNORECASE), 'enables'),
    (re.compile(r'\bprovide[sd]? a way to\b', re.IGNORECASE), 'lets you'),
    (re.compile(r'\bone of the key\b', re.IGNORECASE), 'a key'),
    (re.compile(r'\bone of the main\b', re.IGNORECASE), 'a main'),
    (re.compile(r'\bone of the most important\b', re.IGNORECASE), 'an important'),
    # "I have studied" → casual
    (re.compile(r"\bI have studied\b", re.IGNORECASE), "I've read about"),
    (re.compile(r"\bI am familiar with\b", re.IGNORECASE), "I know"),
    (re.compile(r"\bI am not familiar with\b", re.IGNORECASE), "I haven't used"),
    # Remove verbose filler phrases entirely
    (re.compile(r'\bIt is worth noting that\b', re.IGNORECASE), ''),
    (re.compile(r'\bIt is important to note that\b', re.IGNORECASE), ''),
    (re.compile(r'\bIt should be noted that\b', re.IGNORECASE), ''),
    (re.compile(r'\bKeep in mind that\b', re.IGNORECASE), ''),
    (re.compile(r'\bIn summary[,:]?\s*', re.IGNORECASE), ''),
    (re.compile(r'\bIn conclusion[,:]?\s*', re.IGNORECASE), ''),
    (re.compile(r'\bOverall[,:]?\s+', re.IGNORECASE), ''),
    (re.compile(r'\bTo summarize[,:]?\s*', re.IGNORECASE), ''),
    # Replace overly precise technical hedges
    (re.compile(r'\bwhen working with\b', re.IGNORECASE), 'when using'),
    (re.compile(r'\bwhen dealing with\b', re.IGNORECASE), 'when handling'),
    (re.compile(r'\bwhen it comes to\b', re.IGNORECASE), 'for'),
    # Expand contractions that LLM skips (rare but possible)
    (re.compile(r'\bI have\b(?= used| been| built| worked| deployed| implemented)', re.IGNORECASE), "I've"),
    (re.compile(r'\bIt is\b(?= used| a | an | the | faster| better| easier)', re.IGNORECASE), "It's"),
    (re.compile(r'\bYou can\b', re.IGNORECASE), "You can"),  # keep as-is (already natural)
]

# Pre-compiled AI identity leak patterns (compiled once at module load)
_AI_LEAK_PATTERNS = [re.compile(p, re.IGNORECASE) for p in [
    r"As an AI[^.]*\.\s*",
    r"I am an AI[^.]*\.\s*",
    r"I'?m an (AI|artificial)[^.]*\.\s*",
    r"I don'?t have a physical[^.]*\.\s*",
    r"I do not have a physical[^.]*\.\s*",
    r"created by Anthropic[^.]*\.\s*",
    r"I am an artificial intelligence[^.]*\.\s*",
    r"without a physical form[^.]*\.\s*",
    r"I cannot (participate|appear|be recorded)[^.]*\.\s*",
    r"I can only (respond|communicate|provide)[^.]*text[^.]*\.\s*",
    r"I (apologize|'m sorry|am sorry),?\s*(but\s*)?(I\s*)?(do not|don'?t|am not|cannot)[^.]*\.\s*",
    r"I'?m afraid[^.]*\.\s*",
    r"there seems to be (a |some )?misunderstanding[^.]*\.\s*",
    r"without (any )?more (specific )?details[^.]*\.\s*",
    r"I do not (actually )?have (any |direct )?(experience|information|knowledge|expertise)[^.]*\.\s*",
    r"I don'?t have (any |direct )?(experience|information|knowledge|expertise)[^.]*\.\s*",
    r"I (apologize|'m sorry),?\s*I misunderstood[^.]*\.\s*",
    r"However,?\s*I can provide[^.]*\.\s*",
    r"Could you please provide more context[^.]*\.\s*",
    r"I'?m not sure which[^.]*\.\s*",
    r"I haven'?t had the (opportunity|need|chance)[^.]*\.\s*",
    r"As a developer with \d+ years? of experience,?\s*",
    r"In my experience (working with|as a)[^,]*,\s*",
]]

def humanize_response(text: str) -> str:
    """Strip AI-style formatting to produce spoken-style plain text."""
    if not text:
        return text
    # Remove entire code blocks first (```...```)
    text = _CODE_BLOCK_RE.sub('', text)
    # Keep inline backticks intact — the web frontend's renderInline() converts
    # `cmd` → <code class="ic">cmd</code>.  Stripping them here loses code styling
    # in the final completed card (the stream shows styled code, complete shows plain text).
    # Strip AI openers
    for pattern in _OPENER_PATTERNS:
        text = pattern.sub('', text)
    # Strip "Here's an example:" and similar
    text = _HERES_EXAMPLE_RE.sub('', text)
    text = _FOR_EXAMPLE_RE.sub('', text)
    text = _KEY_POINTS_RE.sub('', text)
    # Remove bold-only header lines (e.g., "**Infrastructure Automation**")
    text = _BOLD_HEADER_RE.sub('', text)
    # Remove markdown headers (## Header)
    text = _HEADER_RE.sub('', text)
    # Remove bold markers but keep inner text
    text = _BOLD_RE.sub(r'\1', text)
    # KEEP bullet point markers (- ) since we want bullet format output
    # But remove numbered list markers (1. 2. 3.)
    text = _NUMBERED_RE.sub('- ', text)
    # Remove "Topic: explanation" patterns (e.g., "Memory Efficiency: Generators...")
    text = re.sub(r'(?m)^\s*[A-Z][A-Za-z\s/]+:\s+', '', text)
    # Remove label-colon in bullet lines (e.g., "- API Server: Exposes..." → "- API Server exposes...")
    text = re.sub(r'(?m)^(\s*-\s+[A-Za-z\s/]+):\s+', r'\1 ', text)
    # Remove trailing "such as:", "including:", "for example:" at end of bullets
    text = re.sub(r',?\s*(such as|including|for example|e\.g\.)\s*:?\s*$', '.', text, flags=re.MULTILINE | re.IGNORECASE)
    # Remove sub-bullets (indented bullets like "  - item")
    text = re.sub(r'(?m)^\s{2,}-\s+.*$', '', text)
    # Remove label-colon patterns mid-sentence (e.g., "Iterability: Generators are...")
    text = re.sub(r'(?<=[.!?])\s+[A-Z][A-Za-z\s/]{2,30}:\s+', ' ', text)
    # Remove e.g. patterns
    text = re.sub(r'\s*\(e\.g\.?\s*[^)]*\)', '', text)
    # Remove "etc." trailing (require comma before to avoid destroying words like "etcd")
    text = re.sub(r',\s+etc\.?\s*', '. ', text)
    # Remove standalone syntax symbols like [], (), {}, __method__() patterns
    text = re.sub(r'\s*\[\]\s*', ' ', text)
    text = re.sub(r'\s*\(\)\s*', ' ', text)
    text = re.sub(r'\s*\{\}\s*', ' ', text)
    text = re.sub(r'__\w+__\(\)', '', text)  # __enter__(), __exit__(), __init__()
    text = re.sub(r'__\w+__', '', text)  # __init__, __str__, etc.
    # Remove "Note that..." filler sentences
    text = re.sub(r'\(Note that[^)]*\)', '', text)
    text = re.sub(r'Note that[^.]*\.\s*', '', text)
    # Remove "It is important to note..." filler
    text = re.sub(r'It is important to (note|understand|remember)[^.]*\.\s*', '', text, flags=re.IGNORECASE)
    # Collapse multiple newlines but preserve single newlines for bullet format
    text = re.sub(r'\n{3,}', '\n', text)
    text = re.sub(r'\n\n', '\n', text)
    # Fix missing space after leading dash (from LLM prefill "-" + token)
    text = re.sub(r'^-([^ \n])', r'- \1', text)
    # Collapse multiple spaces
    text = re.sub(r'  +', ' ', text)
    # Replace AI-sounding formal words with natural conversational equivalents
    for pattern, replacement in _HUMANIZE_WORDS:
        text = pattern.sub(replacement, text)
    # Remove AI identity leaks and "I don't have experience" patterns (pre-compiled)
    for pattern in _AI_LEAK_PATTERNS:
        text = pattern.sub("", text)
    # Collapse spaces and periods after removals
    text = re.sub(r'\.{2,}', '.', text)
    text = re.sub(r'  +', ' ', text)
    text = re.sub(r'^\s*[,.]\s*', '', text)
    text = text.strip()

    # Limit to max 4 bullet points (drop excess bullets)
    lines = text.split('\n')
    bullet_count = 0
    truncated_lines = []
    for line in lines:
        if line.strip().startswith('-'):
            bullet_count += 1
            if bullet_count > 4:
                break
        truncated_lines.append(line)
    text = '\n'.join(truncated_lines).strip()

    return text


def clear_session(session_id: str | None = None):
    """Clear conversation history for this session (or all sessions if session_id=None)."""
    if session_id is None:
        _SESSION_HISTORIES.clear()
        HISTORY.clear()
    else:
        _SESSION_HISTORIES.pop(session_id, None)


# ── Question Type Classifier ───────────────────────────────────────────────────

import re as _re

_BEHAVIORAL_RE = _re.compile(
    r'\b('
    # Classic behavioral openers
    r'tell me about (a time|yourself|your experience|a challenge|a situation)|'
    r'describe (a time|a situation|a challenge|a moment|an instance|a project|a scenario|your)|'
    r'give (me )?an example of|share an example|walk me through|'
    # How did/do/have/would you patterns
    r'how (did|do|have|would) you (handle|deal with|manage|approach|navigate|overcome|resolve|work with)|'
    r'have you (ever|previously|handled|dealt|worked with|managed|led|resolved|overcome)|'
    r'when (did|have|were) you|when you (had to|faced|dealt|worked|disagreed)|'
    # What would you do
    r'what would you do (if|when)|what (do|did|would) you do when|'
    # Soft-skill / HR keywords standing alone
    r'(strength|weakness|challenge|conflict|failure|mistake|achievement|'
    r'accomplishment|leadership|initiative|adaptab|teamwork|collaboration|'
    r'disagree|under pressure|handle stress|prioriti|influence|persuad|motivat|'
    r'difficult (coworker|colleague|manager|situation|client|team|person)|'
    r'constructive criticism|missed deadline|change your mind|'
    r'short.term goal|long.term goal)|'
    # Career / HR
    r'(salary|notice period|'
    r'why (do you want|should we hire|this (company|role|job|position)|are you leaving|did you leave)|'
    r'where do you see yourself|'
    r'biggest (mistake|regret|learning|challenge|achievement|failure)|'
    r'proud of|what drives you|passion|'
    r'greatest (strength|weakness|achievement|failure)|'
    r'work (style|ethic|environment|culture)|'
    r'team player|work (well|alone|independently|collaboratively))'
    r')\b',
    _re.IGNORECASE,
)

_SYSTEM_DESIGN_RE = _re.compile(
    r'\b('
    r'design (a|an|the)|architect(ure)?|'
    r'how would you (build|design|scale|architect|implement) (a|an|the)|'
    r'scalab(le|ility)|distributed (systems?|services?|databases?|caches?|queues?)|'
    r'high.availab(ility)?|load balanc|'
    r'(design|build|implement|create).{0,20}(systems?|services?|platforms?|databases?|api|pipeline|microservices?)|'
    r'how (would|do) you (approach|architect|scale|handle) .{0,30}(traffic|scale|load|million|billion)|'
    r'trade.?offs?|cap theorem|eventual consistency|sharding|replication|'
    r'message queues?|event.driven|caching strategy|cdn|rate limit(er|ing)|'
    r'microservices?|monolith|service mesh|api gateway'
    r')\b',
    _re.IGNORECASE,
)


def classify_question_type(question: str) -> str:
    """Classify question as: behavioral | system_design | coding | technical.
    Used to select the right answer prompt strategy.
    """
    q = (question or '').strip()
    if _BEHAVIORAL_RE.search(q):
        return 'behavioral'
    if _SYSTEM_DESIGN_RE.search(q):
        return 'system_design'
    try:
        from question_validator import is_code_request
        if is_code_request(q):
            return 'coding'
    except Exception:
        pass
    return 'technical'


# ── Extra Prompts for Behavioral / System-Design Questions ────────────────────

BEHAVIORAL_PROMPT = """You are answering a behavioral interview question using the STAR method. Speak as the person from the RESUME.

STRUCTURE: Situation → Action → Result in 2-4 conversational sentences. Under 80 words total.
- Use "I" throughout. Be specific — mention actual tools, teams, or outcomes from your resume.
- Sound human and direct. No bullet points. Just flowing speech.
- Start directly with the situation. Do NOT say "Sure", "Great question", or "Certainly".
- NEVER reveal you are an AI."""

SYSTEM_DESIGN_PROMPT = """You are answering a system design question in a job interview. Speak as the senior engineer from the RESUME.

FORMAT: Exactly 3 bullet points:
  Bullet 1: High-level components / architecture choice (which services/DBs/queues)
  Bullet 2: Key technical decision with brief reason (why that DB, why that queue, trade-off)
  Bullet 3: How you'd scale it or the main trade-off you'd accept in production

Keep each bullet under 20 words. Use "I'd" and "I've" — personal and direct.
NO colons inside bullets. NO bold. NO markdown headers.
NEVER reveal you are an AI."""


# ── Vision: Solve Coding Problem from Screenshot ──────────────────────────────

def solve_coding_from_image(image_b64: str, media_type: str = "image/png") -> str:
    """Extract and solve a coding problem from a screenshot using Claude's vision.
    Returns formatted solution with code + brief explanation bullets.
    """
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",   # vision-capable model
            max_tokens=1800,
            temperature=TEMP_CODING,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "This is a screenshot of a coding/technical problem from an interview or online judge. "
                            "Extract the problem statement, then write a clean working solution.\n\n"
                            "Format your response as:\n"
                            "1. A brief one-line problem summary\n"
                            "2. The complete solution code (Python preferred unless the screenshot specifies another language)\n"
                            "3. 2-3 bullet points explaining the approach and time/space complexity\n\n"
                            "Write code like a real human would in an interview — short variable names, no over-engineering."
                        ),
                    },
                ],
            }],
        )
        return response.content[0].text.strip()
    except Exception as e:
        dlog.log_error("[LLM] solve_coding_from_image failed", e)
        return ""


# Per-session conversation history.
# Keyed by session_id (str). Falls back to "_default" for callers that don't
# pass a session_id. Each session keeps last 5 Q+A pairs (≈ 300 tokens).
from collections import deque
_SESSION_HISTORIES: dict = {}
_SESSION_HISTORY_MAXLEN = 5

# Legacy alias — kept for any direct external references during transition
HISTORY = deque(maxlen=_SESSION_HISTORY_MAXLEN)


def _get_history(session_id: str | None) -> deque:
    """Return the history deque for this session, creating it if needed."""
    key = session_id or "_default"
    if key not in _SESSION_HISTORIES:
        _SESSION_HISTORIES[key] = deque(maxlen=_SESSION_HISTORY_MAXLEN)
    return _SESSION_HISTORIES[key]

def get_interview_answer(question: str, resume_text: str = "", job_description: str = "",
                         include_code: bool = False, active_user_context: str = "",
                         question_type: str = "technical",
                         session_id: str | None = None) -> str:
    """Single-shot interview answer with per-session history context."""
    if question_type == 'behavioral':
        system_prompt = BEHAVIORAL_PROMPT
    elif question_type == 'system_design':
        system_prompt = SYSTEM_DESIGN_PROMPT
    else:
        system_prompt = INTERVIEW_PROMPT

    if active_user_context:
        # Rich context from user_manager (role + style hint + resume summary + JD + exp filter)
        system_prompt += f"\n\n{active_user_context}"
    else:
        if resume_text:
            system_prompt += f"\n\nYOUR RESUME (answer as this person):\n{resume_text}"
        if job_description:
            system_prompt += f"\n\nJOB DESCRIPTION (tailor answers to this):\n{job_description}"

    question = (question or '').strip()
    if not question:
        return ""

    dlog.log(f"[LLM] Single-shot: {question[:60]}", "DEBUG")

    # Build messages with per-session history — skip any entry where q or a is empty
    _hist = _get_history(session_id)
    messages = []
    for q, a in _hist:
        if q and a:
            messages.append({"role": "user", "content": q})
            messages.append({"role": "assistant", "content": a})
    messages.append({"role": "user", "content": question})
    # Prefill to force bullet format (prevents preamble)
    messages.append({"role": "assistant", "content": "-"})

    try:
        api_start = time.time()
        # Cache the system prompt for repeated questions (saves ~300-500ms on cache hit)
        system_block = [{"type": "text", "text": system_prompt,
                         "cache_control": {"type": "ephemeral"}}]
        response = client.messages.create(
            model=MODEL,
            max_tokens=_get_interview_token_budget(active_user_context, question),
            temperature=TEMP_INTERVIEW,
            system=system_block,
            messages=messages,
            extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
        )
        api_time = time.time() - api_start
        answer = humanize_response("-" + response.content[0].text.strip())

        # Update per-session history (deque auto-evicts oldest when full)
        _get_history(session_id).append((question, answer))
            
        dlog.log(f"[LLM] Done: {len(answer)} chars in {api_time*1000:.0f}ms", "DEBUG")
        return answer

    except Exception as e:
        dlog.log_error("[LLM] Single-shot failed", e)
        return ""


_ROLE_CONTEXT = {
    "python": "INTERVIEW ROLE: Python Developer. Use Python for ALL code examples. Focus on Python idioms, Django/FastAPI, async, testing, packaging. IMPORTANT: For the personal experience bullet, reference Python/programming work only. Do NOT mention telecom, SIP, SS7, Diameter, production support, grep, awk, or non-Python tools.",
    "java": "INTERVIEW ROLE: Java Developer. Use Java for ALL code examples. Focus on Spring Boot, JVM, multithreading, Maven/Gradle, design patterns, Hibernate, generics, Collections framework, Stream API, Spring Security.",
    "javascript": "INTERVIEW ROLE: JavaScript/Node.js Developer. Use JavaScript/TypeScript for ALL code examples. Focus on React, Node.js, async/await, Promises, event loop, closures, REST APIs, Express, TypeScript.",
    "sql": "INTERVIEW ROLE: Data/SQL Engineer. Always include SQL examples. Focus on query optimization, indexes, joins, window functions, stored procedures, ACID, normalization.",
    "saas": "INTERVIEW ROLE: SaaS Product/Backend Engineer. Focus on multi-tenancy, subscriptions, billing, REST APIs, webhooks, scalability, OAuth, RBAC, rate limiting, and B2B product concepts.",
    "system_design": "INTERVIEW ROLE: System Design / Senior Engineer. Focus on scalability, distributed systems, CAP theorem, load balancing, caching, microservices, sharding, consistent hashing, message queues.",
    "devops": "INTERVIEW ROLE: DevOps/Cloud Engineer. Focus on CI/CD pipelines, Docker, Kubernetes, Terraform, Ansible, monitoring (Prometheus/Grafana), Linux administration, cloud services (AWS/GCP/Azure), shell scripting.",
    "production_support": "INTERVIEW ROLE: Production Support Engineer. Focus on incident management, root cause analysis, monitoring, log analysis (grep/awk/sed), Linux troubleshooting, SLA/SLO, ticketing systems, on-call processes.",
    "telecom": "INTERVIEW ROLE: Telecom/IMS Support Engineer. Focus on SIP protocol, SS7, Diameter, IMS architecture, VoIP, Kamailio, Wireshark, call flow analysis, telecom troubleshooting.",
}


def get_streaming_interview_answer(question: str, resume_text: str = "", job_description: str = "",
                                   active_user_context: str = "", model: str = None,
                                   question_type: str = "technical",
                                   session_id: str | None = None):
    """Streaming interview answer with per-session history context."""
    if question_type == 'behavioral':
        system_prompt = BEHAVIORAL_PROMPT
    elif question_type == 'system_design':
        system_prompt = SYSTEM_DESIGN_PROMPT
    else:
        system_prompt = INTERVIEW_PROMPT

    # Inject interview role context (set from terminal bar role selector)
    try:
        import config as _cfg
        _role = getattr(_cfg, 'INTERVIEW_ROLE', 'general')
        if _role and _role != 'general':
            _role_hint = _ROLE_CONTEXT.get(_role)
            if _role_hint:
                system_prompt += f"\n\n{_role_hint}"
    except Exception:
        pass

    # Inject interview round style hint (HR/TECH/DESIGN/CODE)
    _rp = _get_round_params()
    if _rp.get("style"):
        system_prompt += f"\n\nANSWER STYLE: {_rp['style']}"

    if active_user_context:
        # Rich context from user_manager (role + style hint + resume summary + JD + exp filter)
        system_prompt += f"\n\n{active_user_context}"
    else:
        if resume_text:
            system_prompt += f"\n\nYOUR RESUME (answer as this person):\n{resume_text}"
        if job_description:
            system_prompt += f"\n\nJOB DESCRIPTION (tailor answers to this):\n{job_description}"

    question = (question or '').strip()
    if not question:
        return

    dlog.log(f"[LLM] Streaming: {question[:60]}", "DEBUG")
    stream_start = time.time()

    # Build messages with per-session history — skip any entry where q or a is empty
    _hist = _get_history(session_id)
    messages = []
    for q, a in _hist:
        if q and a:
            messages.append({"role": "user", "content": q})
            messages.append({"role": "assistant", "content": a})
    messages.append({"role": "user", "content": question})
    # Prefill to force bullet format
    messages.append({"role": "assistant", "content": "-"})

    # Fallback model when primary is overloaded; use per-user model if provided
    _primary_model = model or MODEL
    FALLBACK_MODEL = "claude-sonnet-4-6"

    # Use prompt caching on the system prompt so repeated questions share cached tokens.
    # cache_control marks the system prompt for server-side caching (TTL ~5 min).
    # This saves ~300-500ms on the 2nd+ request with the same system prompt.
    system_block = [{"type": "text", "text": system_prompt,
                     "cache_control": {"type": "ephemeral"}}]

    _stream_rp = _get_round_params()
    _stream_temp = _stream_rp.get("temperature") or TEMP_INTERVIEW

    full_answer = "-"
    try:
        yield "-"
        for attempt_model in [_primary_model, FALLBACK_MODEL]:
            succeeded = False
            for retry in range(3):  # Retry up to 3x for 502/5xx errors
                try:
                    with client.messages.stream(
                        model=attempt_model,
                        max_tokens=_get_interview_token_budget(active_user_context, question),
                        temperature=_stream_temp,
                        system=system_block,
                        messages=messages,
                        extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
                    ) as stream:
                        for text in stream.text_stream:
                            full_answer += text
                            yield text
                    succeeded = True
                    break  # Stream completed successfully
                except Exception as e:
                    err_str = str(e)
                    is_overloaded = "overloaded" in err_str
                    is_gateway_err = any(code in err_str for code in ("502", "503", "529", "InternalServerError"))
                    if is_overloaded and attempt_model != FALLBACK_MODEL:
                        print(f"[LLM] {attempt_model} overloaded, falling back to {FALLBACK_MODEL}")
                        break  # Try fallback model
                    if is_gateway_err and retry < 2:
                        wait = 0.5 * (retry + 1)
                        print(f"[LLM] Gateway error ({err_str[:40].strip()}), retry {retry+1}/2 in {wait}s...")
                        # Use gevent.sleep under gevent server to avoid blocking event loop
                        try:
                            import gevent as _gevent
                            _gevent.sleep(wait)
                        except ImportError:
                            time.sleep(wait)
                        full_answer = "-"  # Reset for clean retry
                        continue
                    raise  # Re-raise for unrecoverable errors
            if succeeded:
                break  # No need to try fallback model

        # Update per-session history — only store if both question and answer are non-empty
        if question and full_answer and full_answer.strip('-').strip():
            _get_history(session_id).append((question, full_answer))
        dlog.log(f"[LLM] Stream done in {(time.time() - stream_start)*1000:.0f}ms", "DEBUG")

    except Exception as e:
        dlog.log_error("[LLM] Stream failed", e)
        yield ""


def _clean_code_answer(text: str) -> str:
    """Strip any text preamble/explanation from code answers, keep only code."""
    if not text:
        return text
    # Remove markdown code fences
    text = re.sub(r'^```\w*\n?', '', text)
    text = re.sub(r'\n?```$', '', text)
    text = text.strip()
    # If starts with text preamble (not code), strip everything before first code line
    lines = text.split('\n')
    code_start_idx = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        su = stripped.upper()
        if (stripped.startswith('def ') or stripped.startswith('class ') or
            stripped.startswith('import ') or stripped.startswith('from ') or
            stripped.startswith('if ') or stripped.startswith('for ') or
            stripped.startswith('while ') or stripped.startswith('#') or
            re.match(r'^[a-z_]\w*\s*=', stripped) or
            stripped.startswith('- hosts:') or stripped.startswith('- hosts ') or
            stripped.startswith('- name:') or stripped.startswith('---') or
            # Terraform patterns
            stripped.startswith('provider ') or stripped.startswith('resource ') or
            stripped.startswith('variable ') or stripped.startswith('output ') or
            stripped.startswith('terraform {') or stripped.startswith('data ') or
            stripped.startswith('module ') or stripped.startswith('locals {') or
            # Dockerfile patterns
            su.startswith('FROM ') or su.startswith('RUN ') or
            su.startswith('COPY ') or su.startswith('CMD ') or
            su.startswith('WORKDIR ') or su.startswith('EXPOSE ') or
            su.startswith('ENV ') or su.startswith('ARG ') or su.startswith('ENTRYPOINT') or
            # SQL patterns (case-insensitive)
            su.startswith('SELECT ') or su.startswith('CREATE ') or
            su.startswith('INSERT ') or su.startswith('ALTER ') or
            su.startswith('UPDATE ') or su.startswith('DELETE ') or
            su.startswith('DROP ') or su.startswith('WITH ') or
            # Java patterns
            stripped.startswith('public ') or stripped.startswith('private ') or
            stripped.startswith('protected ') or stripped.startswith('static ') or
            stripped.startswith('@Override') or stripped.startswith('@') or
            re.match(r'^(int|long|String|boolean|List|Map|Set|void|char|double|float)\s+\w+', stripped) or
            # JavaScript patterns
            stripped.startswith('const ') or stripped.startswith('let ') or
            stripped.startswith('var ') or stripped.startswith('function ') or
            stripped.startswith('async ') or stripped.startswith('export ') or
            stripped.startswith('module.exports') or stripped.startswith('require(') or
            re.match(r'^(class|interface|enum)\s+\w+', stripped) or
            # Groovy/Jenkins
            stripped.startswith('pipeline {') or stripped.startswith('node {') or
            stripped.startswith('stage(') or stripped.startswith('sh ') or
            # Shell/Bash
            stripped.startswith('#!/') or stripped.startswith('ps ') or
            stripped.startswith('df ') or stripped.startswith('grep ') or
            stripped.startswith('awk ') or stripped.startswith('sed ') or
            stripped.startswith('find ') or stripped.startswith('echo ') or
            stripped.startswith('curl ') or stripped.startswith('ss ') or
            # CSS patterns
            re.match(r'^[\.\#]?[a-zA-Z\*][\w\-]*\s*\{', stripped) or
            stripped.startswith('@media') or stripped.startswith(':root') or
            # HTML patterns
            stripped.startswith('<')):
            code_start_idx = i
            break
    if code_start_idx > 0:
        text = '\n'.join(lines[code_start_idx:]).strip()
    # Remove trailing explanation text after the code
    final_lines = []
    code_ended = False
    for line in text.split('\n'):
        if code_ended:
            break
        # If we hit an empty line after code, check if next line looks like text
        if not line.strip() and final_lines:
            final_lines.append(line)
            continue
        # Text explanation lines (no indentation, starts with "This", "Here", "The", etc.)
        # Also catches mid-code LLM self-corrections like "Wait, you asked for Java."
        if (final_lines and not line.startswith(' ') and not line.startswith('\t') and
            re.match(r'^(This|Here|The|It|Note|Output|Example|Usage|How|You|Wait|Actually|'
                     r'Oops|Sorry|Let me|Now|Above|Below|In the|To run|Run with)', line)):
            break
        final_lines.append(line)
    # Remove trailing empty lines
    while final_lines and not final_lines[-1].strip():
        final_lines.pop()
    return '\n'.join(final_lines)


# Language keywords used to auto-detect the desired coding language from the question.
# Order matters: more specific first (javascript before java).
_LANG_KEYWORDS: list[tuple[str, list[str]]] = [
    ('javascript', ['javascript', ' js ', 'node.js', 'nodejs', 'react', 'express',
                    'typescript', 'ts ', '.ts', 'vue', 'angular', 'jquery',
                    'promise', 'async/await in js', 'arrow function']),
    ('java',       ['java', 'spring boot', 'hibernate', 'jpa ', 'maven', 'gradle',
                    'junit', 'hashmap', 'arraylist', 'java 8', 'java 11',
                    'thread pool', 'completablefuture', 'servlet']),
    ('sql',        ['sql', 'postgresql', 'postgres', 'mysql', 'sqlite', 'oracle',
                    'select ', 'insert ', 'update ', 'delete ', 'join ', 'trigger ',
                    'stored procedure', 'cte', 'window function', 'plpgsql']),
    ('bash',       ['bash', 'shell script', 'linux script', '#!/bin',
                    'cron job', 'shell one liner', 'awk script', 'sed script',
                    'ansible', 'playbook', 'terraform', 'dockerfile',
                    'jenkinsfile', 'docker-compose',
                    # Unix/shell scripting requests
                    'write a bash', 'write a shell', 'bash script to', 'shell script to',
                    'bash function', 'shell function', 'bash one liner',
                    'sed command', 'awk command', 'grep command',
                    'write a sed', 'write an awk', 'write a grep',
                    'xargs', 'heredoc', 'shebang', 'subshell',
                    'crontab entry', 'cron expression',
                    'bash array', 'associative array',
                    'bash loop', 'while loop in bash', 'for loop in bash',
                    'case statement in bash', 'if else in bash',
                    'getopts', 'getopt',
                    'trap command', 'signal trap',
                    'bash function', 'shell function',
                    'parameter expansion', 'variable expansion',
                    'process substitution', 'command substitution',
                    'pipe to', 'redirect to', 'stderr redirect',
                    'named pipe', 'mkfifo']),
    ('python',     ['python', 'django', 'flask', 'fastapi', 'pandas', 'numpy',
                    'pytest', 'asyncio', 'pydantic', 'sqlalchemy', 'celery']),
]


def detect_coding_language(question: str) -> str:
    """Detect the coding language from question text, falling back to CODING_LANGUAGE config."""
    lower = ' ' + question.lower() + ' '
    for lang, keywords in _LANG_KEYWORDS:
        if any(kw in lower for kw in keywords):
            return lang
    return _cfg.CODING_LANGUAGE  # configured default (env var or "python")


def get_coding_answer(question: str, user_context: str = "") -> str:
    """Single-shot coding answer. Clean executable code only."""
    dlog.log(f"[LLM] Coding: {question[:60]}", "DEBUG")

    q_lower = question.lower()
    # Use larger token budget for infra scripts (Ansible/Terraform/Dockerfile/Jenkinsfile)
    is_infra = any(kw in q_lower for kw in _INFRA_KEYWORDS)
    max_tok = MAX_TOKENS_CODING_INFRA if is_infra else MAX_TOKENS_CODING

    # Auto-detect language and inject a language hint so the LLM picks the right one
    lang = detect_coding_language(question)
    lang_display = {
        'javascript': 'JavaScript (ES6+)',
        'java':       'Java',
        'sql':        'SQL / PostgreSQL',
        'bash':       'Bash / Shell',
        'python':     'Python',
    }.get(lang, lang.capitalize())

    # Only inject hint if it's not already explicit in the question
    if lang not in q_lower and lang_display.lower() not in q_lower:
        question_with_lang = f"[Write in {lang_display} ONLY — no other languages]\n{question}"
    else:
        question_with_lang = question

    try:
        api_start = time.time()
        response = client.messages.create(
            model=MODEL,
            max_tokens=max_tok,
            temperature=TEMP_CODING,
            system=CODING_PROMPT,
            messages=[{"role": "user", "content": question_with_lang}]
        )
        answer = _clean_code_answer(response.content[0].text.strip())
        dlog.log(f"[LLM] Coding done: {len(answer)} chars in {(time.time() - api_start)*1000:.0f}ms (lang={lang}, infra={is_infra})", "DEBUG")
        return answer

    except Exception as e:
        dlog.log_error("[LLM] Coding failed", e)
        return ""


def correct_question_intent(question: str) -> str:
    """
    Lightweight LLM call to correct a garbled/misspelled IT interview question.

    Used ONLY when DB lookup misses AND the question contains no recognizable
    tech term — strong signal that STT produced garbage.

    Returns corrected question string, or the original if already clear.
    Never raises; returns original on error.

    Examples:
        "What is Gill in Python?"    → "What is GIL in Python?"
        "What is CACD pipeline?"     → "What is CI/CD pipeline?"
        "How does asinsio work?"     → "How does asyncio work?"
    """
    _CORRECTION_SYSTEM = """You are a technical interview question spell-checker.

Fix ONLY spelling/pronunciation/STT errors in IT interview questions.

Common STT errors to fix:
  CACD→CI/CD, gill→GIL, asinsio→asyncio, h top→htop, deep coffee→deep copy,
  sequel→SQL, stateful set→StatefulSet, demon set→DaemonSet, etsy d→etcd,
  wizzy→WSGI, guni corn→gunicorn, j query→jQuery, web pak→webpack,
  post grey sql→PostgreSQL, java script→JavaScript, hash map→HashMap,
  thread pool→ThreadPool, garbage collector→GC, spring boot→Spring Boot,
  docker file→Dockerfile, jenkins file→Jenkinsfile, kube config→kubeconfig,
  cron job→CronJob, config map→ConfigMap, helm chart→Helm chart,
  argo cd→ArgoCD, terra form→Terraform, ans ible→Ansible,
  dollar hash→$#, dollar question mark→$?, dollar star→$*,
  dollar at→$@, dollar zero→$0, dollar exclamation→$!,
  she bang→shebang, sub shell→subshell, named pipe→named pipe,
  hurd link→hard link, sym link→symlink, back tick→backtick,
  x args→xargs, sed in place→sed -i, pipe fail→pipefail,
  set minus e→set -e, set minus x→set -x, set minus u→set -u,
  file descriptor→file descriptor, oc tal→octal

Unix/Bash questions ARE valid IT questions — do NOT return NOT_IT for:
  - Questions about $#, $?, $@, $*, $0 (bash special variables)
  - "What does $# mean?" → valid bash question
  - "How to use sed to replace text?" → valid Linux question
  - "What is the exit status?" → valid shell question
  - "How to pass arguments to a shell script?" → valid question
  - "What is shebang?" → valid Unix question
  - "Difference between hard link and soft link?" → valid Linux question
  - "What is a named pipe / FIFO?" → valid Unix question
  - "How to redirect stderr to stdout?" → valid shell question
  - "What is process substitution?" → valid bash question

RULES:
- Return ONLY the corrected question, nothing else
- If already correct, return it unchanged
- If NOT about IT (Python/Java/JS/HTML/CSS/SQL/Linux/Unix/Bash/Shell/DevOps/SRE/
  K8s/OpenStack/Cloud/Django/Flask/Networking/Algorithms), return: NOT_IT
- Do NOT add words or change meaning — only fix spelling/STT errors
- Maximum 1 line output"""

    try:
        # Use Haiku with tight token budget — correction is just a rephrased question
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=40,
            temperature=0.0,
            system=_CORRECTION_SYSTEM,
            messages=[{"role": "user", "content": question}]
        )
        corrected = response.content[0].text.strip()
        # If LLM says it's not IT, return sentinel
        if corrected.upper().startswith("NOT_IT"):
            return ""
        # Sanity: corrected should be similar length (not a completely different answer)
        if len(corrected) > len(question) * 2.5 or len(corrected) < 5:
            return question
        return corrected
    except Exception as e:
        dlog.log_error("[LLM] correct_question_intent failed", e)
        return question


_QUICK_PROMPT = """You are a Python developer answering a quick interview question. Be ultra concise.

RULES:
- If it is a concept question: 1-2 lines explanation + a clean Python code block using triple backticks.
- If it is a coding question: ONLY a Python code block using triple backticks, no explanation text before it.
- Code blocks MUST use ```python ... ``` format with proper indentation and newlines.
- Max 120 tokens total. No filler. No "Here is", no "Sure", no "Certainly".
- No numbered lists. No excessive bullets. Direct answer only.
- For coding: write complete, runnable functions with a usage example comment at the end."""


def get_quick_answer(question: str) -> str:
    """
    Fast focused answer for focus-mode Ask bar.
    - Short system prompt → less prefill → faster TTFT
    - 120 token cap → streams in ~0.8s instead of 2-3s
    - Returns full text (not streaming) for simplicity
    """
    import config as _cfg
    _role = getattr(_cfg, 'INTERVIEW_ROLE', 'python')
    role_hint = _ROLE_CONTEXT.get(_role, _ROLE_CONTEXT.get('python', ''))
    system = _QUICK_PROMPT
    if role_hint:
        system += f"\n\n{role_hint}"

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=280,          # short = fast TTFT (~0.8s)
            temperature=0.1,
            system=system,
            messages=[{"role": "user", "content": question}],
        )
        return (resp.content[0].text or '').strip()
    except Exception as e:
        dlog.log_error("[LLM] get_quick_answer failed", e)
        return ""


def generate_qa_payload(question: str, answer: str, wants_code: bool = False) -> dict:
    """
    Validate a Q&A pair and generate a structured payload for DB auto-learning.

    Returns dict with keys: valid, question, answer, code, keywords, tags, reason
    or None on failure.

    Only called asynchronously from background worker — never on the critical path.
    """
    import json as _json

    AUTO_LEARN_SYSTEM = """You are an IT interview knowledge validator.

Given a question+answer from a live interview, return a JSON payload for storage.

ACCEPT any real IT interview question about:
- Python (OOP, async, GIL, generators, decorators, memory, keywords)
- Java (JVM, GC, collections, concurrency, Spring, JPA, design patterns)
- JavaScript (event loop, closures, promises, ES6, DOM, Node.js, React)
- HTML / CSS (box model, flexbox, grid, specificity, semantic, accessibility)
- SQL / PostgreSQL (JOINs, indexes, ACID, MVCC, window functions, CTEs, VACUUM)
- Django (ORM, N+1, signals, DRF, middleware, migrations, CBV)
- Flask (Blueprint, WSGI, Jinja2, application context, Flask-SQLAlchemy)
- Linux (processes, signals, permissions, disk, memory, systemd, networking, troubleshooting)
- Production Support (incident response, RCA, SOP, log analysis, on-call, P1/P2)
- DevOps (Docker, CI/CD, GitHub Actions, GitLab CI, Helm, ArgoCD, GitOps, SonarQube)
- SRE (SLO, SLI, error budget, golden signals, alerting, chaos engineering)
- Kubernetes (pods, deployments, StatefulSets, RBAC, PVC, probes, HPA, etcd)
- OpenStack (Nova, Neutron, Cinder, Glance, Keystone, live migration, security groups)
- Cloud (AWS, GCP, Azure, EC2, S3, IAM, VPC, Lambda, EKS)
- Terraform / Ansible (modules, playbooks, roles, state, providers)
- Networking (TCP/IP, DNS, HTTP, TLS, load balancing, firewall)
- Algorithms / Data Structures (sorting, trees, graphs, DP, complexity)
- Software Engineering (SOLID, design patterns, microservices, REST, gRPC)

REJECT if:
- Not IT related at all
- Pure personal ("Tell me about yourself" type)
- Too basic only if the answer adds no real value ("What is a variable?" with 5-word answer)
- Answer under 12 words

OUTPUT: JSON only, no other text:
{
  "valid": true,
  "question": "cleaned question text",
  "answer": "answer text (no code)",
  "code": "code block if applicable, else empty string",
  "keywords": ["kw1", "kw2", "kw3"],
  "tags": ["tag1", "tag2"],
  "reason": "why accepted or rejected"
}

Tags must be from: python, java, javascript, html, css, sql, postgresql, django, flask,
linux, devops, sre, kubernetes, openstack, aws, docker, ansible, terraform, bash,
monitoring, database, networking, coding, algorithms"""

    user_content = f"Question: {question}\n\nAnswer:\n{answer}"

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=650,   # Bumped: 400 caused "Unterminated string" JSON truncation
            temperature=0.1,
            system=AUTO_LEARN_SYSTEM,
            messages=[{"role": "user", "content": user_content}]
        )
        raw = response.content[0].text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = re.sub(r'^```\w*\n?', '', raw)
            raw = re.sub(r'\n?```$', '', raw)
        raw = raw.strip()
        # Attempt to fix truncated JSON (unterminated string → strip incomplete last field)
        try:
            return _json.loads(raw)
        except _json.JSONDecodeError:
            # Try to recover by trimming to last complete JSON field
            idx = raw.rfind('",')
            if idx > 0:
                # Find the last complete field boundary and close the object
                truncated = raw[:idx+1]
                # Count open braces/brackets to decide how to close
                opens = truncated.count('{') - truncated.count('}')
                truncated += '}'  * opens
                try:
                    return _json.loads(truncated)
                except Exception:
                    pass
            raise
    except Exception as e:
        dlog.log_error("[AutoLearn] generate_qa_payload failed", e)
        return None


def _detect_language(editor_content: str, url: str) -> str:
    """Detect programming language from editor content and URL."""
    ec = editor_content.strip()
    if ec:
        if re.search(r'\bpublic\s+(class|static|void|int|boolean)\b', ec): return "Java"
        if re.search(r'(#include|vector<|std::|cout|cin|auto\s+\w+\s*=)', ec): return "C++"
        if re.search(r'\b(const|let|var)\s+\w+|=>\s*\{|function\s+\w+\s*\(', ec): return "JavaScript"
        if re.search(r'(def |import |from |class \w+:)', ec): return "Python 3"
    # Fallback: check URL for language clues
    url_l = url.lower()
    if 'java' in url_l: return "Java"
    if 'javascript' in url_l or 'js' in url_l: return "JavaScript"
    return "Python 3"


def get_platform_solution(problem_text: str, editor_content: str = "", url: str = "") -> str:
    """Generate solution for coding platforms (##start / Ctrl+Alt+Enter mode)."""
    lang = _detect_language(editor_content, url)
    dlog.log(f"[LLM] Platform solve ({lang}): {url[:40]}", "DEBUG")
    print(f"[CODE] Solving in {lang} | {url[:50]}")

    user_content = (
        f"LANGUAGE: {lang}\n"
        f"URL: {url}\n\n"
        f"EDITOR_CONTENT (use exact function/class name):\n{editor_content}\n\n"
        f"PROBLEM:\n{problem_text}"
    )

    try:
        api_start = time.time()
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS_PLATFORM,
            temperature=0.0,
            system=PLATFORM_PROMPT,
            messages=[{"role": "user", "content": user_content}]
        )

        answer = response.content[0].text.strip()

        # Strip common LLM explanation phrases
        unwanted_prefixes = [
            "Here's the Python code",
            "Here is the Python code",
            "Here's the code",
            "Here is the code",
            "Here's my solution",
            "Here is my solution",
            "The solution is",
            "Below is the",
            "This code",
        ]
        for prefix in unwanted_prefixes:
            if answer.lower().startswith(prefix.lower()):
                # Find where the actual code starts (after : or newline)
                idx = answer.find(':')
                if idx != -1 and idx < 100:
                    answer = answer[idx+1:].strip()
                else:
                    idx = answer.find('\n')
                    if idx != -1:
                        answer = answer[idx+1:].strip()
                break

        if answer.startswith("```"):
            answer = answer.split("\n", 1)[1] if "\n" in answer else answer
        if answer.endswith("```"):
            answer = answer.rsplit("\n", 1)[0] if "\n" in answer else answer
        # Also handle ```python specifically
        if answer.startswith("```python"):
            answer = answer[9:].strip()

        lines = answer.split('\n')
        clean_lines = []
        in_multiline_comment = False
        for line in lines:
            stripped = line.strip()
            # Strip /* ... */ block comments (Java/JS/C++)
            if '/*' in stripped:
                in_multiline_comment = True
            if in_multiline_comment:
                if '*/' in stripped:
                    in_multiline_comment = False
                continue
            # Strip full-line comments: # (Python), // (Java/JS/C++)
            if stripped.startswith('#') and not stripped.startswith('#!'):
                continue
            if stripped.startswith('//'):
                continue
            # Strip inline # comments (Python) — be careful not to strip strings
            if '#' in line and "'" not in line and '"' not in line:
                line = line.split('#')[0].rstrip()
            # Strip inline // comments — be careful not to strip strings
            if '//' in line and '"' not in line and "'" not in line:
                line = line.split('//')[0].rstrip()
            if line.strip():  # skip lines that became empty after stripping
                clean_lines.append(line)
            elif clean_lines:  # preserve blank lines between code blocks
                clean_lines.append('')

        answer = '\n'.join(clean_lines).strip()
        dlog.log(f"[LLM] Platform done: {len(answer)} chars in {(time.time() - api_start)*1000:.0f}ms", "DEBUG")
        return answer

    except Exception as e:
        dlog.log_error("[LLM] Platform failed", e)
        return ""


import json as _json_mod

_PROFILE_EXTRACT_SYSTEM = """\
You are a resume parser. Given resume text, extract a concise professional profile.
Return ONLY valid JSON with these exact keys:
{
  "key_skills": "comma-separated top 12 skills/tools/technologies from resume",
  "domain": "primary domain e.g. Production Support / Investment Banking",
  "custom_instructions": "2-3 sentence AI behavior rule tailored to this role. Include: preferred coding language, how to answer domain-specific questions (e.g. SQL→Python), and answer style focus."
}
Rules:
- key_skills: extract ONLY tools/languages actually mentioned, comma-separated, max 12
- domain: be specific (e.g. "Production Support / Investment Banking" not just "IT")
- custom_instructions: mention coding language preference and any domain-specific answer style
- Output ONLY the JSON object, no markdown, no explanation"""


def extract_profile_from_resume(resume_text: str) -> dict:
    """
    Use LLM to extract key_skills, domain, and custom_instructions from resume text.
    Returns dict with those 3 keys, or empty dict on failure.
    Designed to run in a background thread — fast (Haiku, ~1s).
    """
    # Use Haiku for speed and cost efficiency
    _haiku_model = "claude-haiku-4-5-20251001"
    # Trim resume to 3000 chars to keep tokens low and response fast
    text_slice = resume_text[:3000].strip()
    if len(resume_text) > 3000:
        text_slice += "\n[...truncated]"

    try:
        t0 = time.time()
        response = client.messages.create(
            model=_haiku_model,
            max_tokens=400,
            temperature=0.0,
            system=_PROFILE_EXTRACT_SYSTEM,
            messages=[{"role": "user", "content": text_slice}]
        )
        raw = response.content[0].text.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        result = _json_mod.loads(raw)
        dlog.log(f"[LLM] Profile extracted in {(time.time()-t0)*1000:.0f}ms", "DEBUG")
        return {k: str(v).strip() for k, v in result.items()
                if k in ("key_skills", "domain", "custom_instructions")}
    except Exception as e:
        dlog.log_error("[LLM] extract_profile_from_resume failed", e)
        return {}
