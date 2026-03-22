// Drishi Enterprise — Audio Capture Offscreen Document
// Mode A (sarvamKey set):  accumulate audio → silence detection → Sarvam AI STT → send text JSON
// Mode B (no sarvamKey):   stream raw PCM-16 binary to /ws/audio (original behavior)
//
// Uses AudioWorklet (no deprecation warnings) with ScriptProcessorNode fallback.
// Performance tuned: 64ms chunks, 1.0s silence, en-IN Sarvam (no auto-detect), abort stale requests.

const SAMPLE_RATE       = 16000;
const BUFFER_SIZE       = 1024; // 64ms per callback at 16kHz (was 256ms/4096)
const WS_PING_INTERVAL_MS = 15000; // keepalive ping to prevent 1005 disconnects
const WS_CONNECT_TIMEOUT  = 4000;  // ms (was 8000)

let audioCtx          = null;
let wsConn            = null;
let wsServerUrl       = null;  // stored for reconnect
let wsUserToken       = null;
let wsSecretCode      = null;
let activeStream      = null;
let processor         = null;  // AudioWorkletNode or ScriptProcessorNode
let audioElement      = null;  // passthrough: keeps tab audio audible while capturing
let wsPingTimer       = null;  // keepalive interval
let sarvamAbortCtrl   = null;  // AbortController for in-flight Sarvam request

// ── Message handler ───────────────────────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === 'audio_start_capture') {
    startCapture(msg.streamId, msg.serverUrl, msg.secretCode, msg.sarvamKey, msg.userToken || '')
      .then(() => sendResponse({ ok: true }))
      .catch(e => {
        stopCapture();
        sendResponse({ ok: false, error: e.message });
      });
    return true; // async
  }
  if (msg.type === 'audio_stop_capture') {
    stopCapture();
    sendResponse({ ok: true });
  }
  if (msg.type === 'audio_ping') {
    sendResponse({ ok: true, streaming: !!activeStream });
  }
});

// ── WAV encoder (PCM-16 mono) ─────────────────────────────────────────────────
function encodePcm16ToWav(int16Samples, sampleRate) {
  const numSamples = int16Samples.length;
  const buffer = new ArrayBuffer(44 + numSamples * 2);
  const view   = new DataView(buffer);
  const writeStr = (offset, str) => {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  };
  writeStr(0, 'RIFF');
  view.setUint32(4, 36 + numSamples * 2, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);   // PCM
  view.setUint16(22, 1, true);   // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);   // block align
  view.setUint16(34, 16, true);  // bits per sample
  writeStr(36, 'data');
  view.setUint32(40, numSamples * 2, true);
  new Int16Array(buffer, 44).set(int16Samples);
  return buffer;
}

// ── Float32 → Int16 conversion ────────────────────────────────────────────────
function float32ToPcm16(float32) {
  const pcm16 = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    pcm16[i] = Math.max(-32768, Math.min(32767, Math.round(float32[i] * 32767)));
  }
  return pcm16;
}

// ── Sarvam AI transcription ───────────────────────────────────────────────────
async function transcribeWithSarvam(int16Samples, sarvamKey, signal) {
  const wavBuffer = encodePcm16ToWav(int16Samples, SAMPLE_RATE);
  const blob = new Blob([wavBuffer], { type: 'audio/wav' });
  const formData = new FormData();
  formData.append('file', blob, 'audio.wav');
  formData.append('model', 'saarika:v2.5');
  formData.append('language_code', 'en-IN');  // fixed → skips auto-detect (~400ms saved)
  formData.append('with_timestamps', 'false');

  // Use passed-in AbortController so caller can cancel a stale in-flight request
  const resp = await fetch('https://api.sarvam.ai/speech-to-text', {
    method: 'POST',
    headers: { 'api-subscription-key': sarvamKey },
    body: formData,
    signal: signal || null,
  });

  if (!resp.ok) {
    const errText = await resp.text().catch(() => '');
    throw new Error(`Sarvam API ${resp.status}: ${errText}`);
  }
  const result = await resp.json();
  return (result.transcript || '').trim();
}

