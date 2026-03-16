"""
Audio Listener for Drishi Pro

PRODUCTION AUDIO CAPTURE

DESIGN:
- Accept ANY system playback audio (monitors, pulse, pipewire)
- NEVER block or modify the system microphone
- NEVER fail startup due to audio issues
- Ignore microphone audio BY LOGIC only
- Always start successfully
"""

import numpy as np
import webrtcvad
import time
import os
import re
import scipy.signal
import subprocess
import signal
import sys
import warnings
from collections import deque

# Suppress warnings
warnings.filterwarnings("ignore")

# Audio configuration
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_DURATION_MS = 30
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION_MS / 1000)

# Global audio device (lazy loaded)
SELECTED_AUDIO_DEVICE = None

# Volume gate: YouTube optimized
MIN_VOLUME_THRESHOLD = 0.01
DETECTION_THRESHOLD = 0.02

# Adaptive silence durations (seconds)
# Lower = faster trigger after speech ends; 0.45s works well for clean call audio
SILENCE_YOUTUBE = 0.45
SILENCE_MEET = 0.55
SILENCE_ZOOM = 0.55
SILENCE_TEAMS = 0.55
SILENCE_DEFAULT = 0.45
current_silence_duration = SILENCE_DEFAULT


def set_silence_duration(duration: float):
    """Set silence duration for finalization."""
    global current_silence_duration
    current_silence_duration = max(0.3, min(3.0, duration))


def set_platform_silence(platform: str):
    """Set silence duration based on platform."""
    durations = {
        'youtube': SILENCE_YOUTUBE,
        'meet': SILENCE_MEET,
        'zoom': SILENCE_ZOOM,
        'teams': SILENCE_TEAMS,
        'default': SILENCE_DEFAULT,
    }
    set_silence_duration(durations.get(platform.lower(), SILENCE_DEFAULT))


def is_system_audio_device(device_name):
    """
    Check if device is likely a system audio device.

    POLICY: Accept anything that looks like system audio.
    """
    if not device_name:
        return False

    name_lower = device_name.lower()

    # Always accept pulse
    if name_lower == 'pulse':
        return True

    # System audio keywords
    system_keywords = [
        'monitor', 'loopback', 'stereo mix', 'wave out', 'what u hear',
        'output', 'system_audio', 'sink', 'playback', 'speaker', 'pipewire',
        'microphone', 'mic', 'input'  # Enforcing microphone acceptance for testing
    ]

    for keyword in system_keywords:
        if keyword in name_lower:
            return True

    # Reject obvious microphones
    mic_keywords = ['microphone', 'mic', 'webcam', 'input']
    for keyword in mic_keywords:
        if keyword in name_lower:
            return False

    # Accept default and unknown devices
    return True


def list_audio_devices():
    """List available audio input devices."""
    devices = []
    
    # On Linux, try to get targets from PipeWire/PulseAudio directly
    if sys.platform.startswith('linux'):
        try:
            # Use pw-record to list targets
            output = subprocess.check_output(['pw-record', '--list-targets'], stderr=subprocess.DEVNULL, text=True)
            for line in output.split('\n'):
                line = line.strip()
                if not line or 'Available targets' in line:
                    continue
                
                # Format: "ID: type description=\"Full Name\" prio=X"
                # Example: "52: monitor description=\"Speaker + Headphones\" prio=1000"
                match = re.search(r'^\*?\s*(\d+):\s+(\w+)\s+description="([^"]+)"', line)
                if match:
                    idx = match.group(1)
                    dtype = match.group(2)
                    desc = match.group(3)
                    
                    # Store descriptive entry
                    devices.append((idx, f"{dtype}: {desc}", 1))
        except:
            pass

    # Standard fallback using sounddevice
    try:
        import sounddevice as sd
        device_list = sd.query_devices()
        for idx, device in enumerate(device_list):
            if device['max_input_channels'] > 0:
                # Avoid duplicates if we already found it via pw-record
                if not any(str(idx) == str(d[0]) for d in devices):
                    devices.append((idx, device['name'], device['max_input_channels']))
    except Exception:
        pass
        
    return devices


