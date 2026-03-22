(() => {
  if (window.RemoteControlManager) {
    return;
  }

  const DEFAULT_STATUS_TEXT = "Remote control inactive";

  const manager = {
    videoElement: null,
    buttonElement: null,
    statusElement: null,
    sendMessageFn: null,
    viewerId: null,
    active: false,
    pending: false,
    token: null,
    mouseRAF: null,
    pendingMouseEvent: null,
    // BUGFIX: track whether mousedown originated inside video to guard mouseup
    mousedownInVideo: false,
    // Pointer lock mode: virtual cursor accumulates relative mouse deltas
    pointerLocked: false,
    virtualX: 0.5,
    virtualY: 0.5,
    keyMap: {
      "Control": "ctrl",
      "Alt": "alt",
      "AltGraph": "alt",
      "Shift": "shift",
      "Meta": "win",
      "OS": "win",
      "Command": "win",
      "Enter": "enter",
      "Backspace": "backspace",
      "Tab": "tab",
      "Escape": "esc",
      " ": "space",
      "ArrowUp": "up",
      "ArrowDown": "down",
      "ArrowLeft": "left",
      "ArrowRight": "right",
      "Delete": "delete",
      "PageUp": "pageup",
      "PageDown": "pagedown",
      "Home": "home",
      "End": "end",
      "CapsLock": "capslock",
      "Insert": "insert",
      "ContextMenu": "apps",
      "F1": "f1", "F2": "f2", "F3": "f3", "F4": "f4", "F5": "f5", "F6": "f6",
      "F7": "f7", "F8": "f8", "F9": "f9", "F10": "f10", "F11": "f11", "F12": "f12"
    }
  };

  const keyListenerOptions = { capture: true };

  function updateButtonLabel() {
    if (!manager.buttonElement) {
      return;
    }
    // We handle visual state (active/pending) via colors in dashboard.js
    // to preserve the professional SVG icons.
    manager.buttonElement.disabled = (manager.active || manager.pending) && !manager.active;
  }

  function updateStatusText(text) {
    if (!manager.statusElement) {
      return;
    }

    manager.statusElement.textContent = text || DEFAULT_STATUS_TEXT;
  }

  function sendControl(payload) {
    if (!manager.active || !manager.token || !manager.sendMessageFn) {
      return;
    }

    manager.sendMessageFn({ ...payload, token: manager.token });
  }

  function requestControl() {
    if (!manager.sendMessageFn || manager.active || manager.pending) {
      return;
    }

    manager.pending = true;
    updateButtonLabel();
    updateStatusText("Requesting remote control...");
    manager.sendMessageFn({ type: "control_request", auto_trust: true });
  }

  function bindSocket(sendFn) {
    manager.sendMessageFn = sendFn;
    updateButtonLabel();
  }

  function setViewerId(id) {
    manager.viewerId = id;
  }

  function handleStatus(payload) {
    const status = payload.status;
    const isController = payload.controller_id && payload.controller_id === manager.viewerId;

    if (status === "granted" && isController && payload.token) {
      manager.active = true;
      manager.token = payload.token;
      manager.pending = false;
      updateStatusText("Remote control granted");
      startTracking();
    } else if (status === "denied") {
      manager.active = false;
      manager.token = null;
      manager.pending = false;
      stopTracking();
      updateStatusText("Remote control denied");
    } else if (status === "disabled" || status === "agent_missing" || status === "token_mismatch") {
      manager.active = false;
      manager.token = null;
      manager.pending = false;
      stopTracking();
      updateStatusText(payload.message || "Remote control disabled");
    } else if (status === "already_active" || status === "pending") {
      manager.pending = false;
      updateStatusText(payload.message || "Remote control unavailable");
    }

    updateButtonLabel();
  }

  function startTracking() {
    if (!manager.videoElement || manager.active === false) {
      return;
    }

    manager.videoElement.addEventListener("mousemove", handleMouseMove);
    manager.videoElement.addEventListener("mousedown", handleMouseDown);
    manager.videoElement.addEventListener("mouseup", handleMouseUp);
    manager.videoElement.addEventListener("wheel", handleWheel, { passive: false });
    window.addEventListener("keydown", handleKeyDown, keyListenerOptions);
    window.addEventListener("keyup", handleKeyUp, keyListenerOptions);
    document.addEventListener("pointerlockchange", handlePointerLockChange);
    document.addEventListener("pointerlockerror", handlePointerLockError);

    // Request pointer lock so the browser stops intercepting Ctrl+W, F5, Ctrl+T, etc.
    // This is what makes keyboard shortcuts work like TeamViewer/AnyDesk.
    manager.virtualX = 0.5;
    manager.virtualY = 0.5;
    manager.videoElement.requestPointerLock({ unadjustedMovement: true }).catch(() => {
      // unadjustedMovement not supported on all platforms — fall back to normal pointer lock
      manager.videoElement.requestPointerLock().catch(() => {});
    });
  }

  function stopTracking() {
    if (!manager.videoElement) {
      return;
    }

    manager.videoElement.removeEventListener("mousemove", handleMouseMove);
    manager.videoElement.removeEventListener("mousedown", handleMouseDown);
    manager.videoElement.removeEventListener("mouseup", handleMouseUp);
    manager.videoElement.removeEventListener("wheel", handleWheel);
    window.removeEventListener("keydown", handleKeyDown, keyListenerOptions);
    window.removeEventListener("keyup", handleKeyUp, keyListenerOptions);
    document.removeEventListener("pointerlockchange", handlePointerLockChange);
    document.removeEventListener("pointerlockerror", handlePointerLockError);

    if (document.pointerLockElement) {
      document.exitPointerLock();
    }
    manager.pointerLocked = false;

    if (manager.mouseRAF) {
      cancelAnimationFrame(manager.mouseRAF);
      manager.mouseRAF = null;
      manager.pendingMouseEvent = null;
    }
    manager.mousedownInVideo = false;
  }

  function handlePointerLockChange() {
    manager.pointerLocked = (document.pointerLockElement === manager.videoElement);
    if (manager.pointerLocked) {
      // Show subtle overlay on video so user knows they're in locked mode
      if (manager.videoElement) manager.videoElement.style.cursor = "none";
      updateStatusText("Remote control active — press Alt+Z to unlock mouse");
    } else {
      if (manager.videoElement) manager.videoElement.style.cursor = "";
      if (manager.active) {
        updateStatusText("Remote control active (click video to re-lock mouse)");
        // Re-request pointer lock on next click
        if (manager.videoElement) {
          manager.videoElement.addEventListener("click", _reLockOnClick, { once: true });
        }
      }
    }
  }

  function handlePointerLockError() {
    // Pointer lock failed (e.g. document not focused, or feature-policy blocked)
    // Fall back gracefully — keyboard capture via capture:true still works for most keys
    manager.pointerLocked = false;
    if (manager.active) updateStatusText("Remote control active (use shortcut buttons for Ctrl+W, Alt+Tab)");
  }

  function _reLockOnClick() {
    if (manager.active && manager.videoElement) {
      manager.videoElement.requestPointerLock({ unadjustedMovement: true }).catch(() => {
        manager.videoElement.requestPointerLock().catch(() => {});
      });
    }
  }

  function getVideoContentRect(video) {
    // Account for object-fit: contain letterboxing.
    // The video element fills the container but the actual content has black bars.
    const rect = video.getBoundingClientRect();
    const vw = video.videoWidth || rect.width;
    const vh = video.videoHeight || rect.height;
    if (!vw || !vh) return rect;

    const elemAspect = rect.width / rect.height;
    const vidAspect = vw / vh;
    let contentW, contentH, offsetX, offsetY;

    if (elemAspect > vidAspect) {
      // Letterbox: black bars on left/right
      contentH = rect.height;
      contentW = rect.height * vidAspect;
      offsetX = (rect.width - contentW) / 2;
      offsetY = 0;
    } else {
      // Pillarbox: black bars on top/bottom
      contentW = rect.width;
      contentH = rect.width / vidAspect;
      offsetX = 0;
      offsetY = (rect.height - contentH) / 2;
    }
    return { left: rect.left + offsetX, top: rect.top + offsetY, width: contentW, height: contentH };
  }

  function handleMouseMove(event) {
    if (!manager.active || !manager.videoElement) {
      return;
    }

    if (manager.pointerLocked) {
      // Pointer lock mode: accumulate relative deltas into virtual cursor position
      const rect = manager.videoElement.getBoundingClientRect();
      const w = rect.width  || window.innerWidth;
      const h = rect.height || window.innerHeight;
      manager.virtualX = Math.min(1, Math.max(0, manager.virtualX + event.movementX / w));
      manager.virtualY = Math.min(1, Math.max(0, manager.virtualY + event.movementY / h));

      if (manager.mouseRAF) return;
      const vx = manager.virtualX, vy = manager.virtualY;
      manager.mouseRAF = requestAnimationFrame(() => {
        manager.mouseRAF = null;
        sendControl({ type: "control", action: "mouse_move", x_ratio: manager.virtualX, y_ratio: manager.virtualY });
      });
      return;
    }

    // Absolute mode (pointer lock not active)
    manager.pendingMouseEvent = event;
    if (manager.mouseRAF) {
      return;
    }

    manager.mouseRAF = requestAnimationFrame(() => {
      manager.mouseRAF = null;
      if (!manager.pendingMouseEvent) {
        return;
      }
      const rect = getVideoContentRect(manager.videoElement);
      if (rect.width === 0 || rect.height === 0) {
        manager.pendingMouseEvent = null;
        return;
      }
      const normalizedX = Math.min(Math.max((manager.pendingMouseEvent.clientX - rect.left) / rect.width, 0), 1);
      const normalizedY = Math.min(Math.max((manager.pendingMouseEvent.clientY - rect.top) / rect.height, 0), 1);
      sendControl({ type: "control", action: "mouse_move", x_ratio: normalizedX, y_ratio: normalizedY });
      manager.pendingMouseEvent = null;
    });
  }

  function handleMouseDown(event) {
    if (!manager.active) return;
    manager.mousedownInVideo = true;
    event.preventDefault();
    sendControl({ type: "control", action: "mouse_down", button: event.button });
  }

  function handleMouseUp(event) {
    if (!manager.active) return;
    if (!manager.mousedownInVideo) return;
    event.preventDefault();
    sendControl({ type: "control", action: "mouse_up", button: event.button });
    manager.mousedownInVideo = false;
  }

  function handleWheel(event) {
    if (!manager.active) {
      return;
    }
    event.preventDefault();
    sendControl({ type: "control", action: "scroll", deltaX: event.deltaX, deltaY: event.deltaY });
  }

  function handleKeyDown(event) {
    if (!manager.active) return;

    // Alt+Z = unlock mouse (exit pointer lock) without dropping remote control
    if (event.altKey && event.code === "KeyZ") {
      if (document.pointerLockElement) document.exitPointerLock();
      return;
    }

    event.preventDefault();
    event.stopPropagation();

    if (event.repeat) return;

    const mappedKey = manager.keyMap[event.key] || event.key;
    sendControl({ type: "control", action: "key_down", key: mappedKey });
  }

  function handleKeyUp(event) {
    if (!manager.active) return;
    if (event.altKey && event.code === "KeyZ") return;

    event.preventDefault();
    event.stopPropagation();

    const mappedKey = manager.keyMap[event.key] || event.key;
    sendControl({ type: "control", action: "key_up", key: mappedKey });
  }

  function reset() {
    manager.active = false;
    manager.pending = false;
    manager.token = null;
    stopTracking();
    updateButtonLabel();
    updateStatusText();
  }

  function init(options) {
    manager.videoElement = options.video;
    manager.buttonElement = options.button;
    manager.statusElement = options.status;

    if (manager.buttonElement) {
      manager.buttonElement.addEventListener("click", requestControl);
    }

    updateStatusText();
    updateButtonLabel();
    return {
      bindSocket,
      setViewerId,
      handleStatus,
      reset,
      sendControl,
      get active() { return manager.active; },
      get token()  { return manager.token; },
    };
  }

  window.RemoteControlManager = { init };
})();
