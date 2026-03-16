#!/bin/bash

set -e
cd "$(dirname "$0")"

# ─────────────────────────────────────────────────────────────────
#  Drishi Pro — Startup Script
#  Works on: Ubuntu / Debian / macOS
#  Usage:  ./run.sh
# ─────────────────────────────────────────────────────────────────

echo ""
echo "============================================================"
echo "   DRISHI PRO"
echo "============================================================"
echo ""

# ── 1. Check Python ───────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found."
    echo "Install it: sudo apt install python3 python3-pip python3-venv"
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "  ✓ Python $PYTHON_VERSION found"

# ── 2. Create virtual environment if missing ──────────────────────
if [ ! -d "venv" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv venv
    echo "  ✓ venv created"
fi

# Activate venv
source venv/bin/activate
echo "  ✓ venv activated"

# ── 3. Install / upgrade dependencies ────────────────────────────
echo ""
echo "  Checking dependencies..."

# Install system-level audio deps on Linux if missing
if [[ "$OSTYPE" == "linux"* ]]; then
    if ! dpkg -s portaudio19-dev &>/dev/null 2>&1; then
        echo "  Installing system audio libraries (requires sudo)..."
        sudo apt-get install -y portaudio19-dev python3-dev libsndfile1 2>/dev/null || true
    fi
fi

# Hash-based install: only re-run pip when requirements.txt actually changed.
# This makes subsequent launches ~5x faster (skips pip resolver on every start).
REQ_HASH_FILE="venv/.req_hash"
REQ_HASH_NOW=$(md5sum requirements.txt 2>/dev/null | cut -d' ' -f1)
REQ_HASH_SAVED=$(cat "$REQ_HASH_FILE" 2>/dev/null || echo "")

if [ "$REQ_HASH_NOW" != "$REQ_HASH_SAVED" ]; then
    echo "  requirements.txt changed — installing dependencies..."
    python3 -m ensurepip --upgrade -q 2>/dev/null || true
    python3 -m pip install -q --upgrade pip
    python3 -m pip install -q -r requirements.txt
    echo "$REQ_HASH_NOW" > "$REQ_HASH_FILE"
    echo "  ✓ Dependencies updated"
else
    echo "  ✓ Dependencies up-to-date (no changes)"
fi

# Install deepgram-sdk only if DEEPGRAM_API_KEY is set
if grep -q "^DEEPGRAM_API_KEY=.\+" .env 2>/dev/null; then
    python3 -m pip install -q deepgram-sdk
fi

echo "  ✓ All dependencies ready"

# ── 4. Load .env ──────────────────────────────────────────────────
if [ -f ".env" ]; then
    set -a
    source <(grep -v '^#' .env | grep -v '^$')
    set +a
fi

# ── 5. Check API key ──────────────────────────────────────────────
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo ""
    echo "  ERROR: ANTHROPIC_API_KEY is not set."
    echo ""
    echo "  Fix: Add it to .env file:"
    echo "    echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env"
    echo ""
    echo "  Get your key at: https://console.anthropic.com/"
    exit 1
fi
echo "  ✓ Anthropic API key loaded"

# ── 6. Model selection ────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  CONFIGURATION"
echo "============================================================"
echo ""

echo "  STT Model (Speech-to-Text):"
echo "    1) tiny.en          - Fastest offline (~200ms), basic accuracy"
echo "    2) small.en         - Good offline accuracy (~700ms)"
echo "    3) Sarvam AI        - Indian accent optimized (~900ms)  (needs SARVAM_API_KEY)"
echo "    4) Deepgram Nova-3  - ~250ms + highest accuracy  ★ RECOMMENDED  (needs DEEPGRAM_API_KEY)"
echo "                          Free: \$200 credit (~780 hrs). Paid: \$0.0043/min (~\$0.26/interview)"
echo ""

# Auto-select Deepgram if key is set, else Sarvam
_DEFAULT_STT=3
if [ -n "$DEEPGRAM_API_KEY" ]; then
    _DEFAULT_STT=4
fi
read -p "  STT choice [1-4, default=$_DEFAULT_STT]: " stt_choice
[ -z "$stt_choice" ] && stt_choice=$_DEFAULT_STT

STT_BACKEND="local"
case "$stt_choice" in
    1) STT_MODEL="tiny.en" ;;
    2) STT_MODEL="small.en" ;;
    3)
        if [ -z "$SARVAM_API_KEY" ]; then
            echo "  ERROR: SARVAM_API_KEY not in .env. Get free key at dashboard.sarvam.ai"
            exit 1
        fi
        STT_MODEL="sarvam-saarika-v2"; STT_BACKEND="sarvam" ;;
    4)
        if [ -z "$DEEPGRAM_API_KEY" ]; then
            echo ""
            echo "  ── Deepgram Setup ──────────────────────────────────────────"
            echo "  1. Go to: https://console.deepgram.com/signup"
            echo "  2. Sign up (free — \$200 credit, no credit card needed)"
            echo "  3. Create an API key"
            echo "  4. Add to .env:  DEEPGRAM_API_KEY=your_key_here"
            echo "  ────────────────────────────────────────────────────────────"
            echo ""
            exit 1
        fi
        STT_MODEL="deepgram-nova-3"; STT_BACKEND="deepgram" ;;
    *)
        echo "  Invalid choice. Please enter 1, 2, 3 or 4."
        exit 1 ;;
