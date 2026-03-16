// BASE URL loaded from storage — supports both local and Render.com
let BASE = 'http://localhost:8000';
let SECRET_CODE = '';
let isOnline = false;
let lastHash = '';
let pollTimer = null;
let serverOnline = false;
let typingState = 'idle';
let capturing = false;

// ── Tab switching ──────────────────────────────────────────────────────────────
function switchTab(tab) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.getElementById('tab' + tab.charAt(0).toUpperCase() + tab.slice(1)).classList.add('active');
  document.getElementById('panel' + tab.charAt(0).toUpperCase() + tab.slice(1)).classList.add('active');

  if (tab === 'coder') {
    fetchLatestAnswer();   // Always refresh slot list when switching to Coder tab
    loadTypingState();
  }
}

// ── Bootstrap — load settings then start polling ──────────────────────────────
chrome.storage.sync.get({ serverUrl: 'http://localhost:8000', secretCode: '' }, (data) => {
  BASE        = (data.serverUrl || 'http://localhost:8000').replace(/\/$/, '');
  SECRET_CODE = data.secretCode || '';
  poll();
  pollTimer = setInterval(poll, 1500);
  checkScanChatAvailable();
  loadSettingsForm();
});

// Listen for capture status / answers from background
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'CAPTURE_STATUS') {
    updateCaptureStatus(msg.status);
  }
  if (msg.type === 'SERVER_MESSAGE' && msg.data) {
    const d = msg.data;
    if (d.type === 'answer') showToast('Answer received!', 'ok');
    if (d.type === 'transcript') {
      document.getElementById('askStatus').textContent = `Heard: "${d.text}"`;
      document.getElementById('askStatus').className = 'ask-status gen';
    }
    if (d.type === 'connected' && d.mobile_url) {
      showMobileUrl(d.mobile_url);
    }
  }
});

// ══════════════════════════════════════════════════════════════════════════════
// INTERVIEW TAB
// ══════════════════════════════════════════════════════════════════════════════

// ── Single poll: check server + refresh answers ───────────────────────────────
async function poll() {
  try {
    const r = await fetch(`${BASE}/api/answers`, { signal: AbortSignal.timeout(2500) });
    if (!r.ok) { setOnline(false); return; }
    const answers = await r.json();
    setOnline(true);
    const hash = JSON.stringify(answers.slice(0, 5).map(a => a.answer?.slice(-40) + a.is_complete));
    if (hash !== lastHash) {
      lastHash = hash;
      renderFeed(answers);
    }
  } catch (_) {
    setOnline(false);
  }
}

function setOnline(online) {
  if (isOnline === online) return;
  isOnline = online;
  serverOnline = online;
  const dot = document.getElementById('dot');
  const txt = document.getElementById('statusTxt');
  dot.classList.toggle('offline', !online);
  txt.textContent = online ? 'Server Online' : 'Server Offline';
  document.getElementById('askBtn').disabled = !online;
  checkScanChatAvailable();
  // Sync Code Typer server status indicator if visible
  setCoderServerStatus(online);
}

// ── Scan Chat button ──────────────────────────────────────────────────────────
async function checkScanChatAvailable() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const url = tab?.url || '';
    const isSupported = url.includes('meet.google.com') || url.includes('teams.microsoft.com') || url.includes('teams.live.com');
    document.getElementById('scanChatBtn').disabled = !(isOnline && isSupported);
    const countEl = document.getElementById('chatCount');
    if (isSupported) {
      try {
        const r = await fetch(`${BASE}/api/chat_questions`, { signal: AbortSignal.timeout(1500) });
        const d = await r.json();
        countEl.textContent = d.count > 0
          ? `${d.count} chat question${d.count !== 1 ? 's' : ''} captured this session`
          : 'No chat questions captured yet';
      } catch { countEl.textContent = ''; }
    } else {
      countEl.textContent = isOnline ? 'Open Google Meet or Teams to scan chat' : '';
    }
  } catch { }
}

