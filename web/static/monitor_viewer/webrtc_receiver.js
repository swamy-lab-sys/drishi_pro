class WebRTCReceiver {
  constructor({ sessionId, viewerId, sendSignal, onRemoteStream, onStateChange }) {
    this.sessionId = sessionId;
    this.viewerId = viewerId;
    this.sendSignal = sendSignal;
    this.onRemoteStream = onRemoteStream;
    this.onStateChange = onStateChange;
    this.pc = null;
    this.remoteStream = null;
  }

  ensurePeer() {
    if (this.pc) {
      return this.pc;
    }

    this.pc = new RTCPeerConnection(RTC_CONFIGURATION);
    this.remoteStream = new MediaStream();
    this.onRemoteStream(this.remoteStream);

    this.pc.ontrack = (event) => {
      event.streams[0].getTracks().forEach((track) => {
        this.remoteStream.addTrack(track);
      });
      this.onStateChange("streaming");
    };

    this.pc.onicecandidate = (event) => {
      if (!event.candidate) {
        return;
      }

      this.sendSignal("ice_candidate", {
        viewer_id: this.viewerId,
        data: event.candidate,
      });
    };

    this.pc.onconnectionstatechange = () => {
      const state = this.pc.connectionState;
      if (state === "failed" || state === "disconnected") {
        this.onStateChange("reconnecting");
      }
      if (state === "closed") {
        this.onStateChange("stopped");
      }
    };

    return this.pc;
  }

  async handleOffer(offer) {
    const pc = this.ensurePeer();
    await pc.setRemoteDescription(new RTCSessionDescription(offer));
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);
    this.sendSignal("answer", {
      viewer_id: this.viewerId,
      data: pc.localDescription,
    });
    this.onStateChange("answer_sent");
  }

  async handleIceCandidate(candidate) {
    if (!this.pc) {
      return;
    }

    await this.pc.addIceCandidate(new RTCIceCandidate(candidate));
  }

  reset() {
    if (this.pc) {
      this.pc.ontrack = null;
      this.pc.onicecandidate = null;
      this.pc.onconnectionstatechange = null;
      this.pc.close();
      this.pc = null;
    }

    if (this.remoteStream) {
      this.remoteStream.getTracks().forEach((track) => track.stop());
      this.remoteStream = null;
    }

    this.onStateChange("idle");
  }
}
