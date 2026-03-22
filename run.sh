#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  Drishi Enterprise — Universal Startup Script
#  Supports: Local · Screen Share · Chrome Extension · ngrok Global
#  Works on: Ubuntu / Debian / macOS
# ═══════════════════════════════════════════════════════════════════

set -e
cd "$(dirname "$0")"

# ── Colors ────────────────────────────────────────────────────────
G='\033[0;32m'; Y='\033[0;33m'; B='\033[0;34m'; R='\033[0;31m'
C='\033[0;36m'; W='\033[1;37m'; D='\033[0m'

# ── Banner ────────────────────────────────────────────────────────
clear
echo ""
echo -e "${G}╔═══════════════════════════════════════════════╗${D}"
echo -e "${G}║${W}       DRISHI  —  Interview Intelligence       ${G}║${D}"
echo -e "${G}╚═══════════════════════════════════════════════╝${D}"
echo ""

# ═══════════════════════════════════════════════════════════════════
#  STEP 1 — Python & venv
# ═══════════════════════════════════════════════════════════════════
if ! command -v python3 &>/dev/null; then
    echo -e "${R}ERROR: python3 not found.${D}"
    echo "Install: sudo apt install python3 python3-pip python3-venv"
    exit 1
fi
PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo -e "  ${G}✓${D} Python $PYTHON_VERSION"

