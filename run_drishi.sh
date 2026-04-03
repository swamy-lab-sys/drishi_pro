#!/bin/bash
# Drishi launcher — prevents display freeze on HP Laptop 15s (Tiger Lake i3-1115G4)
#
# WHY THIS EXISTS:
#   The system has 2 physical cores (each with HT → 4 logical CPUs).
#   When Drishi's Whisper fallback or scikit-learn runs, it saturates all 4 CPUs,
#   starving the i915 display driver → display freezes.
#
# WHAT THIS DOES (without modifying Drishi):
#   - Pins Drishi to physical core 0 (logical CPUs 0,2) via taskset
#   - Limits OpenMP/MKL/ctranslate2 threads to 2 via env vars
#   - Runs at lower CPU priority (nice +10) so display gets CPU first
#   - Physical core 1 (CPUs 1,3) stays free for GNOME + i915 driver

set -e

# ── Thread limits for all numerical libraries ────────────────────────────────
export OMP_NUM_THREADS=2           # NumPy / SciPy / scikit-learn (OpenMP)
export MKL_NUM_THREADS=2           # Intel MKL
export OPENBLAS_NUM_THREADS=2      # OpenBLAS
export NUMEXPR_NUM_THREADS=2       # NumExpr
export CT2_INTRA_THREADS=2         # CTranslate2 / faster-whisper fallback
export CT2_INTER_THREADS=1         # CTranslate2 parallel sessions

# ── Launch Drishi pinned to physical core 0 (CPUs 0,2) at lower priority ────
# nice -n 10  → yield CPU to display/GNOME when contested
# taskset -c 0,2 → physical core 0 HT siblings only; core 1 (CPUs 1,3) free
echo "[launcher] Drishi restricted to CPUs 0,2 (core 0). Display CPUs 1,3 (core 1) reserved."
exec nice -n 10 taskset -c 0,2 bash "$(dirname "$0")/run.sh" "$@"
