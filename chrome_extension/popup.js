// BASE URL loaded from storage — supports both local and Render.com
let BASE = 'https://particulate-arely-unrenovative.ngrok-free.dev';

// ── Ngrok bypass header (required when tunnelled via ngrok) ───────────────────
const NGROK_HEADERS = { 'ngrok-skip-browser-warning': 'true' };

/** Drop-in fetch() that always sends the ngrok bypass header */
function apiFetch(url, opts = {}) {
  return fetch(url, {
    ...opts,
    headers: { ...NGROK_HEADERS, ...(opts.headers || {}) },
  });
}

let SECRET_CODE  = '';
let USER_TOKEN   = '';   // per-user token set in Settings
let isOnline     = false;
let lastHash     = '';
let pollTimer    = null;
let serverOnline = false;
let typingState  = 'idle';
let _activeTab   = 'monitor';
let _lastAnswers  = [];

// ── Tab switching ──────────────────────────────────────────────────────────────
function switchTab(tab) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.getElementById('tab' + tab.charAt(0).toUpperCase() + tab.slice(1)).classList.add('active');
  document.getElementById('panel' + tab.charAt(0).toUpperCase() + tab.slice(1)).classList.add('active');
  _activeTab = tab;

  if (tab === 'coder') {
    fetchLatestAnswer();   // Always refresh slot list when switching to Coder tab
    loadTypingState();
  } else if (tab === 'monitor') {
    poll(); // Refresh feed when switching to Monitor tab
    initMonitorTab();
  }
}

// ── Bootstrap — load settings ──────────────────────────────
chrome.storage.sync.get({
  serverUrl: 'https://particulate-arely-unrenovative.ngrok-free.dev',
  secretCode: '',
  userToken: '',
}, (data) => {
  BASE        = (data.serverUrl || 'https://particulate-arely-unrenovative.ngrok-free.dev').replace(/\/$/, '');
  SECRET_CODE = data.secretCode || '';
  USER_TOKEN  = data.userToken  || '';
  poll(); // Initial poll on open
  loadSettingsForm();
  updatePortalCard();
  if (USER_TOKEN) loadUserIdentity(USER_TOKEN);
});

// Listen for messages from background
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'SERVER_MESSAGE' && msg.data) {
    const d = msg.data;
    if (d.type === 'answer') showToast('Answer received!', 'ok');
  }
});

