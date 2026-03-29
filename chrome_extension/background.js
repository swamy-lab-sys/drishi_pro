// Drishi Enterprise — Background Service Worker (MV3)
// Handles: browser monitoring (events, WebRTC screen share, remote control), coding proxy

importScripts("monitor_state.js");

// ── Server URL — loaded dynamically from storage ──────────────────────────────
let SERVER_URL  = 'https://particulate-arely-unrenovative.ngrok-free.dev';
let SECRET_CODE = '';

// ── Audio stream state ────────────────────────────────────────────────────────
let audioOffscreenCreated = false;
let audioStreamActive = false;

// ── Sarvam key cache (avoid repeated /api/stt_config fetches on quick restart)
let _sarvamKeyCache    = null;
let _sarvamKeyCacheAt  = 0;
const SARVAM_KEY_TTL_MS = 60000; // 60 seconds

// ── Ngrok bypass ─────────────────────────────────────────────────────
const NGROK_HEADERS = { 'ngrok-skip-browser-warning': 'true' };
function apiFetch(url, opts = {}) {
  return fetch(url, { ...opts, headers: { ...NGROK_HEADERS, ...(opts.headers || {}) } });
}

// ── Remote logging — prints to laptop1 terminal ───────────────────────────────
let _rlogToken = '';
function rlog(source, msg, level = 'info') {
  console.log(`[rlog/${source}] ${msg}`);
  apiFetch(`${SERVER_URL}/api/ext/log`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source, msg: String(msg), level, token: _rlogToken }),
  }).catch(() => {});
}


// Load settings on startup
chrome.storage.sync.get({ serverUrl: 'https://particulate-arely-unrenovative.ngrok-free.dev', secretCode: '', userToken: '' }, (data) => {
  SERVER_URL  = data.serverUrl;
  SECRET_CODE = data.secretCode;
  _rlogToken  = data.userToken || '';
  // Try to auto-discover tunnel URL from local server
  _autoDiscoverTunnelUrl();
});

chrome.storage.onChanged.addListener((changes) => {
  if (changes.serverUrl)  SERVER_URL  = changes.serverUrl.newValue;
  if (changes.secretCode) SECRET_CODE = changes.secretCode.newValue;
  if (changes.userToken)  _rlogToken  = changes.userToken.newValue || '';
});

// ── Install handler ────────────────────────────────────────────────────────────
chrome.runtime.onInstalled.addListener((details) => {
  if (details.reason === 'install') {
    console.log('[Drishi] Installed v4.1 (Stable Bridge)');
  }
});

