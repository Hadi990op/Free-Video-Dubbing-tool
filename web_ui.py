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
            <div style="font-size: 12px; opacity: 0.6; margin-top: 6px;">
                💡 <b>Preserve Background</b>: Uses AI (Demucs) to separate speech from background music/SFX. Only the speech is dubbed, background audio is preserved with professional sidechain ducking. <b>ON by default.</b><br>
                💡 <b>Intelligent Voice Detection</b>: AI automatically detects each speaker's gender (male/female/child) from voice pitch and assigns the best matching voice.<br>
                💡 <b>Voice Cloning</b>: Uses Chatterbox Multilingual V3 (ZeroGPU) to clone each speaker's original voice and speaks the translated text in that voice — preserves speaker identity across languages.<br>
                💡 <b>Extend Video</b>: When dubbed audio is longer than original, the video is extended (last frame frozen) instead of cutting audio.
            </div>
        </div>

        <!-- Submit -->
        <button class="btn btn-primary" id="dubBtn" onclick="startDubbing()">
            🎬 Start Dubbing
        </button>

        <!-- Progress -->
        <div class="progress-container" id="progressContainer">
            <div class="progress-bar-bg">
                <div class="progress-bar-fill" id="progressBar"></div>
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

// Compute base path so API calls work regardless of mount point
// e.g. if served at /dubber/, base = '/dubber/'
const BASE = window.location.pathname.replace(/\/[^/]*$/, '/');

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
    var numSpeakersVal = document.getElementById('numSpeakers').value;
    if (numSpeakersVal) formData.append('num_speakers', numSpeakersVal);

    document.getElementById('dubBtn').disabled = true;
    document.getElementById('progressContainer').classList.add('active');
    document.getElementById('resultContainer').classList.remove('active');
    document.getElementById('errorContainer').classList.remove('active');
    document.getElementById('progressBar').style.width = '0%';
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

                // Show processing indicator
                document.getElementById('processingIndicator').classList.add('active');

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
                    addConsoleLog('✅ Dubbing complete!', 'done', -1);
                    document.getElementById('processingIndicator').classList.remove('active');
                    showResult(data);
                } else if (data.status === 'paused') {
                    clearInterval(pollInterval);
                    addConsoleLog('⏸ Paused: ' + data.message, 'err', -1);
                    document.getElementById('processingIndicator').classList.remove('active');
                    showError(data.message);
                    if (data.can_resume) document.getElementById('resumeBtn').style.display = 'block';
                } else if (data.status === 'error') {
                    clearInterval(pollInterval);
                    addConsoleLog('❌ Error: ' + data.message, 'err', -1);
                    document.getElementById('processingIndicator').classList.remove('active');
                    showError(data.message);
                    if (data.can_resume) document.getElementById('resumeBtn').style.display = 'block';
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

    document.getElementById('resultInfo').innerHTML = `
        Source: <span>${data.source_lang}</span> → Target: <span>${data.target_lang}</span><br>
        Segments: <span>${data.segments_count}</span> | Time: <span>${data.elapsed_seconds}s</span> | Voice: <span>${data.voice}</span>
        ${speakerInfo}
    `;

    document.getElementById('downloadVideo').href = BASE + 'api/download/' + jobId + '/video';
    if (data.srt_file) {
        document.getElementById('downloadSrt').style.display = 'block';
        document.getElementById('downloadSrt').href = BASE + 'api/download/' + jobId + '/srt';
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
                  False, multi_speaker, num_speakers, voice_clone, extend_video),
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
                voice_clone=False, extend_video=True):
    """Background job processor."""
    import dubber

    output_path = str(OUTPUT_DIR / job_id / "dubbed.mp4")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Job directory for checkpoints — use the upload dir which has the video
    job_dir = str(UPLOAD_DIR / job_id)

    def progress_callback(stage, message, sub_progress=None, sub_total=None):
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
        )
        jobs[job_id].update({
            "status": "done",
            "progress": 100,
            "message": "Done!",
            "output_video": result["output_video"],
            "srt_file": result.get("srt_file"),
            "source_lang": result.get("source_lang", ""),
            "voice": result.get("voice", ""),
            "segments_count": result.get("segments_count", 0),
            "elapsed_seconds": result.get("elapsed_seconds", 0),
            "multi_speaker": result.get("multi_speaker", False),
            "speakers": result.get("speakers", {}),
            "num_speakers": result.get("num_speakers", 0),
        })
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
                "extend_video": job.get("extend_video", True)},
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id, "status": "resuming"})


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
    return jsonify({"error": "File not found"}), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n🎬 Free Video Dubber Web UI")
    print(f"   Open http://localhost:{port} in your browser\n")
    app.run(host="0.0.0.0", port=port, debug=False)
