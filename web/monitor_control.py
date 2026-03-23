"""Browser monitor remote-control routing (Flask-sock / thread-safe)."""
from __future__ import annotations

import time
import uuid
from collections import defaultdict
from typing import Any

import config
from monitor_manager import MonitorManager

COMMAND_WINDOW_SECONDS = 1.0
COMMAND_LIMIT = 180
TRUSTED_SECRET = "12"

ALLOWED_ACTIONS = {
    "mouse_move", "mouse_click", "mouse_down", "mouse_up",
    "scroll", "key_press", "key_down", "key_up", "marker",
}

_command_counters: dict[tuple[str, str], dict[str, Any]] = defaultdict(
    lambda: {"count": 0, "window_start": 0.0}
)


def _reset_counter(session_id: str, viewer_id: str) -> None:
    _command_counters.pop((session_id, viewer_id), None)


def _rate_limit(session_id: str, viewer_id: str) -> bool:
    key = (session_id, viewer_id)
    stats = _command_counters[key]
    now = time.time()
    if now - stats["window_start"] > COMMAND_WINDOW_SECONDS:
        stats["window_start"] = now
        stats["count"] = 0
    stats["count"] += 1
    return stats["count"] <= COMMAND_LIMIT


def request_control(manager: MonitorManager, session_id: str, viewer_id: str,
                    payload: dict | None = None) -> None:
    # Check if either native agent or browser extension is available
    has_agent = manager.agent_connected(session_id)
    s = manager._get_session(session_id)
    with s.lock:
        has_sender = s.sender_ws is not None

    if not has_agent and not has_sender:
        manager.send_to_viewer(session_id, viewer_id, {
            "type": "control_status",
            "status": "agent_missing",
            "message": "No control agent or extension connected.",
        })
        return

    provided_secret = payload.get("secret") if isinstance(payload, dict) else None
    # Auto-trust if requested via the simplified direct URL
    is_trusted = (provided_secret == TRUSTED_SECRET) or payload.get("auto_trust") is True

    ctrl = manager.get_control(session_id)
    if ctrl["control_enabled"]:
        existing_controller = ctrl["controller_id"]
        # Check if the existing controller is still connected
        s2 = manager._get_session(session_id)
        with s2.lock:
            controller_still_connected = existing_controller in s2.viewers
        # Trusted viewer can steal control, or take over disconnected controller
        if is_trusted or not controller_still_connected:
            manager.clear_controller(session_id)
        else:
            manager.send_to_viewer(session_id, viewer_id, {
                "type": "control_status",
                "status": "already_active",
                "controller_id": existing_controller,
            })
            return

    if ctrl["pending_request"] and ctrl["pending_request"] != viewer_id:
        if is_trusted:
            manager.clear_control_request(session_id)
        else:
            manager.send_to_viewer(session_id, viewer_id, {
                "type": "control_status",
                "status": "pending",
                "message": "Another viewer has a pending request.",
            })
            return

    print(f"[CTRL] request_control: session={session_id}, viewer={viewer_id}, trusted={is_trusted}, has_agent={has_agent}, has_sender={has_sender}")
    # Auto-approve trusted requests — no manual sender approval needed
    if is_trusted:
        token = uuid.uuid4().hex
        manager.set_controller(session_id, viewer_id, token)
        _reset_counter(session_id, viewer_id)
        manager.send_to_viewer(session_id, viewer_id, {
            "type": "control_status",
            "status": "granted",
            "controller_id": viewer_id,
            "token": token,
        })
        manager.send_to_agent(session_id, {
            "type": "control_session",
            "status": "granted",
            "controller_id": viewer_id,
        })
        return

    manager.set_control_request(session_id, viewer_id)
    manager.send_to_sender(session_id, {
        "type": "control_request",
        "viewer_id": viewer_id,
        "trusted": is_trusted,
        "secret": provided_secret,
    })
    manager.send_to_viewer(session_id, viewer_id, {
        "type": "control_status",
        "status": "requested",
    })


