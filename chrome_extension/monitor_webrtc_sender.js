class WebRTCSender {
  constructor({ onSignal, onStatusChange, onViewerCountChange }) {
    this.onSignal = onSignal;
    this.onStatusChange = onStatusChange;
    this.onViewerCountChange = onViewerCountChange;
    this.stream = null;
    this.peers = new Map();
    this.pendingViewerIds = new Set();
  }

  async setStream(stream) {
    this.stopStream();
    this.stream = stream;
    this.onStatusChange("streaming");

    const [videoTrack] = stream.getVideoTracks();
    if (videoTrack) {
      videoTrack.addEventListener("ended", () => {
        this.stopStream();
        this.onStatusChange("stopped");
      });
      // "detail" = mixed desktop content (text + graphics); best for remote desktop sharing
      if (videoTrack.contentHint !== undefined) {
        videoTrack.contentHint = "detail";
      }
    }

    for (const viewerId of [...this.pendingViewerIds]) {
      this.pendingViewerIds.delete(viewerId);
      this.createOffer(viewerId).catch((error) => {
        console.debug("Could not create deferred offer:", viewerId, error);
      });
    }
  }

  stopStream() {
    for (const viewerId of this.peers.keys()) {
      this.closePeer(viewerId);
    }

    if (this.stream) {
      this.stream.getTracks().forEach((track) => track.stop());
      this.stream = null;
    }

    this.onViewerCountChange(0);
  }

  async ensurePeer(viewerId) {
    if (!this.stream) {
      this.pendingViewerIds.add(viewerId);
      throw new Error("No screen stream is available for WebRTC.");
    }

    if (this.peers.has(viewerId)) {
      return this.peers.get(viewerId).pc;
    }

    const pc = new RTCPeerConnection(MON_RTC_CONFIGURATION);
    this.stream.getTracks().forEach((track) => {
      pc.addTrack(track, this.stream);
    });

    pc.onicecandidate = (event) => {
      if (!event.candidate) {
        return;
      }

      this.onSignal("ice_candidate", viewerId, event.candidate);
    };

    pc.onconnectionstatechange = () => {
      const state = pc.connectionState;
      if (state === "failed" || state === "closed" || state === "disconnected") {
        this.closePeer(viewerId);
      }
    };

    this.peers.set(viewerId, { pc });
    this.onViewerCountChange(this.peers.size);
    return pc;
  }

  async createOffer(viewerId) {
    if (!this.stream) {
      this.pendingViewerIds.add(viewerId);
      return;
    }

    const pc = await this.ensurePeer(viewerId);
    const offer = await pc.createOffer({
      offerToReceiveAudio: false,
      offerToReceiveVideo: false,
    });
    await pc.setLocalDescription(offer);
    this.onSignal("offer", viewerId, pc.localDescription);
  }

  async handleAnswer(viewerId, answer) {
    const peerEntry = this.peers.get(viewerId);
    if (!peerEntry) {
      return;
    }

    await peerEntry.pc.setRemoteDescription(new RTCSessionDescription(answer));
  }

  async handleIceCandidate(viewerId, candidate) {
    const peerEntry = this.peers.get(viewerId);
    if (!peerEntry) {
      return;
    }

    await peerEntry.pc.addIceCandidate(new RTCIceCandidate(candidate));
  }

  closePeer(viewerId) {
    this.pendingViewerIds.delete(viewerId);

    const peerEntry = this.peers.get(viewerId);
    if (!peerEntry) {
      return;
    }

    peerEntry.pc.onicecandidate = null;
    peerEntry.pc.onconnectionstatechange = null;
    peerEntry.pc.close();
    this.peers.delete(viewerId);
    this.onViewerCountChange(this.peers.size);
  }

  closeAll() {
    this.pendingViewerIds.clear();
    for (const viewerId of [...this.peers.keys()]) {
      this.closePeer(viewerId);
    }
    this.stopStream();
  }
}
