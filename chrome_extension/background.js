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


// Load settings on startup
chrome.storage.sync.get({ serverUrl: 'https://particulate-arely-unrenovative.ngrok-free.dev', secretCode: '' }, (data) => {
  SERVER_URL  = data.serverUrl;
  SECRET_CODE = data.secretCode;
});

chrome.storage.onChanged.addListener((changes) => {
  if (changes.serverUrl)  SERVER_URL  = changes.serverUrl.newValue;
  if (changes.secretCode) SECRET_CODE = changes.secretCode.newValue;
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
  if (request.action === 'audio_start') { handleAudioStart(sendResponse, request.userToken || ''); return true; }
  if (request.action === 'audio_stop')  { handleAudioStop(sendResponse);  return true; }
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
    sendResponse({ success: true, data });
  } catch (error) {
    sendResponse({ success: false, error: error.message });
  }
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
// ══════════════════════════════════════════════════════════════════════════════

// ── Meeting tab URL patterns — used to auto-detect the interview tab ──────────
const MEETING_URL_PATTERNS = [
  '*://meet.google.com/*',
  '*://*.teams.microsoft.com/*',
  '*://teams.live.com/*',
  '*://teams.microsoft.com/*',
  '*://zoom.us/wc/*',
  '*://zoom.us/j/*',
  '*://app.zoom.us/wc/*',
  '*://*.zoom.us/wc/*',
  '*://webex.com/*',
  '*://*.webex.com/meet/*',
];

/**
 * Find the best tab to capture audio from.
 * Priority: (1) known meeting URL, (2) last-active tab across all windows.
 * Returns { tab, source: 'meeting'|'active' }
 */
async function findMeetingTab() {
  // 1. Search across all windows for a recognised meeting URL
  for (const pattern of MEETING_URL_PATTERNS) {
    const tabs = await chrome.tabs.query({ url: pattern });
    if (tabs.length > 0) {
      // Pick the most recently accessed tab
      tabs.sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0));
      return { tab: tabs[0], source: 'meeting' };
    }
  }
  // 2. Fall back to whatever tab is currently active in the focused window
  const [activeTab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (activeTab?.id) return { tab: activeTab, source: 'active' };
  return { tab: null, source: null };
}

async function handleAudioStart(sendResponse, userToken = '') {
  try {
    // Auto-detect meeting tab (Google Meet, Teams, Zoom, Webex)
    const { tab: targetTab, source } = await findMeetingTab();
    if (!targetTab?.id) {
      throw new Error('No meeting tab found. Open Google Meet / Teams / Zoom in Chrome first.');
    }
    console.log(`[AudioStream] Target tab: [${source}] ${targetTab.url?.slice(0, 80)}`);
    // Tell popup which tab we're capturing
    chrome.storage.local.set({
      captureTabTitle: targetTab.title || targetTab.url || 'Unknown tab',
      captureTabUrl:   targetTab.url  || '',
    });

    // Load current server settings
    const stored     = await chrome.storage.sync.get({ serverUrl: SERVER_URL, secretCode: SECRET_CODE });
    const serverUrl  = stored.serverUrl  || SERVER_URL;
    const secretCode = stored.secretCode || SECRET_CODE;

    // Parallelize: create offscreen doc AND fetch stt_config simultaneously
    const offscreenPromise = audioOffscreenCreated
      ? Promise.resolve()
      : chrome.offscreen.createDocument({
          url: chrome.runtime.getURL('audio_offscreen.html'),
          reasons: ['USER_MEDIA'],
          justification: 'Capture tab audio and stream PCM-16 to Drishi /ws/audio endpoint',
        }).then(() => { audioOffscreenCreated = true; });

    // Use cached Sarvam key if fresh (< 60s), otherwise fetch
    const sarvamKeyPromise = (async () => {
      const now = Date.now();
      if (_sarvamKeyCache !== null && now - _sarvamKeyCacheAt < SARVAM_KEY_TTL_MS) {
        return _sarvamKeyCache;
      }
      try {
        const tokenParam = secretCode
          ? `?token=${encodeURIComponent(secretCode)}&ngrok-skip-browser-warning=1`
          : '?ngrok-skip-browser-warning=1';
        const cfgResp = await fetch(`${serverUrl}/api/stt_config${tokenParam}`,
          { signal: AbortSignal.timeout(3000) });  // was 5000
        if (cfgResp.ok) {
          const cfg = await cfgResp.json();
          _sarvamKeyCache   = cfg.sarvam_key || '';
          _sarvamKeyCacheAt = Date.now();
          return _sarvamKeyCache;
        }
      } catch (e) {
        console.warn('[AudioStream] Could not fetch stt_config:', e.message);
      }
      return _sarvamKeyCache || '';
    })();

    const [, sarvamKey] = await Promise.all([offscreenPromise, sarvamKeyPromise]);
    console.log(`[AudioStream] Starting — Sarvam STT: ${sarvamKey ? 'ON (client-side)' : 'OFF (raw PCM)'}`);

    // Get streamId as late as possible (token expires ~2s) — right before sending to offscreen doc
    const streamId = await new Promise((resolve, reject) => {
      chrome.tabCapture.getMediaStreamId({ targetTabId: targetTab.id }, (id) => {
        if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
        else resolve(id);
      });
    });

    // Kick off capture in offscreen doc
    const resp = await chrome.runtime.sendMessage({
      type: 'audio_start_capture',
      streamId,
      serverUrl,
      secretCode,
      sarvamKey,
      userToken,   // per-user token — included in WS URL so server routes to right session
    });

    if (resp?.ok) {
      audioStreamActive = true;
      chrome.storage.local.set({ audioStreamStatus: 'streaming' });
      sendResponse({ ok: true });
    } else {
      sendResponse({ ok: false, error: resp?.error || 'Capture failed to start' });
    }
  } catch (e) {
    console.error('[AudioStream] Start failed:', e.message);
    sendResponse({ ok: false, error: e.message });
  }
}

async function handleAudioStop(sendResponse) {
  try {
    if (audioOffscreenCreated) {
      await chrome.runtime.sendMessage({ type: 'audio_stop_capture' }).catch(() => {});
      await chrome.offscreen.closeDocument().catch(() => {});
      audioOffscreenCreated = false;
    }
    audioStreamActive = false;
    chrome.storage.local.set({ audioStreamStatus: 'stopped', captureTabTitle: '', captureTabUrl: '' });
    sendResponse({ ok: true });
  } catch (e) {
    sendResponse({ ok: false, error: e.message });
  }
}
