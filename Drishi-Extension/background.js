// Drishi Pro — Background Service Worker (MV3)
// Handles: voice assistant shortcuts, tab audio capture, coding proxy, server comms

// ── Server URL — loaded dynamically from storage ──────────────────────────────
let SERVER_URL   = 'http://localhost:8000';
let SECRET_CODE  = '';
let captureTabId = null;
let offscreenCreated = false;

// Load settings on startup
chrome.storage.sync.get({ serverUrl: 'http://localhost:8000', secretCode: '' }, (data) => {
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
    console.log('[Drishi] Installed v4.0');
    chrome.tabs.create({ url: `${SERVER_URL}/` });
  } else if (details.reason === 'update') {
    console.log('[Drishi] Updated to', chrome.runtime.getManifest().version);
  }
});

// ── Keyboard shortcuts ─────────────────────────────────────────────────────────
chrome.commands.onCommand.addListener((command) => {
  if (command === 'open-voice-mode') {
    chrome.tabs.create({ url: `${SERVER_URL}/`, active: true });
  } else if (command === 'open-voice-popup') {
    openSidePanel();
  }
});

function openSidePanel() {
  chrome.system.display.getInfo((displays) => {
    const primary = displays.find(d => d.isPrimary) || displays[0];
    const w = primary ? primary.workArea.width : 1920;
    const h = primary ? primary.workArea.height : 1080;
    chrome.windows.create({
      url: `${SERVER_URL}/`,
      type: 'popup',
      width: 520,
      height: Math.min(900, h - 20),
      left: w - 540,
      top: 10
    });
  });
}

// ── Message listener ──────────────────────────────────────────────────────────
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  // ── Standard navigation messages ────────────────────────────────────────
  if (request.action === 'openVoice') { openSidePanel(); sendResponse({ success: true }); return; }
  if (request.action === 'openMain')  { chrome.tabs.create({ url: `${SERVER_URL}/` }); sendResponse({ success: true }); return; }
  if (request.action === 'checkServer') {
    fetch(`${SERVER_URL}/health`, { signal: AbortSignal.timeout(3000) })
      .then(r => sendResponse({ running: r.ok }))
      .catch(() => sendResponse({ running: false }));
    return true;
  }

  // ── Tab audio capture ────────────────────────────────────────────────────
  if (request.action === 'startCapture') {
    startTabCapture(request.tabId).then(r => sendResponse(r)).catch(e => sendResponse({ ok: false, error: e.message }));
    return true;
  }
  if (request.action === 'stopCapture') {
    stopTabCapture().then(() => sendResponse({ ok: true })).catch(() => sendResponse({ ok: true }));
    return true;
  }
  if (request.action === 'getCaptureState') {
    sendResponse({ capturing: captureTabId !== null });
    return;
  }

  // ── Messages relayed from offscreen ─────────────────────────────────────
  if (request.type === 'SERVER_MESSAGE') {
    // Forward answer to all Meet/Teams content scripts
    broadcastToMeetTabs(request.data);
    // Also post to popup if open
    chrome.runtime.sendMessage({ type: 'SERVER_MESSAGE', data: request.data }).catch(() => {});
    return;
  }
  if (request.type === 'CAPTURE_STATUS') {
    chrome.runtime.sendMessage(request).catch(() => {});
    return;
  }

  // ── Code typer proxy messages ────────────────────────────────────────────
  if (request.type === 'SOLVE_PROBLEM_PROXY')      { handleSolveRequest(request.payload, sendResponse); return true; }
  if (request.type === 'FETCH_SOLUTION_BY_INDEX')  { handleFetchSolutionByIndex(request.index, sendResponse); return true; }
  if (request.type === 'CONTROL_START')            { handleControlStart(sendResponse); return true; }
  if (request.type === 'SOLVE_CHAT_PROXY')         { handleSolveChatProxy(request.payload, sendResponse); return true; }
});

// ── Tab audio capture ─────────────────────────────────────────────────────────
async function startTabCapture(tabId) {
  // Get a stream ID for the target tab
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
  captureTabId = tabId;

  // Ensure offscreen document exists
  await ensureOffscreen();

  // Tell offscreen.js to start capturing
  const resp = await chrome.runtime.sendMessage({
    type:       'START_CAPTURE',
    streamId,
    serverUrl:  SERVER_URL,
    secretCode: SECRET_CODE,
  });
  return resp || { ok: true };
}

async function stopTabCapture() {
  captureTabId = null;
  try {
    await chrome.runtime.sendMessage({ type: 'STOP_CAPTURE' });
  } catch (_) {}
}

async function ensureOffscreen() {
  if (offscreenCreated) return;
  const existing = await chrome.offscreen.hasDocument().catch(() => false);
  if (!existing) {
    await chrome.offscreen.createDocument({
      url:    chrome.runtime.getURL('offscreen.html'),
      reasons: ['USER_MEDIA'],
      justification: 'Capture tab audio for interview assistant',
    });
  }
  offscreenCreated = true;
}

// ── Broadcast server answer to all open Meet/Teams/Zoom tabs ──────────────────
function broadcastToMeetTabs(data) {
  chrome.tabs.query({}, (tabs) => {
    for (const tab of tabs) {
      const url = tab.url || '';
      if (url.includes('meet.google.com') || url.includes('teams.microsoft.com') ||
          url.includes('teams.live.com')  || url.includes('zoom.us')) {
        chrome.tabs.sendMessage(tab.id, { action: 'drishi_answer', data }).catch(() => {});
      }
    }
  });
}

// ── Code typer proxy handlers ─────────────────────────────────────────────────
async function handleControlStart(sendResponse) {
  try {
    await fetch(`${SERVER_URL}/api/control/start`, { method: 'POST' });
    sendResponse({ success: true });
  } catch (err) {
    sendResponse({ success: false, error: err.message });
  }
}

async function handleFetchSolutionByIndex(index, sendResponse) {
  const url = `${SERVER_URL}/api/get_answer_by_index?index=${index}`;
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`Server returned ${response.status}`);
    const data = await response.json();
    sendResponse({ success: true, data });
  } catch (error) {
    try {
      await new Promise(r => setTimeout(r, 500));
      const retryResp = await fetch(url);
      const retryData = await retryResp.json();
      sendResponse({ success: true, data: retryData });
    } catch {
      sendResponse({ success: false, error: error.message });
    }
  }
}

async function handleSolveRequest(payload, sendResponse) {
  try {
    const response = await fetch(`${SERVER_URL}/api/solve_problem`, {
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
    const response = await fetch(`${SERVER_URL}/api/cc_question`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Auth-Token':  SECRET_CODE,
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