if [ ! -d "venv" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv venv
fi
source venv/bin/activate
echo -e "  ${G}✓${D} venv activated"

# ═══════════════════════════════════════════════════════════════════
#  STEP 2 — Dependencies (hash-cached for fast startup)
# ═══════════════════════════════════════════════════════════════════
if [[ "$OSTYPE" == "linux"* ]]; then
    if ! dpkg -s portaudio19-dev &>/dev/null 2>&1; then
        echo "  Installing system audio libs (requires sudo)..."
        sudo apt-get install -y portaudio19-dev python3-dev libsndfile1 2>/dev/null || true
    fi
fi
REQ_HASH_FILE="venv/.req_hash"
REQ_HASH_NOW=$(md5sum requirements.txt 2>/dev/null | cut -d' ' -f1)
REQ_HASH_SAVED=$(cat "$REQ_HASH_FILE" 2>/dev/null || echo "")
if [ "$REQ_HASH_NOW" != "$REQ_HASH_SAVED" ]; then
    echo "  Installing dependencies..."
    python3 -m pip install -q --upgrade pip
    python3 -m pip install -q -r requirements.txt
    echo "$REQ_HASH_NOW" > "$REQ_HASH_FILE"
fi
echo -e "  ${G}✓${D} Dependencies ready"

# ═══════════════════════════════════════════════════════════════════
#  STEP 3 — Load .env
# ═══════════════════════════════════════════════════════════════════
if [ -f ".env" ]; then
    set -a; source <(grep -v '^#' .env | grep -v '^$'); set +a
fi

# ── Test mode ─────────────────────────────────────────────────────
if [ "$1" = "tests" ]; then
    python3 -m pytest tests/ -v; exit
fi

# ── Check Anthropic key ───────────────────────────────────────────
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo -e "\n${R}ERROR: ANTHROPIC_API_KEY not set.${D}"
    echo "  Add it to .env:  ANTHROPIC_API_KEY=sk-ant-..."
    echo "  Get key at: https://console.anthropic.com/"
    exit 1
fi
echo -e "  ${G}✓${D} Anthropic key loaded"

# ── PostgreSQL (required) ─────────────────────────────────────────
if [ -z "$DATABASE_URL" ]; then
    echo -e "\n${R}ERROR: DATABASE_URL not set.${D}"
    echo "  Add to .env:  DATABASE_URL=postgresql://drishi:drishi@localhost:5434/drishi"
    exit 1
fi

# Auto-start drishi-pg Docker container if not running
if command -v docker &>/dev/null; then
    if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^drishi-pg$'; then
        echo -e "  ${Y}⚡${D} Starting PostgreSQL container (drishi-pg)..."
        docker start drishi-pg > /dev/null 2>&1 || \
        docker run -d --name drishi-pg \
            -e POSTGRES_DB=drishi \
            -e POSTGRES_USER=drishi \
            -e POSTGRES_PASSWORD=drishi \
            -p 5434:5432 \
            postgres:14 > /dev/null 2>&1 || true
        # Wait for PG to be ready (up to 10s)
        for _i in 1 2 3 4 5; do
            sleep 2
            python3 -c "
import pg8000.dbapi, os
from urllib.parse import urlparse
u = urlparse(os.environ['DATABASE_URL'])
try:
    pg8000.dbapi.connect(host=u.hostname, port=u.port or 5432,
        user=u.username, password=u.password,
        database=u.path.lstrip('/'), ssl_context=False).close()
    print('ok')
except: pass
" 2>/dev/null | grep -q ok && break
        done
    fi
fi

# Verify connection
PG_OK=$(python3 -c "
import pg8000.dbapi, os
from urllib.parse import urlparse
u = urlparse(os.environ.get('DATABASE_URL',''))
try:
    pg8000.dbapi.connect(host=u.hostname, port=u.port or 5432,
        user=u.username, password=u.password,
        database=u.path.lstrip('/'), ssl_context=False).close()
    print('ok')
except Exception as e:
    print(f'err:{e}')
" 2>/dev/null)

if [[ "$PG_OK" == ok ]]; then
    echo -e "  ${G}✓${D} PostgreSQL connected (${DATABASE_URL%%@*}@...)"
else
    echo -e "\n${R}ERROR: Cannot connect to PostgreSQL.${D}"
    echo "  DATABASE_URL=$DATABASE_URL"
    echo "  Error: ${PG_OK#err:}"
    echo ""
    echo "  Start PostgreSQL:"
    echo "  docker run -d --name drishi-pg -e POSTGRES_DB=drishi \\"
    echo "    -e POSTGRES_USER=drishi -e POSTGRES_PASSWORD=drishi \\"
    echo "    -p 5434:5432 postgres:14"
    exit 1
fi

# ── Detect local IP ───────────────────────────────────────────────
LOCAL_IP=$(python3 -c "import socket; s=socket.socket(); s.settimeout(3); s.connect(('8.8.8.8',80)); print(s.getsockname()[0])" 2>/dev/null || echo "127.0.0.1")
WEB_PORT="${WEB_PORT:-8000}"

# ── Free port 8000 if Docker containers are holding it ────────────
if fuser "${WEB_PORT}/tcp" &>/dev/null 2>&1; then
    docker stop drishi-flask 2>/dev/null && sleep 1 || true
fi

# ═══════════════════════════════════════════════════════════════════
#  STEP 4 — Read launch config from .env (set via Admin UI → Settings → Launch)
# ═══════════════════════════════════════════════════════════════════

# All these are already sourced from .env above — just apply defaults if missing
AUDIO_SOURCE="${AUDIO_SOURCE:-system}"
USE_NGROK="${USE_NGROK:-false}"
STT_BACKEND="${STT_BACKEND:-local}"
STT_MODEL="${STT_MODEL_OVERRIDE:-Systran/faster-distil-whisper-small.en}"
LLM_MODEL="${LLM_MODEL_OVERRIDE:-claude-haiku-4-5-20251001}"
USER_ID_OVERRIDE="${USER_ID_OVERRIDE:-}"

# ── Interactive ngrok selection ───────────────────────────────────
# Determine default option from .env
if [ "$USE_NGROK" = "true" ]; then
    [ -n "$NGROK_DOMAIN" ] && NGROK_DEFAULT="3" || NGROK_DEFAULT="2"
else
    NGROK_DEFAULT="1"
fi

echo -e "  ${W}Select access mode:${D}"
[ "$NGROK_DEFAULT" = "1" ] && TAG1=" ${G}← current${D}" || TAG1=""
[ "$NGROK_DEFAULT" = "2" ] && TAG2=" ${G}← current${D}" || TAG2=""
[ "$NGROK_DEFAULT" = "3" ] && TAG3=" ${G}← current${D}" || TAG3=""
echo -e "  [1] Local / LAN only (no ngrok)$TAG1"
echo -e "  [2] Local + ngrok (random URL)$TAG2"
[ -n "$NGROK_DOMAIN" ] && \
echo -e "  [3] Local + ngrok (fixed: $NGROK_DOMAIN)$TAG3"

read -r -t 10 -p "  Choose [1/2/3] (auto in 10s: $NGROK_DEFAULT): " NGROK_CHOICE || true
NGROK_CHOICE="${NGROK_CHOICE:-$NGROK_DEFAULT}"

case "$NGROK_CHOICE" in
    1) USE_NGROK=false; NGROK_DOMAIN=""  ;;
    2) USE_NGROK=true;  NGROK_DOMAIN=""  ;;
    3) USE_NGROK=true                    ;;  # keeps NGROK_DOMAIN from .env
    *) USE_NGROK=false; NGROK_DOMAIN=""  ;;
