// ICE configuration — STUN for local/same-ISP, TURN relay for cross-NAT (cross-city/ISP).
// Without TURN, WebRTC fails when both peers are behind different NAT (e.g. Bangalore ↔ Hyderabad).
const MON_RTC_CONFIGURATION = {
  iceServers: [
    // STUN — discovers public IP (works if both on same ISP/network)
    { urls: ["stun:stun.l.google.com:19302"] },
    { urls: ["stun:stun1.l.google.com:19302"] },
    { urls: ["stun:global.stun.twilio.com:3478"] },
    // TURN relay — required for cross-NAT (different cities/ISPs)
    // Replace with your own coTURN or paid TURN (Twilio/metered.ca) for production
    {
      urls: [
        "turn:openrelay.metered.ca:80",
        "turn:openrelay.metered.ca:443",
        "turn:openrelay.metered.ca:443?transport=tcp",
      ],
      username: "openrelayproject",
      credential: "openrelayproject",
    },
  ],
};
