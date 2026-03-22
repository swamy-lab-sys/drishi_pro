/* ── Element references ── */
const el = {
  hubDot: document.getElementById("hub-dot"),
  hubStatusText: document.getElementById("hub-status-text"),
  sessionLabel: document.getElementById("session-label"),
  screenPreview: document.getElementById("screen-preview"),
  remoteCursor: document.getElementById("remote-cursor"),
  emptyScreen: document.getElementById("empty-screen"),
  disconnectBtn: document.getElementById("disconnect-btn"),
  controlBtn: document.getElementById("control-btn"),
  controlOverlay: document.getElementById("control-active-overlay"),
  activityFeed: document.getElementById("activity-feed"),
  viewerCount: document.getElementById("viewer-count"),
  currentUrl: document.getElementById("current-url"),
  sessionTimer: document.getElementById("session-timer"),
  noteToggleBtn: document.getElementById("note-toggle-btn"),
  notePanel: document.getElementById("note-panel"),
  noteBody: document.getElementById("note-body"),
  appShell: document.querySelector(".app-shell"),
  copyNotesBtn: document.getElementById("copy-notes-btn"),
  sessionNotes: document.getElementById("session-notes"),
  fullscreenBtn: document.getElementById("fullscreen-btn"),
  controlHub: document.getElementById("control-hub"),
  hubDragHandle: document.getElementById("hub-drag-handle"),
  activityHub: document.getElementById("activity-hub"),
  activityClear: document.getElementById("activity-clear"),
  screenStage: document.getElementById("screen-stage"),
};

/* ── Element references for backwards compatibility (hidden elements) ── */
const ghostEl = {
  connectionStatus: document.getElementById("connection-status"),
  connectionSidebar: document.getElementById("connection-status-sidebar"),
  topbarSession: document.getElementById("topbar-session"),
  controlStatusText: document.getElementById("control-status-text"),
  lastClick: document.getElementById("last-click"),
  lastKey: document.getElementById("last-key"),
  sidebar: document.getElementById("sidebar"),
};

/* ── Config ── */
const RTC_CONFIGURATION = window.RTC_CONFIGURATION || { iceServers: [{ urls: ["stun:stun.l.google.com:19302"] }] };
const RECONNECT_DELAYS = [1000, 2000, 5000, 10000, 15000];
let _userDisconnected = false;
/* ── Logic to fetch session_id and key from path or query ── */
function getUrlParams() {
  const urlParams = new URLSearchParams(window.location.search);
  let sid = urlParams.get("session_id")?.trim();
  let key = urlParams.get("key")?.trim();

  // Try clean path: /v/603410/12
  const pathParts = window.location.pathname.split('/');
  if (pathParts[1] === 'v') {
    if (!sid && pathParts[2]) sid = pathParts[2];
    if (!key && pathParts[3]) {
      key = pathParts[3] === 'none' ? '' : pathParts[3];
    }
  }
  return { sid: sid || "default", key: key || "" };
}

const urlData = getUrlParams();
const sessionId = urlData.sid;
const urlSecretKey = urlData.key;

/* ── State ── */
let socket = null;
let reconnectTimer = null;
let reconnectAttempt = 0;
let receiver = null;
let viewerId = null;
let pendingSignals = [];
let sessionStartedAt = null;
let timerInterval = null;
let _pingInterval = null;

/* ── Remote Control ── */
const remoteControl = window.RemoteControlManager?.init({
  video: el.screenPreview,
  button: el.controlBtn,
  status: null,
}) ?? null;

/* ═══════════════════════════════════════
   HELPERS
═══════════════════════════════════════ */
function buildWsUrl() {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = new URL(`${proto}//${window.location.host}/ws/monitor`);
  url.searchParams.set('ngrok-skip-browser-warning', '1');
  return url.toString();
}