def select_audio_device_interactive():
    """
    Find the best system playback source (monitor/loopback).
    STRICT: Never fallback to a microphone.
    """
    global SELECTED_AUDIO_DEVICE
    if SELECTED_AUDIO_DEVICE is not None:
        return SELECTED_AUDIO_DEVICE

    try:
        sources_out = subprocess.check_output(['pactl', 'list', 'short', 'sources'], text=True)
        monitors = []
        
        for line in sources_out.split('\n'):
            if not line.strip(): continue
            parts = line.split('\t')
            if len(parts) < 2: continue
            name = parts[1]

            # ONLY ACCEPT MONITOR/LOOPBACK
            if 'monitor' in name.lower() or 'loopback' in name.lower():
                # Explicitly REJECT anything that looks like a microphone input
                # if 'mic' in name.lower() or 'input' in name.lower() and 'monitor' not in name.lower():
                #     continue
                monitors.append(name)

        # 1. ALWAYS PREFER THE DEFAULT SINK MONITOR
        try:
            default_sink = subprocess.check_output(['pactl', 'get-default-sink'], text=True).strip()
            default_monitor = f"{default_sink}.monitor"
            for mon in monitors:
                if mon == default_monitor:
                    SELECTED_AUDIO_DEVICE = mon
                    # Ensure unmuted and volume is up (done once at selection, not per-capture)
                    try:
                        subprocess.run(['pactl', 'set-source-mute', mon, '0'], stderr=subprocess.DEVNULL)
                        subprocess.run(['pactl', 'set-source-volume', mon, '100%'], stderr=subprocess.DEVNULL)
                    except Exception:
                        pass
                    print(f"✓ Using Default Sink: {SELECTED_AUDIO_DEVICE}")
                    return mon
        except Exception:
            pass

        # 2. Prefer HDMI/External monitors
        for mon in monitors:
            if 'hdmi' in mon.lower() or 'external' in mon.lower():
                SELECTED_AUDIO_DEVICE = mon
                print(f"✓ Using External: {SELECTED_AUDIO_DEVICE}")
                return mon

        # 3. Prefer any monitor
        if monitors:
            SELECTED_AUDIO_DEVICE = monitors[0]
            print(f"✓ Using Fallback: {SELECTED_AUDIO_DEVICE}")
            return monitors[0]

    except Exception as e:
        print(f"Selection error: {e}")

    # If NO system playback device is found, return None. 
    print("❌ No system monitor found!")
    return None


def flush_audio_buffers():
    """Flush audio buffers to prevent bleeding across questions."""
    try:
        import sounddevice as sd
        sd.stop()
        time.sleep(0.05)
    except Exception:
        pass


class VoiceActivityDetector:
    """Voice Activity Detection using WebRTC VAD."""

    def __init__(self, aggressiveness=3):
        self.vad = webrtcvad.Vad(aggressiveness)
        self.sample_rate = SAMPLE_RATE

    def is_speech(self, audio_chunk):
        """Check if audio chunk contains speech."""
        if audio_chunk.dtype == np.float32:
            audio_int16 = (audio_chunk * 32767).astype(np.int16)
        else:
            audio_int16 = audio_chunk.astype(np.int16)

        try:
            return self.vad.is_speech(audio_int16.tobytes(), self.sample_rate)
        except Exception:
            return False