// ── Single poll: check server online + refresh coder answers ─────────────────
// Monitor tab no longer loads answers (open the web dashboard instead).
async function poll() {
  try {
    const r = await apiFetch(`${BASE}/health`, { signal: AbortSignal.timeout(2500) });
    if (!r.ok) { setOnline(false); return; }
    setOnline(true);
    // Only refresh answers when Coder tab is open
    if (_activeTab === 'coder') {
      const answersUrl = USER_TOKEN
        ? `${BASE}/api/answers?user_token=${encodeURIComponent(USER_TOKEN)}`
        : `${BASE}/api/answers`;
      const ar = await apiFetch(answersUrl, { signal: AbortSignal.timeout(2500) });
      if (ar.ok) {
        const answers = await ar.json();
        _lastAnswers = answers || [];
        const hash = JSON.stringify(answers.slice(0, 5).map(a => a.answer?.slice(-40) + a.is_complete));
        if (hash !== lastHash) { lastHash = hash; showLatestAnswer(answers); }
      }
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
}

// ── Portal card ───────────────────────────────────────────────────────────────

function updatePortalCard() {
  const card = document.getElementById('portalCard');
  const hint = document.getElementById('noTokenHint');
  const link = document.getElementById('portalLink');
  const chip = document.getElementById('portalTokenChip');
  if (!card || !hint) return;

  if (USER_TOKEN) {
    card.style.display = '';
    hint.style.display = 'none';
    if (link) link.href = `${BASE}/portal/${encodeURIComponent(USER_TOKEN)}`;
    if (chip) chip.textContent = USER_TOKEN;
    // Reset name/role to loading state
    const nameEl = document.getElementById('portalUserName');
    const roleEl = document.getElementById('portalUserRole');
    if (nameEl) nameEl.textContent = 'Loading...';
    if (roleEl) roleEl.textContent = '';
  } else {
    card.style.display = 'none';
    hint.style.display = '';
  }
}

async function loadUserIdentity(token) {
  if (!token) return;
  try {
    const r = await apiFetch(`${BASE}/api/ext_users/${encodeURIComponent(token)}/settings`,
      { signal: AbortSignal.timeout(3000) });
    if (!r.ok) { _setPortalIdentity(token, ''); return; }
    const d = await r.json();
    _setPortalIdentity(d.name || token, d.role || '');
    // Also update portal link with correct base
    const link = document.getElementById('portalLink');
    if (link) link.href = `${BASE}/portal/${encodeURIComponent(token)}`;
  } catch (_) {
    _setPortalIdentity(token, '');
  }
}

function _setPortalIdentity(name, role) {
  const nameEl = document.getElementById('portalUserName');
  const roleEl = document.getElementById('portalUserRole');
  if (nameEl) nameEl.textContent = name || USER_TOKEN;
  if (roleEl) roleEl.textContent = role || (role === '' ? 'Interview Candidate' : role);
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
  } catch (_) { }
}

function formatAnswer(text) {
  if (!text) return '';

  const codeMatch = text.match(/```[\w]*\n?([\s\S]*?)```/);
  if (codeMatch) {
    const pre = text.slice(0, codeMatch.index).trim();
    const code = codeMatch[1].trim();
    const post = text.slice(codeMatch.index + codeMatch[0].length).trim();
    let out = '';
    if (pre) out += formatAnswer(pre);
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

// ══════════════════════════════════════════════════════════════════════════════
// CODE TYPER TAB
// ══════════════════════════════════════════════════════════════════════════════

async function fetchLatestAnswer() {
  const btn = document.getElementById('apFetch');
  btn.textContent = '↻ Loading...';
  try {
    const answersUrl = USER_TOKEN
      ? `${BASE}/api/answers?user_token=${encodeURIComponent(USER_TOKEN)}`
      : `${BASE}/api/answers`;
    const r = await apiFetch(answersUrl, { signal: AbortSignal.timeout(3000) });
    if (r.ok) {
      const answers = await r.json();
      showLatestAnswer(answers);
      btn.textContent = '↻ Refreshed!';
      setTimeout(() => { btn.textContent = '↻ Refresh slots'; }, 1500);
      return;
    }
  } catch (_) { }
  btn.textContent = '↻ Refresh slots';
  document.getElementById('apContent').innerHTML =
    '<div class="ap-empty">🔴 Server offline</div>';
}

function showLatestAnswer(answers) {
  const el = document.getElementById('apContent');
  if (!answers || answers.length === 0) {
    el.innerHTML = '<div class="ap-empty">No questions yet — waiting for interview</div>';
    return;
  }

  const completed = [...answers].reverse().filter(a => a.is_complete && a.answer);
  if (completed.length === 0) {
    el.innerHTML = '<div class="ap-empty">No completed answers yet</div>';
    return;
  }

  const items = completed.map((a, i) => {
    const slotNum = i + 1;
    const q = (a.question || '').slice(0, 46) + (a.question && a.question.length > 46 ? '…' : '');
    const hasCode = a.answer.includes('def ') || a.answer.includes('```');
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
  const label = document.getElementById('wpmValue');

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
  const txt = document.getElementById('stateTxt');
  txt.className = 'state-txt';
  switch (state) {
    case 'active':
      icon.textContent = '▶'; txt.textContent = 'Typing...'; txt.classList.add('active'); break;
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
      const answersUrl = USER_TOKEN
        ? `${BASE}/api/answers?user_token=${encodeURIComponent(USER_TOKEN)}`
        : `${BASE}/api/answers`;
      const r = await apiFetch(answersUrl, { signal: AbortSignal.timeout(2000) });
      if (r.ok) { const answers = await r.json(); showLatestAnswer(answers); }
    } catch (_) { }
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
        if (callback) callback(ok);
      }
    );
  });
}

function showToast(msg, type = '') {
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.className = 'toast' + (type ? ' ' + type : '') + ' show';
  setTimeout(() => { t.className = 'toast'; }, 2000);
}

function escHtml(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ══════════════════════════════════════════════════════════════════════════════
// AUDIO STREAM TAB CONTROLS
// ══════════════════════════════════════════════════════════════════════════════

(function initAudioStream() {
  const startBtn  = document.getElementById('audioStartBtn');
  const stopBtn   = document.getElementById('audioStopBtn');
  const statusEl  = document.getElementById('audioStatus');

  if (!startBtn) return;

  function setAudioStatus(text, color) {
    statusEl.textContent = text;
    statusEl.style.color = color || '#475569';
  }

  function setStreamingUI(active, tabTitle) {
    if (active) {
      startBtn.style.background = '#14532d';
      startBtn.style.borderColor = '#14532d';
      startBtn.style.color = '#4ade80';
      startBtn.textContent = '● Streaming';
      startBtn.disabled = true;
      stopBtn.disabled = false;
      const src = tabTitle ? `Capturing: ${tabTitle.slice(0, 40)}` : 'Streaming tab audio → Drishi server';
      setAudioStatus(src, '#4ade80');
    } else {
      startBtn.style.background = '#065f46';
      startBtn.style.borderColor = '#065f46';
      startBtn.style.color = '#4ade80';
      startBtn.textContent = '▶ Start Stream';
      startBtn.disabled = false;
      stopBtn.disabled = false;
      setAudioStatus('Auto-detects Google Meet / Teams / Zoom tab', '#94a3b8');
    }
  }

  // Restore state on popup open
  chrome.storage.local.get({ audioStreamStatus: 'stopped', captureTabTitle: '' }, (data) => {
    setStreamingUI(data.audioStreamStatus === 'streaming', data.captureTabTitle);
  });

  // Listen for status updates from background
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === 'local' && (changes.audioStreamStatus || changes.captureTabTitle)) {
      chrome.storage.local.get({ audioStreamStatus: 'stopped', captureTabTitle: '' }, (data) => {
        setStreamingUI(data.audioStreamStatus === 'streaming', data.captureTabTitle);
      });
    }
  });

  startBtn.addEventListener('click', async () => {
    startBtn.disabled = true;
    startBtn.textContent = '⏳ Starting...';
    setAudioStatus('Detecting Meet / Teams / Zoom tab...', '#f59e0b');
    try {
      const resp = await chrome.runtime.sendMessage({ action: 'audio_start', userToken: USER_TOKEN });
      if (resp?.ok) {
        const { captureTabTitle } = await new Promise(r => chrome.storage.local.get({ captureTabTitle: '' }, r));
        setStreamingUI(true, captureTabTitle);
        showToast('Audio streaming started!', 'ok');
      } else {
        startBtn.disabled = false;
        startBtn.textContent = '▶ Start Stream';
        setAudioStatus('Error: ' + (resp?.error || 'unknown'), '#f87171');
        showToast('Stream failed — open Meet / Teams / Zoom in Chrome first', 'err');
      }
    } catch (e) {
      startBtn.disabled = false;
      startBtn.textContent = '▶ Start Stream';
      setAudioStatus('Error: ' + e.message, '#f87171');
    }
  });

  stopBtn.addEventListener('click', async () => {
    stopBtn.disabled = true;
    setAudioStatus('Stopping...', '#f59e0b');
    try {
      await chrome.runtime.sendMessage({ action: 'audio_stop' });
      setStreamingUI(false);
      showToast('Audio stream stopped', '');
    } catch (e) {
      setAudioStatus('Error: ' + e.message, '#f87171');
    }
    stopBtn.disabled = false;
  });
})();

