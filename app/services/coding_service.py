"""Coding workflow services and shared in-memory state."""

from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path

import answer_storage
import llm_client

try:
    import debug_logger as dlog
except ImportError:  # pragma: no cover - fallback for stripped environments
    class _DlogStub:
        def log(self, *args, **kwargs):
            pass

        def log_error(self, *args, **kwargs):
            pass

    dlog = _DlogStub()


latest_code = {
    "code": "",
    "timestamp": 0,
    "platform": "",
    "source": "",
    "status": "idle",
    "mode": "auto",
    "control": "stopped",
}

recent_problems = {
    "last_hash": "",
    "last_time": 0,
}

DEDUP_WINDOW_SECONDS = 10


def set_llm_model_payload(data: dict | None) -> tuple[dict, int]:
    """Change the LLM model from dashboard settings."""
    data = data or {}
    model = data.get("model", "")
    model_map = {
        "haiku": "claude-haiku-4-5-20251001",
        "sonnet": "claude-sonnet-4-6",
    }
    if model not in model_map:
        return {"error": f"Unknown model: {model}"}, 400
    try:
        os.environ["LLM_MODEL_OVERRIDE"] = model_map[model]
        llm_client.MODEL = model_map[model]
        return {"model": model, "id": model_map[model]}, 200
    except Exception as exc:
        return {"error": str(exc)}, 500


def performance_payload() -> tuple[dict, int]:
    """Return recent performance logs."""
    try:
        log_file = Path.home() / ".drishi" / "logs" / "performance.log"
        if log_file.exists():
            lines = log_file.read_text(encoding="utf-8").splitlines()
            return {"logs": lines[-50:]}, 200
        return {"logs": []}, 200
    except Exception as exc:
        return {"error": str(exc)}, 500


def clear_session_payload() -> tuple[dict, int]:
    """Clear the current answer session."""
    try:
        answer_storage.clear_all(force_clear=True)
        print("[API] Session cleared manually - starting fresh")
        return {"status": "cleared", "message": "All Q&A history cleared"}, 200
    except Exception as exc:
        return {"error": str(exc)}, 500


def _ensure_example_call(lines: list[str]) -> list[str]:
    """LLM now generates code with examples, so just return as-is."""
    return lines


def extract_code_from_answer(answer_text: str) -> tuple[str | None, list[str]]:
    """Parse markdown code blocks from an answer into clean lines."""
    match = re.search(r"```(\w*)\n(.*?)```", answer_text, re.DOTALL)
    if match:
        lang = match.group(1) or "python"
        code = match.group(2).rstrip("\n")
        lines = code.split("\n")
        return lang, _ensure_example_call(lines)

    stripped = answer_text.strip()
    if not stripped:
        return None, []

    if (
        re.match(r"^(def |class |import |from |for |while |if |print\()", stripped)
        or "\ndef " in stripped
        or "\nclass " in stripped
        or "\nprint(" in stripped
        or (stripped.count("\n") >= 2 and "(" in stripped and ":" in stripped)
    ):
        return "python", _ensure_example_call(stripped.split("\n"))

    return None, []


def code_payload() -> dict:
    """Return the latest answer that contains code."""
    answers = answer_storage.get_all_answers()
    for ans in answers:
        if not ans.get("answer") or not ans.get("is_complete"):
            continue
        lang, lines = extract_code_from_answer(ans["answer"])
        if lines:
            code_text = "\n".join(lines)
            code_id = hashlib.md5(code_text.encode()).hexdigest()[:12]
            return {
                "has_code": True,
                "code_id": code_id,
                "language": lang,
                "lines": lines,
                "question": ans.get("question", ""),
                "timestamp": ans.get("timestamp", ""),
            }
    return {
        "has_code": False,
        "code_id": None,
        "language": None,
        "lines": [],
        "question": None,
        "timestamp": None,
    }


def code_payloads() -> dict:
    """Return all code answers in chronological order."""
    answers = list(reversed(answer_storage.get_all_answers()))
    codes = []
    index = 1
    for ans in answers:
        if not ans.get("answer") or not ans.get("is_complete"):
            continue
        lang, lines = extract_code_from_answer(ans["answer"])
        if lines:
            code_text = "\n".join(lines)
            code_id = hashlib.md5(code_text.encode()).hexdigest()[:12]
            codes.append({
                "index": index,
                "code_id": code_id,
                "language": lang,
                "lines": lines,
                "question": ans.get("question", ""),
                "timestamp": ans.get("timestamp", ""),
            })
            index += 1
    return {"codes": codes, "count": len(codes)}


def coding_state_payload() -> dict:
    """Return whether a code answer is currently being generated."""
    answers = answer_storage.get_all_answers()
    is_generating = False
    last_code_ts = None
    for ans in answers:
        if not ans.get("is_complete") and ans.get("answer"):
            _, lines = extract_code_from_answer(ans["answer"])
            if lines:
                is_generating = True
                break
        if ans.get("is_complete") and ans.get("answer"):
            _, lines = extract_code_from_answer(ans["answer"])
            if lines and not last_code_ts:
                last_code_ts = ans.get("timestamp")
    return {
        "is_generating": is_generating,
        "last_code_timestamp": last_code_ts,
    }