// ── Message listener ──────────────────────────────────────────────────────────
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'checkServer') {
    apiFetch(`${SERVER_URL}/health`, { signal: AbortSignal.timeout(3000) })
      .then(async r => {
        try {
          const data = await r.json();
          sendResponse({ running: r.ok && data.status === 'ok' });
        } catch (e) {
          sendResponse({ running: false });
        }
      })
      .catch(() => sendResponse({ running: false }));
    return true; // async
  }

  // ── Audio stream messages ─────────────────────────────────────────────────
  if (request.action === 'audio_start') { handleAudioStart(sendResponse, request); return true; }
  if (request.action === 'audio_stop')  { handleAudioStop(sendResponse); return true; }
  if (request.type === 'remote_start_capture') { handleRemoteStartCapture(sendResponse); return true; }
  if (request.type === 'remote_stop_capture')  { handleAudioStop(sendResponse); return true; }
  if (request.type === 'audio_status') {
    audioStreamActive = request.status === 'streaming';
    chrome.storage.local.set({ audioStreamStatus: request.status || 'stopped' });
    sendResponse({ ok: true }); return false;
  }

  // ── Code typer proxy messages ────────────────────────────────────────────
  if (request.type === 'SOLVE_PROBLEM_PROXY') { handleSolveRequest(request.payload, sendResponse); return true; }
  if (request.type === 'FETCH_SOLUTION_BY_INDEX') { handleFetchSolutionByIndex(request.index, sendResponse); return true; }
  if (request.type === 'CONTROL_START') { handleControlStart(sendResponse); return true; }
  if (request.type === 'SOLVE_CHAT_PROXY') { handleSolveChatProxy(request.payload, sendResponse); return true; }
  if (request.type === 'EXT_LOG') { rlog(request.source || 'ext', request.msg, request.level || 'info'); sendResponse({ ok: true }); return false; }

  // ── Monitor message listener ───────────────────────────────────────────────
  if (request.type === "mon_get_state") {
    monLoadState().then(() => sendResponse(monCurrentSettings));
    return true;
  }
  if (request.type === "mon_start_monitoring") {
    monStartMonitoring(request)
      .then(() => sendResponse({ ok: true }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }
  if (request.type === "mon_stop_monitoring") {
    monStopMonitoring()
      .then(() => sendResponse({ ok: true }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }
  if (request.type === "mon_capture_host_ready") {
    monMarkCaptureReady(); sendResponse({ ok: true }); return false;
  }
  if (request.type === "mon_screen_capture_started") {
    monUpdateState({ screenStatus: "streaming", screenEnabled: true, lastError: "" });
    monCaptureRestartAttempts = 0;
    _broadcastToAllTabs({ type: 'drishi_screen_share_state', active: true });
    sendResponse({ ok: true });
    return false;
  }
  if (request.type === "mon_screen_capture_failed") {
    monUpdateState({ screenStatus: "error", screenEnabled: false, lastError: request.error || "Screen capture failed", streamViewerCount: 0 });
    sendResponse({ ok: true }); return false;
  }
  if (request.type === "mon_screen_capture_stopped") {
    const reason = request.reason || "manual";
    const interrupted = reason === "interrupted";
    monUpdateState({
      screenStatus: interrupted ? "interrupted" : (monCurrentSettings.monitoring ? "stopped" : "idle"),
      screenEnabled: interrupted ? true : false,
      streamViewerCount: 0,
      lastError: interrupted ? (request.error || monCurrentSettings.lastError) : "",
    });
    _broadcastToAllTabs({ type: 'drishi_screen_share_state', active: false });
    sendResponse({ ok: true });
    if (interrupted) monQueueCaptureRestart(reason);
    return false;
  }
  if (request.type === "mon_webrtc_viewer_count") {
    monUpdateState({ streamViewerCount: request.count }); sendResponse({ ok: true }); return false;
  }
  if (request.type === "mon_browser_event") {
    if (monCurrentSettings.monitoring) {
      monSendToBackend(monWrapEvent(request.payload.type, {
        ...request.payload, tabId: sender.tab?.id, url: request.payload.url || sender.tab?.url || "",
      }));
    }
    sendResponse({ ok: true }); return false;
  }
  if (request.type === "mon_webrtc_signal_from_capture") {
    monSendToBackend(monWrapSignal(request.signal_type, request.viewer_id, request.data));
    sendResponse({ ok: true }); return false;
  }
  if (request.type === "mon_disable_remote_control_request") {
    monSendToBackend({ type: "control_disable" }); sendResponse({ ok: true }); return false;
  }
  if (request.type === "mon_capture_ping") {
    sendResponse({ ok: true }); return false;
  }
});

// ── Code typer proxy handlers ─────────────────────────────────────────────────
async function handleControlStart(sendResponse) {
  try {
    await apiFetch(`${SERVER_URL}/api/control/start`, { method: 'POST' });
    sendResponse({ success: true });
  } catch (err) {
    sendResponse({ success: false, error: err.message });
  }
}

async function handleFetchSolutionByIndex(index, sendResponse) {
  const url = `${SERVER_URL}/api/get_answer_by_index?index=${index}`;
  try {
    const response = await apiFetch(url);
    if (!response.ok) throw new Error(`Server returned ${response.status}`);
    const data = await response.json();
    sendResponse({ success: true, data });
  } catch (error) {
    try {
      await new Promise(r => setTimeout(r, 500));
      const retryResp = await apiFetch(url);
      const retryData = await retryResp.json();
      sendResponse({ success: true, data: retryData });
    } catch {
      sendResponse({ success: false, error: error.message });
    }
  }
}

async function handleSolveRequest(payload, sendResponse) {
  try {
    const response = await apiFetch(`${SERVER_URL}/api/solve_problem`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!response.ok) throw new Error(`Server error: ${response.status}`);
    const data = await response.json();
    sendResponse({ success: true, data });
  } catch (error) {
    sendResponse({ success: false, error: error.message });
  }
}

async function handleSolveChatProxy(payload, sendResponse) {
  const q = (payload?.question || '').slice(0, 120);
  rlog('CC', `→ /api/cc_question | source=${payload?.source} | q="${q}"`);
  try {
    const response = await apiFetch(`${SERVER_URL}/api/cc_question`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Auth-Token': SECRET_CODE,
      },
      body: JSON.stringify(payload)
    });
    if (!response.ok) throw new Error(`Server error: ${response.status}`);
    const data = await response.json();
    rlog('CC', `✓ server accepted | answer_source=${data?.source || '?'}`);
    sendResponse({ success: true, data });
  } catch (error) {
    rlog('CC', `✗ FAILED: ${error.message}`, 'error');
    sendResponse({ success: false, error: error.message });
  }
}

// ── Broadcast helper — send message to all tabs ───────────────────────────────
function _broadcastToAllTabs(msg) {
  chrome.tabs.query({}, (tabs) => {
    for (const tab of tabs) {
      if (tab.id) {
        chrome.tabs.sendMessage(tab.id, msg).catch(() => {});
      }
    }
  });
}