initWpmSlider();
document.getElementById('btnStart').addEventListener('click', onSolve);
document.getElementById('btnPause').addEventListener('click', () => sendCoderCmd('trigger-pause'));
document.getElementById('btnStop').addEventListener('click', () => sendCoderCmd('trigger-stop'));
document.getElementById('apFetch').addEventListener('click', fetchLatestAnswer);

// ══════════════════════════════════════════════════════════════════════════════
// MONITOR TAB
// ══════════════════════════════════════════════════════════════════════════════

function initMonitorTab() {
  loadMonitorQr();
  updatePortalCard();
}

async function loadMonitorQr() {
  try {
    const r = await apiFetch(`${BASE}/api/local_url`, { signal: AbortSignal.timeout(2000) });
    const d = await r.json();
    const monitorUrl = d.monitor_url || BASE + '/monitor';
    const fullUrl = d.url || BASE + '/';

    document.getElementById('monitorUrl').textContent = monitorUrl;

    const _qr = (url, size) =>
      `https://api.qrserver.com/v1/create-qr-code/?size=${size}x${size}&data=${encodeURIComponent(url)}&format=png&margin=4`;

    document.getElementById('monitorQrImg').src = _qr(monitorUrl, 108);
    document.getElementById('fullUiQrImg').src = _qr(fullUrl, 108);

    document.getElementById('monitorCopyBtn')._url = monitorUrl;
    document.getElementById('fullUiCopyBtn')._url = fullUrl;
    // Update portal link with resolved base
    const portalLink = document.getElementById('portalLink');
    if (portalLink && USER_TOKEN) portalLink.href = `${d.url?.replace(/\/$/, '') || BASE}/portal/${encodeURIComponent(USER_TOKEN)}`;
  } catch {
    document.getElementById('monitorUrl').textContent = BASE + '/monitor';
    const _qr = (url, size) =>
      `https://api.qrserver.com/v1/create-qr-code/?size=${size}x${size}&data=${encodeURIComponent(url)}&format=png&margin=4`;
    document.getElementById('monitorQrImg').src = _qr(BASE + '/monitor', 108);
    document.getElementById('fullUiQrImg').src = _qr(BASE + '/', 108);
    document.getElementById('monitorCopyBtn')._url = BASE + '/monitor';
    document.getElementById('fullUiCopyBtn')._url = BASE + '/';
  }
}

