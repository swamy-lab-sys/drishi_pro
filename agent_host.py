#!/usr/bin/env python3
"""
Drishi Native Agent — OS-Level Remote Control via X11 XTEST.

Uses python-xlib XTEST extension for kernel-level input injection:
- Works for ALL OS shortcuts (Super key, Alt+Tab, Ctrl+N, etc.)
- Absolute mouse positioning — no drift
- No xdotool / PyAutoGUI dependency for input
- Zero subprocess overhead — direct X11 protocol calls

Run automatically by run.sh. Do NOT run manually unless debugging.
Usage:
    python3 agent_host.py              # auto-reads session from .env
    python3 agent_host.py 660636       # explicit session_id
"""

import os
import sys
import json
import time
import signal
import subprocess
import threading

_LOG_FILE = "/tmp/drishi_agent.log"

def _log(msg):
    try:
        with open(_LOG_FILE, "a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass

# ── Dependency bootstrap ─────────────────────────────────────────────────────
try:
    import websocket
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "websocket-client"])
    import websocket

try:
    from Xlib import X, XK, display as _xdisplay
    from Xlib.ext import xtest as _xtest
    _HAS_XLIB = True
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "python-xlib"])
    try:
        from Xlib import X, XK, display as _xdisplay
        from Xlib.ext import xtest as _xtest
        _HAS_XLIB = True
    except ImportError:
        _HAS_XLIB = False

# ── X11 keysym map ───────────────────────────────────────────────────────────
# Maps normalised key names (from viewer) → X11 keysym strings
_KEYSYM_MAP = {
    # Modifiers
    "ctrl": "Control_L",    "control": "Control_L",
    "alt": "Alt_L",
    "shift": "Shift_L",
    "altgraph": "Alt_R",
    # Super / Windows / Meta — GNOME Activities, Ubuntu taskbar
    "win": "Super_L",       "meta": "Super_L",      "super": "Super_L",
    # Navigation
    "enter": "Return",      "return": "Return",
    "backspace": "BackSpace",
    "tab": "Tab",
    "esc": "Escape",        "escape": "Escape",
    "space": "space",
    "up": "Up",             "down": "Down",
    "left": "Left",         "right": "Right",
    "delete": "Delete",     "del": "Delete",
    "pageup": "Prior",      "pagedown": "Next",
    "home": "Home",         "end": "End",
    "insert": "Insert",
    "capslock": "Caps_Lock",
    "apps": "Menu",
    "printscreen": "Print",
    # Function keys
    **{f"f{i}": f"F{i}" for i in range(1, 13)},
}