function formatTime(date) {
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function elapsed(startAt) {
  const secs = Math.floor((Date.now() - startAt) / 1000);
  const mm = String(Math.floor(secs / 60)).padStart(2, "0");
  const ss = String(secs % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

/* ═══════════════════════════════════════
   CONNECTION STATUS
═══════════════════════════════════════ */
function setConnectionStatus(text, state = "disconnected") {
  if (el.hubStatusText) el.hubStatusText.textContent = text;
  if (el.hubDot) el.hubDot.className = `status-dot ${state}`;

  if (ghostEl.connectionStatus) {
    ghostEl.connectionStatus.textContent = text;
    ghostEl.connectionStatus.className = `connection-chip ${state}`;
  }
}

/* ═══════════════════════════════════════
   ACTIVITY FEED
═══════════════════════════════════════ */
const ACTIVITY_ICONS = {
  tab_change: "🔗",
  page_loaded: "📄",
  click: "🖱️",
  keydown: "⌨️",
  mousemove: "↔️",
  screen_frame: "🖥️",
  url_change: "🌐",
  active_url: "📍",
  marker: "🚩",
  round_started: "🚩",
  default: "⚡",
};

function describeEvent(ev) {
  switch (ev.event_type || ev.type) {
    case "tab_change": return `Tab → ${abbreviate(ev.data?.url || ev.url || "?", 32)}`;
    case "page_loaded": return `Loaded: ${abbreviate(ev.data?.url || ev.url || "?", 32)}`;
    case "click": return `Click (${ev.data?.x ?? ev.x}, ${ev.data?.y ?? ev.y})`;
    case "keydown": return `Key: ${ev.data?.key || ev.key}`;
    case "active_url": return `Active: ${abbreviate(ev.data?.url || ev.url || "?", 32)}`;
    case "url_change": return `URL: ${abbreviate(ev.data?.url || ev.url || "?", 32)}`;
    default: return `${ev.event_type || ev.type || "event"}`;
  }
}

function abbreviate(text, max) {
  if (!text) return "";
  return text.length > max ? "…" + text.slice(-(max - 1)) : text;
}

function prependActivity(eventPayload) {
  const empty = el.activityFeed?.querySelector(".activity-empty");
  if (empty) empty.remove();

  const icon = ACTIVITY_ICONS[eventPayload.event_type || eventPayload.type] || ACTIVITY_ICONS.default;
  const item = document.createElement("div");
  item.className = "activity-item";
  item.innerHTML = `
    <span class="activity-icon">${icon}</span>
    <div class="activity-msg">${describeEvent(eventPayload)}</div>`;
  el.activityFeed?.prepend(item);

  const items = el.activityFeed?.querySelectorAll(".activity-item") || [];
  if (items.length > 40) items[items.length - 1].remove();
}

/* ═══════════════════════════════════════
   STATE STRIP UPDATES
═══════════════════════════════════════ */
function applyEventToStrip(ev) {
  const data = ev.data || ev;
  const evType = ev.event_type || ev.type;

  if (data.url && el.currentUrl) {
    el.currentUrl.textContent = abbreviate(data.url, 80);
  }
  if (evType === "click") {
    showClickPulse(data.x, data.y);
  }
  if (evType === "mousemove" || evType === "click") {
    updateRemoteCursor(data.x, data.y);
  }
}

function updateRemoteCursor(x, y) {
  if (!el.remoteCursor || !el.screenPreview || !x || !y) return;
  el.remoteCursor.style.display = "block";
  el.remoteCursor.style.left = `${(x / window.innerWidth) * 100}%`;
  el.remoteCursor.style.top = `${(y / window.innerHeight) * 100}%`;
}

function showClickPulse(x, y) {
  if (!el.screenStage) return;
  const pulse = document.createElement("div");
  pulse.className = "click-pulse";
  pulse.style.left = `${(x / window.innerWidth) * 100}%`;
  pulse.style.top = `${(y / window.innerHeight) * 100}%`;
  el.screenStage.appendChild(pulse);
  setTimeout(() => pulse.remove(), 400);
}

/* ═══════════════════════════════════════
   SESSION TIMER
═══════════════════════════════════════ */
function startTimer() {
  if (timerInterval) return;
  sessionStartedAt = sessionStartedAt || Date.now();
  timerInterval = setInterval(() => {
    if (el.sessionTimer) el.sessionTimer.textContent = elapsed(sessionStartedAt);
  }, 1000);
}

function stopTimer() {
  clearInterval(timerInterval);
  timerInterval = null;
}

/* ═══════════════════════════════════════
   CONTROL UI
═══════════════════════════════════════ */
function updateControlUI(status, message) {
  const btn = el.controlBtn;
  const overlay = el.controlOverlay;

  if (ghostEl.controlStatusText) {
    ghostEl.controlStatusText.textContent = status;
  }

  const skPanel = document.getElementById("shortcut-panel");
  switch (status) {
    case "granted":
      if (overlay) overlay.style.display = "flex";
      if (btn) { btn.style.display = "flex"; btn.style.color = "var(--success)"; btn.title = "Stop Remote Control"; }
      if (skPanel) skPanel.style.display = "flex";
      break;
    case "available":
      if (overlay) overlay.style.display = "none";
      if (btn) { btn.style.display = "flex"; btn.style.color = ""; btn.disabled = false; btn.title = "Request Remote Control"; }
      break;
    case "requested":
    case "pending":
      if (btn) { btn.style.display = "flex"; btn.style.color = "var(--warning)"; }
      break;
    case "disabled":
    case "agent_missing":
    case "unauthorized":
      if (overlay) overlay.style.display = "none";
      if (skPanel) skPanel.style.display = "none";
      if (btn) { btn.style.display = "none"; }
      break;
    default:
      if (overlay) overlay.style.display = "none";
      if (skPanel) skPanel.style.display = "none";
      if (btn) btn.style.display = "none";
  }
}

function showControlPrompt(text) {
  const prompt = document.createElement("div");
  prompt.style.cssText = `
    position: fixed; top: 20px; left: 50%; transform: translateX(-50%);
    background: rgba(76, 175, 80, 0.9); color: white; padding: 10px 20px;
    border-radius: 30px; font-weight: bold; z-index: 9999;
    backdrop-filter: blur(8px); box-shadow: 0 4px 15px rgba(0,0,0,0.3);
    pointer-events: none; transition: opacity 0.5s ease;
  `;
  prompt.textContent = text;
  document.body.appendChild(prompt);
  setTimeout(() => {
    prompt.style.opacity = "0";
    setTimeout(() => prompt.remove(), 500);
  }, 3000);
}

/* ═══════════════════════════════════════
   SCREEN SHOW / HIDE
═══════════════════════════════════════ */
function showScreen() {
  if (el.screenPreview) el.screenPreview.style.display = "block";
  if (el.emptyScreen) el.emptyScreen.style.display = "none";
  startTimer();
}

function hideScreen() {
  if (el.screenPreview) {
    el.screenPreview.srcObject = null;
    el.screenPreview.style.display = "none";
  }
  if (el.emptyScreen) el.emptyScreen.style.display = "flex";
}

function toggleFullscreen() {
  if (!document.fullscreenElement) {
    el.appShell?.requestFullscreen().catch(err => console.error(err));
  } else {
    document.exitFullscreen();
  }
}

/* ═══════════════════════════════════════
   WEBRTC RECEIVER
═══════════════════════════════════════ */
function ensureReceiver() {
  if (receiver) return receiver;

  receiver = new WebRTCReceiver({
    sessionId,
    viewerId,
    sendSignal: (signalType, payload) => {
      if (!socket || socket.readyState !== WebSocket.OPEN) return;
      socket.send(JSON.stringify({
        type: "signal",
        signal_type: signalType,
        session_id: sessionId,
        viewer_id: payload.viewer_id,
        data: payload.data,
      }));
    },
    onRemoteStream: (stream) => {
      if (el.screenPreview) {
        el.screenPreview.srcObject = stream;
        el.screenPreview.muted = true;
      }
      showScreen();
    },
    onStateChange: (status) => {
      const connected = status === "streaming" || status === "answer_sent";
      setConnectionStatus(
        connected ? "Streaming" : status,
        connected ? "connected" : "connecting"
      );
    },
  });

  return receiver;
}

function resetReceiver() {
  if (receiver) { receiver.reset(); receiver = null; }
  hideScreen();
  updateControlUI("disabled");
  if (el.controlOverlay) el.controlOverlay.style.display = "none";
  if (el.screenPreview) el.screenPreview.classList.remove("rc-active");
}

/* ═══════════════════════════════════════
   SIGNAL QUEUE
═══════════════════════════════════════ */
function flushPendingSignals() {
  if (!receiver || !viewerId || pendingSignals.length === 0) return;
  const queued = pendingSignals;
  pendingSignals = [];
  queued.forEach(handleSignal);
}

function handleSignal(payload) {
  if (!receiver || !viewerId) { pendingSignals.push(payload); return; }
  if (payload.signal_type === "offer") {
    receiver.handleOffer(payload.data).catch(err => {
      console.error("viewer: offer error", err);
      setConnectionStatus("Offer error", "disconnected");
    });
  } else if (payload.signal_type === "ice_candidate") {
    receiver.handleIceCandidate(payload.data).catch(() => { });
  }
}

/* ═══════════════════════════════════════
   RECONNECT
═══════════════════════════════════════ */
function scheduleReconnect() {
  if (reconnectTimer || _userDisconnected) return;
  const delay = RECONNECT_DELAYS[Math.min(reconnectAttempt, RECONNECT_DELAYS.length - 1)];
  reconnectAttempt++;
  const label = reconnectAttempt > 3 ? "Server offline — retrying…" : `Reconnecting…`;
  setConnectionStatus(label, "connecting");
  reconnectTimer = setTimeout(() => { reconnectTimer = null; connectViewer(); }, delay);
}

/* ═══════════════════════════════════════
   WEBSOCKET
═══════════════════════════════════════ */
function connectViewer() {
  if (socket && socket.readyState !== WebSocket.CLOSED) return;

  _userDisconnected = false;
  setConnectionStatus("Connecting…", "connecting");
  socket = new WebSocket(buildWsUrl());

  socket.addEventListener("open", () => {
    socket.send(JSON.stringify({ type: "register", role: "viewer", session_id: sessionId }));
    reconnectAttempt = 0;
    setConnectionStatus("Connected", "connected");
    // Keep connection alive — server times out at 300s, ping every 60s
    if (_pingInterval) clearInterval(_pingInterval);
    _pingInterval = setInterval(() => {
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "ping", session_id: sessionId }));
      }
    }, 60000);
    if (el.sessionLabel) el.sessionLabel.textContent = sessionId;
    if (el.disconnectBtn) el.disconnectBtn.style.display = "flex";
    if (el.controlHub) el.controlHub.style.display = "flex";

    remoteControl?.bindSocket((payload) => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(payload));
    });

    updateControlUI("disabled");
    if (el.controlBtn) el.controlBtn.disabled = false;
  });

  socket.addEventListener("message", (ev) => {
    let payload;
    try { payload = JSON.parse(ev.data); } catch { return; }

    if (payload.type === "session_snapshot") {
      const prevViewerId = viewerId;
      viewerId = payload.client_id || viewerId;
      ensureReceiver();
      remoteControl?.setViewerId(viewerId);
      flushPendingSignals();

      const state = payload.state || {};
      if (state.current_url && el.currentUrl) el.currentUrl.textContent = abbreviate(state.current_url, 80);

      (payload.logs || []).slice(-20).forEach(entry => prependActivity(entry));

      if (payload.metrics) {
        if (el.viewerCount) el.viewerCount.textContent = payload.metrics.connected_viewers ?? "0";
        const canControl = payload.metrics.can_control || payload.metrics.agent_connected || payload.metrics.sender_connected;
        updateControlUI(canControl ? "available" : "agent_missing");
      }

      // Auto-request control on every connect/reconnect (handles new viewer_id after page reload)
      const isQuickLink = window.location.pathname.startsWith('/v');
      if (isQuickLink && !remoteControl?.active) {
        setTimeout(() => {
          if (!remoteControl?.active && socket?.readyState === WebSocket.OPEN) {
            const req = { type: "control_request", auto_trust: true };
            if (urlSecretKey && urlSecretKey !== 'none') req.secret = urlSecretKey;
            socket.send(JSON.stringify(req));
          }
        }, 500);
      }
      return;
    }

    if (payload.type === "session_metrics") {
      if (el.viewerCount) el.viewerCount.textContent = payload.metrics?.connected_viewers ?? "0";
      const canControl = payload.metrics?.can_control || payload.metrics?.agent_connected || payload.metrics?.sender_connected;
      if (!remoteControl?.active) updateControlUI(canControl ? "available" : "agent_missing");
      return;
    }

    if (payload.type === "control_status") {
      remoteControl?.handleStatus(payload);
      updateControlUI(payload.status, payload.message);
      return;
    }

    if (payload.type === "signal") {
      handleSignal(payload);
      return;
    }

    if (payload.type === "event") {
      applyEventToStrip(payload);
      prependActivity(payload);
      return;
    }
  });

  socket.addEventListener("close", () => {
    if (_pingInterval) { clearInterval(_pingInterval); _pingInterval = null; }
    pendingSignals = [];
    resetReceiver();
    stopTimer();
    // Always auto-reconnect unless user explicitly clicked disconnect
    // ngrok/server closes with code 1000 on timeout — must still reconnect
    if (!_userDisconnected) {
      scheduleReconnect();
    } else {
      setConnectionStatus("Disconnected", "disconnected");
    }
    remoteControl?.reset();
    if (el.controlBtn) el.controlBtn.disabled = true;
    if (el.disconnectBtn) el.disconnectBtn.style.display = "none";
  });
}

