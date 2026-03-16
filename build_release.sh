#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
#  Drishi Pro — Release Packager
#  Creates a distributable zip for any system (Linux/macOS/WSL2).
#
#  Usage:
#    chmod +x build_release.sh
#    ./build_release.sh
#
#  Output: Drishi-v<VERSION>.zip
# ─────────────────────────────────────────────────────────────────────────────

set -e
cd "$(dirname "$0")"

VERSION="2.0"
PACKAGE_NAME="Drishi-v${VERSION}"
OUT_DIR="/tmp/${PACKAGE_NAME}"
ZIP_FILE="${PACKAGE_NAME}.zip"

echo ""
echo "============================================================"
echo "  BUILDING RELEASE PACKAGE: ${PACKAGE_NAME}"
echo "============================================================"
echo ""

# ── Clean up old build ────────────────────────────────────────────────────────
rm -rf "${OUT_DIR}" "${ZIP_FILE}"
mkdir -p "${OUT_DIR}"

# ── Copy Python source files ──────────────────────────────────────────────────
echo "  Copying source files..."
cp *.py                           "${OUT_DIR}/"
cp requirements.txt               "${OUT_DIR}/"
cp run.sh                         "${OUT_DIR}/"
cp Dockerfile                     "${OUT_DIR}/"
cp docker-compose.yml             "${OUT_DIR}/"

