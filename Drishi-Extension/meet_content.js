/**
 * content.js — Auto-captures chat messages from Google Meet and Microsoft Teams.
 *
 * Strategy: DOM-agnostic text extraction.
 * Instead of relying on CSS class names (which Meet changes every few weeks),
 * we detect the chat panel by ARIA labels + role attributes, then extract
 * all visible text from it. Class names ≠ required.
 *
 * Injected on: https://meet.google.com/* and https://*.teams.microsoft.com/*
 */

const SERVER = 'http://localhost:8000';
const DEDUP_TTL_MS = 15000;  // ignore same text within 15s
const MIN_LEN = 12;   // raised from 8 — filters "Pin message", "12:39 AM", etc.
const MAX_LEN = 600;

// ── Meet / Teams UI noise — exact phrases to skip ───────────────────────────
// These are system messages and UI labels, not participant chat.
const UI_NOISE = new Set([
  'pin message', 'send message', 'send a message',
  'no chat messages yet', 'continuous chat is off', 'continuous chat is on',
  'let participants send messages', 'in-call messages',
  'messages won\'t be saved when the call ends',
  'you can pin a message to make it visible for people who join later',
  'type a message', 'write a message', 'message everyone',
  'chat with everyone', 'chat with host',
  'only you can see this message', 'message deleted',
  'thumbs up', 'thumbs down', 'add reaction',
  'reply', 'more options', 'edit message',
]);

function isUiNoise(text) {
  const lower = text.toLowerCase().trim();
  // Exact match in noise set
  if (UI_NOISE.has(lower)) return true;
  // Starts with time pattern "12:39 AM" or "00:39"
  if (/^\d{1,2}:\d{2}(\s*(AM|PM))?$/.test(lower)) return true;
  // Only digits / punctuation
  if (/^[\d\s:.,!?-]+$/.test(lower)) return true;
  // Meet system banners (long but contain these markers)
  if (lower.includes("messages won't be saved") ||
      lower.includes('continuous chat is') ||
      lower.includes('pin a message to make it visible')) return true;
  return false;
}

const sent = new Map();   // text → timestamp

const host = window.location.hostname;
const IS_MEET  = host.includes('meet.google.com');
const IS_TEAMS = host.includes('teams.microsoft.com') || host.includes('teams.live.com');

if (!IS_MEET && !IS_TEAMS) {
  // Not a supported platform — do nothing
  console.log('[IVA] Not on Meet or Teams — content script idle');
  // Don't throw — let it exit silently
} else {

const PLATFORM = IS_MEET ? 'google-meet-chat' : 'teams-chat';

// ── Send to backend ─────────────────────────────────────────────────────────
function sendToBackend(text) {
  text = text.trim();
  if (!text || text.length < MIN_LEN || text.length > MAX_LEN) return;
  if (isUiNoise(text)) return;   // Skip Meet UI chrome

  const now = Date.now();
  if (sent.has(text) && now - sent.get(text) < DEDUP_TTL_MS) return;
  sent.set(text, now);

  console.log(`[IVA] Captured: "${text.slice(0, 60)}..."`);

  fetch(`${SERVER}/api/cc_question`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question: text, source: PLATFORM }),
  }).then(r => r.json())
    .then(d => console.log(`[IVA] Backend: ${d.status || d.error}`))
    .catch(() => console.log('[IVA] Server offline or unreachable'));
}

// ── Clean text from a node ──────────────────────────────────────────────────
function nodeText(el) {
  const clone = el.cloneNode(true);
  // Remove UI chrome: buttons, icons, timestamps, tooltips
  clone.querySelectorAll(
    'button, time, [aria-hidden="true"], img, svg, .VfPpkd-RLmnJb, [role="tooltip"]'
  ).forEach(e => e.remove());
  return clone.textContent.replace(/\s+/g, ' ').trim();
}

// ── Find the chat panel (DOM-agnostic) ──────────────────────────────────────
// Uses ARIA roles/labels which are far more stable than CSS class names.
let _chatPanel = null;