function renderMonitorFeed(answers) {
  const feed = document.getElementById('monitorFeed');
  const empty = document.getElementById('monitorEmpty');
  const count = document.getElementById('monitorCount');

  if (!answers || answers.length === 0) {
    empty.style.display = '';
    feed.querySelectorAll('.qa-card').forEach(el => el.remove());
    count.textContent = '';
    return;
  }
  empty.style.display = 'none';
  count.textContent = `${answers.length} answer${answers.length !== 1 ? 's' : ''}`;

  feed.querySelectorAll('.qa-card').forEach(el => el.remove());
  answers.slice(0, 8).forEach((item, idx) => {
    const card = document.createElement('div');
    card.className = 'qa-card' + (idx === 0 && item.is_complete ? ' latest' : '');

    const src = (item.metrics && item.metrics.source) || '';
    const isDb = src.startsWith('db-');
    const isGen = !item.is_complete;
    const badgeCls = isDb ? 'db' : (isGen ? 'gen' : 'api');
    const badgeTxt = isDb ? 'DB' : (isGen ? 'GEN' : 'API');

    const qText = (item.question || '').trim();
    const ansText = (item.answer || '').trim();
    const ansHtml = !item.is_complete
      ? (ansText ? `<div class="ans-text">${esc(ansText)}</div>` : `<div class="thinking">Generating answer</div>`)
      : formatAnswer(ansText);

    card.innerHTML = `
      <div class="card-top">
        <span class="src-badge ${badgeCls}">${badgeTxt}</span>
        <span class="q-text">${esc(qText)}</span>
        <button class="copy-btn" data-ans="${esc(ansText)}" title="Copy">Copy</button>
      </div>
      <div class="ans-wrap">${ansHtml}</div>`;
    feed.appendChild(card);
  });

  feed.scrollTop = 0;
  feed.querySelectorAll('.copy-btn').forEach(btn => {
    btn.addEventListener('click', () => copyText(btn.dataset.ans, btn));
  });

  // Auto-speak the newest complete answer if TTS is enabled
  const newest = answers[0];
  if (newest && newest.is_complete && newest.answer) {
    ttsSpeak(newest.answer);
  }
}

