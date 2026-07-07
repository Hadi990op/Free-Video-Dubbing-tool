#!/bin/bash
# ====================================================================
# run.sh — Start the Free Video Dubbing Tool web UI
# ====================================================================

set -e

cd "$(dirname "$0")"

# Required env vars for local voice cloning (Coqui XTTS-v2, non-commercial CPML)
export COQUI_TOS_AGREED=1
export HF_HUB_DISABLE_TELEMETRY=1

# Activate virtual environment
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
else
    echo "⚠ Virtual environment not found. Run 'bash setup.sh' first."
    exit 1
fi

# Check ffmpeg
if ! command -v ffmpeg &>/dev/null; then
    echo "✗ ffmpeg not found. Install it: sudo apt-get install ffmpeg"
    exit 1
fi

PORT=${1:-5050}
WORKERS=${2:-1}
THREADS=${3:-8}

echo "╔══════════════════════════════════════════════════════════╗"
echo "║  🎬 Starting Free Video Dubbing Tool                    ║"
echo "║  📍 http://localhost:$PORT                                ║"
echo "║  🔧 Workers: $WORKERS | Threads: $THREADS                    ║"
echo "╚══════════════════════════════════════════════════════════╝"

exec gunicorn web_ui:app \
    --bind 0.0.0.0:$PORT \
    --workers $WORKERS \
    --threads $THREADS \
    --timeout 0 \
    --worker-class gthread \
    --access-logfile - \
    --error-logfile -