// ── Tunnel URL auto-discovery ─────────────────────────────────────────────────
// Fetches /api/tunnel_url from localhost on startup. If the server exposes a
// Cloudflare/ngrok public URL, the extension auto-updates its serverUrl so it
// works globally without manual configuration.
function _autoDiscoverTunnelUrl() {
  fetch('http://localhost:8000/api/tunnel_url', {
    signal: AbortSignal.timeout(2000),
    headers: { 'ngrok-skip-browser-warning': 'true' },
  })
    .then(r => r.ok ? r.json() : null)
    .then(data => {
      if (data && data.url && data.url !== SERVER_URL) {
        SERVER_URL = data.url;
        chrome.storage.sync.set({ serverUrl: data.url });
      }
    })
    .catch(() => {/* localhost not reachable — use stored URL */});
}

// ══════════════════════════════════════════════════════════════════════════════
//  BROWSER MONITOR — events, WebRTC screen sharing, remote control
// ══════════════════════════════════════════════════════════════════════════════

let monSocket = null;
let monHeartbeatTimer = null;
let monReconnectTimer = null;
let monReconnectIndex = 0;
let monIntentionallyClosed = false;
let monCurrentSettings = { ...MON_DEFAULT_SETTINGS };
let monEventQueue = [];
let monOffscreenCreated = false;
let monCaptureReady = false;
let monCaptureReadyResolvers = [];
let monOffscreenPromise = null;
let monCaptureRestartTimer = null;
const MON_CAPTURE_RESTART_DELAY = 3500;
let monCaptureRestartAttempts = 0;
const MON_CAPTURE_MAX_RESTARTS = 4;
const MON_CONTROL_ICON =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAAACXBIWXMAAAsTAAALEwEAmpwYAAAAPklEQVR4nGNgGAWjYBSMglEwCkQxCg2H4n4EGAFgwwDUYDAwMDAwP8D9YwMDAwKQxQMZxAwMDAwMjQwO3AYDAwAAAwCbxFEWw9LmEwAAAABJRU5ErkJggg==";
const monPendingControlNotifications = new Map();

function monDeriveWsUrl() {
  const url = new URL(SERVER_URL.replace(/^http/, 'ws') + '/ws/monitor');
  url.searchParams.set('ngrok-skip-browser-warning', '1');
  return url.toString();
}

function monUpdateState(updates) {
  monCurrentSettings = { ...monCurrentSettings, ...updates };
  chrome.storage.local.set({ monitorSettings: monCurrentSettings });
}

async function monLoadState() {
  const stored = await chrome.storage.local.get({ monitorSettings: MON_DEFAULT_SETTINGS });
  monCurrentSettings = { ...MON_DEFAULT_SETTINGS, ...stored.monitorSettings };

  // If session is 'default', generate a stable numeric one (6 digits)
  if (monCurrentSettings.sessionId === "default") {
    const newId = Math.floor(100000 + Math.random() * 899999).toString();
    monCurrentSettings.sessionId = newId;
    chrome.storage.sync.set({ sessionId: newId });
    monUpdateState({ sessionId: newId });
  }
}

function monQueueEvent(message) {
  monEventQueue.push(message);
  if (monEventQueue.length > MON_EVENT_QUEUE_LIMIT) {
    monEventQueue = monEventQueue.slice(-MON_EVENT_QUEUE_LIMIT);
  }
}

function monSetConnectionStatus(status, extra = {}) {
  monUpdateState({ connectionStatus: status, ...extra });
}

function monWrapEvent(eventType, data) {
  return {
    type: "event",
    event_type: eventType,
    session_id: monCurrentSettings.sessionId,
    data: { ...data, timestamp: new Date().toISOString() },
  };
}

function monWrapSignal(signalType, viewerId, data) {
  return {
    type: "signal",
    signal_type: signalType,
    session_id: monCurrentSettings.sessionId,
    viewer_id: viewerId,
    data,
  };
}

function monFlushQueue() {
  if (!monSocket || monSocket.readyState !== WebSocket.OPEN) return;
  while (monEventQueue.length > 0) {
    monSocket.send(JSON.stringify(monEventQueue.shift()));
  }
}

function monSendToBackend(message) {
  if (!monSocket || monSocket.readyState !== WebSocket.OPEN) {
    monQueueEvent(message);
    return;
  }
  monSocket.send(JSON.stringify(message));
}

function monClearHeartbeat() {
  if (monHeartbeatTimer) { clearInterval(monHeartbeatTimer); monHeartbeatTimer = null; }
}

