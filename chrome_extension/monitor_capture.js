function notifyBackground(message, contextLabel) {
  return chrome.runtime.sendMessage(message).catch((error) => {
    console.debug(`${contextLabel}: background unavailable`, error);
    return null;
  });
}

const sender = new WebRTCSender({
  onSignal: (signalType, viewerId, data) => {
    notifyBackground(
      { type: "mon_webrtc_signal_from_capture", signal_type: signalType, viewer_id: viewerId, data },
      "mon_webrtc_signal_from_capture"
    );
  },
  onStatusChange: (status) => {
    notifyBackground(
      {
        type: status === "stopped" ? "mon_screen_capture_stopped" : "mon_screen_capture_started",
        screenStatus: status,
      },
      "mon_capture_status"
    );
  },
  onViewerCountChange: (count) => {
    notifyBackground({ type: "mon_webrtc_viewer_count", count }, "mon_webrtc_viewer_count");
  },
});

let activeStream = null;
let autoPromptTriggered = false;
let manualStop = false;

async function startScreenCapture(streamId) {
  if (activeStream) {
    activeStream.getTracks().forEach((t) => t.stop());
    activeStream = null;
  }
  manualStop = false;

  // Settle time for Linux/Wayland to release the picker context
  await new Promise(r => setTimeout(r, 600));

  console.log("[MonitorCapture] Starting Simplified Capture for ID:", streamId);

  // Note: On most Linux builds, the token is SINGLE-USE.
  // 1080p/30fps for crisp remote desktop quality (TeamViewer-level).
  // Fall back to 720p if GPU/compositor rejects the higher resolution.
  const constraints = {
    audio: false,
    video: {
      mandatory: {
        chromeMediaSource: 'desktop',
        chromeMediaSourceId: streamId,
        maxWidth: 1920,
        maxHeight: 1080,
        maxFrameRate: 30
      }
    }
  };

  try {
    const stream = await navigator.mediaDevices.getUserMedia(constraints);

    const [track] = stream.getVideoTracks();
    if (!track) throw new Error("Stream established but no video track found.");

    // "Visibility Mode" Fix: attach the stream to the visible video element
    const video = document.getElementById("capture-video");
    const previewContainer = document.getElementById("preview-container");
    const statusDot = document.getElementById("status-dot");
    const statusText = document.getElementById("status-text");
    const actionBtn = document.getElementById("manual-picker-btn");

    if (video && previewContainer) {
      video.srcObject = stream;
      video.onloadedmetadata = () => video.play().catch(() => { });
      previewContainer.style.display = "block";
    }

    if (statusDot && statusText) {
      statusDot.classList.add("active");
      statusText.textContent = "Live";
      statusText.style.color = "var(--success)";
    }

    if (actionBtn) {
      actionBtn.textContent = "Stop Screen Share";
      actionBtn.style.background = "#ef4444"; // Red for stop
    }

    track.addEventListener("ended", () => {
      if (manualStop) { manualStop = false; return; }
      activeStream = null;
      sender.stopStream();
      reportCaptureStopped("interrupted", "Capture ended by system.");
    });

    activeStream = stream;
    await sender.setStream(stream);
    console.log("[MonitorCapture] SUCCESS: Remote visibility pipeline established.");
    showShareUrl();
  } catch (error) {
    console.error("[MonitorCapture] CAPTURE FAILURE:", error.name, error.message);

    if (error.message.includes("tab capture") || error.name === "AbortError") {
      console.error("[Monitor] LINUX STABILITY ALERT: Hardware acceleration or library inconsistency detected.");
      console.error("[Monitor] ACTION REQUIRED: Restart Chrome with --disable-gpu or choose 'Entire Screen' instead of 'Tab'.");
    }
    throw error;
  }
}

function stopScreenCapture() {
  if (activeStream) {
    manualStop = true;
    activeStream.getTracks().forEach((t) => t.stop());
    activeStream = null;
  }
  sender.closeAll();
  reportCaptureStopped("manual", "Stopped");

  // Reset UI
  const previewContainer = document.getElementById("preview-container");
  const statusDot = document.getElementById("status-dot");
  const statusText = document.getElementById("status-text");
  const actionBtn = document.getElementById("manual-picker-btn");

  if (previewContainer) previewContainer.style.display = "none";
  if (statusDot) statusDot.classList.remove("active");
  if (statusText) {
    statusText.textContent = "Ready";
    statusText.style.color = "var(--text-dim)";
  }
  if (actionBtn) {
    actionBtn.textContent = "Start Screen Share";
    actionBtn.style.background = ""; // Back to primary
  }
}

async function promptAndStartCapture(streamId) {
  try {
    await startScreenCapture(streamId);
  } catch (error) {
    await notifyBackground(
      { type: "mon_screen_capture_failed", error: error.name + ": " + error.message },
      "mon_screen_capture_failed"
    );
  }
}

function handleStartRequest(streamId) {
  // Allow multiple starts by resetting the trigger flag
  autoPromptTriggered = true;
  promptAndStartCapture(streamId);
}