async function _copyUrlBtn(btn, fallbackUrl) {
  const url = btn._url || fallbackUrl || document.getElementById('monitorUrl').textContent;
  try {
    await navigator.clipboard.writeText(url);
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = orig; btn.classList.remove('copied'); }, 1500);
  } catch { showToast('Copy failed', 'err'); }
}

document.getElementById('monitorCopyBtn').addEventListener('click', function () {
  _copyUrlBtn(this, BASE + '/monitor');
});

document.getElementById('fullUiCopyBtn').addEventListener('click', function () {
  _copyUrlBtn(this, BASE + '/');
});

// ══════════════════════════════════════════════════════════════════════════════
// SETTINGS TAB
// ══════════════════════════════════════════════════════════════════════════════

function loadSettingsForm() {
  document.getElementById('settingsUrl').value = BASE;
  document.getElementById('settingsCode').value = SECRET_CODE;
  document.getElementById('settingsUserToken').value = USER_TOKEN;
  _updateUserTokenStatus(USER_TOKEN);
}

function _updateUserTokenStatus(token) {
  const el = document.getElementById('userTokenStatus');
  const portalLinkEl = document.getElementById('settingsPortalLink');
  if (!el) return;
  if (token) {
    el.textContent = `✓ Token set — answers isolated to your account`;
    el.style.color = '#4ade80';
    if (portalLinkEl) {
      portalLinkEl.style.display = '';
      portalLinkEl.href = `${BASE}/portal/${encodeURIComponent(token)}`;
    }
  } else {
    el.textContent = 'No token — using shared/global answers (system audio mode)';
    el.style.color = '#64748b';
    if (portalLinkEl) portalLinkEl.style.display = 'none';
  }
}

document.getElementById('settingsSave').addEventListener('click', () => {
  const url   = (document.getElementById('settingsUrl').value || '').trim().replace(/\/$/, '');
  const code  = (document.getElementById('settingsCode').value || '').trim();
  const token = (document.getElementById('settingsUserToken').value || '').trim();

  if (!url) { setSettingsMsg('Enter a server URL', '#f87171'); return; }

  chrome.storage.sync.set({ serverUrl: url, secretCode: code, userToken: token }, () => {
    BASE        = url;
    SECRET_CODE = code;
    USER_TOKEN  = token;
    _updateUserTokenStatus(token);
    setSettingsMsg('✓ Saved!', '#4ade80');
    lastHash = '';
    setOnline(false);
    poll();
    updatePortalCard();
    if (token) loadUserIdentity(token);
    setTimeout(() => setSettingsMsg('', ''), 3000);
  });
});

document.getElementById('settingsTest').addEventListener('click', async () => {
  const url = (document.getElementById('settingsUrl').value || '').trim().replace(/\/$/, '');
  setSettingsMsg('Testing...', '#f59e0b');
  try {
    const r = await apiFetch(`${url}/health`, { signal: AbortSignal.timeout(4000) });
    if (r.ok) {
      setSettingsMsg(`✓ Connected!`, '#4ade80');
    } else {
      setSettingsMsg(`Server error: ${r.status}`, '#f87171');
    }
  } catch (e) {
    setSettingsMsg(`Cannot reach server`, '#f87171');
  }
});