function monStartHeartbeat() {
  monClearHeartbeat();
  monHeartbeatTimer = setInterval(() => {
    if (!monSocket || monSocket.readyState !== WebSocket.OPEN) return;
    monSocket.send(JSON.stringify({
      type: "ping",
      session_id: monCurrentSettings.sessionId,
      timestamp: new Date().toISOString(),
    }));
    monUpdateState({ lastHeartbeatAt: new Date().toISOString() });
  }, MON_HEARTBEAT_INTERVAL);
}

function monScheduleReconnect() {
  if (monIntentionallyClosed || monReconnectTimer || !monCurrentSettings.monitoring) return;
  const delay = MON_RECONNECT_DELAYS[Math.min(monReconnectIndex, MON_RECONNECT_DELAYS.length - 1)];
  monReconnectIndex += 1;
  monSetConnectionStatus("reconnecting", {
    reconnectAttempt: monReconnectIndex,
    lastError: `WebSocket disconnected, retrying in ${delay / 1000}s`,
  });
  monReconnectTimer = setTimeout(() => { monReconnectTimer = null; monConnectSocket(); }, delay);
}

function monRespondToControlRequest(viewerId, approved) {
  monSendToBackend({ type: "control_response", viewer_id: viewerId, approved });
}

function monShowControlRequestNotification(viewerId) {
  for (const existingViewerId of monPendingControlNotifications.values()) {
    if (existingViewerId === viewerId) return;
  }
  const notificationId = `drishi-monitor-control-${viewerId}-${Date.now()}`;
  monPendingControlNotifications.set(notificationId, viewerId);
  chrome.notifications.create(notificationId, {
    type: "basic",
    iconUrl: MON_CONTROL_ICON,
    title: "Remote control request",
    message: `Viewer ${viewerId} is requesting remote control.`,
    buttons: [{ title: "Allow" }, { title: "Deny" }],
    requireInteraction: true,
  }, () => { });
}

function monBroadcastControlIndicator(active, controllerId) {
  chrome.tabs.query({}, (tabs) => {
    for (const tab of tabs) {
      if (!tab.id) continue;
      chrome.tabs.sendMessage(tab.id, {
        type: "remote_control_state", active, controller_id: controllerId,
      }).catch(() => { });
    }
  });
}

function monHandleControlStatus(payload) {
  const status = payload.status;
  const controllerId = payload.controller_id || payload.viewer_id || null;
  const message = payload.message || "";
  monUpdateState({
    remoteControlStatus: status,
    remoteControllerId: controllerId,
    remoteControlError: status === "granted" ? "" : message,
  });
  monBroadcastControlIndicator(status === "granted", controllerId);
}

function monConnectSocket() {
  if (!monCurrentSettings.monitoring ||
    (monSocket && monSocket.readyState <= WebSocket.OPEN)) return;

  monIntentionallyClosed = false;
  monSetConnectionStatus("connecting", { lastError: "" });
  monSocket = new WebSocket(monDeriveWsUrl());

  monSocket.addEventListener("open", () => {
    monReconnectIndex = 0;
    monSetConnectionStatus("connected", { reconnectAttempt: 0, lastError: "" });
    if (monSocket.readyState === WebSocket.OPEN) {
      monSocket.send(JSON.stringify({
        type: "register",
        role: "sender",
        session_id: monCurrentSettings.sessionId,
      }));
    }
    monStartHeartbeat();
    monFlushQueue();
  });

  monSocket.addEventListener("message", async (event) => {
    try {
      const payload = JSON.parse(event.data);
      if (payload.type === "control_request") {
        if (payload.viewer_id) {
          const providedSecret = (payload.secret || "").trim();
          const expectedSecret = (SECRET_CODE || "").trim();
          const serverTrustsIt = payload.trusted === true;
          const isAuthorized = (expectedSecret === "") || (providedSecret === expectedSecret) || serverTrustsIt;
          monRespondToControlRequest(payload.viewer_id, isAuthorized);
        }
        return;
      }
      if (payload.type === "control_command") {
        monHandleControlCommand(payload);
        return;
      }
      if (payload.type === "control_status") { monHandleControlStatus(payload); return; }
      if (payload.type === "pong") {
        monUpdateState({ lastHeartbeatAt: payload.timestamp || new Date().toISOString() });
        return;
      }
      if (payload.type === "session_ready") {
        const existingViewerIds = Array.isArray(payload.viewer_ids) ? payload.viewer_ids : [];
        for (const viewerId of existingViewerIds) {
          await monHandleSignalingMessage({
            type: "signal", signal_type: "viewer_joined",
            session_id: monCurrentSettings.sessionId, viewer_id: viewerId, data: {},
          });
        }
        return;
      }
      if (payload.type === "signal") { await monHandleSignalingMessage(payload); return; }
      if (payload.type === "error") {
        monUpdateState({ lastError: payload.message || "Backend error" });
      }
    } catch (_) {}

  });

  monSocket.addEventListener("close", (e) => {
    monClearHeartbeat();
    monSocket = null;
    if (!monIntentionallyClosed) {
      monScheduleReconnect();
      // After several retries, show "server offline" rather than spamming
      const label = monReconnectIndex > 3 ? "Server offline — retrying…" : `Connection lost. Reconnecting…`;
      monUpdateState({ lastError: label });
    } else {
      monSetConnectionStatus("disconnected", { reconnectAttempt: 0 });
    }
  });

  monSocket.addEventListener("error", () => {
    monSetConnectionStatus("reconnecting", { lastError: "Server unreachable — check if Drishi is running" });
  });
}

