(() => {
  const OVERLAY_ID = "browser-monitor-remote-control-overlay";
  const BUTTON_ID = "browser-monitor-remote-control-stop";

  function createOverlay() {
    if (document.getElementById(OVERLAY_ID)) {
      return document.getElementById(OVERLAY_ID);
    }

    const overlay = document.createElement("div");
    overlay.id = OVERLAY_ID;
    overlay.innerHTML = `
      <div class="remote-control-pill">
        <span class="rc-dot"></span>
        <div class="rc-expanded">
          <span id="remote-control-text">Remote control active</span>
          <button type="button" id="${BUTTON_ID}">Stop</button>
        </div>
      </div>
    `;
    const style = document.createElement("style");
    style.textContent = `
      #${OVERLAY_ID} {
        position: fixed;
        top: 10px;
        right: 10px;
        z-index: 2147483647;
        pointer-events: none;
      }
      .remote-control-pill {
        pointer-events: all;
        background: rgba(0, 0, 0, 0.7);
        backdrop-filter: blur(4px);
        padding: 6px;
        border-radius: 20px;
        border: 1px solid rgba(255, 255, 255, 0.1);
        display: flex;
        align-items: center;
        gap: 0;
        overflow: hidden;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        width: 24px;
        height: 24px;
        cursor: default;
      }
      .remote-control-pill:hover {
        width: auto;
        padding: 6px 12px;
        gap: 10px;
      }
      .rc-dot {
        width: 10px;
        height: 10px;
        background: #ff4d4d;
        border-radius: 50%;
        box-shadow: 0 0 8px #ff4d4d;
        flex-shrink: 0;
      }
      .rc-expanded {
        display: none;
        white-space: nowrap;
        color: #fff;
        font-family: inherit;
        font-size: 11px;
        align-items: center;
        gap: 10px;
      }
      .remote-control-pill:hover .rc-expanded {
        display: flex;
      }
      #${BUTTON_ID} {
        background: rgba(255, 255, 255, 0.1);
        border: 1px solid rgba(255, 255, 255, 0.2);
        color: #fff;
        border-radius: 4px;
        padding: 2px 8px;
        cursor: pointer;
        font-size: 10px;
      }
      #${BUTTON_ID}:hover {
        background: #ff4d4d;
        border-color: #ff4d4d;
      }
    `;
    document.head.appendChild(style);
    document.body.appendChild(overlay);
    return overlay;
  }

  function showOverlay(controllerId) {
    const overlay = createOverlay();
    overlay.style.display = "block";
    const text = overlay.querySelector("#remote-control-text");
    if (text) {
      text.textContent = controllerId
        ? `Remote control active (Viewer ${controllerId})`
        : "Remote control session active";
    }
    const button = overlay.querySelector(`#${BUTTON_ID}`);
    if (button && !button.dataset.remoteControlListener) {
      button.dataset.remoteControlListener = "1";
      button.addEventListener("click", () => {
        chrome.runtime.sendMessage({ type: "mon_disable_remote_control_request" });
      });
    }
  }

  function hideOverlay() {
    const existing = document.getElementById(OVERLAY_ID);
    if (existing) {
      existing.style.display = "none";
    }
  }

  chrome.runtime.onMessage.addListener((message) => {
    if (message.type === "remote_control_state") {
      if (message.active) {
        showOverlay(message.controller_id);
      } else {
        hideOverlay();
      }
    }
  });
})();