// ── Init Tab listeners ────────────────────────────────────────────────────────
document.getElementById('tabCoder').addEventListener('click', () => switchTab('coder'));
document.getElementById('tabMonitor').addEventListener('click', () => switchTab('monitor'));
document.getElementById('tabSettings').addEventListener('click', () => switchTab('settings'));

// ── TTS (Read Answers Aloud) ──────────────────────────────────────────────────
let _ttsEnabled = false;
let _ttsLastSpoken = '';

(function initTts() {
  const toggle = document.getElementById('ttsToggle');
  const stopBtn = document.getElementById('ttsStopBtn');
  if (!toggle) return;

  // Restore saved preference
  chrome.storage.sync.get({ ttsEnabled: false }, d => {
    _ttsEnabled = d.ttsEnabled;
    toggle.checked = _ttsEnabled;
  });

  toggle.addEventListener('change', () => {
    _ttsEnabled = toggle.checked;
    chrome.storage.sync.set({ ttsEnabled: _ttsEnabled });
    if (!_ttsEnabled) speechSynthesis.cancel();
  });

  if (stopBtn) stopBtn.addEventListener('click', () => speechSynthesis.cancel());
})();

function ttsSpeak(text) {
  if (!_ttsEnabled || !text || text === _ttsLastSpoken) return;
  _ttsLastSpoken = text;
  speechSynthesis.cancel();
  const utt = new SpeechSynthesisUtterance(text);
  utt.rate = 0.95;
  utt.pitch = 1;
  utt.lang = 'en-US';
  speechSynthesis.speak(utt);
}

// ── Solve Problem from Screenshot ─────────────────────────────────────────────
document.getElementById('btnSolveImg').addEventListener('click', async () => {
  const btn = document.getElementById('btnSolveImg');
  const status = document.getElementById('captureStatus');
  btn.disabled = true;
  btn.textContent = '⏳ Capturing screenshot...';
  try {
    const imgDataUrl = await chrome.tabs.captureVisibleTab(null, { format: 'png' });
    const b64 = imgDataUrl.replace(/^data:image\/png;base64,/, '');
    status.textContent = 'Sending to AI solver...';
    status.style.color = '#60a5fa';

    const r = await apiFetch(`${BASE}/api/solve_from_image`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image: b64, media_type: 'image/png' }),
    });
    const d = await r.json();
    if (d.solution) {
      status.textContent = '';
      showToast('Solution ready!', 'ok');
      // Show result in a panel below the button
      let resultEl = document.getElementById('solveImgResult');
      if (!resultEl) {
        resultEl = document.createElement('div');
        resultEl.id = 'solveImgResult';
        resultEl.style.cssText = 'margin-top:8px;background:#0a0e1a;border:1px solid #1e3a5f;border-radius:8px;padding:10px 12px;';
        btn.parentNode.insertBefore(resultEl, status);
      }
      resultEl.innerHTML = `
        <div style="font-size:9px;font-weight:800;color:#60a5fa;letter-spacing:.5px;margin-bottom:6px;">📋 SOLUTION</div>
        <pre style="font-size:10px;color:#a5f3fc;white-space:pre-wrap;word-break:break-word;max-height:180px;overflow-y:auto;font-family:monospace;line-height:1.5;">${d.solution.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}</pre>`;
      if (_ttsEnabled) ttsSpeak('Solution generated from screenshot.');
    } else {
      status.textContent = d.error || 'No solution returned';
      status.style.color = '#f87171';
    }
  } catch (e) {
    status.textContent = '❌ ' + e.message;
    status.style.color = '#f87171';
    showToast('Screenshot failed', 'err');
  }
  btn.disabled = false;
  btn.textContent = '📷 Solve Problem from Screenshot';
  setTimeout(() => { status.textContent = ''; }, 4000);
});