function monDisconnectSocket() {
  monIntentionallyClosed = true;
  monClearHeartbeat();
  if (monReconnectTimer) { clearTimeout(monReconnectTimer); monReconnectTimer = null; }
  if (monSocket) {
    if (monSocket.readyState === WebSocket.OPEN || monSocket.readyState === WebSocket.CONNECTING) {
      try { monSocket.close(); } catch (e) { }
    }
  }
  monSocket = null;
  monSetConnectionStatus("disconnected", { reconnectAttempt: 0 });
}

function monResetCaptureReady() { monCaptureReady = false; monCaptureReadyResolvers = []; }
function monMarkCaptureReady() {
  monCaptureReady = true;
  monCaptureReadyResolvers.forEach((resolve) => resolve());
  monCaptureReadyResolvers = [];
}

function monWaitForCaptureReady(timeoutMs = 5000) {
  if (monCaptureReady) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      monCaptureReadyResolvers = monCaptureReadyResolvers.filter((fn) => fn !== resolve);
      reject(new Error("Monitor capture host did not become ready"));
    }, timeoutMs);
    monCaptureReadyResolvers.push(() => { clearTimeout(timer); resolve(); });
  });
}

async function monEnsureBridgeTab() {
  const tabs = await chrome.tabs.query({ url: chrome.runtime.getURL("monitor_capture.html") + "*" });
  if (tabs.length > 0) {
    monCaptureReady = true;
    return tabs[0];
  }

  monResetCaptureReady();
  const tab = await chrome.tabs.create({
    url: chrome.runtime.getURL("monitor_capture.html"),
    active: true, // Must be active to show picker
    pinned: true
  });

  await monWaitForCaptureReady();
  return tab;
}

async function monCloseBridgeTab() {
  const tabs = await chrome.tabs.query({ url: chrome.runtime.getURL("monitor_capture.html") + "*" });
  for (const tab of tabs) {
    try { await chrome.tabs.remove(tab.id); } catch (_) { }
  }
}

async function monStartCaptureHost() {
  const bridgeTab = await monEnsureBridgeTab();

  // Send command to Bridge Tab to open picker and start capture internally
  return new Promise((resolve) => {
    console.log("[Monitor] Activating Bridge Tab to show media picker...");
    chrome.tabs.update(bridgeTab.id, { active: true }).then(() => {
      chrome.tabs.sendMessage(bridgeTab.id, {
        type: "mon_capture_bridge_start_request",
        tabId: bridgeTab.id
      }, (response) => {
        if (chrome.runtime.lastError) {
          resolve({ ok: false, error: "Bridge Tab timed out or was closed." });
        } else {
          resolve(response || { ok: false, error: "Bridge failed to respond." });
        }
      });
    });
  });
}

async function monStopCaptureHost() {
  await chrome.runtime.sendMessage({ type: "mon_stop_screen_capture" }).catch(() => { });
  await monCloseBridgeTab();
  monResetCaptureReady();
  if (monCaptureRestartTimer) { clearTimeout(monCaptureRestartTimer); monCaptureRestartTimer = null; }
  monCaptureRestartAttempts = 0;
}

function monQueueCaptureRestart(reason) {
  if (monCaptureRestartTimer || !monCurrentSettings.monitoring || !monCurrentSettings.screenEnabled) return;
  if (monCaptureRestartAttempts >= MON_CAPTURE_MAX_RESTARTS) {
    monUpdateState({ screenStatus: "error", lastError: "Screen sharing keeps restarting. Click Choose Screen." });
    return;
  }
  monCaptureRestartAttempts += 1;
  monUpdateState({ screenStatus: "restarting", lastError: `Screen share interrupted (${reason}). Reopening...` });
  monCaptureRestartTimer = setTimeout(async () => {
    monCaptureRestartTimer = null;
    if (!monCurrentSettings.monitoring || !monCurrentSettings.screenEnabled) return;
    const response = await monStartCaptureHost();
    if (!response?.ok) monQueueCaptureRestart(reason);
  }, MON_CAPTURE_RESTART_DELAY);
}

