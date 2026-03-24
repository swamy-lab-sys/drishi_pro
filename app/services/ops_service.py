"""Operational and support services for Drishi Enterprise."""

from __future__ import annotations

import datetime
import os
import socket
from pathlib import Path

from flask import Response
import psutil

import answer_storage
import qa_database
import state


def get_session_info_payload() -> dict:
    """Summary of current session for UI header."""
    info = state.get_session_info()
    db_stats = qa_database.get_stats()
    info["db_count"] = db_stats.get("total", 0)
    info["session_id"] = answer_storage._session_id

    try:
        import answer_cache

        stats = answer_cache.get_stats()
        total = stats.get("hits", 0) + stats.get("misses", 0)
        info["cache_hits"] = round(stats["hits"] * 100 / total) if total > 0 else 0
    except Exception:
        info["cache_hits"] = 0

    return info


def get_system_health_payload() -> dict:
    """Real-time system monitoring."""
    sarvam_down = False
    try:
        import stt as _stt
        import config as _cfg
        if _cfg.STT_BACKEND == "sarvam":
            sarvam_down = _stt.is_sarvam_down()
    except Exception:
        pass
    return {
        "cpu": psutil.cpu_percent(),
        "ram": psutil.virtual_memory().percent,
        "stt_status": "Listening",
        "llm_status": "Idle",
        "sarvam_down": sarvam_down,
    }


def get_api_status_payload() -> list[dict]:
    """Expanded status of external API integrations."""
    status = []

    sarvam_key = os.environ.get("SARVAM_API_KEY", "")
    status.append({
        "service": "Sarvam AI",
        "status": "Active" if sarvam_key else "Missing Key",
        "key_configured": bool(sarvam_key),
        "usage": "15 requests today",
        "latency": "0.9s",
    })

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    status.append({
        "service": "Anthropic Claude",
        "status": "Active" if anthropic_key else "Missing Key",
        "key_configured": bool(anthropic_key),
        "usage": "42 requests today",
        "latency": "1.4s",
    })

    openai_key = os.environ.get("OPENAI_API_KEY", "")
    status.append({
        "service": "OpenAI",
        "status": "Active" if openai_key else "Missing Key",
        "key_configured": bool(openai_key),
        "usage": "8 requests today",
        "latency": "1.1s",
    })

    deepgram_key = os.environ.get("DEEPGRAM_API_KEY", "")
    status.append({
        "service": "Deepgram STT",
        "status": "Active" if deepgram_key else "Missing Key",
        "key_configured": bool(deepgram_key),
        "usage": "120 minutes",
        "latency": "0.4s",
    })

    return status


def get_answers_payload(user_token: str = '') -> list:
    """Return answers for a specific extension user token, or global answers if none."""
    if user_token:
        try:
            import ext_user_store
            storage = ext_user_store.get_user_storage(user_token)
            if storage is not None:
                return storage.get_all_answers()
        except Exception:
            pass
    return answer_storage.get_all_answers()


def get_transcribing_payload() -> dict:
    text = answer_storage.get_transcribing()
    return {"text": text or ""}


def get_logs_payload() -> dict:
    log_file = Path.home() / ".drishi" / "logs" / "debug.log"
    if log_file.exists():
        with open(log_file, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
            return {"logs": [line.strip() for line in lines[-100:]]}
    return {"logs": []}


def get_local_url_payload(request_host: str) -> dict:
    # Detect if request came through a public tunnel (ngrok, render, etc.)
    is_public_host = (
        request_host
        and "ngrok" in request_host
        or "onrender.com" in request_host
        or (not any(c.isdigit() for c in request_host.split(":")[0]) and "localhost" not in request_host)
    )

    if is_public_host:
        # Use the incoming host directly (already has the right domain + port if any)
        scheme = "https"
        base = f"{scheme}://{request_host}"
        return {
            "url": f"{base}/",
            "monitor_url": f"{base}/monitor",
            "ip": request_host,
            "port": "443",
        }

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        local_ip = sock.getsockname()[0]
        sock.close()
    except Exception:
        local_ip = "127.0.0.1"

    port = request_host.split(":")[-1] if ":" in request_host else "8000"
    base = f"http://{local_ip}:{port}"
    return {
        "url": f"{base}/",
        "monitor_url": f"{base}/monitor",
        "ip": local_ip,
        "port": port,
    }


def get_tunnel_url_payload() -> dict:
    """Return the active public ngrok tunnel URL for extension auto-config."""
    import urllib.request

    try:
        with urllib.request.urlopen("http://localhost:4040/api/tunnels", timeout=2) as r:
            import json
            data = json.loads(r.read())
            tunnels = [t for t in data.get("tunnels", []) if t.get("proto") == "https"]
            if tunnels:
                return {"url": tunnels[0]["public_url"], "provider": "ngrok"}
    except Exception:
        pass

    return {"url": None, "provider": None}


def build_session_export_response() -> Response:
    answers = answer_storage.get_all_answers()
    completed = [a for a in answers if a.get("is_complete") and a.get("answer")]
    completed = list(reversed(completed))
    payload = {
        "exported_at": datetime.datetime.now().isoformat(),
        "total": len(completed),
        "session": completed,
    }
    return Response(
        __import__("json").dumps(payload, ensure_ascii=False, indent=2),
        mimetype="application/json",
        headers={
            "Content-Disposition":
                f"attachment; filename=interview_session_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        },
    )


def build_session_export_md_response() -> Response:
    answers = answer_storage.get_all_answers()
    completed = [a for a in answers if a.get("is_complete") and a.get("answer")]
    completed = list(reversed(completed))
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"# Interview Session — {ts}\n", f"**{len(completed)} questions**\n\n---\n"]
    for i, ans in enumerate(completed, 1):
        lines.append(f"## Q{i}. {ans.get('question', '').strip()}\n")
        lines.append(f"{ans.get('answer', '').strip()}\n\n---\n")
    md = "\n".join(lines)
    return Response(
        md,
        mimetype="text/markdown",
        headers={
            "Content-Disposition":
                f"attachment; filename=interview_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        },
    )
