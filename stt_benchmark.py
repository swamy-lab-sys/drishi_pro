#!/usr/bin/env python3
"""
STT Benchmark Script — Drishi Pro
Generates Indian English interview questions via Sarvam TTS,
then benchmarks all available STT backends for latency and accuracy.
"""

import sys
import os
import time
import base64
import struct
import wave
import io
import json
import tempfile
import requests
import numpy as np

# ── Bootstrap ──────────────────────────────────────────────────────────────────
sys.path.insert(0, '/home/venkat/Drishi')

# Load .env manually
_env_path = '/home/venkat/Drishi/.env'
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _v = _line.split('=', 1)
                os.environ.setdefault(_k.strip(), _v.strip())
    print(f"[INIT] Loaded .env from {_env_path}")

# Import config and stt after env is set
import config
import stt

# ── Test sentences ─────────────────────────────────────────────────────────────
TEST_SENTENCES = [
    "What is the difference between list and tuple in Python?",
    "Explain how Django ORM handles N plus 1 query problem.",
    "What is GIL in Python and how does it affect multithreading?",
    "How do you handle incidents in production support using ITIL framework?",
    "Explain Docker containerization and how it differs from virtual machines.",
]

SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY", "")
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")

SAMPLE_RATE = 16000


# ── TTS: Generate audio via Sarvam ─────────────────────────────────────────────

