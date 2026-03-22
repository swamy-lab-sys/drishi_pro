"""Thread-safe session manager for browser monitoring (Flask-sock compatible)."""
from __future__ import annotations

import json
import uuid
import threading
from collections import defaultdict, deque
from typing import Any

LOG_LIMIT = 300


def _send_json(ws, data: dict) -> bool:
    try:
        ws.send(json.dumps(data))
        return True
    except Exception:
        return False


class MonitorSession:
    def __init__(self):
        self.lock = threading.Lock()
        self.sender_ws = None
        self.sender_id: str | None = None
        self.viewers: dict[str, Any] = {}   # viewer_id -> ws
        self.agent_ws = None
        self.agent_id: str | None = None
        self.ws_index: dict[Any, tuple[str, str]] = {}  # ws -> (role, id)
        self.state: dict[str, Any] = {
            "current_url": "",
            "mouse_position": None,
            "last_click": None,
            "last_key": None,
        }
        self.control: dict[str, Any] = {
            "controller_id": None,
            "control_token": None,
            "control_enabled": False,
            "pending_request": None,
        }
        self.logs: deque = deque(maxlen=LOG_LIMIT)


class MonitorManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: dict[str, MonitorSession] = {}

    def _get_session(self, session_id: str) -> MonitorSession:
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = MonitorSession()
            return self._sessions[session_id]

    def _metrics(self, s: MonitorSession) -> dict:
        """Must be called with s.lock held."""
        sender_connected = s.sender_ws is not None
        agent_conn = s.agent_ws is not None
        return {
            "connected_viewers": len(s.viewers),
            "sender_connected": sender_connected,
            "agent_connected": agent_conn,
            "can_control": sender_connected or agent_conn,
        }

    def metrics_for_session(self, session_id: str) -> dict:
        """Like metrics() but also checks default agent fallback."""
        m = self.metrics(session_id)
        if not m["agent_connected"] and session_id != "default":
            default_s = self._get_session("default")
            with default_s.lock:
                if default_s.agent_ws is not None:
                    m["agent_connected"] = True
                    m["can_control"] = True
        return m

    def metrics(self, session_id: str) -> dict:
        s = self._get_session(session_id)
        with s.lock:
            return self._metrics(s)

    def _broadcast_to_viewers(self, s: MonitorSession, payload: dict) -> list[str]:
        """Send payload to all viewers. Returns list of dead viewer IDs. Call outside lock."""
        with s.lock:
            viewers_copy = list(s.viewers.items())
        dead = []
        for vid, vws in viewers_copy:
            if not _send_json(vws, payload):
                dead.append(vid)
        return dead

    def _remove_dead_viewers(self, s: MonitorSession, dead: list[str]) -> None:
        if not dead:
            return
        with s.lock:
            for vid in dead:
                s.viewers.pop(vid, None)

    def _broadcast_metrics(self, session_id: str, s: MonitorSession) -> None:
        """Broadcast metrics to all viewers. Call outside lock."""
        payload = {
            "type": "session_metrics",
            "session_id": session_id,
            "metrics": self.metrics(session_id),
        }
        dead = self._broadcast_to_viewers(s, payload)
        self._remove_dead_viewers(s, dead)

    def register_sender(self, ws, session_id: str) -> dict:
        s = self._get_session(session_id)
        sender_id = uuid.uuid4().hex[:8]
        with s.lock:
            s.sender_ws = ws
            s.sender_id = sender_id
            s.ws_index[ws] = ("sender", sender_id)
            s.state["session_id"] = session_id
            viewer_ids = list(s.viewers.keys())
        self._broadcast_metrics(session_id, s)
        return {
            "type": "session_ready",
            "session_id": session_id,
            "role": "sender",
            "client_id": sender_id,
            "viewer_ids": viewer_ids,
        }

    def register_viewer(self, ws, session_id: str) -> dict:
        s = self._get_session(session_id)
        viewer_id = uuid.uuid4().hex[:8]
        with s.lock:
            s.viewers[viewer_id] = ws
            s.ws_index[ws] = ("viewer", viewer_id)
            snapshot = {
                "type": "session_snapshot",
                "session_id": session_id,
                "client_id": viewer_id,
                "state": dict(s.state),
                "metrics": self._metrics(s),
                "logs": list(s.logs),
            }
            sender_ws = s.sender_ws
        self._broadcast_metrics(session_id, s)
        if sender_ws:
            _send_json(sender_ws, {
                "type": "signal",
                "signal_type": "viewer_joined",
                "session_id": session_id,
                "viewer_id": viewer_id,
                "data": {},
            })
        return snapshot

    def register_agent(self, ws, session_id: str) -> dict:
        s = self._get_session(session_id)
        agent_id = uuid.uuid4().hex[:8]
        with s.lock:
            s.agent_ws = ws
            s.agent_id = agent_id
            s.ws_index[ws] = ("agent", agent_id)
        self._broadcast_metrics(session_id, s)
        return {"type": "agent_ready", "session_id": session_id, "client_id": agent_id}

    def unregister(self, ws, session_id: str | None) -> None:
        if not session_id:
            return
        s = self._get_session(session_id)
        with s.lock:
            info = s.ws_index.pop(ws, None)
            if not info:
                return
            role, client_id = info
            sender_ws = s.sender_ws

        if role == "viewer":
            with s.lock:
                s.viewers.pop(client_id, None)
                # Clear stale control state owned by this viewer
                if s.control.get("pending_request") == client_id:
                    s.control["pending_request"] = None
                if s.control.get("controller_id") == client_id and s.control.get("control_enabled"):
                    s.control.update({
                        "controller_id": None,
                        "control_token": None,
                        "control_enabled": False,
                        "pending_request": None,
                    })
            self._broadcast_metrics(session_id, s)
            if sender_ws:
                _send_json(sender_ws, {
                    "type": "signal",
                    "signal_type": "viewer_left",
                    "session_id": session_id,
                    "viewer_id": client_id,
                    "data": {},
                })
        elif role == "sender":
            with s.lock:
                if s.sender_ws is ws:
                    s.sender_ws = None
                    s.sender_id = None
            self._broadcast_metrics(session_id, s)
        elif role == "agent":
            with s.lock:
                if s.agent_ws is ws:
                    s.agent_ws = None
                    s.agent_id = None
                    s.control = {
                        "controller_id": None,
                        "control_token": None,
                        "control_enabled": False,
                        "pending_request": None,
                    }
            self._broadcast_metrics(session_id, s)
            dead = self._broadcast_to_viewers(s, {
                "type": "control_status",
                "status": "agent_missing",
                "message": "Control agent disconnected.",
            })
            self._remove_dead_viewers(s, dead)

    def send_to_sender(self, session_id: str, payload: dict) -> bool:
        s = self._get_session(session_id)
        with s.lock:
            sender_ws = s.sender_ws
        return _send_json(sender_ws, payload) if sender_ws else False

    def send_to_viewer(self, session_id: str, viewer_id: str, payload: dict) -> bool:
        s = self._get_session(session_id)
        with s.lock:
            vws = s.viewers.get(viewer_id)
        return _send_json(vws, payload) if vws else False

    def send_to_agent(self, session_id: str, payload: dict) -> bool:
        s = self._get_session(session_id)
        with s.lock:
            aws = s.agent_ws
        if aws:
            return _send_json(aws, payload)
        # Fall back to the global default agent (started by run.sh on the host machine)
        if session_id != "default":
            return self.send_to_agent("default", payload)
        return False

    def broadcast(self, session_id: str, payload: dict) -> None:
        s = self._get_session(session_id)
        dead = self._broadcast_to_viewers(s, payload)
        self._remove_dead_viewers(s, dead)

    def apply_event(self, session_id: str, event_type: str, data: dict) -> dict:
        s = self._get_session(session_id)
        with s.lock:
            st = s.state
            st["session_id"] = session_id
            if event_type in {"active_url", "url_change", "tab_change", "page_loaded"}:
                st["current_url"] = data.get("url", "")
            elif event_type == "mousemove":
                st["mouse_position"] = {"x": data.get("x"), "y": data.get("y")}
            elif event_type == "click":
                st["last_click"] = {"x": data.get("x"), "y": data.get("y")}
                st["mouse_position"] = {"x": data.get("x"), "y": data.get("y")}
            elif event_type == "keydown":
                st["last_key"] = data.get("key")
            log_entry = {"type": "event", "event_type": event_type, "data": data}
            s.logs.append(log_entry)
        return log_entry

    def agent_connected(self, session_id: str) -> bool:
        s = self._get_session(session_id)
        with s.lock:
            if s.agent_ws is not None:
                return True
        # Fall back to the global default agent
        if session_id != "default":
            default_s = self._get_session("default")
            with default_s.lock:
                return default_s.agent_ws is not None
        return False

    def get_control(self, session_id: str) -> dict:
        s = self._get_session(session_id)
        with s.lock:
            return dict(s.control)

    def set_control_request(self, session_id: str, viewer_id: str) -> None:
        s = self._get_session(session_id)
        with s.lock:
            s.control["pending_request"] = viewer_id

    def clear_control_request(self, session_id: str) -> None:
        s = self._get_session(session_id)
        with s.lock:
            s.control["pending_request"] = None

    def set_controller(self, session_id: str, viewer_id: str, token: str) -> None:
        s = self._get_session(session_id)
        with s.lock:
            s.control.update({
                "controller_id": viewer_id,
                "control_token": token,
                "control_enabled": True,
                "pending_request": None,
            })

    def clear_controller(self, session_id: str) -> None:
        s = self._get_session(session_id)
        with s.lock:
            s.control.update({
                "controller_id": None,
                "control_token": None,
                "control_enabled": False,
                "pending_request": None,
            })