def respond_control(manager: MonitorManager, session_id: str, viewer_id: str,
                    approved: bool) -> None:
    ctrl = manager.get_control(session_id)
    if ctrl["pending_request"] != viewer_id:
        return

    manager.clear_control_request(session_id)
    if not approved:
        manager.send_to_viewer(session_id, viewer_id, {
            "type": "control_status",
            "status": "denied",
        })
        return

    token = uuid.uuid4().hex
    manager.set_controller(session_id, viewer_id, token)
    _reset_counter(session_id, viewer_id)

    manager.send_to_viewer(session_id, viewer_id, {
        "type": "control_status",
        "status": "granted",
        "controller_id": viewer_id,
        "token": token,
    })
    manager.send_to_sender(session_id, {
        "type": "control_status",
        "status": "granted",
        "controller_id": viewer_id,
    })
    manager.send_to_agent(session_id, {
        "type": "control_session",
        "status": "granted",
        "controller_id": viewer_id,
    })


def disable_control(manager: MonitorManager, session_id: str) -> None:
    ctrl = manager.get_control(session_id)
    if not ctrl["control_enabled"]:
        return

    controller_id = ctrl["controller_id"]
    manager.clear_controller(session_id)
    _reset_counter(session_id, controller_id)

    manager.send_to_sender(session_id, {"type": "control_status", "status": "disabled"})
    if controller_id:
        manager.send_to_viewer(session_id, controller_id, {
            "type": "control_status",
            "status": "disabled",
        })
    manager.send_to_agent(session_id, {"type": "control_session", "status": "disabled"})


def handle_control_command(manager: MonitorManager, session_id: str,
                           viewer_id: str, payload: dict) -> None:
    ctrl = manager.get_control(session_id)
    if not ctrl["control_enabled"] or ctrl["controller_id"] != viewer_id:
        manager.send_to_viewer(session_id, viewer_id, {
            "type": "control_status",
            "status": "unauthorized",
        })
        return

    if payload.get("token") != ctrl["control_token"]:
        manager.send_to_viewer(session_id, viewer_id, {
            "type": "control_status",
            "status": "token_mismatch",
        })
        return

    action = payload.get("action")
    if action not in ALLOWED_ACTIONS:
        manager.send_to_viewer(session_id, viewer_id, {
            "type": "control_status",
            "status": "invalid_action",
        })
        return

    if not _rate_limit(session_id, viewer_id):
        manager.send_to_viewer(session_id, viewer_id, {
            "type": "control_status",
            "status": "rate_limited",
        })
        return

    if action == "marker":
        manager.apply_event(session_id, "marker", payload)
        manager.broadcast(session_id, {
            "type": "event",
            "session_id": session_id,
            "event_type": "marker",
            "data": payload,
            "metrics": manager.metrics(session_id),
        })
        return

    # Pass through ratio coordinates for agent to handle screen-size scaling
    rx = payload.get("x_ratio")
    ry = payload.get("y_ratio")
    
    command_data = {k: v for k, v in payload.items() if k != "type"}
    if rx is not None: command_data["rx"] = rx
    if ry is not None: command_data["ry"] = ry

    command_payload = {
        "type": "control_command",
        "action": action,
        "viewer_id": viewer_id,
        **command_data,
    }

    # Priority 1: Native Agent (System-wide control like VSCode)
    if manager.agent_connected(session_id):
        if config.VERBOSE and action not in ("mouse_move",):
            print(f"[WS] CONTROL_CMD→agent: session={session_id}, action={action}")
        manager.send_to_agent(session_id, command_payload)
    else:
        if config.VERBOSE and action not in ("mouse_move",):
            print(f"[WS] CONTROL_CMD→extension: session={session_id}, action={action} (no agent connected)")
        manager.send_to_sender(session_id, command_payload)
