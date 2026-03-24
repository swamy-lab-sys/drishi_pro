/**
 * meeting_captions.js — Drishi meeting caption capture
 *
 * Injected into Google Meet, Zoom, Teams pages.
 * Two-phase MutationObserver (performance-safe):
 *   Phase 1 — broad scan of document to find the caption container
 *   Phase 2 — narrow observer on that container only (characterData)
 *
 * Captured text is debounced 800ms then sent via background.js to /api/cc_question.
 */

(() => {
  // ── Guard: run once per page ───────────────────────────────────────────────
  if (window.__drishiCaptionsActive) return;
  window.__drishiCaptionsActive = true;

  // ── Platform selectors ────────────────────────────────────────────────────
  const PLATFORMS = [
    {
      name: 'google-meet',
      test: () => location.hostname === 'meet.google.com',
      // Several caption containers used across Meet versions
      selectors: ['.a4cQT', '.oQ3bgc', '.iOzk7', '[jsname="tgaKEf"]',
                  '[data-message-text]', '.CNusmb', '.zs7s8d'],
    },
    {
      name: 'zoom',
      test: () => location.hostname.includes('zoom.us') || location.hostname.includes('zoom.com'),
      selectors: ['#live-transcription-subtitle', '.subtitle-container',
                  '.captions-box__text', '[class*="caption"]'],
    },
    {
      name: 'teams',
      test: () => location.hostname.includes('teams.microsoft.com') ||
                  location.hostname === 'teams.live.com',
      selectors: ['[data-tid="closed-captions-renderer"]',
                  '.fui-ChatMessageBody', '[class*="caption-text"]',
                  '[class*="transcript"]'],
    },
  ];

  const platform = PLATFORMS.find(p => p.test());
  if (!platform) return;

  // ── State ─────────────────────────────────────────────────────────────────
  let screenSharing = false;
  let debounceTimer = null;
  let lastSent = '';
  let phase2Observer = null;
  let phase1Observer = null;
  let captionContainer = null;

  // ── Screen share state listener ───────────────────────────────────────────
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type === 'drishi_screen_share_state') {
      screenSharing = msg.active;
    }
  });

  // ── Text extraction ───────────────────────────────────────────────────────
  function extractText(node) {
    return (node.textContent || node.innerText || '').trim();
  }

  function isOwnMessage(text) {
    return /^(you|me)\s*:/i.test(text);
  }

  function isWorthSending(text) {
    if (!text || text.length < 10) return false;
    const words = text.split(/\s+/).filter(Boolean);
    if (words.length < 4) return false;
    if (isOwnMessage(text)) return false;
    return true;
  }

  // ── Send via background proxy ─────────────────────────────────────────────
  function sendCaption(text) {
    if (screenSharing) return;
    if (text === lastSent) return;
    if (!isWorthSending(text)) return;

    lastSent = text;
    chrome.runtime.sendMessage({
      type: 'SOLVE_CHAT_PROXY',
      payload: { question: text, source: 'meeting_caption' },
    }).catch(() => {});
  }

  function scheduleDebounce(text) {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => sendCaption(text), 800);
  }

  // ── Phase 2: narrow observer on caption container ─────────────────────────
  function attachPhase2(container) {
    if (phase2Observer) return; // already watching
    captionContainer = container;

    phase2Observer = new MutationObserver(() => {
      scheduleDebounce(extractText(captionContainer));
    });

    phase2Observer.observe(container, {
      childList: true,
      subtree: true,
      characterData: true,
    });

    // Disconnect the broad phase-1 scanner
    if (phase1Observer) {
      phase1Observer.disconnect();
      phase1Observer = null;
    }
  }

  // ── Phase 1: broad scan to find caption container ─────────────────────────
  function findContainer() {
    for (const sel of platform.selectors) {
      const el = document.querySelector(sel);
      if (el) return el;
    }
    return null;
  }

  function startPhase1() {
    // Try immediately first
    const found = findContainer();
    if (found) { attachPhase2(found); return; }

    // Otherwise watch document for DOM additions (childList only — cheap)
    phase1Observer = new MutationObserver(() => {
      const el = findContainer();
      if (el) attachPhase2(el);
    });

    phase1Observer.observe(document.body || document.documentElement, {
      childList: true,
      subtree: true,
    });
  }

  // ── Boot ──────────────────────────────────────────────────────────────────
  if (document.body) {
    startPhase1();
  } else {
    document.addEventListener('DOMContentLoaded', startPhase1, { once: true });
  }
})();
