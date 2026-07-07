#!/bin/bash
# ====================================================================
# setup.sh — One-command setup for Free Video Dubbing Tool
# Run this after cloning the repo:
#   git clone https://github.com/Hadi990op/Free-Video-Dubbing-tool.git
#   cd Free-Video-Dubbing-tool
#   bash setup.sh
# ====================================================================

set -e

echo "╔══════════════════════════════════════════════════════════╗"
echo "║     🎬 Free Video Dubbing Tool — Setup                  ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ---- 1. Check Python ----
echo "▶ Checking Python..."
if command -v python3 &>/dev/null; then
    PYTHON=python3
    PY_VER=$(python3 --version 2>&1)
    echo "  ✓ Found $PY_VER"
else
    echo "  ✗ Python 3 not found. Installing..."
    apt-get update -qq && apt-get install -y python3 python3-pip python3-venv
    PYTHON=python3
fi

# ---- 2. Check ffmpeg ----
echo "▶ Checking ffmpeg..."
if command -v ffmpeg &>/dev/null; then
    echo "  ✓ ffmpeg found: $(ffmpeg -version 2>&1 | head -1)"
else
    echo "  ✗ ffmpeg not found. Installing..."
    apt-get update -qq && apt-get install -y ffmpeg
    echo "  ✓ ffmpeg installed"
fi

# ---- 3. Create virtual environment ----
echo "▶ Creating virtual environment..."
if [ ! -d "venv" ]; then
    $PYTHON -m venv venv
    echo "  ✓ Virtual environment created"
else
    echo "  ✓ Virtual environment already exists"
fi

# Activate venv
source venv/bin/activate
PIP="pip"

# ---- 4. Install Python dependencies ----
echo "▶ Installing Python dependencies (this may take a few minutes)..."
echo ""
echo "  Installing PyTorch (for faster-whisper & voice cloning)..."
$PIP install torch torchaudio --index-url https://download.pytorch.org/whl/cpu --quiet 2>/dev/null || \
$PIP install torch torchaudio --quiet

echo "  Installing remaining dependencies..."
$PIP install -r requirements.txt --quiet

echo "  ✓ All Python dependencies installed"
echo ""

# ---- 5. Create runtime directories ----
echo "▶ Creating runtime directories..."
mkdir -p uploads outputs
echo "  ✓ uploads/ and outputs/ created"

# ---- 6. Pre-download Whisper model (optional, speeds up first run) ----
echo "▶ Pre-downloading Whisper 'base' model (optional)..."
$PYTHON -c "
try:
    from faster_whisper import WhisperModel
    print('  Downloading base model...')
    WhisperModel('base', device='cpu', compute_type='int8')
    print('  ✓ Whisper base model ready')
except Exception as e:
    print(f'  ⚠ Skipped ({e})')
    print('  Model will auto-download on first run')
" 2>/dev/null || echo "  ⚠ Will auto-download on first run"

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║     ✅ Setup Complete!                                   ║"
echo "╠══════════════════════════════════════════════════════════╣"
echo "║                                                          ║"
echo "║  To start the web UI:                                    ║"
echo "║     bash run.sh                                          ║"
echo "║                                                          ║"
echo "║  Then open: http://localhost:5050                         ║"
echo "║                                                          ║"
echo "║  Or use the CLI:                                          ║"
echo "║     source venv/bin/activate                             ║"
echo "║     python dubber.py video.mp4 --target-lang hi           ║"
echo "║                                                          ║"
echo "╚══════════════════════════════════════════════════════════╝"