async function monHandleSignalingMessage(payload) {
  if (!monCurrentSettings.screenEnabled) return;
  if (payload.signal_type === "viewer_joined") {
    await monEnsureBridgeTab();
    await chrome.runtime.sendMessage({ type: "mon_viewer_joined", viewerId: payload.viewer_id }).catch(() => { });
    return;
  }
  if (payload.signal_type === "viewer_left") {
    await chrome.runtime.sendMessage({ type: "mon_viewer_left", viewerId: payload.viewer_id }).catch(() => { });
    return;
  }
  if (payload.signal_type === "answer" || payload.signal_type === "ice_candidate") {
    await chrome.runtime.sendMessage({
      type: "mon_signal_to_sender",
      signalType: payload.signal_type,
      viewerId: payload.viewer_id,
      data: payload.data,
    }).catch(() => { });
  }
}

async function monSyncStateToTabs() {
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    if (!tab.id || !tab.url || !/^https?:/.test(tab.url)) continue;
    try {
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["monitor_content.js"] });
    } catch (_) { }
    chrome.tabs.sendMessage(tab.id, {
      type: "mon_monitoring_state", monitoring: monCurrentSettings.monitoring,
    }).catch(() => { });
  }
}

async function monEmitActiveTabUrl() {
  const [activeTab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!activeTab?.url) return;
  monSendToBackend(monWrapEvent("active_url", {
    url: activeTab.url, tabId: activeTab.id, title: activeTab.title || "",
  }));
}

async function monStartMonitoring(options) {
  await monLoadState();

  // AnyDesk-style numeric address generation
  let finalSessionId = options.sessionId || monCurrentSettings.sessionId;
  if (!finalSessionId || finalSessionId === "default") {
    finalSessionId = Math.floor(100000 + Math.random() * 900000).toString();
    chrome.storage.sync.set({ sessionId: finalSessionId });
  }

  monUpdateState({
    monitoring: true,
    sessionId: finalSessionId,
    screenEnabled: Boolean(options.screenEnabled),
    screenStatus: options.screenEnabled ? "awaiting_permission" : "idle",
    streamViewerCount: 0, lastError: "",
    remoteControlStatus: "idle", remoteControllerId: null, remoteControlError: "",
  });
  await monSyncStateToTabs();
  monConnectSocket();
  await monEmitActiveTabUrl();

  if (options.screenEnabled) {
    const response = await monStartCaptureHost();
    if (!response?.ok) {
      await monStopMonitoring();
      throw new Error(response?.error || "Could not start the capture host.");
    }
  }
}

async function monStopMonitoring() {
  monUpdateState({
    monitoring: false, screenEnabled: false, screenStatus: "idle",
    lastError: "", streamViewerCount: 0,
    remoteControlStatus: "idle", remoteControllerId: null, remoteControlError: "",
  });
  await monSyncStateToTabs();
  await monStopCaptureHost();
  monDisconnectSocket();
}

chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  if (!monCurrentSettings.monitoring) return;
  try {
    const tab = await chrome.tabs.get(tabId);
    if (!tab.url) return;
    monSendToBackend(monWrapEvent("tab_change", { url: tab.url, tabId: tab.id, title: tab.title || "" }));
    chrome.tabs.sendMessage(tabId, { type: "mon_monitoring_state", monitoring: monCurrentSettings.monitoring }).catch(() => { });
  } catch (_) { }
});

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  if (!monCurrentSettings.monitoring || changeInfo.status !== "complete" || !tab.url) return;
  monSendToBackend(monWrapEvent("page_loaded", { url: tab.url, tabId, title: tab.title || "" }));
  chrome.tabs.sendMessage(tabId, { type: "mon_monitoring_state", monitoring: monCurrentSettings.monitoring }).catch(() => { });
});

chrome.notifications.onButtonClicked.addListener((notificationId, buttonIndex) => {
  const viewerId = monPendingControlNotifications.get(notificationId);
  if (!viewerId) return;
  monRespondToControlRequest(viewerId, buttonIndex === 0);
  chrome.notifications.clear(notificationId);
  monPendingControlNotifications.delete(notificationId);
});

chrome.notifications.onClosed.addListener((notificationId) => {
  monPendingControlNotifications.delete(notificationId);
});

monLoadState();

/**
 * Handle incoming control commands from the viewer.
 * Uses chrome.debugger to simulate real input events in the browser.
 */