esac
echo ""

export AUDIO_SOURCE

# Derive mode label for display
case "${AUDIO_SOURCE}:${USE_NGROK}" in
    system:true)     MODE_LABEL="System audio + ngrok" ;;
    extension:true)  MODE_LABEL="Chrome Extension + ngrok" ;;
    extension:false) MODE_LABEL="Chrome Extension" ;;
    *)               MODE_LABEL="Same laptop (system audio)" ;;
esac

# Map LLM ID to friendly label
case "$LLM_MODEL" in
    *sonnet*) LLM_LABEL="Sonnet 4.6" ;;
    *)        LLM_LABEL="Haiku 4.5"  ;;
esac

# Map STT backend to friendly label
case "$STT_BACKEND" in
    sarvam)     STT_LABEL="Sarvam (saarika:v2.5 · Indian EN)" ;;
    deepgram)   STT_LABEL="Deepgram (nova-3 · cloud)" ;;
    assemblyai) STT_LABEL="AssemblyAI (cloud)" ;;
    *)          STT_LABEL="$STT_MODEL (local whisper)" ;;
esac

echo ""
echo -e "  ${G}✓${D} Mode:  $MODE_LABEL"
echo -e "  ${G}✓${D} STT:   $STT_LABEL"
echo -e "  ${G}✓${D} LLM:   $LLM_LABEL"
[ -n "$USER_ID_OVERRIDE" ] && echo -e "  ${G}✓${D} User ID: $USER_ID_OVERRIDE"
echo ""
echo -e "  ${C}Tip: Change all settings at http://localhost:$WEB_PORT/settings → Launch Config${D}"

# ── Audio Setup (Linux PulseAudio) ────────────────────────────────
if [ "$AUDIO_SOURCE" = "system" ]; then
    if [[ "$OSTYPE" == "linux"* ]] && command -v pactl &>/dev/null; then
        pkill pw-loopback 2>/dev/null || true
        OLD_MOD=$(pactl list short modules 2>/dev/null | grep 'sink_name=iva_call' | awk '{print $1}')
        [ -n "$OLD_MOD" ] && pactl unload-module "$OLD_MOD" 2>/dev/null || true
        DEFAULT_SINK=$(pactl get-default-sink 2>/dev/null || echo "")
        if [ -n "$DEFAULT_SINK" ]; then
            export PULSE_SOURCE="${DEFAULT_SINK}.monitor"
            ORIGINAL_SOURCE=$(pactl get-default-source 2>/dev/null || echo "")
            trap '[[ -n "$ORIGINAL_SOURCE" ]] && pactl set-default-source "$ORIGINAL_SOURCE" 2>/dev/null || true' EXIT
            echo -e "  ${G}✓${D} Audio: system monitor (${DEFAULT_SINK}.monitor)"
        fi
    fi
elif [ "$AUDIO_SOURCE" = "extension" ]; then
    echo -e "  ${G}✓${D} Audio: Chrome extension (no local capture)"
fi

# ═══════════════════════════════════════════════════════════════════
#  STEP 9 — Data / DB
# ═══════════════════════════════════════════════════════════════════
mkdir -p ~/.drishi
rm -f ~/.drishi/current_answer.json ~/.drishi/history.json 2>/dev/null || true
echo -e "  ${G}✓${D} Fresh session ready"

# ═══════════════════════════════════════════════════════════════════
#  STEP 10 — ngrok (only if needed)
# ═══════════════════════════════════════════════════════════════════
NGROK_URL=""
NGROK_PID=""