function disconnectViewer() {
  _userDisconnected = true;
  if (socket) {
    const s = socket;
    socket = null;
    s.close();
  }
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  resetReceiver();
  setConnectionStatus("Disconnected", "disconnected");
  stopTimer();
  if (el.controlBtn) el.controlBtn.disabled = true;
  if (el.disconnectBtn) el.disconnectBtn.style.display = "none";
}

/* ═══════════════════════════════════════
   DRAGGABLE HUB
═══════════════════════════════════════ */
function initDraggable(element, handle) {
  let pos1 = 0, pos2 = 0, pos3 = 0, pos4 = 0;
  handle.onmousedown = dragMouseDown;

  function dragMouseDown(e) {
    e = e || window.event;
    e.preventDefault();
    pos3 = e.clientX;
    pos4 = e.clientY;
    document.onmouseup = closeDragElement;
    document.onmousemove = elementDrag;
    element.style.transition = 'none';
  }

  function elementDrag(e) {
    e = e || window.event;
    e.preventDefault();
    pos1 = pos3 - e.clientX;
    pos2 = pos4 - e.clientY;
    pos3 = e.clientX;
    pos4 = e.clientY;
    element.style.top = (element.offsetTop - pos2) + "px";
    element.style.left = (element.offsetLeft - pos1) + "px";
    element.style.transform = 'none'; // Clear translate for manual pos
  }

  function closeDragElement() {
    document.onmouseup = null;
    document.onmousemove = null;
    element.style.transition = 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)';
  }
}

