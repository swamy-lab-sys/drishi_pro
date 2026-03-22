// Drishi Enterprise — AudioWorklet Processor
// Replaces the deprecated ScriptProcessorNode.
// Accumulates 128-sample render quanta into CHUNK_SIZE (4096) blocks,
// then transfers them to the main thread via a zero-copy postMessage.

const CHUNK_SIZE = 4096;

class AudioCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buf = [];
    this._bufLen = 0;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch || !ch.length) return true;

    // Copy the 128-sample render quantum into our accumulation buffer
    this._buf.push(new Float32Array(ch));
    this._bufLen += ch.length;

    // Emit complete CHUNK_SIZE blocks
    while (this._bufLen >= CHUNK_SIZE) {
      const out = new Float32Array(CHUNK_SIZE);
      let written = 0;
      while (written < CHUNK_SIZE) {
        const head = this._buf[0];
        const need = CHUNK_SIZE - written;
        if (head.length <= need) {
          out.set(head, written);
          written    += head.length;
          this._bufLen -= head.length;
          this._buf.shift();
        } else {
          out.set(head.subarray(0, need), written);
          this._buf[0] = head.subarray(need);
          this._bufLen -= need;
          written      += need;
        }
      }
      // Transfer ownership (zero-copy) to avoid GC pressure
      this.port.postMessage(out, [out.buffer]);
    }
    return true; // keep processor alive
  }
}

registerProcessor('drishi-audio-capture', AudioCaptureProcessor);
