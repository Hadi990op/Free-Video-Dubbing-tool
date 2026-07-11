# 🎬 Free Video Dubbing Tool PRO

AI-powered video dubbing — translate and dub any video into 30+ languages with **lip sync**, **non-speech sound preservation**, **background music preservation**, and **zero-shot voice cloning**. **100% free, no API keys required.**

![Python](https://img.shields.io/badge/Python-3.12+-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Free](https://img.shields.io/badge/100%25_Free-success)

## ✨ Features

### 🎯 Core Dubbing
- 🌍 **30+ languages** — Hindi, Spanish, French, Arabic, Chinese, Japanese, and more
- 🎙️ **Multi-speaker detection** — auto-detects speakers via diarization, assigns distinct voices to each
- 📝 **Subtitles** — generates SRT files in the target language
- 🔄 **Resume** — checkpoint system saves progress; resume after crashes/restarts
- 📊 **Real-time progress** — live progress bar and stage tracking
- 🔥 **Burned subtitles** — option to burn translated subtitles into the video
- 🚀 **Long video support** — handles 30min+ videos with memory-efficient processing
- 🌐 **Remote transcription** — offload Whisper to a second VM for faster processing
- 📱 **Web UI** — beautiful, responsive web interface

### 🎬 Lip Sync
- Each dubbed audio clip is **speed-adjusted to fit the original speaker's time slot exactly**
- Dubbed voice starts and ends at the **same moment** as the original speaker's lips
- Speed range 0.7x–1.5x (no distortion), trim if still too long, silence padding if too short
- **No video extension needed** — audio and video stay the same duration

### 😂 Non-Speech Sound Preservation
- Automatically **detects and preserves laughs, sighs, gasps, and reactions** from the original audio
- Finds gaps between speech segments, measures energy (RMS), extracts non-speech sounds
- Mixes them back into the dubbed track at **original timestamps**
- Makes the dub feel natural — when the speaker laughs, you hear the laugh

### 🎵 Background Music Preservation
- **Demucs htdemucs** AI model separates speech from background music/SFX
- Original background music is kept and automatically **ducks** (sidechain compression) when voice is present
- Music returns to full volume during speech pauses — exactly like professional radio/TV mixes
- Background music gets an EQ scoop at 2kHz to "make room" for voice clarity
- **ON by default** — no flag needed

### 🎙️ Voice Cloning (6-Tier Fallback)
- Clone the original speaker's voice and speak translated text in that voice
- **6 fallback tiers**: Chatterbox V3 → IndexTTS-2 → XTTS-v2 (HF Space) → OpenVoice V2 → local XTTS-v2 → Kokoro TTS → edge-tts
- **Emotion control**: IndexTTS-2 inherits emotion from reference audio; Chatterbox V3 supports exaggeration parameter
- Each speaker's reference audio extracted from original video for authentic cloning
- If all cloning tiers fail, gracefully falls back to synthetic voice (no crash)

### 🗣️ Intelligent Voice Detection
- AI detects each speaker's **gender (male/female/child)** from voice pitch (F0 analysis via librosa/pyin)
- Assigns the best matching TTS voice automatically
- No manual voice selection needed — works out of the box

### 🌐 Hinglish / Roman Urdu Translation
- Hindi & Urdu translations output in **natural Hinglish/Roman Urdu** — not formal shuddh Hindi
- Natural code-switching: Hindi sentence structure + English words mixed in
- Common English words (hello, welcome, amazing) kept as-is
- Technical terms (AI, neural, video) kept in English
- **Roman script only** — readable by everyone, no Devanagari/Urdu script
- Example: "Hello everyone, welcome to this video" → "Hello sabko, is video mein welcome hai"

### 🧠 SOTA TTS Engine (Kokoro-82M)
- **Kokoro-82M** as primary TTS engine — SOTA quality, 82M parameters, comparable to ElevenLabs
- Supports 10+ languages natively: English, Hindi, Spanish, French, Italian, Portuguese, Chinese, Japanese, and more
- Natural, expressive speech with proper intonation
- **ModelManager** swaps models in/out of RAM on demand — critical for low-RAM machines
- Edge-TTS remains fallback for 23+ unsupported languages

### ⚡ WhisperX Word-Level Alignment
- **WhisperX** for transcription with **word-level alignment** — precise timing for each word
- Better timing accuracy than faster-whisper alone
- VAD filter skips silence for faster transcription
- Can offload to a remote VM worker for parallel processing

## 🛠️ Tech Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Speech-to-Text | [WhisperX](https://github.com/m-bain/whisperX) | Transcription with word-level alignment + VAD |
| Translation | [Pollinations AI](https://pollinations.ai/) (GPT-OSS-20B) + Google Translate (fallback) | Context-aware, natural Hinglish translation |
| Text-to-Speech | [Kokoro-82M](https://github.com/hexgrad/Kokoro-82M) | SOTA TTS, 82M params, 10+ languages |
| TTS Fallback | [edge-tts](https://github.com/rany2/edge-tts) | Microsoft Edge TTS (free, 23+ languages) |
| Voice Cloning | Chatterbox V3 / IndexTTS-2 / XTTS-v2 / OpenVoice V2 | Multi-tier voice cloning with emotion |
| Vocal Isolation | [Demucs](https://github.com/facebookresearch/demucs) (htdemucs) | Separate vocals from background music |
| Speaker Diarization | [simple-diarizer](https://github.com/cvqlai1/simple_diarizer) | Detect number of speakers |
| Voice Analysis | [librosa](https://librosa.org/) (pyin F0) | Gender/age detection from voice pitch |
| Non-Speech Detection | Custom energy-based analysis | Detect laughs, sighs, reactions in speech gaps |
| Audio Mixing | [ffmpeg](https://ffmpeg.org/) filters | Sidechain ducking, EQ, compression, crossfades |
| Video Processing | [ffmpeg](https://ffmpeg.org/) | Audio extraction, muxing, subtitles |
| Web Framework | [Flask](https://flask.palletsprojects.com/) + Gunicorn | Web UI server |
| Memory Management | ModelManager singleton | Swaps AI models in/out of RAM on demand |

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
   - **Keep background music** — AI separates vocals from music/SFX, keeps music and auto-ducks it under speech (ON by default)
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

# Basic dubbing (English → Hindi with Hinglish translation)
python dubber.py input.mp4 --target-lang hi

# With multi-speaker detection
python dubber.py input.mp4 --target-lang hi --multi-speaker

# Clone original speaker's voice
python dubber.py input.mp4 --target-lang hi --voice-clone

# Clone voice + multi-speaker + burn subtitles
python dubber.py input.mp4 --target-lang hi --voice-clone --multi-speaker --burn-subtitles

# Keep original background music (ON by default, use --no-background to disable)
python dubber.py input.mp4 --target-lang es --no-background

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

> **Hindi & Urdu** translations output in **Hinglish/Roman Urdu** — natural conversational style with Hindi+English+Urdu code-switching in Latin script. Example: "Hello sabko, is video mein welcome hai" instead of formal "नमस्ते सभी को, इस वीडियो में स्वागत है". This is how South Asian people actually talk in daily life.

## 🏗️ Architecture

### Pipeline Stages

```
Video Input → Extract Audio → [Vocal Isolation (Demucs)] → Transcribe (WhisperX)
                                                                    ↓
Output Video ← Mux (ffmpeg) ← Mix Audio ← [Lip Sync] ← TTS / Voice Clone ← Translate (LLM)
                                          ↑                          ↑
                                  [Non-Speech Sounds]         [Hinglish Translation]
                                  (laughs, sighs)             (Hindi+English mix)
```

1. **Extract Audio** — ffmpeg extracts WAV (16kHz mono) from video
2. **Vocal Isolation** (optional) — Demucs htdemucs separates vocals from background music/SFX
3. **Transcribe** — WhisperX with word-level alignment + VAD filter (skips silence for speed)
   - Can offload to a remote VM worker for faster processing
4. **Non-Speech Extraction** — Finds gaps between speech segments, extracts laughs/sighs/reactions
5. **Translate** — LLM-based context-aware translation (GPT-OSS-20B via Pollinations, free)
   - Hinglish for Hindi/Urdu: natural code-switching (Hindi+English+Urdu mix in Roman script)
   - Falls back to Google Translate romanized API if LLM is unavailable
   - Batches segments with surrounding context for natural, idiomatic translations
6. **TTS / Voice Cloning** — One of:
   - **Kokoro-82M** (primary): SOTA quality, 82M params, 10+ languages
   - **Voice cloning** (with `--voice-clone`): 6-tier fallback chain (see below)
   - **Edge-TTS** (fallback): 23+ languages, synthetic voice
7. **Lip Sync** — Each TTS clip speed-adjusted (0.7x–1.5x atempo) to fit original speech time slot
   - Dubbed voice starts/ends at same moment as original speaker's lips
   - Trim if too long, pad with silence if too short
8. **Build Audio** — Professional audio mixing with ffmpeg:
   - Non-speech sounds (laughs, sighs) mixed back at original timestamps
   - Clips placed at correct timestamps with crossfades (30ms in / 20ms out)
   - Voice processing chain: high-pass + low-pass + compression + EQ + de-ess + limiter
   - Sidechain ducking: background music auto-ducks when voice is present
9. **Mux** — ffmpeg combines dubbed audio with video

### Voice Cloning Pipeline (6-Tier Fallback)

When `--voice-clone` is enabled, each clip tries these backends in order:

| Tier | Backend | Quality | GPU? | Notes |
|------|---------|---------|------|-------|
| 1 | **Chatterbox Multilingual V3** | ⭐⭐⭐⭐⭐ | Free HF ZeroGPU | 23 languages, emotion control via exaggeration, language_id parameter |
| 2 | **IndexTTS-2** | ⭐⭐⭐⭐⭐ | Free HF ZeroGPU | 8 emotion vectors, inherits emotion from ref audio |
| 3 | **XTTS-v2 (HF Space)** | ⭐⭐⭐⭐ | Free HF ZeroGPU | Legacy, limited language support |
| 4 | **OpenVoice V2** | ⭐⭐⭐ | CPU | Fast tone color conversion, ALL languages |
| 5 | **Local Coqui XTTS-v2** | ⭐⭐⭐⭐ | CPU | Slow (~30x RTF) but high quality |
| 6 | **Kokoro-82M / edge-tts** | ⭐⭐⭐ | CPU | SOTA synthetic voice (Kokoro) or generic (edge-tts) |

- HuggingFace ZeroGPU spaces provide **free A10G GPU** access (quota resets daily)
- Set `HF_TOKEN` env var for increased ZeroGPU quota (add token to `.hf_token` file)
- If all cloning tiers fail for a clip, it gracefully falls back to Kokoro/edge-tts (no crash)
- Multi-speaker: each speaker gets their own reference audio extracted from the original video

### Lip Sync

- Each dubbed audio clip is **speed-adjusted** to fit the original speaker's time slot exactly
- Uses ffmpeg `atempo` filter: speed range 0.7x–1.5x (no distortion)
- If clip is still too long after max speed-up: trims to fit slot
- If clip is shorter than slot: pads with silence at the end
- Result: dubbed voice starts and ends at the **same moment** as the original speaker's lips
- **No video extension needed** — audio matches video duration naturally

### Non-Speech Sound Preservation

- After transcription, finds **gaps between speech segments** in the original audio
- Measures energy (RMS) of each gap — filters out silence, keeps actual sounds
- Extracts clips above energy threshold (laughs, sighs, gasps, reactions)
- Mixes these clips back into the dubbed track at their **original timestamps**
- Makes the dub feel natural — when the speaker laughs, the laugh is preserved
- Uses original full audio (not Demucs output) for full sound energy

### Background Music Preservation

- **Demucs htdemucs** model separates audio into vocals (speech) and no_vocals (music + SFX)
- Transcription runs on isolated vocals for **better accuracy**
- Dubbed TTS is mixed with background music using **sidechain compression**:
  - Music automatically **ducks** (lowers volume) when voice is present
  - Music returns to full volume during speech pauses
  - Threshold: -20dB, Ratio: 6:1, Attack: 20ms, Release: 400ms
  - This is exactly how professional radio/TV mixes work
- Background music also gets an EQ scoop at 2kHz to "make room" for voice clarity
- **ON by default** — use `--no-background` to disable

### Hinglish Translation

- Hindi & Urdu use **Hinglish/Roman Urdu** — natural code-switching in Latin script
- LLM (GPT-OSS-20B) translates with 11 specific rules:
  - Hindi/Urdu sentence structure with English words mixed in
  - Common English words kept as-is (hello, welcome, amazing, etc.)
  - Technical terms kept in English (AI, neural, video, etc.)
  - Roman script only — no Devanagari, no Urdu script
  - 7 concrete translation examples for consistency
- Example: "Hello everyone, welcome to this amazing video about AI"
  → "Hello sabko, welcome hai is amazing video mein AI ke baare mein"
- Google Translate romanized API as fallback (outputs both native + roman, prefers roman)
- Kokoro TTS handles Hinglish well (tested and confirmed)

### ModelManager (Memory-Efficient)

- **Singleton** that manages all AI models in RAM
- Swaps models in/out of RAM on demand — critical for low-RAM machines (1.8GB)
- Models: WhisperX, Kokoro TTS, Demucs, simple-diarizer
- Only one model loaded at a time — frees RAM between stages
- 4GB swap file auto-created by `setup.sh` for model weight overflow

### Long Video Optimizations

- VAD filter skips silence during transcription (faster, less memory)
- Whisper model freed from RAM after transcription (ModelManager swap)
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
├── kokoro_tts.py       # Kokoro-82M TTS module (SOTA quality)
├── model_manager.py    # ModelManager singleton (RAM-efficient model swapping)
├── voice_manager.py    # Voice detection (F0 pitch analysis, gender/age)
├── non_speech.py       # Non-speech sound extraction (laughs, sighs, reactions)
├── chatterbox_tts.py   # Chatterbox V3 voice cloning module
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
  - Kokoro TTS needs ~970MB RAM (managed by ModelManager — swapped out when not in use)
- **Disk**: ~500MB for dependencies + ~1.8GB for XTTS model + ~200MB for OpenVoice + ~330MB for Kokoro + space for video files
- **CPU**: Works on CPU (GPU not required, but speeds up Whisper transcription and voice cloning)

## 🐛 Troubleshooting

| Problem | Solution |
|---------|----------|
| `ffmpeg not found` | `sudo apt-get install ffmpeg` |
| `No module named 'whisperx'` | `source venv/bin/activate` then `pip install -r requirements.txt` |
| TTS sounds robotic | Try `--model small` for better transcription; Kokoro TTS gives best quality |
| Audio out of sync | Lip sync is automatic; try `--no-background` if issues persist |
| Voice cloning fails | Tool auto-falls back through 6 tiers to Kokoro/edge-tts; check HF Token for more GPU quota |
| Voice cloning slow | OpenVoice V2 on CPU has RTF ~2.2; local XTTS on CPU is ~30x slower. This is normal. |
| ZeroGPU quota exhausted | Free daily quota; wait for reset or set `HF_TOKEN` env var for increased quota |
| Out of memory | Use smaller model: `--model tiny` or `--model base`; ensure swap is enabled (`swapon --show`) |
| Slow transcription | Use `--model tiny` or `--model base` (default); or set up remote VM worker |
| Hindi translation too formal | Hinglish mode is automatic for `hi` — outputs natural "Hello sabko" style |

## 📜 License

MIT — Free to use, modify, and distribute.

## 🤝 Contributing

Contributions welcome! Feel free to open issues or submit pull requests.