/* ═══════════════════════════════════════
   INIT
═══════════════════════════════════════ */
window.addEventListener("load", () => {
  if (el.controlHub && el.hubDragHandle) {
    initDraggable(el.controlHub, el.hubDragHandle);
  }

  el.disconnectBtn?.addEventListener("click", disconnectViewer);

  el.noteToggleBtn?.addEventListener("click", () => {
    el.noteBody?.classList.toggle("collapsed");
  });

  document.querySelector(".note-toggle")?.addEventListener("click", () => {
    el.noteBody?.classList.toggle("collapsed");
  });

  el.copyNotesBtn?.addEventListener("click", () => {
    if (!el.sessionNotes) return;
    navigator.clipboard.writeText(el.sessionNotes.value);
    el.copyNotesBtn.textContent = "Copied!";
    setTimeout(() => { if (el.copyNotesBtn) el.copyNotesBtn.textContent = "Copy Notes"; }, 1500);
  });

  el.fullscreenBtn?.addEventListener("click", toggleFullscreen);
  el.activityClear?.addEventListener("click", () => {
    if (el.activityFeed) el.activityFeed.innerHTML = '<div class="activity-empty">Cleared</div>';
  });

  // Shortcut panel — send OS/browser-intercepted keys via button clicks
  document.querySelectorAll(".sk-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      if (!remoteControl?.active) return;
      const keys = btn.dataset.keys.split("+");
      const hasCtrl  = keys.includes("ctrl");
      const hasAlt   = keys.includes("alt");
      const hasShift = keys.includes("shift");
      const mainKey  = keys[keys.length - 1];
      const sc = (action, key) => remoteControl.sendControl({ type: "control", action, key });
      if (hasCtrl)  sc("key_down", "ctrl");
      if (hasAlt)   sc("key_down", "alt");
      if (hasShift) sc("key_down", "shift");
      sc("key_down", mainKey);
      sc("key_up",   mainKey);
      if (hasShift) sc("key_up", "shift");
      if (hasAlt)   sc("key_up", "alt");
      if (hasCtrl)  sc("key_up", "ctrl");
    });
  });

  updateControlUI("disabled");
  connectViewer();
});
