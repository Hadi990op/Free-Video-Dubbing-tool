#!/usr/bin/env python3
"""
Free Video Dubber - Web UI
A beautiful web interface for the dubbing tool.
"""

import os
import sys
import json
import uuid
import threading
import time
from pathlib import Path

# Ensure dubber.py is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, request, jsonify, send_file, render_template_string

app = Flask(__name__)

# Allow large video uploads up to 500MB
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

WORK_DIR = Path(__file__).parent
UPLOAD_DIR = WORK_DIR / "uploads"
OUTPUT_DIR = WORK_DIR / "outputs"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# Track job status
jobs = {}
# Cancel flags — set to True to stop a running job
cancel_flags = {}

# Maximum number of old jobs to keep (older ones auto-deleted on startup)
MAX_OLD_JOBS = 5


def cleanup_old_jobs():
    """Delete old job directories to free disk space.
    Keeps MAX_OLD_JOBS most recent. Called on startup."""
    import time as _time
    import shutil as _shutil
    now = _time.time()

    for base_dir in [UPLOAD_DIR, OUTPUT_DIR]:
        try:
            dirs = []
            for d in base_dir.iterdir():
                if d.is_dir():
                    try:
                        mtime = d.stat().st_mtime
                    except OSError:
                        continue
                    dirs.append((d, mtime))
            dirs.sort(key=lambda x: x[1], reverse=True)
            for d, mtime in dirs[MAX_OLD_JOBS:]:
                if now - mtime > 3600:  # Only delete if older than 1 hour
                    try:
                        _shutil.rmtree(d, ignore_errors=True)
                        print(f"[cleanup] Removed old dir: {d.name}")
                    except Exception:
                        pass
        except Exception:
            pass

    # Clean leftover temp files in /tmp
    try:
        for pattern in ["dubber_*", "video_extend_*"]:
            for f in Path("/tmp").glob(pattern):
                if now - f.stat().st_mtime > 86400:
                    try:
                        if f.is_dir():
                            _shutil.rmtree(f, ignore_errors=True)
                        else:
                            f.unlink()
                    except Exception:
                        pass
    except Exception:
        pass

    # Clean orphaned checkpoint dirs (no video file = orphaned)
    try:
        for d in UPLOAD_DIR.iterdir():
            if not d.is_dir():
                continue
            has_video = any(f.suffix in ('.mp4', '.mkv', '.avi', '.mov', '.webm')
                           for f in d.iterdir() if f.is_file())
            has_output = (OUTPUT_DIR / d.name).exists()
            if not has_video and not has_output and now - d.stat().st_mtime > 3600:
                _shutil.rmtree(d, ignore_errors=True)
                print(f"[cleanup] Removed orphaned: {d.name}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Job status persistence — survives server restart / OOM kill
# ---------------------------------------------------------------------------

def save_job_status(job_id, status_data):
    """Save job status to disk so it survives server restart."""
    try:
        status_path = UPLOAD_DIR / job_id / "job_status.json"
        # Don't save logs (too large) — they're reconstructed from checkpoint
        slim = {k: v for k, v in status_data.items() if k != "logs"}
        slim["saved_at"] = time.time()
        with open(status_path, "w") as f:
            json.dump(slim, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def load_job_status(job_id):
    """Load job status from disk. Returns None if not found."""
    try:
        status_path = UPLOAD_DIR / job_id / "job_status.json"
        if status_path.exists():
            with open(status_path) as f:
                return json.load(f)
    except Exception:
        pass
    return None

def auto_resume_jobs():
    """On startup: find jobs with checkpoints but no completed output,
    and mark them as paused so the user can resume."""
    import dubber
    for d in UPLOAD_DIR.iterdir():
        if not d.is_dir():
            continue
        job_id = d.name
        # Skip if output already exists (job completed)
        if (OUTPUT_DIR / job_id / "dubbed.mp4").exists():
            continue
        # Check for checkpoint
        ckpt = dubber.load_checkpoint(str(d))
        if not ckpt:
            continue
        # Check for video file
        has_video = any(f.suffix in ('.mp4', '.mkv', '.avi', '.mov', '.webm')
                       for f in d.iterdir() if f.is_file())
        if not has_video:
            continue
        # Try to load saved status
        saved = load_job_status(job_id)
        stage = ckpt.get("stage", 0)
        jobs[job_id] = {
            "status": "paused",
            "stage": stage,
            "progress": saved.get("progress", {1: 5, 2: 30, 3: 55, 4: 75, 5: 90}.get(stage, 0)) if saved else {1: 5, 2: 30, 3: 55, 4: 75, 5: 90}.get(stage, 0),
            "message": f"Server restarted. Interrupted at stage {stage}. Click Resume to continue.",
            "can_resume": True,
            "target_lang": ckpt.get("target_lang", saved.get("target_lang", "hi") if saved else "hi"),
            "voice": ckpt.get("voice") or (saved.get("voice") if saved else None),
            "model_size": ckpt.get("model_size", saved.get("model_size", "base") if saved else "base"),
            "keep_bg": saved.get("keep_bg", False) if saved else False,
            "burn_subtitles": saved.get("burn_subtitles", False) if saved else False,
            "gen_srt": saved.get("gen_srt", True) if saved else True,
            "multi_speaker": ckpt.get("multi_speaker", False),
            "num_speakers": ckpt.get("num_speakers"),
            "voice_clone": saved.get("voice_clone", False) if saved else False,
            "extend_video": saved.get("extend_video", True) if saved else True,
            "emotion_transfer": saved.get("emotion_transfer", True) if saved else True,
            "prosody_strength": saved.get("prosody_strength", 1.0) if saved else 1.0,
            "anti_copyright": saved.get("anti_copyright", False) if saved else False,
            "blur_original_subtitles": saved.get("blur_original_subtitles", False) if saved else False,
            "subtitle_lang": saved.get("subtitle_lang", None) if saved else None,
            "funny_mode": saved.get("funny_mode", False) if saved else False,
            "output_video": None,
            "srt_file": None,
            "subtitle_srt_file": None,
            "segments_count": 0,
            "elapsed_seconds": 0,
            "logs": [],
        }
        print(f"[startup] Found interrupted job {job_id} at stage {stage} — marked as paused (resumable)")


# Run cleanup on import (service startup)
cleanup_old_jobs()
auto_resume_jobs()


# ---------------------------------------------------------------------------
# HTML Template
# ---------------------------------------------------------------------------

HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
    <meta http-equiv="Pragma" content="no-cache">
    <title>🎬 Free Video Dubber — AI-Powered</title>
    <style>
        :root {
            --bg: #0a0a0f;
            --card: #14141f;
            --border: #2a2a3e;
            --text: #e4e4f0;
            --muted: #8888a0;
            --accent: #6c5ce7;
            --accent2: #a29bfe;
            --success: #00b894;
            --error: #e17055;
            --warn: #fdcb6e;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 900px; margin: 0 auto; }

        /* Header */
        header {
            text-align: center;
            padding: 40px 20px 30px;
        }
        header h1 {
            font-size: 2.5em;
            font-weight: 800;
            background: linear-gradient(135deg, var(--accent), var(--accent2));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin-bottom: 8px;
        }
        header p {
            color: var(--muted);
            font-size: 1.1em;
        }
        .badges {
            display: flex;
            gap: 10px;
            justify-content: center;
            margin-top: 15px;
            flex-wrap: wrap;
        }
        .badge {
            background: var(--card);
            border: 1px solid var(--border);
            padding: 6px 14px;
            border-radius: 20px;
            font-size: 0.85em;
            color: var(--muted);
        }
        .badge.green { color: var(--success); border-color: var(--success); }

        /* Card */
        .card {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 30px;
            margin-bottom: 20px;
        }

        /* Form */
        .form-group { margin-bottom: 22px; }
        label {
            display: block;
            margin-bottom: 8px;
            font-weight: 600;
            font-size: 0.95em;
        }
        label .hint { font-weight: 400; color: var(--muted); font-size: 0.85em; }

        select, input[type="text"], input[type="url"] {
            width: 100%;
            padding: 12px 16px;
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 10px;
            color: var(--text);
            font-size: 1em;
            transition: border-color 0.2s;
        }
        select:focus, input:focus {
            outline: none;
            border-color: var(--accent);
        }

        /* Upload zone */
        .upload-zone {
            border: 2px dashed var(--border);
            border-radius: 12px;
            padding: 40px;
            text-align: center;
            cursor: pointer;
            transition: all 0.3s;
            position: relative;
        }
        .upload-zone:hover, .upload-zone.dragover {
            border-color: var(--accent);
            background: rgba(108, 92, 231, 0.05);
        }
        .upload-zone .icon { font-size: 2.5em; margin-bottom: 10px; }
        .upload-zone p { color: var(--muted); }
        .upload-zone .filename {
            color: var(--success);
            font-weight: 600;
            margin-top: 8px;
        }
        #fileInput { display: none; }

        /* Toggles */
        .toggle-row {
            display: flex;
            gap: 20px;
            flex-wrap: wrap;
            margin-top: 5px;
        }
        .toggle {
            display: flex;
            align-items: center;
            gap: 8px;
            cursor: pointer;
            padding: 8px 14px;
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 10px;
            font-size: 0.9em;
            transition: all 0.2s;
        }
        .toggle:hover { border-color: var(--accent); }
        .toggle input { width: 18px; height: 18px; accent-color: var(--accent); }

        /* Submit */
        .btn {
            width: 100%;
            padding: 16px;
            border: none;
            border-radius: 12px;
            font-size: 1.1em;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.2s;
        }
        .btn-primary {
            background: linear-gradient(135deg, var(--accent), var(--accent2));
            color: white;
        }
        .btn-primary:hover { transform: translateY(-1px); box-shadow: 0 8px 30px rgba(108, 92, 231, 0.3); }
        .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
        .btn-download {
            background: var(--success);
            color: white;
            margin-top: 10px;
            text-decoration: none;
            display: block;
            text-align: center;
        }

        /* Progress */
        .progress-container { display: none; margin-top: 20px; }
        .progress-container.active { display: block; }
        .progress-bar-bg {
            width: 100%;
            height: 10px;
            background: var(--border);
            border-radius: 5px;
            overflow: hidden;
            margin-bottom: 12px;
        }
        .progress-bar-fill {
            height: 100%;
            background: linear-gradient(90deg, var(--accent), var(--accent2));
            border-radius: 5px;
            transition: width 0.5s ease;
            width: 0%;
        }
        .progress-row {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 12px;
        }
        .progress-row .progress-bar-bg {
            flex: 1;
            margin-bottom: 0;
        }
        .progress-pct {
            font-size: 1.1em;
            font-weight: 700;
            color: var(--accent);
            min-width: 48px;
            text-align: right;
            font-variant-numeric: tabular-nums;
        }
        .progress-text {
            font-size: 0.9em;
            color: var(--text);
            min-height: 1.2em;
            font-weight: 600;
        }
        .progress-subtext {
            font-size: 0.85em;
            color: var(--muted);
            margin-top: 4px;
            min-height: 1em;
        }
        .progress-steps {
            display: flex;
            gap: 8px;
            margin-top: 15px;
            flex-wrap: wrap;
        }
        .step {
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 0.8em;
            background: var(--bg);
            border: 1px solid var(--border);
            color: var(--muted);
            transition: all 0.3s;
        }
        .step.active { border-color: var(--accent); color: var(--accent2); }
        .step.done { border-color: var(--success); color: var(--success); }
        .step.error { border-color: var(--error); color: var(--error); }

        /* Console log */
        .console-log {
            margin-top: 15px;
            background: #0d0d14;
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 12px;
            max-height: 250px;
            overflow-y: auto;
            font-family: 'SF Mono', 'Monaco', 'Cascadia Code', 'Courier New', monospace;
            font-size: 0.78em;
            line-height: 1.5;
            color: #9d9db8;
        }
        .console-log .log-line { padding: 1px 0; }
        .console-log .log-line.log-err { color: var(--error); }
        .console-log .log-line.log-done { color: var(--success); }
        .console-log .log-line.log-stage { color: var(--accent2); font-weight: 600; }
        .console-log::-webkit-scrollbar { width: 6px; }
        .console-log::-webkit-scrollbar-track { background: transparent; }
        .console-log::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

        /* Spinner */
        .spinner {
            display: inline-block;
            width: 14px;
            height: 14px;
            border: 2px solid var(--border);
            border-top-color: var(--accent);
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
            margin-right: 6px;
            vertical-align: middle;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        .processing-indicator { display: none; align-items: center; color: var(--muted); font-size: 0.85em; margin-top: 8px; }
        .processing-indicator.active { display: flex; }

        /* Result */
        .result-container { display: none; margin-top: 20px; }
        .result-container.active { display: block; }
        .result-card {
            background: var(--bg);
            border: 1px solid var(--success);
            border-radius: 12px;
            padding: 20px;
        }
        .result-card h3 { color: var(--success); margin-bottom: 10px; }
        .result-info { font-size: 0.9em; color: var(--muted); margin-bottom: 15px; }
        .result-info span { color: var(--text); font-weight: 600; }

        /* Error */
        .error-container { display: none; margin-top: 20px; }
        .error-container.active { display: block; }
        .error-card {
            background: rgba(225, 112, 85, 0.1);
            border: 1px solid var(--error);
            border-radius: 12px;
            padding: 20px;
            color: var(--error);
        }

        footer {
            text-align: center;
            padding: 30px;
            color: var(--muted);
            font-size: 0.85em;
        }
        footer a { color: var(--accent2); text-decoration: none; }

        /* Responsive */
        @media (max-width: 600px) {
            header h1 { font-size: 1.8em; }
            .card { padding: 20px; }
        }
    </style>
</head>
<body>
<div class="container">
    <header>
        <h1>🎬 Pro Video Dubber</h1>
        <p>AI-Powered Video Translation & Dubbing — SOTA Quality, 100% Free</p>
        <div class="badges">
            <span class="badge">🎙️ WhisperX</span>
            <span class="badge">🧠 GPT-OSS Translation</span>
            <span class="badge">🔊 Kokoro TTS</span>
            <span class="badge">🎭 Voice Cloning</span>
            <span class="badge green">✅ $0 Cost</span>
        </div>
    </header>

    <div class="card">
        <!-- Upload -->
        <div class="form-group">
            <label>📹 Upload Video <span class="hint">(MP4, MKV, AVI, MOV, etc.)</span></label>
            <div class="upload-zone" id="uploadZone" onclick="document.getElementById('fileInput').click()">
                <div class="icon">📁</div>
                <p>Click to browse or drag & drop your video here</p>
                <div class="filename" id="filename"></div>
            </div>
            <input type="file" id="fileInput" accept="video/*" onchange="handleFileSelect(this)">
        </div>

        <!-- Target Language -->
        <div class="form-group">
            <label>🌍 Target Language <span class="hint">(dub into this language)</span></label>
            <select id="targetLang" onchange="updateVoices()">
                <option value="hi">🇮🇳 Hindi (हिन्दी)</option>
                <option value="en">🇬🇧/🇺🇸 English</option>
                <option value="es">🇪🇸 Spanish (Español)</option>
                <option value="fr">🇫🇷 French (Français)</option>
                <option value="de">🇩🇪 German (Deutsch)</option>
                <option value="it">🇮🇹 Italian (Italiano)</option>
                <option value="pt">🇧🇷 Portuguese (Português)</option>
                <option value="ru">🇷🇺 Russian (Русский)</option>
                <option value="ja">🇯🇵 Japanese (日本語)</option>
                <option value="ko">🇰🇷 Korean (한국어)</option>
                <option value="zh">🇨🇳 Chinese (中文)</option>
                <option value="ar">🇸🇦 Arabic (العربية)</option>
                <option value="tr">🇹🇷 Turkish (Türkçe)</option>
                <option value="id">🇮🇩 Indonesian (Bahasa Indonesia)</option>
                <option value="bn">🇧🇩 Bengali (বাংলা)</option>
                <option value="ta">🇮🇳 Tamil (தமிழ்)</option>
                <option value="te">🇮🇳 Telugu (తెలుగు)</option>
                <option value="ur">🇵🇰 Urdu (اردو)</option>
                <option value="mr">🇮🇳 Marathi (मराठी)</option>
                <option value="gu">🇮🇳 Gujarati (ગુજરાતી)</option>
                <option value="kn">🇮🇳 Kannada (ಕನ್ನಡ)</option>
                <option value="ml">🇮🇳 Malayalam (മലയാളം)</option>
                <option value="pa">🇮🇳 Punjabi (ਪੰਜਾਬੀ)</option>
                <option value="th">🇹🇭 Thai (ภาษาไทย)</option>
                <option value="vi">🇻🇳 Vietnamese (Tiếng Việt)</option>
                <option value="pl">🇵🇱 Polish (Polski)</option>
                <option value="nl">🇳🇱 Dutch (Nederlands)</option>
                <option value="sv">🇸🇪 Swedish (Svenska)</option>
                <option value="fa">🇮🇷 Persian (فارسی)</option>
                <option value="he">🇮🇱 Hebrew (עברית)</option>
                <option value="uk">🇺🇦 Ukrainian (Українська)</option>
                <option value="ms">🇲🇾 Malay (Bahasa Melayu)</option>
                <option value="fil">🇵🇭 Filipino (Filipino)</option>
            </select>
        </div>

        <!-- Subtitle Language (separate from dub language) -->
        <div class="form-group">
            <label>📝 Subtitle Language <span class="hint">(burn subtitles in this language; leave blank = same as dub)</span></label>
            <select id="subtitleLang">
                <option value="">— Same as dub language —</option>
                <option value="en">🇬🇧/🇺🇸 English</option>
                <option value="hi">🇮🇳 Hindi (हिन्दी)</option>
                <option value="es">🇪🇸 Spanish (Español)</option>
                <option value="fr">🇫🇷 French (Français)</option>
                <option value="de">🇩🇪 German (Deutsch)</option>
                <option value="it">🇮🇹 Italian (Italiano)</option>
                <option value="pt">🇧🇷 Portuguese (Português)</option>
                <option value="ru">🇷🇺 Russian (Русский)</option>
                <option value="ja">🇯🇵 Japanese (日本語)</option>
                <option value="ko">🇰🇷 Korean (한국어)</option>
                <option value="zh">🇨🇳 Chinese (中文)</option>
                <option value="ar">🇸🇦 Arabic (العربية)</option>
                <option value="tr">🇹🇷 Turkish (Türkçe)</option>
                <option value="id">🇮🇩 Indonesian (Bahasa Indonesia)</option>
                <option value="bn">🇧🇩 Bengali (বাংলা)</option>
                <option value="ta">🇮🇳 Tamil (தமிழ்)</option>
                <option value="te">🇮🇳 Telugu (తెలుగు)</option>
                <option value="ur">🇵🇰 Urdu (اردو)</option>
                <option value="mr">🇮🇳 Marathi (मराठी)</option>
                <option value="gu">🇮🇳 Gujarati (ગુજરાતી)</option>
                <option value="kn">🇮🇳 Kannada (ಕನ್ನಡ)</option>
                <option value="ml">🇮🇳 Malayalam (മലയാളം)</option>
                <option value="pa">🇮🇳 Punjabi (ਪੰਜਾਬੀ)</option>
                <option value="th">🇹🇭 Thai (ภาษาไทย)</option>
                <option value="vi">🇻🇳 Vietnamese (Tiếng Việt)</option>
                <option value="pl">🇵🇱 Polish (Polski)</option>
                <option value="nl">🇳🇱 Dutch (Nederlands)</option>
                <option value="sv">🇸🇪 Swedish (Svenska)</option>
                <option value="fa">🇮🇷 Persian (فارسی)</option>
                <option value="he">🇮🇱 Hebrew (עברית)</option>
                <option value="uk">🇺🇦 Ukrainian (Українська)</option>
                <option value="ms">🇲🇾 Malay (Bahasa Melayu)</option>
                <option value="fil">🇵🇭 Filipino (Filipino)</option>
            </select>
        </div>

        <!-- Voice -->
        <div class="form-group">
            <label>🔊 Voice <span class="hint">(auto-selected, or choose specific)</span></label>
            <select id="voice">
                <option value="">🔄 Auto (best default voice)</option>
            </select>
        </div>

        <!-- Whisper Model -->
        <div class="form-group">
            <label>⚙️ AI Model Quality <span class="hint">(higher = better accuracy, slower)</span></label>
            <select id="modelSize">
                <option value="tiny">⚡ Tiny (fastest, lower accuracy)</option>
                <option value="base" selected>✅ Base (balanced — recommended)</option>
                <option value="small">🎯 Small (better accuracy)</option>
                <option value="medium">🔬 Medium (high accuracy, slow)</option>
                <option value="large">🏆 Large-v3 (best accuracy, very slow)</option>
            </select>
        </div>

        <!-- Options -->
        <div class="form-group">
            <label>📋 Options</label>
            <div class="toggle-row">
                <label class="toggle">
                    <input type="checkbox" id="keepBg">
                    🎵 Keep background music (AI separates vocals from music/SFX)
                </label>
                <label class="toggle">
                    <input type="checkbox" id="burnSubtitles">
                    📝 Burn subtitles
                </label>
                <label class="toggle">
                    <input type="checkbox" id="genSrt" checked>
                    💾 Save .srt file
                </label>
            </div>
        </div>

        <div class="form-group">
            <label>🎙️ Multi-Speaker Mode (Professional Dubbing)</label>
            <div class="toggle-row">
                <label class="toggle">
                    <input type="checkbox" id="multiSpeaker">
                    🔊 Detect speakers &amp; assign unique voices to each character
                </label>
            </div>
            <div id="speakerCountGroup" style="display:none; margin-top: 10px;">
                <label style="font-size: 13px; opacity: 0.8;">
                    Number of speakers (leave empty for auto-detect):
                </label>
                <input type="number" id="numSpeakers" min="1" max="12"
                       placeholder="Auto-detect" style="width: 100%; padding: 8px 12px;
                       border: 1px solid rgba(255,255,255,0.2); border-radius: 8px;
                       background: rgba(255,255,255,0.05); color: white; font-size: 14px;
                       margin-top: 6px;">
                <div style="font-size: 12px; opacity: 0.6; margin-top: 6px;">
                    💡 Auto-detect analyzes the audio to find how many people are speaking.
                    If you know the exact number, specify it for better accuracy.
                    Each speaker gets a distinct voice (male/female variations).
                </div>
            </div>
        </div>

        <div class="form-group">
            <label>🎭 Studio-Level Options</label>
            <div class="toggle-row">
                <label class="toggle">
                    <input type="checkbox" id="voiceClone">
                    🎙️ Clone original voice (uses original speaker's voice, not synthetic TTS)
                </label>
                <label class="toggle">
                    <input type="checkbox" id="extendVideo" checked>
                    🎬 Extend video to fit audio (freeze-frame, no audio cutting)
                </label>
            </div>
            <div class="toggle-row" style="margin-top: 8px;">
                <label class="toggle">
                    <input type="checkbox" id="keepBg" checked>
                    🎵 Preserve background audio (music, sound effects, ambient sounds)
                </label>
            </div>

            <div style="margin-top: 14px; padding: 14px; background: var(--bg); border: 1px solid var(--border); border-radius: 10px;">
                <label style="display: flex; align-items: center; gap: 8px; margin-bottom: 10px;">
                    <input type="checkbox" id="emotionTransfer" checked style="width: 18px; height: 18px; accent-color: var(--accent);">
                    <span style="font-weight: 700;">🎭 Emotion & Prosody Transfer</span>
                    <span class="hint" style="margin-left: auto;">Artist-level dubbing</span>
                </label>
                <div style="font-size: 12px; opacity: 0.7; margin-bottom: 12px; line-height: 1.5;">
                    Analyzes each segment's emotion (happy, sad, angry, surprised, etc.) using AI,
                    then transfers the original speaker's pitch, energy, and speaking rate to the
                    dubbed voice — making it sound like a professional voice artist.
                    <b>Impossible to tell it was dubbed.</b>
                </div>
                <div style="display: flex; align-items: center; gap: 12px;">
                    <label style="font-size: 0.85em; white-space: nowrap; margin: 0;">Transfer strength:</label>
                    <input type="range" id="prosodyStrength" min="0" max="100" value="100"
                           style="flex: 1; accent-color: var(--accent);"
                           oninput="document.getElementById('prosodyLabel').textContent = this.value + '%'">
                    <span id="prosodyLabel" style="font-size: 0.85em; min-width: 40px; text-align: right;">100%</span>
                </div>
            </div>

            <div style="margin-top: 14px; padding: 14px; background: rgba(76, 175, 80, 0.06); border: 1px solid rgba(76, 175, 80, 0.3); border-radius: 10px;">
                <label style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
                    <input type="checkbox" id="antiCopyright" style="width: 18px; height: 18px; accent-color: #4CAF50;">
                    <span style="font-weight: 700;">🔒 Anti-Copyright Mode</span>
                    <span class="hint" style="margin-left: auto; color: #4CAF50;">YouTube-safe</span>
                </label>
                <div style="font-size: 12px; opacity: 0.8; line-height: 1.5;">
                    Applies subtle visual + audio transformations that defeat YouTube Content ID
                    fingerprinting — your dubbed video won't get copyright-striked by the original
                    rights holder. <b>Viewers won't notice any difference.</b>
                    <br><span style="opacity: 0.6; font-size: 11px; margin-top: 4px; display: block;">
                    ✓ Mirror flip &nbsp; ✓ Slight zoom &nbsp; ✓ Color grade shift &nbsp; ✓ Audio pitch nudge &nbsp; ✓ Sharpening
                    </span>
                </div>
            </div>

            <div style="margin-top: 10px; padding: 14px; background: rgba(255, 152, 0, 0.06); border: 1px solid rgba(255, 152, 0, 0.3); border-radius: 10px;">
                <label style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
                    <input type="checkbox" id="blurOriginalSubs" style="width: 18px; height: 18px; accent-color: #FF9800;">
                    <span style="font-weight: 700;">🙈 Blur Original Subtitles</span>
                    <span class="hint" style="margin-left: auto; color: #FF9800;">Auto-detect & hide</span>
                </label>
                <div style="font-size: 12px; opacity: 0.8; line-height: 1.5;">
                    Automatically detects hardcoded (burned-in) subtitles in the original video
                    using computer vision and blurs them out — so they don't show alongside
                    your new dubbed subtitles. <b>Works on any video with existing subtitles.</b>
                    <br><span style="opacity: 0.6; font-size: 11px; margin-top: 4px; display: block;">
                    ✓ AI text detection &nbsp; ✓ Bottom + top subtitle areas &nbsp; ✓ Seamless blur
                    </span>
                </div>
            </div>

            <div style="margin-top: 10px; padding: 14px; background: rgba(233, 30, 99, 0.06); border: 1px solid rgba(233, 30, 99, 0.3); border-radius: 10px;">
                <label style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
                    <input type="checkbox" id="funnyMode" style="width: 18px; height: 18px; accent-color: #E91E63;">
                    <span style="font-weight: 700;">😂 Funny/Comedy Dub Mode</span>
                    <span class="hint" style="margin-left: auto; color: #E91E63;">Sarcastic & be-adab</span>
                </label>
                <div style="font-size: 12px; opacity: 0.8; line-height: 1.5;">
                    Rewrites the translation to be <b>funny, sarcastic, and irreverent</b> instead of
                    faithful. Serious dialogue becomes comedy, formal speech becomes casual slang,
                    educational content gets roasted. Think parody/comedy roast dub — be-adab, funny,
                    thori si adult humor, but not offensive. <b>Only affects the dub audio, not subtitles.</b>
                    <br><span style="opacity: 0.6; font-size: 11px; margin-top: 4px; display: block;">
                    ✓ Sarcastic rewriting &nbsp; ✓ Slang & be-adab style &nbsp; ✓ Comedy roast tone &nbsp; ✓ Mild adult humor
                    </span>
                </div>
            </div>
                💡 <b>Preserve Background</b>: Uses AI (Demucs) to separate speech from background music/SFX. Only the speech is dubbed, background audio is preserved with professional sidechain ducking. <b>ON by default.</b><br>
                💡 <b>Non-Speech Sounds</b>: Automatically detects and preserves laughs, sighs, gasps, and reactions from the original audio. These are mixed back into the dubbed track at their original timestamps — making the dub feel natural.<br>
                💡 <b>Lip Sync</b>: Each dubbed audio clip is speed-adjusted to fit the original speaker's time slot exactly. The dubbed voice starts and ends at the same moment as the original — lips match audio.<br>
                💡 <b>Voice Cloning</b>: Uses Chatterbox Multilingual V3 (ZeroGPU) to clone each speaker's original voice and speaks the translated text in that voice — preserves speaker identity across languages.<br>
                💡 <b>Emotion Transfer</b>: Uses emotion2vec+ (AI speech emotion recognition) to detect emotions, then passes them to IndexTTS-2/Chatterbox for emotion-aware TTS. Post-processes with pitch shifting, energy matching, and dynamic range control.<br>
                💡 <b>Intelligent Voice Detection</b>: AI detects each speaker's gender (male/female/child) from voice pitch and assigns the best matching voice.
            </div>
        </div>

        <!-- Submit -->
        <button class="btn btn-primary" id="dubBtn" onclick="startDubbing()">
            🎬 Start Dubbing
        </button>

        <!-- Progress -->
        <div class="progress-container" id="progressContainer">
            <div class="progress-row">
                <div class="progress-bar-bg">
                    <div class="progress-bar-fill" id="progressBar"></div>
                </div>
                <div class="progress-pct" id="progressPct">0%</div>
            </div>
            <div class="progress-text" id="progressText">Initializing...</div>
            <div class="progress-subtext" id="progressSubtext"></div>
            <div class="processing-indicator" id="processingIndicator">
                <span class="spinner"></span>
                <span id="processingText">Processing in background — you can switch tabs, this will keep running.</span>
            </div>
            <div class="progress-steps" id="progressSteps">
                <span class="step" id="step1">1. Extract Audio</span>
                <span class="step" id="step2">2. Transcribe</span>
                <span class="step" id="step3">3. Translate</span>
                <span class="step" id="step4">4. Generate Voice</span>
                <span class="step" id="step5">5. Mux Video</span>
            </div>
            <div class="console-log" id="consoleLog"></div>
            <button class="btn" id="cancelBtn" style="display: none; margin-top: 15px; background: #e74c3c; color: white; font-size: 14px; padding: 10px 24px;">✖ Cancel Job</button>
        </div>

        <!-- Result -->
        <div class="result-container" id="resultContainer">
            <div class="result-card">
                <h3>✅ Dubbing Complete!</h3>
                <div class="result-info" id="resultInfo"></div>
                <div id="previewWrap" style="margin: 15px 0; display: none;">
                    <video id="dubbedPreview" controls preload="metadata"
                          style="width: 100%; max-height: 400px; border-radius: 10px; background: #000;"></video>
                </div>
                <a class="btn btn-download" id="downloadVideo" href="#">📥 Download Dubbed Video</a>
                <a class="btn btn-download" id="downloadSrt" href="#" style="background: var(--accent); display: none;">📝 Download Subtitles (.srt)</a>
                <a class="btn btn-download" id="downloadSubSrt" href="#" style="background: #FF9800; display: none;">📝 Download Subtitle Language .srt</a>
                <button class="btn" id="cleanupBtn" style="display: block; margin-top: 15px; background: #2c3e50; color: white; font-size: 14px; padding: 10px 24px; width: 100%;">🗑️ Clean Up &amp; Start New (Free Memory)</button>
            </div>
        </div>

        <!-- Error -->
        <div class="error-container" id="errorContainer">
            <div class="error-card" id="errorText"></div>
            <button class="btn btn-resume" id="resumeBtn" style="display: none; margin-top: 15px; background: var(--success);">🔄 Resume from Checkpoint</button>
        </div>
    </div>

    <footer>
        <p>🎬 Free Video Dubber — Powered by Whisper AI, Google Translate & Edge TTS</p>
        <p style="margin-top: 5px;">100% Free • Open Source • No API Keys • No Limits</p>
    </footer>
</div>

<script>
// State
let uploadedFile = null;
let jobId = null;
let pollInterval = null;
let maxProgress = 0;        // monotonic progress — never goes backwards
let lastLogMsg = '';       // dedupe console log
let lastLogTime = 0;

// Job persistence — survives browser/tab close
const JOB_STORAGE_KEY = 'dubber_job_id';

function saveJobId(id) {
    try { localStorage.setItem(JOB_STORAGE_KEY, id); } catch(e) {}
}
function clearJobId() {
    try { localStorage.removeItem(JOB_STORAGE_KEY); } catch(e) {}
}
function getSavedJobId() {
    try { return localStorage.getItem(JOB_STORAGE_KEY); } catch(e) { return null; }
}

// Compute base path so API calls work regardless of mount point
// e.g. if served at /dubber/, base = '/dubber/'
const BASE = window.location.pathname.replace(/\/[^/]*$/, '/');

// On page load: check if there's an active job from a previous session
(function checkSavedJob() {
    const savedId = getSavedJobId();
    if (!savedId) return;
    fetch(BASE + 'api/status/' + savedId)
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                // Job gone (cleanup ran or server restarted) — clear stale ID
                clearJobId();
                return;
            }
            if (data.status === 'done') {
                // Job finished while we were away — show result
                jobId = savedId;
                document.getElementById('progressContainer').classList.add('active');
                document.getElementById('progressBar').style.width = '100%';
                document.getElementById('progressPct').textContent = '100%';
                document.getElementById('progressText').textContent = 'Completed (reconnected)';
                showResult(data);
                clearJobId();
            } else if (data.status === 'error') {
                clearJobId();
            } else if (data.status === 'paused') {
                // Job was interrupted (server restart/OOM) — show Resume button
                jobId = savedId;
                maxProgress = data.progress || 0;
                document.getElementById('progressContainer').classList.add('active');
                document.getElementById('progressBar').style.width = maxProgress + '%';
                document.getElementById('progressPct').textContent = Math.round(maxProgress) + '%';
                document.getElementById('progressText').textContent = data.message || 'Interrupted. Click Resume to continue.';
                document.getElementById('processingIndicator').classList.remove('active');
                if (data.can_resume) document.getElementById('resumeBtn').style.display = 'block';
            } else {
                // Job still running — reconnect to it
                jobId = savedId;
                maxProgress = data.progress || 0;
                document.getElementById('progressContainer').classList.add('active');
                document.getElementById('dubBtn').disabled = true;
                document.getElementById('progressBar').style.width = maxProgress + '%';
                document.getElementById('progressPct').textContent = Math.round(maxProgress) + '%';
                document.getElementById('progressText').textContent = data.message || 'Reconnected to running job...';
                pollStatus();
            }
        })
        .catch(() => clearJobId());
})();

// Voice options per language
const VOICES = {
    hi: [["hi-IN-MadhurNeural","Madhur (Male)"],["hi-IN-SwaraNeural","Swara (Female)"],["hi-IN-AaravNeural","Aarav (Male)"],["hi-IN-NeerjaNeural","Neerja (Female)"]],
    en: [["en-US-AriaNeural","Aria (US Female)"],["en-US-GuyNeural","Guy (US Male)"],["en-GB-SoniaNeural","Sonia (UK Female)"],["en-GB-RyanNeural","Ryan (UK Male)"],["en-AU-NatashaNeural","Natasha (AU Female)"],["en-IN-NeerjaNeural","Neerja (IN Female)"]],
    es: [["es-ES-ElviraNeural","Elvira (ES Female)"],["es-ES-AlvaroNeural","Alvaro (ES Male)"],["es-MX-DaliaNeural","Dalia (MX Female)"],["es-MX-JorgeNeural","Jorge (MX Male)"]],
    fr: [["fr-FR-DeniseNeural","Denise (Female)"],["fr-FR-HenriNeural","Henri (Male)"],["fr-CA-SylvieNeural","Sylvie (CA Female)"]],
    de: [["de-DE-KatjaNeural","Katja (Female)"],["de-DE-ConradNeural","Conrad (Male)"],["de-AT-IngridNeural","Ingrid (AT Female)"]],
    it: [["it-IT-ElsaNeural","Elsa (Female)"],["it-IT-DiegoNeural","Diego (Male)"]],
    pt: [["pt-BR-FranciscaNeural","Francisca (BR Female)"],["pt-BR-AntonioNeural","Antonio (BR Male)"],["pt-PT-RaquelNeural","Raquel (PT Female)"]],
    ru: [["ru-RU-SvetlanaNeural","Svetlana (Female)"],["ru-RU-DmitryNeural","Dmitry (Male)"]],
    ja: [["ja-JP-NanamiNeural","Nanami (Female)"],["ja-JP-KeitaNeural","Keita (Male)"]],
    ko: [["ko-KR-SunHiNeural","SunHi (Female)"],["ko-KR-InJoonNeural","InJoon (Male)"]],
    zh: [["zh-CN-XiaoxiaoNeural","Xiaoxiao (Female)"],["zh-CN-YunxiNeural","Yunxi (Male)"],["zh-CN-YunjianNeural","Yunjian (Male)"]],
    ar: [["ar-SA-HamedNeural","Hamed (Male)"],["ar-SA-ZariyahNeural","Zariyah (Female)"]],
    tr: [["tr-TR-EmelNeural","Emel (Female)"],["tr-TR-AhmetNeural","Ahmet (Male)"]],
    id: [["id-ID-GadisNeural","Gadis (Female)"],["id-ID-ArdiNeural","Ardi (Male)"]],
    bn: [["bn-IN-TanishaaNeural","Tanishaa (Female)"],["bn-BD-NabanitaNeural","Nabanita (BD Female)"]],
    ta: [["ta-IN-PallaviNeural","Pallavi (Female)"],["ta-IN-ValluvarNeural","Valluvar (Male)"]],
    te: [["te-IN-ShrutiNeural","Shruti (Female)"],["te-IN-MohanNeural","Mohan (Male)"]],
    ur: [["ur-PK-UzmaNeural","Uzma (Female)"],["ur-PK-AsadNeural","Asad (Male)"]],
    mr: [["mr-IN-AarohiNeural","Aarohi (Female)"],["mr-IN-ManoharNeural","Manohar (Male)"]],
    gu: [["gu-IN-DhwaniNeural","Dhwani (Female)"],["gu-IN-NiranjanNeural","Niranjan (Male)"]],
    kn: [["kn-IN-SapnaNeural","Sapna (Female)"],["kn-IN-GaganNeural","Gagan (Male)"]],
    ml: [["ml-IN-SobhanaNeural","Sobhana (Female)"],["ml-IN-MidhunNeural","Midhun (Male)"]],
    pa: [["pa-IN-NeeruNeural","Neeru (Female)"],["pa-IN-NeerajNeural","Neeraj (Male)"]],
    th: [["th-TH-PremwadeeNeural","Premwadee (Female)"],["th-TH-NiwatNeural","Niwat (Male)"]],
    vi: [["vi-VN-HoaiMyNeural","HoaiMy (Female)"],["vi-VN-NamMinhNeural","NamMinh (Male)"]],
    pl: [["pl-PL-ZofiaNeural","Zofia (Female)"],["pl-PL-MarekNeural","Marek (Male)"]],
    nl: [["nl-NL-ColetteNeural","Colette (Female)"],["nl-NL-MaartenNeural","Maarten (Male)"]],
    sv: [["sv-SE-SofieNeural","Sofie (Female)"],["sv-SE-MattiasNeural","Mattias (Male)"]],
    fa: [["fa-IR-DilaraNeural","Dilara (Female)"],["fa-IR-FaridNeural","Farid (Male)"]],
    he: [["he-IL-HilaNeural","Hila (Female)"],["he-IL-AvriNeural","Avri (Male)"]],
    uk: [["uk-UA-PolinaNeural","Polina (Female)"],["uk-UA-OstapNeural","Ostap (Male)"]],
    ms: [["ms-MY-YasminNeural","Yasmin (Female)"],["ms-MY-OsmanNeural","Osman (Male)"]],
    fil: [["fil-PH-AngeloNeural","Angelo (Male)"],["fil-PH-BlessicaNeural","Blessica (Female)"]],
};

function updateVoices() {
    const lang = document.getElementById('targetLang').value;
    const voiceSelect = document.getElementById('voice');
    voiceSelect.innerHTML = '<option value="">🔄 Auto (best default voice)</option>';
    if (VOICES[lang]) {
        VOICES[lang].forEach(([val, label]) => {
            const opt = document.createElement('option');
            opt.value = val;
            opt.textContent = '🔊 ' + label;
            voiceSelect.appendChild(opt);
        });
    }
}
updateVoices();

// File upload
const uploadZone = document.getElementById('uploadZone');
['dragenter', 'dragover'].forEach(e => {
    uploadZone.addEventListener(e, ev => { ev.preventDefault(); uploadZone.classList.add('dragover'); });
});
['dragleave', 'drop'].forEach(e => {
    uploadZone.addEventListener(e, ev => { ev.preventDefault(); uploadZone.classList.remove('dragover'); });
});
uploadZone.addEventListener('drop', ev => {
    const files = ev.dataTransfer.files;
    if (files.length > 0) handleFile(files[0]);
});

// Multi-speaker toggle
document.getElementById('multiSpeaker').addEventListener('change', function() {
    document.getElementById('speakerCountGroup').style.display = this.checked ? 'block' : 'none';
});

function handleFileSelect(input) {
    if (input.files.length > 0) handleFile(input.files[0]);
}

function handleFile(file) {
    uploadedFile = file;
    const sizeMB = (file.size/1024/1024).toFixed(1);
    document.getElementById('filename').textContent = '✅ ' + file.name + ' (' + sizeMB + ' MB)';
    if (file.size > 500 * 1024 * 1024) {
        document.getElementById('filename').textContent = '❌ ' + file.name + ' (' + sizeMB + ' MB) - TOO LARGE! Max 500MB. Please use a shorter or lower quality video.';
        uploadedFile = null;
        document.getElementById('dubBtn').disabled = true;
        return;
    }
    document.getElementById('dubBtn').disabled = false;
}

// Start dubbing
async function startDubbing() {
    if (!uploadedFile) { alert('Please upload a video first!'); return; }

    const sizeMB = (uploadedFile.size/1024/1024).toFixed(1);
    if (uploadedFile.size > 500 * 1024 * 1024) {
        showError('Video is ' + sizeMB + 'MB. Maximum allowed size is 500MB. Please use a shorter or lower quality video.');
        return;
    }

    const formData = new FormData();
    formData.append('video', uploadedFile);
    formData.append('target_lang', document.getElementById('targetLang').value);
    formData.append('voice', document.getElementById('voice').value);
    formData.append('model_size', document.getElementById('modelSize').value);
    formData.append('keep_bg', document.getElementById('keepBg').checked);
    formData.append('burn_subtitles', document.getElementById('burnSubtitles').checked);
    formData.append('gen_srt', document.getElementById('genSrt').checked);
    formData.append('multi_speaker', document.getElementById('multiSpeaker').checked);
    formData.append('voice_clone', document.getElementById('voiceClone').checked);
    formData.append('extend_video', document.getElementById('extendVideo').checked);
    formData.append('emotion_transfer', document.getElementById('emotionTransfer').checked);
    formData.append('prosody_strength', document.getElementById('prosodyStrength').value / 100);
    formData.append('anti_copyright', document.getElementById('antiCopyright').checked);
    formData.append('funny_mode', document.getElementById('funnyMode').checked);
    formData.append('blur_original_subtitles', document.getElementById('blurOriginalSubs').checked);
    var subLangVal = document.getElementById('subtitleLang').value;
    if (subLangVal) formData.append('subtitle_lang', subLangVal);
    var numSpeakersVal = document.getElementById('numSpeakers').value;
    if (numSpeakersVal) formData.append('num_speakers', numSpeakersVal);

    document.getElementById('dubBtn').disabled = true;
    document.getElementById('progressContainer').classList.add('active');
    document.getElementById('resultContainer').classList.remove('active');
    document.getElementById('errorContainer').classList.remove('active');
    document.getElementById('progressBar').style.width = '0%';
    document.getElementById('progressPct').textContent = '0%';
    document.getElementById('progressText').textContent = 'Uploading video...';
    maxProgress = 0;

    // Clear console log
    document.getElementById('consoleLog').innerHTML = '';

    // Reset steps
    for (let i = 1; i <= 5; i++) {
        document.getElementById('step' + i).className = 'step';
    }

    try {
        // Use XMLHttpRequest for upload progress tracking and better timeout handling
        const xhr = new XMLHttpRequest();
        xhr.open('POST', BASE + 'api/dub', true);
        xhr.timeout = 600000; // 10 minutes

        xhr.upload.onprogress = function(e) {
            if (e.lengthComputable) {
                const pct = Math.round((e.loaded / e.total) * 100);
                document.getElementById('progressBar').style.width = pct + '%';
                document.getElementById('progressPct').textContent = pct + '%';
                document.getElementById('progressText').textContent = 'Uploading video... ' + pct + '%';
            }
        };

        xhr.upload.onerror = function() {
            const sizeMB = (uploadedFile.size/1024/1024).toFixed(1);
            showError('Upload failed (file: ' + sizeMB + 'MB). Possible causes:\\n' +
                '1. File exceeds 500MB limit\\n' +
                '2. Network connection interrupted\\n' +
                '3. Browser security policy blocking upload\\n' +
                'Try a smaller file or check your connection.');
            document.getElementById('dubBtn').disabled = false;
        };

        xhr.upload.ontimeout = function() {
            showError('Upload timed out. The video may be too large. Try a shorter video or lower quality (max 500MB).');
            document.getElementById('dubBtn').disabled = false;
        };

        xhr.onerror = function() {
            showError('Network error: Could not reach the server. Status: ' + xhr.status + '. Check your connection and try again.');
            document.getElementById('dubBtn').disabled = false;
        };

        xhr.onload = function() {
            if (xhr.status >= 200 && xhr.status < 300) {
                try {
                    const data = JSON.parse(xhr.responseText);
                    if (data.error) {
                        throw new Error(data.error);
                    }
                    jobId = data.job_id;
                    saveJobId(jobId);
                    document.getElementById('progressText').textContent = 'Processing...';
                    pollStatus();
                } catch (e) {
                    showError(e.message);
                    document.getElementById('dubBtn').disabled = false;
                }
            } else {
                let msg = 'Server error (' + xhr.status + ')';
                try {
                    const data = JSON.parse(xhr.responseText);
                    if (data.error) msg = data.error;
                } catch (e) {}
                showError(msg);
                document.getElementById('dubBtn').disabled = false;
            }
        };

        xhr.send(formData);
    } catch (err) {
        showError(err.message);
        document.getElementById('dubBtn').disabled = false;
    }
}

function pollStatus() {
    let errorCount = 0;
    let lastStage = -1;

    function doPoll() {
        fetch(BASE + 'api/status/' + jobId, { signal: AbortSignal.timeout(15000) })
            .then(resp => resp.json())
            .then(data => {
                errorCount = 0;

                // Monotonic progress bar — only go forward, never backward
                const pct = data.progress || 0;
                if (pct > maxProgress) maxProgress = pct;
                document.getElementById('progressBar').style.width = maxProgress + '%';
                document.getElementById('progressPct').textContent = Math.round(maxProgress) + '%';
                document.getElementById('progressText').textContent = data.message || 'Processing...';

                // Sub-progress (e.g. "45/425 segments translated")
                const subEl = document.getElementById('progressSubtext');
                if (data.sub_progress !== null && data.sub_progress !== undefined &&
                    data.sub_total !== null && data.sub_total !== undefined && data.sub_total > 0) {
                    const subPct = Math.round((data.sub_progress / data.sub_total) * 100);
                    subEl.textContent = data.sub_progress + ' / ' + data.sub_total + ' (' + subPct + '%)';
                    subEl.style.display = 'block';
                } else {
                    subEl.textContent = '';
                }

                // Show processing indicator + cancel button
                document.getElementById('processingIndicator').classList.add('active');
                document.getElementById('cancelBtn').style.display = 'block';

                // Update steps
                for (let i = 1; i <= 5; i++) {
                    const el = document.getElementById('step' + i);
                    if (data.stage > i) el.className = 'step done';
                    else if (data.stage === i) el.className = 'step active';
                    else el.className = 'step';
                }

                // Log stage transitions and messages to console
                if (data.stage !== lastStage || (data.message && data.message !== lastLogMsg && Date.now() - lastLogTime > 1500)) {
                    addConsoleLog(data.message || '', data.stage, lastStage);
                    lastLogMsg = data.message || '';
                    lastLogTime = Date.now();
                    lastStage = data.stage;
                }

                // Sync full logs from server if available (catches up after background tab)
                if (data.logs && Array.isArray(data.logs) && data.logs.length > 0) {
                    syncConsoleLogs(data.logs);
                }

                if (data.status === 'done') {
                    clearInterval(pollInterval);
                    maxProgress = 100;
                    document.getElementById('progressBar').style.width = '100%';
                    document.getElementById('progressPct').textContent = '100%';
                    addConsoleLog('✅ Dubbing complete!', 'done', -1);
                    document.getElementById('processingIndicator').classList.remove('active');
                    document.getElementById('cancelBtn').style.display = 'none';
                    clearJobId();
                    showResult(data);
                } else if (data.status === 'paused') {
                    clearInterval(pollInterval);
                    addConsoleLog('⏸ Paused: ' + data.message, 'err', -1);
                    document.getElementById('processingIndicator').classList.remove('active');
                    document.getElementById('cancelBtn').style.display = 'none';
                    showError(data.message);
                    if (data.can_resume) document.getElementById('resumeBtn').style.display = 'block';
                } else if (data.status === 'error') {
                    clearInterval(pollInterval);
                    addConsoleLog('❌ Error: ' + data.message, 'err', -1);
                    document.getElementById('processingIndicator').classList.remove('active');
                    document.getElementById('cancelBtn').style.display = 'none';
                    clearJobId();
                    showError(data.message);
                    if (data.can_resume) document.getElementById('resumeBtn').style.display = 'block';
                } else if (data.status === 'cancelled') {
                    clearInterval(pollInterval);
                    addConsoleLog('✖ Job cancelled', 'err', -1);
                    document.getElementById('processingIndicator').classList.remove('active');
                    document.getElementById('cancelBtn').style.display = 'none';
                    clearJobId();
                    showError('Job cancelled. All files cleaned up.');
                }
            })
            .catch(e => {
                errorCount++;
                if (errorCount >= 10) {
                    clearInterval(pollInterval);
                    showError('Lost connection to server after 10 retries. The job may still be processing — try refreshing the page.');
                    document.getElementById('dubBtn').disabled = false;
                }
                // Keep trying — transient errors are normal, especially on background tabs
            });
    }

    // Use setInterval (not setTimeout chain) — survives background tab throttling better
    pollInterval = setInterval(doPoll, 2000);

    // Also poll on visibility change (when tab becomes visible again)
    document.addEventListener('visibilitychange', function() {
        if (!document.hidden && jobId) doPoll();
    });
}

function addConsoleLog(msg, stage, prevStage) {
    const log = document.getElementById('consoleLog');
    if (!log || !msg) return;

    // Stage transition: add a header line
    const stageNames = {1:'Extract Audio', 2:'Transcribe', 3:'Translate', 4:'Generate Voice', 5:'Build & Mux', 6:'Mux Video', 'done':'Complete'};
    if (stage !== prevStage && stageNames[stage]) {
        const hdr = document.createElement('div');
        hdr.className = 'log-line log-stage';
        hdr.textContent = '── ' + stageNames[stage] + ' ──';
        log.appendChild(hdr);
    }

    const line = document.createElement('div');
    line.className = 'log-line' + (stage === 'err' ? ' log-err' : stage === 'done' ? ' log-done' : '');
    line.textContent = msg;
    log.appendChild(line);

    // Auto-scroll to bottom
    log.scrollTop = log.scrollHeight;

    // Keep max 200 lines
    while (log.children.length > 200) log.removeChild(log.firstChild);
}

function syncConsoleLogs(serverLogs) {
    // Rebuild console from server logs (used when reconnecting after background tab)
    const log = document.getElementById('consoleLog');
    if (!log || !serverLogs || serverLogs.length === 0) return;

    // If server has more logs than we've shown, rebuild
    if (serverLogs.length > log.children.length) {
        log.innerHTML = '';
        let lastStage = -1;
        const stageNames = {1:'Extract Audio', 2:'Transcribe', 3:'Translate', 4:'Generate Voice', 5:'Build & Mux', 6:'Mux Video', 'done':'Complete'};
        for (const entry of serverLogs) {
            const stage = entry.stage;
            const msg = entry.message || '';
            if (stage !== lastStage && stageNames[stage]) {
                const hdr = document.createElement('div');
                hdr.className = 'log-line log-stage';
                hdr.textContent = '── ' + stageNames[stage] + ' ──';
                log.appendChild(hdr);
                lastStage = stage;
            }
            if (msg) {
                const line = document.createElement('div');
                line.className = 'log-line';
                line.textContent = msg;
                log.appendChild(line);
            }
        }
        log.scrollTop = log.scrollHeight;
    }
}

function showResult(data) {
    document.getElementById('progressContainer').classList.remove('active');
    document.getElementById('resultContainer').classList.add('active');
    document.getElementById('dubBtn').disabled = false;

    var speakerInfo = '';
    if (data.multi_speaker && data.speakers) {
        speakerInfo = '<br><span style="color: #4fc3f7;">🎙️ Multi-speaker dubbing:</span><br>';
        var speakers = data.speakers;
        for (var spkId in speakers) {
            speakerInfo += '  Speaker ' + spkId + ' → <span>' + speakers[spkId] + '</span><br>';
        }
    }

    var emotionInfo = '';
    if (data.emotion_summary) {
        var es = data.emotion_summary;
        emotionInfo = '<br><span style="color: #a29bfe;">🎭 Emotions detected:</span> '
            + (es.dominant_emotion_display || es.dominant_emotion || 'neutral')
            + ' (dominant)';
        var dist = es.emotion_distribution || {};
        var parts = [];
        for (var emo in dist) {
            if (dist[emo] > 0.05) parts.push(emo + ' ' + Math.round(dist[emo]*100) + '%');
        }
        if (parts.length > 0) emotionInfo += ' — ' + parts.join(', ');
    }

    document.getElementById('resultInfo').innerHTML = `
        Source: <span>${data.source_lang}</span> → Target: <span>${data.target_lang}</span><br>
        Segments: <span>${data.segments_count}</span> | Time: <span>${data.elapsed_seconds}s</span> | Voice: <span>${data.voice}</span>
        ${speakerInfo}
        ${emotionInfo}
    `;

    document.getElementById('downloadVideo').href = BASE + 'api/download/' + jobId + '/video';
    if (data.srt_file) {
        document.getElementById('downloadSrt').style.display = 'block';
        document.getElementById('downloadSrt').href = BASE + 'api/download/' + jobId + '/srt';
    }
    if (data.subtitle_srt_file) {
        document.getElementById('downloadSubSrt').style.display = 'block';
        document.getElementById('downloadSubSrt').href = BASE + 'api/download/' + jobId + '/subsrt';
    }
    // Show video preview
    var previewWrap = document.getElementById('previewWrap');
    var previewVideo = document.getElementById('dubbedPreview');
    previewWrap.style.display = 'block';
    previewVideo.src = BASE + 'api/download/' + jobId + '/video';
    previewVideo.load();
}

function showError(msg) {
    document.getElementById('progressContainer').classList.remove('active');
    document.getElementById('errorContainer').classList.add('active');
    document.getElementById('errorText').textContent = '❌ Error: ' + msg;
    document.getElementById('dubBtn').disabled = false;
    document.getElementById('processingIndicator').classList.remove('active');
}

async function resumeJob() {
    if (!jobId) return;
    document.getElementById('resumeBtn').style.display = 'none';
    document.getElementById('errorContainer').classList.remove('active');
    document.getElementById('progressContainer').classList.add('active');
    document.getElementById('progressText').textContent = 'Resuming from checkpoint...';
    document.getElementById('dubBtn').disabled = true;
    document.getElementById('progressBar').style.width = '0%';
    document.getElementById('progressPct').textContent = '0%';
    maxProgress = 0;
    document.getElementById('consoleLog').innerHTML = '';

    try {
        const resp = await fetch(BASE + 'api/resume/' + jobId, { method: 'POST' });
        const data = await resp.json();
        if (data.error) throw new Error(data.error);
        pollStatus();
    } catch (err) {
        showError(err.message);
        document.getElementById('resumeBtn').style.display = 'block';
    }
}

// Wire up resume button
document.getElementById('resumeBtn').addEventListener('click', resumeJob);

// Cancel button — stops running job and cleans up
document.getElementById('cancelBtn').addEventListener('click', async function() {
    if (!jobId) return;
    if (!confirm('Cancel this job? All progress will be lost and files cleaned up.')) return;

    const btn = document.getElementById('cancelBtn');
    btn.textContent = '⏳ Cancelling...';
    btn.disabled = true;

    try {
        await fetch(BASE + 'api/cancel/' + jobId, { method: 'POST' });
        // Wait a moment for the server to process the cancel
        setTimeout(async () => {
            // Full cleanup
            await fetch(BASE + 'api/cleanup/' + jobId, { method: 'POST' });
            // Reset UI to fresh state
            resetUI();
        }, 2000);
    } catch (e) {
        // Even if cancel fails, do cleanup
        try { await fetch(BASE + 'api/cleanup/' + jobId, { method: 'POST' }); } catch(e2) {}
        resetUI();
    }
});

// Cleanup button — after download, purges everything and resets
document.getElementById('cleanupBtn').addEventListener('click', async function() {
    if (!jobId) {
        // No job to clean — just reset UI
        resetUI();
        return;
    }

    const btn = document.getElementById('cleanupBtn');
    btn.textContent = '⏳ Cleaning up...';
    btn.disabled = true;

    try {
        await fetch(BASE + 'api/cleanup/' + jobId, { method: 'POST' });
    } catch (e) {
        // Ignore errors — cleanup is best-effort
    }

    resetUI();
});

function resetUI() {
    // Clear job tracking
    jobId = null;
    maxProgress = 0;
    clearJobId();

    // Hide all status containers
    document.getElementById('progressContainer').classList.remove('active');
    document.getElementById('resultContainer').classList.remove('active');
    document.getElementById('errorContainer').classList.remove('active');

    // Reset progress bar
    document.getElementById('progressBar').style.width = '0%';
    document.getElementById('progressPct').textContent = '0%';
    document.getElementById('progressText').textContent = '';
    document.getElementById('progressSubtext').textContent = '';
    document.getElementById('progressSubtext').style.display = 'none';
    document.getElementById('consoleLog').innerHTML = '';

    // Reset steps
    for (let i = 1; i <= 5; i++) {
        document.getElementById('step' + i).className = 'step';
    }

    // Hide cancel button
    document.getElementById('cancelBtn').style.display = 'none';
    document.getElementById('cancelBtn').textContent = '✖ Cancel Job';
    document.getElementById('cancelBtn').disabled = false;

    // Reset cleanup button
    document.getElementById('cleanupBtn').textContent = '🗑️ Clean Up & Start New (Free Memory)';
    document.getElementById('cleanupBtn').disabled = false;

    // Hide preview
    document.getElementById('previewWrap').style.display = 'none';
    document.getElementById('dubbedPreview').src = '';

    // Hide SRT button
    document.getElementById('downloadSrt').style.display = 'none';

    // Re-enable dub button
    document.getElementById('dubBtn').disabled = false;

    // Hide resume button
    document.getElementById('resumeBtn').style.display = 'none';

    // Hide processing indicator
    document.getElementById('processingIndicator').classList.remove('active');

    // Scroll to top
    window.scrollTo({ top: 0, behavior: 'smooth' });
}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/dub", methods=["POST"])
def api_dub():
    try:
        video_file = request.files.get("video")
        if not video_file:
            return jsonify({"error": "No video file provided"}), 400

        target_lang = request.form.get("target_lang", "hi")
        voice = request.form.get("voice", "") or None
        model_size = request.form.get("model_size", "base")
        keep_bg = request.form.get("keep_bg", "false").lower() == "true"
        burn_subtitles = request.form.get("burn_subtitles", "false").lower() == "true"
        gen_srt = request.form.get("gen_srt", "true").lower() == "true"
        multi_speaker = request.form.get("multi_speaker", "false").lower() == "true"
        num_speakers_str = request.form.get("num_speakers", "")
        num_speakers = int(num_speakers_str) if num_speakers_str and num_speakers_str.isdigit() else None
        voice_clone = request.form.get("voice_clone", "false").lower() == "true"
        extend_video = request.form.get("extend_video", "true").lower() == "true"
        emotion_transfer = request.form.get("emotion_transfer", "true").lower() == "true"
        anti_copyright = request.form.get("anti_copyright", "false").lower() == "true"
        blur_original_subtitles = request.form.get("blur_original_subtitles", "false").lower() == "true"
        subtitle_lang = request.form.get("subtitle_lang", "").strip() or None
        funny_mode = request.form.get("funny_mode", "false").lower() == "true"
        prosody_strength_str = request.form.get("prosody_strength", "1.0")
        try:
            prosody_strength = float(prosody_strength_str)
            prosody_strength = max(0.0, min(1.0, prosody_strength))
        except (ValueError, TypeError):
            prosody_strength = 1.0

        # Save uploaded file
        job_id = str(uuid.uuid4())[:8]
        job_dir = UPLOAD_DIR / job_id
        job_dir.mkdir(exist_ok=True)

        video_path = job_dir / video_file.filename
        video_file.save(str(video_path))

        # Setup job tracking
        jobs[job_id] = {
            "status": "processing",
            "stage": 0,
            "progress": 0,
            "message": "Starting...",
            "output_video": None,
            "srt_file": None,
            "subtitle_srt_file": None,
            "source_lang": None,
            "target_lang": target_lang,
            "voice": voice,
            "model_size": model_size,
            "keep_bg": keep_bg,
            "burn_subtitles": burn_subtitles,
            "gen_srt": gen_srt,
            "multi_speaker": multi_speaker,
            "num_speakers": num_speakers,
            "voice_clone": voice_clone,
            "extend_video": extend_video,
            "emotion_transfer": emotion_transfer,
            "prosody_strength": prosody_strength,
            "anti_copyright": anti_copyright,
            "blur_original_subtitles": blur_original_subtitles,
            "subtitle_lang": subtitle_lang,
            "funny_mode": funny_mode,
            "segments_count": 0,
            "elapsed_seconds": 0,
            "can_resume": False,
            "logs": [],
        }

        # Start processing in background thread
        thread = threading.Thread(
            target=process_job,
            args=(job_id, str(video_path), target_lang, voice,
                  model_size, keep_bg, burn_subtitles, gen_srt,
                  False, multi_speaker, num_speakers, voice_clone, extend_video,
                  emotion_transfer, prosody_strength, anti_copyright,
                  blur_original_subtitles, subtitle_lang),
            daemon=True,
        )
        thread.start()

        return jsonify({"job_id": job_id})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Handle large file upload errors
@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({"error": "File too large! Maximum allowed size is 500MB. Please use a shorter video or lower quality."}), 413


# Handle 404
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404


def process_job(job_id, video_path, target_lang, voice, model_size,
                keep_bg, burn_subtitles, gen_srt, resume=False,
                multi_speaker=False, num_speakers=None,
                voice_clone=False, extend_video=True,
                emotion_transfer=True, prosody_strength=1.0,
                anti_copyright=False, blur_original_subtitles=False,
                subtitle_lang=None, funny_mode=False):
    """Background job processor."""
    import dubber

    # Register cancel flag
    cancel_flags[job_id] = False

    output_path = str(OUTPUT_DIR / job_id / "dubbed.mp4")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Job directory for checkpoints — use the upload dir which has the video
    job_dir = str(UPLOAD_DIR / job_id)

    # Save job parameters to checkpoint so auto_resume can restore them after OOM/restart
    try:
        dubber.save_checkpoint(job_dir, 0, {
            "target_lang": target_lang,
            "voice": voice,
            "model_size": model_size,
            "keep_bg": keep_bg,
            "burn_subtitles": burn_subtitles,
            "gen_srt": gen_srt,
            "multi_speaker": multi_speaker,
            "num_speakers": num_speakers,
            "voice_clone": voice_clone,
            "extend_video": extend_video,
            "emotion_transfer": emotion_transfer,
            "prosody_strength": prosody_strength,
        })
    except Exception:
        pass
    # Also save to job_status.json for the status endpoint
    save_job_status(job_id, jobs.get(job_id, {}))

    def progress_callback(stage, message, sub_progress=None, sub_total=None):
        # Check cancel flag
        if cancel_flags.get(job_id, False):
            raise InterruptedError("Job cancelled by user")

        # Stage ranges (start%, end%) for overall progress
        stage_ranges = {1: (0, 5), 2: (5, 30), 3: (30, 55), 4: (55, 75), 5: (75, 90), 6: (90, 95)}
        stage_done = {1: 5, 2: 30, 3: 55, 4: 75, 5: 90, 6: 95, "done": 100}

        if stage == "done":
            pct = 100
        elif isinstance(stage, int) and stage in stage_ranges:
            start_pct, end_pct = stage_ranges[stage]
            if sub_progress is not None and sub_total and sub_total > 0:
                sub_frac = sub_progress / sub_total
            else:
                sub_frac = 0
            pct = start_pct + sub_frac * (end_pct - start_pct)
        else:
            pct = stage_done.get(stage, 0)

        # Build log entry
        log_entry = {"stage": stage if isinstance(stage, int) else 6,
                      "message": (message or "").strip(),
                      "time": time.time()}
        if sub_progress is not None:
            log_entry["sub_progress"] = sub_progress
            log_entry["sub_total"] = sub_total

        jobs[job_id].update({
            "stage": stage if isinstance(stage, int) else 6,
            "progress": round(pct, 1),
            "message": (message or "").strip(),
            "sub_progress": sub_progress,
            "sub_total": sub_total,
        })
        # Persist to disk (throttled — at most once per 5 seconds)
        if not hasattr(progress_callback, "_last_save"):
            progress_callback._last_save = 0
        if time.time() - progress_callback._last_save > 5:
            progress_callback._last_save = time.time()
            save_job_status(job_id, jobs[job_id])

        # Append to log history (keep last 200)
        if "logs" not in jobs[job_id]:
            jobs[job_id]["logs"] = []
        # Only add if message differs from last
        logs = jobs[job_id]["logs"]
        if not logs or logs[-1].get("message") != log_entry["message"]:
            logs.append(log_entry)
            if len(logs) > 200:
                jobs[job_id]["logs"] = logs[-200:]

    try:
        result = dubber.dub_video(
            video_path=video_path,
            target_lang=target_lang,
            voice=voice,
            model_size=model_size,
            output_path=output_path,
            keep_background=False,  # legacy: full audio at low volume (not used)
            keep_background_music=keep_bg,  # new: Demucs vocal isolation + sidechain
            burn_subtitles=burn_subtitles,
            generate_srt_file=gen_srt,
            progress_callback=progress_callback,
            job_dir=job_dir,
            resume=resume,
            multi_speaker=multi_speaker,
            num_speakers=num_speakers,
            use_voice_cloning=voice_clone,
            extend_video=extend_video,
            emotion_transfer=emotion_transfer,
            prosody_strength=prosody_strength,
            anti_copyright=anti_copyright,
            blur_original_subtitles=blur_original_subtitles,
            subtitle_lang=subtitle_lang,
            funny_mode=funny_mode,
        )
        jobs[job_id].update({
            "status": "done",
            "progress": 100,
            "message": "Done!",
            "output_video": result["output_video"],
            "srt_file": result.get("srt_file"),
            "subtitle_srt_file": result.get("subtitle_srt_file"),
            "subtitle_language": result.get("subtitle_language", ""),
            "source_lang": result.get("source_lang", ""),
            "voice": result.get("voice", ""),
            "segments_count": result.get("segments_count", 0),
            "elapsed_seconds": result.get("elapsed_seconds", 0),
            "multi_speaker": result.get("multi_speaker", False),
            "speakers": result.get("speakers", {}),
            "num_speakers": result.get("num_speakers", 0),
            "emotion_summary": result.get("emotion_summary"),
        })
        save_job_status(job_id, jobs[job_id])
    except InterruptedError as e:
        # Job cancelled by user
        if "logs" not in jobs[job_id]:
            jobs[job_id]["logs"] = []
        jobs[job_id]["logs"].append({"stage": "err", "message": "Cancelled by user", "time": time.time()})
        jobs[job_id].update({
            "status": "cancelled",
            "message": "Job cancelled by user.",
            "can_resume": False,
        })
        save_job_status(job_id, jobs[job_id])
        print(f"[job {job_id}] Cancelled by user")
    except Exception as e:
        # Log error to console
        err_msg = f"Error: {e}"
        if "logs" not in jobs[job_id]:
            jobs[job_id]["logs"] = []
        jobs[job_id]["logs"].append({"stage": "err", "message": err_msg, "time": time.time()})

        # Check if we have a checkpoint — if so, mark as "paused" not "error"
        ckpt = dubber.load_checkpoint(job_dir) if job_dir else None
        if ckpt:
            jobs[job_id].update({
                "status": "paused",
                "message": f"Interrupted at stage {ckpt['stage']}. Click Resume to continue from where it stopped.",
                "stage": ckpt["stage"],
                "can_resume": True,
            })
        else:
            jobs[job_id].update({
                "status": "error",
                "message": str(e),
            })
        save_job_status(job_id, jobs[job_id])
    finally:
        # === Automatic cache cleanup after every job (done or error) ===
        import gc
        import shutil as _shutil
        from pathlib import Path

        # 1. Clear Python garbage + model caches
        gc.collect()

        # 2. Delete temp working dirs for this job
        try:
            for tmp_dir in Path("/tmp").glob("dubber_*"):
                try:
                    _shutil.rmtree(tmp_dir, ignore_errors=True)
                except Exception:
                    pass
        except Exception:
            pass

        # 3. Clear torch cache if loaded
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            torch.cuda.synchronize() if torch.cuda.is_available() else None
        except Exception:
            pass

        # 4. Free ModelManager memory
        try:
            from model_manager import ModelManager
            mm = ModelManager()
            mm.unload_current()
        except Exception:
            pass

        # 5. Clear cancel flag
        cancel_flags.pop(job_id, None)

        print(f"[job {job_id}] Cache cleanup done")


@app.route("/api/status/<job_id>")
def api_status(job_id):
    job = jobs.get(job_id)
    if not job:
        # Check if there's a checkpoint on disk (server may have restarted)
        import dubber
        job_dir = str(UPLOAD_DIR / job_id)
        ckpt = dubber.load_checkpoint(job_dir)
        if ckpt:
            # Reconstruct job state from checkpoint
            jobs[job_id] = {
                "status": "paused",
                "stage": ckpt["stage"],
                "progress": {1: 5, 2: 30, 3: 55, 4: 75, 5: 90}.get(ckpt["stage"], 0),
                "message": f"Interrupted at stage {ckpt['stage']}. Click Resume to continue.",
                "can_resume": True,
                "target_lang": ckpt.get("target_lang", ""),
                "logs": [],
            }
            return jsonify(jobs[job_id])
        return jsonify({"error": "Job not found"}), 404

    # Return job status with recent logs (last 20 to keep response small)
    resp_data = dict(job)
    resp_data["logs"] = job.get("logs", [])[-20:]
    return jsonify(resp_data)


@app.route("/api/resume/<job_id>", methods=["POST"])
def api_resume(job_id):
    """Resume a paused/interrupted job from its last checkpoint."""
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    if job["status"] not in ("paused", "error"):
        return jsonify({"error": "Job is not in a resumable state"}), 400

    # Find the uploaded video
    job_upload_dir = UPLOAD_DIR / job_id
    video_path = None
    if job_upload_dir.exists():
        for f in job_upload_dir.iterdir():
            if f.is_file() and f.suffix in ('.mp4', '.mkv', '.avi', '.mov', '.webm'):
                video_path = str(f)
                break

    if not video_path:
        return jsonify({"error": "Original video file not found"}), 400

    # Get target_lang and voice from job memory, or fall back to checkpoint
    target_lang = job.get("target_lang", "")
    voice = job.get("voice")
    multi_speaker = job.get("multi_speaker", False)
    num_speakers = job.get("num_speakers")
    if not target_lang:
        # Try reading from checkpoint
        import dubber
        ckpt = dubber.load_checkpoint(str(job_upload_dir))
        if ckpt:
            target_lang = ckpt.get("target_lang", "hi")
            if not voice:
                voice = ckpt.get("voice")
            if ckpt.get("multi_speaker"):
                multi_speaker = True
                num_speakers = ckpt.get("num_speakers")
        else:
            target_lang = "hi"
    if not voice:
        voice = None

    # Update job state
    job.update({
        "status": "processing",
        "message": "Resuming from checkpoint...",
        "can_resume": False,
        "target_lang": target_lang,
        "voice": voice,
        "multi_speaker": multi_speaker,
        "num_speakers": num_speakers,
    })

    thread = threading.Thread(
        target=process_job,
        args=(job_id, video_path, target_lang,
              voice, job.get("model_size", "base"),
              job.get("keep_bg", False), job.get("burn_subtitles", False),
              job.get("gen_srt", True)),
        kwargs={"resume": True, "multi_speaker": multi_speaker,
                "num_speakers": num_speakers,
                "voice_clone": job.get("voice_clone", False),
                "extend_video": job.get("extend_video", True),
                "emotion_transfer": job.get("emotion_transfer", True),
                "prosody_strength": job.get("prosody_strength", 1.0),
                "anti_copyright": job.get("anti_copyright", False),
                "blur_original_subtitles": job.get("blur_original_subtitles", False),
                "subtitle_lang": job.get("subtitle_lang", None),
                "funny_mode": job.get("funny_mode", False)},
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id, "status": "resuming"})


@app.route("/api/cancel/<job_id>", methods=["POST"])
def api_cancel(job_id):
    """Cancel a running job."""
    if job_id not in jobs:
        return jsonify({"error": "Job not found"}), 404

    # Set cancel flag — the processing thread checks this in progress_callback
    cancel_flags[job_id] = True

    # Update status immediately
    jobs[job_id].update({
        "status": "cancelled",
        "message": "Cancelling...",
    })
    save_job_status(job_id, jobs[job_id])

    return jsonify({"job_id": job_id, "status": "cancelling"})


@app.route("/api/cleanup/<job_id>", methods=["POST"])
def api_cleanup(job_id):
    """Full cleanup after user is done with the result.
    Removes: upload dir, output dir, checkpoints, temp files, model caches.
    Frees all memory and storage for this job."""
    import gc
    import shutil as _shutil

    # Cancel if still running
    cancel_flags[job_id] = True

    # Remove from memory
    jobs.pop(job_id, None)
    cancel_flags.pop(job_id, None)

    # Delete upload directory (original video + checkpoints + status)
    upload_path = UPLOAD_DIR / job_id
    if upload_path.exists():
        _shutil.rmtree(upload_path, ignore_errors=True)

    # Delete output directory (dubbed video + srt)
    output_path = OUTPUT_DIR / job_id
    if output_path.exists():
        _shutil.rmtree(output_path, ignore_errors=True)

    # Delete temp working dirs
    for pattern in ["dubber_*", "video_extend_*", "demucs_*"]:
        for f in Path("/tmp").glob(pattern):
            try:
                if f.is_dir():
                    _shutil.rmtree(f, ignore_errors=True)
                else:
                    f.unlink(missing_ok=True)
            except Exception:
                pass

    # Clear Python garbage + model caches
    gc.collect()

    # Free model memory
    try:
        from model_manager import ModelManager
        mm = ModelManager()
        mm.unload_current()
    except Exception:
        pass

    # Clear torch cache
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass

    print(f"[cleanup] Job {job_id} fully purged — storage + memory freed")
    return jsonify({"job_id": job_id, "status": "cleaned"})


@app.route("/api/download/<job_id>/<ftype>")
def api_download(job_id, ftype):
    job = jobs.get(job_id)

    # If job not in memory, check if output exists on disk
    if not job:
        video_path = OUTPUT_DIR / job_id / "dubbed.mp4"
        srt_path = OUTPUT_DIR / job_id / "dubbed.srt"
        if ftype == "video" and video_path.exists():
            return send_file(str(video_path), as_attachment=True,
                             download_name=f"dubbed.mp4")
        elif ftype == "srt" and srt_path.exists():
            return send_file(str(srt_path), as_attachment=True,
                             download_name=f"subtitles.srt")
        return jsonify({"error": "Job not found"}), 404

    if job["status"] != "done":
        return jsonify({"error": "Job not ready yet"}), 404

    if ftype == "video" and job.get("output_video"):
        return send_file(job["output_video"], as_attachment=True,
                         download_name=f"dubbed_{job['target_lang']}.mp4")
    elif ftype == "srt" and job.get("srt_file"):
        return send_file(job["srt_file"], as_attachment=True,
                         download_name=f"subtitles_{job['target_lang']}.srt")
    elif ftype == "subsrt" and job.get("subtitle_srt_file"):
        sub_lang = job.get("subtitle_lang", "sub")
        return send_file(job["subtitle_srt_file"], as_attachment=True,
                         download_name=f"subtitles_{sub_lang}.srt")
    return jsonify({"error": "File not found"}), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n🎬 Free Video Dubber Web UI")
    print(f"   Open http://localhost:{port} in your browser\n")
    app.run(host="0.0.0.0", port=port, debug=False)
