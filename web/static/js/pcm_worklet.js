// Drishi — AudioWorklet for phone mic capture in monitor page
// Same logic as chrome_extension/audio_processor_worklet.js
const CHUNK_SIZE = 1024; // 64ms @ 16kHz

class DrishiPcmCapture extends AudioWorkletProcessor {
  constructor() { super(); this._buf = []; this._bufLen = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch || !ch.length) return true;
    this._buf.push(new Float32Array(ch));
    this._bufLen += ch.length;
    while (this._bufLen >= CHUNK_SIZE) {
      const out = new Float32Array(CHUNK_SIZE);
      let written = 0;
      while (written < CHUNK_SIZE) {
        const head = this._buf[0], need = CHUNK_SIZE - written;
        if (head.length <= need) {
          out.set(head, written); written += head.length;
          this._bufLen -= head.length; this._buf.shift();
        } else {
          out.set(head.subarray(0, need), written);
          this._buf[0] = head.subarray(need);
          this._bufLen -= need; written += need;
        }
      }
      this.port.postMessage(out, [out.buffer]);
    }
    return true;
  }
}
registerProcessor('drishi-pcm-capture', DrishiPcmCapture);