// ── Start capture ─────────────────────────────────────────────────────────────
async function startCapture(streamId, serverUrl, secretCode, sarvamKey, userToken = '') {
  stopCapture();
  // Store for WS reconnect
  wsServerUrl  = serverUrl;
  wsSecretCode = secretCode;
  wsUserToken  = userToken;

  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } },
    video: false,
  });
  activeStream = stream;

  // ── WebSocket connection ────────────────────────────────────────────────────
  const base   = (serverUrl || '').replace(/\/$/, '');
  const wsBase = base.replace(/^https/, 'wss').replace(/^http/, 'ws');
  // Build query string with optional secret code and user token
  const params = new URLSearchParams({ 'ngrok-skip-browser-warning': '1' });
  if (secretCode) params.set('token', secretCode);
  if (userToken)  params.set('user_token', userToken);
  const wsUrl  = `${wsBase}/ws/audio?${params.toString()}`;

  wsConn = new WebSocket(wsUrl);
  wsConn.binaryType = 'arraybuffer';

  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('WebSocket connection timed out (4s)')), WS_CONNECT_TIMEOUT);
    wsConn.onopen = () => {
      clearTimeout(timer);
      console.log('[Drishi] WS connected →', wsUrl);
      // Start audio passthrough so user can still hear the meeting
      audioElement = new Audio();
      audioElement.srcObject = stream;
      audioElement.play().catch(e => console.warn('[Drishi] Audio passthrough muted (autoplay):', e));
      resolve();
    };
    wsConn.onerror = () => { clearTimeout(timer); reject(new Error('WebSocket connection failed')); };
  });

  // Keepalive ping — prevents 1005 "No Status Received" drops on idle connections
  wsPingTimer = setInterval(() => {
    if (wsConn && wsConn.readyState === WebSocket.OPEN) {
      wsConn.send(JSON.stringify({ type: 'ping' }));
    }
  }, WS_PING_INTERVAL_MS);

  wsConn.onclose = (evt) => {
    console.log(`[Drishi] WS closed (${evt.code})`);
    clearInterval(wsPingTimer);
    wsPingTimer = null;
    // Auto-reconnect on unexpected close (not 1000 = normal stop)
    if (evt.code !== 1000 && activeStream && wsServerUrl) {
      console.log('[Drishi] WS dropped unexpectedly — reconnecting in 1.5s...');
      setTimeout(() => reconnectWs(), 1500);
    } else {
      stopCapture();
      chrome.runtime.sendMessage({ type: 'audio_status', status: 'stopped', reason: 'ws_closed' }).catch(() => {});
    }
  };

  // ── Audio context ───────────────────────────────────────────────────────────
  audioCtx = new AudioContext({ sampleRate: SAMPLE_RATE });

  // Google Meet (and other conferencing tabs) may suspend the AudioContext due to
  // Chrome's autoplay policy. Resume explicitly so audio processing actually runs.
  if (audioCtx.state === 'suspended') {
    console.log('[Drishi] AudioContext suspended — resuming for Google Meet...');
    await audioCtx.resume();
  }
  audioCtx.addEventListener('statechange', async () => {
    if (audioCtx && audioCtx.state === 'suspended') {
      console.log('[Drishi] AudioContext re-suspended — resuming...');
      try { await audioCtx.resume(); } catch (_) {}
    }
  });

  const source = audioCtx.createMediaStreamSource(stream);

  // ── Mode A state (Sarvam client-side STT) ──────────────────────────────────
  const SILENCE_THRESHOLD  = 0.008;
  const SILENCE_CHUNKS_END = 16;      // 16 × 64ms = 1.0s of silence → flush (was 6×256ms=1.5s)
  const MIN_SPEECH_SAMPLES = 8000;    // 0.5s minimum (was 0.75s/12000)
  const MAX_BUFFER_SAMPLES = 96000;   // 6s safety cap

  let pcmBuffer     = [];
  let bufferSamples = 0;
  let inSpeech      = false;
  let silenceCount  = 0;
  let transcribing  = false;

  async function flushBuffer() {
    if (bufferSamples < MIN_SPEECH_SAMPLES) {
      pcmBuffer = []; bufferSamples = 0; inSpeech = false; silenceCount = 0;
      return;
    }
    // Cancel any stale in-flight request before starting new one
    if (sarvamAbortCtrl) {
      sarvamAbortCtrl.abort();
    }
    sarvamAbortCtrl = new AbortController();
    const currentAbort = sarvamAbortCtrl;

    transcribing = true;
    const total = new Int16Array(bufferSamples);
    let offset = 0;
    for (const chunk of pcmBuffer) { total.set(chunk, offset); offset += chunk.length; }
    pcmBuffer = []; bufferSamples = 0; inSpeech = false; silenceCount = 0;
    try {
      console.log(`[Drishi] Sarvam STT: ${total.length} samples (${(total.length / SAMPLE_RATE).toFixed(1)}s)`);
      const text = await transcribeWithSarvam(total, sarvamKey, currentAbort.signal);
      if (currentAbort.signal.aborted) return; // newer segment superseded this one
      console.log(`[Drishi] Sarvam result: "${text}"`);
      if (text && text.length >= 4 && wsConn && wsConn.readyState === WebSocket.OPEN) {
        wsConn.send(JSON.stringify({ type: 'text_question', text }));
      }
    } catch (e) {
      if (e.name !== 'AbortError') console.error('[Drishi] Sarvam error:', e.message);
    } finally {
      transcribing = false;
      if (sarvamAbortCtrl === currentAbort) sarvamAbortCtrl = null;
    }
  }

  // ── Per-chunk processor — called by both AudioWorklet and ScriptProcessorNode
  function processChunk(float32) {
    if (!wsConn || wsConn.readyState !== WebSocket.OPEN) return;

    if (sarvamKey) {
      // Mode A: accumulate + silence detection → Sarvam AI STT
      let sum = 0;
      for (let i = 0; i < float32.length; i++) sum += float32[i] * float32[i];
      const rms      = Math.sqrt(sum / float32.length);
      const isSilent = rms < SILENCE_THRESHOLD;
      const pcm16    = float32ToPcm16(float32);

      if (!isSilent) {
        inSpeech = true; silenceCount = 0;
      } else if (inSpeech) {
        silenceCount++;
      }

      pcmBuffer.push(pcm16);
      bufferSamples += pcm16.length;

      const endOfSpeech = inSpeech && silenceCount >= SILENCE_CHUNKS_END;
      const bufferFull  = bufferSamples >= MAX_BUFFER_SAMPLES;
      if ((endOfSpeech || bufferFull) && !transcribing) {
        flushBuffer();
      }
    } else {
      // Mode B: raw PCM-16 streaming to server STT
      const pcm16 = float32ToPcm16(float32);
      wsConn.send(pcm16.buffer);
    }
  }

  // ── Audio pipeline: AudioWorklet (preferred) with ScriptProcessorNode fallback
  let useWorklet = false;
  try {
    await audioCtx.audioWorklet.addModule(
      chrome.runtime.getURL('audio_processor_worklet.js')
    );
    const workletNode = new AudioWorkletNode(audioCtx, 'drishi-audio-capture');
    workletNode.port.onmessage = (e) => processChunk(e.data);
    source.connect(workletNode);
    workletNode.connect(audioCtx.destination);
    processor  = workletNode;
    useWorklet = true;
    console.log('[Drishi] AudioWorklet active (sarvam=' + !!sarvamKey + ')');
  } catch (e) {
    console.warn('[Drishi] AudioWorklet unavailable, using ScriptProcessorNode fallback:', e.message);
    const spn = audioCtx.createScriptProcessor(BUFFER_SIZE, 1, 1);
    spn.onaudioprocess = (ev) => processChunk(ev.inputBuffer.getChannelData(0));
    source.connect(spn);
    spn.connect(audioCtx.destination);
    processor = spn;
  }

  chrome.runtime.sendMessage({ type: 'audio_status', status: 'streaming' }).catch(() => {});
  console.log(`[Drishi] Capturing tab audio at ${SAMPLE_RATE / 1000}kHz | mode=${sarvamKey ? 'Sarvam STT' : 'raw PCM'} | engine=${useWorklet ? 'AudioWorklet' : 'ScriptProcessorNode'}`);
}

