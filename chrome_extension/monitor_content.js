if (!window.drishiMonitorInstalled) {
  window.drishiMonitorInstalled = true;

  let monitoring = false;
  let lastMouseMoveSentAt = 0;
  let lastUrl = window.location.href;
  let urlCheckTimer = null;

  function emit(payload) {
    if (!monitoring) return;
    try {
      if (!chrome.runtime?.id) return;
      chrome.runtime.sendMessage({
        type: "mon_browser_event",
        payload: { ...payload, url: window.location.href },
      }).catch(() => { });
    } catch (_) { }
  }

  function emitCurrentUrl(eventType = "active_url") {
    const nextUrl = window.location.href;
    if (nextUrl === lastUrl && eventType === "url_change") {
      return;
    }

    lastUrl = nextUrl;
    emit({
      type: eventType,
      url: nextUrl,
      title: document.title,
    });
  }

  function patchHistory() {
    const originalPushState = history.pushState;
    const originalReplaceState = history.replaceState;

    history.pushState = function (...args) {
      originalPushState.apply(this, args);
      emitCurrentUrl("url_change");
    };

    history.replaceState = function (...args) {
      originalReplaceState.apply(this, args);
      emitCurrentUrl("url_change");
    };

    window.addEventListener("popstate", () => emitCurrentUrl("url_change"));
    window.addEventListener("hashchange", () => emitCurrentUrl("url_change"));
  }

  function observeDomChanges() {
    const observer = new MutationObserver(() => {
      emitCurrentUrl("url_change");
    });

    observer.observe(document.documentElement, {
      subtree: true,
      childList: true,
      characterData: false,
    });
  }

  function startUrlPolling() {
    urlCheckTimer = window.setInterval(() => {
      if (window.location.href !== lastUrl) {
        emitCurrentUrl("url_change");
      }
    }, 1000);
  }

  document.addEventListener(
    "click",
    (event) => {
      emit({
        type: "click",
        x: event.clientX,
        y: event.clientY,
      });
    },
    true
  );

  document.addEventListener(
    "keydown",
    (event) => {
      emit({
        type: "keydown",
        key: event.key,
      });
    },
    true
  );

  document.addEventListener(
    "mousemove",
    (event) => {
      const now = Date.now();
      if (now - lastMouseMoveSentAt < 100) {
        return;
      }

      lastMouseMoveSentAt = now;
      emit({
        type: "mousemove",
        x: event.clientX,
        y: event.clientY,
      });
    },
    true
  );

  chrome.runtime.onMessage.addListener((message) => {
    if (message.type === "mon_monitoring_state") {
      monitoring = message.monitoring;
      if (monitoring) {
        emitCurrentUrl("active_url");
      }
    }
  });

  chrome.runtime.sendMessage({ type: "mon_get_state" }, (state) => {
    if (chrome.runtime.lastError || !state) {
      return;
    }

    monitoring = state.monitoring;
    if (monitoring) {
      emitCurrentUrl("active_url");
    }
  });

  patchHistory();
  observeDomChanges();
  startUrlPolling();
}
