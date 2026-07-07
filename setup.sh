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
echo "  Installing PyTorch (pinned <2.9 for CPU — torch 2.9+ needs torchcodec"
echo "  native libs that fail on CPU-only/minimal VMs)..."
$PIP install "torch>=2.0,<2.9" "torchaudio>=2.0,<2.9" --index-url https://download.pytorch.org/whl/cpu --quiet 2>/dev/null || \
$PIP install "torch>=2.0,<2.9" "torchaudio>=2.0,<2.9" --quiet

echo "  Installing remaining dependencies..."
$PIP install -r requirements.txt --quiet

echo "  ✓ All Python dependencies installed"
echo ""

# ---- 4.5. Add swap on low-RAM VMs (XTTS-v2 needs ~1.8GB; swap prevents OOM) ----
TOTAL_RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
if [ "$TOTAL_RAM_KB" -lt 4000000 ] && [ ! -f /swapfile ]; then
    echo "▶ Low-RAM machine detected ($(echo "scale=1;$TOTAL_RAM_KB/1048576"|bc)GB). Creating 4GB swap..."
    fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null 2>&1 && swapon /swapfile
    grep -q "/swapfile" /etc/fstab || echo "/swapfile none swap sw 0 0" >> /etc/fstab
    echo "  ✓ Swap enabled (persists across reboots)"
fi

# ---- 4.6. Agree to Coqui CPML license (non-commercial) so XTTS-v2 loads ----
# This enables local voice cloning without an interactive prompt.
echo "▶ Configuring Coqui XTTS-v2 (non-commercial CPML license)..."
grep -q 'COQUI_TOS_AGREED' ~/.bashrc 2>/dev/null || echo 'export COQUI_TOS_AGREED=1' >> ~/.bashrc
echo 'export COQUI_TOS_AGREED=1' >> venv/bin/activate 2>/dev/null || true
echo "  ✓ Coqui license agreed (non-commercial use)"

# ---- 4.7. Install OpenVoice V2 (fast voice cloning, primary backend) ----
echo "▶ Installing OpenVoice V2 (fast voice cloning, MIT license, all languages)..."
OV_DIR="$(dirname "$0")/../OpenVoice"
if [ ! -d "$OV_DIR" ]; then
    git clone https://github.com/myshell-ai/OpenVoice.git "$OV_DIR" 2>/dev/null
fi
if [ -d "$OV_DIR" ]; then
    $PIP install -e "$OV_DIR" --no-deps --quiet 2>/dev/null
    # Download V2 checkpoints (~200MB)
    $PYTHON -c "
from huggingface_hub import snapshot_download
import os
ov_dir = os.path.join('$OV_DIR', 'checkpoints_v2')
if not os.path.exists(os.path.join(ov_dir, 'converter', 'checkpoint.pth')):
    print('  Downloading OpenVoice V2 checkpoints...')
    snapshot_download(repo_id='myshell-ai/OpenVoiceV2', local_dir=ov_dir)
    print('  ✓ OpenVoice V2 checkpoints downloaded')
else:
    print('  ✓ OpenVoice V2 checkpoints already present')
" 2>/dev/null
    echo "  ✓ OpenVoice V2 installed (primary voice cloning backend)"
else
    echo "  ⚠ OpenVoice V2 clone failed — will use XTTS-v2 fallback"
fi

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