// ── Render Q&A feed ───────────────────────────────────────────────────────────
function renderFeed(answers) {
  const feed = document.getElementById('qaFeed');
  const empty = document.getElementById('emptyMsg');

  if (!answers || answers.length === 0) {
    empty.style.display = '';
    feed.querySelectorAll('.qa-card').forEach(el => el.remove());
    return;
  }
  empty.style.display = 'none';

  const shown = answers.slice(0, 5);
  feed.querySelectorAll('.qa-card').forEach(el => el.remove());

  shown.forEach((item, idx) => {
    const card = document.createElement('div');
    card.className = 'qa-card' + (idx === 0 ? ' latest' : '');

    const src = (item.metrics && item.metrics.source) || '';
    const isDb  = src.startsWith('db-');
    const isGen = !item.is_complete;
    let badgeCls = isDb ? 'db' : (isGen ? 'gen' : 'api');
    let badgeTxt = isDb ? 'DB' : (isGen ? 'GEN' : 'API');
    if (!item.is_complete) { card.classList.add('streaming'); card.classList.remove('latest'); }

    const qText  = (item.question || '').trim();
    const ansText = (item.answer || '').trim();

    let ansHtml = '';
    if (!item.is_complete) {
      ansHtml = ansText
        ? `<div class="ans-text">${esc(ansText)}</div>`
        : `<div class="thinking">Generating answer</div>`;
    } else {
      ansHtml = formatAnswer(ansText);
    }

    card.innerHTML = `
      <div class="card-top">
        <span class="src-badge ${badgeCls}">${badgeTxt}</span>
        <span class="q-text">${esc(qText)}</span>
        <button class="copy-btn" data-ans="${esc(ansText)}" title="Copy answer">Copy</button>
      </div>
      <div class="ans-wrap">${ansHtml}</div>`;

    feed.appendChild(card);
  });

  feed.scrollTop = 0;

  feed.querySelectorAll('.copy-btn').forEach(btn => {
    btn.addEventListener('click', () => copyText(btn.dataset.ans, btn));
  });
}

function formatAnswer(text) {
  if (!text) return '';

  const codeMatch = text.match(/```[\w]*\n?([\s\S]*?)```/);
  if (codeMatch) {
    const pre  = text.slice(0, codeMatch.index).trim();
    const code = codeMatch[1].trim();
    const post = text.slice(codeMatch.index + codeMatch[0].length).trim();
    let out = '';
    if (pre)  out += formatAnswer(pre);
    out += `<pre class="ans-code">${esc(code)}</pre>`;
    if (post) out += formatAnswer(post);
    return out;
  }

  const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
  const bulletLines = lines.filter(l => l.startsWith('- ') || l.startsWith('• ') || l.startsWith('* '));
  if (bulletLines.length >= 2 && bulletLines.length >= lines.length * 0.6) {
    const items = lines.map(l => {
      const clean = l.replace(/^[-•*]\s+/, '');
      return `<li>${esc(clean)}</li>`;
    });
    return `<ul class="ans-bullets">${items.join('')}</ul>`;
  }

  return `<div class="ans-text">${esc(text)}</div>`;
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

async function copyText(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = orig; btn.classList.remove('copied'); }, 1500);
  } catch (_) {}
}

// ── Quick Ask ─────────────────────────────────────────────────────────────────
async function sendQuestion() {
  const input  = document.getElementById('askInput');
  const btn    = document.getElementById('askBtn');
  const status = document.getElementById('askStatus');
  const q = input.value.trim();
  if (!q || !isOnline) return;

  btn.disabled = true;
  btn.textContent = '...';
  status.textContent = 'Sending...';
  status.className = 'ask-status gen';

  try {
    const r = await fetch(`${BASE}/api/cc_question`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: q, source: 'chat' }),
      signal: AbortSignal.timeout(20000)
    });
    const d = await r.json();

    if (d.status === 'answered' || d.status === 'processing') {
      const src = d.source || '';
      const label = src.startsWith('db-') ? 'DB hit' : 'API';
      status.textContent = `✓ Sent (${label})`;
      status.className = 'ask-status ok';
      input.value = '';
      await poll();
    } else if (d.status === 'rejected') {
      status.textContent = `Rejected: ${d.reason || 'not an interview question'}`;
      status.className = 'ask-status err';
    } else if (d.status === 'duplicate') {
      status.textContent = 'Already answered above';
      status.className = 'ask-status ok';
    } else {
      status.textContent = d.error || d.message || 'Queued for processing';
      status.className = 'ask-status gen';
    }
  } catch (e) {
    if (e.name === 'TimeoutError') {
      status.textContent = 'Sent — answer generating...';
      status.className = 'ask-status gen';
      input.value = '';
    } else {
      status.textContent = 'Error: ' + e.message;
      status.className = 'ask-status err';
    }
  } finally {
    btn.disabled = !isOnline;
    btn.textContent = 'Ask';
    setTimeout(() => { status.textContent = ''; status.className = 'ask-status'; }, 5000);
  }
}

