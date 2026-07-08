# 🎬 Free Video Dubbing Tool

AI-powered video dubbing — translate and dub any video into 30+ languages. **100% free, no API keys required.**

![Python](https://img.shields.io/badge/Python-3.12+-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Free](https://img.shields.io/badge/100%25_Free-success)

## ✨ Features

- 🌍 **30+ languages** — Hindi, Spanish, French, Arabic, Chinese, Japanese, and more
- 🎙️ **Multi-speaker detection** — auto-detects speakers, assigns distinct voices to each
- 🎭 **Voice cloning** — clone the original speaker's voice and speak translated text in that voice
  - **6 fallback tiers**: Chatterbox V3 → IndexTTS-2 → XTTS-v2 (HF Space) → OpenVoice V2 → local XTTS-v2 → edge-tts
  - **Emotion control**: IndexTTS-2 inherits emotion from reference audio; Chatterbox V3 supports exaggeration parameter
- 🎵 **Background music preservation** — AI vocal isolation (Demucs htdemucs) separates speech from music/SFX
  - Original background music is kept and automatically **ducks** (sidechain compression) when voice is present
- 🎬 **Video extension** — extends video (freeze-frame) to fit longer dubbed audio instead of cutting audio
- 📝 **Subtitles** — generates SRT files in the target language
- 🔄 **Resume** — checkpoint system saves progress; resume after crashes/restarts
- 📊 **Real-time progress** — live progress bar and stage tracking
- 🔥 **Burned subtitles** — option to burn translated subtitles into the video
- 🚀 **Long video support** — handles 30min+ videos with memory-efficient processing
- 🌐 **Remote transcription** — offload Whisper to a second VM for faster processing
- 📱 **Web UI** — beautiful, responsive web interface

## 🛠️ Tech Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Speech-to-Text | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | Transcription with VAD filter |
| Translation | [Pollinations AI](https://pollinations.ai/) (LLM) + Google Translate (fallback) | Context-aware, natural translation |
| Text-to-Speech | [edge-tts](https://github.com/rany2/edge-tts) | Microsoft Edge TTS (free, no API key) |
| Voice Cloning | Chatterbox V3 / IndexTTS-2 / XTTS-v2 / OpenVoice V2 | Multi-tier voice cloning with emotion |
| Vocal Isolation | [Demucs](https://github.com/facebookresearch/demucs) (htdemucs) | Separate vocals from background music |
| Speaker Diarization | [simple-diarizer](https://github.com/cvqlai1/simple_diarizer) | Detect number of speakers |
| Audio Mixing | [ffmpeg](https://ffmpeg.org/) filters | Sidechain ducking, EQ, compression, crossfades |
| Video Processing | [ffmpeg](https://ffmpeg.org/) | Audio extraction, muxing, subtitles, freeze-frames |
| Web Framework | [Flask](https://flask.palletsprojects.com/) + Gunicorn | Web UI server |

## 🚀 Quick Start

### Option 1: One-Command Setup (Linux/Mac)

```bash
git clone https://github.com/Hadi990op/Free-Video-Dubbing-tool.git
cd Free-Video-Dubbing-tool
bash setup.sh
```

Then start the web UI:
```bash
bash run.sh
```

Open **http://localhost:5050** in your browser.

### Option 2: Manual Setup

```bash
git clone https://github.com/Hadi990op/Free-Video-Dubbing-tool.git
cd Free-Video-Dubbing-tool

# Install system dependencies
sudo apt-get update
sudo apt-get install -y ffmpeg python3 python3-pip python3-venv

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

# Create runtime directories
mkdir -p uploads outputs

# Start the web UI
gunicorn web_ui:app --bind 0.0.0.0:5050 --workers 1 --threads 8 --timeout 0 --worker-class gthread
```

### Option 3: Docker

```bash
git clone https://github.com/Hadi990op/Free-Video-Dubbing-tool.git
cd Free-Video-Dubbing-tool
docker-compose up --build
```

Open **http://localhost:5050** in your browser.

## 📖 Usage

### Web UI

1. Open `http://localhost:5050` in your browser
2. Upload a video file (MP4, AVI, MOV, etc.)
3. Select target language
4. Optionally enable:
   - **Keep background music** — AI separates vocals from music/SFX, keeps music and auto-ducks it under speech
   - **Burn subtitles** — burns translated subtitles into video
   - **Multi-speaker mode** — detects and assigns unique voices to each speaker
   - **Clone original voice** — uses AI to clone the original speaker's voice
   - **Extend video** — extends video to fit longer dubbed audio (no audio cutting)
5. Click "Start Dubbing"
6. Download the dubbed video + subtitles when done

### CLI

```bash
# Activate virtual environment
source venv/bin/activate

# Basic dubbing (English → Hindi)
python dubber.py input.mp4 --target-lang hi

# With multi-speaker detection
python dubber.py input.mp4 --target-lang hi --multi-speaker

# Clone original speaker's voice
python dubber.py input.mp4 --target-lang hi --voice-clone

# Clone voice + multi-speaker + extend video
python dubber.py input.mp4 --target-lang hi --voice-clone --multi-speaker

# Keep original background music + burn subtitles
python dubber.py input.mp4 --target-lang es --burn-subtitles

# Don't extend video (truncate audio to fit original timeline)
python dubber.py input.mp4 --target-lang hi --no-extend-video

# With checkpoint/resume support
python dubber.py input.mp4 --target-lang hi --job-dir ./myjob --resume

# Choose Whisper model size
python dubber.py input.mp4 --target-lang hi --model small  # tiny, base, small, medium, large

# List available voices for a language
python dubber.py --list-voices hi

# List all supported languages
python dubber.py --list-langs
```

### CLI Options

| Flag | Description | Default |
|------|-------------|---------|
| `--target-lang, -t` | Target language code | `hi` |
| `--voice, -v` | Edge-TTS voice name (auto-selected if not specified) | auto |
| `--model, -m` | Whisper model size: tiny, base, small, medium, large | `base` |
| `--output, -o` | Output video path | auto-generated |
| `--no-background` | Don't keep original background music | off (keeps music) |
| `--burn-subtitles` | Burn translated subtitles into video | off |
| `--no-srt` | Don't generate SRT subtitle file | off (generates SRT) |
| `--multi-speaker` | Enable multi-speaker detection (diarization) | off |
| `--num-speakers` | Force number of speakers (auto-detected if not set) | auto |
| `--voice-clone` | Clone original speaker's voice | off |
| `--no-extend-video` | Don't extend video to fit audio (truncates audio) | off (extends) |
| `--job-dir` | Job directory for checkpoints (enables resume) | none |
| `--resume` | Resume from last checkpoint in job-dir | off |
| `--list-voices [lang]` | List available TTS voices | — |
| `--list-langs` | List supported target languages | — |

## 🌍 Supported Languages

`hi` (Hindi), `es` (Spanish), `fr` (French), `de` (German), `ar` (Arabic), `zh` (Chinese), `ja` (Japanese), `ko` (Korean), `ru` (Russian), `pt` (Portuguese), `it` (Italian), `tr` (Turkish), `id` (Indonesian), `vi` (Vietnamese), `th` (Thai), `pl` (Polish), `nl` (Dutch), `sv` (Swedish), `bn` (Bengali), `ur` (Urdu), `ta` (Tamil), `te` (Telugu), `mr` (Marathi), `gu` (Gujarati), `pa` (Punjabi), `fil` (Filipino), `ms` (Malay), `ro` (Romanian), `uk` (Ukrainian), `el` (Greek)

> **Hindi & Urdu** translations output in **Roman script** (Hinglish/Roman Urdu) for natural conversational style — e.g., "Hey guys, kya chal raha hai?" instead of formal Devanagari. edge-tts pronounces Roman script identically to native script.

## 🏗️ Architecture

### Pipeline Stages

```
Video Input → Extract Audio → [Vocal Isolation (Demucs)] → Transcribe (Whisper)
                                                                    ↓
Output Video ← Mux (ffmpeg) ← Mix Audio ← TTS / Voice Cloning ← Translate (LLM)
```

1. **Extract Audio** — ffmpeg extracts WAV (16kHz mono) from video
2. **Vocal Isolation** (optional) — Demucs htdemucs separates vocals from background music/SFX
3. **Transcribe** — faster-whisper with VAD filter (skips silence for speed)
   - Can offload to a remote VM worker for faster processing
4. **Translate** — LLM-based context-aware translation (Pollinations AI, free, no API key)
   - Falls back to Google Translate if LLM is unavailable
   - Batches segments with surrounding context for natural, idiomatic translations
5. **TTS** — One of:
   - **edge-tts** (default): 8 concurrent clips, synthetic voice, duration-controlled rate
   - **Voice cloning** (with `--voice-clone`): 6-tier fallback chain (see below)
6. **Build Audio** — Professional audio mixing with ffmpeg:
   - Clips placed at correct timestamps with crossfades (30ms in / 20ms out)
   - Voice processing chain: high-pass + low-pass + compression + EQ + de-ess + limiter
   - Sidechain ducking: background music auto-ducks when voice is present
7. **Mux** — ffmpeg combines dubbed audio with video (extends video with freeze-frames if needed)

### Voice Cloning Pipeline (6-Tier Fallback)

When `--voice-clone` is enabled, each clip tries these backends in order:

| Tier | Backend | Quality | GPU? | Notes |
|------|---------|---------|------|-------|
| 1 | **Chatterbox Multilingual V3** | ⭐⭐⭐⭐⭐ | Free HF ZeroGPU | 23 languages, emotion control via exaggeration |
| 2 | **IndexTTS-2** | ⭐⭐⭐⭐⭐ | Free HF ZeroGPU | 8 emotion vectors, inherits emotion from ref audio |
| 3 | **XTTS-v2 (HF Space)** | ⭐⭐⭐⭐ | Free HF ZeroGPU | Legacy, limited language support |
| 4 | **OpenVoice V2** | ⭐⭐⭐ | CPU | Fast tone color conversion, ALL languages |
| 5 | **Local Coqui XTTS-v2** | ⭐⭐⭐⭐ | CPU | Slow (~30x RTF) but high quality |
| 6 | **edge-tts** | ⭐⭐ | None | Generic synthetic voice (no cloning) |

- HuggingFace ZeroGPU spaces provide **free A10G GPU** access (quota resets daily)
- Set `HF_TOKEN` env var for increased ZeroGPU quota
- If all cloning tiers fail for a clip, it gracefully falls back to edge-tts (no crash)
- Multi-speaker: each speaker gets their own reference audio extracted from the original video

### Background Music Preservation

- **Demucs htdemucs** model separates audio into vocals (speech) and no_vocals (music + SFX)
- Transcription runs on isolated vocals for **better accuracy**
- Dubbed TTS is mixed with background music using **sidechain compression**:
  - Music automatically **ducks** (lowers volume) when voice is present
  - Music returns to full volume during speech pauses
  - Threshold: -20dB, Ratio: 6:1, Attack: 20ms, Release: 400ms
  - This is exactly how professional radio/TV mixes work
- Background music also gets an EQ scoop at 2kHz to "make room" for voice clarity

### Duration-Controlled TTS

- TTS generates at the correct speed **natively** using edge-tts rate parameter
- Avoids `atempo` audio distortion artifacts
- For each clip: generate → measure duration → calculate rate → regenerate at correct rate
- Rate clamped to 0.7x–1.5x to avoid unnatural speech

### Video Extension

- When dubbed audio is longer than original video (common when translating from fast-speaking languages)
- Video is extended using ffmpeg `tpad` filter (freeze-frames the last frame)
- No audio is cut or speed-adjusted — full translated speech preserved
- Can be disabled with `--no-extend-video` flag

### Long Video Optimizations

- VAD filter skips silence during transcription (faster, less memory)
- Whisper model freed from RAM after transcription
- Translation rate-limit protection (pause every 50 segments)
- TTS concurrency at 8 with retry/backoff
- Memory-efficient audio mixing (concat approach for 200+ clips)
- Temp file cleanup between stages

### Resume/Checkpoint System

- Each stage saves `checkpoint.json` to the job directory
- Translation saves per-segment progress (every 5 segments)
- TTS clips checked for existence on resume (skip already generated)
- If the service crashes, jobs can be resumed from last checkpoint
- Web UI shows "Resume" button for interrupted jobs

## 📁 Project Structure

```
Free-Video-Dubbing-tool/
├── dubber.py           # Main dubbing engine (CLI + library)
├── web_ui.py           # Flask web UI server
├── cleanup_jobs.py     # Background job cleanup script
├── setup.sh            # One-command setup script
├── run.sh              # Start web UI script
├── requirements.txt    # Python dependencies
├── Dockerfile          # Docker image definition
├── docker-compose.yml  # Docker Compose config
├── .gitignore
├── LICENSE
└── README.md
```

## ⚙️ System Requirements

- **Python** 3.10+
- **ffmpeg** (with libmp3lame, aac, h264 support)
- **RAM**:
  - `tiny`/`base` model: ~2GB
  - `small` model: ~3GB
  - `medium` model: ~5GB
  - `large` model: ~10GB
  - Voice cloning (XTTS-v2) needs ~1.8GB extra; `setup.sh` auto-creates 4GB swap on machines with <4GB RAM
  - Vocal isolation (Demucs) needs ~80MB model, runs on CPU
- **Disk**: ~500MB for dependencies + ~1.8GB for XTTS model + ~200MB for OpenVoice + space for video files
- **CPU**: Works on CPU (GPU not required, but speeds up Whisper transcription and voice cloning)

## 🐛 Troubleshooting

| Problem | Solution |
|---------|----------|
| `ffmpeg not found` | `sudo apt-get install ffmpeg` |
| `No module named 'faster_whisper'` | `source venv/bin/activate` then `pip install -r requirements.txt` |
| TTS sounds robotic | Try `--model small` for better transcription |
| Audio out of sync | Enable `--extend-video` (default on) or try `--no-background` |
| Voice cloning fails | Tool auto-falls back through 6 tiers to edge-tts; check HF Token for more GPU quota |
| Voice cloning slow | OpenVoice V2 on CPU has RTF ~2.2; local XTTS on CPU is ~30x slower. This is normal. |
| ZeroGPU quota exhausted | Free daily quota; wait for reset or set `HF_TOKEN` env var for increased quota |
| Out of memory | Use smaller model: `--model tiny` or `--model base`; ensure swap is enabled (`swapon --show`) |
| Slow transcription | Use `--model tiny` or `--model base` (default); or set up remote VM worker |

## 📜 License

MIT — Free to use, modify, and distribute.

## 🤝 Contributing

Contributions welcome! Feel free to open issues or submit pull requests.