esac
echo "  ✓ STT: $STT_MODEL"

echo ""
echo "  LLM Model (Answer Generation):"
echo "    1) Haiku 4.5   - Fastest  (~1s/answer)"
echo "    2) Sonnet 4.6  - Best quality + fast  (~2s/answer)  ★ recommended"
echo ""
read -p "  LLM choice [1-2, default=1]: " llm_choice

case "$llm_choice" in
    2) LLM_MODEL="claude-sonnet-4-6";         LLM_LABEL="Sonnet 4.6" ;;
    *) LLM_MODEL="claude-haiku-4-5-20251001"; LLM_LABEL="Haiku 4.5" ;;
esac
echo "  ✓ LLM: $LLM_LABEL"

echo ""
echo "  Select User Profile:"

# ── Load users from DB directly (no module import — instant) ─────
_QA_DB="$HOME/.drishi/qa_pairs.db"
[ ! -f "$_QA_DB" ] && _QA_DB="qa_pairs.db"

# Build indexed list:  "<menu_num>|<db_id>|<name>|<role>|<has_intro>"
_USER_INDEX=$(python3 -c "
import sqlite3, sys
db = sys.argv[1]
try:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = con.execute('SELECT id, name, role, self_introduction FROM users ORDER BY id').fetchall()
    con.close()
    for i, r in enumerate(rows, 1):
        has_intro = '1' if (r['self_introduction'] or '').strip() else '0'
        role = (r['role'] or 'no role')[:35]
        name = (r['name'] or '')[:20]
        print(f'{i}|{r[\"id\"]}|{name}|{role}|{has_intro}')
except Exception:
    pass
" "$_QA_DB" 2>/dev/null)

_USER_COUNT=$(echo "$_USER_INDEX" | grep -c '|' 2>/dev/null || echo 0)

if [ "$_USER_COUNT" -eq 0 ]; then
    echo "  (No profiles yet — open http://localhost:8000/users to create one)"
    echo ""
    export USER_ID_OVERRIDE=""
else
    # Print menu
    while IFS='|' read -r num db_id name role has_intro; do
        [ -z "$num" ] && continue
        intro_flag=""
        [ "$has_intro" = "1" ] && intro_flag=" ✓"
        printf "    %s) %-22s %s%s\n" "$num" "$name" "$role" "$intro_flag"
    done <<< "$_USER_INDEX"
    echo ""

    # Auto-select if only one user
    if [ "$_USER_COUNT" -eq 1 ]; then
        _FIRST=$(echo "$_USER_INDEX" | head -1)
        IFS='|' read -r _n _auto_id _auto_name _auto_role _hi <<< "$_FIRST"
        read -p "  Select user [default=1, Enter to skip]: " _pick
        if [ -z "$_pick" ] || [ "$_pick" = "1" ]; then
            export USER_ID_OVERRIDE="$_auto_id"
            echo "  ✓ User: $_auto_name"
        else
            export USER_ID_OVERRIDE=""
            echo "  ✓ No user profile"
        fi
    else
        read -p "  Select user [1-$_USER_COUNT, Enter to skip]: " _pick
        if [ -z "$_pick" ]; then
            export USER_ID_OVERRIDE=""
            echo "  ✓ No user profile"
        else
            _MATCHED=$(echo "$_USER_INDEX" | awk -F'|' -v p="$_pick" '$1==p {print $2"|"$3}')
            if [ -n "$_MATCHED" ]; then
                IFS='|' read -r _sel_id _sel_name <<< "$_MATCHED"
                export USER_ID_OVERRIDE="$_sel_id"
                echo "  ✓ User: $_sel_name"
            else
                export USER_ID_OVERRIDE=""
                echo "  ✓ No user profile"
            fi
        fi
    fi
fi

export STT_MODEL_OVERRIDE="$STT_MODEL"
export STT_BACKEND="$STT_BACKEND"
export LLM_MODEL_OVERRIDE="$LLM_MODEL"

# ── 7. Configure audio source ─────────────────────────────────────
# Set PULSE_SOURCE to the speaker output monitor so the app captures
# system audio (interviewer's voice from the call) via PulseAudio.
if [[ "$OSTYPE" == "linux"* ]] && command -v pactl &>/dev/null; then
    # Clean up any leftover virtual sinks from old sessions
    pkill pw-loopback 2>/dev/null || true
    OLD_MOD=$(pactl list short modules 2>/dev/null | grep 'sink_name=iva_call' | awk '{print $1}')
    [ -n "$OLD_MOD" ] && pactl unload-module "$OLD_MOD" 2>/dev/null || true

    DEFAULT_SINK=$(pactl get-default-sink 2>/dev/null || echo "")
    if [ -n "$DEFAULT_SINK" ]; then
        MONITOR="${DEFAULT_SINK}.monitor"
        export PULSE_SOURCE="$MONITOR"
        # Save original default source so we can restore it on exit
        ORIGINAL_SOURCE=$(pactl get-default-source 2>/dev/null || echo "")
        # Restore mic for other apps when Drishi exits
        trap '[[ -n "$ORIGINAL_SOURCE" ]] && pactl set-default-source "$ORIGINAL_SOURCE" 2>/dev/null || true' EXIT
        echo "  ✓ Audio: system monitor ($MONITOR)"
    else
        echo "  ⚠ Could not detect default sink — using default audio input"
    fi
else
    echo "  ⚠ Non-Linux — using default audio input"
fi

# ── 8. Ensure data directory and QA database ──────────────────────
mkdir -p ~/.drishi
# Keep DB in sync: copy from project if ~/.drishi/qa_pairs.db is missing or older
if [ ! -f ~/.drishi/qa_pairs.db ] || [ qa_pairs.db -nt ~/.drishi/qa_pairs.db ]; then
    cp qa_pairs.db ~/.drishi/qa_pairs.db 2>/dev/null || true
fi

# ── 9. Clear stale session data ───────────────────────────────────
echo ""
echo "  Clearing previous session data..."
rm -f ~/.drishi/current_answer.json 2>/dev/null || true
rm -f ~/.drishi/history.json 2>/dev/null || true
echo "  ✓ Fresh session ready"

# ── 9. Launch ─────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  Starting server at: http://localhost:8000  ← open this in Chrome"
echo "  ⚠  Use localhost:8000 NOT 0.0.0.0:8000 (Chrome blocks SSE on 0.0.0.0)"
echo "  Press Ctrl+C to stop"
echo "============================================================"
echo ""

export PYTHONWARNINGS="ignore"
# Redirect .pyc cache to a writable location so the corrupt system
# argparse.cpython-310.pyc is never loaded (Python rewrites it cleanly).
export PYTHONPYCACHEPREFIX="/tmp/drishi_pycache"
exec python3 -W ignore main.py