// ── WS reconnect (called when WS drops but stream is still active) ────────────
async function reconnectWs() {
  if (!activeStream || !wsServerUrl) return;
  try {
    const base   = wsServerUrl.replace(/\/$/, '');
    const wsBase = base.replace(/^https/, 'wss').replace(/^http/, 'ws');
    const params = new URLSearchParams({ 'ngrok-skip-browser-warning': '1' });
    if (wsSecretCode) params.set('token', wsSecretCode);
    if (wsUserToken)  params.set('user_token', wsUserToken);
    const wsUrl = `${wsBase}/ws/audio?${params.toString()}`;

    wsConn = new WebSocket(wsUrl);
    wsConn.binaryType = 'arraybuffer';
    await new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error('reconnect timeout')), WS_CONNECT_TIMEOUT);
      wsConn.onopen  = () => { clearTimeout(t); resolve(); };
      wsConn.onerror = () => { clearTimeout(t); reject(new Error('reconnect failed')); };
    });
    // Re-attach close handler
    wsConn.onclose = (evt) => {
      clearInterval(wsPingTimer); wsPingTimer = null;
      if (evt.code !== 1000 && activeStream && wsServerUrl) {
        setTimeout(() => reconnectWs(), 1500);
      }
    };
    // Restart ping
    wsPingTimer = setInterval(() => {
      if (wsConn && wsConn.readyState === WebSocket.OPEN) {
        wsConn.send(JSON.stringify({ type: 'ping' }));
      }
    }, WS_PING_INTERVAL_MS);
    console.log('[Drishi] WS reconnected →', wsUrl);
  } catch (e) {
    console.error('[Drishi] WS reconnect failed:', e.message);
    chrome.runtime.sendMessage({ type: 'audio_status', status: 'stopped', reason: 'reconnect_failed' }).catch(() => {});
  }
}

// ── Stop capture ──────────────────────────────────────────────────────────────
function stopCapture() {
  if (sarvamAbortCtrl) { sarvamAbortCtrl.abort(); sarvamAbortCtrl = null; }
  if (wsPingTimer)  { clearInterval(wsPingTimer); wsPingTimer = null; }
  if (audioElement) { try { audioElement.pause(); audioElement.srcObject = null; } catch (_) {} audioElement = null; }
  if (processor)    { try { processor.disconnect(); } catch (_) {} processor    = null; }
  if (audioCtx)     { try { audioCtx.close();       } catch (_) {} audioCtx     = null; }
  if (wsConn)       { try { wsConn.close(1000, 'stop'); } catch (_) {} wsConn   = null; }
  if (activeStream) { activeStream.getTracks().forEach(t => t.stop()); activeStream = null; }
  wsServerUrl = null; wsSecretCode = null; wsUserToken = null;
}
