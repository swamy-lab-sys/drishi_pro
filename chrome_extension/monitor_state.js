const MON_DEFAULT_SETTINGS = {
  monitoring: false,
  sessionId: "default",
  monitorServerUrl: "", // Derived from extension Server URL on connect
  screenEnabled: false,
  connectionStatus: "disconnected",
  reconnectAttempt: 0,
  lastError: "",
  lastHeartbeatAt: null,
  screenStatus: "idle",
  streamViewerCount: 0,
  remoteControlStatus: "idle",
  remoteControllerId: null,
  remoteControlError: "",
};

const MON_RECONNECT_DELAYS = [5000, 10000, 30000];
const MON_HEARTBEAT_INTERVAL = 20000;
const MON_EVENT_QUEUE_LIMIT = 200;
const MON_RTC_CONFIGURATION = {
  iceServers: [{ urls: ["stun:stun.l.google.com:19302"] }],
};