document.getElementById('askBtn').addEventListener('click', sendQuestion);
document.getElementById('askInput').addEventListener('keydown', e => {
  if (e.key === 'Enter') sendQuestion();
});

// ── Footer buttons ────────────────────────────────────────────────────────────
document.getElementById('btnOpen').addEventListener('click', () => {
  chrome.tabs.create({ url: `${BASE}/` });
  window.close();
});
document.getElementById('btnExport').addEventListener('click', () => {
  chrome.tabs.create({ url: `${BASE}/api/session_export` });
});
document.getElementById('btnQB').addEventListener('click', () => {
  chrome.tabs.create({ url: `${BASE}/questions` });
  window.close();
});

// ── QR Code for mobile ────────────────────────────────────────────────────────
document.getElementById('btnQR').addEventListener('click', async () => {
  const section = document.getElementById('qrSection');
  if (section.classList.contains('visible')) {
    section.classList.remove('visible');
    return;
  }
  try {
    const r = await fetch(`${BASE}/api/local_url`, { signal: AbortSignal.timeout(2000) });
    const d = await r.json();
    const url = d.url;
    document.getElementById('qrUrl').textContent = url;
    // Use QR server API to generate code image
    const qrApiUrl = `https://api.qrserver.com/v1/create-qr-code/?size=140x140&data=${encodeURIComponent(url)}&format=png&margin=4`;
    document.getElementById('qrImg').src = qrApiUrl;
    section.classList.add('visible');
  } catch (e) {
    showToast('Server offline', 'err');
  }
});

document.getElementById('qrClose').addEventListener('click', () => {
  document.getElementById('qrSection').classList.remove('visible');
});

// ── Scan Chat Now ─────────────────────────────────────────────────────────────
document.getElementById('scanChatBtn').addEventListener('click', async () => {
  const btn    = document.getElementById('scanChatBtn');
  const status = document.getElementById('scanStatus');
  btn.disabled = true;
  btn.textContent = 'Scanning...';
  status.textContent = '';

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) throw new Error('No active tab');

    const response = await chrome.tabs.sendMessage(tab.id, { action: 'scan_chat' });
    const count = response?.count ?? 0;

    status.textContent = `Found ${count} msg${count !== 1 ? 's' : ''}`;
    status.style.color = count > 0 ? '#4ade80' : '#64748b';

    await checkScanChatAvailable();
    await poll();
  } catch (e) {
    status.textContent = e.message?.includes('Could not establish connection')
      ? 'No chat panel open'
      : 'Error: ' + (e.message || 'unknown');
    status.style.color = '#f87171';
  } finally {
    btn.disabled = false;
    btn.textContent = '📥 Scan Chat Now';
    setTimeout(() => { status.textContent = ''; status.style.color = ''; }, 4000);
  }
});

// ══════════════════════════════════════════════════════════════════════════════
// CODE TYPER TAB
// ══════════════════════════════════════════════════════════════════════════════

function setCoderServerStatus(online) {
  // No separate server indicator in Code Typer tab — shared header dot handles it
}