class XTestInput:
    """
    Direct X11 XTEST input driver.
    All operations are synchronous and OS-level (bypasses window focus).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._d = None
        self._screen_w = 1920
        self._screen_h = 1080
        self._connect()

    def _connect(self):
        try:
            self._d = _xdisplay.Display()
            screen = self._d.screen()
            self._screen_w = screen.width_in_pixels
            self._screen_h = screen.height_in_pixels
        except Exception:
            self._d = None

    def _keysym(self, name: str):
        """Resolve a key name string → keysym integer."""
        # 1. Check our normalised map first
        mapped = _KEYSYM_MAP.get(name.lower())
        if mapped:
            ks = XK.string_to_keysym(mapped)
            if ks:
                return ks
        # 2. Single printable ASCII char: use unicode code point directly
        if len(name) == 1:
            cp = ord(name)
            # Lowercase letters a-z → keysym = code point
            # Uppercase letters A-Z → try uppercase keysym
            ks = XK.string_to_keysym(name)
            if ks:
                return ks
            return cp
        # 3. Try xlib direct lookup (handles named keys not in our map)
        ks = XK.string_to_keysym(name)
        if ks:
            return ks
        return 0

    def _sync(self):
        try:
            self._d.sync()
        except Exception:
            self._d = None

    # ── Mouse ─────────────────────────────────────────────────────────────────

    def mouse_move(self, x: int, y: int):
        if not self._d:
            self._connect()
            return
        with self._lock:
            try:
                _xtest.fake_input(self._d, X.MotionNotify, x=x, y=y)
                self._d.flush()
            except Exception:
                self._d = None

    def mouse_button(self, button: int, press: bool):
        if not self._d:
            self._connect()
        with self._lock:
            try:
                event = X.ButtonPress if press else X.ButtonRelease
                _xtest.fake_input(self._d, event, button)
                self._d.flush()
            except Exception:
                self._d = None

    def scroll(self, delta_x: float, delta_y: float):
        """Map deltaX/deltaY → X11 scroll buttons (4/5/6/7)."""
        if not self._d:
            self._connect()
        steps_y = int(abs(delta_y) / 40) or (1 if delta_y else 0)
        steps_x = int(abs(delta_x) / 40) or (1 if delta_x else 0)
        btn_y = 5 if delta_y > 0 else 4   # 4=up, 5=down
        btn_x = 7 if delta_x > 0 else 6   # 6=left, 7=right
        with self._lock:
            try:
                for _ in range(steps_y):
                    _xtest.fake_input(self._d, X.ButtonPress, btn_y)
                    _xtest.fake_input(self._d, X.ButtonRelease, btn_y)
                for _ in range(steps_x):
                    _xtest.fake_input(self._d, X.ButtonPress, btn_x)
                    _xtest.fake_input(self._d, X.ButtonRelease, btn_x)
                self._d.flush()
            except Exception:
                self._d = None

    # ── Keyboard ──────────────────────────────────────────────────────────────

    def key_event(self, key: str, press: bool):
        """Inject key press or release OS-wide (XTEST)."""
        if not self._d:
            self._connect()
        ks = self._keysym(key)
        if not ks:
            _log(f"key_unknown: '{key}'")
            return
        with self._lock:
            try:
                keycode = self._d.keysym_to_keycode(ks)
                if not keycode:
                    _log(f"key_no_keycode: '{key}' ks={hex(ks)}")
                    return
                event = X.KeyPress if press else X.KeyRelease
                _xtest.fake_input(self._d, event, keycode)
                self._d.flush()
                _log(f"key_{'dn' if press else 'up'}: '{key}' kc={keycode}")
            except Exception as e:
                _log(f"key_err: {e}")
                self._d = None

    @property
    def screen_size(self):
        return self._screen_w, self._screen_h


# ── Session / URL helpers ────────────────────────────────────────────────────

def _get_session_id():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    sid = os.environ.get("MONITOR_SESSION_ID", "").strip()
    if sid and sid != "default":
        return sid
    path = os.path.expanduser("~/.drishi/session_id")
    if os.path.exists(path):
        try:
            sid = open(path).read().strip()
            if sid:
                return sid
        except Exception:
            pass
    return None


def _get_ws_url():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    override = os.environ.get("MONITOR_SERVER_URL", "").strip()
    if override:
        url = override.replace("https://", "wss://").replace("http://", "ws://")
        return url.rstrip("/") + "/ws/monitor"
    ngrok = os.environ.get("NGROK_DOMAIN", "").strip()
    if ngrok:
        return f"wss://{ngrok}/ws/monitor"
    port = os.environ.get("WEB_PORT", "8000")
    return f"ws://localhost:{port}/ws/monitor"


# ── Agent ────────────────────────────────────────────────────────────────────

class DrishiAgent:

    def __init__(self, ws_url: str, session_id: str):
        self._ws_url = ws_url
        self._session_id = session_id
        self._ws = None
        self._running = True
        self._input = XTestInput() if _HAS_XLIB else None
        self._screen_w, self._screen_h = (
            self._input.screen_size if self._input else (1920, 1080)
        )

    # ── Command dispatch ──────────────────────────────────────────────────────

    def _handle(self, payload: dict):
        if not self._input:
            return
        action = payload.get("action", "")
        rx = payload.get("rx")
        ry = payload.get("ry")
        x = round(rx * self._screen_w) if rx is not None else payload.get("x") or 0
        y = round(ry * self._screen_h) if ry is not None else payload.get("y") or 0

        if action == "mouse_move":
            self._input.mouse_move(x, y)

        elif action == "mouse_click":
            btn = 1 if payload.get("button", 0) == 0 else 3
            self._input.mouse_move(x, y)
            self._input.mouse_button(btn, True)
            self._input.mouse_button(btn, False)

        elif action == "mouse_down":
            btn = 1 if payload.get("button", 0) == 0 else 3
            self._input.mouse_move(x, y)
            self._input.mouse_button(btn, True)

        elif action == "mouse_up":
            btn = 1 if payload.get("button", 0) == 0 else 3
            self._input.mouse_button(btn, False)

        elif action == "scroll":
            self._input.scroll(
                payload.get("deltaX", 0),
                payload.get("deltaY", 0),
            )

        elif action in ("key_down", "key_press", "keydown"):
            key = payload.get("key", "")
            if key:
                self._input.key_event(key, True)

        elif action == "key_up":
            key = payload.get("key", "")
            if key:
                self._input.key_event(key, False)

    # ── WebSocket callbacks ───────────────────────────────────────────────────

    def _on_open(self, ws):
        _log(f"connected session={self._session_id}")
        ws.send(json.dumps({
            "type": "agent_connect",
            "session_id": self._session_id,
            "role": "agent",
        }))

    def _on_message(self, ws, message):
        try:
            payload = json.loads(message)
            msg_type = payload.get("type")
            if msg_type == "control_command":
                self._handle(payload)
            elif msg_type == "session_change":
                # Server tells agent a new sender registered with a different session
                new_sid = payload.get("session_id", "").strip()
                if new_sid and new_sid != self._session_id:
                    _log(f"session_change → {new_sid}, reconnecting")
                    self._session_id = new_sid
                    ws.close()
        except Exception as e:
            _log(f"msg_error: {e}")

    def _on_error(self, ws, error):
        pass

    def _on_close(self, ws, *args):
        pass

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        headers = ["ngrok-skip-browser-warning: true"]
        while self._running:
            # Re-read session_id on every reconnect — picks up sender's latest session
            fresh_sid = _get_session_id()
            if fresh_sid and fresh_sid != self._session_id:
                _log(f"session_id updated: {self._session_id} → {fresh_sid}")
                self._session_id = fresh_sid
            try:
                self._ws = websocket.WebSocketApp(
                    self._ws_url,
                    header=headers,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self._ws.run_forever(ping_interval=15, ping_timeout=8)
            except Exception:
                pass
            if self._running:
                time.sleep(3)

    def stop(self):
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    session_id = sys.argv[1] if len(sys.argv) > 1 else _get_session_id()
    if not session_id:
        sys.exit(1)

    ws_url = _get_ws_url()
    agent = DrishiAgent(ws_url, session_id)

    def _stop(sig, frame):
        agent.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    agent.run()