// ── CAPTURE Button (Ctrl+Alt+Q) ─────────────────────────────────────────────
document.getElementById('btnCapture').addEventListener('click', async () => {
  const btn = document.getElementById('btnCapture');
  const status = document.getElementById('captureStatus');
  
  btn.disabled = true;
  btn.textContent = '⏳ Capturing...';
  btn.style.opacity = '0.7';
  status.textContent = 'Sending force capture to meeting page...';
  
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) throw new Error('No active tab');
    
    const resp = await chrome.tabs.sendMessage(tab.id, { type: 'FORCE_CAPTURE' });
    
    if (resp && resp.success) {
      status.textContent = `✅ Captured on ${resp.platform}! Check dashboard.`;
      status.style.color = '#4ade80';
      showToast('Capture sent! Check dashboard.', 'ok');
    } else {
      status.textContent = '⚠️ Not on a meeting page';
      status.style.color = '#f59e0b';
    }
  } catch (e) {
    status.textContent = '❌ Error: Open Teams/Meet/Zoom first';
    status.style.color = '#f87171';
    showToast('Open a meeting page first', 'err');
  }
  
  setTimeout(() => {
    btn.disabled = false;
    btn.innerHTML = '📸 CAPTURE NOW <span style="font-size:10px;opacity:.7;">(Ctrl+Alt+Q)</span>';
    btn.style.opacity = '1';
  }, 2000);
});

function setSettingsMsg(text, color) {
  const el = document.getElementById('settingsMsg');
  el.textContent = text;
  el.style.color = color || '#64748b';
}

