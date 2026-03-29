Set up the Drishi project from scratch on a fresh OS. Do each step using Bash tool and confirm each one before moving on.

## Steps to execute

### 1. Install system packages
Run:
```
sudo apt update -y && sudo apt install -y git python3.10 python3.10-venv python3-pip docker.io curl ngrok
sudo usermod -aG docker $USER
```

### 2. Start PostgreSQL in Docker on port 5434
Run:
```
docker rm -f drishi-pg 2>/dev/null || true
docker run -d \
  --name drishi-pg \
  --restart always \
  -e POSTGRES_USER=drishi \
  -e POSTGRES_PASSWORD=drishi \
  -e POSTGRES_DB=drishi \
  -p 5434:5432 \
  postgres:14
```
Wait 3 seconds then verify with: `docker ps | grep drishi-pg`

### 3. Create .env file
Check if KEYS.txt exists in the project root.
- If YES: read KEYS.txt and use those values to write .env
- If NO: write .env with placeholder values and tell the user to fill in the API keys

The .env must contain:
```
ANTHROPIC_API_KEY=<from KEYS.txt or placeholder>
DEEPGRAM_API_KEY=<from KEYS.txt or placeholder>
SARVAM_API_KEY=<from KEYS.txt or placeholder>
NGROK_DOMAIN=particulate-arely-unrenovative.ngrok-free.dev
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
```

### 4. Set up Python virtual environment
Run:
```
python3.10 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 5. Restore database backup
Check if `~/qa_pairs_backup.db` exists.
- If YES: run `mkdir -p ~/.drishi && cp ~/qa_pairs_backup.db ~/.drishi/qa_pairs.db`
- If NO: tell the user no backup was found, the app will start with a fresh DB

### 6. Fix Python bytecode cache (common issue on fresh installs)
Run:
```
sudo rm -f /usr/lib/python3.10/__pycache__/functools.cpython-310.pyc
```

### 7. Verify everything
Run: `python3 -W ignore main.py --check 2>/dev/null || python3 -c "import config; print('config OK')" 2>/dev/null`
Then confirm: docker postgres is running, .env exists, venv is active.

### 8. Final message
Tell the user:
- Everything is set up
- Run `./run.sh` to start Drishi
- Open http://localhost:8000 in browser
- If docker permission error: log out and log back in, then run `./run.sh` again
