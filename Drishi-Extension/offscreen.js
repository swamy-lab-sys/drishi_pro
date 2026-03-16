/**
 * Drishi Pro — Offscreen Audio Capture
 *
 * Runs in an offscreen document (MV3).
 * Receives tab audio via chrome.tabCapture stream ID,
 * applies client-side VAD, and streams PCM-16 chunks to the
 * Drishi backend via WebSocket when speech is detected.
 *
 * Protocol (binary):
 *   client → server : Int16Array (PCM-16 mono 16 kHz, one speech segment)
 *   server → client : JSON string { type, ... }
 */

'use strict';

let audioCtx    = null;
let sourceNode  = null;
let processorNode = null;
let mediaStream = null;
let ws          = null;
let serverUrl   = '';
let secretCode  = '';
let capturing   = false;

// VAD parameters
const SAMPLE_RATE      = 16000;
const CHUNK_SAMPLES    = 4096;          // ~256ms per chunk
const SPEECH_THRESHOLD = 0.018;         // RMS threshold to detect speech
const SILENCE_CHUNKS   = 12;            // ~3s silence → send segment
const MIN_SPEECH_CHUNKS = 3;            // ignore < ~0.75s audio

let speechBuffer   = [];   // accumulated Float32 chunks during speech
let silenceCounter = 0;
let isSpeaking     = false;

// ── Message handling from background.js ──────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === 'START_CAPTURE') {
    serverUrl  = msg.serverUrl;
    secretCode = msg.secretCode;
    startCapture(msg.streamId).then(() => sendResponse({ ok: true })).catch(e => {
      sendResponse({ ok: false, error: e.message });
    });
    return true; // async
  }

  if (msg.type === 'STOP_CAPTURE') {
    stopCapture();
    sendResponse({ ok: true });
  }

  if (msg.type === 'PING_OFFSCREEN') {
    sendResponse({ alive: true, capturing });
  }
});

// ── Start capture ─────────────────────────────────────────────────────────────
async function startCapture(streamId) {
  if (capturing) stopCapture();

  // Get the tab's MediaStream via the stream ID
  mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      mandatory: {
        chromeMediaSource:   'tab',
        chromeMediaSourceId: streamId,
      }
    },
    video: false
  });

  // Connect to WebSocket backend
  const wsUrl = serverUrl
    .replace(/^https:\/\//, 'wss://')
    .replace(/^http:\/\//, 'ws://')
    .replace(/\/$/, '');
  ws = new WebSocket(`${wsUrl}/ws/audio?token=${encodeURIComponent(secretCode)}`);
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => {
    notifyBackground({ type: 'CAPTURE_STATUS', status: 'connected' });
    setupAudio();
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      notifyBackground({ type: 'SERVER_MESSAGE', data: msg });
    } catch (_) {}
  };

  ws.onerror = () => notifyBackground({ type: 'CAPTURE_STATUS', status: 'ws_error' });
  ws.onclose = () => {
    notifyBackground({ type: 'CAPTURE_STATUS', status: 'disconnected' });
    capturing = false;
  };

  capturing = true;
}

// ── Audio processing ──────────────────────────────────────────────────────────
function setupAudio() {
  audioCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
  sourceNode = audioCtx.createMediaStreamSource(mediaStream);

  // ScriptProcessorNode — deprecated but universally available in offscreen docs
  processorNode = audioCtx.createScriptProcessor(CHUNK_SAMPLES, 1, 1);

  processorNode.onaudioprocess = (e) => {
    const pcm = e.inputBuffer.getChannelData(0); // Float32Array

    // RMS-based VAD
    let sum = 0;
    for (let i = 0; i < pcm.length; i++) sum += pcm[i] * pcm[i];
    const rms = Math.sqrt(sum / pcm.length);

    if (rms > SPEECH_THRESHOLD) {
      // Speech detected
      if (!isSpeaking) {
        isSpeaking     = true;
        silenceCounter = 0;
        speechBuffer   = [];
      }
      speechBuffer.push(new Float32Array(pcm));
      silenceCounter = 0;
    } else if (isSpeaking) {
      // Silence after speech
      speechBuffer.push(new Float32Array(pcm));
      silenceCounter++;

      if (silenceCounter >= SILENCE_CHUNKS) {
        // Enough silence — send the accumulated segment
        if (speechBuffer.length >= MIN_SPEECH_CHUNKS) {
          sendSegment(speechBuffer);
        }
        speechBuffer   = [];
        isSpeaking     = false;
        silenceCounter = 0;
      }
    }
  };

  sourceNode.connect(processorNode);
  processorNode.connect(audioCtx.destination);
}

// ── Send one speech segment to server ────────────────────────────────────────
function sendSegment(chunks) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;

  // Flatten Float32 chunks → Int16Array (PCM-16)
  const totalLen = chunks.reduce((s, c) => s + c.length, 0);
  const pcm16 = new Int16Array(totalLen);
  let offset = 0;
  for (const chunk of chunks) {
    for (let i = 0; i < chunk.length; i++) {
      pcm16[offset++] = Math.max(-32768, Math.min(32767, Math.round(chunk[i] * 32767)));
    }
  }

  ws.send(pcm16.buffer);
  notifyBackground({ type: 'CAPTURE_STATUS', status: 'sent_segment',
                     durationMs: Math.round(totalLen / SAMPLE_RATE * 1000) });
}

// ── Send typed question (from popup) ─────────────────────────────────────────
function sendTextQuestion(text) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: 'text_question', text }));
}

// ── Stop capture ─────────────────────────────────────────────────────────────
function stopCapture() {
  capturing = false;
  isSpeaking = false;
  speechBuffer = [];

  processorNode?.disconnect();
  sourceNode?.disconnect();
  audioCtx?.close().catch(() => {});
  mediaStream?.getTracks().forEach(t => t.stop());
  ws?.close();

  processorNode = null;
  sourceNode    = null;
  audioCtx      = null;
  mediaStream   = null;
  ws            = null;
}

// ── Relay messages to background service worker ───────────────────────────────
function notifyBackground(msg) {
  chrome.runtime.sendMessage(msg).catch(() => {});
}
