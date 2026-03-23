"""ElevenLabs TTS client for earpiece mode.

Converts text to speech using ElevenLabs streaming API and returns raw MP3
audio bytes. Designed to be called from the /ws/tts WebSocket endpoint.

Flag-gated by ELEVENLABS_ENABLED (default: false) — server runs normally
without this even when enabled=false or API key is missing.

Usage:
    chunks = list(stream_tts("Hello world"))   # yields bytes chunks
    audio_bytes = tts_text("Hello world")      # returns all bytes at once
"""

from __future__ import annotations

import os
from typing import Generator

import config


def is_enabled() -> bool:
    return bool(config.ELEVENLABS_ENABLED and config.ELEVENLABS_API_KEY)


def stream_tts(text: str, voice_id: str = None) -> Generator[bytes, None, None]:
    """Stream MP3 audio chunks from ElevenLabs for the given text.

    Yields raw bytes chunks as they arrive from the API.
    Raises RuntimeError if TTS is not enabled or API call fails.
    """
    if not is_enabled():
        raise RuntimeError("ElevenLabs TTS is not enabled")

    api_key = config.ELEVENLABS_API_KEY
    vid = voice_id or config.ELEVENLABS_VOICE_ID

    try:
        import httpx

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}/stream"
        headers = {
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload = {
            "text": text,
            "model_id": "eleven_turbo_v2",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.0,
                "use_speaker_boost": True,
            },
        }
        with httpx.stream("POST", url, json=payload, headers=headers, timeout=30.0) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_bytes(chunk_size=4096):
                if chunk:
                    yield chunk
    except ImportError:
        # Fall back to requests if httpx not available
        import requests  # type: ignore

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}/stream"
        headers = {
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload = {
            "text": text,
            "model_id": "eleven_turbo_v2",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
            },
        }
        with requests.post(url, json=payload, headers=headers, stream=True, timeout=30) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_content(chunk_size=4096):
                if chunk:
                    yield chunk


def tts_text(text: str, voice_id: str = None) -> bytes:
    """Return all MP3 audio bytes for the given text (non-streaming)."""
    return b"".join(stream_tts(text, voice_id=voice_id))