def record_until_silence(
    max_duration=25.0,
    silence_duration=None,
    vad_aggressiveness=2,
    device=None,
    on_speech_start=None,   # Zero Latency Mode: called once when speech is first detected
):
    """
    Record audio until silence detected.
    Uses sounddevice for reliable cross-platform audio capture.
    """
    import sounddevice as sd
    global SELECTED_AUDIO_DEVICE, current_silence_duration

    if silence_duration is None:
        silence_duration = current_silence_duration

    # Ensure system device is selected
    if device is None:
        if SELECTED_AUDIO_DEVICE is None:
            device = select_audio_device_interactive()
        else:
            device = SELECTED_AUDIO_DEVICE

    # If no system device found, enter silent wait state
    if device is None:
        time.sleep(1.0)
        return np.array([], dtype=np.float32)

    vad = VoiceActivityDetector(vad_aggressiveness)
    audio_chunks = []
    silence_chunks = 0
    max_silence_chunks = int(silence_duration * 1000 / CHUNK_DURATION_MS)

    start_time = time.time()
    speech_detected = False
    leading_chunks = deque(maxlen=int(500 / CHUNK_DURATION_MS)) # 500ms lead buffer

    # --- LINUX PAREC CAPTURE (MOST RELIABLE FOR MONITORS) ---
    if sys.platform.startswith('linux') and device and ('monitor' in device.lower() or 'loopback' in device.lower()):
        process = None
        try:
            # Volume/mute already set in select_audio_device_interactive()
            cmd = f"parec -d {device} --format=s16le --rate=16000 --channels=1"
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=True)
            
            print(f"  [CAPTURE] Listening to system audio via parec on {device}")
            
            while True:
                if time.time() - start_time > max_duration:
                    break
                
                # s16le = 2 bytes per sample
                raw_bytes = process.stdout.read(CHUNK_SIZE * 2)
                if not raw_bytes:
                    break
                
                # Convert s16le to float32 normalized [-1, 1]
                chunk_int = np.frombuffer(raw_bytes, dtype=np.int16)
                chunk = chunk_int.astype(np.float32) / 32768.0

                raw_rms = np.sqrt(np.mean(chunk**2))

                # Amplify in-place and derive amplified RMS from raw (avoids recompute)
                chunk *= 15.0
                rms = raw_rms * 15.0

                # Volume bar (Calibrated for weak monitor audio)
                bar_len = int(min(rms * 100, 30))
                bar = "#" * bar_len
                if silence_chunks % 30 == 0 and os.environ.get('VERBOSE'):
                    print(f"\r  [VOL] {bar:<30} (rms={rms:.4f}, raw={raw_rms:.5f})", end="", flush=True)
                else:
                    print(f"\r  [VOL] {bar:<30} ({rms:.5f})", end="", flush=True)

                is_speech_chunk = False
                if rms > MIN_VOLUME_THRESHOLD:
                    is_speech_chunk = vad.is_speech(chunk)
                    # Override: If volume is even slightly elevated, it's speech
                    if not is_speech_chunk and rms > 0.04:
                        is_speech_chunk = True

                if is_speech_chunk:
                    if not speech_detected:
                        if os.environ.get('VERBOSE'): print("\n  [DETECTED] Speech started...")
                        # Prepend the lead buffer
                        audio_chunks.extend(list(leading_chunks))
                        leading_chunks.clear()
                        # Zero Latency Mode: fire callback immediately on speech start
                        if on_speech_start:
                            import threading as _thr
                            _thr.Thread(target=on_speech_start, daemon=True).start()
                    silence_chunks = 0
                    speech_detected = True
                    audio_chunks.append(chunk)
                else:
                    silence_chunks += 1
                    if speech_detected:
                        audio_chunks.append(chunk)
                    else:
                        leading_chunks.append(chunk)

                if speech_detected and silence_chunks > int(silence_duration * 1000 / CHUNK_DURATION_MS):
                    break
                if len(audio_chunks) > 200: # ~6 seconds max for YouTube
                    break

            if audio_chunks:
                full_audio = np.concatenate(audio_chunks)

                # Peak normalization for Whisper
                max_val = np.abs(full_audio).max()
                if max_val > 0.01:
                    full_audio = full_audio / max_val * 0.9
                return full_audio
            return np.array([], dtype=np.float32)

        except Exception as e:
            if os.environ.get('VERBOSE'): print(f"\nParec error: {e}")
            return np.array([], dtype=np.float32)
        finally:
            if process:
                try:
                    process.terminate()
                except:
                    pass

    # --- FALLBACK: SOUNDDEVICE ---
    stream = None
    try:
        stream = sd.InputStream(
            device=device,
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype='float32',
            blocksize=CHUNK_SIZE
        )
        stream.start()
        print(f"  [LISTEN] Using {device}")

        while True:
            if time.time() - start_time > max_duration:
                break

            chunk, overflowed = stream.read(CHUNK_SIZE)
            chunk = chunk.flatten()
            chunk = chunk * 2.0 # Digital gain

            rms = np.sqrt(np.mean(chunk**2))

            bar_len = int(min(rms * 100, 30))
            bar = "#" * bar_len
            print(f"\r  [VOL] {bar:<30} ({rms:.5f})", end="", flush=True)

            is_speech_chunk = False
            if rms > MIN_VOLUME_THRESHOLD:
                is_speech_chunk = vad.is_speech(chunk)
                if not is_speech_chunk and rms > 0.025:
                    is_speech_chunk = True

            if is_speech_chunk:
                silence_chunks = 0
                speech_detected = True
                audio_chunks.append(chunk)
            else:
                silence_chunks += 1
                if speech_detected and silence_chunks < 15:
                    audio_chunks.append(chunk)

            if speech_detected and silence_chunks > int(silence_duration * 1000 / CHUNK_DURATION_MS):
                break
            if len(audio_chunks) > 500:
                break

        if audio_chunks:
            return np.concatenate(audio_chunks)
        return np.array([], dtype=np.float32)

    except Exception as e:
        if os.environ.get('VERBOSE'): print(f"\nCapture error: {e}")
        return np.array([], dtype=np.float32)
    finally:
        if stream:
            try:
                stream.stop()
                stream.close()
            except:
                pass