// ── Browser Monitor controls ───────────────────────────────────────────────
(function initBrowserMonitor() {
  const startBtn = document.getElementById('monStartBtn');
  const stopBtn = document.getElementById('monStopBtn');
  const statusEl = document.getElementById('monStatus');
  const sessionEl = document.getElementById('monSessionId');
  const screenEl = document.getElementById('monScreenEnabled');
  const viewerUrlEl = document.getElementById('monViewerUrl');
  const viewerCopyBtn = document.getElementById('monViewerCopyBtn');
  const monitorCount = document.getElementById('monitorCount');

  if (!startBtn) return;

  // Hover effects for the copy button
  if (viewerCopyBtn) {
    viewerCopyBtn.addEventListener('mouseenter', () => {
      viewerCopyBtn.style.borderColor = '#818cf8';
      viewerCopyBtn.style.color = '#818cf8';
    });
    viewerCopyBtn.addEventListener('mouseleave', () => {
      if (!viewerCopyBtn.classList.contains('copied')) {
        viewerCopyBtn.style.borderColor = '#252840';
        viewerCopyBtn.style.color = '#64748b';
      }
    });
    viewerCopyBtn.addEventListener('click', async () => {
      const url = viewerUrlEl.textContent.trim();
      if (!url || url === '\u2014' || url === '\u2014') { showToast('No URL yet — start monitor first', 'err'); return; }
      try {
        await navigator.clipboard.writeText(url);
        const orig = viewerCopyBtn.textContent;
        viewerCopyBtn.textContent = '\u2705 Copied!';
        viewerCopyBtn.style.borderColor = '#4ade80';
        viewerCopyBtn.style.color = '#4ade80';
        viewerCopyBtn.classList.add('copied');
        setTimeout(() => {
          viewerCopyBtn.textContent = orig;
          viewerCopyBtn.style.borderColor = '#252840';
          viewerCopyBtn.style.color = '#64748b';
          viewerCopyBtn.classList.remove('copied');
        }, 1800);
        showToast('Viewer URL copied!', 'ok');
      } catch (_) { showToast('Copy failed', 'err'); }
    });
  }

  function setMonStatus(text, color) {
    statusEl.textContent = text;
    statusEl.style.color = color || '#475569';
  }

  function updateViewerUrl(serverUrl, sessionId) {
    const base = serverUrl.replace(/\/$/, '');
    // Use the new simplified /v/ format
    const url = `${base}/v/${encodeURIComponent(sessionId)}`;
    viewerUrlEl.textContent = url;
    viewerUrlEl.title = url;
  }

  function updateMonCountBadge(count) {
    if (!monitorCount) return;
    if (count > 0) {
      monitorCount.textContent = `(${count} viewer${count !== 1 ? 's' : ''})`;
      monitorCount.style.color = '#4ade80';
    } else {
      monitorCount.textContent = '';
    }
  }

  chrome.runtime.sendMessage({ type: 'mon_get_state' }, (state) => {
    if (!state) return;
    sessionEl.value = state.sessionId || 'default';
    screenEl.checked = state.screenEnabled || false;
    updateMonCountBadge(state.streamViewerCount || 0);
    if (state.monitoring) {
      setMonStatus('Monitoring active — ' + (state.connectionStatus || ''), '#4ade80');
    }
  });

  // Listen for real-time viewer count updates via storage
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === 'local' && changes.monitorSettings) {
      const newState = changes.monitorSettings.newValue;
      if (newState) {
        updateMonCountBadge(newState.streamViewerCount || 0);
        if (newState.monitoring) {
          setMonStatus('Monitoring active — ' + (newState.connectionStatus || ''), '#4ade80');
        } else {
          setMonStatus(newState.connectionStatus === 'disconnected' ? 'Stopped' : newState.connectionStatus, '#64748b');
        }
      }
    }
  });

  startBtn.addEventListener('click', async () => {
    const sessionId = sessionEl.value.trim() || 'default';
    const screenEnabled = screenEl.checked;
    setMonStatus('Starting…', '#f59e0b');
    try {
      const resp = await chrome.runtime.sendMessage({
        type: 'mon_start_monitoring',
        sessionId,
        screenEnabled,
      });
      if (resp?.ok) {
        setMonStatus('Monitoring active', '#4ade80');
        chrome.storage.sync.get({ serverUrl: 'https://particulate-arely-unrenovative.ngrok-free.dev' }, (data) => {
          updateViewerUrl(data.serverUrl, sessionId);
        });
      } else {
        setMonStatus('Error: ' + (resp?.error || 'unknown'), '#f87171');
      }
    } catch (e) {
      setMonStatus('Error: ' + e.message, '#f87171');
    }
  });

  stopBtn.addEventListener('click', async () => {
    setMonStatus('Stopping…', '#f59e0b');
    try {
      await chrome.runtime.sendMessage({ type: 'mon_stop_monitoring' });
      setMonStatus('Stopped', '#64748b');
      viewerUrlEl.textContent = '\u2014';
      if (viewerCopyBtn) {
        viewerCopyBtn.style.borderColor = '#252840';
        viewerCopyBtn.style.color = '#64748b';
        viewerCopyBtn.textContent = '\ud83d\udccb Copy';
        viewerCopyBtn.classList.remove('copied');
      }
    } catch (e) {
      setMonStatus('Error: ' + e.message, '#f87171');
    }
  });

  chrome.storage.sync.get({ serverUrl: 'https://particulate-arely-unrenovative.ngrok-free.dev' }, (data) => {
    chrome.runtime.sendMessage({ type: 'mon_get_state' }, (state) => {
      if (state?.monitoring) {
        updateViewerUrl(data.serverUrl, state.sessionId || 'default');
      }
    });

    // Add a periodic diagnostic check while popup is open
    setInterval(async () => {
      try {
        const r = await apiFetch(`${data.serverUrl}/health`, { signal: AbortSignal.timeout(2000) });
        if (r.ok) {
          const statusTxt = document.getElementById('statusTxt');
          if (statusTxt) {
            statusTxt.textContent = "Tunnel: OK";
            statusTxt.style.color = "#4ade80";
          }
        }
      } catch (e) { }
    }, 5000);
  });
})();
