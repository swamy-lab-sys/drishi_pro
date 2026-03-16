# ─────────────────────────────────────────────────────────────────────────────
#  Interview Voice Assistant — Docker Image
#  Packages: Web UI + LLM pipeline + Q&A Database
#  Audio capture runs on the HOST and feeds via PulseAudio socket.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

LABEL maintainer="Interview Voice Assistant"
LABEL description="AI-powered real-time interview assistant"

# ── System packages ────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        python3-dev \
        portaudio19-dev \
        libsndfile1 \
        libgomp1 \
        ffmpeg \
        pulseaudio-utils \
        alsa-utils \
        curl \
        wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python deps (cached layer — only rebuilds when requirements.txt changes) ──
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

# ── Application source ─────────────────────────────────────────────────────────
COPY *.py              ./
COPY web/              ./web/

RUN mkdir -p /root/.interview_assistant

EXPOSE 8000

ENV PYTHONUNBUFFERED=1 \
    PYTHONWARNINGS=ignore

# ── Health check ───────────────────────────────────────────────────────────────
HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -sf http://localhost:8000/api/answers > /dev/null || exit 1

# ── Default: start the full pipeline (audio + web server) ─────────────────────
CMD ["python3", "-W", "ignore", "main.py"]
