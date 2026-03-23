"""JD Auto-Configure service.

Parses a pasted job description with a single LLM call to extract:
  - role  (maps to INTERVIEW_ROLE)
  - skills (list of keywords)
  - company (name for context)
  - round hint (tech / hr / design / code)

Then asynchronously seeds ~20 targeted Q&A pairs into the DB so the
next interview has instant DB hits for role-specific questions.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import config


# ── LLM extraction ────────────────────────────────────────────────────────────

_JD_EXTRACT_PROMPT = """You are a JSON extractor. Given a job description, extract:
- "role": one of: python, java, javascript, sql, saas, system_design, devops, production_support, telecom, general
- "company": company name (or "" if not found)
- "skills": list of up to 10 key technical skills mentioned
- "round_hint": one of: tech, hr, design, code (best guess for primary interview round)
- "experience_years": integer (or null if not stated)

Respond with ONLY valid JSON. No extra text."""


def analyze_jd(jd_text: str) -> dict[str, Any]:
    """Use LLM to extract structured info from a job description.
    Returns dict with keys: role, company, skills, round_hint, experience_years.
    Falls back gracefully if LLM fails.
    """
    if not jd_text or len(jd_text.strip()) < 30:
        return _default_analysis()

    try:
        import llm_client
        from anthropic import Anthropic

        client = Anthropic(max_retries=0, timeout=15.0)
        response = client.messages.create(
            model=config.LLM_MODEL,
            max_tokens=300,
            temperature=0.0,
            system=_JD_EXTRACT_PROMPT,
            messages=[{"role": "user", "content": jd_text[:3000]}],
        )
        raw = response.content[0].text.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        return {
            "role": data.get("role", "general"),
            "company": data.get("company", ""),
            "skills": data.get("skills", []),
            "round_hint": data.get("round_hint", "tech"),
            "experience_years": data.get("experience_years"),
        }
    except Exception as exc:
        print(f"[JD] analyze_jd failed: {exc}")
        return _default_analysis()


def _default_analysis() -> dict[str, Any]:
    return {"role": "general", "company": "", "skills": [], "round_hint": "tech", "experience_years": None}


# ── Q&A seeding ───────────────────────────────────────────────────────────────

_SEED_PROMPT = """\
You are a senior interviewer. Given a job description and detected role/skills,
generate exactly {count} interview Q&A pairs that are highly likely to be asked.

Rules:
- Mix: 40% technical depth, 30% practical/scenario, 20% behavioural, 10% company/role fit
- Each answer: 3 concise bullet points, under 120 words total
- Cover skills mentioned in the JD
- JSON array: [{{"question":"...","answer":"...","tags":"..."}}]
- tags: comma-separated role + skill keywords (e.g. "python,django,async")
- Respond with ONLY the JSON array. No preamble."""


def seed_jd_qa(jd_text: str, analysis: dict, count: int = 20) -> int:
    """Generate and insert Q&A pairs derived from the JD into the DB.
    Returns the number of pairs inserted.
    Designed to be called in a background thread.
    """
    if not jd_text:
        return 0

    role = analysis.get("role", "general")
    skills = ", ".join(analysis.get("skills", []))
    company = analysis.get("company", "")

    context = f"Role: {role}"
    if company:
        context += f"\nCompany: {company}"
    if skills:
        context += f"\nKey skills: {skills}"
    context += f"\n\nJob Description:\n{jd_text[:2500]}"

    try:
        from anthropic import Anthropic

        client = Anthropic(max_retries=0, timeout=30.0)
        response = client.messages.create(
            model=config.LLM_MODEL,
            max_tokens=3000,
            temperature=0.2,
            system=_SEED_PROMPT.format(count=count),
            messages=[{"role": "user", "content": context}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        pairs = json.loads(raw)
        if not isinstance(pairs, list):
            return 0
    except Exception as exc:
        print(f"[JD] seed_jd_qa LLM call failed: {exc}")
        return 0

    inserted = 0
    try:
        import qa_database

        for item in pairs:
            q = (item.get("question") or "").strip()
            a = (item.get("answer") or "").strip()
            tags = (item.get("tags") or role).strip()
            if q and a:
                try:
                    qa_database.add_qa(q, answer_theory=a, tags=tags)
                    inserted += 1
                except Exception:
                    pass
    except Exception as exc:
        print(f"[JD] seed_jd_qa DB insert failed: {exc}")

    print(f"[JD] Seeded {inserted}/{len(pairs)} Q&A pairs for role={role}")
    return inserted


# ── High-level configure entry point ──────────────────────────────────────────

def configure_from_jd(jd_text: str) -> dict[str, Any]:
    """Full pipeline: analyze JD → apply settings → kick off async Q&A seeding.

    Returns the analysis dict plus {"settings_applied": {...}, "seeding": "started"}.
    The Q&A seeding happens in a daemon thread — caller gets the response immediately.
    """
    analysis = analyze_jd(jd_text)
    applied = {}

    # Apply role
    try:
        from app.services.settings_service import update_interview_role
        result = update_interview_role(analysis["role"])
        applied["interview_role"] = result.get("updated", {}).get("interview_role", analysis["role"])
        applied["coding_language"] = result.get("updated", {}).get("coding_language", "python")
    except Exception as exc:
        print(f"[JD] apply role failed: {exc}")


    # Kick off Q&A seeding in background
    t = threading.Thread(
        target=seed_jd_qa,
        args=(jd_text, analysis),
        kwargs={"count": 20},
        daemon=True,
        name="jd-seed",
    )
    t.start()

    return {**analysis, "settings_applied": applied, "seeding": "started"}