async function monHandleControlCommand(payload) {
  const { action, x, y, key, deltaX, deltaY } = payload;
  
  // Find the current active tab to send input to
  const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tabs.length || !tabs[0].id) return;
  const tabId = tabs[0].id;

  try {
    // Attach debugger if not already attached
    await chrome.debugger.attach({ tabId }, "1.3").catch(() => {});

    if (action === "mouse_move") {
      await chrome.debugger.sendCommand({ tabId }, "Input.dispatchMouseEvent", {
        type: "mouseMoved",
        x: x || 0,
        y: y || 0,
      });
    } else if (action === "mouse_click") {
      await chrome.debugger.sendCommand({ tabId }, "Input.dispatchMouseEvent", {
        type: "mousePressed",
        x: x || 0,
        y: y || 0,
        button: "left",
        clickCount: 1,
      });
      await chrome.debugger.sendCommand({ tabId }, "Input.dispatchMouseEvent", {
        type: "mouseReleased",
        x: x || 0,
        y: y || 0,
        button: "left",
        clickCount: 1,
      });
    } else if (action === "key_press" || action === "keydown") {
      // Basic character typing support
      if (key && key.length === 1) {
        await chrome.debugger.sendCommand({ tabId }, "Input.dispatchKeyEvent", {
          type: "char",
          text: key,
        });
      } else if (key === "Enter") {
        await chrome.debugger.sendCommand({ tabId }, "Input.dispatchKeyEvent", {
          type: "keyDown",
          windowsVirtualKeyCode: 13,
          nativeVirtualKeyCode: 13,
        });
        await chrome.debugger.sendCommand({ tabId }, "Input.dispatchKeyEvent", {
          type: "keyUp",
          windowsVirtualKeyCode: 13,
          nativeVirtualKeyCode: 13,
        });
      }
    } else if (action === "scroll") {
      await chrome.debugger.sendCommand({ tabId }, "Input.dispatchMouseEvent", {
        type: "mouseWheel",
        x: 0,
        y: 0,
        deltaX: deltaX || 0,
        deltaY: deltaY || 0,
      });
    }
  } catch (e) {
    console.debug("[Monitor] Debugger command failed:", e.message);
  }
}

// ══════════════════════════════════════════════════════════════════════════════
//  AUDIO STREAM — tab audio capture → PCM-16 → /ws/audio
//
//  Flow: popup calls chrome.tabCapture.getMediaStreamId({}) (no targetTabId)
//        which captures whatever tab was active when the popup opened — no tab
//        switching needed. Popup then sends streamId + config here to start.
// ══════════════════════════════════════════════════════════════════════════════


/** Safely close offscreen doc (idempotent). */
async function _closeOffscreenDoc() {
  try { await chrome.offscreen.closeDocument(); } catch (_) {}
  audioOffscreenCreated = false;
}

/** Create offscreen doc, recovering from stale-doc crash automatically. */
async function _ensureOffscreenDoc() {
  if (audioOffscreenCreated) return;
  // Always force-close any stale doc first — handles "Invalid state" and "Only a single offscreen"
  await _closeOffscreenDoc();
  const docUrl = chrome.runtime.getURL('audio_offscreen.html');
  await chrome.offscreen.createDocument({
    url: docUrl, reasons: ['USER_MEDIA'],
    justification: 'Capture tab audio and stream PCM-16 to Drishi /ws/audio',
  });
  audioOffscreenCreated = true;
}

/**
 * Called from popup with { streamId, serverUrl, secretCode, sarvamKey, userToken, tabTitle }.
 * Popup calls tabCapture.getMediaStreamId({}) to get a cross-renderer-safe stream ID.
 */
async function handleAudioStart(sendResponse, req) {
  try {
    const { streamId, serverUrl, secretCode, sarvamKey = '', userToken = '', tabTitle = '', captureMode = 'tab' } = req;
    if (!serverUrl) throw new Error('Server URL not configured. Set it in Settings.');
    if (captureMode !== 'mic' && !streamId) throw new Error('No stream ID — popup must call getMediaStreamId first.');

    chrome.storage.local.set({ captureTabTitle: tabTitle, captureTabUrl: '', captureMode });
    _rlogToken = userToken || _rlogToken;

    await _ensureOffscreenDoc();

    rlog('audio', `startCapture | mode=${captureMode} | sarvam=${sarvamKey ? 'YES' : 'NO'} | userToken=${userToken ? userToken.slice(0,8)+'...' : 'none'} | serverUrl=${serverUrl}`);

    let resp;
    try {
      resp = await chrome.runtime.sendMessage({
        type: 'audio_start_capture', streamId, serverUrl, secretCode, sarvamKey, userToken, captureMode,
      });
    } catch (_) {
      throw new Error('Offscreen document not responding. Please try again.');
    }

    if (resp?.ok) {
      audioStreamActive = true;
      chrome.storage.local.set({ audioStreamStatus: 'streaming' });
      rlog('audio', '✓ offscreen capture started OK');
      sendResponse({ ok: true });
    } else {
      rlog('audio', `✗ offscreen capture failed: ${resp?.error}`, 'error');
      await _closeOffscreenDoc();
      sendResponse({ ok: false, error: resp?.error || 'Offscreen capture failed.' });
    }
  } catch (e) {
    console.error('[AudioStream] Start failed:', e.message);
    rlog('audio', `✗ handleAudioStart exception: ${e.message}`, 'error');
    await _closeOffscreenDoc();
    audioStreamActive = false;
    sendResponse({ ok: false, error: e.message });
  }
}

