"""
Professional Audio Capture Engine - CONCURRENT EDITION
Supports: sounddevice > pyaudio > parec (PulseAudio direct)
Maintains a persistent stream for zero-latency capture.
On Linux, PULSE_SOURCE env var controls capture source (set to speaker monitor).
"""

import numpy as np
import subprocess
import queue
import time
import threading
from collections import deque
import os

# Audio configuration
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_SIZE = 480  # 30ms
BYTES_PER_CHUNK = CHUNK_SIZE * 4  # float32 = 4 bytes per sample

# Detect best available backend — sounddevice first (uses PulseAudio on Linux,
# which respects PULSE_SOURCE to select the capture device)
_BACKEND = None
sd = None
pyaudio_module = None

try:
    import sounddevice as _sd
    _sd.query_devices()
    sd = _sd
    _BACKEND = "sounddevice"
except Exception:
    pass

if not _BACKEND:
    try:
        import pyaudio as _pa
        _test_pa = _pa.PyAudio()
        _test_pa.get_default_input_device_info()
        _test_pa.terminate()
        pyaudio_module = _pa
        _BACKEND = "pyaudio"
    except Exception:
        pass

if not _BACKEND:
    import shutil as _shutil
    if _shutil.which("parec"):
        _BACKEND = "parec"

if _BACKEND:
    print(f"  [Audio] Backend: {_BACKEND}")
else:
    print("[CRITICAL] No audio backend available!")