def generate_tts_sarvam(text: str) -> np.ndarray:
    """
    Call Sarvam TTS API and return float32 numpy array at 16kHz.
    Response JSON has 'audios' list; each item is base64-encoded WAV.
    """
    url = "https://api.sarvam.ai/text-to-speech"
    headers = {"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"}
    payload = {
        "inputs": [text],
        "target_language_code": "en-IN",
        "speaker": "anushka",
        "model": "bulbul:v2",
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # Extract base64 audio
    audios = data.get("audios", [])
    if not audios:
        raise ValueError(f"TTS response missing 'audios': {data}")

    audio_b64 = audios[0]
    raw_bytes = base64.b64decode(audio_b64)

    # Parse WAV bytes
    with io.BytesIO(raw_bytes) as buf:
        with wave.open(buf, 'rb') as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            n_frames = wf.getnframes()
            raw_pcm = wf.readframes(n_frames)

    # Decode PCM samples
    if sampwidth == 2:
        samples = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float32) / 32768.0
    elif sampwidth == 4:
        samples = np.frombuffer(raw_pcm, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Unsupported sample width: {sampwidth}")

    # Convert stereo to mono
    if n_channels == 2:
        samples = samples.reshape(-1, 2).mean(axis=1)

    # Resample to 16kHz if needed
    if framerate != SAMPLE_RATE:
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(SAMPLE_RATE, framerate)
        samples = resample_poly(samples, SAMPLE_RATE // g, framerate // g)
        samples = samples.astype(np.float32)

    return samples


# ── WER calculation ────────────────────────────────────────────────────────────

def compute_wer(reference: str, hypothesis: str) -> float:
    """Approximate WER: Levenshtein on word sequences, return as fraction."""
    ref_words = reference.lower().replace("?", "").replace(".", "").split()
    hyp_words = hypothesis.lower().replace("?", "").replace(".", "").split()

    if not ref_words:
        return 0.0

    # Edit distance matrix
    m, n = len(ref_words), len(hyp_words)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if ref_words[i - 1] == hyp_words[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])

    return dp[m][n] / max(m, 1)


def word_accuracy(reference: str, hypothesis: str) -> float:
    """Return word accuracy = 1 - WER, clamped to [0, 1]."""
    return max(0.0, 1.0 - compute_wer(reference, hypothesis))


# ── Backend runners ────────────────────────────────────────────────────────────

def run_backend(name: str, audio: np.ndarray, fn) -> tuple:
    """Run a backend function, return (latency_ms, transcript, error)."""
    try:
        t0 = time.time()
        result = fn(audio)
        elapsed = (time.time() - t0) * 1000
        if isinstance(result, tuple):
            text = result[0]
        else:
            text = str(result)
        return elapsed, text, None
    except Exception as e:
        return None, "", str(e)


# ── Main benchmark ─────────────────────────────────────────────────────────────

def main():
    print("=" * 72)
    print("  Drishi Pro — STT Benchmark")
    print("=" * 72)
    print(f"  Test sentences : {len(TEST_SENTENCES)}")
    print(f"  SARVAM_API_KEY : {'SET' if SARVAM_API_KEY else 'MISSING'}")
    print(f"  DEEPGRAM_API_KEY: {'SET' if DEEPGRAM_API_KEY else 'MISSING'}")
    print()

    # ── Step 1: Generate TTS audio ─────────────────────────────────────────────
    print("── Step 1: Generating TTS audio via Sarvam ──")
    audio_clips = []
    for i, sentence in enumerate(TEST_SENTENCES, 1):
        print(f"  [{i}/{len(TEST_SENTENCES)}] Generating: {sentence[:60]}...")
        try:
            t0 = time.time()
            audio = generate_tts_sarvam(sentence)
            dur = time.time() - t0
            duration_s = len(audio) / SAMPLE_RATE
            print(f"           OK — {len(audio)} samples ({duration_s:.2f}s audio) — TTS took {dur:.2f}s")
            audio_clips.append(audio)
        except Exception as e:
            print(f"           ERROR: {e}")
            # Generate silent fallback (2 seconds of silence)
            audio_clips.append(np.zeros(SAMPLE_RATE * 2, dtype=np.float32))

    print()

    # ── Step 2: Define backends ────────────────────────────────────────────────
    backends = []

    # a) Sarvam STT
    if SARVAM_API_KEY:
        def sarvam_fn(audio):
            config.STT_BACKEND = "sarvam"
            config.SARVAM_API_KEY = SARVAM_API_KEY
            # Reset session so each benchmark call uses a fresh session
            return stt._transcribe_sarvam(audio)
        backends.append(("Sarvam saarika:v2.5", sarvam_fn))
    else:
        print("  [SKIP] Sarvam STT — SARVAM_API_KEY not set")

    # b) Deepgram STT
    if DEEPGRAM_API_KEY:
        def deepgram_fn(audio):
            config.STT_BACKEND = "deepgram"
            config.DEEPGRAM_API_KEY = DEEPGRAM_API_KEY
            return stt._transcribe_deepgram(audio)
        backends.append(("Deepgram nova-3", deepgram_fn))
    else:
        print("  [SKIP] Deepgram STT — DEEPGRAM_API_KEY not set")

    # c) Local Whisper tiny.en
    print("  [LOAD] Loading Whisper tiny.en model...")
    try:
        stt.load_model("tiny.en")
        loaded_tiny = True
        print("  [LOAD] tiny.en ready")
    except Exception as e:
        print(f"  [LOAD] tiny.en failed: {e}")
        loaded_tiny = False

    if loaded_tiny:
        def whisper_tiny_fn(audio):
            return stt._transcribe_local(audio, model_override="tiny.en")
        backends.append(("Whisper tiny.en (local)", whisper_tiny_fn))

    # d) Local Whisper Systran/faster-distil-whisper-small.en
    print("  [LOAD] Loading Systran/faster-distil-whisper-small.en model...")
    try:
        stt.load_model("Systran/faster-distil-whisper-small.en")
        loaded_distil = True
        print("  [LOAD] faster-distil-whisper-small.en ready")
    except Exception as e:
        print(f"  [LOAD] faster-distil-whisper-small.en failed: {e}")
        loaded_distil = False

    if loaded_distil:
        def whisper_distil_fn(audio):
            return stt._transcribe_local(audio, model_override="Systran/faster-distil-whisper-small.en")
        backends.append(("Whisper faster-distil-small.en (local)", whisper_distil_fn))

    print()
    print(f"  Active backends: {len(backends)}")
    for b in backends:
        print(f"    - {b[0]}")
    print()

    # ── Step 3: Run benchmarks ─────────────────────────────────────────────────
    print("── Step 2: Running benchmarks ──")
    print()

    # results[backend_name] = list of {latency_ms, transcript, accuracy, sentence}
    results = {b[0]: [] for b in backends}

    for i, (sentence, audio) in enumerate(zip(TEST_SENTENCES, audio_clips), 1):
        print(f"  Sentence {i}: {sentence[:65]}")
        for bname, bfn in backends:
            latency_ms, transcript, error = run_backend(bname, audio, bfn)
            if error:
                print(f"    [{bname:45s}] ERROR: {error}")
                results[bname].append({
                    "sentence": sentence,
                    "transcript": "",
                    "latency_ms": None,
                    "accuracy": 0.0,
                    "error": error,
                })
            else:
                acc = word_accuracy(sentence, transcript)
                results[bname].append({
                    "sentence": sentence,
                    "transcript": transcript,
                    "latency_ms": latency_ms,
                    "accuracy": acc,
                    "error": None,
                })
                status = "OK" if acc >= 0.7 else ("FAIR" if acc >= 0.4 else "POOR")
                print(f"    [{bname:45s}] {latency_ms:6.0f}ms  acc={acc*100:.0f}%  [{status}]")
                print(f"      Transcript: {transcript[:80]}")
        print()

    # ── Step 4: Print report ───────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("  BENCHMARK REPORT")
    print("=" * 72)
    print()

    # Summary table header
    col_w = 38
    print(f"{'Backend':<{col_w}} {'Avg ms':>8} {'Min ms':>8} {'Max ms':>8} {'Avg WER%':>10} {'Acc%':>7}")
    print("-" * 82)

    recommendation = []
    for bname, _ in backends:
        runs = results[bname]
        valid = [r for r in runs if r["latency_ms"] is not None]
        if not valid:
            print(f"  {bname:<{col_w}} {'N/A':>8} {'N/A':>8} {'N/A':>8} {'N/A':>10} {'N/A':>7}")
            continue

        latencies = [r["latency_ms"] for r in valid]
        accuracies = [r["accuracy"] for r in valid]
        avg_lat = sum(latencies) / len(latencies)
        min_lat = min(latencies)
        max_lat = max(latencies)
        avg_acc = sum(accuracies) / len(accuracies)
        avg_wer = (1 - avg_acc) * 100

        print(f"  {bname:<{col_w}} {avg_lat:>8.0f} {min_lat:>8.0f} {max_lat:>8.0f} {avg_wer:>9.1f}% {avg_acc*100:>6.1f}%")
        recommendation.append((bname, avg_lat, avg_acc))

    print()

    # Per-sentence detail table
    print("── Per-sentence accuracy ──────────────────────────────────────────────")
    for i, sentence in enumerate(TEST_SENTENCES):
        short = sentence[:50] + "..." if len(sentence) > 50 else sentence
        print(f"\n  Q{i+1}: {short}")
        for bname, _ in backends:
            r = results[bname][i]
            if r["error"]:
                print(f"       {bname:45s}  ERROR")
            else:
                lat = f"{r['latency_ms']:.0f}ms" if r['latency_ms'] else "N/A"
                acc = f"{r['accuracy']*100:.0f}%"
                tx = r['transcript'][:70] if r['transcript'] else "(empty)"
                print(f"       {bname:45s}  {lat:>7}  acc={acc}")
                print(f"         → {tx}")

    print()
    print("── Recommendation ─────────────────────────────────────────────────────")

    if recommendation:
        # Score: balanced between speed and accuracy
        # Weight: 40% speed (lower is better), 60% accuracy (higher is better)
        # Normalize latencies
        lats = [x[1] for x in recommendation]
        max_lat = max(lats) if max(lats) > 0 else 1.0
        scored = []
        for bname, lat, acc in recommendation:
            speed_score = 1.0 - (lat / max_lat)
            combined = 0.4 * speed_score + 0.6 * acc
            scored.append((bname, lat, acc, combined))

        scored.sort(key=lambda x: -x[3])

        print()
        print(f"  {'Backend':<45} {'Avg ms':>8} {'Acc%':>7} {'Score':>7}")
        print("  " + "-" * 72)
        for rank, (bname, lat, acc, score) in enumerate(scored, 1):
            marker = "  ← BEST OVERALL" if rank == 1 else ""
            print(f"  {bname:<45} {lat:>8.0f} {acc*100:>6.1f}% {score:>6.3f}{marker}")

        best = scored[0][0]
        print()
        print(f"  RECOMMENDATION: Use '{best}' for Indian English interview questions.")

        # Find fastest
        fastest = min(recommendation, key=lambda x: x[1])
        most_accurate = max(recommendation, key=lambda x: x[2])
        print(f"  Fastest backend:   {fastest[0]} ({fastest[1]:.0f}ms avg)")
        print(f"  Most accurate:     {most_accurate[0]} ({most_accurate[2]*100:.1f}% avg accuracy)")

    print()
    print("=" * 72)
    print("  Benchmark complete.")
    print("=" * 72)


if __name__ == "__main__":
    main()