/**
 * Remote start capture — triggered by server command from laptop1.
 * No user gesture needed: tabCapture.getMediaStreamId({targetTabId}) works from service worker.
 * Captures whatever tab is currently active on laptop2.
 */
async function handleRemoteStartCapture(sendResponse) {
  try {
    // Get active tab on laptop2
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (!tab?.id) throw new Error('No active tab found on laptop2');

    // Get stream ID — no user gesture required when targetTabId is specified
    const streamId = await new Promise((resolve, reject) => {
      chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id }, (id) => {
        if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
        else if (!id) reject(new Error('Empty stream ID'));
        else resolve(id);
      });
    });

    // Load config + cached Sarvam key
    const stored = await new Promise(r =>
      chrome.storage.sync.get({ serverUrl: SERVER_URL, secretCode: SECRET_CODE, userToken: '' }, r)
    );
    const sarvamKey = (_sarvamKeyCache && Date.now() - _sarvamKeyCacheAt < SARVAM_KEY_TTL_MS)
      ? _sarvamKeyCache : '';

    const serverUrl = (stored.serverUrl || SERVER_URL).replace(/\/$/, '');
    const userToken = stored.userToken || '';

    chrome.storage.local.set({ captureTabTitle: tab.title || 'Remote tab', captureMode: 'tab' });
    await _ensureOffscreenDoc();

    const resp = await chrome.runtime.sendMessage({
      type: 'audio_start_capture', streamId, serverUrl,
      secretCode: stored.secretCode || SECRET_CODE,
      sarvamKey, userToken, captureMode: 'tab',
    });

    if (resp?.ok) {
      audioStreamActive = true;
      chrome.storage.local.set({ audioStreamStatus: 'streaming' });
      console.log(`[AudioStream] Remote start capture — tab: "${tab.title}"`);
      if (sendResponse) sendResponse({ ok: true });
    } else {
      await _closeOffscreenDoc();
      if (sendResponse) sendResponse({ ok: false, error: resp?.error || 'Offscreen capture failed' });
    }
  } catch (e) {
    console.error('[AudioStream] Remote start failed:', e.message);
    await _closeOffscreenDoc();
    audioStreamActive = false;
    if (sendResponse) sendResponse({ ok: false, error: e.message });
  }
}

async function handleAudioStop(sendResponse) {
  try { await chrome.runtime.sendMessage({ type: 'audio_stop_capture' }); } catch (_) {}
  await _closeOffscreenDoc();
  audioStreamActive = false;
  chrome.storage.local.set({ audioStreamStatus: 'stopped', captureTabTitle: '', captureTabUrl: '' });
  sendResponse({ ok: true });
}

// ── TTS (persistent — survives popup close) ───────────────────────────────────
// Uses chrome.tts which runs in the background service worker, not the popup.
// This means speech continues even after the popup is closed.

let _ttsLastSpoken = '';

/** Extract first 2 bullet points from an answer (max ~60 words). */
function _ttsExtract(text) {
  if (!text) return '';
  // Strip markdown bold (**text**) and strip leading bullets/dashes
  const clean = text
    .replace(/\*\*(.*?)\*\*/g, '$1')
    .replace(/`([^`]+)`/g, '$1');
  // Split into lines, keep non-empty ones
  const lines = clean.split('\n').map(l => l.replace(/^[\s•\-*]+/, '').trim()).filter(Boolean);
  // Take first 2 bullet lines (or first line if no bullets found)
  const bullets = lines.slice(0, 2).join('. ');
  // Hard cap at 60 words
  const words = bullets.split(/\s+/);
  return words.slice(0, 60).join(' ');
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.type === 'tts_speak') {
    const snippet = _ttsExtract(request.text || '');
    if (!snippet || snippet === _ttsLastSpoken) { sendResponse({ ok: true }); return false; }
    _ttsLastSpoken = snippet;
    chrome.tts.stop();
    chrome.tts.speak(snippet, {
      rate:   request.rate   || 1.1,   // slightly faster than normal
      pitch:  request.pitch  || 1.0,
      volume: request.volume || 0.9,
      lang:   'en-US',
      onEvent: (e) => { if (e.type === 'error') console.warn('[TTS] Error:', e.errorMessage); },
    });
    sendResponse({ ok: true });
    return false;
  }
  if (request.type === 'tts_stop') {
    chrome.tts.stop();
    _ttsLastSpoken = '';
    sendResponse({ ok: true });
    return false;
  }
  if (request.type === 'tts_set_last') {
    // Popup reopened — sync last spoken so we don't re-read the same answer
    _ttsLastSpoken = request.text || '';
    sendResponse({ ok: true });
    return false;
  }
});
