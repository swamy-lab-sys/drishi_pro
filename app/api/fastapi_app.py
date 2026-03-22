"""FastAPI entry point for Drishi Enterprise."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Depends, HTTPException, Header, Query, Body, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import config
from app.api.schemas.settings import (
    AudioSettings,
    AudioSettingsUpdate,
    JobDescription,
    ServerIP,
    SettingsUpdateResponse
)
from app.services import settings_service

app = FastAPI(
    title="Drishi Enterprise API",
    description="Modernized FastAPI backend for Drishi Enterprise",
    version="1.0.0"
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Auth Dependency ──────────────────────────────────────────────────────────

async def verify_secret_code(
    x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"),
    token: Optional[str] = Query(None)
):
    """Verify the secret code if configured."""
    secret = config.SECRET_CODE
    if not secret:
        return True
    
    provided_token = x_auth_token or token
    if provided_token != secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing secret code"
        )
    return True

# ── Settings Endpoints ───────────────────────────────────────────────────────

class CodingLanguage(BaseModel):
    language: str

@app.get("/api/coding_language", response_model=CodingLanguage, tags=["Settings"])
async def get_coding_language(auth: bool = Depends(verify_secret_code)):
    return {"language": config.CODING_LANGUAGE}

@app.post("/api/coding_language", response_model=CodingLanguage, tags=["Settings"])
async def set_coding_language(
    payload: CodingLanguage,
    auth: bool = Depends(verify_secret_code)
):
    lang = payload.language.lower().strip()
    allowed = {"python", "java", "javascript", "sql", "bash"}
    if lang not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown language. Allowed: {sorted(allowed)}"
        )
    config.CODING_LANGUAGE = lang
    os.environ["CODING_LANGUAGE"] = lang
    return {"language": lang}

class STTModelResponse(BaseModel):
    model: str
    changed: Optional[bool] = None

@app.get("/api/stt_model", response_model=STTModelResponse, tags=["Settings"])
async def get_stt_model(auth: bool = Depends(verify_secret_code)):
    try:
        import stt
        return {"model": stt.model_name or config.STT_MODEL}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@app.post("/api/stt_model", response_model=STTModelResponse, tags=["Settings"])
async def set_stt_model(
    payload: Dict[str, str],
    auth: bool = Depends(verify_secret_code)
):
    new_model = payload.get("model")
    if not new_model:
        raise HTTPException(status_code=400, detail="No model specified")
    
    allowed = ["tiny.en", "base.en", "small.en", "medium.en"]
    if new_model not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid model. Allowed: {allowed}")

    try:
        import stt
        old_model = stt.model_name or config.STT_MODEL
        if new_model == old_model:
            return {"model": old_model, "changed": False}

        config.STT_MODEL = new_model
        stt.DEFAULT_MODEL = new_model
        stt.load_model(new_model)
        return {"model": new_model, "changed": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@app.get("/api/audio_settings", response_model=AudioSettings, tags=["Settings"])
async def get_audio_settings(auth: bool = Depends(verify_secret_code)):
    return settings_service.get_audio_settings_payload()

@app.post("/api/audio_settings", response_model=SettingsUpdateResponse, tags=["Settings"])
async def set_audio_settings(
    data: Dict[str, Any], 
    auth: bool = Depends(verify_secret_code)
):
    # Using Dict for now to match the flexible update logic in service
    return settings_service.update_audio_settings(data)

@app.post("/api/save_jd", tags=["Settings"])
async def save_jd(
    payload: JobDescription,
    auth: bool = Depends(verify_secret_code)
):
    result, status_code = settings_service.save_job_description_payload({"text": payload.text})
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=result.get("error"))
    return result

@app.get("/api/get_jd", response_model=JobDescription, tags=["Settings"])
async def get_jd(auth: bool = Depends(verify_secret_code)):
    result, status_code = settings_service.get_job_description_payload()
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=result.get("error"))
    return result

@app.get("/api/ip", response_model=ServerIP, tags=["Settings"])
async def get_ip():
    # Public endpoint in Flask, keeping it public here
    return settings_service.get_server_ip_payload()

@app.get("/health", tags=["Ops"])
async def health():
    return {"status": "healthy", "version": "1.0.0"}
