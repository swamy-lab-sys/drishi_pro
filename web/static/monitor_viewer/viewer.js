const sessionInput = document.getElementById("session-id");
const connectButton = document.getElementById("connect-btn");
const connectionStatus = document.getElementById("connection-status");
const activeUrl = document.getElementById("active-url");
const lastClick = document.getElementById("last-click");
const lastKey = document.getElementById("last-key");
const lastMouseMove = document.getElementById("last-mousemove");
const activityLog = document.getElementById("activity-log");
const screenPreview = document.getElementById("screen-preview");
const emptyScreen = document.getElementById("empty-screen");

let socket = null;

function websocketUrl() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws/monitor`;
}

function setStatus(text, isConnected = false) {
  connectionStatus.textContent = text;
  connectionStatus.style.color = isConnected ? "#2f7d32" : "#6f6254";
}

function prependLog(message) {
  const item = document.createElement("li");
  item.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
  activityLog.prepend(item);

  while (activityLog.children.length > 100) {
    activityLog.removeChild(activityLog.lastChild);
  }
}

function describeEvent(event) {
  switch (event.type) {
    case "tab_change":
      return `Tab changed to ${event.url || "unknown page"}`;
    case "page_loaded":
      return `Page loaded: ${event.url || "unknown page"}`;
    case "click":
      return `Click at (${event.x}, ${event.y})`;
    case "keydown":
      return `Key pressed: ${event.key}`;
    case "mousemove":
      return `Mouse moved to (${event.x}, ${event.y})`;
    case "screen_frame":
      return "Screen frame received";
    default:
      return `${event.type} event`;
  }
}

function applyState(state) {
  activeUrl.textContent = state.activeUrl || "Waiting for data...";
  lastClick.textContent = state.lastClick
    ? `${state.lastClick.x}, ${state.lastClick.y}`
    : "-";
  lastKey.textContent = state.lastKey || "-";
  lastMouseMove.textContent = state.lastMouseMove
    ? `${state.lastMouseMove.x}, ${state.lastMouseMove.y}`
    : "-";

  if (state.latestFrame) {
    screenPreview.src = state.latestFrame;
    screenPreview.style.display = "block";
    emptyScreen.style.display = "none";
  }
}

function handleEvent(event) {
  if (event.url) {
    activeUrl.textContent = event.url;
  }

  if (event.type === "click") {
    lastClick.textContent = `${event.x}, ${event.y}`;
  }

  if (event.type === "keydown") {
    lastKey.textContent = event.key;
  }

  if (event.type === "mousemove") {
    lastMouseMove.textContent = `${event.x}, ${event.y}`;
  }

  if (event.type === "screen_frame" && event.frame) {
    screenPreview.src = event.frame;
    screenPreview.style.display = "block";
    emptyScreen.style.display = "none";
  }

  prependLog(describeEvent(event));
}

function connectViewer() {
  if (socket) {
    socket.close();
  }

  setStatus("Connecting...");
  socket = new WebSocket(websocketUrl());

  socket.addEventListener("open", () => {
    const sessionId = sessionInput.value.trim() || "default";
    socket.send(
      JSON.stringify({
        type: "register",
        role: "viewer",
        sessionId,
      })
    );
    setStatus(`Connected to ${sessionId}`, true);
    activityLog.innerHTML = "";
  });

  socket.addEventListener("message", (messageEvent) => {
    const payload = JSON.parse(messageEvent.data);

    if (payload.type === "session_snapshot") {
      applyState(payload.state || {});
      (payload.logs || []).forEach((event) => prependLog(describeEvent(event)));
      return;
    }

    handleEvent(payload);
  });

  socket.addEventListener("close", () => {
    setStatus("Disconnected");
  });

  socket.addEventListener("error", () => {
    setStatus("Connection error");
  });
}

connectButton.addEventListener("click", connectViewer);
window.addEventListener("load", connectViewer);