async function startCaptureSequence() {
  console.log("[MonitorCapture] Initializing same-tab capture sequence...");

  // Settle internal state
  if (activeStream) {
    activeStream.getTracks().forEach((t) => t.stop());
    activeStream = null;
  }
  manualStop = false;

  return new Promise((resolve) => {
    try {
      // Anchoring the picker to the entire desktop scope, but triggered from this tab context
      chrome.desktopCapture.chooseDesktopMedia(
        ["screen", "window", "tab"],
        (streamId) => {
          if (chrome.runtime.lastError) {
            console.error("[MonitorCapture] Picker Error:", chrome.runtime.lastError.message);
            resolve({ ok: false, error: chrome.runtime.lastError.message });
            return;
          }
          if (!streamId) {
            console.log("[MonitorCapture] Picker canceled.");
            resolve({ ok: false, error: "Canceled" });
            return;
          }

          // Token is fresh and in THIS tab context - start immediately
          startScreenCapture(streamId)
            .then(() => resolve({ ok: true }))
            .catch((err) => resolve({ ok: false, error: err.message }));
        }
      );
    } catch (e) {
      console.error("[MonitorCapture] Picker failed:", e);
      resolve({ ok: false, error: e.message });
    }
  });
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === "mon_capture_ping") { sendResponse({ ok: true }); return false; }
  if (message.type === "mon_stop_screen_capture") { stopScreenCapture(); sendResponse({ ok: true }); return false; }

  // New dispatcher for the full sequence
  if (message.type === "mon_capture_bridge_start_request") {
    startCaptureSequence().then(sendResponse);
    return true; // async
  }

  if (message.type === "mon_start_screen_capture") {
    handleStartRequest(message.streamId);
    sendResponse({ ok: true });
    return false;
  }

  if (message.type === "mon_viewer_joined") {
    sender.createOffer(message.viewerId)
      .then(() => sendResponse({ ok: true }))
      .catch((e) => sendResponse({ ok: false, error: e.message }));
    return true;
  }

  if (message.type === "mon_signal_to_sender") {
    const handler = message.signalType === "answer"
      ? sender.handleAnswer(message.viewerId, message.data)
      : sender.handleIceCandidate(message.viewerId, message.data);
    Promise.resolve(handler)
      .then(() => sendResponse({ ok: true }))
      .catch((e) => sendResponse({ ok: false, error: e.message }));
    return true;
  }

  if (message.type === "mon_viewer_left") { sender.closePeer(message.viewerId); sendResponse({ ok: true }); return false; }
  return false;
});

document.getElementById("manual-picker-btn")?.addEventListener("click", () => {
  const key = document.getElementById("secret-key-input")?.value || "";
  chrome.storage.sync.set({ secretCode: key }, () => {
    console.log("[MonitorCapture] Secret key synced to background:", key);
    startCaptureSequence();
  });
});

document.getElementById("copy-url-btn")?.addEventListener("click", () => {
  const input = document.getElementById("viewer-url-display");
  if (!input) return;
  navigator.clipboard.writeText(input.value);
  const btn = document.getElementById("copy-url-btn");
  if (btn) {
    const original = btn.textContent;
    btn.textContent = "✓";
    setTimeout(() => { btn.textContent = original; }, 1500);
  }
});

function showShareUrl() {
  chrome.storage.sync.get({ serverUrl: "", secretCode: "", sessionId: "default" }, (data) => {
    const configuredBase = (data.serverUrl || "http://localhost:8000").replace(/\/$/, "");
    const session = data.sessionId || "default";
    const secret = data.secretCode || "";

    function renderUrl(base) {
      let url = `${base}/v/${session}`;
      if (secret && secret !== "none") {
        url += `/${encodeURIComponent(secret)}`;
      }
      const display = document.getElementById("viewer-url-display");
      const container = document.getElementById("share-url-container");
      if (display && container) {
        display.value = url;
        container.style.display = "block";
      }
    }

    // Ask the server for its best public URL (ngrok if active, else LAN IP)
    fetch(`${configuredBase}/api/public_url`, { headers: { "ngrok-skip-browser-warning": "true" } })
      .then(r => r.ok ? r.json() : null)
      .then(json => renderUrl(json?.url ? json.url.replace(/\/$/, "") : configuredBase))
      .catch(() => renderUrl(configuredBase));
  });
}

// Load current key and Address on open
chrome.storage.sync.get({ secretCode: "", sessionId: "default" }, (data) => {
  const input = document.getElementById("secret-key-input");
  if (input) input.value = data.secretCode;
  
  const passDisplay = document.getElementById("password-display");
  if (passDisplay) passDisplay.textContent = data.secretCode || "12";

  const addressDisplay = document.getElementById("drishi-address-display");
  if (addressDisplay) {
    const s = data.sessionId.toString();
    if (s.length === 6 && /^\d+$/.test(s)) {
      // Format numeric ID as "123 456"
      addressDisplay.textContent = s.slice(0, 3) + " " + s.slice(3);
    } else {
      addressDisplay.textContent = s;
    }
  }
});

notifyBackground({ type: "mon_capture_host_ready" }, "mon_capture_host_ready");

function reportCaptureStopped(reason, message) {
  notifyBackground(
    { type: "mon_screen_capture_stopped", screenStatus: "stopped", reason },
    "mon_capture_stopped"
  );
}
