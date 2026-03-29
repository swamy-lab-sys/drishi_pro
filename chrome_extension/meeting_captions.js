/**
 * meeting_captions.js — Drishi meeting caption capture
 *
 * Key design:
 * - Watch all candidate elements simultaneously
 * - Track text delta per element — only process NEWLY ADDED text (fixes iOzk7 accumulation)
 * - Cache skipped elements — avoid re-evaluating buttons on every DOM mutation
 * - Clean speaker names and UI artifacts before sending
 */

(() => {
  if (window.__drishiCaptionsActive) return;
  window.__drishiCaptionsActive = true;

  // ── Platform config ───────────────────────────────────────────────────────
  const PLATFORMS = [
    {
      name: 'google-meet',
      test: () => location.hostname === 'meet.google.com',
      selectors: [
        '[aria-live="polite"][aria-atomic="false"]',
        '[aria-live="polite"]',
        '[aria-live="assertive"]',
        '.a4cQT', '.oQ3bgc', '.iOzk7',
        '[data-message-text]', '.CNusmb', '.zs7s8d', '.TBMuR',
      ],
      rejectClasses: ['Sh4xSc', 'P9KVBf'],
    },
    {
      name: 'zoom',
      test: () => location.hostname.includes('zoom.us') || location.hostname.includes('zoom.com'),
      selectors: ['#live-transcription-subtitle', '.subtitle-container',
                  '.captions-box__text', '[class*="caption"]'],
      rejectClasses: [],
    },
    {
      name: 'teams',
      test: () => location.hostname.includes('teams.microsoft.com') ||
                  location.hostname === 'teams.live.com',
      selectors: ['[data-tid="closed-captions-renderer"]',
                  '.fui-ChatMessageBody', '[class*="caption-text"]',
                  '[class*="transcript"]'],
      rejectClasses: [],
    },
  ];

  const platform = PLATFORMS.find(p => p.test());
  if (!platform) return;

  // ── Remote log ────────────────────────────────────────────────────────────
  function extLog(msg, level = 'info') {
    console.log(`[CC] ${msg}`);
    chrome.runtime.sendMessage({ type: 'EXT_LOG', source: 'captions', msg, level }).catch(() => {});
  }

  // ── State ─────────────────────────────────────────────────────────────────
  let screenSharing = false;
  let debounceTimer = null;
  let lastSent = '';
  let broadObserver = null;
  const watchedEls  = new Map();  // el → MutationObserver
  const skippedEls  = new Set();  // elements already evaluated and rejected — don't re-log
  const prevTextMap = new Map();  // el → last text we processed (for delta extraction)

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type === 'drishi_screen_share_state') screenSharing = msg.active;
  });

  // ── Text cleaning ─────────────────────────────────────────────────────────
  function extractText(node) {
    return (node.textContent || node.innerText || '').trim();
  }

  function cleanCaptionText(text) {
    // Remove scroll-to-bottom button text
    text = text.replace(/arrow_downward\s*Jump to bottom\s*/gi, '');
    // Remove Material icon name prefixes
    text = text.replace(/^(mic_none|mic|videocam|volume_up|screen_share|more_vert)\s*/i, '');
    // Strip speaker name: Google Meet runs "Speaker Name" directly into caption text
    // Pattern: title-case name (lowercase end) immediately followed by uppercase start of sentence
    const nameJoin = text.match(/^([A-Z][a-z]+(?: [A-Za-z]+){0,3}[a-z])([A-Z])/);
    if (nameJoin) text = text.slice(nameJoin[1].length);
    return text.trim();
  }

  // ── Delta extraction — only return text ADDED since last check ────────────
  function extractDelta(el) {
    const current = extractText(el);
    const prev    = prevTextMap.get(el) || '';
    prevTextMap.set(el, current);

    if (!prev) return current;  // first time — return all
    if (current === prev) return '';  // unchanged

    if (current.startsWith(prev)) {
      // Text was appended — return only the new portion
      return current.slice(prev.length).trim();
    }
    // Text changed completely (new utterance) — return the full new text
    return current;
  }

  // ── UI / garbage detection ────────────────────────────────────────────────
  function isGoogleMeetUIText(text) {
    if (!text) return false;
    if (/^language[A-Z]/.test(text)) return true;
    if ((text.match(/BETA/g) || []).length >= 2) return true;
    if (/\([A-Z][a-z]+ ?[A-Za-z]*\)BETA/.test(text)) return true;
    if (/Click New meeting|Get a link you can share|Plan ahead|Your meeting is safe|Join with a code/i.test(text)) return true;
    if (/^(mic_none|videocam|screen_share|more_vert|arrow_downward|Jump to bottom|Default)$/i.test(text.trim())) return true;
    if (/^You have joined the call\b/i.test(text.trim())) return true;
    const sentences = text.split(/[.!?]+/).map(s => s.trim()).filter(Boolean);
    const STATUS_RE = /^Your (camera|microphone) is (on|off)$|^You (have joined|are now|left) the call/i;
    if (sentences.length > 0 && sentences.every(s => STATUS_RE.test(s))) return true;
    return false;
  }

  function isWorthSending(text) {
    if (!text || text.length < 10) return false;
    if (text.split(/\s+/).filter(Boolean).length < 4) return false;
    if (/^(you|me)\s*:/i.test(text)) return false;
    if (isGoogleMeetUIText(text)) return false;
    if (/^(mic_none|videocam|Default|HP True Vision|arrow_downward)/i.test(text)) return false;
    return true;
  }

  // ── Element validation ────────────────────────────────────────────────────
  const SKIP_TAGS = new Set(['BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'A', 'IMG', 'SVG', 'HEADER', 'NAV', 'FOOTER']);

  function isRejectClass(el) {
    const cls = el.className || '';
    return (platform.rejectClasses || []).some(c => cls.includes(c));
  }

  function isValidCaptionEl(el) {
    if (!el) return false;
    if (SKIP_TAGS.has(el.tagName)) return false;
    if (isRejectClass(el)) return false;
    const text = extractText(el);
    if (text.length > 800) return false;
    if (isGoogleMeetUIText(text)) return false;
    return true;
  }

  // ── Watch / unwatch ───────────────────────────────────────────────────────
  function watchElement(el) {
    if (watchedEls.has(el) || skippedEls.has(el)) return;

    if (!isValidCaptionEl(el)) {
      skippedEls.add(el);  // cache — don't re-evaluate on next mutation
      return;
    }

    const ariaLive = el.getAttribute('aria-live') || '';
    extLog(`WATCH ${el.tagName}.${String(el.className).slice(0, 25)} aria-live="${ariaLive}" text="${extractText(el).slice(0, 40)}"`);

    const obs = new MutationObserver(onMutation);
    obs.observe(el, { childList: true, subtree: true, characterData: true });
    watchedEls.set(el, obs);
  }

  function unwatchElement(el, reason) {
    const obs = watchedEls.get(el);
    if (obs) { obs.disconnect(); watchedEls.delete(el); prevTextMap.delete(el); }
    skippedEls.add(el);  // don't re-watch this specific element instance
    extLog(`UNWATCH ${el.tagName}.${String(el.className).slice(0, 25)} | ${reason}`);
  }

  // ── Mutation handler ──────────────────────────────────────────────────────
  function onMutation() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      // Unwatch elements that became garbage (e.g. a4cQT → language list)
      for (const [el] of watchedEls) {
        const t = extractText(el);
        if (t.length > 800 || isGoogleMeetUIText(t)) {
          unwatchElement(el, `garbage: "${t.slice(0, 50)}"`);
        }
      }

      // Extract delta text from each watched element, pick best
      let best = '';
      for (const [el] of watchedEls) {
        const delta   = extractDelta(el);
        const cleaned = cleanCaptionText(delta);
        if (cleaned) extLog(`delta from ${el.tagName}.${String(el.className).slice(0, 20)}: "${cleaned.slice(0, 80)}"`);
        if (isWorthSending(cleaned) && cleaned.length > best.length) best = cleaned;
      }

      if (best) sendCaption(best);
    }, 600);
  }

  // ── Send ──────────────────────────────────────────────────────────────────
  function sendCaption(text) {
    if (screenSharing) return;
    // Dedup: ignore if we already sent this exact cleaned text recently
    if (text === lastSent) return;
    if (!isWorthSending(text)) return;
    lastSent = text;
    extLog(`SEND: "${text.slice(0, 100)}"`);
    chrome.runtime.sendMessage({
      type: 'SOLVE_CHAT_PROXY',
      payload: { question: text, source: 'meeting_caption' },
    }).catch(() => {});
  }

  // ── DOM scanning ──────────────────────────────────────────────────────────
  function scanForContainers() {
    for (const sel of platform.selectors) {
      try { document.querySelectorAll(sel).forEach(watchElement); } catch (_) {}
    }
  }

  function startBroadObserver() {
    if (broadObserver) return;
    // Only trigger scanForContainers on childList changes (element added/removed),
    // NOT characterData — that would re-fire on every text change and spam scanForContainers
    broadObserver = new MutationObserver((mutations) => {
      const hasNodeChange = mutations.some(m => m.type === 'childList');
      if (hasNodeChange) scanForContainers();
    });
    broadObserver.observe(document.body || document.documentElement, {
      childList: true,
      subtree: true,
    });
  }

  // ── Boot ──────────────────────────────────────────────────────────────────
  function boot() {
    if (location.pathname.includes('/landing')) {
      extLog('landing page — watching for meeting navigation');
      const navObs = new MutationObserver(() => {
        if (!location.pathname.includes('/landing')) {
          navObs.disconnect();
          window.__drishiCaptionsActive = false;
          setTimeout(() => { window.__drishiCaptionsActive = false; boot2(); }, 1000);
        }
      });
      navObs.observe(document.body || document.documentElement, { childList: true, subtree: true });
      return;
    }
    boot2();
  }

  function boot2() {
    if (window.__drishiCaptionsActive2) return;
    window.__drishiCaptionsActive2 = true;
    extLog(`active on ${platform.name} | ${location.pathname}`);
    scanForContainers();
    startBroadObserver();
    extLog(`watching ${watchedEls.size} element(s) initially`);
  }

  if (document.body) boot();
  else document.addEventListener('DOMContentLoaded', boot, { once: true });
})();