def record_until_silence_parec(
    max_duration=25.0,
    silence_duration=None,
    vad_aggressiveness=2,
    device=None
):
    """
    DEPRECATED: Old parec-based recording. Kept for reference.
    Uses 'parec' on Linux - has low capture levels on some HP laptops.
    """
    global SELECTED_AUDIO_DEVICE, current_silence_duration

    if silence_duration is None:
        silence_duration = current_silence_duration

    vad = VoiceActivityDetector(vad_aggressiveness)
    audio_chunks = []
    silence_chunks = 0
    max_silence_chunks = int(silence_duration * 1000 / CHUNK_DURATION_MS)

    start_time = time.time()

    if device is None:
        if SELECTED_AUDIO_DEVICE is None:
            select_audio_device_interactive()
        device = SELECTED_AUDIO_DEVICE

    # PULSEAUDIO/PIPEWIRE RECORDING (LINUX - USING PAREC)
    if sys.platform.startswith('linux'):
        # Ensure unmuted and volume is up
        try:
            subprocess.run(['pactl', 'set-source-mute', str(device), '0'], stderr=subprocess.DEVNULL)
            subprocess.run(['pactl', 'set-source-volume', str(device), '100%'], stderr=subprocess.DEVNULL)
        except: pass
        
        # Use simple parec for maximum stability on HP laptops
        cmd = f"parec -d {device} --format=float32le --rate=16000 --channels=1"
        
        process = None
        try:
            import select
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                shell=True
            )
            
            bytes_to_read = CHUNK_SIZE * 4
            last_log_time = 0
            limit = max_silence_chunks # Initialize limit
            speech_detected = False  # Track if we've heard any speech
            
            while True:
                # 1. Check total duration
                if time.time() - start_time > max_duration:
                    break
                
                # 2. Wait for data with short timeout (200ms)
                r, _, _ = select.select([process.stdout], [], [], 0.2)
                if not r:
                    # No data available right now, count as silence
                    silence_chunks += 1
                    # SELF-HEALING: If we are silent for 4s, the ID might have changed
                    if silence_chunks > 20 and len(audio_chunks) < 1:
                        if os.environ.get('VERBOSE'): print("\n[Self-Healing] Device silent. Resetting selection...")
                        SELECTED_AUDIO_DEVICE = None # Force re-detect next time
                        break
                    
                    if silence_chunks > limit and len(audio_chunks) > 3:
                        break
                    continue

                raw_bytes = process.stdout.read(bytes_to_read)
                if not raw_bytes:
                    break
                
                chunk = np.frombuffer(raw_bytes, dtype=np.float32)

                # 3. VOLUME GATE & MONITOR
                rms = np.sqrt(np.mean(chunk**2))

                # Activity Indicator (Show Name + Volume)
                bar_len = int(min(rms * 30, 15))  # Scale for mic levels
                bar = "#" * bar_len
                # label = "MIC" if 'input' in str(device).lower() else str(device).split('.')[-1][:8]
                # print(f"\r  [{label:8}] {bar:<15} ({rms:.4f})", end="", flush=True)

                is_speech_chunk = False

                # Use VAD for microphone (reliable with normal audio levels)
                if rms > MIN_VOLUME_THRESHOLD:
                    is_speech_chunk = vad.is_speech(chunk)
                
                # 4. Silence Logic
                if is_speech_chunk:
                    if os.environ.get('VERBOSE') and silence_chunks > 5:
                       print(f"  [LISTEN] Using {device}")
                    silence_chunks = 0
                    speech_detected = True  # We've heard actual speech
                    audio_chunks.append(chunk)
                else:
                    silence_chunks += 1
                    # Keep generous silence padding to capture word transitions
                    if silence_chunks < 15:
                        audio_chunks.append(chunk)

                # SELF-HEALING: If absolute silence persists for 1.2s (40 chunks)
                # and we haven't started capturing a question yet, reset the ID scan
                if rms < MIN_VOLUME_THRESHOLD and silence_chunks > 40 and len(audio_chunks) < 2:
                    SELECTED_AUDIO_DEVICE = None
                    break

                # Break logic - 1s silence after speech ends
                limit = max_silence_chunks 
                
                # HARD CAP: If we have > 7s of audio, process it now
                # This prevents "waiting forever" if music is playing
                if len(audio_chunks) > 230: # ~7 seconds
                    if os.environ.get('VERBOSE'): print("\n[Auto-Trigger] Long audio, processing...")
                    break

                # Only break on silence if we've detected actual speech AND captured enough audio
                # Require at least 0.5s of audio (about 17 chunks at 30ms each) before allowing silence break
                min_chunks_for_break = 17  # ~0.5 seconds of audio
                if speech_detected and silence_chunks > limit and len(audio_chunks) > min_chunks_for_break:
                    if os.environ.get('VERBOSE'): print(f"\n[Silence] Breaking after {silence_chunks} silence chunks (speech detected, {len(audio_chunks)} chunks)")
                    break

                # Debug: Show silence progress periodically (only if waiting for speech to end)
                if os.environ.get('VERBOSE') and speech_detected and silence_chunks > 0 and silence_chunks % 10 == 0:
                    print(f" [silence:{silence_chunks}/{limit}]", end="", flush=True)
                    
        except Exception as e:
            if os.environ.get('VERBOSE'):
                print(f"Capture error: {e}")
        finally:
            if process:
                try:
                    process.terminate()
                    process.wait(timeout=0.2)
                except:
                    pass

        if audio_chunks:
            return np.concatenate(audio_chunks)
        return np.array([], dtype=np.float32)

    # FALLBACK/OTHER PLATFORMS (PortAudio)
    actual_samplerate = SAMPLE_RATE
    stream_opened = False
    stream = None

    try:
        import sounddevice as sd
        # Try requested sample rate first
        try:
            stream = sd.InputStream(
                device=device,
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype='float32',
                blocksize=CHUNK_SIZE
            )
            stream.start()
            stream_opened = True
        except Exception:
            # Fallback to common sample rates if 16kHz is rejected
            for fallback_rate in [44100, 48000]:
                try:
                    stream = sd.InputStream(
                        device=device,
                        samplerate=fallback_rate,
                        channels=CHANNELS,
                        dtype='float32'
                    )
                    stream.start()
                    actual_samplerate = fallback_rate
                    stream_opened = True
                    break
                except Exception:
                    continue

        if not stream_opened:
            return np.array([], dtype=np.float32)

        active_chunk_size = int(actual_samplerate * CHUNK_DURATION_MS / 1000)
        
        while True:
            if time.time() - start_time > max_duration:
                break

            chunk, _ = stream.read(active_chunk_size)
            
            if actual_samplerate != SAMPLE_RATE:
                resampled_chunk = scipy.signal.resample(
                    chunk, 
                    int(len(chunk) * SAMPLE_RATE / actual_samplerate)
                ).astype(np.float32)
                process_chunk = resampled_chunk
            else:
                process_chunk = chunk

            audio_chunks.append(process_chunk)

            if vad.is_speech(process_chunk):
                silence_chunks = 0
            else:
                silence_chunks += 1

            if silence_chunks > max_silence_chunks and len(audio_chunks) > 10:
                break

    except Exception:
        pass
    finally:
        if stream:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    if audio_chunks:
        return np.concatenate(audio_chunks)
    return np.array([], dtype=np.float32)