function findChatPanel() {
  if (_chatPanel && document.body.contains(_chatPanel)) return _chatPanel;

  if (IS_MEET) {
    // Strategy 1: aria-label on the send-message input area
    const inputEl = document.querySelector('[aria-label*="Send a message" i], [aria-label*="message to everyone" i], [aria-label*="chat" i][role="textbox"]');
    if (inputEl) {
      // Walk up until we find a sizeable container
      let el = inputEl.parentElement;
      for (let i = 0; i < 8 && el; i++) {
        if (el.scrollHeight > 200) { _chatPanel = el; return el; }
        el = el.parentElement;
      }
    }

    // Strategy 2: find a scrollable list/log that contains message-like elements
    const lists = document.querySelectorAll('[role="list"], [role="log"], [role="feed"]');
    for (const list of lists) {
      const items = list.querySelectorAll('[role="listitem"], li');
      if (items.length >= 1 && list.scrollHeight > 100) {
        _chatPanel = list;
        return list;
      }
    }

    // Strategy 3: jsname attributes Google uses for chat (changes, but worth trying)
    const MEET_PANEL_JSNAMES = ['A4nIid', 'bnzx', 'x9IQ0c', 'vlQKpe', 'UVQVnd'];
    for (const jn of MEET_PANEL_JSNAMES) {
      const el = document.querySelector(`[jsname="${jn}"]`);
      if (el && el.scrollHeight > 100) { _chatPanel = el; return el; }
    }

    // Strategy 4: any data-panel-id element (Meet panels)
    for (const id of ['2', '3', 'chat']) {
      const el = document.querySelector(`[data-panel-id="${id}"]`);
      if (el) { _chatPanel = el; return el; }
    }
  }

  if (IS_TEAMS) {
    const el = document.querySelector('[data-tid="chat-pane-list"], [role="feed"], [aria-label*="Chat" i][role="region"]');
    if (el) { _chatPanel = el; return el; }
  }

  return null;
}

// ── Extract individual message texts from a container ───────────────────────
function extractTextsFromPanel(panel) {
  if (!panel) return [];

  const results = [];

  // Method A: role="listitem" / li elements (most structured)
  const items = panel.querySelectorAll('[role="listitem"], li');
  if (items.length > 0) {
    for (const item of items) {
      const t = nodeText(item);
      if (t.length >= MIN_LEN) results.push(t);
    }
    if (results.length > 0) return results;
  }

  // Method B: data-message-id elements
  const msgEls = panel.querySelectorAll('[data-message-id]');
  if (msgEls.length > 0) {
    for (const el of msgEls) {
      const t = nodeText(el);
      if (t.length >= MIN_LEN) results.push(t);
    }
    if (results.length > 0) return results;
  }

  // Method C: jsname elements with text
  const MEET_MSG_JSNAMES = ['r4nke', 'YPqjbf', 'MmEJAf', 'EjRKef', 'B6zTjf'];
  for (const jn of MEET_MSG_JSNAMES) {
    const els = panel.querySelectorAll(`[jsname="${jn}"]`);
    for (const el of els) {
      const t = nodeText(el);
      if (t.length >= MIN_LEN) results.push(t);
    }
    if (results.length > 0) return results;
  }

  // Method D: direct text children with enough content (last resort)
  const children = panel.querySelectorAll('div, span');
  for (const child of children) {
    // Skip nested containers (only look at near-leaf nodes)
    const childCount = child.querySelectorAll('div, span, p').length;
    if (childCount > 3) continue;
    const t = nodeText(child);
    if (t.length >= MIN_LEN && t.length <= MAX_LEN) {
      // Make sure it's visible text, not empty/hidden
      const style = window.getComputedStyle(child);
      if (style.display !== 'none' && style.visibility !== 'hidden') {
        results.push(t);
      }
    }
  }

  return results;
}

// ── Full-page fallback scan (for when panel detection fails) ─────────────────
// Looks for any text node that looks like a chat message based on DOM position.
function fullPageScan() {
  const results = [];

  // Try known Meet message selectors (class names change but worth trying)
  const MEET_CLASS_SELECTORS = [
    '[data-message-id]',
    '[jsname="r4nke"]', '[jsname="YPqjbf"]', '[jsname="MmEJAf"]',
    '.GDhqjd', '.oIy2qc', '.Ss4fHf', '.vbfxQd', '.RDPZE', '.T4LgNb',
    '.zs7s8d', '.NWpY0', '.bj8aOd',  // 2025 Meet classes
  ];

  for (const sel of MEET_CLASS_SELECTORS) {
    try {
      const els = document.querySelectorAll(sel);
      if (els.length > 0) {
        for (const el of els) {
          const t = nodeText(el);
          if (t.length >= MIN_LEN && t.length <= MAX_LEN) results.push(t);
        }
        if (results.length > 0) {
          console.log(`[IVA] Found ${results.length} messages with selector: ${sel}`);
          return results;
        }
      }
    } catch { }
  }

  // Teams selectors
  if (IS_TEAMS) {
    const TEAMS_SELECTORS = [
      '[data-tid="chat-pane-message"]',
      '.fui-ChatMessage',
      '[class*="chat-message-content"]',
      '[data-track-action-outcome="messageSent"]',
    ];
    for (const sel of TEAMS_SELECTORS) {
      try {
        const els = document.querySelectorAll(sel);
        if (els.length > 0) {
          for (const el of els) {
            const t = nodeText(el);
            if (t.length >= MIN_LEN) results.push(t);
          }
          if (results.length > 0) return results;
        }
      } catch { }
    }
  }

  return results;
}

