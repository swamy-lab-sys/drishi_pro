#!/bin/bash
# ─────────────────────────────────────────────
#  Drishi — Full Restore after OS Reinstall
#  Run once:  bash SETUP.sh
# ─────────────────────────────────────────────

set -e

echo "=== 1. Install system dependencies ==="
sudo apt update -y
sudo apt install -y git python3.10 python3.10-venv python3-pip docker.io curl

sudo usermod -aG docker $USER
echo "NOTE: Log out and back in if docker permission errors occur"

echo ""
echo "=== 2. Start PostgreSQL (Docker on port 5434) ==="
docker rm -f drishi-pg 2>/dev/null || true
docker run -d \
  --name drishi-pg \
  --restart always \
  -e POSTGRES_USER=drishi \
  -e POSTGRES_PASSWORD=drishi \
  -e POSTGRES_DB=drishi \
  -p 5434:5432 \
  postgres:14
sleep 3
echo "✓ PostgreSQL running at localhost:5434"

echo ""
echo "=== 3. Create .env ==="
# Load real keys from KEYS.txt if present (not in git)
ANTHROPIC_KEY="YOUR_ANTHROPIC_KEY"
DEEPGRAM_KEY="YOUR_DEEPGRAM_KEY"
SARVAM_KEY="YOUR_SARVAM_KEY"
NGROK_DOMAIN="particulate-arely-unrenovative.ngrok-free.dev"

if [ -f KEYS.txt ]; then
  source KEYS.txt
  echo "  ✓ Loaded keys from KEYS.txt"
else
  echo "  ⚠ KEYS.txt not found — fill in API keys in .env manually after setup"
fi

cat > .env << EOF
ANTHROPIC_API_KEY=$ANTHROPIC_KEY
DEEPGRAM_API_KEY=$DEEPGRAM_KEY
SARVAM_API_KEY=$SARVAM_KEY
NGROK_DOMAIN=$NGROK_DOMAIN
DATABASE_URL=postgresql://drishi:drishi@localhost:5434/drishi

STT_BACKEND=sarvam
LLM_MODEL_OVERRIDE=claude-haiku-4-5-20251001
SILENCE_DEFAULT=0.6
MAX_RECORDING_DURATION=11.0
CODING_LANGUAGE=python
AUDIO_SOURCE=system
USE_NGROK=false
INTERVIEW_ROLE=python
SARVAM_LANGUAGE=en-IN
USER_ID_OVERRIDE=3
EOF
echo "✓ .env created"

echo ""
echo "=== 4. Setup Python venv & dependencies ==="
python3.10 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "✓ Dependencies installed"

echo ""
echo "=== 5. Restore database backup (if exists) ==="
if [ -f ~/qa_pairs_backup.db ]; then
    mkdir -p ~/.drishi
    cp ~/qa_pairs_backup.db ~/.drishi/qa_pairs.db
    echo "✓ qa_pairs.db restored from backup"
else
    echo "  (no backup found at ~/qa_pairs_backup.db — skipping)"
fi

echo ""
echo "══════════════════════════════════════"
echo "  ✓ Setup complete!  Run:  ./run.sh"
echo "══════════════════════════════════════"