async function checkServer() {
  try {
    const r = await fetch(`${BASE}/api/answers`, { signal: AbortSignal.timeout(2000) });
    if (r.ok) {
      serverOnline = true;
      const answers = await r.json();
      showLatestAnswer(answers);
      return;
    }
  } catch (_) {}
  serverOnline = false;
  document.getElementById('apContent').innerHTML =
    '<div class="ap-empty">🔴 Server offline — start the Drishi Pro</div>';
}

async function fetchLatestAnswer() {
  const btn = document.getElementById('apFetch');
  btn.textContent = '↻ Loading...';
  try {
    const r = await fetch(`${BASE}/api/answers`, { signal: AbortSignal.timeout(3000) });
    if (r.ok) {
      const answers = await r.json();
      showLatestAnswer(answers);
      btn.textContent = '↻ Refreshed!';
      setTimeout(() => { btn.textContent = '↻ Refresh from server'; }, 1500);
      return;
    }
  } catch (_) {}
  btn.textContent = '↻ Refresh from server';
  document.getElementById('apContent').innerHTML =
    '<div class="ap-empty">🔴 Server offline — start the Drishi Pro</div>';
  showToast('Server offline', 'err');
}

function showLatestAnswer(answers) {
  const el = document.getElementById('apContent');
  if (!answers || answers.length === 0) {
    el.innerHTML = '<div class="ap-empty">No questions yet — ask something in Meet Chat</div>';
    return;
  }

  // Show all completed answers as numbered slots
  const completed = [...answers].reverse().filter(a => a.is_complete && a.answer);
  if (completed.length === 0) {
    el.innerHTML = '<div class="ap-empty">No completed answers yet</div>';
    return;
  }

  const items = completed.map((a, i) => {
    const slotNum = i + 1;
    const q = (a.question || '').slice(0, 46) + (a.question && a.question.length > 46 ? '…' : '');
    const hasCode = a.answer.includes('def ') || a.answer.includes('class ') || a.answer.includes('```');
    const icon = hasCode ? '⌨' : '💬';
    return `<div class="slot-row" title="${escHtml(a.question || '')}">
      <span class="slot-num">#${slotNum}</span>
      <span class="slot-icon">${icon}</span>
      <span class="slot-q">${escHtml(q)}</span>
    </div>`;
  }).join('');

  el.innerHTML = `<div class="slots-hint">Type <kbd>#1</kbd>…<kbd>#${completed.length}</kbd> in Programiz to auto-type:</div>${items}`;
}

function initWpmSlider() {
  const slider = document.getElementById('wpmSlider');
  const label  = document.getElementById('wpmValue');

  chrome.storage.sync.get({ wpm: 40 }, data => {
    slider.value = data.wpm;
    label.textContent = data.wpm + ' WPM';
  });

  slider.addEventListener('input', () => {
    const val = parseInt(slider.value);
    label.textContent = val + ' WPM';
    chrome.storage.sync.set({ wpm: val });
    sendCoderCmd('set-wpm', { wpm: val });
  });
}

function loadTypingState() {
  chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
    if (!tabs.length) return;
    chrome.tabs.sendMessage(tabs[0].id, { type: 'EXTENSION_COMMAND', command: 'get-state' },
      resp => {
        if (chrome.runtime.lastError) return;
        if (resp && resp.state) setTypingState(resp.state);
      }
    );
  });
}

function setTypingState(state) {
  typingState = state;
  const icon = document.getElementById('stateIcon');
  const txt  = document.getElementById('stateTxt');
  txt.className = 'state-txt';
  switch (state) {
    case 'active':
      icon.textContent = '▶'; txt.textContent = 'Typing in progress...'; txt.classList.add('active'); break;
    case 'paused':
      icon.textContent = '⏸'; txt.textContent = 'Paused'; txt.classList.add('paused'); break;
    default:
      icon.textContent = '⏹'; txt.textContent = 'Idle'; break;
  }
}

async function onSolve() {
  const btn = document.getElementById('btnStart');
  btn.disabled = true;
  btn.textContent = '⚡ Sending...';

  if (serverOnline) {
    try {
      const r = await fetch(`${BASE}/api/answers`, { signal: AbortSignal.timeout(2000) });
      if (r.ok) { const answers = await r.json(); showLatestAnswer(answers); }
    } catch (_) {}
  }

  sendCoderCmd('start-solving', {}, (success) => {
    btn.disabled = false;
    btn.textContent = '⚡ SOLVE';
    if (success) { showToast('Typing started!', 'ok'); setTypingState('active'); }
    else { showToast('Open a coding page first', 'err'); }
  });
}