// ── Detect if node is own message ────────────────────────────────────────────
function isOwnMessage(el) {
  const html = el.outerHTML || '';
  if (IS_MEET) {
    return (
      el.getAttribute('aria-label')?.toLowerCase().includes('you:') ||
      el.closest('[data-self-message]') !== null ||
      html.includes('self-message') ||
      html.includes('oqomef') ||    // own-message CSS class
      html.includes('Jxufy') ||     // 2025 own message marker
      el.getAttribute('data-sender-type') === 'self'
    );
  }
  if (IS_TEAMS) {
    const msgEl = el.closest('[data-tid="chat-pane-message"]') || el;
    return (
      msgEl.getAttribute('data-is-me-message') === 'true' ||
      msgEl.classList.contains('fui-ChatMyMessage')
    );
  }
  return false;
}

// ── Process a batch of texts ─────────────────────────────────────────────────
function processTexts(texts) {
  const seen = new Set();
  for (const t of texts) {
    if (!seen.has(t)) {
      seen.add(t);
      sendToBackend(t);
    }
  }
}

// ── MutationObserver — watch the entire document for chat additions ──────────
const observer = new MutationObserver(mutations => {
  for (const mutation of mutations) {
    for (const node of mutation.addedNodes) {
      if (node.nodeType !== 1) continue;

      // Skip script/style/head/nav additions
      const tag = node.tagName?.toLowerCase();
      if (['script','style','head','meta','link','noscript'].includes(tag)) continue;

      const text = nodeText(node);
      if (text.length < MIN_LEN || text.length > MAX_LEN) continue;

      // Is this node inside the chat panel?
      const panel = findChatPanel();
      if (panel && panel.contains(node)) {
        sendToBackend(text);
        continue;
      }

      // Does the node itself look like a chat message?
      const html = node.outerHTML || '';
      const isMsgNode =
        node.hasAttribute('data-message-id') ||
        MEET_JSNAMES_MSG.has(node.getAttribute('jsname')) ||
        IS_TEAMS && (
          node.hasAttribute('data-tid') ||
          node.classList.contains('fui-ChatMessage')
        );

      if (isMsgNode && !isOwnMessage(node)) {
        sendToBackend(text);
      }
    }
  }
});

const MEET_JSNAMES_MSG = new Set(['r4nke', 'YPqjbf', 'MmEJAf', 'EjRKef', 'B6zTjf']);

observer.observe(document.body, { childList: true, subtree: true });

// ── Scan existing messages on load ───────────────────────────────────────────
function scanExisting() {
  console.log('[IVA] Scanning existing chat messages...');

  // Try panel-based extraction first
  const panel = findChatPanel();
  let texts = [];

  if (panel) {
    console.log('[IVA] Chat panel found:', panel.tagName, panel.className?.slice(0, 40));
    texts = extractTextsFromPanel(panel);
  }

  // Fall back to full-page scan
  if (texts.length === 0) {
    console.log('[IVA] Panel scan empty, trying full-page scan...');
    texts = fullPageScan();
  }

  console.log(`[IVA] Scan found ${texts.length} message(s)`);
  processTexts(texts);
}

// Wait for page to be fully interactive before scanning
if (document.readyState === 'complete') {
  setTimeout(scanExisting, 2500);
} else {
  window.addEventListener('load', () => setTimeout(scanExisting, 2500));
}

// Re-scan after 8s in case chat panel loads late (Meet lazy-loads chat)
setTimeout(() => {
  _chatPanel = null;  // reset cached panel to force re-detection
  scanExisting();
}, 8000);

// ── Message listener — popup "Scan Chat Now" button ─────────────────────────
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.action === 'scan_chat') {
    _chatPanel = null;  // force re-detect
    const panel = findChatPanel();
    let texts = panel ? extractTextsFromPanel(panel) : fullPageScan();
    processTexts(texts);
    console.log(`[IVA] Manual scan: ${texts.length} message(s) found`);
    sendResponse({ count: texts.length, platform: PLATFORM });
    return true;
  }

  if (msg.action === 'debug_info') {
    const panel = findChatPanel();
    sendResponse({
      platform: PLATFORM,
      panelFound: !!panel,
      panelTag: panel?.tagName,
      panelClass: panel?.className?.slice(0, 60),
      url: location.href.slice(0, 60),
    });
    return true;
  }
});

console.log(`[IVA] Chat monitor active on ${PLATFORM} — ${host}`);

} // end if IS_MEET || IS_TEAMS
