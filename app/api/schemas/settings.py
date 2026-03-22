"""Pydantic schemas for Settings API."""

from __future__ import annotations

from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class AudioSettings(BaseModel):
    silence_duration: float = Field(..., ge=0.3, le=4.0)
    max_duration: float = Field(..., ge=5.0, le=30.0)
    stt_backend: str
    stt_model: str
    app_mode: str
    cloud_mode: bool


class AudioSettingsUpdate(BaseModel):
    silence_duration: Optional[float] = Field(None, ge=0.3, le=4.0)
    max_duration: Optional[float] = Field(None, ge=5.0, le=30.0)
    stt_backend: Optional[str] = None
    stt_model: Optional[str] = None


class SettingsUpdateResponse(BaseModel):
    updated: Dict[str, Any]


class JobDescription(BaseModel):
    text: str


class ServerIP(BaseModel):
    ip: str