def solve_problem_payload(data: dict | None) -> tuple[dict, int]:
    """Solve a coding problem coming from the extension/editor surface."""
    data = data or {}
    if "problem" not in data:
        return {"error": "No problem text provided"}, 400

    problem_text = data.get("problem", "")
    editor_content = data.get("editor", "")
    url = data.get("url", "")
    source = data.get("source", "editor")

    problem_hash = hashlib.md5((problem_text[:500] + url).encode()).hexdigest()
    now = time.time()
    if (
        problem_hash == recent_problems["last_hash"]
        and now - recent_problems["last_time"] < DEDUP_WINDOW_SECONDS
    ):
        print(f"[API] DUPLICATE - Skipping (same problem within {DEDUP_WINDOW_SECONDS}s)")
        return {"solution": "", "duplicate": True}, 200

    recent_problems["last_hash"] = problem_hash
    recent_problems["last_time"] = now

    print("\n[API] SOLVE REQUEST RECEIVED")
    print(f"      Source: {source}")
    print(f"      URL: {url}")
    dlog.log(f"[API] Solve request from {source} for {url}", "INFO")

    latest_code["status"] = "generating"
    latest_code["platform"] = url
    latest_code["source"] = source
    latest_code["timestamp"] = time.time()

    if source == "chat":
        latest_code["mode"] = "view"
        print("      [CHAT MODE] Forced to VIEW-ONLY")

    try:
        print(f"\n{'=' * 50}")
        print(" QUESTION (Extracted Problem Text):")
        print(f"{'-' * 50}\n{problem_text}\n{'-' * 50}")

        solution = llm_client.get_platform_solution(problem_text, editor_content, url)

        latest_code["code"] = solution
        latest_code["status"] = "complete"
        latest_code["timestamp"] = time.time()

        q_lines = problem_text.strip().split("\n")
        short_question = q_lines[0][:100] if q_lines else "Coding Problem"
        if url:
            platform_match = re.search(
                r"(hackerrank|leetcode|codewars|codility|codesignal)",
                url.lower(),
            )
            if platform_match:
                short_question = f"[{platform_match.group(1).upper()}] {short_question}"

        answer_storage.set_complete_answer(
            question_text=short_question,
            answer_text=solution,
            metrics={"source": source, "url": url[:50] if url else None},
        )

        print("\n ANSWER (Generated Code):")
        print(f"{'-' * 50}\n{solution}\n{'-' * 50}")
        print(f"Solution generated ({len(solution)} chars)")
        print(f"{'=' * 50}\n")
        return {"solution": solution}, 200
    except Exception as exc:
        latest_code["status"] = "error"
        dlog.log_error("[API] Solve failed", exc)
        return {"error": str(exc)}, 500


def latest_code_payload() -> dict:
    return latest_code


def control_start_payload() -> dict:
    latest_code["control"] = "running"
    print("[CONTROL] START/RESUME")
    return {"status": "running", "mode": latest_code["mode"]}


def control_pause_payload() -> dict:
    latest_code["control"] = "paused"
    print("[CONTROL] PAUSE")
    return {"status": "paused", "mode": latest_code["mode"]}


def control_stop_payload() -> dict:
    latest_code["control"] = "stopped"
    latest_code["status"] = "idle"
    print("[CONTROL] STOP")
    return {"status": "stopped", "mode": latest_code["mode"]}


def control_toggle_mode_payload() -> dict:
    latest_code["mode"] = "view" if latest_code["mode"] == "auto" else "auto"
    mode_name = "AUTO-TYPE" if latest_code["mode"] == "auto" else "VIEW-ONLY"
    print(f"[CONTROL] MODE -> {mode_name}")
    return {"mode": latest_code["mode"], "status": latest_code["control"]}


def answer_by_index_payload(index_raw: str) -> tuple[dict, int]:
    """
    Return code by 1-based index.

    Strategy (in order):
    1. Collect all code blocks across all answers (chronological, oldest first).
       Block #1 = first code block in first answer, #2 = second code block overall, etc.
    2. This lets the user ask one question with multiple examples and access #1 #2 #3
       from Programiz individually.
    """
    try:
        index = int(index_raw)
    except ValueError:
        return {"found": False, "error": "Invalid index format"}, 400

    try:
        all_answers = answer_storage.get_all_answers()
        if not all_answers:
            return {"found": False, "error": "No questions found"}, 404

        chronological = list(reversed(all_answers))

        # Build a flat list of code blocks across all answers
        code_blocks = []  # list of (question, code_str)
        _code_re = re.compile(r"```(?:\w+)?\n(.*?)```", re.DOTALL)
        for ans in chronological:
            raw = ans.get("answer", "")
            if not raw or not ans.get("is_complete", True):
                continue
            matches = _code_re.findall(raw)
            if matches:
                for m in matches:
                    code_blocks.append((ans.get("question", ""), m.strip()))
            else:
                # No code fence — check if it looks like raw code
                stripped = raw.strip()
                if (re.match(r"^(def |class |import |from |for |while |if |SELECT\b)", stripped)
                        or ("\ndef " in stripped)
                        or (stripped.count("\n") >= 2 and "(" in stripped and ":" in stripped)):
                    code_blocks.append((ans.get("question", ""), stripped))

        if not code_blocks:
            return {"found": False, "error": "No code blocks found in any answer"}, 404

        # Clamp index
        if index <= 0:
            index = 1
        if index > len(code_blocks):
            return {
                "found": False,
                "error": f"Index {index} out of bounds (1-{len(code_blocks)})",
            }, 404

        question, code = code_blocks[index - 1]
        return {
            "found": True,
            "index": index,
            "total": len(code_blocks),
            "question": question,
            "code": code,
        }, 200
    except Exception as exc:
        return {"found": False, "error": str(exc)}, 500