if [ "$USE_NGROK" = "true" ]; then
    if ! command -v ngrok &>/dev/null; then
        echo ""
        echo -e "${Y}  ngrok not found — install it for global access:${D}"
        echo "  1. https://ngrok.com/download  (free account)"
        echo "  2. ngrok config add-authtoken YOUR_TOKEN"
        echo "  3. Re-run this script"
        echo ""
        echo -e "  ${Y}Continuing without ngrok (LAN access only)...${D}"
        USE_NGROK=false
    else
        echo ""
        echo -e "  Starting ngrok tunnel..."
        # Kill any stale ngrok process (free tier: only 1 tunnel allowed)
        pkill -x ngrok 2>/dev/null || true; sleep 0.5
        if [ -n "$NGROK_DOMAIN" ]; then
            ngrok http --url="$NGROK_DOMAIN" --request-header-add "ngrok-skip-browser-warning: true" "$WEB_PORT" --log=stdout > /tmp/ngrok.log 2>&1 &
        else
            ngrok http --request-header-add "ngrok-skip-browser-warning: true" "$WEB_PORT" --log=stdout > /tmp/ngrok.log 2>&1 &
        fi
        NGROK_PID=$!

        # Wait for URL (up to 10s) — use Python urllib (avoids libssl crash from curl)
        for _i in 1 2 3 4 5; do
            sleep 2
            NGROK_URL=$(python3 -c "
import urllib.request, json, sys
try:
    with urllib.request.urlopen('http://localhost:4040/api/tunnels', timeout=3) as r:
        d = json.load(r)
    t = [x for x in d.get('tunnels',[]) if x.get('proto')=='https']
    print(t[0]['public_url'] if t else '')
except: print('')
" 2>/dev/null || echo "")
            [ -n "$NGROK_URL" ] && break
        done

        if [ -n "$NGROK_URL" ]; then
            echo -e "  ${G}✓${D} ngrok tunnel active"
        else
            echo -e "  ${Y}⚠${D} ngrok URL unavailable — using LAN only"
        fi
    fi
fi

# ═══════════════════════════════════════════════════════════════════
#  STEP 11 — Export env vars & start system agent
# ═══════════════════════════════════════════════════════════════════
export STT_MODEL_OVERRIDE="$STT_MODEL"
export STT_BACKEND="$STT_BACKEND"
export LLM_MODEL_OVERRIDE="$LLM_MODEL"
export ENABLE_MONITORING="true"
export NGROK_URL="$NGROK_URL"
export PYTHONWARNINGS="ignore"
export PYTHONPYCACHEPREFIX="/tmp/drishi_pycache"

# Start system agent (if display available)
# Kill any leftover agent processes from previous runs first
pkill -f "agent_host.py" 2>/dev/null || true; sleep 0.5

AGENT_PID=""
if [ -n "$DISPLAY" ]; then
    # Resolve XAUTHORITY if not set (common in GNOME/GDM sessions)
    if [ -z "$XAUTHORITY" ]; then
        if [ -f "${HOME}/.Xauthority" ]; then
            export XAUTHORITY="${HOME}/.Xauthority"
        else
            export XAUTHORITY="/run/user/$(id -u)/gdm/Xauthority"
        fi
    fi
    DISPLAY="$DISPLAY" XAUTHORITY="$XAUTHORITY" \
    NGROK_DOMAIN="" MONITOR_SERVER_URL="http://localhost:$WEB_PORT" \
        ./venv/bin/python3 agent_host.py default >> /tmp/drishi_agent.log 2>&1 &
    AGENT_PID=$!
fi

# Cleanup trap — AGENT_PID is intentionally excluded:
# `exec` fires EXIT before replacing the shell, which would kill the agent instantly.
# The agent is self-managing (reconnects) and is killed by pkill on next ./run.sh.
trap 'kill $NGROK_PID 2>/dev/null || true
      [[ -n "$ORIGINAL_SOURCE" ]] && pactl set-default-source "$ORIGINAL_SOURCE" 2>/dev/null || true' EXIT

# ═══════════════════════════════════════════════════════════════════
#  READY — Show all access URLs
# ═══════════════════════════════════════════════════════════════════
echo ""
echo -e "${G}╔═══════════════════════════════════════════════╗${D}"
echo -e "${G}║             DRISHI IS STARTING                ║${D}"
echo -e "${G}╠═══════════════════════════════════════════════╣${D}"
echo -e "${G}║${D}                                               ${G}║${D}"
echo -e "${G}║${D}  ${W}YOUR MACHINE (Interview UI):${D}                 ${G}║${D}"
echo -e "${G}║${D}  ${C}http://localhost:$WEB_PORT${D}                      ${G}║${D}"

if [ "$LOCAL_IP" != "127.0.0.1" ]; then
echo -e "${G}║${D}                                               ${G}║${D}"
echo -e "${G}║${D}  ${W}SAME NETWORK (LAN):${D}                          ${G}║${D}"
echo -e "${G}║${D}  ${C}http://$LOCAL_IP:$WEB_PORT${D}                  ${G}║${D}"
fi

if [ -n "$NGROK_URL" ]; then
echo -e "${G}║${D}                                               ${G}║${D}"
echo -e "${G}║${D}  ${W}GLOBAL (anywhere on internet):${D}               ${G}║${D}"
echo -e "${G}║${D}  ${C}$NGROK_URL${D}"
fi

echo -e "${G}║${D}                                               ${G}║${D}"
echo -e "${G}║${D}  ${W}MONITOR (phone / 2nd screen):${D}                ${G}║${D}"
if [ -n "$NGROK_URL" ]; then
echo -e "${G}║${D}  ${C}$NGROK_URL/monitor${D}"
else
echo -e "${G}║${D}  ${C}http://$LOCAL_IP:$WEB_PORT/monitor${D}           ${G}║${D}"
fi

if [ "$AUDIO_SOURCE" = "extension" ]; then
echo -e "${G}║${D}                                               ${G}║${D}"
echo -e "${G}║${D}  ${W}CHROME EXTENSION — set server URL to:${D}        ${G}║${D}"
if [ -n "$NGROK_URL" ]; then
echo -e "${G}║${D}  ${C}$NGROK_URL${D}"
else
echo -e "${G}║${D}  ${C}http://$LOCAL_IP:$WEB_PORT${D}                   ${G}║${D}"
fi
fi

echo -e "${G}║${D}                                               ${G}║${D}"
echo -e "${G}║${D}  ${W}SCREEN SHARE TIP:${D}                            ${G}║${D}"
echo -e "${G}║${D}  Share browser tab (not full screen) in       ${G}║${D}"
echo -e "${G}║${D}  Meet/Teams/Zoom. Keep Drishi in another tab. ${G}║${D}"
echo -e "${G}║${D}                                               ${G}║${D}"
echo -e "${G}║${D}  Press ${R}Ctrl+C${D} to stop                          ${G}║${D}"
echo -e "${G}╚═══════════════════════════════════════════════╝${D}"
echo ""

# Fix libssl3 3.0.2-0ubuntu1.21 relocation bug (0x808 should be 0x008)
if [ ! -f /tmp/libssl.so.3 ]; then
  python3 -c "
import struct, shutil
src='/lib/x86_64-linux-gnu/libssl.so.3'
dst='/tmp/libssl.so.3'
with open(src,'rb') as f: data=bytearray(f.read())
e_shoff=struct.unpack_from('<Q',data,40)[0]; e_shentsize=struct.unpack_from('<H',data,58)[0]
e_shnum=struct.unpack_from('<H',data,60)[0]; e_shstrndx=struct.unpack_from('<H',data,62)[0]
shstr_off=struct.unpack_from('<Q',data,e_shoff+e_shstrndx*e_shentsize+24)[0]
rela_offset=rela_size=None
for i in range(e_shnum):
  sh=e_shoff+i*e_shentsize; sh_name=struct.unpack_from('<I',data,sh)[0]
  name=data[shstr_off+sh_name:].split(b'\x00')[0].decode()
  if name=='.rela.dyn':
    rela_offset=struct.unpack_from('<Q',data,sh+24)[0]; rela_size=struct.unpack_from('<Q',data,sh+32)[0]; break
if rela_offset:
  for i in range(rela_size//24):
    off=rela_offset+i*24; ri=struct.unpack_from('<Q',data,off+8)[0]
    if ri&0xffffffff==0x808: struct.pack_into('<Q',data,off+8,(ri&0xffffffff00000000)|8)
with open(dst,'wb') as f: f.write(data)
" 2>/dev/null && echo "  ✓ libssl relocation fix applied" || true
fi

exec env LD_LIBRARY_PATH=/tmp python3 -W ignore main.py
