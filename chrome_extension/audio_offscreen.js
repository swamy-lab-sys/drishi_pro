// Drishi Enterprise — Audio Capture Offscreen Document
// Mode A (sarvamKey set):  accumulate audio → silence detection → Sarvam AI STT → send text JSON
// Mode B (no sarvamKey):   stream raw PCM-16 binary to /ws/audio (original behavior)
//
// Uses AudioWorklet (no deprecation warnings) with ScriptProcessorNode fallback.
// Performance tuned: 64ms chunks, 1.0s silence, en-IN Sarvam (no auto-detect), abort stale requests.

const SAMPLE_RATE       = 16000;
const BUFFER_SIZE       = 1024; // 64ms per callback at 16kHz (was 256ms/4096)
const WS_PING_INTERVAL_MS = 15000; // keepalive ping to prevent 1005 disconnects
const WS_CONNECT_TIMEOUT  = 10000;  // ms — ngrok adds ~1-2s latency, need more headroom

// ── Remote logging ────────────────────────────────────────────────────────────
let _rlogUrl   = '';
let _rlogToken = '';
function rlog(msg, level = 'info') {
  console.log(`[offscreen] ${msg}`);
  if (!_rlogUrl) return;
  fetch(`${_rlogUrl}/api/ext/log`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': 'true' },
    body: JSON.stringify({ source: 'offscreen', msg: String(msg), level, token: _rlogToken }),
  }).catch(() => {});
}
// RMS log throttle: log audio stats every N chunks to avoid spamming
let _rmsLogCount = 0;

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
    startCapture(msg.streamId, msg.serverUrl, msg.secretCode, msg.sarvamKey, msg.userToken || '', msg.captureMode || 'tab')
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

// ── POST fallback for Sarvam transcripts (no WS needed) ──────────────────────
// Used when WS fails/times out. Sends directly to /api/cc_question like meeting captions.
async function sendTranscriptViaPost(text) {
  if (!_rlogUrl) return;
  try {
    const headers = {
      'Content-Type': 'application/json',
      'ngrok-skip-browser-warning': 'true',
    };
    if (wsSecretCode) headers['X-Auth-Token'] = wsSecretCode;
    const body = { question: text, source: 'sarvam_audio' };
    if (wsUserToken) body.user_token = wsUserToken;
    const resp = await fetch(`${_rlogUrl}/api/cc_question`, {
      method: 'POST', headers, body: JSON.stringify(body),
    });
    rlog(`POST /api/cc_question → ${resp.status}: "${text.slice(0,60)}"`);
  } catch (e) {
    rlog(`POST fallback failed: ${e.message}`, 'error');
  }
}