function sendCoderCmd(command, extra = {}, callback) {
  chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
    if (!tabs.length) { if (callback) callback(false); return; }
    chrome.tabs.sendMessage(tabs[0].id,
      { type: 'EXTENSION_COMMAND', command, ...extra },
      resp => {
        const ok = !chrome.runtime.lastError && resp && resp.success !== false;
        if (!ok && command !== 'get-state') {
          if (command === 'trigger-pause') showToast('Paused', 'ok');
          if (command === 'trigger-stop')  showToast('Stopped', 'ok');
        }
        if (callback) callback(ok);
      }
    );
  });
}

function showToast(msg, type = '') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast' + (type ? ' ' + type : '') + ' show';
  setTimeout(() => { t.className = 'toast'; }, 2000);
}

function escHtml(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ── Init Code Typer controls ──────────────────────────────────────────────────
initWpmSlider();
document.getElementById('btnStart').addEventListener('click', onSolve);
document.getElementById('btnPause').addEventListener('click', () => sendCoderCmd('trigger-pause'));
document.getElementById('btnStop').addEventListener('click',  () => sendCoderCmd('trigger-stop'));
document.getElementById('apFetch').addEventListener('click', fetchLatestAnswer);

// ══════════════════════════════════════════════════════════════════════════════
// AUDIO CAPTURE (cloud / WebSocket mode)
// ══════════════════════════════════════════════════════════════════════════════

document.getElementById('captureBtn').addEventListener('click', async () => {
  if (capturing) {
    // Stop capture
    await chrome.runtime.sendMessage({ action: 'stopCapture' });
    capturing = false;
    updateCaptureStatus('stopped');
    return;
  }

  // Start capture on the currently active Meet/Teams/Zoom tab
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) { showToast('No active tab', 'err'); return; }

    const url = tab.url || '';
    const isMeetTab = url.includes('meet.google.com') || url.includes('teams.microsoft.com') ||
                      url.includes('teams.live.com') || url.includes('zoom.us');
    if (!isMeetTab) {
      showToast('Open Meet / Teams / Zoom tab first', 'err');
      document.getElementById('captureHint').textContent = '⚠ Switch to your interview tab, then click again';
      return;
    }

    document.getElementById('captureBtn').textContent = '⏳ Connecting...';
    document.getElementById('captureBtn').disabled = true;

    const resp = await chrome.runtime.sendMessage({ action: 'startCapture', tabId: tab.id });
    if (resp?.ok !== false) {
      capturing = true;
      updateCaptureStatus('connecting');
    } else {
      showToast(resp?.error || 'Capture failed', 'err');
      updateCaptureStatus('stopped');
    }
  } catch (e) {
    showToast(e.message || 'Error', 'err');
    updateCaptureStatus('stopped');
  }
});

function updateCaptureStatus(status) {
  const btn  = document.getElementById('captureBtn');
  const hint = document.getElementById('captureHint');
  btn.disabled = false;

  switch (status) {
    case 'connected':
      capturing = true;
      btn.textContent = '⏹ Stop Capture';
      btn.style.background = '#065f46'; btn.style.color = '#4ade80'; btn.style.borderColor = '#065f46';
      hint.textContent = '🟢 Capturing — speak and answers will appear automatically';
      document.getElementById('captureStatus').textContent = 'Live';
      document.getElementById('captureStatus').style.color = '#4ade80';
      break;
    case 'connecting':
      btn.textContent = '⏳ Connecting...';
      btn.style.background = '#78350f'; btn.style.color = '#f59e0b'; btn.style.borderColor = '#78350f';
      hint.textContent = 'Connecting to server...';
      break;
    case 'sent_segment':
      hint.textContent = '🎙 Speech detected — transcribing...';
      break;
    case 'ws_error':
      capturing = false;
      hideMobileUrl();
      btn.textContent = '🎙 Capture This Tab\'s Audio';
      btn.style.background = '#7f1d1d'; btn.style.color = '#f87171'; btn.style.borderColor = '#7f1d1d';
      hint.textContent = '⚠ Connection failed — check server URL and secret code in Settings';
      document.getElementById('captureStatus').textContent = '';
      break;
    default:
      capturing = false;
      hideMobileUrl();
      btn.textContent = '🎙 Capture This Tab\'s Audio';
      btn.style.background = '#1e3a5f'; btn.style.color = '#60a5fa'; btn.style.borderColor = '#1e3a5f';
      hint.textContent = 'Works on Meet, Teams, Zoom — captures interviewer\'s voice';
      document.getElementById('captureStatus').textContent = '';
  }
}