# ── Copy web UI ───────────────────────────────────────────────────────────────
echo "  Copying web UI..."
mkdir -p "${OUT_DIR}/web/templates"
cp web/server.py                  "${OUT_DIR}/web/"
cp web/templates/*.html           "${OUT_DIR}/web/templates/"

# ── Copy Chrome extension ─────────────────────────────────────────────────────
echo "  Copying Chrome extension..."
cp -r chrome_extension/           "${OUT_DIR}/chrome_extension/"

# ── Bundle Q&A database (pre-trained, 335 entries) ───────────────────────────
echo "  Bundling Q&A database..."
DB_SRC="${HOME}/.drishi/qa_pairs.db"
if [ -f "${DB_SRC}" ]; then
    cp "${DB_SRC}"                "${OUT_DIR}/qa_pairs.db"
    DB_SIZE=$(du -sh "${DB_SRC}" | cut -f1)
    echo "    ✓ qa_pairs.db (${DB_SIZE})"
else
    echo "    ⚠ qa_pairs.db not found at ${DB_SRC} — DB will be initialized fresh"
fi

# ── Create .env.example ───────────────────────────────────────────────────────
cat > "${OUT_DIR}/.env.example" << 'ENVEOF'
# ─────────────────────────────────────────────────────────────────────────────
#  Drishi Pro — Configuration
#  1. Copy this file:  cp .env.example .env
#  2. Fill in your API key below
#  3. Run:  ./run.sh
# ─────────────────────────────────────────────────────────────────────────────

# REQUIRED — get free key at: https://console.anthropic.com/
ANTHROPIC_API_KEY=sk-ant-YOUR-KEY-HERE

# OPTIONAL: Deepgram cloud STT (select option 6 in run.sh)
# Get free key: https://deepgram.com
# DEEPGRAM_API_KEY=

# OPTIONAL: Sarvam AI (Indian accent support — select option 7 in run.sh)
# Get free key: https://dashboard.sarvam.ai
# SARVAM_API_KEY=
ENVEOF

# ── Create setup script that runs on first launch ─────────────────────────────
cat > "${OUT_DIR}/setup.sh" << 'SETUPEOF'
#!/bin/bash
# First-time setup helper
set -e
cd "$(dirname "$0")"

echo ""
echo "============================================================"
echo "  Drishi Pro — First Time Setup"
echo "============================================================"
echo ""

# Copy qa_pairs.db to data dir
DATA_DIR="${HOME}/.drishi"
mkdir -p "${DATA_DIR}"
if [ -f "qa_pairs.db" ] && [ ! -f "${DATA_DIR}/qa_pairs.db" ]; then
    cp qa_pairs.db "${DATA_DIR}/qa_pairs.db"
    echo "  ✓ Q&A database installed ($(du -sh qa_pairs.db | cut -f1))"
elif [ -f "${DATA_DIR}/qa_pairs.db" ]; then
    echo "  ✓ Q&A database already exists at ${DATA_DIR}/"
fi

# Create .env if missing
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "  ⚠  .env file created from template."
    echo "  → Open .env and add your ANTHROPIC_API_KEY before running."
    echo ""
else
    echo "  ✓ .env exists"
fi

echo ""
echo "  Setup complete! Now run:  ./run.sh"
echo ""
SETUPEOF
chmod +x "${OUT_DIR}/setup.sh"

# ── Create quick-start README ─────────────────────────────────────────────────
cat > "${OUT_DIR}/README.txt" << 'READEOF'
╔══════════════════════════════════════════════════════════════╗
║           DRISHI PRO  v2.0                  ║
║      AI-powered real-time Python & DevOps interview helper  ║
╚══════════════════════════════════════════════════════════════╝

QUICK START (3 steps)
──────────────────────────────────────────────────────────────

STEP 1 — API Key
  cp .env.example .env
  # Edit .env and add your ANTHROPIC_API_KEY
  # Get free key: https://console.anthropic.com/

STEP 2 — Run (Linux / macOS / WSL2)
  chmod +x run.sh
  ./run.sh
  # Opens browser at http://localhost:8000

  OR with Docker:
  docker compose up
  # Then open http://localhost:8000

STEP 3 — Chrome Extension
  Open Chrome → Settings → Extensions → Developer Mode ON
  → "Load unpacked" → select the chrome_extension/ folder
  → Join Google Meet call and start interview!

──────────────────────────────────────────────────────────────
HOW IT WORKS

  Microphone/Speaker → Whisper STT → Claude AI → Answer UI
  Google Meet chat → Chrome Extension → Answer UI

  Voice: Interviewer speaks → answer appears automatically
  Chat:  Coding question pasted in Meet → answer + Programiz button

──────────────────────────────────────────────────────────────
CODING QUESTIONS (Programiz integration)

  When interviewer gives a coding question:
  1. Answer appears in the UI with "▶ Run in Programiz" button
  2. Click button → Programiz.com opens in a new tab
  3. In the Programiz editor, type:   #1   (or #2, #3...)
  4. Chrome extension auto-types the solution code!
  5. Or type  ##start  to auto-type the latest code answer

──────────────────────────────────────────────────────────────
REQUIREMENTS

  - Python 3.10+ (auto-installed in Docker)
  - Anthropic API key (free tier works)
  - Chrome browser + Extension (for chat detection)
  - Linux: PulseAudio/PipeWire for speaker monitoring
  - macOS: Works with default audio
  - Windows: Use WSL2 (Ubuntu) → then same as Linux

──────────────────────────────────────────────────────────────
KEYBOARD SHORTCUTS (Chrome Extension)

  Ctrl+Alt+Q  → Open assistant popup
  Ctrl+Alt+A  → Quick ask a question
  Ctrl+Alt+P  → Pause/Resume code typing

──────────────────────────────────────────────────────────────
PERFORMANCE

  DB-cached answers:   3–34ms  (330+ questions pre-loaded)
  Fresh AI answers:    1–3s    (Claude Haiku)
  Code generation:     2–5s    (Claude Sonnet)

READEOF

# ── Make run.sh executable ────────────────────────────────────────────────────
chmod +x "${OUT_DIR}/run.sh"

# ── Create the zip ────────────────────────────────────────────────────────────
echo "  Creating zip archive..."
cd /tmp
zip -qr "${OLDPWD}/${ZIP_FILE}" "${PACKAGE_NAME}/"
cd - > /dev/null

# ── Cleanup temp dir ──────────────────────────────────────────────────────────
rm -rf "${OUT_DIR}"

# ── Summary ───────────────────────────────────────────────────────────────────
ZIP_SIZE=$(du -sh "${ZIP_FILE}" | cut -f1)
echo ""
echo "============================================================"
echo "  ✓ Package built: ${ZIP_FILE} (${ZIP_SIZE})"
echo ""
echo "  Contents:"
unzip -l "${ZIP_FILE}" | grep -v "^Archive\|^--\|files$" | awk '{print "    "$4}' | head -40
echo ""
echo "  To distribute:"
echo "    Share ${ZIP_FILE} — recipient unzips and runs setup.sh + run.sh"
echo "============================================================"
echo ""
