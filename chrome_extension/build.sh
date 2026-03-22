#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
#  Drishi Enterprise — Extension Build Script
#  Produces a protected dist/ folder ready to be loaded as an unpacked
#  extension or packaged into a .crx file.
#
#  Protection layers:
#   1. JavaScript obfuscation  — variable renaming, string encoding,
#      dead-code injection, self-defending code.
#   2. Server-side extension-ID lock (set EXTENSION_ID in server .env).
#
#  Usage:
#    cd chrome_extension
#    ./build.sh          # creates dist/
#    ./build.sh --clean  # removes dist/ then rebuilds
# ─────────────────────────────────────────────────────────────────────────────

set -e
cd "$(dirname "$0")"

DIST="dist"

if [ "$1" = "--clean" ]; then
    echo "  Removing old dist/..."
    rm -rf "$DIST"
fi

mkdir -p "$DIST"

OBFUSCATOR="./node_modules/.bin/javascript-obfuscator"
if [ ! -f "$OBFUSCATOR" ]; then
    echo "  Installing javascript-obfuscator..."
    npm install --save-dev javascript-obfuscator --silent
fi

echo ""
echo "============================================================"
echo "  Drishi Extension — Protected Build"
echo "============================================================"
echo ""

# ── Copy static assets (unchanged) ───────────────────────────────────────────
echo "  Copying assets..."
for f in manifest.json popup.html audio_offscreen.html monitor_capture.html; do
    [ -f "$f" ] && cp "$f" "$DIST/$f"
done
for img in *.png; do
    [ -f "$img" ] && cp "$img" "$DIST/$img"
done

# ── Obfuscation settings ──────────────────────────────────────────────────────
# Full protection for main extension scripts
FULL_OBF_OPTS=(
    --compact true
    --control-flow-flattening true
    --control-flow-flattening-threshold 0.5
    --dead-code-injection true
    --dead-code-injection-threshold 0.3
    --identifier-names-generator hexadecimal
    --rename-globals false
    --self-defending true
    --string-array true
    --string-array-encoding rc4
    --string-array-rotate true
    --string-array-shuffle true
    --string-array-threshold 0.8
    --unicode-escape-sequence false
    --target browser-no-eval
)

# Light obfuscation for AudioWorklet (runs in restricted worklet context —
# no console, no browser globals, only AudioWorkletProcessor API)
WORKLET_OBF_OPTS=(
    --compact true
    --identifier-names-generator hexadecimal
    --rename-globals false
    --string-array false
    --self-defending false
    --target browser-no-eval
)

# ── Obfuscate each JS file ────────────────────────────────────────────────────
JS_MAIN=(
    background.js
    popup.js
    audio_offscreen.js
    coder_content.js
    monitor_content.js
    monitor_control_handler.js
    monitor_state.js
    monitor_capture.js
    monitor_webrtc_sender.js
    page_bridge.js
    typewriter.js
)

echo "  Obfuscating JS files..."
for js in "${JS_MAIN[@]}"; do
    if [ -f "$js" ]; then
        "$OBFUSCATOR" "$js" --output "$DIST/$js" "${FULL_OBF_OPTS[@]}" 2>/dev/null
        printf "    ✓ %-35s → %s\n" "$js" "obfuscated"
    fi
done

# AudioWorklet — light obfuscation only
if [ -f "audio_processor_worklet.js" ]; then
    "$OBFUSCATOR" audio_processor_worklet.js \
        --output "$DIST/audio_processor_worklet.js" \
        "${WORKLET_OBF_OPTS[@]}" 2>/dev/null
    printf "    ✓ %-35s → %s\n" "audio_processor_worklet.js" "minified"
fi

# ── Size report ───────────────────────────────────────────────────────────────
echo ""
echo "  Size comparison:"
for js in "${JS_MAIN[@]}" audio_processor_worklet.js; do
    if [ -f "$js" ] && [ -f "$DIST/$js" ]; then
        orig=$(wc -c < "$js")
        dist=$(wc -c < "$DIST/$js")
        printf "    %-35s %6d → %6d bytes\n" "$js" "$orig" "$dist"
    fi
done

echo ""
echo "  ✓ Protected build ready in: chrome_extension/dist/"
echo ""
echo "  To load in Chrome:"
echo "    1. Open chrome://extensions"
echo "    2. Enable Developer mode"
echo "    3. Click 'Load unpacked' → select chrome_extension/dist/"
echo ""
echo "  To package as .crx (locked to your signing key):"
echo "    chrome --pack-extension=dist/ --pack-extension-key=drishi.pem"
echo ""