class SharedAudioStream:
    """
    Singleton-style stream that stays open for the entire session.
    Prevents latency/glitches from constant start/stop.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SharedAudioStream, cls).__new__(cls)
            cls._instance.is_active = False
            cls._instance.audio_queue = queue.Queue(maxsize=10000)
            cls._instance.stream = None
            cls._instance.pa_instance = None
            cls._instance._parec_proc = None
            cls._instance._parec_thread = None
            cls._instance.device = None
        return cls._instance

    # === sounddevice backend ===
    def _sd_callback(self, indata, frames, time_info, status):
        if self.is_active:
            try:
                self.audio_queue.put_nowait(indata.copy().flatten())
            except queue.Full:
                pass

    # === pyaudio backend ===
    def _pa_callback(self, in_data, frame_count, time_info, status):
        if self.is_active:
            try:
                audio = np.frombuffer(in_data, dtype=np.float32)
                self.audio_queue.put_nowait(audio.copy())
            except queue.Full:
                pass
        return (None, 0)

    # === parec backend ===
    def _parec_reader(self):
        """Read raw audio from parec subprocess stdout. Auto-restarts on crash."""
        while self.is_active:
            proc = self._parec_proc
            if proc is None:
                time.sleep(0.5)
                continue
            while self.is_active and proc.poll() is None:
                try:
                    data = proc.stdout.read(BYTES_PER_CHUNK)
                    if not data:
                        break
                    audio = np.frombuffer(data, dtype=np.float32)
                    if len(audio) == CHUNK_SIZE:
                        self.audio_queue.put_nowait(audio.copy())
                except queue.Full:
                    pass
                except Exception:
                    break
            if self.is_active:
                time.sleep(0.5)
                self._start_parec()

    def _start_parec(self):
        """Start (or restart) the parec subprocess."""
        try:
            if self._parec_proc and self._parec_proc.poll() is None:
                self._parec_proc.terminate()
            source = os.environ.get("PULSE_SOURCE", "")
            # Unique restore ID per-process so PipeWire stream-restore never
            # overrides --device with a stale cached source
            restore_id = f"iva_capture_{os.getpid()}"
            cmd = [
                "parec",
                f"--rate={SAMPLE_RATE}",
                f"--channels={CHANNELS}",
                "--format=float32le",
                "--latency-msec=30",
                f"--property=module-stream-restore.id={restore_id}",
            ]
            if source:
                cmd.append(f"--device={source}")
                print(f"  [Audio] parec capturing from: {source}")
            else:
                print("  [Audio] parec: using default source")
            self._parec_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=BYTES_PER_CHUNK
            )
        except Exception as e:
            print(f"[CRITICAL] parec failed: {e}")
            self._parec_proc = None

    def start(self):
        if self.stream is not None or self._parec_proc is not None:
            return

        self.is_active = True

        if _BACKEND == "sounddevice":
            try:
                # Use PULSE_SOURCE to pick monitor device if set
                target_device = self.device
                if target_device is None:
                    pulse_source = os.environ.get("PULSE_SOURCE", "").strip()
                    if pulse_source:
                        # Try passing the PulseAudio source name directly (works on Linux/PA)
                        devices = sd.query_devices()
                        matched = None
                        # 1. Exact name match
                        for i, d in enumerate(devices):
                            if d.get('max_input_channels', 0) > 0 and d.get('name', '') == pulse_source:
                                matched = i
                                break
                        # 2. Partial name match (pulse_source is substring of device name)
                        if matched is None:
                            for i, d in enumerate(devices):
                                dname = d.get('name', '')
                                if d.get('max_input_channels', 0) > 0 and pulse_source in dname:
                                    matched = i
                                    break
                        # 3. Any monitor/loopback device
                        if matched is None:
                            for i, d in enumerate(devices):
                                dname = d.get('name', '').lower()
                                if d.get('max_input_channels', 0) > 0 and ('monitor' in dname or 'loopback' in dname):
                                    matched = i
                                    break
                        if matched is not None:
                            target_device = matched
                            print(f"  [Audio] Monitor device: [{matched}] {sd.query_devices(matched)['name']}")
                self.stream = sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=CHANNELS,
                    device=target_device,
                    callback=self._sd_callback,
                    blocksize=CHUNK_SIZE
                )
                self.stream.start()
                src = os.environ.get("PULSE_SOURCE", "default mic")
                print(f"  [Audio] sounddevice capturing (PULSE_SOURCE={src})")
            except Exception as e:
                print(f"[CRITICAL] sounddevice failed: {e}")
                self.stream = None

        elif _BACKEND == "pyaudio":
            try:
                self.pa_instance = pyaudio_module.PyAudio()
                self.stream = self.pa_instance.open(
                    format=pyaudio_module.paFloat32,
                    channels=CHANNELS,
                    rate=SAMPLE_RATE,
                    input=True,
                    frames_per_buffer=CHUNK_SIZE,
                    stream_callback=self._pa_callback
                )
                self.stream.start_stream()
            except Exception as e:
                print(f"[CRITICAL] pyaudio failed: {e}")
                self.stream = None

        elif _BACKEND == "parec":
            self._start_parec()
            self._parec_thread = threading.Thread(
                target=self._parec_reader, daemon=True
            )
            self._parec_thread.start()

    def stop(self):
        self.is_active = False
        if _BACKEND == "sounddevice" and self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        elif _BACKEND == "pyaudio" and self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None
            if self.pa_instance:
                self.pa_instance.terminate()
                self.pa_instance = None
        elif _BACKEND == "parec" and self._parec_proc:
            self._parec_proc.terminate()
            self._parec_proc.wait(timeout=2)
            self._parec_proc = None

    def get_chunk(self, timeout=0.1):
        try:
            return self.audio_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def flush(self):
        """Clear all pending audio."""
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except Exception:
                break


# Global stream instance
_stream = SharedAudioStream()


class SmartVAD:
    """Ultra-sensitive adaptive Speech Detection."""
    def __init__(self):
        self.noise_floor = 0.0001
        self.speech_threshold = 0.0005
        self.history = deque(maxlen=100)

    def update(self, chunk):
        rms = np.sqrt(np.mean(chunk**2))
        self.history.append(rms)
        if len(self.history) >= 10:
            self.noise_floor = max(np.percentile(list(self.history), 10), 0.0001)
            self.speech_threshold = max(self.noise_floor * 2.5, 0.001)

    def is_speech(self, chunk):
        chunk_boosted = chunk * 20.0
        rms = np.sqrt(np.mean(chunk_boosted**2))
        self.update(chunk_boosted)
        return rms > max(self.speech_threshold, 0.005)


def capture_question(max_duration=6.0, silence_duration=1.2, verbose=False, on_speech_start=None):
    """Capture a single question segment using the persistent global stream."""
    if _BACKEND is None:
        if verbose:
            print("[ERROR] No audio backend available")
        return None

    _stream.start()
    _stream.flush()

    vad = SmartVAD()
    audio_chunks = []
    lead_buffer = deque(maxlen=int(500 / 30))
    speech_detected = False
    silence_chunks = 0
    max_silence_chunks = int(silence_duration * 1000 / 30)
    max_chunks = int(max_duration * 1000 / 30)

    start_time = time.time()

    try:
        while (time.time() - start_time) < (max_duration + 2.0):
            chunk = _stream.get_chunk(timeout=0.1)
            if chunk is None:
                continue

            if vad.is_speech(chunk):
                if not speech_detected:
                    if verbose:
                        print("\n[SPEECH] Detected!")
                    audio_chunks.extend(list(lead_buffer))
                    lead_buffer.clear()
                    speech_detected = True
                    if on_speech_start:
                        try:
                            on_speech_start()
                        except Exception:
                            pass
                silence_chunks = 0
                audio_chunks.append(chunk)
            else:
                if speech_detected:
                    silence_chunks += 1
                    audio_chunks.append(chunk)
                    if silence_chunks > max_silence_chunks:
                        if verbose:
                            print(f"[SILENCE] {silence_duration}s detected.")
                        break
                else:
                    lead_buffer.append(chunk)

            if len(audio_chunks) >= max_chunks:
                break

        if not audio_chunks:
            return None

        full_audio = np.concatenate(audio_chunks)

        # Normalize for Whisper/Sarvam
        max_val = np.abs(full_audio).max()
        if max_val > 0.001:
            full_audio = full_audio / max_val * 0.9

        return full_audio

    except Exception as e:
        if verbose:
            print(f"[ERROR] capture_question: {e}")
        return None


if __name__ == "__main__":
    print(f"Testing capture (backend: {_BACKEND})...")
    q = capture_question(verbose=True)
    if q is not None:
        print(f"Captured {len(q)} samples ({len(q)/SAMPLE_RATE:.1f}s)")
    else:
        print("No audio captured.")