// ── Start capture ─────────────────────────────────────────────────────────────
async function startCapture(streamId, serverUrl, secretCode, sarvamKey, userToken = '', captureMode = 'tab') {
  stopCapture();
  // Store for WS reconnect
  wsServerUrl  = serverUrl;
  wsSecretCode = secretCode;
  wsUserToken  = userToken;
  _rlogUrl     = (serverUrl || '').replace(/\/$/, '');
  _rlogToken   = userToken || '';
  rlog(`startCapture called | mode=${captureMode} | sarvam=${sarvamKey ? 'YES(len='+sarvamKey.length+')' : 'NO'} | serverUrl=${serverUrl} | userToken=${userToken ? userToken.slice(0,8)+'...' : 'none'}`);

  let stream;
  try {
    if (captureMode === 'mic') {
      // Mic mode: no tab recording indicator, captures ambient room audio
      stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    } else if (captureMode === 'desktop') {
      const constraints = {
        audio: { mandatory: { chromeMediaSource: 'desktop', chromeMediaSourceId: streamId } },
        video: { mandatory: { chromeMediaSource: 'desktop', chromeMediaSourceId: streamId } },
      };
      stream = await navigator.mediaDevices.getUserMedia(constraints);
      stream.getVideoTracks().forEach(t => t.stop());
    } else {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } },
        video: false,
      });
    }
    const audioTracks = stream.getAudioTracks();
    rlog(`getUserMedia OK | audioTracks=${audioTracks.length} | label="${audioTracks[0]?.label || 'none'}"`);
  } catch (e) {
    rlog(`getUserMedia FAILED: ${e.message}`, 'error');
    throw e;
  }
  activeStream = stream;

  // ── WebSocket connection ────────────────────────────────────────────────────
  const base   = (serverUrl || '').replace(/\/$/, '');
  const wsBase = base.replace(/^https/, 'wss').replace(/^http/, 'ws');
  // Build query string with optional secret code and user token
  const params = new URLSearchParams({ 'ngrok-skip-browser-warning': '1' });
  if (secretCode) params.set('token', secretCode);
  if (userToken)  params.set('user_token', userToken);
  const wsUrl  = `${wsBase}/ws/audio?${params.toString()}`;

  rlog(`WS connecting → ${wsUrl} (sarvam=${!!sarvamKey}: WS optional in Sarvam mode)`);
  wsConn = new WebSocket(wsUrl);
  wsConn.binaryType = 'arraybuffer';

  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      rlog(`WS TIMEOUT after ${WS_CONNECT_TIMEOUT}ms | url=${wsUrl}`, 'error');
      wsConn = null;  // mark as unavailable
      if (sarvamKey) {
        // Sarvam mode: WS is optional — can send transcripts via POST fallback
        rlog(`Sarvam mode: continuing without WS — will use POST fallback`);
        resolve();
      } else {
        reject(new Error(`WebSocket connection timed out (${WS_CONNECT_TIMEOUT/1000}s)`));
      }
    }, WS_CONNECT_TIMEOUT);
    wsConn.onopen = () => {
      clearTimeout(timer);
      rlog(`WS connected OK → ${wsUrl}`);
      audioElement = new Audio();
      audioElement.srcObject = stream;
      audioElement.play().catch(e => console.warn('[Drishi] Audio passthrough muted (autoplay):', e));
      resolve();
    };
    wsConn.onerror = (e) => {
      clearTimeout(timer);
      rlog(`WS onerror | url=${wsUrl}`, 'error');
      wsConn = null;
      if (sarvamKey) {
        rlog(`Sarvam mode: WS failed — continuing with POST fallback`);
        resolve();
      } else {
        reject(new Error('WebSocket connection failed'));
      }
    };
  });

  // Keepalive ping — prevents 1005 "No Status Received" drops on idle connections
  wsPingTimer = setInterval(() => {
    if (wsConn && wsConn.readyState === WebSocket.OPEN) {
      wsConn.send(JSON.stringify({ type: 'ping' }));
    }
  }, WS_PING_INTERVAL_MS);

  // ── Receive server commands (laptop1 → laptop2 remote control) ─────────────
  wsConn.onmessage = (evt) => {
    if (typeof evt.data !== 'string') return;
    try {
      const msg = JSON.parse(evt.data);
      if (msg.type !== 'command') return;
      if (msg.action === 'start_capture') {
        // Ask background to start tab capture remotely (no user gesture needed)
        chrome.runtime.sendMessage({ type: 'remote_start_capture' })
          .catch(e => console.warn('[Drishi] remote_start_capture failed:', e.message));
      } else if (msg.action === 'stop_capture') {
        stopCapture();
        chrome.runtime.sendMessage({ type: 'audio_status', status: 'stopped', reason: 'remote_stop' }).catch(() => {});
      }
    } catch (_) {}
  };

  wsConn.onclose = (evt) => {
    rlog(`WS closed | code=${evt.code} reason="${evt.reason || 'none'}"`, evt.code === 1000 ? 'info' : 'error');
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
    await audioCtx.resume();
  }
  audioCtx.addEventListener('statechange', async () => {
    if (audioCtx && audioCtx.state === 'suspended') {
      try { await audioCtx.resume(); } catch (_) {}
    }
  });

  const source = audioCtx.createMediaStreamSource(stream);

  // ── Mode A state (Sarvam client-side STT) ──────────────────────────────────
  // Mic mode needs higher thresholds: mic picks up user's voice + ambient room noise.
  // Raise threshold so only clear speech triggers capture, and require longer utterances
  // (filters out short acknowledgments like "Okay", "Right", "Thank you").
  const SILENCE_THRESHOLD  = captureMode === 'mic' ? 0.018 : 0.008;
  const SILENCE_CHUNKS_END = captureMode === 'mic' ? 14  : 10;   // mic: 900ms, tab: 640ms
  const MIN_SPEECH_SAMPLES = captureMode === 'mic' ? 24000 : 8000; // mic: 1.5s min, tab: 0.5s
  const MAX_BUFFER_SAMPLES = 96000;   // 6s safety cap
  const PRE_ROLL_CHUNKS    = 3;       // ~200ms pre-roll before speech onset

  let pcmBuffer     = [];
  let bufferSamples = 0;
  let inSpeech      = false;
  let silenceCount  = 0;
  let transcribing  = false;
  let preRoll       = [];             // circular pre-roll: last N silent chunks

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
      const durSec = (total.length / SAMPLE_RATE).toFixed(1);
      rlog(`Sarvam STT sending: ${total.length} samples (${durSec}s)`);
      const text = await transcribeWithSarvam(total, sarvamKey, currentAbort.signal);
      if (currentAbort.signal.aborted) return;
      rlog(`Sarvam result: "${text}"`);
      if (text && text.length >= 4) {
        if (wsConn && wsConn.readyState === WebSocket.OPEN) {
          // Primary: send over existing WS connection
          wsConn.send(JSON.stringify({ type: 'text_question', text }));
          rlog(`Sarvam→WS: "${text.slice(0,80)}"`);
        } else {
          // Fallback: POST directly to /api/cc_question (works without WS / through ngrok)
          rlog(`WS unavailable — posting via HTTP: "${text.slice(0,60)}"`);
          sendTranscriptViaPost(text);
        }
      } else {
        rlog(`Sarvam returned empty/short text — skipped`);
      }
    } catch (e) {
      if (e.name !== 'AbortError') rlog(`Sarvam ERROR: ${e.message}`, 'error');
    } finally {
      transcribing = false;
      if (sarvamAbortCtrl === currentAbort) sarvamAbortCtrl = null;
    }
  }

  // ── Per-chunk processor — called by both AudioWorklet and ScriptProcessorNode
  function processChunk(float32) {
    // Sarvam mode: process audio locally — WS not required (sends via POST fallback)
    // Raw PCM mode: WS required to stream binary
    if (!sarvamKey && (!wsConn || wsConn.readyState !== WebSocket.OPEN)) return;

    if (sarvamKey) {
      // Mode A: accumulate + silence detection → Sarvam AI STT
      let sum = 0;
      for (let i = 0; i < float32.length; i++) sum += float32[i] * float32[i];
      const rms      = Math.sqrt(sum / float32.length);
      const isSilent = rms < SILENCE_THRESHOLD;
      const pcm16    = float32ToPcm16(float32);

      // Log audio stats every 50 chunks (~3s) to confirm audio is flowing
      _rmsLogCount++;
      if (_rmsLogCount % 50 === 1) {
        rlog(`audio flowing | rms=${rms.toFixed(4)} (threshold=${SILENCE_THRESHOLD}) silent=${isSilent} inSpeech=${inSpeech} buffered=${(bufferSamples/SAMPLE_RATE).toFixed(1)}s wsState=${wsConn?.readyState}`);
      }

      if (!isSilent) {
        if (!inSpeech) {
          // Speech onset — prepend pre-roll so we don't clip the start
          for (const pre of preRoll) { pcmBuffer.push(pre); bufferSamples += pre.length; }
          preRoll = [];
        }
        inSpeech = true; silenceCount = 0;
      } else if (inSpeech) {
        silenceCount++;
      }

      if (inSpeech) {
        // Only accumulate while speech is active (avoid 6s silent flushes)
        pcmBuffer.push(pcm16);
        bufferSamples += pcm16.length;
      } else {
        // Keep a rolling pre-roll window so speech onset isn't clipped
        preRoll.push(pcm16);
        if (preRoll.length > PRE_ROLL_CHUNKS) preRoll.shift();
      }

      const endOfSpeech = inSpeech && silenceCount >= SILENCE_CHUNKS_END;
      const bufferFull  = inSpeech && bufferSamples >= MAX_BUFFER_SAMPLES;
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
  } catch (e) {
    console.warn('[Drishi] AudioWorklet unavailable, using ScriptProcessorNode fallback:', e.message);
    const spn = audioCtx.createScriptProcessor(BUFFER_SIZE, 1, 1);
    spn.onaudioprocess = (ev) => processChunk(ev.inputBuffer.getChannelData(0));
    source.connect(spn);
    spn.connect(audioCtx.destination);
    processor = spn;
  }

  chrome.runtime.sendMessage({ type: 'audio_status', status: 'streaming' }).catch(() => {});
  rlog(`CAPTURE ACTIVE | ${SAMPLE_RATE/1000}kHz | mode=${sarvamKey ? 'Sarvam STT' : 'raw PCM'} | engine=${useWorklet ? 'AudioWorklet' : 'ScriptProcessorNode'}`);
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
