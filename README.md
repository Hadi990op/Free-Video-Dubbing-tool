# 🎬 Free Video Dubbing Tool

AI-powered video dubbing — translate and dub any video into 30+ languages. **100% free, no API keys required.**

![Python](https://img.shields.io/badge/Python-3.12+-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Free](https://img.shields.io/badge/100%25-Free-success)

## ✨ Features

- 🌍 **30+ languages** — Hindi, Spanish, French, Arabic, Chinese, Japanese, and more
- 🎙️ **Multi-speaker detection** — auto-detects speakers, assigns distinct voices to each
- 🎭 **Voice cloning** — clone the original speaker's voice and speak translated text in that voice (studio-level quality)
- 🎬 **Video extension** — extends video (freeze-frame) to fit longer dubbed audio instead of cutting audio
- 📝 **Subtitles** — generates SRT files in the target language
- 🔄 **Resume** — checkpoint system saves progress; resume after crashes/restarts
- 📊 **Real-time progress** — live progress bar and stage tracking
- 🎵 **Background audio** — option to keep original background audio at low volume
- 🔥 **Burned subtitles** — option to burn translated subtitles into the video
- 🚀 **Long video support** — handles 30min+ videos with memory-efficient processing
- 📱 **Web UI** — beautiful, responsive web interface

## 🛠️ Tech Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Speech-to-Text | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | Transcription with VAD filter |
| Translation | [deep-translator](https://github.com/nidhaloff/deep-translator) | Google Translate (free, no API key) |
| Text-to-Speech | [edge-tts](https://github.com/rany2/edge-tts) | Microsoft Edge TTS (free, no API key) |
| Voice Cloning | [XTTS-v2](https://huggingface.co/coqui/XTTS-v2) | Clone original speaker's voice (free) |
| Speaker Diarization | [simple-diarizer](https://github.com/cvqlai1/simple_diarizer) | Detect number of speakers |
| Video Processing | [ffmpeg](https://ffmpeg.org/) | Audio extraction, muxing, subtitles |
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
   - **Keep background audio** — keeps original voice at low volume
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

# Clone original speaker's voice (studio-level quality)
python dubber.py input.mp4 --target-lang hi --voice-clone

# Clone voice + multi-speaker + extend video
python dubber.py input.mp4 --target-lang hi --voice-clone --multi-speaker

# Keep original background audio + burn subtitles
python dubber.py input.mp4 --target-lang es --no-background --burn-subtitles

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
| `--no-background` | Don't keep original background audio | off |
| `--burn-subtitles` | Burn translated subtitles into video | off |
| `--no-srt` | Don't generate SRT subtitle file | off (generates SRT) |
| `--multi-speaker` | Enable multi-speaker detection (diarization) | off |
| `--num-speakers` | Force number of speakers (auto-detected if not set) | auto |
| `--voice-clone` | Clone original speaker's voice (XTTS-v2) | off |
| `--no-extend-video` | Don't extend video to fit audio (truncates audio) | off (extends) |
| `--job-dir` | Job directory for checkpoints (enables resume) | none |
| `--resume` | Resume from last checkpoint in job-dir | off |
| `--list-voices [lang]` | List available TTS voices | — |
| `--list-langs` | List supported target languages | — |

## 🌍 Supported Languages

`hi` (Hindi), `es` (Spanish), `fr` (French), `de` (German), `ar` (Arabic), `zh` (Chinese), `ja` (Japanese), `ko` (Korean), `ru` (Russian), `pt` (Portuguese), `it` (Italian), `tr` (Turkish), `id` (Indonesian), `vi` (Vietnamese), `th` (Thai), `pl` (Polish), `nl` (Dutch), `sv` (Swedish), `bn` (Bengali), `ur` (Urdu), `ta` (Tamil), `te` (Telugu), `mr` (Marathi), `gu` (Gujarati), `pa` (Punjabi), `fil` (Filipino), `ms` (Malay), `ro` (Romanian), `uk` (Ukrainian), `el` (Greek)

## 🏗️ Architecture

### Pipeline Stages

```
Video Input → Extract Audio → Transcribe (Whisper) → Translate (Google)
                                                          ↓
Output Video ← Mux (ffmpeg) ← Build Audio ← TTS / Voice Cloning
```

1. **Extract Audio** — ffmpeg extracts WAV (16kHz mono) from video
2. **Transcribe** — faster-whisper with VAD filter (skips silence for speed)
3. **Translate** — Google Translate with retry + rate-limit protection
4. **TTS** — One of:
   - **edge-tts** (default): 8 concurrent clips, synthetic voice
   - **Voice cloning** (XTTS-v2): 2 concurrent clips, cloned from original speaker
5. **Build Audio** — ffmpeg mixes clips at correct timestamps
6. **Mux** — ffmpeg combines dubbed audio with video (extends video if needed)

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

### Voice Cloning (Studio-Level)
- **OpenVoice V2** (primary, MIT license, free for commercial use) — fast tone color conversion on CPU.
  - Pipeline: edge-tts generates speech in target language → OpenVoice converts voice tone to match original speaker.
  - Works for **ALL languages** (edge-tts handles the language, OpenVoice handles the voice).
  - RTF ~2.2 on CPU (2.2x slower than real-time) — much faster than XTTS-v2 (which was ~30x slower).
  - Model is lightweight (~200MB), loads in ~3 seconds.
- **Coqui XTTS-v2** (fallback) — slower but higher quality for supported languages.
  - The model (~1.8GB) is downloaded once and reused across runs (loaded as a singleton).
  - Works on low-RAM VMs: `setup.sh` automatically creates a 4GB swap file so the model doesn't get OOM-killed.
- **Fallback chain**: OpenVoice V2 → local XTTS-v2 → HuggingFace XTTS Gradio spaces → edge-tts synthetic voice. If cloning fails for a clip, it gracefully falls back instead of crashing.
- Serial concurrency during voice cloning to keep memory bounded.

### Video Extension
- When dubbed audio is longer than original video (common when translating from fast-speaking languages)
- Video is extended using ffmpeg `tpad` filter (freeze-frames the last frame)
- No audio is cut or speed-adjusted — full translated speech preserved
- Can be disabled with `--no-extend-video` flag

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
  - Voice cloning (XTTS-v2) needs ~1.8GB extra; `setup.sh` auto-creates 4GB swap on machines with <4GB RAM so it works on small VMs too.
- **Disk**: ~500MB for dependencies + ~1.8GB for the XTTS model (downloaded once) + space for video files
- **CPU**: Works on CPU (GPU not required, but speeds up Whisper transcription and voice cloning)

## 🐛 Troubleshooting

| Problem | Solution |
|---------|----------|
| `ffmpeg not found` | `sudo apt-get install ffmpeg` |
| `No module named 'faster_whisper'` | `source venv/bin/activate` then `pip install -r requirements.txt` |
| TTS sounds robotic | Try `--model small` for better transcription |
| Audio out of sync | Enable `--extend-video` (default on) or try `--no-background` |
| Voice cloning fails | Tool auto-falls back to edge-tts; ensure OpenVoice V2 is installed (run `bash setup.sh`) |
| Voice cloning slow | OpenVoice V2 on CPU has RTF ~2.2 (1 min audio → ~2 min processing). This is normal for CPU. |
| Out of memory | Use smaller model: `--model tiny` or `--model base`; ensure swap is enabled (`swapon --show`) |
| Slow transcription | Use `--model tiny` or `--model base` (default) |
| Slow voice cloning | Local XTTS on CPU is slow (esp. low-RAM VMs using swap); this is normal. First clip also loads the ~1.8GB model. |

## 📜 License

MIT — Free to use, modify, and distribute.

## 🤝 Contributing

Contributions welcome! Feel free to open issues or submit pull requests.