// ── Mobile URL display ────────────────────────────────────────────────────────

function showMobileUrl(url) {
  const box = document.getElementById('mobileUrlBox');
  const inp = document.getElementById('mobileUrlInput');
  if (!box || !inp) return;
  inp.value = url;
  box.style.display = 'block';
}

function copyMobileUrl() {
  const inp = document.getElementById('mobileUrlInput');
  const btn = document.getElementById('copyMobileBtn');
  if (!inp || !inp.value) return;
  navigator.clipboard.writeText(inp.value).then(() => {
    btn.textContent = '✓ Copied!';
    setTimeout(() => btn.textContent = 'Copy', 2000);
  });
}

// Hide mobile URL box when capture stops
function hideMobileUrl() {
  const box = document.getElementById('mobileUrlBox');
  if (box) box.style.display = 'none';
}


// ══════════════════════════════════════════════════════════════════════════════
// SETTINGS TAB
// ══════════════════════════════════════════════════════════════════════════════

function loadSettingsForm() {
  document.getElementById('settingsUrl').value  = BASE;
  document.getElementById('settingsCode').value = SECRET_CODE;
}

document.getElementById('settingsSave').addEventListener('click', () => {
  const url  = (document.getElementById('settingsUrl').value  || '').trim().replace(/\/$/, '');
  const code = (document.getElementById('settingsCode').value || '').trim();

  if (!url) { setSettingsMsg('Enter a server URL', '#f87171'); return; }

  chrome.storage.sync.set({ serverUrl: url, secretCode: code }, () => {
    BASE        = url;
    SECRET_CODE = code;
    setSettingsMsg('✓ Saved! Reconnecting...', '#4ade80');
    // Restart polling with new BASE
    clearInterval(pollTimer);
    lastHash = '';
    setOnline(false);
    pollTimer = setInterval(poll, 1500);
    poll();
    setTimeout(() => setSettingsMsg('', ''), 3000);
  });
});

document.getElementById('settingsTest').addEventListener('click', async () => {
  const url = (document.getElementById('settingsUrl').value || '').trim().replace(/\/$/, '');
  const code = (document.getElementById('settingsCode').value || '').trim();
  setSettingsMsg('Testing...', '#f59e0b');
  try {
    const r = await fetch(`${url}/health`, { signal: AbortSignal.timeout(4000) });
    if (r.ok) {
      const d = await r.json();
      setSettingsMsg(`✓ Connected! Cloud mode: ${d.cloud ? 'yes' : 'no'}`, '#4ade80');
      // Test auth if secret code provided
      if (code) {
        const ar = await fetch(`${url}/api/auth`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code }),
          signal: AbortSignal.timeout(4000)
        });
        const ad = await ar.json();
        setSettingsMsg(ad.ok ? '✓ Server + auth OK!' : '⚠ Wrong secret code', ad.ok ? '#4ade80' : '#f87171');
      }
    } else {
      setSettingsMsg(`Server error: ${r.status}`, '#f87171');
    }
  } catch (e) {
    setSettingsMsg(`Cannot reach server: ${e.message}`, '#f87171');
  }
});

function setSettingsMsg(text, color) {
  const el = document.getElementById('settingsMsg');
  el.textContent = text;
  el.style.color  = color || '#64748b';
}
