(function () {
  'use strict';

  // =========================================
  // DUPLICATE INJECTION GUARD
  // =========================================
  if (window.__programizCodeTyperInjected) {
    return;
  }
  window.__programizCodeTyperInjected = true;

  // ── Ngrok bypass header ───────────────────────────────────────
  const NGROK_HEADERS = { 'ngrok-skip-browser-warning': 'true' };
  let SERVER_URL = 'https://particulate-arely-unrenovative.ngrok-free.dev';

  function apiFetch(url, opts = {}) {
    return fetch(url, { ...opts, headers: { ...NGROK_HEADERS, ...(opts.headers || {}) } });
  }


  // Skip iframes on coding platforms to prevent duplicate typing
  const isTopFrame = window === window.top;
  const isCodingPlatform = /codewars|hackerrank|leetcode|codility|codesignal|programiz/i.test(window.location.href);

  if (isCodingPlatform && !isTopFrame) {
    console.log('[CodeTyper] Skipping iframe on coding platform');
    return;
  }

  const BRIDGE_SOURCE = 'programiz-code-typer-bridge';
  const BRIDGE_TARGET = 'programiz-code-typer-content';
  const LOG = '[CodeTyper]';

  let state = 'IDLE';
  let typewriter = null;
  let lastCodeId = null;
  let bridgeReady = false;
  let bridgeTimedOut = false;
  let pendingBridgeCalls = {};
  let callId = 0;
  let triggerPollInterval = null;
  let editorType = 'unknown';
  let currentWpm = 20; // Default 20 WPM for natural typing
  let retryCount = 0;
  const MAX_RETRIES = 2;
  let isOrphaned = false;

  // Load WPM and SERVER_URL from storage (only once)
  try {
    if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.id) {
      chrome.storage.sync.get({ wpm: 20, serverUrl: 'https://particulate-arely-unrenovative.ngrok-free.dev' }, (data) => {
        if (chrome.runtime.lastError) return;
        currentWpm = data.wpm;
        SERVER_URL = (data.serverUrl || 'https://particulate-arely-unrenovative.ngrok-free.dev').replace(/\/$/, '');
        console.log(LOG, 'WPM loaded:', currentWpm);
        console.log(LOG, 'SERVER_URL loaded:', SERVER_URL);
      });
    }
  } catch (e) { /* Extension context not available */ }

  try {
    if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.onChanged) {
      chrome.storage.onChanged.addListener((changes, area) => {
        if (area === 'sync') {
          if (changes.wpm) {
            currentWpm = changes.wpm.newValue;
            console.log(LOG, 'WPM updated to:', currentWpm);
          }
          if (changes.serverUrl) {
            SERVER_URL = (changes.serverUrl.newValue || 'https://particulate-arely-unrenovative.ngrok-free.dev').replace(/\/$/, '');
            console.log(LOG, 'SERVER_URL updated to:', SERVER_URL);
          }
        }
      });
    }
  } catch (e) { /* Extension context not available */ }

  function checkOrphaned() {
    try {
      // Test if extension context is valid by accessing chrome.runtime.id
      if (chrome.runtime && chrome.runtime.id) {
        return false; // Not orphaned
      }
    } catch (e) {
      // Accessing chrome.runtime threw - extension is orphaned
    }

    // Only log once and shutdown gracefully
    if (!isOrphaned) {
      console.log(LOG, 'Extension context changed. Will reconnect on page refresh.');
      isOrphaned = true;
      if (triggerPollInterval) {
        clearInterval(triggerPollInterval);
        triggerPollInterval = null;
      }
      goIdle();
      showPageNotification('⟳ Extension reloaded — please refresh this tab to re-enable #N typing', 'warn', 6000);
    }
    return true;
  }

  // ── Floating page notification (never types into editor) ────────────────────
  function showPageNotification(msg, type = 'info', duration = 3500) {
    // Remove existing
    const old = document.getElementById('__iva_notify__');
    if (old) old.remove();

    const colors = {
      info: { bg: '#1e3a5f', border: '#3b82f6', text: '#93c5fd' },
      ok: { bg: '#14532d', border: '#22c55e', text: '#86efac' },
      warn: { bg: '#78350f', border: '#f59e0b', text: '#fcd34d' },
      error: { bg: '#7f1d1d', border: '#ef4444', text: '#fca5a5' },
    };
    const c = colors[type] || colors.info;

    const el = document.createElement('div');
    el.id = '__iva_notify__';
    el.style.cssText = `
      position: fixed; top: 18px; right: 18px; z-index: 2147483647;
      background: ${c.bg}; border: 1.5px solid ${c.border}; color: ${c.text};
      padding: 9px 16px; border-radius: 8px; font-size: 13px; font-weight: 600;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      max-width: 360px; box-shadow: 0 4px 20px rgba(0,0,0,0.5);
      pointer-events: none; transition: opacity 0.3s;
    `;
    el.textContent = msg;
    document.body.appendChild(el);

    const hide = setTimeout(() => {
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 350);
    }, duration);

    // Make it click-away if needed (re-enable pointer-events)
    el.style.pointerEvents = 'auto';
    el.addEventListener('click', () => {
      clearTimeout(hide);
      el.remove();
    });
  }

  function setState(newState) {
    if (checkOrphaned()) return;
    const old = state;
    state = newState;
    console.log(LOG, `${old} -> ${newState}`);
    try {
      if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.id) {
        chrome.runtime.sendMessage({ type: 'stateChange', state: newState }, () => {
          // Ignore lastError - expected when extension reloads
          void chrome.runtime.lastError;
        });
      }
    } catch (e) { /* Extension context not available */ }
  }

  function directGetContent() {
    // 1. CodeMirror 6 (modern)
    const cmContent = document.querySelector('.cm-content');
    if (cmContent) return cmContent.innerText;

    // 2. CodeMirror 5 (Codewars uses this)
    const cm5 = document.querySelector('.CodeMirror');
    if (cm5 && cm5.CodeMirror) {
      return cm5.CodeMirror.getValue();
    }
    // Fallback: get text from CodeMirror lines
    const cmLines = document.querySelectorAll('.CodeMirror-line');
    if (cmLines.length > 0) {
      return Array.from(cmLines).map(l => l.textContent).join('\n');
    }

    // 3. Monaco editor (HackerRank, etc)
    const viewLines = document.querySelectorAll('.view-line');
    if (viewLines.length > 0) {
      return Array.from(viewLines).map(l => l.textContent).join('\n');
    }

    // 4. Ace editor
    const aceLines = document.querySelectorAll('.ace_line');
    if (aceLines.length > 0) {
      return Array.from(aceLines).map(l => l.textContent).join('\n');
    }

    // 5. Codewars specific - #code textarea
    const codewarsTA = document.querySelector('#code');
    if (codewarsTA && codewarsTA.value) {
      return codewarsTA.value;
    }

    // 6. Textarea fallback
    const ta = document.querySelector('textarea.monaco-mouse-cursor-text') ||
      document.querySelector('.monaco-editor textarea') ||
      document.querySelector('textarea');

    if (ta) {
      if (window !== window.top) {
        const isEditor = ta.classList.contains('monaco-mouse-cursor-text') ||
          ta.closest('.monaco-editor') ||
          ta.closest('.CodeMirror') ||
          ta.closest('.ace_editor');
        if (!isEditor) return null;
      }
      return ta.value || "";
    }

    // 7. Generic ContentEditable
    const ce = document.querySelector('[contenteditable="true"][role="textbox"]');
    if (ce) return ce.innerText || "";

    return window === window.top ? "" : null;
  }

  function directClear() {
    console.log(LOG, 'Attempting to clear editor...');

    // 0. Monaco Editor - Try API first (HackerRank)
    if (typeof monaco !== 'undefined' && monaco.editor) {
      try {
        const editors = monaco.editor.getEditors ? monaco.editor.getEditors() : [];
        if (editors.length > 0) {
          const editor = editors[0];
          const model = editor.getModel();
          if (model) {
            editor.setValue('');
            console.log(LOG, 'Editor cleared (Monaco API)');
            return true;
          }
        }
      } catch (e) {
        console.warn(LOG, 'Monaco direct clear failed:', e);
      }
    }

    // 1. Target by common editor classes/roles
    const editor = document.querySelector('.monaco-editor, .cm-editor, .CodeMirror, .ace_editor, [role="textbox"][aria-multiline="true"]');
    if (editor) {
      // SAFETY: Do NOT use execCommand on Monaco if we don't have the API. It breaks the editor state.
      // Instead, use direct textarea manipulation as a fallback.
      if (editor.classList.contains('monaco-editor') || editor.closest('.monaco-editor')) {
        console.warn(LOG, 'Using SAFE direct clear on Monaco (textarea override)');
        const ta = editor.querySelector('textarea');
        if (ta) {
          ta.value = '';
          ta.dispatchEvent(new Event('input', { bubbles: true }));
          return true;
        }
        return false;
      }

      editor.focus();
      const ta = editor.querySelector('textarea') || editor;
      try {
        if (ta.focus) ta.focus();
        document.execCommand('selectAll');
        document.execCommand('delete');

        // Final wipe
        if (ta.innerText && ta.innerText.trim()) ta.innerText = '';
        if (ta.value) ta.value = '';

        console.log(LOG, 'Editor cleared (Class/Role Match)');
        return true;
      } catch (e) { }
    }

    const cmContent = document.querySelector('.cm-content');
    if (cmContent) {
      cmContent.focus();
      document.execCommand('selectAll');
      document.execCommand('delete');
      if (cmContent.innerText && cmContent.innerText.trim()) {
        cmContent.innerText = '';
      }
      console.log(LOG, 'Editor cleared (cm-content)');
      return true;
    }

    const ce = document.querySelector('[contenteditable="true"], [role="textbox"]');
    if (ce) {
      ce.focus();
      document.execCommand('selectAll');
      document.execCommand('delete');
      if (ce.innerText && ce.innerText.trim()) {
        ce.innerText = '';
      }
      console.log(LOG, 'Editor cleared (contenteditable)');
      return true;
    }

    const ta = document.querySelector('textarea.monaco-mouse-cursor-text') ||
      document.querySelector('.monaco-editor textarea') ||
      document.querySelector('textarea');
    if (ta) {
      ta.focus();
      document.execCommand('selectAll');
      document.execCommand('delete');
      ta.value = '';
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      console.log(LOG, 'Editor cleared (textarea/fallback)');
      return true;
    }

    console.warn(LOG, 'Could not find editor to clear!');
    return false;
  }

  function directInsertChar(char) {
    // HackerRank / Monaco Editor - MUST use Monaco API, execCommand breaks it
    if (typeof monaco !== 'undefined' && monaco.editor) {
      try {
        const editors = monaco.editor.getEditors ? monaco.editor.getEditors() : [];
        if (editors.length > 0) {
          const editor = editors[0];
          const model = editor.getModel();
          if (model) {
            const pos = model.getFullModelRange().getEndPosition();
            const range = new monaco.Range(pos.lineNumber, pos.column, pos.lineNumber, pos.column);
            const op = { range: range, text: char, forceMoveMarkers: true };
            model.pushEditOperations([], [op], () => null);
            editor.setPosition(model.getFullModelRange().getEndPosition());
            return true;
          }
        }
      } catch (e) {
        console.warn(LOG, 'Monaco direct insert failed:', e);
      }
    }

    // CodeMirror 6
    const cmContent = document.querySelector('.cm-content');
    if (cmContent) {
      cmContent.focus();
      // Handle newlines for contenteditable
      if (char === '\n') {
        document.execCommand('insertParagraph');
      } else {
        document.execCommand('insertText', false, char);
      }
      return true;
    }

    // Generic ContentEditable
    const ce = document.querySelector('[contenteditable="true"], [role="textbox"]');
    if (ce) {
      ce.focus();
      if (char === '\n') {
        document.execCommand('insertParagraph'); // Fallback for enter
      } else {
        document.execCommand('insertText', false, char);
      }
      return true;
    }

    const ta = document.querySelector('textarea');
    if (ta) {
      ta.focus();
      const nativeSetter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
      nativeSetter.call(ta, ta.value + char);
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      return true;
    }
    return false;
  }

  function injectBridge() {
    const script = document.createElement('script');
    script.src = chrome.runtime.getURL('page_bridge.js');
    script.onload = () => {
      console.log(LOG, 'Bridge script loaded via src');
      script.remove();
    };
    script.onerror = () => {
      console.warn(LOG, 'Bridge script blocked by CSP, using inline injection');
      script.remove();
      injectBridgeInline();
    };
    (document.head || document.documentElement).appendChild(script);
  }

  function injectBridgeInline() {
    fetch(chrome.runtime.getURL('page_bridge.js'))
      .then(r => r.text())
      .then(code => {
        const script = document.createElement('script');
        script.textContent = code;
        (document.head || document.documentElement).appendChild(script);
        script.remove();
        console.log(LOG, 'Bridge injected inline');
      })
      .catch(err => {
        console.warn(LOG, 'Inline injection also failed:', err);
        bridgeTimedOut = true;
      });
  }

  window.addEventListener('message', function (event) {
    if (event.source !== window) return;
    if (!event.data || event.data.source !== BRIDGE_SOURCE) return;

    if (event.data.ready) {
      const newType = event.data.editorType || 'unknown';

      // If we already have a real editor, don't let an "unknown" one overwrite it
      if (bridgeReady && editorType !== 'unknown' && newType === 'unknown') return;

      bridgeReady = true;
      editorType = newType;
      console.log(LOG, 'Bridge ready, editor:', editorType);
      return;
    }
    const cb = pendingBridgeCalls[event.data.id];
    if (cb) {
      delete pendingBridgeCalls[event.data.id];
      cb(event.data);
    }
  });

  function bridgeCall(command, payload) {
    return new Promise((resolve) => {
      const id = ++callId;
      pendingBridgeCalls[id] = resolve;
      let hasResponse = false;
      window.postMessage({ source: BRIDGE_TARGET, command, payload, id }, '*');
      const timeoutTimer = setTimeout(() => {
        if (!hasResponse) {
          hasResponse = true;
          delete pendingBridgeCalls[id]; // Ensure it's cleaned up
          resolve({ success: false, error: 'Timeout' });
        }
      }, 5000);
      // Override the resolve function to clear the timeout and set hasResponse
      const originalResolve = resolve;
      resolve = (value) => {
        clearTimeout(timeoutTimer);
        hasResponse = true;
        originalResolve(value);
      };
      pendingBridgeCalls[id] = resolve; // Update with the new resolve
    });
  }

  // LISTEN FOR POPUP COMMANDS
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === 'EXTENSION_COMMAND') {
      console.log(LOG, 'Received popup command:', message.command);
      switch (message.command) {
        case 'start-solving':
          // Check context
          if (isInChatContext()) goSolveModeChat();
          else goSolveMode();
          break;
        case 'trigger-pause':
          triggerPause();
          break;
        case 'trigger-stop':
          triggerStop();
          break;
      }
      sendResponse({ status: 'ok' });
    }
  });

  async function getEditorContent() {
    try {
      if (bridgeReady) {
        const resp = await bridgeCall('getContent');
        if (resp && resp.success) return resp.result || "";
      }
      return await directGetContent() || "";
    } catch (e) {
      console.warn(LOG, 'Failed to get editor content:', e);
      return "";
    }
  }

  async function clearEditor() {
    let success = false;
    if (bridgeReady) {
      const resp = await bridgeCall('clear');
      if (resp.success) success = true;
    }

    // VERIFY clear worked (HackerRank safeguard)
    try {
      await new Promise(r => setTimeout(r, 500)); // Let UI catch up (async Monaco actions)
      const currentContent = await getEditorContent();

      // If content > 5 chars (e.g. '##start' is 7), force retry
      if (currentContent && currentContent.length > 5) {
        console.warn(LOG, `Bridge clear failed (len=${currentContent.length}), forcing direct clear...`);
        const result = await directClear();
        return result;
      }
    } catch (e) { }

    return success || directClear();
  }

  async function insertCharToEditor(char) {
    if (bridgeReady) {
      const resp = await bridgeCall('insertChar', char);
      if (resp.success) return true;
    }
    return directInsertChar(char);
  }

  async function setEditorContentDirect(text) {
    console.log(LOG, 'Setting editor content via fallback (direct DOM):', text.substring(0, 50));

    // CodeMirror 6
    const cmContent = document.querySelector('.cm-content');
    if (cmContent) {
      cmContent.focus();
      // Use execCommand for safer editing
      document.execCommand('selectAll');
      document.execCommand('insertText', false, text);
      return true;
    }

    const ce = document.querySelector('[contenteditable="true"], [role="textbox"]');
    if (ce) {
      ce.focus();
      document.execCommand('selectAll');
      document.execCommand('insertText', false, text);
      return true;
    }

    const ta = document.querySelector('textarea');
    if (ta) {
      ta.value = text;
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      return true;
    }
    return false;
  }

  function goIdle() {
    if (typewriter) {
      typewriter.abort();
      typewriter = null;
    }
    lastCodeId = null;
    setState('IDLE');
  }

  async function goArmed(codeIndex) {
    if (typewriter) {
      typewriter.abort();
      typewriter = null;
    }
    await clearEditor();
    setState('ARMED');
    try {
      chrome.runtime.sendMessage({ type: 'startPolling' });
      chrome.runtime.sendMessage({ type: 'requestCode', index: codeIndex });
    } catch (e) { }
    console.log(LOG, 'Requesting code #' + codeIndex);
  }

  function clickRunButton() {
    const selectors = [
      'button[data-cy="run-btn"]',
      'button.run-btn',
      'button[title="Run"]',
      'button[aria-label="Run"]',
      '.editor-run button',
      '.run-button',
    ];
    for (const sel of selectors) {
      const btn = document.querySelector(sel);
      if (btn) {
        console.log(LOG, 'Clicking Run button:', sel);
        btn.click();
        return true;
      }
    }
    const buttons = document.querySelectorAll('button');
    for (const btn of buttons) {
      if (btn.textContent.trim() === 'Run' || btn.textContent.trim() === 'Run Code') {
        console.log(LOG, 'Clicking Run button by text');
        btn.click();
        return true;
      }
    }
    console.warn(LOG, 'Run button not found');
    return false;
  }

  async function startTyping(payload) {
    if (state !== 'ARMED') return;

    lastCodeId = payload.code_id;
    setState('TYPING');

    typewriter = new Typewriter(currentWpm);
    await clearEditor();

    console.log(LOG, 'Typing code #' + payload.index, '-', payload.lines.length, 'lines at', currentWpm, 'WPM');

    const completed = await typewriter.type(
      payload.lines,
      insertCharToEditor,
      null
    );

    if (completed) {
      console.log(LOG, 'Typing complete, auto-executing...');
      setTimeout(() => clickRunButton(), 500);
      setState('IDLE');
    }
  }

  function filterNoiseFromText(text) {
    // Remove common UI noise patterns
    const noisePatterns = [
      /Sign up|Sign in|Log in|Log out|Register|Create account/gi,
      /Submit|Run Code|Test|Reset|Copy|Share/gi,
      /Copyright|©|All rights reserved/gi,
      /Cookie|Privacy Policy|Terms of Service/gi,
      /Advertisement|Sponsored|Promo/gi,
      /Loading\.\.\.|Please wait/gi,
      /★|⭐|Rating|Difficulty:/gi,
      /\d+ (views|submissions|accepted|likes)/gi,
    ];

    let cleaned = text;
    for (const pattern of noisePatterns) {
      cleaned = cleaned.replace(pattern, '');
    }

    // Collapse multiple newlines
    cleaned = cleaned.replace(/\n{3,}/g, '\n\n');

    return cleaned.trim();
  }

  function extractProblemText() {
    // 1. Try common specific selectors for platforms
    const specificSelectors = [
      // LeetCode
      '.content__u3I1',
      '[data-track-load="description_content"]',
      '.question-content',

      // HackerRank
      '.challenge_problem_statement_body',
      '.challenge_input_format_body',
      '.challenge_constraints_body',
      '.problem-statement-container',
      '.challenge-body-html',

      // Codewars
      '#description',
      '.description',
      '.kata-description',
      '[class*="kata-description"]',

      // Codility
      '.task-description',
      '.brinza-task-description',
      '[data-testid="task-description"]',

      // CodeSignal
      '.task-description',
      '.markdown-body',
      '[class*="TaskDescription"]',
      '[class*="task-description"]',

      // Generic
      '[class*="question-content"]',
      '[class*="problem-description"]',
      '.problem-statement',
      '#problem_statement'
    ];

    // Try to find a good container
    for (const sel of specificSelectors) {
      const el = document.querySelector(sel);
      if (el && el.innerText.length > 200) {
        return filterNoiseFromText(el.innerText.slice(0, 10000));
      }
    }

    // 2. Heuristic: Look for large text containers
    const candidates = ['[class*="description"]', '[class*="problem"]', '#content', 'article', 'main', '.ace_content'];

    // Try to find a good container
    for (const sel of candidates) {
      const el = document.querySelector(sel);
      if (el && el.innerText.length > 200) {
        return filterNoiseFromText(el.innerText.slice(0, 10000));
      }
    }

    // 3. DOM Heuristic: Find largest visible text block or scrollable problem container
    const allContainers = document.querySelectorAll('div, section, article');
    let bestContainer = null;
    let bestScore = 0;

    for (const el of allContainers) {
      // Skip tiny elements, nav, header, footer
      const rect = el.getBoundingClientRect();
      if (rect.width < 200 || rect.height < 100) continue;
      if (el.closest('nav, header, footer, aside, [role="navigation"]')) continue;

      const text = el.innerText || '';
      const textLength = text.length;

      // Prefer scrollable containers (likely problem statement)
      const isScrollable = el.scrollHeight > el.clientHeight;
      const scrollBonus = isScrollable ? 1.5 : 1;

      // Score by text length and scrollability
      const score = textLength * scrollBonus;

      if (score > bestScore && textLength > 200 && textLength < 15000) {
        bestScore = score;
        bestContainer = el;
      }
    }

    if (bestContainer) {
      console.log(LOG, 'Found best container via heuristic:', bestContainer.className);
      return filterNoiseFromText(bestContainer.innerText.slice(0, 10000));
    }

    // 4. Fallback: Clone body, remove noise elements
    const clone = document.body.cloneNode(true);
    const removeSelectors = 'script, style, noscript, svg, img, video, nav, header, footer, aside, [role="navigation"], [role="banner"], [role="contentinfo"], .sidebar, .nav, .header, .footer, .ads, .advertisement, [class*="cookie"], [class*="popup"]';
    const toRemove = clone.querySelectorAll(removeSelectors);
    toRemove.forEach(s => s.remove());

    const bodyText = filterNoiseFromText(clone.innerText.slice(0, 8000));

    if (bodyText.length > 200) return bodyText;

    // 5. LAST RESORT: Use Title
    console.warn(LOG, 'Could not find problem text, using Title:', document.title);
    return "Problem Title: " + document.title + "\n\n(Please solve the standard algorithm problem with this name)";
  }


  // ═══════════════════════════════════════════════
  // GOOGLE MEET CC + CHAT CAPTURE MODE (STEALTH)
  // ═══════════════════════════════════════════════

  let ccCaptureEnabled = false;
  let ccBuffer = '';
  let ccBufferTimeout = null;
  let lastCaptionText = '';
  let captionObserver = null;
  let chatObserver = null;
  let processedCaptions = new Set();
  const CC_SILENCE_GAP_MS = 600;

  function isGoogleMeet() {
    return window.location.href.includes('meet.google.com');
  }

  function detectChatPlatform() {
    const url = window.location.href;
    if (url.includes('meet.google.com')) return 'google-meet';
    if (url.includes('zoom.us')) return 'zoom';
    if (url.includes('teams.microsoft.com')) return 'teams';
    if (url.includes('teams.live.com')) return 'teams';
    return null;
  }

  // ═══════════════════════════════════════════════
  // FORCE CAPTURE (Manual capture on demand)
  // ═══════════════════════════════════════════════

  async function forceCaptureNow() {
    const platform = detectChatPlatform();
    if (!platform) {
      console.warn(LOG, 'Force capture: Not on a supported meeting platform');
      return { captured: false, message: 'Not on Teams/Meet/Zoom' };
    }

    console.log(LOG, '⚡ FORCE CAPTURE initiated on', platform);

    // Enable capture if not already
    if (!ccCaptureEnabled) {
      await startCCapture();
    }

    // Process existing chat messages
    processAllExistingChatMessages();

    // Try to capture current caption text
    const captionContainer = findCaptionContainer();
    let currentCaption = '';
    if (captionContainer) {
      currentCaption = extractCaptionText(captionContainer) || '';
      if (currentCaption && currentCaption !== lastCaptionText) {
        lastCaptionText = currentCaption;
        if (!processedCaptions.has(currentCaption)) {
          processedCaptions.add(currentCaption);
          await sendCapturedQuestion(currentCaption.trim(), platform + '-manual-capture');
          console.log(LOG, 'Captured caption via force:', currentCaption.slice(0, 80));
        }
      }
    }

    // Try Zoom chat
    if (platform === 'zoom') {
      const zoomMsgs = getZoomChatMessages();
      if (zoomMsgs.length > 0) {
        const lastMsg = zoomMsgs[zoomMsgs.length - 1];
        await sendCapturedQuestion(lastMsg.text, platform + '-manual-capture');
        console.log(LOG, 'Captured Zoom chat:', lastMsg.text.slice(0, 80));
      }
    }

    // Try Teams chat
    if (platform === 'teams') {
      const teamsMsgs = getTeamsChatMessages();
      if (teamsMsgs.length > 0) {
        const lastMsg = teamsMsgs[teamsMsgs.length - 1];
        await sendCapturedQuestion(lastMsg.text, platform + '-manual-capture');
        console.log(LOG, 'Captured Teams chat:', lastMsg.text.slice(0, 80));
      }
    }

    return { captured: true, platform, caption: currentCaption };
  }

  // Ctrl+Alt+Q shortcut for force capture
  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.altKey && e.key.toLowerCase() === 'q') {
      e.preventDefault();
      forceCaptureNow().then(result => {
        if (result.captured) {
          showToast('Captured! Check dashboard.');
        } else {
          showToast('Not on a meeting page');
        }
      });
    }
  });

  function showToast(msg) {
    const toast = document.createElement('div');
    toast.style.cssText = 'position:fixed;bottom:20px;right:20px;background:#4f46e5;color:#fff;padding:12px 20px;border-radius:8px;font-family:sans-serif;font-size:14px;z-index:999999;box-shadow:0 4px 12px rgba(0,0,0,0.3);';
    toast.textContent = msg;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
  }

  // ═══════════════════════════════════════════════
  // CC CAPTION CAPTURE
  // ═══════════════════════════════════════════════

  function findCaptionContainer() {
    // Google Meet caption selectors (may change with UI updates)
    const selectors = [
      '[role="region"][aria-label*="Caption"]',
      '[role="region"][aria-label*="caption"]',
      'div[jscontroller="KPn5nb"]',
      '.a4cQT',  // Caption container class
      '[data-message-text]',
      '.TBMuR',  // Caption bubble
      '.iOzk7',  // Caption text
    ];

    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) return el;
    }
    return null;
  }

  function extractCaptionText(container) {
    if (!container) return null;

    // Get all text nodes in caption area
    const textNodes = container.querySelectorAll('span, div');
    let texts = [];

    for (const node of textNodes) {
      const text = (node.innerText || node.textContent || '').trim();
      if (text.length > 2) {
        texts.push(text);
      }
    }

    // Join and deduplicate
    const fullText = texts.join(' ').trim();
    return fullText;
  }

  function isSelfCaption(text) {
    // Detect if caption is from "You" (self)
    const selfIndicators = [
      /^you:/i,
      /^you said/i,
      /^\(you\)/i,
    ];
    for (const pattern of selfIndicators) {
      if (pattern.test(text)) return true;
    }
    return false;
  }

  function isFillerOrNoise(text) {
    const lower = text.toLowerCase().trim();
    const fillers = [
      'okay', 'ok', 'alright', 'right', 'yeah', 'yes', 'no', 'um', 'uh',
      'hmm', 'ah', 'oh', 'so', 'well', 'let me', 'one second', 'one moment',
      'can you hear me', 'hello', 'hi', 'hey',
    ];
    return fillers.some(f => lower === f || lower === f + '.');
  }

  function hasInterrogativeIntent(text) {
    const lower = text.toLowerCase().trim();

    // Ends with question mark
    if (text.endsWith('?')) return true;

    // Starts with interrogative
    const starters = [
      'what', 'why', 'how', 'explain', 'describe', 'tell me', 'walk me',
      'design', 'build', 'deploy', 'configure', 'debug', 'can you',
      'could you', 'have you', 'do you', 'is there', 'are there',
      'when', 'where', 'which',
    ];
    return starters.some(s => lower.startsWith(s));
  }

  function processCaptionBuffer() {
    if (!ccBuffer.trim()) return;

    const question = ccBuffer.trim();
    ccBuffer = '';

    // Skip if too short
    if (question.length < 10) return;

    // Skip filler
    if (isFillerOrNoise(question)) return;

    // Skip self-captions
    if (isSelfCaption(question)) return;

    // Check for interrogative intent
    if (!hasInterrogativeIntent(question) && !question.endsWith('?')) {
      // Only process if it looks like a question
      return;
    }

    // Deduplicate
    const questionHash = hashString(question);
    if (processedCaptions.has(questionHash)) return;
    processedCaptions.add(questionHash);

    console.log(LOG, '📝 CC Question captured:', question.substring(0, 80));

    // Send to server for processing
    sendCapturedQuestion(question, 'cc');
  }

  function onCaptionMutation(mutations) {
    if (!ccCaptureEnabled) return;

    const container = findCaptionContainer();
    if (!container) return;

    const currentText = extractCaptionText(container);
    if (!currentText || currentText === lastCaptionText) return;

    // Detect new text added
    let newText = currentText;
    if (lastCaptionText && currentText.startsWith(lastCaptionText)) {
      newText = currentText.slice(lastCaptionText.length).trim();
    }
    lastCaptionText = currentText;

    if (!newText || newText.length < 2) return;

    // Skip self-captions
    if (isSelfCaption(newText)) return;

    // Add to buffer
    ccBuffer += ' ' + newText;
    ccBuffer = ccBuffer.trim();

    // Clear existing timeout
    if (ccBufferTimeout) clearTimeout(ccBufferTimeout);

    // Check if we should process immediately (ends with ?)
    if (ccBuffer.endsWith('?')) {
      processCaptionBuffer();
      return;
    }

    // Check for strong interrogative at start
    if (hasInterrogativeIntent(ccBuffer) && ccBuffer.split(' ').length >= 5) {
      // Wait for silence gap before processing
      ccBufferTimeout = setTimeout(() => {
        processCaptionBuffer();
      }, CC_SILENCE_GAP_MS);
    } else {
      // Standard silence gap
      ccBufferTimeout = setTimeout(() => {
        processCaptionBuffer();
      }, CC_SILENCE_GAP_MS);
    }
  }

  function startCaptionObserver() {
    if (captionObserver) return;

    // Observe entire body for caption changes (container may appear/disappear)
    captionObserver = new MutationObserver(onCaptionMutation);
    captionObserver.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
    });

    console.log(LOG, '👁️ Caption observer started');
  }

  function stopCaptionObserver() {
    if (captionObserver) {
      captionObserver.disconnect();
      captionObserver = null;
    }
    ccBuffer = '';
    lastCaptionText = '';
    if (ccBufferTimeout) clearTimeout(ccBufferTimeout);
    console.log(LOG, '⏹️ Caption observer stopped');
  }

  // ═══════════════════════════════════════════════
  // MEET CHAT CAPTURE (SECONDARY)
  // ═══════════════════════════════════════════════

  let lastProcessedChatIndex = -1;

  function getMeetChatMessages() {
    // Google Meet chat selectors - Updated 2026
    // Chat messages appear in a side panel with blue pill-style backgrounds

    let messages = [];

    // Method 1: Find all elements that look like chat message content
    // In 2026 Meet, messages have specific styling (blue background pills)
    const allElements = document.querySelectorAll('*');
    const chatCandidates = [];

    for (const el of allElements) {
      const text = (el.textContent || '').trim();
      // Skip if text too short/long
      if (text.length < 5 || text.length > 500) continue;
      // Skip if has many children (it's a container)
      if (el.children.length > 3) continue;
      // Skip common UI elements
      if (text.includes('Send a message') || text.includes('Let participants')) continue;
      if (text.startsWith('Messages won') || text.includes('pin a message')) continue;

      // Check if it's in the chat area (right side panel)
      const rect = el.getBoundingClientRect();
      if (rect.width < 50 || rect.height < 10) continue;
      if (rect.right < window.innerWidth * 0.5) continue; // Must be on right side

      // Check computed style for chat-like elements
      const style = window.getComputedStyle(el);
      const bg = style.backgroundColor;
      const isBlue = bg.includes('33, 150') || bg.includes('0, 137') || bg.includes('25, 103') || bg.includes('rgb(10');

      // Either has blue background OR is in a chat-labeled container
      if (isBlue) {
        chatCandidates.push({ el, text, priority: 1 });
      }
    }

    // Sort by Y position (top to bottom = oldest to newest)
    chatCandidates.sort((a, b) => {
      const rectA = a.el.getBoundingClientRect();
      const rectB = b.el.getBoundingClientRect();
      return rectA.top - rectB.top;
    });

    // Use unique texts only
    const seenTexts = new Set();
    for (const c of chatCandidates) {
      if (!seenTexts.has(c.text)) {
        seenTexts.add(c.text);
        messages.push(c.el);
      }
    }

    // Fallback: Method 2 - Find chat container then get messages
    if (messages.length === 0) {
      const chatContainers = [
        '[aria-label*="chat" i]',
        '[aria-label*="Chat" i]',
        '[data-panel-id="2"]',
      ];

      for (const containerSel of chatContainers) {
        const container = document.querySelector(containerSel);
        if (container) {
          const msgElements = container.querySelectorAll(
            '[data-message-text], [data-sender-id], div[dir="ltr"], div[dir="auto"]'
          );
          const filtered = Array.from(msgElements).filter(el => {
            const text = (el.textContent || '').trim();
            return text.length >= 5 && text.length < 500;
          });
          if (filtered.length > 0) {
            messages = filtered;
            break;
          }
        }
      }
    }

    // Fallback: Method 3 - Direct selectors
    if (messages.length === 0) {
      const directSelectors = [
        '[data-message-text]',
        '[jsname="dTKtvb"]',
        '[jsname="bgckF"]',
        '.GDhqjd',
        '.oIy2qc',
        '[data-message-id]',
      ];

      for (const sel of directSelectors) {
        try {
          const found = document.querySelectorAll(sel);
          const filtered = Array.from(found).filter(el => {
            const text = (el.textContent || '').trim();
            return text.length >= 5 && text.length < 500 && !text.includes('Send a message');
          });
          if (filtered.length > 0) {
            messages = filtered;
            break;
          }
        } catch (e) { /* invalid selector */ }
      }
    }

    console.log(LOG, `getMeetChatMessages found ${messages.length} messages`);
    return messages;
  }

  function isSystemMessage(text) {
    const lower = text.toLowerCase();
    const systemPatterns = [
      'joined', 'left', 'muted', 'unmuted', 'is presenting',
      'started recording', 'stopped recording', 'ended the call',
    ];
    return systemPatterns.some(p => lower.includes(p));
  }

  function isEmojiOnly(text) {
    // Remove emojis and check if anything remains
    const withoutEmoji = text.replace(/[\u{1F000}-\u{1FFFF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]/gu, '').trim();
    return withoutEmoji.length < 3;
  }

  // Process ALL existing chat messages (called when capture starts)
  function processAllExistingChatMessages() {
    const messages = getMeetChatMessages();
    if (messages.length === 0) {
      console.log(LOG, '📭 No existing chat messages found');
      return;
    }

    console.log(LOG, `📬 Processing ${messages.length} existing chat messages...`);

    let processed = 0;
    for (let i = 0; i < messages.length; i++) {
      const msg = messages[i];
      const text = (msg.innerText || msg.textContent || '').trim();

      if (text.length < 10) continue;
      if (isSystemMessage(text)) continue;
      if (isEmojiOnly(text)) continue;
      if (isFillerOrNoise(text)) continue;

      // Check if already processed (dedup by hash)
      const textHash = hashString(text);
      if (processedCaptions.has(textHash)) {
        console.log(LOG, `⏭️ Already answered: "${text.substring(0, 40)}..."`);
        continue;
      }
      processedCaptions.add(textHash);

      console.log(LOG, `💬 Processing existing chat [${i + 1}/${messages.length}]: "${text.substring(0, 60)}..."`);
      sendCapturedQuestion(text, 'chat');
      processed++;

      lastProcessedChatIndex = i;
    }

    console.log(LOG, `✅ Processed ${processed} new chat messages`);
  }

  function checkMeetChat() {
    if (!ccCaptureEnabled) return;

    const messages = getMeetChatMessages();
    if (messages.length === 0) return;

    // Process only new messages
    for (let i = lastProcessedChatIndex + 1; i < messages.length; i++) {
      const msg = messages[i];
      const text = (msg.innerText || msg.textContent || '').trim();

      if (text.length < 10) continue;
      if (isSystemMessage(text)) continue;
      if (isEmojiOnly(text)) continue;
      if (isFillerOrNoise(text)) continue;

      // Deduplicate
      const textHash = hashString(text);
      if (processedCaptions.has(textHash)) continue;
      processedCaptions.add(textHash);

      console.log(LOG, '💬 Meet chat captured:', text.substring(0, 80));
      sendCapturedQuestion(text, 'chat');

      lastProcessedChatIndex = i;
    }
  }

  function startChatObserver() {
    if (chatObserver) return;

    // Poll for new chat messages (more reliable than mutation observer for chat)
    chatObserver = setInterval(checkMeetChat, 1000);
    console.log(LOG, '💬 Chat observer started');
  }

  function stopChatObserver() {
    if (chatObserver) {
      clearInterval(chatObserver);
      chatObserver = null;
    }
    lastProcessedChatIndex = -1;
    console.log(LOG, '⏹️ Chat observer stopped');
  }

  // ═══════════════════════════════════════════════
  // CC/CHAT CAPTURE CONTROLS
  // ═══════════════════════════════════════════════

  async function sendCapturedQuestion(question, source) {
    try {
      const response = await apiFetch(`${SERVER_URL}/api/cc_question`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: question,
          source: source,  // 'cc' or 'chat'
          platform: 'google-meet',
          timestamp: Date.now(),
        })
      });

      if (!response.ok) {
        console.warn(LOG, 'CC question send failed:', response.status);
      }
    } catch (e) {
      console.warn(LOG, 'CC question send error:', e.message);
    }
  }

  function startCCCapture() {
    if (!isGoogleMeet()) {
      console.warn(LOG, 'CC capture only works on Google Meet');
      return;
    }

    ccCaptureEnabled = true;
    processedCaptions.clear();

    // IMPORTANT: Reset index to -1 to process ALL existing messages
    lastProcessedChatIndex = -1;

    // Process all existing chat messages IMMEDIATELY
    processAllExistingChatMessages();

    startCaptionObserver();
    startChatObserver();
    console.log(LOG, '🎙️ CC/Chat capture ENABLED');

    // Notify server
    apiFetch(`${SERVER_URL}/api/cc_control`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'start' })
    }).catch(() => { });
  }

  function stopCCCapture() {
    ccCaptureEnabled = false;
    stopCaptionObserver();
    stopChatObserver();
    ccBuffer = '';
    console.log(LOG, '⏹️ CC/Chat capture DISABLED');

    // Notify server
    apiFetch(`${SERVER_URL}/api/cc_control`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'stop' })
    }).catch(() => { });
  }

  function toggleCCCapture() {
    if (ccCaptureEnabled) {
      stopCCCapture();
    } else {
      startCCCapture();
    }
    return ccCaptureEnabled;
  }

  // ═══════════════════════════════════════════════
  // CC KEYBOARD SHORTCUT (Ctrl+Alt+C)
  // ═══════════════════════════════════════════════

  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.altKey && e.key.toLowerCase() === 'c') {
      e.preventDefault();
      const enabled = toggleCCCapture();
      console.log(LOG, `🎙️ Ctrl+Alt+C: CC Capture ${enabled ? 'ON' : 'OFF'}`);
    }
  });

  // ═══════════════════════════════════════════════
  // DEBUG HELPER (call from console: debugMeetChat())
  // ═══════════════════════════════════════════════

  window.debugMeetChat = function () {
    console.log('=== Google Meet Chat Debug ===');
    console.log('URL:', window.location.href);

    // Check for chat panel
    const chatPanelSelectors = [
      '[aria-label*="chat" i]',
      '[aria-label*="Chat"]',
      '[data-panel-id]',
      '[role="complementary"]',
      'aside',
    ];

    console.log('\n--- Chat Panels ---');
    chatPanelSelectors.forEach(sel => {
      try {
        const found = document.querySelectorAll(sel);
        if (found.length > 0) {
          console.log(`✅ ${sel} → ${found.length} elements`);
          found.forEach((el, i) => {
            const text = el.textContent.substring(0, 100);
            console.log(`   [${i}] text preview: "${text}..."`);
          });
        }
      } catch (e) { }
    });

    // Check for message elements
    const msgSelectors = [
      '[data-message-text]',
      '[data-message-id]',
      '[data-sender-id]',
      '[jsname="dTKtvb"]',
      '[jsname="bgckF"]',
      'div[dir="auto"]',
      'div[dir="ltr"]',
    ];

    console.log('\n--- Message Elements ---');
    msgSelectors.forEach(sel => {
      try {
        const found = document.querySelectorAll(sel);
        const withText = Array.from(found).filter(el => {
          const t = el.textContent.trim();
          return t.length > 5 && t.length < 500;
        });
        if (withText.length > 0) {
          console.log(`✅ ${sel} → ${withText.length} elements with text`);
          withText.slice(-3).forEach((el, i) => {
            console.log(`   [${i}]: "${el.textContent.trim().substring(0, 80)}"`);
          });
        }
      } catch (e) { }
    });

    // Try our actual function
    console.log('\n--- Testing getMeetChatMessages() ---');
    const msgs = getMeetChatMessages();
    console.log(`Found ${msgs.length} messages`);
    msgs.slice(-3).forEach((el, i) => {
      console.log(`   [${i}]: "${(el.textContent || '').trim().substring(0, 80)}"`);
    });

    // Show all text in potential chat area
    console.log('\n--- All visible text in side panels ---');
    const sides = document.querySelectorAll('[data-panel-id], [role="complementary"], aside');
    sides.forEach((panel, pi) => {
      console.log(`Panel ${pi}:`);
      const texts = panel.querySelectorAll('*');
      const unique = new Set();
      texts.forEach(el => {
        const t = el.textContent.trim();
        if (t.length > 10 && t.length < 200 && !unique.has(t)) {
          unique.add(t);
        }
      });
      [...unique].slice(-5).forEach(t => console.log(`   "${t.substring(0, 60)}"`));
    });

    console.log('\n=== End Debug ===');
    console.log('TIP: Make sure chat panel is OPEN and has messages');
    return 'Check console output above';
  };

  // ═══════════════════════════════════════════════
  // PROCTORING / KILL-SWITCH DETECTION
  // ═══════════════════════════════════════════════

  function checkProctoringIndicators() {
    // Kill CC capture if proctoring detected
    const proctoringPatterns = [
      'proctoring', 'monitoring', 'recording', 'screen share',
      'share your screen', 'camera required', 'verify identity',
    ];

    const bodyText = document.body.innerText.toLowerCase();
    for (const pattern of proctoringPatterns) {
      if (bodyText.includes(pattern)) {
        if (ccCaptureEnabled) {
          console.warn(LOG, '⚠️ Proctoring detected, stopping CC capture');
          stopCCCapture();
        }
        return true;
      }
    }
    return false;
  }

  // Check periodically on Meet
  if (isGoogleMeet()) {
    setInterval(checkProctoringIndicators, 5000);
  }

  // Stop on visibility change (tab switch)
  document.addEventListener('visibilitychange', () => {
    if (document.hidden && ccCaptureEnabled) {
      console.log(LOG, '👁️ Tab hidden, pausing CC capture');
      stopCCCapture();
    }
  });

  // ═══════════════════════════════════════════════
  // CHAT MODE DETECTION & CAPTURE (LEGACY)
  // ═══════════════════════════════════════════════


  // Track solved chat messages to avoid duplicates
  const solvedChatMessages = new Set();

  // EXPOSE RESET FUNCTION (call from console: resetChat())
  window.resetChat = function () {
    solvedChatMessages.clear();
    console.log(LOG, '🔄 Chat cache cleared! Press Ctrl+Alt+A to reprocess all messages.');
    return 'Chat cache cleared. Now press Ctrl+Alt+A';
  };

  // Simple hash function for deduplication
  function hashString(str) {
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
      const char = str.charCodeAt(i);
      hash = ((hash << 5) - hash) + char;
      hash = hash & hash; // Convert to 32bit integer
    }
    return hash.toString();
  }

  function getChatMessage() {
    const platform = detectChatPlatform();
    console.log(LOG, 'Platform detected:', platform);

    if (platform === 'google-meet') {
      // Try multiple selectors for Google Meet (UI changes frequently)
      // Updated for 2024-2026 Google Meet UI
      const selectors = [
        // Message content containers
        '[data-message-text]',
        '[jsname="dTKtvb"]',
        '[jsname="bgckF"]',
        // Chat message bubbles
        '.GDhqjd',
        '.oIy2qc',
        '.Zmm6We',
        '.YTbUzc',
        '.Ss4fHf',
        // Generic message containers in chat panel
        '[data-sender-id] [dir="auto"]',
        '[data-self-name] ~ div [dir="auto"]',
        // Fallback: any div with dir=auto inside the chat panel area
        '[aria-label*="chat" i] [dir="auto"]',
        '[aria-label*="message" i] [dir="auto"]',
        '[role="list"] [dir="auto"]',
        '[role="listitem"] [dir="auto"]',
      ];

      let allMessages = [];
      for (const selector of selectors) {
        try {
          const found = document.querySelectorAll(selector);
          // Filter to only elements with actual text content
          const withText = Array.from(found).filter(el => {
            const text = (el.textContent || '').trim();
            return text.length >= 5 && text.length < 2000;
          });
          if (withText.length > 0) {
            console.log(LOG, `✅ Found ${withText.length} messages with selector: ${selector}`);
            allMessages = withText;
            break;
          }
        } catch (e) {
          // Invalid selector, skip
        }
      }

      // Fallback: Find chat panel and get all text blocks
      if (allMessages.length === 0) {
        console.log(LOG, '🔍 Trying fallback: scanning chat panel...');
        const chatPanel = document.querySelector('[aria-label*="chat" i], [aria-label*="Chat" i], [data-panel-id="2"]');
        if (chatPanel) {
          const textBlocks = chatPanel.querySelectorAll('div[dir="auto"], span[dir="auto"]');
          const filtered = Array.from(textBlocks).filter(el => {
            const text = (el.textContent || '').trim();
            // Must be reasonable message length, not UI text
            return text.length >= 10 && text.length < 1000 && !text.includes('Send a message');
          });
          if (filtered.length > 0) {
            console.log(LOG, `✅ Fallback found ${filtered.length} messages`);
            allMessages = filtered;
          }
        }
      }

      if (allMessages.length > 0) {
        // Get the last unsolved message
        let lastText = null;

        // Iterate from most recent to oldest
        for (let i = allMessages.length - 1; i >= 0; i--) {
          const msg = allMessages[i];
          const text = (msg.textContent || msg.innerText).trim();

          if (text.length < 10) continue; // Skip short messages

          const textHash = hashString(text);

          // Skip if already solved
          if (solvedChatMessages.has(textHash)) {
            console.log(LOG, `⏭️  Skipping already solved message (hash: ${textHash})`);
            continue;
          }

          // Found an unsolved message
          lastText = text;
          console.log(LOG, '💬 New unsolved message:', text.substring(0, 100));
          return { text: lastText, hash: textHash };
        }

        if (!lastText) {
          console.warn(LOG, '⚠️  All messages already solved. Send a new question!');
          return null;
        }
      } else {
        console.warn(LOG, '❌ No messages found. Is chat panel open?');
        // Debug: log what we can find
        const chatAreas = document.querySelectorAll('[aria-label*="chat" i], [aria-label*="Chat" i]');
        console.log(LOG, 'Chat areas found:', chatAreas.length);
      }
    }

    if (platform === 'teams') {
      const selectors = [
        '[data-message-content]',           // ✅ CONFIRMED (Fluent UI / Live)
        '.fui-Chat__message__content',      // New UI
        '[data-tid="message-body"]',        // Classic
        '.ui-chat__message__content',       // Personal
        '.message-body-content'
      ];

      for (const selector of selectors) {
        try {
          const found = document.querySelectorAll(selector);
          const withText = Array.from(found).filter(el => {
            const text = (el.textContent || '').trim();
            return text.length >= 10 && text.length < 2000;
          });

          if (withText.length > 0) {
            console.log(LOG, `Teams: Found ${withText.length} messages with ${selector}`);

            // Reverse iteration to find latest unsolved (Limit to last 5 to avoid ancient history)
            const startIndex = withText.length - 1;
            const endIndex = Math.max(0, withText.length - 5);

            for (let i = startIndex; i >= endIndex; i--) {
              const text = withText[i].textContent.trim();
              const textHash = hashString(text);

              if (solvedChatMessages.has(textHash)) continue;

              lastText = text;
              console.log(LOG, '💬 Teams: New unsolved message:', text.substring(0, 50));
              return { text: lastText, hash: textHash };
            }
            break;
          }
        } catch (e) { }
      }
    }

    if (platform === 'zoom') {
      // Zoom chat messages
      const messages = document.querySelectorAll('.chat-message__body, .chat-message-container, [aria-label*="message"]');
      console.log(LOG, `Zoom: Found ${messages.length} messages`);
      if (messages.length > 0) {
        for (let i = messages.length - 1; i >= 0; i--) {
          const text = (messages[i].textContent || messages[i].innerText || '').trim();
          if (text.length < 10) continue;
          const textHash = hashString(text);
          if (solvedChatMessages.has(textHash)) continue;
          return { text, hash: textHash };
        }
      }
    }

    return null;
  }

  function isInChatContext() {
    // Check if we're on a chat platform but NOT in a coding editor
    const platform = detectChatPlatform();
    if (!platform) return false;

    // Check if there's NO coding editor active
    const hasEditor = document.querySelector('.cm-content, .ace_editor, [contenteditable="true"][role="textbox"], textarea[class*="code"]');

    // We're in chat context if we're on a chat platform and no editor is focused
    return platform && !hasEditor;
  }

  let lastChatAttempt = 0;
  const CHAT_COOLDOWN_MS = 3000; // 3 seconds between attempts

  async function goSolveModeChat() {
    // For chat mode, we only use fetch() which doesn't need extension context
    // So skip checkOrphaned() - this allows chat capture to work even after extension reload
    console.log(LOG, 'goSolveModeChat() called');

    console.log(LOG, '═══════════════════════════════════════');
    console.log(LOG, 'CHAT MODE: Capturing ALL questions...');
    console.log(LOG, '═══════════════════════════════════════');

    // Cooldown check
    const now = Date.now();
    if (now - lastChatAttempt < CHAT_COOLDOWN_MS) {
      console.warn(LOG, `⏱️  Cooldown active. Wait ${Math.ceil((CHAT_COOLDOWN_MS - (now - lastChatAttempt)) / 1000)}s`);
      return;
    }
    lastChatAttempt = now;

    const platform = detectChatPlatform();

    // Get ALL chat messages (with retry mechanism)
    let allMessages = [];
    for (let attempt = 1; attempt <= 4; attempt++) {
      allMessages = getAllChatMessages(platform);
      if (allMessages && allMessages.length > 0) break;

      console.log(LOG, `Attempt ${attempt}: No messages found. Retrying in 500ms...`);
      await new Promise(r => setTimeout(r, 500));
    }

    if (!allMessages || allMessages.length === 0) {
      console.warn(LOG, '❌ No chat messages found after 4 attempts (2s). Is chat panel open?');
      setState('IDLE');
      return;
    }

    console.log(LOG, `📱 Chat platform: ${platform}`);
    console.log(LOG, `💬 Found ${allMessages.length} total messages`);

    // Process each unsolved message
    let processedCount = 0;
    for (const msgData of allMessages) {
      const { text: chatMessage, hash: messageHash } = msgData;

      // Skip if already solved
      if (solvedChatMessages.has(messageHash)) {
        console.log(LOG, `⏭️  Already solved: "${chatMessage.substring(0, 40)}..."`);
        continue;
      }

      console.log(LOG, `💬 Processing [${processedCount + 1}]: "${chatMessage.substring(0, 60)}..."`);

      setState('TYPING'); // Block other actions

      try {
        // Use background proxy to avoid "Private Network Access" blocks on Teams
        const data = await new Promise((resolve, reject) => {
          safeSendMessage({
            type: 'SOLVE_CHAT_PROXY',
            payload: {
              question: chatMessage,
              source: 'chat',
              platform: platform,
              timestamp: Date.now(),
            }
          }, (response) => {
            if (chrome.runtime.lastError) {
              reject(new Error(chrome.runtime.lastError.message));
            } else if (response && response.success) {
              resolve(response.data);
            } else {
              reject(new Error(response ? response.error : 'Network/Server Error'));
            }
          });
        });

        if (data.status === 'answered') {
          console.log(LOG, `✅ Answered: "${chatMessage.substring(0, 40)}..."`);
          solvedChatMessages.add(messageHash);
          processedCount++;
        } else if (data.status === 'already_answered') {
          console.log(LOG, `⏭️  Already answered (server): "${chatMessage.substring(0, 40)}..."`);
          solvedChatMessages.add(messageHash);
        } else if (data.status === 'rejected') {
          console.log(LOG, `❌ Rejected: ${data.reason}`);
          solvedChatMessages.add(messageHash); // Don't retry rejected
        }
      } catch (e) {
        console.error(LOG, 'Chat solve failed:', e);
      }
    }

    console.log(LOG, `✅ Processed ${processedCount} new questions`);
    setState('IDLE');
  }

  // Get ALL chat messages (not just the latest)
  function getAllChatMessages(platform) {
    const messages = [];

    if (platform === 'google-meet') {
      const selectors = [
        '[data-message-text]',
        '[jsname="dTKtvb"]',
        '[jsname="bgckF"]',
        '.GDhqjd',
        '.oIy2qc',
        '[aria-label*="chat" i] [dir="auto"]',
        '[role="list"] [dir="auto"]',
      ];

      let allElements = [];
      for (const selector of selectors) {
        try {
          const found = document.querySelectorAll(selector);
          const withText = Array.from(found).filter(el => {
            const text = (el.textContent || '').trim();
            return text.length >= 5 && text.length < 2000;
          });
          if (withText.length > 0) {
            allElements = withText;
            console.log(LOG, `✅ Found ${withText.length} messages with: ${selector}`);
            break;
          }
        } catch (e) { }
      }

      // Fallback: Find chat panel
      if (allElements.length === 0) {
        const chatPanel = document.querySelector('[aria-label*="chat" i], [data-panel-id="2"]');
        if (chatPanel) {
          const textBlocks = chatPanel.querySelectorAll('div[dir="auto"], span[dir="auto"]');
          allElements = Array.from(textBlocks).filter(el => {
            const text = (el.textContent || '').trim();
            return text.length >= 10 && text.length < 1000 && !text.includes('Send a message');
          });
        }
      }

      // Convert to message objects with hash
      for (const el of allElements) {
        const text = (el.textContent || el.innerText || '').trim();
        if (text.length >= 10) {
          const hash = hashString(text);
          messages.push({ text, hash });
        }
      }
    }

    if (platform === 'teams') {
      const selectors = [
        '[data-message-content]',           // ✅ CONFIRMED (Fluent UI / Live)
        '.fui-Chat__message__content',      // New UI
        '[data-tid="message-body"]',        // Classic
        '.ui-chat__message__content',       // Personal
        '.message-body-content'
      ];

      for (const selector of selectors) {
        try {
          const found = document.querySelectorAll(selector);
          const withText = Array.from(found).filter(el => {
            const text = (el.textContent || '').trim();
            return text.length >= 5 && text.length < 2000;
          });
          if (withText.length > 0) {
            console.log(LOG, `Teams: Found ${withText.length} messages with: ${selector}`);
            // Teams loads history top-down. We take them all.
            // Convert to message objects with hash
            for (const el of withText) {
              const text = el.textContent.trim();
              const hash = hashString(text);
              messages.push({ text, hash });
            }
            break;
          }
        } catch (e) { }
      }
    }

    return messages;
  }

  // Helper to fetch and type answer by index
  async function fetchSolutionByIndex(index) {
    console.log(LOG, `🔍 Fetching answer #${index}...`);

    // Abort any existing typewriter session first (stop previous #N if any)
    if (typewriter) {
      typewriter.abort();
      typewriter = null;
    }

    // Guard: orphaned extension (reloaded in background)
    if (checkOrphaned()) {
      showPageNotification('⟳ Extension reloaded — refresh this tab to use #N triggers', 'warn', 5000);
      return;
    }

    setState('TYPING');
    controlState = 'running';
    showPageNotification(`⏳ Loading code #${index}…`, 'info', 2500);

    let data = null;
    try {
      // Use background script to prevent "Private Network Access" popup
      data = await new Promise((resolve, reject) => {
        if (checkOrphaned()) {
          reject(new Error('orphaned'));
          return;
        }
        chrome.runtime.sendMessage(
          { type: 'FETCH_SOLUTION_BY_INDEX', index },
          (response) => {
            if (chrome.runtime.lastError) {
              reject(new Error(chrome.runtime.lastError.message));
            } else if (response && response.success) {
              resolve(response.data);
            } else {
              reject(new Error(response ? response.error : 'Network error'));
            }
          }
        );
      });
    } catch (e) {
      console.error(LOG, `❌ Fetch error: ${e.message}`);

      if (e.message === 'orphaned' || e.message.includes('Extension context')) {
        showPageNotification('⟳ Extension reloaded — refresh this tab', 'warn', 5000);
      } else if (e.message.includes('Failed to fetch') || e.message.includes('Network') || e.message.includes('ERR_CONNECTION')) {
        showPageNotification('🔴 Server offline — start Drishi Enterprise first', 'error', 5000);
      } else {
        showPageNotification(`⚠ Could not load #${index}: ${e.message}`, 'warn', 4000);
      }

      setState('IDLE');
      controlState = 'stopped';
      return;
    }

    // ── Handle server response ─────────────────────────────────────────────
    if (!data || !data.found) {
      const reason = data ? data.error : 'No response';
      console.warn(LOG, `❌ Answer #${index} not found: ${reason}`);

      if (reason && reason.toLowerCase().includes('no questions')) {
        showPageNotification(`📭 No code for #${index} yet — ask a question in Meet Chat first`, 'warn', 5000);
      } else if (reason && reason.toLowerCase().includes('out of bounds')) {
        // Parse how many are available
        const m = reason.match(/\(1-(\d+)\)/);
        const avail = m ? m[1] : '?';
        showPageNotification(`📋 Only ${avail} code${avail === '1' ? '' : 's'} available — use #1 to #${avail}`, 'warn', 5000);
      } else {
        showPageNotification(`❌ Code #${index} not found: ${reason || 'unknown'}`, 'error', 4000);
      }

      setState('IDLE');
      controlState = 'stopped';
      return;
    }

    // ── Found — clear editor and type ─────────────────────────────────────
    console.log(LOG, `✅ Typing Answer #${data.index}: ${data.question}`);

    // Clear editor explicitly
    await clearEditor();

    showPageNotification(`▶ Typing code #${data.index} — Ctrl+Alt+P to pause`, 'ok', 3000);

    typewriter = new Typewriter(currentWpm);
    let rawCode = data.code || "";

    // If it's a theory answer (no code blocks), comment it as notes
    if (!rawCode.includes('def ') && !rawCode.includes('class ') &&
      !rawCode.includes('return ') && rawCode.length > 50 && !rawCode.startsWith('#')) {
      rawCode = rawCode.split('\n').map(line => `# ${line}`).join('\n');
    }

    const lines = rawCode.split('\n');

    // Type it out — pause/resume via Ctrl+Alt+P / Ctrl+Alt+A works because
    // controlState is now 'running' and typewriter is set
    const completed = await typewriter.type(lines, insertCharToEditor, null);

    if (completed) {
      console.log(LOG, `✅ SUCCESS: Typed answer #${data.index}`);
      showPageNotification(`✓ Done typing code #${data.index}`, 'ok', 2000);
    }
    // If not completed (aborted/stopped), goIdle() was already called externally

    // Only reset state if we weren't stopped externally (goIdle already set IDLE)
    if (state === 'TYPING') {
      setState('IDLE');
    }
    controlState = 'stopped';
  }

  async function goSolveMode() {
    // 0. Check extension context first
    if (checkOrphaned()) {
      console.log(LOG, 'Extension context invalid, skipping solve mode');
      return;
    }

    // 0a. Safety Check: If in iframe and no editor, ABORT (prevents Chili Piper/Tracker errors)
    const hasEditor = document.querySelector('.monaco-editor, .CodeMirror, .cm-editor, .cm-content, .ace_editor, #code, [data-mode-id]');
    if (window !== window.top && !hasEditor) {
      console.log(LOG, 'No editor found in iframe, skipping');
      return;
    }

    if (state !== 'IDLE' && state !== 'ARMED') {
      console.log(LOG, 'State is', state, '- not starting solve mode');
      return;
    }

    console.log(LOG, '═══════════════════════════════════════');
    console.log(LOG, 'Entering SOLVE mode via ##start...');
    console.log(LOG, '═══════════════════════════════════════');

    // Get current content to preserve signature
    let editorContent = await getEditorContent();
    console.log(LOG, '📝 Raw editor content:', JSON.stringify(editorContent));

    if (!editorContent) editorContent = "";

    // 1. Capture content and Clean trigger for LLM
    const triggerRegex = /(##start|#\d+)/g;
    const cleanedContent = editorContent.replace(triggerRegex, '').trim();
    console.log(LOG, '✂️  Cleaned content (Triggers removed):', JSON.stringify(cleanedContent));

    setState('TYPING');

    // 1a. IMMEDIATELY try to remove the trigger from the editor to stop the loop
    // SILENT WIPE to prevent duplicated logic
    try {
      if (bridgeReady) await bridgeCall('clear');
      else directClear();

      await Promise.race([
        new Promise(r => setTimeout(r, 500))
      ]);
    } catch (e) { /* Non-fatal */ }

    // 2. Extract Problem Statement
    let problemText = extractProblemText();
    console.log(LOG, '📜 Extracted problem text length:', problemText ? problemText.length : 0);

    if (!problemText || problemText.length < 50) {
      console.warn(LOG, '⚠️ Problem text too short or empty. Using fallback.');
      problemText = "Solve the following coding problem based on the editor content.";
    }

    // 3. Request Solution
    try {
      console.log(LOG, `⚡ Sending request to ${SERVER_URL}/api/solve_problem...`);
      console.log(LOG, `   URL: ${window.location.href}`);
      console.log(LOG, `   Editor Content Length: ${editorContent.length}`);

      const data = await new Promise((resolve, reject) => {
        // Check context before sending
        if (checkOrphaned()) {
          return reject(new Error('Extension context invalidated'));
        }

        console.log(LOG, '⚡ Sending proxy request via background script...');

        try {
          chrome.runtime.sendMessage({
            type: 'SOLVE_PROBLEM_PROXY',
            payload: {
              problem: problemText,
              editor: cleanedContent,
              url: window.location.href
            }
          }, (response) => {
            // Safely check lastError
            try {
              if (chrome.runtime.lastError) {
                const err = chrome.runtime.lastError;
                if (err.message && err.message.includes('context invalidated')) {
                  console.log(LOG, 'Extension context changed. Refresh page to continue.');
                  if (triggerPollInterval) clearInterval(triggerPollInterval);
                }
                return reject(err);
              }
            } catch (e) {
              return reject(new Error('Extension context lost'));
            }

            if (response && response.success) {
              resolve(response.data);
            } else {
              reject(new Error(response ? response.error : 'Network error'));
            }
          });
        } catch (e) {
          reject(new Error('Extension context invalidated'));
        }
      });

      console.log(LOG, '✅ Proxy Success! Solution length:', data.solution ? data.solution.length : 0);

      if (data && data.solution) {
        console.log(LOG, 'Solution received. Length:', data.solution.length);

        // Check mode: only type if in AUTO mode
        if (currentMode === 'auto') {
          console.log(LOG, '✍️  AUTO-TYPE mode: Cleaning & Typing into editor...');

          // 4. CLEAR EVERYTHING before typing to avoid duplicates/boilerplate
          await clearEditor();
          await new Promise(r => setTimeout(r, 1000)); // Increased for Monaco stability

          typewriter = new Typewriter(currentWpm);
          const lines = data.solution.split('\n');

          const completed = await typewriter.type(lines, insertCharToEditor, null);
          if (completed) {
            console.log(LOG, 'Solution typing complete');

            // Force scroll to bottom/end of content
            const cmContent = document.querySelector('.cm-content');
            if (cmContent) {
              cmContent.scrollIntoView({ behavior: 'smooth', block: 'end' });
            }

            // AUTO-DEBUG: Verify solution
            await runAndDebug(problemText);
          }
        } else {
          console.log(LOG, '👁️  VIEW-ONLY mode: Solution displayed on server only');
        }
      } else {
        console.error(LOG, 'No solution returned', data);
      }
    } catch (e) {
      // Ignore "Extension context invalidated" - it's a known side effect of reloading extensions
      if (e && e.message && e.message.includes('context invalidated')) {
        console.warn(LOG, 'Extension context invalidated. Please refresh the page to continue.');
        return;
      }
      console.error(LOG, 'Solve failed. Check if Server is running!', e);
    } finally {
      setState('IDLE');
    }
  }

  // ═══════════════════════════════════════════════
  // AUTO-DEBUGGER SYSTEM
  // ═══════════════════════════════════════════════

  async function runAndDebug(originalProblemText) {
    // Determine platform
    const isHackerrank = window.location.hostname.includes('hackerrank');
    const isProgramiz = window.location.hostname.includes('programiz');

    if (!isHackerrank && !isProgramiz) return;

    console.log(LOG, '🚀 Auto-running code to verify logic...');

    await new Promise(r => setTimeout(r, 2000)); // Wait for editor to settle

    // 1. Find and Click Run Button
    let runBtn = null;
    if (isHackerrank) {
      runBtn = document.querySelector('button.hr-monaco-compile') ||
        Array.from(document.querySelectorAll('button')).find(b => b.textContent && b.textContent.includes('Run Code'));
    } else if (isProgramiz) {
      runBtn = document.querySelector('.btn-run, .viz-run-btn') ||
        Array.from(document.querySelectorAll('button')).find(b => b.innerText === 'Run');
    }

    if (!runBtn) {
      console.warn(LOG, 'Could not find Run Code button. Manual run required.');
      return;
    }

    runBtn.click();

    // 2. Wait for Results (Poll for 10s)
    console.log(LOG, 'Waiting for execution results...');
    const result = await waitForExecutionResult();

    if (result.status === 'success') {
      console.log(LOG, '✅ Code execution SUCCESS!');
      retryCount = 0; // Reset
    } else if (result.status === 'error') {
      if (retryCount < MAX_RETRIES) {
        console.warn(LOG, '❌ Code execution FAILED:', result.error);
        retryCount++;

        // 3. Trigger Fix
        console.log(LOG, `🔄 Attempting fix (Retry ${retryCount}/${MAX_RETRIES})...`);

        const fixPrompt = originalProblemText + "\n\nPREVIOUS CODE FAILED WITH ERROR:\n" + result.error + "\n\nPLEASE FIX THE CODE. OUTPUT A COMPLETE SELF-CONTAINED SCRIPT (INCLUDING IMPORTS AND IF __NAME__ == '__MAIN__' BLOCK). OUTPUT RAW PYTHON CODE ONLY.";

        await requestFix(fixPrompt);
      } else {
        console.warn(LOG, '❌ Max retries reached. Please fix manually.');
        retryCount = 0;
      }
    }
  }

  async function waitForExecutionResult() {
    // Poll for result container
    for (let i = 0; i < 20; i++) { // 10 seconds timeout
      await new Promise(r => setTimeout(r, 500));

      // HackerRank specific output check
      const responseArea = document.querySelector('.challenge-response');
      if (responseArea) {
        const text = responseArea.innerText;

        if (text.includes('Congratulations') || text.includes('Sample Test Cases Passed')) {
          return { status: 'success' };
        }

        if (text.includes('Wrong Answer') || text.includes('Error') || text.includes('Traceback') || text.includes('Runtime Error')) {
          // Extract pertinent error part (limit size)
          return { status: 'error', error: text.substring(0, 800) };
        }
      }
    }
    return { status: 'timeout' };
  }

  async function requestFix(problemText) {
    try {
      const data = await new Promise((resolve, reject) => {
        chrome.runtime.sendMessage({
          type: 'SOLVE_PROBLEM_PROXY',
          payload: {
            problem: problemText,
            editor: "", // Treat as fresh solve
            url: window.location.href
          }
        }, (response) => {
          if (response && response.success) resolve(response.data);
          else reject(new Error(response ? response.error : 'Network error'));
        });
      });

      if (data && data.solution) {
        console.log(LOG, '🔧 Fix received. Typing...');
        await clearEditor();
        typewriter = new Typewriter(currentWpm);
        const lines = data.solution.split('\n');
        const completed = await typewriter.type(lines, insertCharToEditor, null);
        if (completed) {
          // Recursive check!
          await runAndDebug(problemText);
        }
      }
    } catch (e) {
      console.error(LOG, 'Fix request failed:', e);
    }
  }


  // ═══════════════════════════════════════════════
  // STEALTH KEYBOARD SHORTCUTS (NO TEXT TRIGGERS)
  // ═══════════════════════════════════════════════

  let currentMode = 'auto'; // 'auto' or 'view'
  let controlState = 'stopped'; // 'stopped', 'running', 'paused'

  // Listen for keyboard shortcuts
  // Global keyboard shortcut listener
  document.addEventListener('keydown', async (e) => {
    // Toggle shortcuts (Ctrl+Alt+A / Ctrl+Alt+S)
    if (e.ctrlKey && e.altKey) {
      try {
        switch (e.key.toLowerCase()) {
          case 'a': // START/RESUME
            e.preventDefault();
            console.log(LOG, '⚡ Ctrl+Alt+A: START/RESUME');
            await triggerStart();
            break;
          case 'p': // PAUSE
            e.preventDefault();
            console.log(LOG, '⏸️  Ctrl+Alt+P: PAUSE');
            await triggerPause();
            break;
          case 's': // STOP
            e.preventDefault();
            console.log(LOG, '🛑 Ctrl+Alt+S: STOP');
            await triggerStop();
            break;
          case 'm': // MODE TOGGLE
            e.preventDefault();
            console.log(LOG, '🔄 Ctrl+Alt+M: TOGGLE MODE');
            await triggerToggleMode();
            break;
        }
      } catch (err) {
        console.error(LOG, 'Shortcut failed (likely refresh needed):', err);
        // Silently fail - no popup during interview
      }
    }
  });

  async function triggerStart() {
    console.log(LOG, '=== triggerStart() called ===');
    console.log(LOG, `Current state: ${state}, controlState: ${controlState}`);

    // If we're PAUSED, just RESUME - don't restart
    if (controlState === 'paused' && typewriter && typewriter.isPaused) {
      console.log(LOG, '▶️ Resuming from pause...');
      controlState = 'running';
      typewriter.resume();
      try {
        await apiFetch(`${SERVER_URL}/api/control/resume`, { method: 'POST' });
      } catch (e) { }
      return;
    }

    controlState = 'running';

    // Notify server via background proxy (handles mixed content) with robust safety check
    safeSendMessage({ type: 'CONTROL_START' });

    // Detect context: Chat vs Editor
    const platform = detectChatPlatform();

    // Logic Update: If on a known chat platform, ALWAYS try Chat Mode first.
    // goSolveModeChat has a built-in RETRY loop (2 seconds) which handles slow DOM loading.
    // Trying to check synchronously here is brittle and defeats the retry logic.
    if (platform) {
      console.log(LOG, `📱 Chat Platform detected (${platform}): Using Chat Solver with Retry...`);
      await goSolveModeChat();

      // If chat solve fails (no messages found after retry), goSolveModeChat returns fast.
      // We could technically fall back to editor here if needed, but usually on Teams 
      // with Ctrl+Alt+A, the user WANTS chat solving.
    } else {
      // Only use Editor mode if NOT on a chat platform
      console.log(LOG, '💻 Editor context detected (Generic/Unknown Platform)');

      // Check state only for editor mode
      if (state !== 'IDLE' && state !== 'ARMED') {
        console.log(LOG, `Skipping editor mode - state is ${state}`);
        return;
      }
      await goSolveMode();
    }
  }

  /**
   * Safe wrapper for chrome.runtime.sendMessage to handle "Extension context invalidated"
   * gracefully without noisy console errors.
   */
  function safeSendMessage(payload, callback) {
    let isInvalid = false;
    try {
      // Accessing chrome.runtime or id can throw if invalidated
      if (!chrome.runtime || !chrome.runtime.id) {
        isInvalid = true;
      }
    } catch (e) {
      isInvalid = true;
    }

    if (isInvalid) {
      console.error(LOG, '❌ Extension context invalidated. Please refresh the page!');
      // Silently fail to avoid interrupting interview
      return;
    }

    try {
      chrome.runtime.sendMessage(payload, (response) => {
        if (chrome.runtime.lastError) {
          // Accessing lastError.message can technically throw too
          const msg = chrome.runtime.lastError.message;
          if (msg && msg.includes('Extension context invalidated')) {
            console.error(LOG, '❌ Context invalidated during send. Refresh required.');
          } else {
            console.warn(LOG, 'Background message failed:', msg);
          }
        }
        if (callback) callback(response);
      });
    } catch (e) {
      console.error(LOG, '❌ Call failed (context invalidated). Please Refresh.', e);
      // Silently fail
    }
  }

  async function triggerPause() {
    console.log(LOG, 'PAUSE triggered');
    try {
      await apiFetch(`${SERVER_URL}/api/control/pause`, { method: 'POST' });
    } catch (e) { }

    controlState = 'paused';
    if (typewriter) {
      typewriter.pause();  // Pause, don't abort - allows resume
    }
  }

  async function triggerStop() {
    console.log(LOG, 'STOP triggered (kill switch)');
    try {
      await apiFetch(`${SERVER_URL}/api/control/stop`, { method: 'POST' });
    } catch (e) { }

    controlState = 'stopped';
    goIdle();
  }

  async function triggerToggleMode() {
    try {
      const response = await apiFetch(`${SERVER_URL}/api/control/toggle_mode`, { method: 'POST' });
      const data = await response.json();
      currentMode = data.mode;
      console.log(LOG, `Mode switched to: ${currentMode === 'auto' ? 'AUTO-TYPE' : 'VIEW-ONLY'}`);
    } catch (e) {
      // Toggle locally if server unreachable
      currentMode = currentMode === 'auto' ? 'view' : 'auto';
      console.log(LOG, `Mode toggled locally to: ${currentMode}`);
    }
  }

  // REMOVED: parseTrigger function (no more text triggers)
  // REMOVED: Text-based ##start, ##stop detection

  // ═══════════════════════════════════════════════
  // TEXT TRIGGER DETECTION
  // ═══════════════════════════════════════════════

  function startTriggerPolling() {
    if (triggerPollInterval) return;

    // Check if we're on a coding platform - always enable polling there
    // isCodingPlatform already defined at top of script

    // Safety: Only poll if we have found an editor, bridge, or on coding platform
    if (!bridgeReady && !bridgeTimedOut && !isCodingPlatform) return;

    console.log(LOG, 'Trigger polling enabled (checking for ##start or #number)');
    if (isCodingPlatform) {
      console.log(LOG, '🎯 Coding platform detected:', window.location.hostname);
    }

    triggerPollInterval = setInterval(async () => {
      // 1. Check for context invalidation and STOP if disconnected
      if (checkOrphaned()) return;

      // Respect PAUSE state (Ctrl+Alt+P)
      if (state === 'PAUSED') {
        return;
      }

      if (state !== 'IDLE') return;

      try {
        const content = await getEditorContent();
        if (!content) return;

        // Check for triggers

        // 1. Priority: ##start (Solve Mode)
        if (content.includes('##start')) {
          console.log(LOG, "🚀 Trigger '##start' detected!");
          // Clear it immediately to prevent double triggers
          // (Actually goSolveMode handles clearing)
          goSolveMode();
          return;
        }

        // 2. Specific Answer Request: #1, #2 (Strict: Must be on its own line)
        // Matches: "#1", "  #1  ", "#12"
        // Does NOT match: "Item #1", "Comments #123"
        const numberMatch = content.match(/(?:^|\n)\s*#(\d+)\s*(?:$|\n)/);
        if (numberMatch) {
          const index = parseInt(numberMatch[1], 10);
          console.log(LOG, `🚀 Trigger '#${index}' detected!`);
          fetchSolutionByIndex(index, numberMatch[0].trim());
          return;
        }
      } catch (e) {
        // Ignore errors during polling
      }
    }, 1000);
  }

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (checkOrphaned()) return;
    try {
      if (msg.type === 'codePayload') {
        if (msg.index === lastCodeId) return;
        lastCodeId = msg.index;
        typewriter = new Typewriter(currentWpm);
        typewriter.type(msg.lines, insertCharToEditor, () => goIdle());
      } else if (msg.type === 'codeNotFound') {
        console.warn(LOG, 'Code #' + msg.index + ' not found. Available: ' + msg.available);
        goIdle();
      } else if (msg.type === 'getState') {
        sendResponse({ state });
        return true;
      } else if (msg.type === 'FORCE_CAPTURE') {
        e.preventDefault();
        forceCaptureNow().then(result => {
          sendResponse({ success: result.captured, platform: result.platform });
        });
        return true;
      }
    } catch (e) { /* Extension context handled */ }
  });

  // --- GHOST TRIGGER (Stealth Mode) ---
  let keyBuffer = '';
  const MAX_BUFFER = 20;

  window.addEventListener('keydown', async (e) => {
    // Manage Buffer
    if (e.key === 'Backspace') {
      keyBuffer = keyBuffer.slice(0, -1);
    } else if (e.key.length === 1 && !e.ctrlKey && !e.altKey && !e.metaKey) {
      keyBuffer += e.key;
      if (keyBuffer.length > MAX_BUFFER) keyBuffer = keyBuffer.slice(-MAX_BUFFER);
    }

    // Check Trigger on ENTER
    if (e.key === 'Enter') {
      // Only fire triggers when IDLE — prevents re-triggering while typing or paused
      if (state !== 'IDLE') return;

      // Check for ##start
      if (keyBuffer.endsWith('##start')) {
        e.preventDefault(); // Stop newline
        e.stopPropagation();
        console.log(LOG, '👻 Ghost Trigger: ##start detected!');

        // 1. Wipe text (7 chars)
        await deleteHistory(7);

        // 2. Execute
        goSolveMode();
        keyBuffer = ''; // access granted
        return;
      }

      // Check for #number (e.g. #1, #12)
      // Regex to match suffix like #12
      const match = keyBuffer.match(/#(\d+)$/);
      if (match) {
        const triggerLen = match[0].length;
        const index = parseInt(match[1], 10);

        e.preventDefault(); // Stop newline
        e.stopPropagation();
        console.log(LOG, `👻 Ghost Trigger: #${index} detected!`);

        // 1. Wipe text
        await deleteHistory(triggerLen);

        // 2. Execute
        fetchSolutionByIndex(index, match[0]);
        keyBuffer = '';
        return;
      }
    }
  }, true); // Capture phase to see keys before editors

  async function deleteHistory(charCount) {
    if (bridgeReady) {
      // Send delete command to bridge (simulates backspaces)
      await bridgeCall('stealthBackspace', charCount);
    }
  }

  document.addEventListener('keydown', (e) => {
    // Interference check: Pause typing if user types
    if (state === 'TYPING' && typewriter) {
      // Ignore shortcuts (Ctrl/Alt/Meta) - CRITICAL Fix for Pause/Resume conflict
      if (e.ctrlKey || e.altKey || e.metaKey) return;

      // Ignore modifier keys themselves
      if (['Control', 'Alt', 'Shift', 'Meta'].includes(e.key)) return;

      typewriter.pauseForUser();
    }
  });

  window.addEventListener('beforeunload', () => {
    try {
      if (state !== 'IDLE') goIdle();
    } catch (e) { }
  });

  console.log(LOG, 'Content script loading (v1.1 - Proxy Mode)...');

  // Check if we're on a coding platform
  // isCodingPlatform already defined at top of script

  if (isCodingPlatform) {
    console.log(LOG, '🎯 Coding platform detected - enabling polling immediately');
    // Start polling right away on coding platforms, retry if editor not ready
    setTimeout(() => {
      bridgeTimedOut = true;
      startTriggerPolling();
    }, 1000);
  }

  injectBridge();

  setTimeout(() => {
    if (!bridgeReady && !bridgeTimedOut) {
      // Check one last time before deciding to be a "silent helper"
      const content = directGetContent();
      if (content !== null || isCodingPlatform) {
        console.log(LOG, 'Direct DOM access works, keeping active.');
        bridgeTimedOut = true;
        startTriggerPolling();
      } else {
        // SILENT ABORT for helper iframes (Chili Piper, Google Ads, etc)
      }
    } else {
      // Bridge found, start polling
      startTriggerPolling();
    }
  }, 3000);
})();
