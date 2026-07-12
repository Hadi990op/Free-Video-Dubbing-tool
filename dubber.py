#!/usr/bin/env python3
"""
Free Video Dubber - Core Dubbing Engine
100% Free: Whisper STT + Google Translate + Edge TTS + FFmpeg
No API keys required.
"""

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
import json
import time
from pathlib import Path
import shutil
import threading

# Non-speech sound preservation (laughs, sighs, reactions)
try:
    from non_speech import get_non_speech_clips, mix_non_speech_into_dub
except ImportError:
    get_non_speech_clips = None
    mix_non_speech_into_dub = None

# ---------------------------------------------------------------------------
# Checkpoint / Resume system
# ---------------------------------------------------------------------------
# Each stage saves its output to a checkpoint file in the job directory.
# If the process crashes or internet drops, we can resume from the last
# completed stage instead of starting from scratch.

# Stage numbers:
#   1 = extract audio       (local, no internet)
#   2 = transcribe           (local, no internet - Whisper)
#   3 = translate            (INTERNET - Google Translate)
#   4 = generate TTS         (INTERNET - Edge-TTS)
#   5 = build dubbed audio   (local, no internet)
#   6 = mux video+audio      (local, no internet)

CHECKPOINT_FILE = "checkpoint.json"


def save_checkpoint(job_dir, stage, data):
    """Save checkpoint data to job directory."""
    ckpt_path = os.path.join(job_dir, CHECKPOINT_FILE)
    ckpt = {"stage": stage, "timestamp": time.time()}
    ckpt.update(data)
    with open(ckpt_path, "w") as f:
        json.dump(ckpt, f, ensure_ascii=False, indent=2)


def load_checkpoint(job_dir):
    """Load checkpoint data from job directory. Returns None if not found."""
    ckpt_path = os.path.join(job_dir, CHECKPOINT_FILE)
    if not os.path.exists(ckpt_path):
        return None
    try:
        with open(ckpt_path) as f:
            return json.load(f)
    except Exception:
        return None

# ---------------------------------------------------------------------------
# Language maps
# ---------------------------------------------------------------------------

# Common target languages -> default Edge-TTS voice (single speaker)
DEFAULT_VOICES = {
    "hi": "hi-IN-MadhurNeural",
    "en": "en-US-AriaNeural",
    "es": "es-ES-ElviraNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
    "it": "it-IT-ElsaNeural",
    "pt": "pt-BR-FranciscaNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "ar": "ar-SA-HamedNeural",
    "tr": "tr-TR-EmelNeural",
    "id": "id-ID-GadisNeural",
    "bn": "bn-IN-TanishaaNeural",
    "ta": "ta-IN-PallaviNeural",
    "te": "te-IN-ShrutiNeural",
    "ur": "ur-PK-UzmaNeural",
    "mr": "mr-IN-AarohiNeural",
    "gu": "gu-IN-DhwaniNeural",
    "kn": "kn-IN-SapnaNeural",
    "ml": "ml-IN-SobhanaNeural",
    "pa": "pa-IN-NeeruNeural",
    "th": "th-TH-PremwadeeNeural",
    "vi": "vi-VN-HoaiMyNeural",
    "pl": "pl-PL-ZofiaNeural",
    "nl": "nl-NL-ColetteNeural",
    "sv": "sv-SE-SofieNeural",
    "fa": "fa-IR-DilaraNeural",
    "he": "he-IL-HilaNeural",
    "uk": "uk-UA-PolinaNeural",
    "ms": "ms-MY-YasminNeural",
    "fil": "fil-PH-AngeloNeural",
}

# ---------------------------------------------------------------------------
# Multi-Speaker Voice Pool
# ---------------------------------------------------------------------------
# For each language, a pool of distinct voices to assign to different speakers.
# Ordered: alternating male/female for variety, then regional variants.
# When a video has N speakers, we assign pool[0]..pool[N-1] to each speaker.

VOICE_POOL = {
    "hi": [
        "hi-IN-MadhurNeural",      # Male 1
        "hi-IN-SwaraNeural",       # Female 1
        "en-IN-PrabhatNeural",      # Male 2 (English-Indian, sounds similar)
        "en-IN-NeerjaNeural",       # Female 2
        "bn-IN-BashkarNeural",      # Male 3 (Bengali-Indian)
        "bn-IN-TanishaaNeural",     # Female 3
        "mr-IN-ManoharNeural",      # Male 4 (Marathi)
        "mr-IN-AarohiNeural",       # Female 4
    ],
    "en": [
        "en-US-AndrewNeural",       # Male 1
        "en-US-AriaNeural",         # Female 1
        "en-US-BrianNeural",        # Male 2
        "en-US-JennyNeural",        # Female 2
        "en-US-ChristopherNeural",  # Male 3
        "en-US-MichelleNeural",     # Female 3
        "en-US-EricNeural",         # Male 4
        "en-US-EmmaNeural",         # Female 4
        "en-GB-RyanNeural",         # Male 5 (British)
        "en-GB-SoniaNeural",        # Female 5 (British)
        "en-AU-WilliamNeural",      # Male 6 (Australian)
        "en-AU-NatashaNeural",      # Female 6 (Australian)
    ],
    "es": [
        "es-ES-AlvaroNeural",       # Male 1 (Spain)
        "es-ES-ElviraNeural",       # Female 1 (Spain)
        "es-MX-JorgeNeural",        # Male 2 (Mexico)
        "es-MX-DaliaNeural",        # Female 2 (Mexico)
        "es-AR-TomasNeural",        # Male 3 (Argentina)
        "es-AR-ElenaNeural",        # Female 3 (Argentina)
        "es-CO-GonzaloNeural",      # Male 4 (Colombia)
        "es-CO-SalomeNeural",       # Female 4 (Colombia)
    ],
    "fr": [
        "fr-FR-HenriNeural",        # Male 1
        "fr-FR-DeniseNeural",       # Female 1
        "fr-CA-AntoineNeural",      # Male 2 (Canadian)
        "fr-CA-SylvieNeural",       # Female 2 (Canadian)
        "fr-FR-RemyMultilingualNeural",  # Male 3
        "fr-FR-EloiseNeural",       # Female 3
        "fr-CH-FabriceNeural",      # Male 4 (Swiss)
        "fr-CH-ArianeNeural",       # Female 4 (Swiss)
    ],
    "de": [
        "de-DE-ConradNeural",       # Male 1
        "de-DE-KatjaNeural",        # Female 1
        "de-AT-JonasNeural",        # Male 2 (Austrian)
        "de-AT-IngridNeural",       # Female 2 (Austrian)
        "de-DE-KillianNeural",     # Male 3
        "de-DE-AmalaNeural",        # Female 3
        "de-CH-JanNeural",          # Male 4 (Swiss)
        "de-CH-LeniNeural",         # Female 4 (Swiss)
    ],
    "it": [
        "it-IT-DiegoNeural",        # Male 1
        "it-IT-ElsaNeural",         # Female 1
        "it-IT-GiuseppeMultilingualNeural",  # Male 2
        "it-IT-IsabellaNeural",     # Female 2
    ],
    "pt": [
        "pt-BR-AntonioNeural",      # Male 1 (Brazilian)
        "pt-BR-FranciscaNeural",    # Female 1 (Brazilian)
        "pt-PT-DuarteNeural",       # Male 2 (European)
        "pt-PT-RaquelNeural",       # Female 2 (European)
        "pt-BR-ThalitaMultilingualNeural",  # Female 3
    ],
    "ru": [
        "ru-RU-DmitryNeural",       # Male 1
        "ru-RU-SvetlanaNeural",     # Female 1
    ],
    "ja": [
        "ja-JP-KeitaNeural",        # Male 1
        "ja-JP-NanamiNeural",       # Female 1
    ],
    "ko": [
        "ko-KR-InJoonNeural",       # Male 1
        "ko-KR-SunHiNeural",        # Female 1
        "ko-KR-HyunsuMultilingualNeural",  # Male 2
    ],
    "zh": [
        "zh-CN-YunjianNeural",      # Male 1
        "zh-CN-XiaoxiaoNeural",     # Female 1
        "zh-CN-YunxiNeural",         # Male 2
        "zh-CN-XiaoyiNeural",       # Female 2
        "zh-CN-YunyangNeural",      # Male 3
        "zh-CN-liaoning-XiaobeiNeural",  # Female 3 (Liaoning dialect)
        "zh-TW-YunJheNeural",       # Male 4 (Taiwanese)
        "zh-TW-HsiaoChenNeural",    # Female 4 (Taiwanese)
    ],
    "ar": [
        "ar-SA-HamedNeural",        # Male 1 (Saudi)
        "ar-SA-ZariyahNeural",      # Female 1 (Saudi)
        "ar-EG-ShakirNeural",       # Male 2 (Egyptian)
        "ar-EG-SalmaNeural",        # Female 2 (Egyptian)
        "ar-AE-HamdanNeural",      # Male 3 (Emirati)
        "ar-AE-FatimaNeural",      # Female 3 (Emirati)
        "ar-IQ-BasselNeural",       # Male 4 (Iraqi)
        "ar-IQ-RanaNeural",         # Female 4 (Iraqi)
    ],
    "tr": [
        "tr-TR-AhmetNeural",        # Male 1
        "tr-TR-EmelNeural",         # Female 1
    ],
    "id": [
        "id-ID-ArdiNeural",         # Male 1
        "id-ID-GadisNeural",        # Female 1
    ],
    "bn": [
        "bn-IN-BashkarNeural",      # Male 1
        "bn-IN-TanishaaNeural",     # Female 1
        "bn-BD-PradeepNeural",      # Male 2 (Bangladeshi)
        "bn-BD-NabanitaNeural",     # Female 2 (Bangladeshi)
    ],
    "ta": [
        "ta-IN-ValluvarNeural",     # Male 1
        "ta-IN-PallaviNeural",      # Female 1
        "ta-LK-KumarNeural",        # Male 2 (Sri Lankan)
        "ta-LK-SaranyaNeural",      # Female 2 (Sri Lankan)
        "ta-MY-SuryaNeural",        # Male 3 (Malaysian)
        "ta-MY-KaniNeural",         # Female 3 (Malaysian)
    ],
    "te": [
        "te-IN-MohanNeural",       # Male 1
        "te-IN-ShrutiNeural",       # Female 1
    ],
    "ur": [
        "ur-PK-AsadNeural",         # Male 1
        "ur-PK-UzmaNeural",         # Female 1
        "ur-IN-SalmanNeural",       # Male 2 (Indian)
        "ur-IN-GulNeural",          # Female 2 (Indian)
    ],
    "mr": [
        "mr-IN-ManoharNeural",      # Male 1
        "mr-IN-AarohiNeural",       # Female 1
    ],
    "gu": [
        "gu-IN-NiranjanNeural",     # Male 1
        "gu-IN-DhwaniNeural",       # Female 1
    ],
    "kn": [
        "kn-IN-GaganNeural",        # Male 1
        "kn-IN-SapnaNeural",        # Female 1
    ],
    "ml": [
        "ml-IN-MidhunNeural",       # Male 1
        "ml-IN-SobhanaNeural",      # Female 1
    ],
    "pa": [
        "pa-IN-NeeruNeural",        # Female 1 (default)
        # pa has limited voices; borrow from hi for additional speakers
        "hi-IN-MadhurNeural",       # Male fallback
        "hi-IN-SwaraNeural",        # Female fallback
    ],
    "th": [
        "th-TH-NiwatNeural",        # Male 1
        "th-TH-PremwadeeNeural",     # Female 1
    ],
    "vi": [
        "vi-VN-NamMinhNeural",      # Male 1
        "vi-VN-HoaiMyNeural",       # Female 1
    ],
    "pl": [
        "pl-PL-MarekNeural",        # Male 1
        "pl-PL-ZofiaNeural",        # Female 1
    ],
    "nl": [
        "nl-NL-MaartenNeural",      # Male 1
        "nl-NL-ColetteNeural",      # Female 1
        "nl-BE-ArnaudNeural",       # Male 2 (Belgian)
        "nl-BE-DenaNeural",         # Female 2 (Belgian)
    ],
    "sv": [
        "sv-SE-MattiasNeural",      # Male 1
        "sv-SE-SofieNeural",        # Female 1
    ],
    "fa": [
        "fa-IR-FaridNeural",        # Male 1
        "fa-IR-DilaraNeural",       # Female 1
    ],
    "he": [
        "he-IL-AvriNeural",         # Male 1
        "he-IL-HilaNeural",         # Female 1
    ],
    "uk": [
        "uk-UA-OstapNeural",        # Male 1
        "uk-UA-PolinaNeural",       # Female 1
    ],
    "ms": [
        "ms-MY-OsmanNeural",        # Male 1
        "ms-MY-YasminNeural",        # Female 1
    ],
    "fil": [
        "fil-PH-AngeloNeural",      # Male 1
        "fil-PH-BlessicaNeural",    # Female 1
    ],
}


def get_voice_for_speaker(speaker_id: int, num_speakers: int, target_lang: str,
                          speaker_voices: dict = None) -> str:
    """Get the TTS voice for a given speaker ID.
    If speaker_voices is provided (explicit mapping), use that.
    Otherwise, assign from the VOICE_POOL in order."""
    if speaker_voices and speaker_id in speaker_voices:
        return speaker_voices[speaker_id]

    pool = VOICE_POOL.get(target_lang, [])
    if not pool:
        # Fallback to default voice for all speakers
        return DEFAULT_VOICES.get(target_lang, "en-US-AriaNeural")

    # Assign pool[i] to speaker i, cycling if more speakers than voices
    return pool[speaker_id % len(pool)]

# Language code -> display name
LANG_NAMES = {
    "hi": "Hindi", "en": "English", "es": "Spanish", "fr": "French",
    "de": "German", "it": "Italian", "pt": "Portuguese", "ru": "Russian",
    "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "ar": "Arabic",
    "tr": "Turkish", "id": "Indonesian", "bn": "Bengali", "ta": "Tamil",
    "te": "Telugu", "ur": "Urdu", "mr": "Marathi", "gu": "Gujarati",
    "kn": "Kannada", "ml": "Malayalam", "pa": "Punjabi", "th": "Thai",
    "vi": "Vietnamese", "pl": "Polish", "nl": "Dutch", "sv": "Swedish",
    "fa": "Persian", "he": "Hebrew", "uk": "Ukrainian", "ms": "Malay",
    "fil": "Filipino",
}


# ---------------------------------------------------------------------------
# Step 1: Extract audio from video
# ---------------------------------------------------------------------------

def extract_audio(video_path: str, output_wav: str) -> None:
    """Extract audio from video as 16kHz mono WAV (Whisper format).
    
    For better quality, we extract at 48kHz stereo first, then downmix
    to 16kHz mono for Whisper. The 48kHz version is kept for reference
    audio extraction (voice cloning) and background music mixing.
    """
    print(f"  [1/5] Extracting audio from video...")
    
    # Extract 16kHz mono for Whisper (primary use)
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-ac", "1", "-ar", "16000",
        "-f", "wav", output_wav
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg audio extraction failed:\n{result.stderr}")


# ---------------------------------------------------------------------------
# Step 1.5: Vocal Isolation (separate speech from background music/SFX)
# ---------------------------------------------------------------------------

def separate_vocals(audio_path: str, temp_dir: str,
                    progress_callback=None) -> tuple:
    """
    Separate audio into vocals (speech) and no_vocals (background music + SFX).
    Uses Demucs (Hybrid Transformer v4) — runs on CPU.

    Returns: (vocals_wav_path, no_vocals_wav_path) or (None, None) on failure.
    The vocals file is used for transcription (cleaner speech = better accuracy).
    The no_vocals file is mixed with dubbed TTS to preserve original background.
    """
    print(f"  [1.5] Separating vocals from background (Demucs mdx_extra)...")
    if progress_callback:
        progress_callback(1, "Separating vocals from background music...")

    output_dir = os.path.join(temp_dir, "demucs_out")
    os.makedirs(output_dir, exist_ok=True)

    try:
        from demucs.separate import main as demucs_main
    except ImportError:
        print("        demucs not installed, skipping vocal isolation")
        return None, None

    import sys
    old_argv = sys.argv
    demucs_args = [
        "demucs",
        "--two-stems", "vocals",   # vocals vs no_vocals
        "-n", "htdemucs",          # Hybrid Demucs v4 (best quality, cached on this VM)
        "-o", output_dir,          # output directory
        "--shifts", "1",           # Minimize shifts for speed
        "--segment", "7",          # Chunk processing — saves RAM, prevents swap
        "--overlap", "0.25",       # Minimal overlap for speed
        audio_path,
    ]

    # Run Demucs in a subprocess with timeout to prevent hanging on long videos.
    # htdemucs on this 2-vCPU VM runs at ~1.5x real-time.
    # For a 10-min video: ~15 min. For a 2-min video: ~3 min.
    import subprocess as _sp

    # Get audio duration to calculate timeout
    try:
        dur_result = _sp.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=10)
        audio_dur = float(dur_result.stdout.strip() or "0")
    except Exception:
        audio_dur = 60  # assume 1 min if unknown

    # Timeout: 3x audio duration (htdemucs is ~1.5x real-time, give 2x margin)
    # Minimum 60s, maximum 1800s (30 min)
    demucs_timeout = min(1800, max(60, int(audio_dur * 3)))

    # Run Demucs in a separate process so we can kill it if it hangs
    venv_python = sys.executable
    # Write args to a temp file to avoid serialization issues
    import json as _json
    import tempfile as _tf
    args_file = _tf.NamedTemporaryFile(mode="w", suffix=".json", delete=False, dir=temp_dir)
    _json.dump(demucs_args, args_file)
    args_file.close()

    demucs_script = f"""
import sys, json
with open({args_file.name!r}) as f:
    sys.argv = json.load(f)
from demucs.separate import main as demucs_main
demucs_main()
"""
    try:
        proc = _sp.Popen(
            [venv_python, "-c", demucs_script],
            stdout=_sp.PIPE, stderr=_sp.PIPE)
        try:
            stdout, stderr = proc.communicate(timeout=demucs_timeout)
        except _sp.TimeoutExpired:
            proc.kill()
            proc.wait()
            print(f"        Demucs timed out after {demucs_timeout}s, skipping vocal isolation")
            sys.argv = old_argv
            return None, None
        if proc.returncode != 0:
            print(f"        Demucs failed (rc={proc.returncode}): {stderr.decode()[:200]}")
            sys.argv = old_argv
            return None, None
    except Exception as e:
        print(f"        Demucs failed: {e}")
        sys.argv = old_argv
        try: os.unlink(args_file.name)
        except: pass
        return None, None
    finally:
        sys.argv = old_argv
        try: os.unlink(args_file.name)
        except: pass

    # Find output files
    track_name = os.path.splitext(os.path.basename(audio_path))[0]
    vocals_path = os.path.join(output_dir, "htdemucs", track_name, "vocals.wav")
    no_vocals_path = os.path.join(output_dir, "htdemucs", track_name, "no_vocals.wav")

    # Fallback: check standard demucs output structure
    if not os.path.exists(vocals_path):
        htdemucs_dir = os.path.join(output_dir, "htdemucs")
        if os.path.isdir(htdemucs_dir):
            for d in os.listdir(htdemucs_dir):
                v = os.path.join(htdemucs_dir, d, "vocals.wav")
                nv = os.path.join(htdemucs_dir, d, "no_vocals.wav")
                if os.path.exists(v) and os.path.exists(nv):
                    vocals_path = v
                    no_vocals_path = nv
                    break

    if os.path.exists(vocals_path) and os.path.exists(no_vocals_path):
        # Convert to mono 16kHz for Whisper (vocals) and stereo 44.1kHz for mix (no_vocals)
        vocals_16k = os.path.join(temp_dir, "vocals_16k.wav")
        no_vocals_44k = os.path.join(temp_dir, "no_vocals_44k.wav")

        subprocess.run(
            ["ffmpeg", "-y", "-i", vocals_path, "-vn", "-ac", "1", "-ar", "16000", vocals_16k],
            capture_output=True, text=True
        )
        subprocess.run(
            ["ffmpeg", "-y", "-i", no_vocals_path, "-vn", "-ac", "2", "-ar", "48000", no_vocals_44k],
            capture_output=True, text=True
        )

        if os.path.exists(vocals_16k) and os.path.exists(no_vocals_44k):
            print(f"        Vocals isolated: {os.path.getsize(vocals_16k)} bytes")
            print(f"        Background isolated: {os.path.getsize(no_vocals_44k)} bytes")
            return vocals_16k, no_vocals_44k

    print("        Vocal isolation failed (output files not found)")
    return None, None


# ---------------------------------------------------------------------------
# Step 1.5: Speaker Diarization (detect who speaks when)
# ---------------------------------------------------------------------------

def diarize_audio(audio_path: str, num_speakers: int = None,
                  max_speakers: int = 8, min_speakers: int = 1,
                  progress_callback=None) -> list:
    """
    Run speaker diarization on audio file.
    Returns list of {start, end, speaker} segments.
    Uses simple-diarizer (Silero VAD + SpeechBrain embeddings).
    No HuggingFace token required.

    num_speakers: if known, forces that many speakers.
                  if None, auto-detects using silhouette score.
    max_speakers: max speakers to consider for auto-detection.
    min_speakers: minimum speakers to return (for voice cloning, set to 2).
    progress_callback(message) called with status updates.
    """
    if progress_callback:
        progress_callback("Loading diarization models...")

    # Patch torch hub trust check (simple-diarizer uses Silero VAD from GitHub)
    import torch
    torch.hub._check_repo_is_trusted = lambda *a, **kw: True

    from simple_diarizer.diarizer import Diarizer
    from sklearn.metrics import silhouette_score
    import numpy as np

    diar = Diarizer(embed_model='xvec', cluster_method='sc')

    if progress_callback:
        progress_callback("Detecting speakers in audio...")

    if num_speakers is not None:
        # Known number of speakers
        segments = diar.diarize(audio_path, num_speakers=num_speakers)
    else:
        # Auto-detect: try different speaker counts, use silhouette score
        # First get embeddings with a dummy run
        result = diar.diarize(audio_path, num_speakers=2, extra_info=True)
        embeds = result['embeds']
        n_embeds = len(embeds)

        if n_embeds < 3:
            # Very few segments, just use 1 speaker
            segments = diar.diarize(audio_path, num_speakers=1)
        else:
            best_n = 1
            best_score = -1
            max_try = min(max_speakers, n_embeds)

            for n in range(2, max_try + 1):
                result_n = diar.diarize(audio_path, num_speakers=n, extra_info=True)
                labels = result_n['cluster_labels']
                score = -1  # default if only 1 cluster
                if len(set(labels)) > 1:
                    score = silhouette_score(embeds, labels)
                    if progress_callback:
                        progress_callback(f"Testing {n} speakers: score={score:.3f}")
                    if score > best_score:
                        best_score = score
                        best_n = n
                # Early stop if score is very good
                if score > 0.3:
                    break

            # If best score is very low (< 0.05), it's likely a single speaker
            # But respect min_speakers (e.g. voice cloning needs at least 2)
            if best_score < 0.05 and best_n > 1:
                best_n = 1
            if best_n < min_speakers:
                best_n = min_speakers

            segments = diar.diarize(audio_path, num_speakers=best_n)

    # Convert to our format
    diarized = []
    for s in segments:
        diarized.append({
            "start": float(s["start"]),
            "end": float(s["end"]),
            "speaker": int(s["label"]),
        })

    # Count speakers
    speakers = set(d["speaker"] for d in diarized)
    num_detected = len(speakers)

    if progress_callback:
        progress_callback(f"Detected {num_detected} speakers, {len(diarized)} segments")

    print(f"        Diarization: {num_detected} speakers, {len(diarized)} segments")
    return diarized


def assign_speakers_to_segments(transcribed_segments: list,
                                  diarized_segments: list) -> list:
    """
    Merge diarization results with transcribed segments.
    For each transcribed segment, find which speaker was talking
    based on time overlap with diarized segments.

    Returns transcribed_segments with added 'speaker' field.
    """
    for seg in transcribed_segments:
        seg_start = seg["start"]
        seg_end = seg["end"]
        seg_mid = (seg_start + seg_end) / 2

        # Find the diarized segment that has the most overlap
        best_speaker = 0
        best_overlap = 0

        for d in diarized_segments:
            # Calculate overlap
            overlap_start = max(seg_start, d["start"])
            overlap_end = min(seg_end, d["end"])
            overlap = max(0, overlap_end - overlap_start)

            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = d["speaker"]

        # If no overlap found, use the speaker whose segment contains the midpoint
        if best_overlap == 0:
            for d in diarized_segments:
                if d["start"] <= seg_mid <= d["end"]:
                    best_speaker = d["speaker"]
                    break

        seg["speaker"] = best_speaker

    return transcribed_segments


# ---------------------------------------------------------------------------
# Step 2: Speech-to-Text with Whisper (with word timestamps)
# ---------------------------------------------------------------------------

# Remote VM worker for transcription offload (load balancing)
REMOTE_VM_API = "https://fan-announce-grit-sell.2n6.me/vm"

def _remote_vm_exec(command, timeout=30):
    """Execute command on remote VM via API. Returns (rc, stdout, stderr)."""
    import requests
    r = requests.post(f"{REMOTE_VM_API}/exec", json={"command": command}, timeout=timeout)
    if r.status_code != 200:
        return 1, "", f"HTTP {r.status_code}"
    data = r.json()
    return data.get("rc", 1), data.get("stdout", ""), data.get("stderr", "")

def _remote_vm_write_file(path, content_b64, timeout=30):
    """Write file to remote VM (content is base64-encoded)."""
    import requests
    r = requests.post(f"{REMOTE_VM_API}/files/write", json={"path": path, "content": content_b64}, timeout=timeout)
    return r.status_code == 200

def _remote_vm_read_file(path, timeout=30):
    """Read file from remote VM. Returns content string or None."""
    import requests
    r = requests.get(f"{REMOTE_VM_API}/files/read", params={"path": path}, timeout=timeout)
    if r.status_code == 200:
        return r.json().get("content")
    return None

def transcribe_remote(audio_path, model_size="base", language=None, timeout=25):
    """Transcribe audio on remote VM worker. Returns segments list or None on failure.
    Remote worker runs Whisper on a separate VM to offload CPU from main server.
    Uses file-based approach: upload audio, start transcription, poll for result."""
    import base64, time, json as _json

    # Check if remote VM worker is alive
    rc, out, err = _remote_vm_exec("curl -s http://localhost:5060/health", timeout=10)
    if rc != 0 or '"ok"' not in out:
        print(f"        Remote VM worker not available: {err}")
        return None

    # Upload audio file (base64 encode, may need chunking for large files)
    file_size = os.path.getsize(audio_path)
    print(f"        Uploading {file_size} bytes to remote VM...")
    if file_size > 8 * 1024 * 1024:  # 8MB limit for base64 via API
        print(f"        File too large for remote VM API ({file_size} bytes), using local")
        return None

    with open(audio_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    remote_audio = f"/opt/job_{int(time.time())}.wav"
    if not _remote_vm_write_file(remote_audio + ".b64", b64):
        print("        Failed to upload audio to remote VM")
        return None

    rc, _, _ = _remote_vm_exec(f"base64 -d {remote_audio}.b64 > {remote_audio} && rm {remote_audio}.b64")
    if rc != 0:
        print("        Failed to decode audio on remote VM")
        return None

    # Start transcription in background (avoids 30s API timeout on the exec endpoint).
    # Write a small shell script to the VM to avoid quoting hell, then run it.
    result_file = remote_audio + ".result.json"
    lang_json = f'"language":"{language}",' if language else ""
    script_content = (
        f'curl -s -X POST http://localhost:5060/transcribe_file '
        f'-H "Content-Type: application/json" '
        f'-d \'{{"path":"{remote_audio}","model_size":"{model_size}",'
        f'{lang_json}'
        f'"output":"{result_file}"}}\' > /dev/null 2>&1\n'
    )
    script_path = "/opt/run_transcribe.sh"
    # The VM files/write API takes plain text content
    _remote_vm_write_file(script_path, script_content)
    _remote_vm_exec(f"chmod +x {script_path}")
    rc, out, _ = _remote_vm_exec(f"nohup sh {script_path} > /opt/transcribe_bg.log 2>&1 & echo $!")
    if rc != 0:
        print("        Failed to start remote transcription")
        return None

    # Poll for result file (timeout after `timeout` seconds)
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(3)
        rc, out, _ = _remote_vm_exec(f"test -f {result_file} && cat {result_file}")
        if rc == 0 and out.strip():
            try:
                result = _json.loads(out.strip())
                # The result file contains the full transcription output
                # (segments, language, duration) — NOT a status response.
                if "segments" in result:
                    # Cleanup
                    _remote_vm_exec(f"rm -f {remote_audio} {result_file} {script_path}")
                    return result.get("segments", []), result.get("language", "unknown")
            except _json.JSONDecodeError:
                pass

    print(f"        Remote transcription timed out after {timeout}s")
    _remote_vm_exec(f"rm -f {remote_audio} {result_file}")
    return None


def transcribe_audio(audio_path: str, model_size: str = "base",
                      progress_callback=None,
                      use_word_alignment: bool = True) -> dict:
    """
    Transcribe audio using faster-whisper with optional WhisperX word-level alignment.
    
    Word-level alignment provides precise per-word timestamps, enabling:
    - Better audio segment timing for dubbing
    - More accurate speech-to-speech timing matching
    - Reduced over/underlap between dubbed segments
    
    Returns dict with segments: [{start, end, text, words?}, ...]
    progress_callback(current_count, total_estimate, preview_text) called periodically.

    Progress is timestamp-based: seg.start / audio_duration.
    This NEVER jumps backwards because audio timestamps are monotonically increasing.
    """
    print(f"  [2/5] Transcribing audio with Whisper ({model_size})...")

    # Get audio duration for timestamp-based progress
    import subprocess as sp
    dur_result = sp.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True
    )
    audio_duration = float(dur_result.stdout.strip() or "0")
    if audio_duration < 1:
        audio_duration = 1.0
    print(f"        Audio duration: {audio_duration:.1f}s ({audio_duration/60:.1f} min)")

    # Try remote VM first (offload CPU). Fall back to local if unavailable.
    # For long videos (>5 min), allow up to 600s (10 min) for transcription
    remote_timeout = min(600, max(30, int(audio_duration * 3)))
    remote_result = None
    try:
        print(f"        Trying remote VM worker (timeout {remote_timeout}s)...")
        remote_result = transcribe_remote(audio_path, model_size, timeout=remote_timeout)
    except Exception as e:
        print(f"        Remote VM error: {e}")

    if remote_result is not None:
        segments, detected_lang = remote_result
        print(f"        [Remote VM] Detected: {detected_lang}, {len(segments)} segments")
        if progress_callback:
            for i, seg in enumerate(segments):
                if i % 5 == 0 or i == len(segments) - 1:
                    progress_callback(i + 1, seg.get("start", 0), audio_duration, seg.get("text", "")[:60])
            progress_callback(len(segments), audio_duration, audio_duration, None)
        return {"segments": segments, "source_lang": detected_lang}

    print(f"        Falling back to local Whisper ({model_size})...")

    # --- Use faster-whisper directly (has real-time progress, faster on CPU) ---
    # WhisperX's word-level alignment doubles processing time on low-RAM CPU
    # and blocks with no progress callback. faster-whisper with word_timestamps=True
    # gives good-enough timing for dubbing, with live progress reporting.
    # Only use WhisperX for short clips (<2 min) where alignment is quick.
    if use_word_alignment and audio_duration < 120:
        try:
            segments, detected_lang = _transcribe_whisperx(
                audio_path, model_size, audio_duration, progress_callback)
            if segments:
                return {"segments": segments, "source_lang": detected_lang}
            print(f"        WhisperX failed, falling back to faster-whisper...")
        except Exception as e:
            print(f"        WhisperX error: {e!r}, falling back to faster-whisper...")

    # --- Primary: faster-whisper (real-time progress, faster on CPU) ---
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    # VAD filter skips silence — huge speedup on long videos with quiet gaps.
    # word_timestamps=True gives precise timing for better audio alignment.
    segments_iter, info = model.transcribe(
        audio_path,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(
            min_silence_duration_ms=500,   # split at 0.5s+ silences
            speech_pad_ms=200,             # pad speech by 200ms
        ),
        word_timestamps=True,
    )

    segments = []
    seg_count = 0
    last_reported_pct = -1  # track last reported percentage for dedup
    for seg in segments_iter:
        text = seg.text.strip()
        if text:
            segments.append({
                "start": round(seg.start, 3),
                "end": round(seg.end, 3),
                "text": text,
            })
            seg_count += 1

            # Timestamp-based progress: how far into the audio we've transcribed
            # This is monotonic — seg.start always increases, never goes back
            ts_pct = int((seg.start / audio_duration) * 100)
            # Report every 2% change or every 5 segments (whichever first)
            if progress_callback and (ts_pct - last_reported_pct >= 2 or seg_count % 5 == 0):
                last_reported_pct = ts_pct
                progress_callback(seg_count, seg.start, audio_duration, text[:60])

    detected_lang = info.language
    print(f"        [Local] Detected source language: {detected_lang}")
    print(f"        Found {len(segments)} speech segments")

    # Free Whisper model from memory — important for long videos.
    del model
    import gc
    gc.collect()

    if progress_callback:
        progress_callback(len(segments), audio_duration, audio_duration, None)
    return {"segments": segments, "source_lang": detected_lang}


def _transcribe_whisperx(audio_path: str, model_size: str,
                          audio_duration: float,
                          progress_callback=None) -> tuple:
    """Transcribe using WhisperX with word-level alignment.
    
    WhisperX uses:
    - faster-whisper for initial transcription
    - wav2vec2 for forced word-level alignment
    
    Returns (segments, detected_language) where segments include
    per-word timestamps.
    
    Falls back to None if WhisperX is not available or fails.
    """
    import whisperx
    from model_manager import _model_manager

    # Map our model sizes to WhisperX-compatible sizes
    wx_size = model_size
    if model_size == "large":
        wx_size = "large-v3"

    # Load model via ModelManager (swaps out any other loaded model)
    def _load():
        return whisperx.load_model(wx_size, device="cpu", compute_type="int8")

    print(f"        Loading WhisperX ({wx_size}) via ModelManager...")
    model = _model_manager.load_model(f"whisperx-{wx_size}", _load)

    # Transcribe
    print(f"        Transcribing with WhisperX...")
    audio = whisperx.load_audio(audio_path)
    result = model.transcribe(audio, batch_size=16, language=None)

    detected_lang = result.get("language", "en")
    print(f"        [WhisperX] Detected language: {detected_lang}")
    print(f"        [WhisperX] Found {len(result['segments'])} segments")

    # Free WhisperX model BEFORE alignment — saves ~400MB RAM
    del model
    try:
        _model_manager.unload_current()
    except Exception:
        pass
    import gc; gc.collect()

    # Word-level alignment — OPTIONAL, can be slow on low-RAM CPU.
    # Skip it if audio is long (>3 min) to save time; faster-whisper's
    # segment timestamps are good enough for dubbing.
    if audio_duration > 180:
        print(f"        Skipping word-level alignment (audio >3min, saves time on CPU)")
    else:
        print(f"        Running word-level alignment...")
        try:
            model_a, metadata = whisperx.load_align_model(
                language_code=detected_lang, device="cpu")
            result = whisperx.align(
                result["segments"], model_a, metadata, audio, device="cpu",
                batch_size=32)

            # Free alignment model
            del model_a
            import gc; gc.collect()
        except Exception as e:
            print(f"        Word alignment failed ({e!r}), using segment-level timestamps")
            # Still use the transcription, just without word-level alignment

    # Convert to our format
    segments = []
    for seg in result["segments"]:
        text = seg["text"].strip()
        if text:
            entry = {
                "start": round(seg["start"], 3),
                "end": round(seg["end"], 3),
                "text": text,
            }
            # Include word-level timestamps if available
            if "words" in seg and seg["words"]:
                entry["words"] = [
                    {"word": w.get("word", ""),
                     "start": round(w.get("start", 0), 3),
                     "end": round(w.get("end", 0), 3)}
                    for w in seg["words"]
                ]
            segments.append(entry)

            if progress_callback:
                progress_callback(len(segments), seg["start"], audio_duration, text[:60])

    print(f"        [WhisperX] Found {len(segments)} segments")
    if progress_callback:
        progress_callback(len(segments), audio_duration, audio_duration, None)

    return segments, detected_lang


# ---------------------------------------------------------------------------
# Step 3: Translate text segments
# ---------------------------------------------------------------------------

# Languages that should be romanized (written in Latin script like daily conversation)
# For these languages, we generate BOTH Devanagari (for Kokoro TTS) and Roman (for Edge-TTS)
ROMAN_LANGS = {"hi", "ur"}

# Languages where Kokoro TTS is available and needs native script
KOKORO_NATIVE_LANGS = {"hi", "zh", "ja"}

# Languages that should use Hinglish/Roman Urdu style — natural mix of Hindi+English+Urdu
# written in Latin script, like how people actually talk in daily life
# e.g., "Hello sabko, aaj hum AI ke baare mein baat karenge"
HINGLISH_LANGS = {"hi", "ur"}

# Language full names for LLM prompts
LANG_FULL_NAMES = {
    "hi": "Hindi (Devanagari script — written in Hindi script like 'आप कैसे हैं?')",
    "ur": "Urdu (Urdu script — written in Urdu script)",
    "es": "Spanish", "fr": "French", "de": "German", "it": "Italian",
    "pt": "Portuguese", "ru": "Russian", "ja": "Japanese", "ko": "Korean",
    "zh": "Chinese (Simplified)", "ar": "Arabic", "tr": "Turkish",
    "nl": "Dutch", "pl": "Polish", "id": "Indonesian", "vi": "Vietnamese",
    "th": "Thai", "bn": "Bengali", "ta": "Tamil", "te": "Telugu",
    "mr": "Marathi", "gu": "Gujarati", "pa": "Punjabi", "ml": "Malayalam",
    "kn": "Kannada", "or": "Odia", "sv": "Swedish", "no": "Norwegian",
    "da": "Danish", "fi": "Finnish", "cs": "Czech", "el": "Greek",
    "ro": "Romanian", "hu": "Hungarian", "uk": "Ukrainian", "he": "Hebrew",
    "fa": "Persian", "ms": "Malay", "tl": "Filipino", "sw": "Swahili",
    "en": "English",
}

# For Romanized languages, also provide a romanized name for Edge-TTS
LANG_ROMAN_NAMES = {
    "hi": "Hindi (Roman script / Hinglish — written in Latin letters like 'aap kaise hain?')",
    "ur": "Urdu (Roman script / Roman Urdu — written in Latin letters like 'aap kaise hain?')",
}


def llm_comprehend_transcript(segments: list, target_lang: str,
                               source_lang: str = None,
                               funny_mode: bool = False) -> dict:
    """Pass 1: Send the FULL transcript to the LLM and get a translation guide.

    The LLM reads the entire transcript and produces:
    - Topic summary (what the video is about)
    - Tone/register (formal, casual, educational, dramatic, etc.)
    - Key terms glossary (how to translate recurring terms consistently)
    - Character/speaker info (if multi-speaker)
    - Style notes for the target language

    This guide is then passed to the translation pass so every batch
    translates with full context — consistent terminology, correct tone,
    and awareness of the overall narrative.

    Returns: dict with keys: topic, tone, glossary, notes  (or None on failure)
    """
    import requests

    lang_name = LANG_FULL_NAMES.get(target_lang, target_lang)
    use_hinglish = target_lang in HINGLISH_LANGS
    use_roman = target_lang in ROMAN_LANGS
    use_kokoro_native = target_lang in KOKORO_NATIVE_LANGS

    if use_hinglish:
        lang_name = "Hinglish (natural Hindi-English-Urdu mix in Roman/Latin script)"
    elif use_kokoro_native:
        lang_name = LANG_FULL_NAMES.get(target_lang, target_lang)
    elif use_roman:
        lang_name = LANG_ROMAN_NAMES.get(target_lang, LANG_FULL_NAMES.get(target_lang, target_lang))

    # Build the full transcript text
    # For very long videos (>400 segments), send a condensed version:
    # first 100 + last 100 + every 10th in between
    if len(segments) > 400:
        condensed = []
        condensed.extend(segments[:100])  # first 100
        for i in range(100, len(segments) - 100, max(1, (len(segments) - 200) // 200)):
            condensed.append(segments[i])
        condensed.extend(segments[-100:])  # last 100
        transcript_segs = condensed
        print(f"        Comprehension: using {len(transcript_segs)}/{len(segments)} segments (condensed for long video)")
    else:
        transcript_segs = segments

    lines = []
    for i, seg in enumerate(transcript_segs):
        spk = seg.get("speaker")
        if spk is not None:
            lines.append(f"[S{spk}] {seg['text']}")
        else:
            lines.append(seg['text'])
    full_transcript = "\n".join(lines)

    if funny_mode:
        system = (
            f"You are a comedy dubbing consultant for a FUNNY/SARCASTIC parody dub. "
            f"You will read a full video transcript and produce a COMEDY DUBBING GUIDE "
            f"for rewriting it to {lang_name}.\n\n"
            "The goal is COMEDY: the dubbed video should be funny, irreverent, sarcastic, "
            "and mildly disrespectful — like a roast or parody dub. Serious dialogue becomes "
            "funny. Formal speech becomes casual/slang. Educational tone becomes sarcastic.\n\n"
            "Analyze the FULL transcript and provide:\n"
            "1. TOPIC: What is this video about? (1-2 sentences)\n"
            "2. TONE: What is the original tone? And what should the comedy tone be?\n"
            "   (e.g., 'Original: serious educational. Comedy: sarcastic roasting')\n"
            "3. GLOSSARY: List 5-15 key terms/phrases and how they should be PARODIED in "
            f"{lang_name}. Format: 'original → funny version'\n"
            "4. NOTES: Comedy direction — things like:\n"
            "   - How to make each character funny (sarcastic narrator, clueless expert, etc.)\n"
            "   - Running gags to establish across the video\n"
            "   - Slang/colloquial expressions to use\n"
            "   - When to use mild adult humor / double meanings / be-adab style\n"
            "   - How to handle serious moments (make them absurd)\n"
            "   - Keep it FUNNY but not outright offensive\n\n"
            "Keep the guide concise but comprehensive. This guide will be used to ensure "
            "consistent comedy style across the entire video.\n\n"
            "Output format:\n"
            "TOPIC: <summary>\n"
            "TONE: <original tone → comedy tone>\n"
            "GLOSSARY:\n"
            "- <original> → <funny version>\n"
            "NOTES:\n"
            "- <comedy note>\n"
        )
    else:
        system = (
            f"You are a professional dubbing translation consultant. "
            f"You will read a full video transcript and produce a TRANSLATION GUIDE "
            f"for translating it to {lang_name}.\n\n"
            "Analyze the FULL transcript and provide:\n"
            "1. TOPIC: What is this video about? (1-2 sentences)\n"
            "2. TONE: What is the tone/register? (e.g., casual, formal, educational, dramatic, comedic, documentary)\n"
            "3. GLOSSARY: List 5-15 key terms, names, or recurring phrases and how they should be translated to "
            f"{lang_name}. Format: 'original → translation'\n"
            "4. NOTES: Any important style guidance for the translator — things like:\n"
            "   - How to handle humor, idioms, or cultural references\n"
            "   - Whether to use formal or informal address (tu/vous, tu/usted, etc.)\n"
            "   - Any terms that should NOT be translated (kept in original language)\n"
            "   - Emotional tone shifts (if the video goes from calm to intense, etc.)\n"
            "   - Speaker-specific style differences (if multi-speaker)\n\n"
            "Keep the guide concise but comprehensive. This guide will be used to ensure "
            "consistent, context-aware translation across the entire video.\n\n"
            "Output format:\n"
            "TOPIC: <summary>\n"
            "TONE: <tone description>\n"
            "GLOSSARY:\n"
            "- <original> → <translation>\n"
            "NOTES:\n"
            "- <note>\n"
        )

    payload = {
        "model": "openai",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Here is the full transcript:\n\n{full_transcript}"}
        ],
        "temperature": 0.3,
        "max_tokens": 4000
    }

    try:
        r = requests.post("https://text.pollinations.ai/openai",
                         json=payload, timeout=90)
        if r.status_code != 200:
            print(f"        Comprehension pass failed: HTTP {r.status_code}")
            return None

        data = r.json()
        # Handle various response structures safely
        choices = data.get("choices", [])
        if not choices:
            print(f"        Comprehension: empty choices in response")
            return None
        message = choices[0].get("message", {})
        # Reasoning models put final answer in "content", thinking in "reasoning"
        guide_text = message.get("content") or ""
        if not guide_text:
            # If content is empty, the model might have used all tokens for reasoning
            print(f"        Comprehension: model returned empty content (likely ran out of tokens for reasoning)")
            return None

        # Parse the guide — the raw text is the source of truth anyway,
        # individual fields are just for logging/preview
        guide = {"raw": guide_text, "topic": "", "tone": "", "glossary": "", "notes": ""}

        import re
        # Flexible parsing: handles markdown (**TOPIC:**), varying whitespace, etc.
        topic_m = re.search(r'\*?\*?TOPIC:?\*?\*?\s*:?\s*(.+?)(?=\n\*?\*?(?:TONE|GLOSSARY|NOTES):?\*?\*?|\Z)', guide_text, re.DOTALL | re.IGNORECASE)
        tone_m = re.search(r'\*?\*?TONE:?\*?\*?\s*:?\s*(.+?)(?=\n\*?\*?(?:GLOSSARY|NOTES):?\*?\*?|\Z)', guide_text, re.DOTALL | re.IGNORECASE)
        gloss_m = re.search(r'\*?\*?GLOSSARY:?\*?\*?\s*:?\s*(.+?)(?=\n\*?\*?NOTES:?\*?\*?|\Z)', guide_text, re.DOTALL | re.IGNORECASE)
        notes_m = re.search(r'\*?\*?NOTES:?\*?\*?\s*:?\s*(.+)', guide_text, re.DOTALL | re.IGNORECASE)

        if topic_m: guide["topic"] = topic_m.group(1).strip().strip('*')
        if tone_m: guide["tone"] = tone_m.group(1).strip().strip('*')
        if gloss_m: guide["glossary"] = gloss_m.group(1).strip().strip('*')
        if notes_m: guide["notes"] = notes_m.group(1).strip().strip('*')

        print(f"        ✅ Comprehension guide generated:")
        print(f"           Topic: {guide['topic'][:80]}...")
        print(f"           Tone: {guide['tone'][:80]}...")
        return guide

    except Exception as e:
        print(f"        Comprehension pass error: {e}")
        return None


def llm_translate_batch(segments: list, target_lang: str, source_lang: str = None,
                        batch_size: int = 15, context_guide: dict = None,
                        funny_mode: bool = False) -> list:
    """Translate segments using LLM (Pollinations AI, free, no API key).
    Sends segments in batches with surrounding context for better translation.
    Returns list of translated strings (same order as input).

    Uses GPT-OSS-20B via Pollinations — context-aware, natural, idiomatic.
    Quality comparable to professional dubbing translation.

    If context_guide is provided (from llm_comprehend_transcript), includes
    the full-video translation guide in every batch's system prompt — ensuring
    consistent terminology, correct tone, and awareness of the overall narrative.

    Advantages over Google Translate:
    - Context-aware: uses surrounding sentences for correct pronouns/tense
    - Idiom-aware: translates idioms naturally, not literally
    - Conversational: natural daily-life style, not formal textbook
    - Consistent: same terminology across the whole video
    - Emotion-preserving: keeps the emotional tone of the original
    """
    import requests

    lang_name = LANG_FULL_NAMES.get(target_lang, target_lang)
    use_roman = target_lang in ROMAN_LANGS
    use_kokoro_native = target_lang in KOKORO_NATIVE_LANGS
    use_hinglish = target_lang in HINGLISH_LANGS

    # For Hindi/Urdu: generate HINGLISH (natural mix of Hindi+English+Urdu in Latin script)
    # This is how people actually talk — not formal shuddh Hindi/Urdu
    # Kokoro TTS handles Hinglish well (tested), Edge-TTS needs Devanagari fallback
    if use_hinglish:
        lang_name = "Hinglish (natural Hindi-English-Urdu mix in Roman/Latin script, like daily conversation)"
    elif use_kokoro_native:
        lang_name = LANG_FULL_NAMES.get(target_lang, target_lang)
    elif use_roman:
        lang_name = LANG_ROMAN_NAMES.get(target_lang, LANG_FULL_NAMES.get(target_lang, target_lang))
    else:
        lang_name = LANG_FULL_NAMES.get(target_lang, target_lang)

    # Build system prompt — professional dubbing translator
    # Emotion-aware: instruct the translator to preserve emotional markers
    # that guide downstream TTS to produce the right emotional delivery.
    _emotion_rules = (
        "EMOTION & DELIVERY RULES (critical for natural dubbing):\n"
        "  E1. Preserve the emotional tone exactly — angry→angry, sad→sad, excited→excited\n"
        "  E2. Keep emotional interjections as-is when natural (oh, wow, ugh, aha, hmm, etc.)\n"
        "  E3. Match the intensity — don't soften strong emotions or flatten dramatic delivery\n"
        "  E4. Use exclamation marks (!) for excited/loud/angry dialogue, NOT for calm speech\n"
        "  E5. Use ellipses (...) for hesitant/trailing-off dialogue\n"
        "  E6. Keep question marks (?) for confused/uncertain dialogue\n"
        "  E7. If the original has ALL CAPS (shouting), keep the translation in ALL CAPS too\n"
        "  E8. Don't add stage directions or emotion descriptions — just translate naturally\n"
    )

    # === FUNNY/COMEDY DUBBING MODE ===
    # When enabled, completely override the translation style:
    # - Serious dialogue → sarcastic/funny
    # - Formal speech → casual/slang/be-adab
    # - Educational tone → mocking/roast
    # - Keep mild adult humor, double meanings, irreverence
    # Works for ALL target languages (Hinglish, native script, romanized)
    if funny_mode:
        _funny_rules = (
            "COMEDY DUBBING RULES (this is a FUNNY/parody dub, NOT a faithful translation):\n"
            "  F1. REWRITE the dialogue to be FUNNY, sarcastic, irreverent — don't just translate literally\n"
            "  F2. Serious/formal speech → make it casual, slangy, be-adab (disrespectful in a funny way)\n"
            "  F3. Educational/informative tone → mock it, roast it, make sarcastic commentary\n"
            "  F4. Add mild adult humor, double meanings, innuendo when appropriate (not vulgar, just cheeky)\n"
            "  F5. Use exclamation marks generously for comic effect\n"
            "  F6. Add funny reactions/interjections (abe, yaar, seriously?, bhaisaab, kya bakwaas, etc.)\n"
            "  F7. Make the speaker sound like they're roasting/making fun of the topic\n"
            "  F8. Keep proper nouns (names, places) as-is but you can make fun of them\n"
            "  F9. DON'T change the meaning completely — keep it related to what's happening\n"
            "  F10. Keep it concise — must fit the same time slot as the original\n"
            "  F11. DON'T be outright offensive or hateful — funny and irreverent, not mean\n"
            "  F12. Keep the comedy consistent with the comedy guide provided\n\n"
        )

        if use_hinglish:
            system = (
                f"You are a COMEDY DUBBING WRITER for a funny/parody video dub. "
                f"Your job is to REWRITE the dialogue in {lang_name} — making it FUNNY, sarcastic, "
                f"irreverent and be-adab. This is NOT a faithful translation — it's a comedy roast dub.\\n\\n"
                + _funny_rules +
                "HINGLISH SPECIFIC:\n"
                "  - Write in natural Hinglish (Hindi+English+Urdu mix in Roman script)\n"
                "  - Use slang: 'abe', 'yaar', 'bhai', 'bhaisaab', 'kya bakwaas', 'bekaar', 'chirkut'\n"
                "  - Use funny insults: 'buddhu', 'bekaar', 'khatara', 'khota sikka'\n"
                "  - Example: 'AI is transforming healthcare' → 'Ab AI bhi doctor ban gaya, MBBS pass nahi kiya bas'\n"
                "  - Example: 'This technology is remarkable' → 'Bhai yeh tech itna bada, dimaag kharab ho gaya dekh ke'\n\n"
                "Only output the funny rewritten lines, one per line, prefixed with segment number. "
                "Format: '1. <funny line>'\\n\\n" + _emotion_rules
            )
        else:
            system = (
                f"You are a COMEDY DUBBING WRITER for a funny/parody video dub. "
                f"Your job is to REWRITE the dialogue in {lang_name} — making it FUNNY, sarcastic, "
                f"irreverent and be-adab (disrespectful in a funny way). "
                f"This is NOT a faithful translation — it's a comedy roast dub.\\n\\n"
                + _funny_rules +
                f"Write in the NATIVE SCRIPT of {lang_name}.\\n"
                "Use slang, colloquialisms, and funny expressions natural to that language.\\n\n"
                "Only output the funny rewritten lines, one per line, prefixed with segment number. "
                "Format: '1. <funny line>'\\n\\n" + _emotion_rules
            )

    elif use_hinglish:
        system = (
            f"You are an expert dubbing translator for professional video content. "
            f"Translate the dialogue to {lang_name}. "
            "CRITICAL RULES:\n"
            "1. Write in HINGLISH — natural mix of Hindi + English + Urdu in Roman/Latin script\n"
            "   This is how Indian/South Asian people ACTUALLY talk in daily life\n"
            "   NOT formal shuddh Hindi, NOT pure Devanagari — natural conversational Hinglish\n"
            "2. Keep common English words AS-IS (hello, welcome, thank you, please, sorry, okay,\n"
            "   actually, basically, literally, guys, super, amazing, etc.) — don't translate them\n"
            "3. Keep ALL technical/modern terms in English (AI, video, internet, app, data,\n"
            "   neural, network, computer, phone, online, digital, etc.) — don't translate them\n"
            "4. Translate ENGLISH SENTENCE STRUCTURE to Hindi/Urdu structure\n"
            "   'We will explore how AI is changing the world' → 'Hum explore karenge AI kaise duniya badal raha hai'\n"
            "   'I cannot believe this is possible' → 'Mujhe believe nahi ho raha yeh possible hai'\n"
            "   Don't leave whole English phrases untranslated — mix naturally like code-switching\n"
            "5. Keep proper nouns (names, places, brands, channels) as-is\n"
            "6. Use Roman script ONLY — no Devanagari, no Urdu script\n"
            "   Example: 'नमस्ते' → 'Namaste', 'आज' → 'aaj', 'क्या' → 'kya'\n"
            "7. Preserve the original emotion, tone, and intensity\n"
            "8. Match the register (formal→formal, casual→casual)\n"
            "9. Adapt idioms to natural Hinglish equivalents, don't translate literally\n"
            "10. Keep the translation concise — it must fit the same time slot as the original\n"
            "11. Maintain natural sentence flow for voice-over\n\n"
            "EXAMPLES of good Hinglish translation:\n"
            "  'Hello everyone, welcome to this video' → 'Hello sabko, is video mein welcome hai'\n"
            "  'Today we will learn about AI' → 'Aaj hum AI ke baare mein seekhenge'\n"
            "  'This is amazing, right?' → 'Yeh amazing hai na?'\n"
            "  'Let me show you something' → 'Main dikhata hoon kuch interesting'\n"
            "  'What do you think?' → 'Tumko kya lagta hai?'\n"
            "  'We will explore how AI is changing the world' → 'Hum explore karenge AI kaise duniya badal raha hai'\n"
            "  'I cannot believe this is possible' → 'Mujhe believe nahi ho raha yeh possible hai'\n\n"
            "Only output the translations, one per line, prefixed with the segment number. "
            "Format: '1. <translation>'\n\n" + _emotion_rules
        )
    elif use_kokoro_native:
        system = (
            f"You are an expert dubbing translator for professional video content. "
            f"Translate the dialogue to {lang_name}. "
            "CRITICAL RULES:\n"
            "1. Keep it natural and conversational — like how people actually talk in daily life\n"
            "2. Preserve the original emotion, tone, and intensity\n"
            "3. Keep proper nouns (names, places, brands) as-is in their original script\n"
            "4. Match the register (formal→formal, casual→casual)\n"
            "5. Adapt idioms to natural equivalents, don't translate literally\n"
            "6. Keep the translation concise — it must fit the same time slot as the original\n"
            "7. Maintain natural sentence flow for voice-over\n"
            "8. Write in the NATIVE SCRIPT of the target language (e.g., Devanagari for Hindi)\n\n"
            "Only output the translations, one per line, prefixed with the segment number. "
            "Format: '1. <translation>'\n\n" + _emotion_rules
        )
    elif use_roman:
        system = (
            f"You are an expert dubbing translator for professional video content. "
            f"Translate the dialogue to {lang_name}. "
            "CRITICAL RULES:\n"
            "1. Keep it natural and conversational — like how people actually talk in daily life\n"
            "2. Preserve the original emotion, tone, and intensity\n"
            "3. Keep proper nouns (names, places, brands) as-is\n"
            "4. Match the register (formal→formal, casual→casual)\n"
            "5. Adapt idioms to natural equivalents, don't translate literally\n"
            "6. Keep the translation concise — it must fit the same time slot as the original\n"
            "7. Maintain natural sentence flow for voice-over\n\n"
            "Only output the translations, one per line, prefixed with the segment number. "
            "Format: '1. <translation>'\n\n" + _emotion_rules
        )
    else:
        system = (
            f"You are an expert dubbing translator for professional video content. "
            f"Translate the dialogue to {lang_name}. "
            "CRITICAL RULES:\n"
            "1. Keep it natural and conversational\n"
            "2. Preserve the original emotion, tone, and intensity\n"
            "3. Keep proper nouns (names, places, brands) as-is\n"
            "4. Match the register (formal→formal, casual→casual)\n"
            "5. Adapt idioms to natural equivalents, don't translate literally\n"
            "6. Keep the translation concise — it must fit the same time slot as the original\n"
            "7. Maintain natural sentence flow for voice-over\n\n"
            "Only output the translations, one per line, prefixed with the segment number. "
            "Format: '1. <translation>'\n\n" + _emotion_rules
        )

    # --- Inject full-video context guide into system prompt ---
    # This is the KEY improvement: the LLM now knows the entire video's topic,
    # tone, key terms, and style before translating any batch. This ensures:
    # - Consistent terminology across all batches (e.g., "AI" always → same term)
    # - Correct register from the start (not guessing per-batch)
    # - Awareness of narrative context (e.g., "this is a tutorial" vs "this is drama")
    if context_guide and context_guide.get("raw"):
        guide_block = (
            "\n\n=== FULL VIDEO TRANSLATION GUIDE ===\n"
            f"{context_guide['raw']}\n"
            "=== END GUIDE ===\n\n"
            "Use this guide to ensure your translations are consistent with the video's "
            "topic, tone, and terminology. Follow the glossary for key terms. "
            "This guide applies to ALL segments in this video.\n"
        )
        system = system + guide_block

    translations = [None] * len(segments)

    # Process in batches with context overlap
    for batch_start in range(0, len(segments), batch_size):
        batch_end = min(batch_start + batch_size, len(segments))
        batch = segments[batch_start:batch_end]

        # Include 2 sentences before and after as context (not translated)
        ctx_before = segments[max(0, batch_start - 2):batch_start]
        ctx_after = segments[batch_end:min(len(segments), batch_end + 2)]

        # Build user message
        lines = []
        if ctx_before:
            lines.append("[Context before]")
            for i, seg in enumerate(ctx_before):
                lines.append(f"C{i}: {seg['text']}")
            lines.append("")

        lines.append("[Translate these]")
        for i, seg in enumerate(batch):
            seg_num = i + 1
            lines.append(f"{seg_num}. {seg['text']}")

        if ctx_after:
            lines.append("")
            lines.append("[Context after — do not translate]")
            for i, seg in enumerate(ctx_after):
                lines.append(f"C{i}: {seg['text']}")

        user_msg = "\n".join(lines)

        # Reasoning models (gpt-oss-20b) use tokens for thinking before
        # generating the answer. With large context guides, 2000 tokens
        # may not be enough. Start at 2000, retry at 4000 if content is empty.
        response_text = None
        for attempt_max_tokens in [2000, 4000]:
            payload = {
                "model": "openai",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg}
                ],
                "temperature": 0.3,
                "max_tokens": attempt_max_tokens
            }

            try:
                r = requests.post("https://text.pollinations.ai/openai",
                                json=payload, timeout=60)
                if r.status_code == 429:
                    raise Exception("429 rate limited")
                if r.status_code != 200:
                    raise Exception(f"HTTP {r.status_code}")

                data = r.json()
                choices = data.get("choices", [])
                if not choices:
                    raise Exception("empty choices")
                message = choices[0].get("message", {})
                response_text = message.get("content") or ""
                if not response_text:
                    if attempt_max_tokens < 4000:
                        print(f"        Batch {batch_start}: empty content, retrying with max_tokens=4000...")
                        time.sleep(2)
                        continue
                    raise Exception("empty content (ran out of tokens for reasoning)")
                # Success — got content
                break

            except Exception as e:
                if "429" in str(e):
                    print(f"        Pollinations rate limited — falling back to Google Translate")
                    return None
                if attempt_max_tokens < 4000:
                    continue  # retry with higher max_tokens
                print(f"        LLM batch {batch_start}-{batch_end} failed: {e}")
                response_text = None
                break

        if not response_text:
            continue  # skip to next batch

        # Parse numbered translations
        import re
        for line in response_text.strip().split("\n"):
            match = re.match(r'^(\d+)\.\s*(.+)', line)
            if match:
                seg_num = int(match.group(1))
                translation = match.group(2).strip()
                if 1 <= seg_num <= len(batch):
                    translations[batch_start + seg_num - 1] = translation

        # Rate limit: anonymous tier is ~1 req / 5s, small delay
        time.sleep(1)

    # If nothing was translated, signal failure
    if all(t is None for t in translations):
        return None
    return translations


def google_translate_romanized(text: str, target_lang: str, source_lang: str = "auto"):
    """Translate text and return BOTH native script AND romanized version.
    Uses Google's unofficial API which includes romanization (dt=rm).
    For hi/ur, the romanized version is natural Roman Hindi/Urdu (Hinglish/Roman Urdu)
    — like how people actually write in daily life (e.g. 'aap kya kar rahe ho?').
    Returns: (native_text, roman_text)  — roman_text is None if not available."""
    import requests
    url = "https://translate.googleapis.com/translate_a/single"
    params = [
        ("client", "gtx"), ("sl", source_lang), ("tl", target_lang),
        ("dt", "t"), ("dt", "rm"), ("q", text)
    ]
    r = requests.get(url, params=params, timeout=15)
    data = r.json()

    # Native translation: data[0] contains [translated, original, ...] pairs
    native_parts = []
    for seg in data[0]:
        if seg and seg[0]:
            native_parts.append(seg[0])
    native = " ".join(native_parts)

    # Romanization: data[0][-1][2] contains romanized words (space-separated letters)
    roman = None
    try:
        if data[0] and len(data[0]) > 0:
            last = data[0][-1]
            if last and len(last) > 2 and last[2]:
                # last[2] is a list of romanized chunks, each with space-separated letters
                roman_raw = " ".join(last[2])
                # Clean: 'a a p  k y a' -> 'aap kya'
                # Double-space = word boundary, single-space = letter separator within word
                words = roman_raw.split("  ")
                roman = " ".join("".join(w.split()) for w in words)
                import re
                roman = re.sub(r"\s+", " ", roman).strip()
    except (IndexError, TypeError):
        pass

    return native, roman


def translate_segments(segments: list, target_lang: str, source_lang: str = None,
                      job_dir: str = None, progress_callback=None,
                      funny_mode: bool = False) -> list:
    """Translate each segment's text to target language.
    
    Primary: LLM (Pollinations AI) — context-aware, natural, idiomatic.
    Fallback: Google Translate — reliable but literal.
    
    For hi/ur: produces Roman Hindi/Urdu (Hinglish/Roman Urdu) — natural daily-life
    style like 'aap kya kar rahe ho?' instead of formal Devanagari/Arabic script.
    This romanized text is what edge-tts speaks AND what subtitles show.
    Supports per-segment checkpointing: if job_dir is provided, saves progress
    to translation_checkpoint.json so it can resume if internet drops.
    progress_callback(done, total, preview) called per segment."""
    use_roman = target_lang in ROMAN_LANGS
    use_kokoro_native = target_lang in KOKORO_NATIVE_LANGS
    use_hinglish = target_lang in HINGLISH_LANGS
    lang_label = LANG_NAMES.get(target_lang, target_lang)
    if use_hinglish:
        lang_label += " (Hinglish)"
    elif use_kokoro_native:
        lang_label += " (Native)"
    elif use_roman:
        lang_label += " (Roman)"
    print(f"  [3/5] Translating to '{lang_label}'...")

    # Load existing translation progress if resuming
    trans_ckpt_path = None
    translated = [None] * len(segments)
    if job_dir:
        trans_ckpt_path = os.path.join(job_dir, "translation_checkpoint.json")
        if os.path.exists(trans_ckpt_path):
            try:
                with open(trans_ckpt_path) as f:
                    saved = json.load(f)
                for i_str, t in saved.get("translated", {}).items():
                    idx = int(i_str)
                    if idx < len(segments):
                        translated[idx] = {
                            **segments[idx],
                            "translated": t,
                        }
                print(f"        Resumed: {sum(1 for t in translated if t)} / {len(segments)} already translated")
            except Exception:
                pass

    # --- Try LLM translation first (context-aware, natural) ---
    pending_indices = [i for i, t in enumerate(translated) if t is None]
    if pending_indices:
        pending_segments = [segments[i] for i in pending_indices]
        print(f"        Translating {len(pending_segments)} segments with LLM (context-aware)...")

        # PASS 1: Comprehension — understand the full transcript before translating
        # This produces a translation guide (topic, tone, glossary, notes) that
        # ensures consistent terminology and correct register across all batches.
        context_guide = None
        if len(pending_segments) >= 5:  # Only for videos with enough content
            print(f"        Pass 1: Analyzing full transcript for context...")
            try:
                context_guide = llm_comprehend_transcript(
                    segments, target_lang, source_lang,
                    funny_mode=funny_mode)
            except Exception as e:
                print(f"        Comprehension pass error (non-fatal): {e}")
                context_guide = None
        else:
            print(f"        (Skipping comprehension pass — only {len(pending_segments)} segments)")

        # PASS 2: Translation — translate each batch with the full context guide
        if context_guide:
            print(f"        Pass 2: Translating with context guide...")
        else:
            print(f"        Pass 2: Translating (no guide available, using batch context)...")

        llm_result = None
        try:
            llm_result = llm_translate_batch(pending_segments, target_lang,
                                           source_lang, batch_size=15,
                                           context_guide=context_guide,
                                           funny_mode=funny_mode)
        except Exception as e:
            print(f"        LLM translation error: {e}")

        if llm_result:
            # Fill in LLM translations (only non-None entries)
            for i, trans in enumerate(llm_result):
                if trans:
                    if i < len(pending_indices):
                        orig_idx = pending_indices[i]
                        if orig_idx < len(segments):
                            translated[orig_idx] = {
                                **segments[orig_idx],
                                "translated": trans,
                            }
            done_count = sum(1 for t in translated if t)
            print(f"        LLM translated {done_count}/{len(segments)} segments")
            if progress_callback and pending_indices:
                first_idx = pending_indices[0]
                preview = translated[first_idx]["translated"][:60] if first_idx < len(translated) and translated[first_idx] else ""
                progress_callback(done_count, len(segments), preview)

            # Save checkpoint
            if trans_ckpt_path:
                done = {str(j): translated[j]["translated"]
                        for j in range(len(translated)) if translated[j]}
                with open(trans_ckpt_path, "w") as f:
                    json.dump({"translated": done}, f, ensure_ascii=False)
        else:
            print(f"        LLM translation failed, falling back to Google Translate...")

    # --- Fallback: Google Translate for any remaining untranslated ---
    remaining = [i for i, t in enumerate(translated) if t is None]
    if remaining:
        if use_hinglish:
            print(f"        Translating {len(remaining)} segments with Google (Hinglish/Roman)...")
        elif use_kokoro_native:
            print(f"        Translating {len(remaining)} segments with Google (Native script)...")
        elif use_roman:
            print(f"        Translating {len(remaining)} segments with Google (Roman)...")
        else:
            print(f"        Translating {len(remaining)} segments with Google Translate...")
        
        if not use_roman or use_kokoro_native:
            from deep_translator import GoogleTranslator
            translator = GoogleTranslator(source="auto", target=target_lang)

        for i in remaining:
            seg = segments[i]
            max_retries = 5
            translated_text = None

            if use_hinglish:
                # Hinglish: use Google's romanized API for natural Roman Hindi/Urdu
                for attempt in range(max_retries):
                    try:
                        native_text, roman_text = google_translate_romanized(
                            seg["text"], target_lang, source_lang or "auto"
                        )
                        # Prefer roman text (natural daily style); fallback to native
                        translated_text = roman_text if roman_text else native_text
                        break
                    except Exception as e:
                        if attempt < max_retries - 1:
                            wait_time = (attempt + 1) * 5
                            print(f"        Translation retry {attempt + 1}/{max_retries} for segment {i} (waiting {wait_time}s): {e}")
                            time.sleep(wait_time)
                        else:
                            print(f"        Warning: translation failed for segment {i} after {max_retries} retries: {e}")
                            translated_text = seg["text"]  # fallback to original
            elif use_kokoro_native:
                # Use Google Translate with native script (Devanagari for Hindi)
                for attempt in range(max_retries):
                    try:
                        translated_text = translator.translate(seg["text"])
                        break
                    except Exception as e:
                        if attempt < max_retries - 1:
                            wait_time = (attempt + 1) * 5
                            print(f"        Translation retry {attempt + 1}/{max_retries} for segment {i} (waiting {wait_time}s): {e}")
                            time.sleep(wait_time)
                        else:
                            print(f"        Warning: translation failed for segment {i} after {max_retries} retries: {e}")
            elif use_roman:
                # Romanized translation via Google's unofficial API
                for attempt in range(max_retries):
                    try:
                        native_text, roman_text = google_translate_romanized(
                            seg["text"], target_lang, source_lang or "auto"
                        )
                        # Prefer roman text (natural daily style); fallback to native
                        translated_text = roman_text if roman_text else native_text
                        break
                    except Exception as e:
                        if attempt < max_retries - 1:
                            wait_time = (attempt + 1) * 5
                            print(f"        Translation retry {attempt + 1}/{max_retries} for segment {i} (waiting {wait_time}s): {e}")
                            time.sleep(wait_time)
                        else:
                            print(f"        Warning: translation failed for segment {i} after {max_retries} retries: {e}")
                            translated_text = seg["text"]  # fallback to original
            else:
                # Non-roman: use deep_translator
                for attempt in range(max_retries):
                    try:
                        translated_text = translator.translate(seg["text"])
                        break
                    except Exception as e:
                        if attempt < max_retries - 1:
                            wait_time = (attempt + 1) * 5
                            print(f"        Translation retry {attempt + 1}/{max_retries} for segment {i} (waiting {wait_time}s): {e}")
                            time.sleep(wait_time)
                            translator = GoogleTranslator(source="auto", target=target_lang)
                        else:
                            print(f"        Warning: translation failed for segment {i} after {max_retries} retries: {e}")
                            translated_text = seg["text"]

            translated[i] = {**seg, "translated": translated_text}

            # Save checkpoint every 5 segments
            if trans_ckpt_path and ((len(remaining) - remaining.index(i)) % 5 == 0 or i == remaining[-1]):
                done = {str(j): translated[j]["translated"] for j in range(len(translated)) if translated[j]}
                with open(trans_ckpt_path, "w") as f:
                    json.dump({"translated": done}, f, ensure_ascii=False)

            # progress indicator
            done_now = sum(1 for t in translated if t)
            if done_now % 10 == 0 or i == remaining[-1]:
                print(f"        Translated {done_now}/{len(segments)} segments")
            if progress_callback:
                progress_callback(done_now, len(segments), translated[i]["translated"][:60] if translated[i] else "")

    # Clean up translation checkpoint after success
    if trans_ckpt_path and os.path.exists(trans_ckpt_path):
        os.remove(trans_ckpt_path)

    return translated


# ---------------------------------------------------------------------------
# Step 3.5: Extract reference voice clips for voice cloning
# ---------------------------------------------------------------------------

def extract_speaker_reference_audio(audio_wav: str, segments: list,
                                     temp_dir: str, max_duration: float = 30.0) -> dict:
    """Extract a reference audio clip for each speaker from the original audio.
    
    For single-speaker videos, extracts one clip from the longest speech segment.
    For multi-speaker, extracts one clip per speaker.
    
    Returns: {speaker_id: reference_audio_path} or {0: reference_audio_path} for single speaker.
    """
    print(f"  [3.5] Extracting reference voice samples for cloning...")
    ref_dir = os.path.join(temp_dir, "voice_refs")
    os.makedirs(ref_dir, exist_ok=True)
    
    # Group segments by speaker
    speaker_segs = {}
    for seg in segments:
        spk = seg.get("speaker", 0)
        if spk not in speaker_segs:
            speaker_segs[spk] = []
        speaker_segs[spk].append(seg)
    
    ref_paths = {}
    for spk, segs in speaker_segs.items():
        # Find the longest segment for this speaker (best for voice cloning)
        best_seg = max(segs, key=lambda s: s["end"] - s["start"])
        seg_dur = best_seg["end"] - best_seg["start"]
        
        # If the best segment is too short, concatenate a few segments
        if seg_dur < 10.0:
            # Concatenate up to 5 segments to get at least 10 seconds
            sorted_segs = sorted(segs, key=lambda s: s["start"])
            clips_to_concat = []
            total_dur = 0
            for s in sorted_segs:
                if total_dur >= 30.0:
                    break
                clips_to_concat.append(s)
                total_dur += (s["end"] - s["start"])
            best_seg_start = clips_to_concat[0]["start"]
            best_seg_end = clips_to_concat[-1]["end"]
        else:
            best_seg_start = best_seg["start"]
            best_seg_end = min(best_seg["end"], best_seg["start"] + max_duration)
        
        ref_path = os.path.join(ref_dir, f"ref_speaker_{spk}.wav")
        # Extract the clip from the original audio
        cmd = [
            "ffmpeg", "-y", "-i", audio_wav,
            "-ss", f"{best_seg_start:.3f}", "-to", f"{best_seg_end:.3f}",
            "-vn", "-ac", "1", "-ar", "22050", ref_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and os.path.exists(ref_path) and os.path.getsize(ref_path) > 0:
            ref_paths[spk] = ref_path
            dur = best_seg_end - best_seg_start
            print(f"        Speaker {spk}: {dur:.1f}s reference clip extracted")
        else:
            print(f"        ⚠ Failed to extract reference for speaker {spk}")
    
    return ref_paths


# ---------------------------------------------------------------------------
# Step 4: Generate TTS audio for each segment (voice cloning or edge-tts)
# ---------------------------------------------------------------------------

# ===========================================================================
# Voice Cloning backend
# ===========================================================================
# Strategy (in priority order):
#   1. LOCAL Coqui XTTS-v2  — runs on this machine, no quota, no internet.
#      Works on low-RAM VMs thanks to swap; the model is loaded once and
#      reused (singleton). Slow on 1 CPU but reliable.
#   2. HuggingFace XTTS Gradio spaces (fallback) — tonyassi/voice-clone and
#      a couple of mirrors, with retries. ZeroGPU quota may exhaust.
#   3. (handled by caller) edge-tts synthetic voice fallback.
#
# This replaces the old single-HF-space approach which failed constantly
# because of ZeroGPU quota exhaustion, with no working local alternative.

os.environ.setdefault("COQUI_TOS_AGREED", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

# ---------------------------------------------------------------------------
# OpenVoice V2 — fast voice cloning via tone color conversion
# ---------------------------------------------------------------------------
# Approach: generate TTS with edge-tts (any language), then apply the
# original speaker's voice tone using OpenVoice V2's tone converter.
# This is MUCH faster than XTTS-v2 on CPU (RTF ~2.2 vs ~30+) and works
# for ALL languages since edge-tts handles the actual speech synthesis.

_openvoice_converter = None
_openvoice_lock = threading.Lock()
_openvoice_failed = False

# Path to the OpenVoice V2 checkpoints (downloaded by setup.sh / first use)
_OPENVOICE_DIR = os.environ.get(
    "OPENVOICE_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "OpenVoice")
)
_OPENVOICE_CKPT = os.path.join(_OPENVOICE_DIR, "checkpoints_v2")


def _get_openvoice():
    """Load the OpenVoice V2 tone converter once. Returns the converter or None."""
    global _openvoice_converter, _openvoice_failed
    if _openvoice_converter is not None:
        return _openvoice_converter
    if _openvoice_failed:
        return None
    with _openvoice_lock:
        if _openvoice_converter is not None:
            return _openvoice_converter
        if _openvoice_failed:
            return None
        try:
            # Add OpenVoice to path if not installed as package
            if os.path.isdir(_OPENVOICE_DIR):
                sys.path.insert(0, _OPENVOICE_DIR)
            from openvoice.api import ToneColorConverter
            cfg = os.path.join(_OPENVOICE_CKPT, "converter", "config.json")
            ckpt = os.path.join(_OPENVOICE_CKPT, "converter", "checkpoint.pth")
            if not os.path.exists(cfg) or not os.path.exists(ckpt):
                print("        OpenVoice V2 checkpoints not found, downloading...")
                from huggingface_hub import snapshot_download
                snapshot_download(repo_id="myshell-ai/OpenVoiceV2",
                                  local_dir=_OPENVOICE_CKPT)
            print("        Loading OpenVoice V2 tone converter...")
            conv = ToneColorConverter(cfg, device="cpu")
            conv.load_ckpt(ckpt)
            _openvoice_converter = conv
            print("        ✓ OpenVoice V2 ready")
            return _openvoice_converter
        except Exception as e:
            _openvoice_failed = True
            print(f"        ⚠ Could not load OpenVoice V2 ({e!r}). Will use XTTS/edge-tts fallback.")
            return None


def _clone_openvoice(text: str, ref_audio_path: str, out_path: str,
                     target_lang: str, edge_voice: str) -> bool:
    """Generate cloned speech using OpenVoice V2 tone conversion.

    Pipeline:
      1. Generate base TTS with edge-tts in the target language
      2. Extract speaker embeddings from source TTS and reference audio
      3. Convert tone color to match the original speaker's voice

    Returns True on success, writes 24kHz mono audio to out_path.
    Works for ALL languages (edge-tts handles language, OpenVoice handles voice).
    """
    import asyncio
    import edge_tts

    conv = _get_openvoice()
    if conv is None:
        return False
    if not ref_audio_path or not os.path.exists(ref_audio_path):
        return False

    safe_text = text.strip()
    if not safe_text:
        return False
    if len(safe_text) > 500:
        safe_text = safe_text[:500]

    tmpdir = tempfile.mkdtemp(prefix="openvoice_")
    try:
        # Step 1: Generate base TTS with edge-tts
        base_tts_path = os.path.join(tmpdir, "base_tts.wav")
        try:
            communicate = edge_tts.Communicate(safe_text, edge_voice)
            # edge-tts outputs mp3; convert to wav for OpenVoice
            base_mp3 = os.path.join(tmpdir, "base_tts.mp3")
            asyncio.run(communicate.save(base_mp3))
            subprocess.run([
                "ffmpeg", "-y", "-i", base_mp3,
                "-vn", "-ac", "1", "-ar", "22050",
                "-c:a", "pcm_s16le", base_tts_path
            ], capture_output=True, text=True)
        except Exception as e:
            print(f"        OpenVoice: edge-tts base generation failed: {e!r}")
            return False

        if not os.path.exists(base_tts_path) or os.path.getsize(base_tts_path) == 0:
            return False

        # Step 2: Extract speaker embeddings
        try:
            src_se = conv.extract_se([base_tts_path])
            tgt_se = conv.extract_se([ref_audio_path])
        except Exception as e:
            print(f"        OpenVoice: SE extraction failed: {e!r}")
            return False

        # Step 3: Convert tone color
        converted_path = os.path.join(tmpdir, "converted.wav")
        try:
            conv.convert(
                audio_src_path=base_tts_path,
                src_se=src_se,
                tgt_se=tgt_se,
                output_path=converted_path,
                tau=0.7,  # higher = more of original speaker's voice character
            )
        except Exception as e:
            print(f"        OpenVoice: tone conversion failed: {e!r}")
            return False

        if not os.path.exists(converted_path) or os.path.getsize(converted_path) == 0:
            return False

        # Step 4: Normalize output to 24kHz mono
        acodec = "libmp3lame" if out_path.lower().endswith(".mp3") else "pcm_s16le"
        r = subprocess.run([
            "ffmpeg", "-y", "-i", converted_path,
            "-vn", "-ac", "2", "-ar", "48000",
            "-c:a", acodec, out_path
        ], capture_output=True, text=True)
        return r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# Singleton for the locally-loaded XTTS model (heavy, ~1.8GB).
_local_xtts = None
_local_xtts_lock = threading.Lock()
_local_xtts_failed = False  # remember permanent load failures (e.g. no RAM)


def _get_local_xtts():
    """Load the local Coqui XTTS-v2 model once and reuse it.
    Returns the TTS instance or None if it can't be loaded on this machine."""
    global _local_xtts, _local_xtts_failed
    if _local_xtts is not None:
        return _local_xtts
    if _local_xtts_failed:
        return None
    with _local_xtts_lock:
        if _local_xtts is not None:
            return _local_xtts
        if _local_xtts_failed:
            return None
        try:
            from TTS.api import TTS
            print("        Loading local XTTS-v2 model (first use downloads ~1.8GB)...")
            _local_xtts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to("cpu")
            print("        ✓ Local XTTS-v2 ready. Languages:", _local_xtts.languages)
            return _local_xtts
        except Exception as e:
            _local_xtts_failed = True
            print(f"        ⚠ Could not load local XTTS-v2 ({e!r}). Will use HF/edge-tts fallback.")
            return None


# Map our target_lang codes to XTTS language codes.
# XTTS supports: en es fr de it pt pl tr ru nl cs ar zh-cn hu ko ja hi
XTTS_LANG_MAP = {
    "hi": "hi", "es": "es", "fr": "fr", "de": "de", "ar": "ar",
    "zh": "zh-cn", "ja": "ja", "ko": "ko", "ru": "ru", "pt": "pt",
    "it": "it", "tr": "tr", "id": "id", "nl": "nl", "pl": "pl",
    "uk": "uk", "el": "el", "cs": "cs", "ro": "ro", "hu": "hu",
    "sk": "sk", "bg": "bg", "hr": "hr", "lt": "lt", "lv": "lv",
    "sl": "sl", "sv": "sv", "fi": "fi", "da": "da", "vi": "vi",
}
# Languages XTTS does NOT support -> we cannot clone locally for these;
# the caller will fall back to edge-tts (or HF if available).
XTTS_UNSUPPORTED = {"bn", "ur", "ta", "te", "mr", "gu", "pa", "fil", "ms", "th"}


def _xtts_lang_for(target_lang: str) -> str | None:
    """Return XTTS language code for a target_lang, or None if unsupported."""
    if target_lang in XTTS_UNSUPPORTED:
        return None
    return XTTS_LANG_MAP.get(target_lang, "en")


def _clone_local(text: str, ref_audio_path: str, out_path: str,
                 xtts_lang: str, max_retries: int = 2) -> bool:
    """Generate speech with the local Coqui XTTS-v2 model.
    Writes a 24kHz mono WAV to out_path. Returns True on success."""
    tts = _get_local_xtts()
    if tts is None:
        return False
    # XTTS needs a non-empty reference clip. Make sure it's a real wav.
    if not ref_audio_path or not os.path.exists(ref_audio_path) or os.path.getsize(ref_audio_path) == 0:
        return False
    # Coqui XTTS produces long generation on very long text; cap to avoid OOM/timeouts.
    safe_text = text.strip()
    if not safe_text:
        return False
    if len(safe_text) > 500:
        safe_text = safe_text[:500]
    wav_tmp = out_path + ".raw.wav"
    for attempt in range(max_retries):
        try:
            tts.tts_to_file(
                text=safe_text,
                language=xtts_lang,
                speaker_wav=ref_audio_path,
                file_path=wav_tmp,
            )
            if os.path.exists(wav_tmp) and os.path.getsize(wav_tmp) > 0:
                # normalize to 24kHz mono; match the container of out_path
                # (downstream paths are .mp3, but some callers use .wav)
                acodec = "libmp3lame" if out_path.lower().endswith(".mp3") else "pcm_s16le"
                r = subprocess.run([
                    "ffmpeg", "-y", "-i", wav_tmp,
                    "-vn", "-ac", "2", "-ar", "48000",
                    "-c:a", acodec, out_path
                ], capture_output=True, text=True)
                if r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    try:
                        os.remove(wav_tmp)
                    except OSError:
                        pass
                    return True
        except Exception as e:
            print(f"        local XTTS retry {attempt + 1}/{max_retries}: {e!r}")
            # If we ran out of memory, drop the singleton and don't keep retrying hard.
            if "out of memory" in str(e).lower() or "CUDA" in str(e):
                break
            time.sleep(2)
    if os.path.exists(wav_tmp):
        try:
            os.remove(wav_tmp)
        except OSError:
            pass
    return False


# --- HuggingFace Gradio-space voice cloning (primary GPU-backed backends) ---
# Pipeline order:
#   1. Chatterbox Multilingual V3  — 23 languages, exaggeration=emotion control,
#      real zero-shot voice cloning. MIT-licensed, free GPU via ZeroGPU.
#   2. IndexTTS-2  — 8 emotion vectors (Happy/Angry/Sad/Afraid/etc),
#      fine-grained emotion control. Apache-2.0, free GPU via ZeroGPU.
#   3. Old XTTS spaces (hasanbasbunar/tonyassi) — legacy fallback.
_hf_clients = None
_hf_client_lock = threading.Lock()

# Chatterbox Multilingual V3 language codes (23 languages)
_CHATTERBOX_LANGS = {
    'en', 'ar', 'da', 'de', 'el', 'es', 'fi', 'fr', 'he', 'hi', 'it',
    'ja', 'ko', 'ms', 'nl', 'no', 'pl', 'pt', 'ru', 'sv', 'sw', 'tr', 'zh',
}

# Old XTTS language names (for legacy hasanbasbunar space)
_XTTS_LANG_NAMES = {
    'en': 'English', 'fr': 'French', 'es': 'Spanish', 'de': 'German',
    'it': 'Italian', 'pt': 'Portuguese', 'pl': 'Polish', 'tr': 'Turkish',
    'ru': 'Russian', 'nl': 'Dutch', 'cs': 'Czech', 'ar': 'Arabic',
    'zh': 'Chinese', 'zh-cn': 'Chinese', 'ja': 'Japanese', 'ko': 'Korean',
    'hu': 'Hungarian', 'hi': 'Hindi',
}

# Legacy spaces (fallback)
_HF_VOICE_SPACES = [
    ("hasanbasbunar/Voice-Cloning-XTTS-v2", "/voice_clone_synthesis"),
    ("tonyassi/voice-clone", "/clone"),
]

# Lazy singleton clients for Chatterbox and IndexTTS-2
_chatterbox_client = None
_chatterbox_lock = threading.Lock()
_indextts_client = None
_indextts_lock = threading.Lock()


def _get_hf_clients():
    """Lazily build gradio clients for the fallback HF spaces."""
    global _hf_clients
    if _hf_clients is not None:
        return _hf_clients
    with _hf_client_lock:
        if _hf_clients is not None:
            return _hf_clients
        from gradio_client import Client
        hf_token = os.environ.get("HF_TOKEN", "")
        token_path = os.path.join(os.path.dirname(__file__), ".hf_token")
        if not hf_token and os.path.exists(token_path):
            with open(token_path) as f:
                hf_token = f.read().strip()
            os.environ["HF_TOKEN"] = hf_token
        clients = []
        for space_id, api_name in _HF_VOICE_SPACES:
            try:
                clients.append((Client(space_id, verbose=False), api_name))
            except Exception as e:
                print(f"        ⚠ Could not connect to HF space {space_id}: {e!r}")
        _hf_clients = clients
        return _hf_clients


def _get_chatterbox_client():
    """Lazily connect to the Chatterbox Multilingual V3 HF Space."""
    global _chatterbox_client
    if _chatterbox_client is not None:
        return _chatterbox_client
    with _chatterbox_lock:
        if _chatterbox_client is not None:
            return _chatterbox_client
        from gradio_client import Client
        hf_token = os.environ.get("HF_TOKEN", "")
        token_path = os.path.join(os.path.dirname(__file__), ".hf_token")
        if not hf_token and os.path.exists(token_path):
            with open(token_path) as f:
                hf_token = f.read().strip()
            os.environ["HF_TOKEN"] = hf_token
        try:
            kwargs = {"verbose": False}
            _chatterbox_client = Client("ResembleAI/Chatterbox-Multilingual-TTS-V3", **kwargs)
            print("        ✓ Connected to Chatterbox Multilingual V3")
        except Exception as e:
            print(f"        ⚠ Could not connect to Chatterbox V3: {e!r}")
            _chatterbox_client = False  # mark as failed
        return _chatterbox_client


def _clone_chatterbox(text: str, ref_audio_path: str, out_path: str,
                      target_lang: str = "en", max_retries: int = 2,
                      exaggeration: float = None,
                      temperature: float = None) -> bool:
    """Generate cloned speech via Chatterbox Multilingual V3 HF Space.
    Supports 23 languages with exaggeration control (emotion).
    Zero-shot voice cloning from 5+ seconds of reference audio.

    Emotion-aware: exaggeration and temperature can be set per-clip to
    match the original speaker's emotional delivery.
      exaggeration: 0.5 = neutral, 0.8 = expressive, 1.0 = exaggerated
      temperature:   0.8 = natural, lower = more flat/monotone (sad), higher = more varied
    """
    if target_lang not in _CHATTERBOX_LANGS:
        return False  # language not supported
    client = _get_chatterbox_client()
    if not client:
        return False
    from gradio_client import handle_file
    safe_text = text.strip()
    if not safe_text:
        return False
    if len(safe_text) > 300:
        safe_text = safe_text[:300]  # Chatterbox max 300 chars

    # Emotion-aware parameters (defaults: neutral)
    exag = exaggeration if exaggeration is not None else 0.5
    temp = temperature if temperature is not None else 0.8

    for attempt in range(max_retries):
        try:
            result = client.predict(
                text_input=safe_text,
                audio_prompt_path_input=handle_file(ref_audio_path),
                language_id_input=target_lang,
                exaggeration_input=exag,
                temperature_input=temp,
                seed_num_input=0,
                cfgw_input=0.5,
                api_name="/generate_tts_audio",
            )
            # Result may be a dict with 'value' key, a filepath string, or tuple
            if isinstance(result, dict):
                result = result.get('value', result)
            elif isinstance(result, (tuple, list)):
                result = result[0] if result else None
            if result and os.path.exists(result) and os.path.getsize(result) > 0:
                subprocess.run([
                    "ffmpeg", "-y", "-i", result,
                    "-vn", "-ac", "2", "-ar", "48000",
                    "-c:a", "libmp3lame", out_path
                ], capture_output=True, text=True)
                if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    return True
        except Exception as e:
            msg = str(e).lower()
            if "quota" in msg or "zerogpu" in msg:
                print(f"        ⚠ Chatterbox ZeroGPU quota exhausted")
                return False  # don't retry, try next backend
            if "not in the list" in msg or "language" in msg:
                print(f"        ⚠ Chatterbox doesn't support language '{target_lang}'")
                return False
            if attempt < max_retries - 1:
                time.sleep((attempt + 1) * 3)
    return False


def _get_indextts_client():
    """Lazily connect to the IndexTTS-2 HF Space."""
    global _indextts_client
    if _indextts_client is not None:
        return _indextts_client
    with _indextts_lock:
        if _indextts_client is not None:
            return _indextts_client
        from gradio_client import Client
        hf_token = os.environ.get("HF_TOKEN", "")
        token_path = os.path.join(os.path.dirname(__file__), ".hf_token")
        if not hf_token and os.path.exists(token_path):
            with open(token_path) as f:
                hf_token = f.read().strip()
            os.environ["HF_TOKEN"] = hf_token
        try:
            kwargs = {"verbose": False}
            _indextts_client = Client("IndexTeam/IndexTTS-2-Demo", **kwargs)
            print("        ✓ Connected to IndexTTS-2")
        except Exception as e:
            print(f"        ⚠ Could not connect to IndexTTS-2: {e!r}")
            _indextts_client = False
        return _indextts_client


def _clone_indextts(text: str, ref_audio_path: str, out_path: str,
                   target_lang: str = "en", max_retries: int = 2,
                   emotion_vec: int = None, emotion_weight: float = None) -> bool:
    """Generate cloned speech via IndexTTS-2 HF Space.
    Supports emotion control via 8 emotion vectors.
    Uses 'Same as the voice reference' mode (emotion inherited from ref audio).

    Emotion-aware: can override with a specific emotion vector and weight.
      emotion_vec: 1-8 (1=Happy, 2=Angry, 3=Sad, 4=Afraid, 5=Surprised,
                    6=Disgusted, 7=Excited, 8=Neutral)
      emotion_weight: 0.0-1.0, higher = more emotion
    If emotion_vec is None, uses 'Same as the voice reference' (inherits from ref).
    """
    client = _get_indextts_client()
    if not client:
        return False
    from gradio_client import handle_file
    safe_text = text.strip()
    if not safe_text:
        return False
    if len(safe_text) > 500:
        safe_text = safe_text[:500]

    # Build emotion vectors: all zeros except the selected one
    vecs = [0.0] * 8
    emo_control = "Same as the voice reference"
    emo_ref = handle_file(ref_audio_path)
    emo_w = 0.8

    if emotion_vec is not None and 1 <= emotion_vec <= 8:
        emo_control = "Use the emotion vector"
        vecs[emotion_vec - 1] = emotion_weight if emotion_weight else 0.7
        emo_w = emotion_weight if emotion_weight is not None else 0.8
        emo_ref = None  # Not used when using emotion vectors

    for attempt in range(max_retries):
        try:
            if emo_control == "Use the emotion vector":
                result = client.predict(
                    emo_control_method=emo_control,
                    prompt=handle_file(ref_audio_path),
                    text=safe_text,
                    emo_ref_path=handle_file(ref_audio_path),
                    emo_weight=emo_w,
                    vec1=vecs[0], vec2=vecs[1], vec3=vecs[2], vec4=vecs[3],
                    vec5=vecs[4], vec6=vecs[5], vec7=vecs[6], vec8=vecs[7],
                    emo_text="", emo_random=False,
                    max_text_tokens_per_segment=120,
                    api_name="/gen_single",
                )
            else:
                result = client.predict(
                    emo_control_method=emo_control,
                    prompt=handle_file(ref_audio_path),
                    text=safe_text,
                    emo_ref_path=handle_file(ref_audio_path),
                    emo_weight=emo_w,
                    vec1=vecs[0], vec2=vecs[1], vec3=vecs[2], vec4=vecs[3],
                    vec5=vecs[4], vec6=vecs[5], vec7=vecs[6], vec8=vecs[7],
                    emo_text="", emo_random=False,
                    max_text_tokens_per_segment=120,
                    api_name="/gen_single",
                )
            if isinstance(result, dict):
                result = result.get('value', result)
            elif isinstance(result, (tuple, list)):
                result = result[0] if result else None
            if result and os.path.exists(result) and os.path.getsize(result) > 0:
                subprocess.run([
                    "ffmpeg", "-y", "-i", result,
                    "-vn", "-ac", "2", "-ar", "48000",
                    "-c:a", "libmp3lame", out_path
                ], capture_output=True, text=True)
                if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    return True
        except Exception as e:
            msg = str(e).lower()
            if "quota" in msg or "zerogpu" in msg:
                print(f"        ⚠ IndexTTS-2 ZeroGPU quota exhausted")
                return False
            if attempt < max_retries - 1:
                time.sleep((attempt + 1) * 3)
    return False


def _clone_hf(text: str, ref_audio_path: str, out_path: str,
              max_retries: int = 2, target_lang: str = "en") -> bool:
    """Generate speech via HuggingFace XTTS Gradio spaces.
    Primary: hasanbasbunar/Voice-Cloning-XTTS-v2 (multi-language, free GPU)
    Fallback: tonyassi/voice-clone (English-only)"""
    from gradio_client import handle_file
    clients = _get_hf_clients()
    if not clients:
        return False
    if len(text.strip()) > 500:
        text = text.strip()[:500]

    # Convert lang code to XTTS language name
    xtts_lang_name = _XTTS_LANG_NAMES.get(target_lang, "English")

    for attempt in range(max_retries):
        for client, api_name in clients:
            try:
                if api_name == "/voice_clone_synthesis":
                    # hasanbasbunar space: needs URL or file, language param
                    result = client.predict(
                        text=text,
                        reference_audio_url=handle_file(ref_audio_path),
                        example_audio_name=None,
                        language=xtts_lang_name,
                        api_name=api_name,
                    )
                else:
                    # tonyassi space: simpler API
                    result = client.predict(
                        text=text,
                        audio=handle_file(ref_audio_path),
                        api_name=api_name,
                    )
                if result and os.path.exists(result) and os.path.getsize(result) > 0:
                    subprocess.run([
                        "ffmpeg", "-y", "-i", result,
                        "-vn", "-ac", "2", "-ar", "48000",
                        "-c:a", "libmp3lame", out_path
                    ], capture_output=True, text=True)
                    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                        return True
            except Exception as e:
                msg = str(e).lower()
                if "quota" in msg or "zerogpu" in msg:
                    print(f"        ⚠ HF ZeroGPU quota exhausted on this space")
                    continue  # try next space
                if "not in the list" in msg:
                    print(f"        ⚠ Language {xtts_lang_name} not supported by this space, trying next")
                    continue
                if attempt < max_retries - 1:
                    time.sleep((attempt + 1) * 3)
    return False


# Track which cloning backend succeeded first — use it for ALL clips
# to maintain voice consistency throughout the video.
_cloning_backend_locked = None  # None = not locked yet
_cloning_backend_lock = threading.Lock()


def reset_cloning_backend():
    """Reset the locked cloning backend. Call at the start of each new dubbing job."""
    global _cloning_backend_locked
    with _cloning_backend_lock:
        _cloning_backend_locked = None


def clone_voice_tts(text: str, ref_audio_path: str, out_path: str,
                     max_retries: int = 3, xtts_lang: str = "en",
                     target_lang: str = "en", edge_voice: str = None,
                     emotion_profile=None) -> bool:
    """Generate cloned speech using edge-tts + OpenVoice V2 tone conversion.

    SINGLE BACKEND — no fallback chain that changes voice mid-video.

    Pipeline:
      1. edge-tts generates base audio in target language (unlimited, no quota)
      2. OpenVoice V2 converts tone color to match original speaker's voice
         (runs locally on CPU, ~300MB, no quota, consistent)

    This is the YouTube auto-dubber approach: high-quality neural TTS (Microsoft
    Azure) + lightweight voice conversion. Voice stays consistent across the
    entire video because it's always the same two-step pipeline.

    If OpenVoice conversion fails, we still output the edge-tts audio (without
    tone conversion) — still consistent, just less voice-matched.

    Returns True on success.
    """
    if not ref_audio_path or not os.path.exists(ref_audio_path):
        return False
    if not edge_voice:
        return False

    safe_text = text.strip()
    if not safe_text:
        return False
    if len(safe_text) > 500:
        safe_text = safe_text[:500]

    import asyncio
    import edge_tts
    import tempfile
    import shutil

    tmpdir = tempfile.mkdtemp(prefix="voicestep_")
    try:
        # Step 1: Generate base TTS with edge-tts
        base_mp3 = os.path.join(tmpdir, "base.mp3")
        base_wav = os.path.join(tmpdir, "base.wav")

        for attempt in range(max_retries):
            try:
                communicate = edge_tts.Communicate(safe_text, edge_voice)
                asyncio.run(communicate.save(base_mp3))
                if os.path.exists(base_mp3) and os.path.getsize(base_mp3) > 0:
                    break
                raise RuntimeError("edge-tts generated empty file")
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep((attempt + 1) * 2)
                else:
                    print(f"        ⚠ edge-tts failed after {max_retries} retries: {e!r}")
                    return False

        # Convert base mp3 to wav for OpenVoice
        subprocess.run([
            "ffmpeg", "-y", "-i", base_mp3,
            "-vn", "-ac", "1", "-ar", "22050",
            "-c:a", "pcm_s16le", base_wav
        ], capture_output=True, text=True)

        if not os.path.exists(base_wav) or os.path.getsize(base_wav) == 0:
            # Fallback: just copy the mp3 to output
            subprocess.run([
                "ffmpeg", "-y", "-i", base_mp3,
                "-vn", "-ac", "2", "-ar", "48000",
                "-c:a", "libmp3lame", out_path
            ], capture_output=True, text=True)
            return os.path.exists(out_path) and os.path.getsize(out_path) > 0

        # Step 2: OpenVoice V2 tone conversion
        conv = _get_openvoice()
        if conv is None:
            # OpenVoice not available — output edge-tts as-is (still consistent)
            subprocess.run([
                "ffmpeg", "-y", "-i", base_mp3,
                "-vn", "-ac", "2", "-ar", "48000",
                "-c:a", "libmp3lame", out_path
            ], capture_output=True, text=True)
            return os.path.exists(out_path) and os.path.getsize(out_path) > 0

        try:
            # Extract speaker embeddings
            src_se = conv.extract_se(base_wav)
            tgt_se = conv.extract_se(ref_audio_path)

            # Convert tone color — tau controls how much of the target voice
            # character is applied. 0.3 = subtle (natural), 1.0 = strong clone.
            # For cartoon/anime dubbing, 0.3-0.5 sounds natural without artifacts.
            converted_wav = os.path.join(tmpdir, "converted.wav")
            conv.convert(
                audio_src_path=base_wav,
                src_se=src_se,
                tgt_se=tgt_se,
                output_path=converted_wav,
                tau=0.3,
            )

            if os.path.exists(converted_wav) and os.path.getsize(converted_wav) > 0:
                subprocess.run([
                    "ffmpeg", "-y", "-i", converted_wav,
                    "-vn", "-ac", "2", "-ar", "48000",
                    "-c:a", "libmp3lame", out_path
                ], capture_output=True, text=True)
                if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    return True
        except Exception as e:
            print(f"        ⚠ OpenVoice tone conversion failed: {e!r}")

        # Fallback: output edge-tts audio without tone conversion
        subprocess.run([
            "ffmpeg", "-y", "-i", base_mp3,
            "-vn", "-ac", "2", "-ar", "48000",
            "-c:a", "libmp3lame", out_path
        ], capture_output=True, text=True)
        return os.path.exists(out_path) and os.path.getsize(out_path) > 0

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def generate_tts_segments(segments: list, target_lang: str,
                                 voice: str, temp_dir: str,
                                 job_dir: str = None,
                                 progress_callback=None,
                                 speaker_voices: dict = None,
                                 use_voice_cloning: bool = False,
                                 speaker_ref_audios: dict = None,
                                 emotion_profiles: list = None) -> list:
    """Generate TTS audio clip for each translated segment.
    Supports per-clip checkpointing: if job_dir is provided, already-generated
    clips are skipped on resume.

    Multi-speaker: if segments have 'speaker' field and speaker_voices is
    provided (mapping speaker_id -> voice_name), each clip uses its speaker's
    assigned voice. Otherwise, uses the global 'voice' parameter.

    Voice cloning: if use_voice_cloning=True and speaker_ref_audios is provided
    (mapping speaker_id -> reference_audio_path), uses HuggingFace XTTS to clone
    the original speaker's voice. Falls back to edge-tts if cloning fails.

    progress_callback(done, total) called per clip."""
    import edge_tts

    # Determine voice mode
    multi_speaker = speaker_voices and any("speaker" in s for s in segments)
    # Check if Kokoro is available for this language
    _kokoro_active = False
    try:
        import kokoro_tts
        _kokoro_active = kokoro_tts.is_supported(target_lang)
    except ImportError:
        pass

    if use_voice_cloning:
        mode_str = "voice cloning (original speaker voice)"
    elif _kokoro_active:
        mode_str = "Kokoro TTS (SOTA quality)"
    else:
        mode_str = "Edge-TTS"
    if multi_speaker:
        speakers_list = sorted(set(s["speaker"] for s in segments if "speaker" in s))
        print(f"  [4/5] Generating multi-speaker voice with {mode_str}...")
        for spk in speakers_list:
            v = speaker_voices.get(spk, voice) if not use_voice_cloning else f"cloned from speaker {spk}"
            print(f"        Speaker {spk} → voice: {v}")
    else:
        print(f"  [4/5] Generating voice with {mode_str} (voice: {voice})...")

    tts_segments = []

    def get_voice_for_seg(seg):
        """Get the voice for a segment (multi-speaker or single)."""
        if multi_speaker and "speaker" in seg:
            return speaker_voices.get(seg["speaker"], voice)
        return voice

    def get_ref_audio_for_seg(seg):
        """Get the reference audio path for voice cloning."""
        if not speaker_ref_audios:
            return None
        spk = seg.get("speaker", 0)
        return speaker_ref_audios.get(spk) or speaker_ref_audios.get(0)

    async def synth_one_edge(idx: int, text: str, out_path: str, seg_voice: str,
                            target_duration: float = None):
        """Synthesize using edge-tts at NORMAL speed.
        Speed adjustment is done later in build_dubbed_audio (single atempo pass)
        to avoid double speed adjustment and extreme slowdowns."""
        # Skip if already generated (resume support)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return
        max_retries = 3

        # Generate at normal speed — no rate pre-adjustment
        # The lip-sync speed adjustment in build_dubbed_audio handles timing
        for attempt in range(max_retries):
            try:
                communicate = edge_tts.Communicate(text, seg_voice)
                await communicate.save(out_path)
                if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    return
                raise RuntimeError("TTS generated empty file")
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    print(f"        TTS retry {attempt + 1}/{max_retries} for clip {idx} (waiting {wait_time}s): {e}")
                    await asyncio.sleep(wait_time)
                else:
                    print(f"        ⚠ TTS failed for clip {idx} after {max_retries} retries, using silence: {e}")
                    silence_cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i",
                                   "anullsrc=r=48000:cl=mono", "-t", "0.3",
                                   "-c:a", "libmp3lame", out_path]
                    subprocess.run(silence_cmd, capture_output=True)
                    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                        return
                    raise RuntimeError(f"TTS failed for clip {idx}: {e}")

    # Use the top-level XTTS language map. If the target language is not
    # supported by XTTS, voice cloning for that language is skipped and we
    # fall back to edge-tts for every clip.
    xtts_lang = _xtts_lang_for(target_lang)

    def synth_one_clone(idx: int, text: str, out_path: str, ref_path: str,
                        seg_voice: str, emotion_profile=None) -> bool:
        """Synthesize using voice cloning. Returns True on success.
        Emotion-aware: passes emotion profile to voice cloning engines."""
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return True
        if not ref_path or not os.path.exists(ref_path):
            return False
        return clone_voice_tts(text, ref_path, out_path, max_retries=3,
                               xtts_lang=xtts_lang,
                               target_lang=target_lang,
                               edge_voice=seg_voice,
                               emotion_profile=emotion_profile)

    async def synth_one(idx: int, text: str, out_path: str, seg_voice: str,
                       ref_path: str = None, target_duration: float = None,
                       use_kokoro: bool = True, emotion_profile=None):
        """Main synthesis dispatcher.

        Priority order (non-voice-cloning mode):
          1. Kokoro TTS — SOTA quality, 82M params, 10+ languages
          2. Edge-TTS — Microsoft's free TTS, 100+ languages (fallback)

        Priority order (voice-cloning mode):
          1. Voice cloning (edge-tts + OpenVoice V2 tone conversion)
          2. Edge-TTS — if voice cloning fails (still same voice, no tone match)

        Voice consistency: voice cloning always uses the same pipeline
        (edge-tts → OpenVoice), so the voice never changes mid-video.
        """
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return

        # --- Voice cloning mode ---
        if use_voice_cloning and ref_path:
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                None, synth_one_clone, idx, text, out_path, ref_path, seg_voice,
                emotion_profile)
            if success:
                return
            print(f"        ⚠ Voice clone failed for clip {idx}, using edge-tts (no tone match)")

        # --- Kokoro TTS (primary for non-cloning, fallback for cloning) ---
        if use_kokoro and not use_voice_cloning:
            try:
                kokoro_ok = await loop_run_kokoro(
                    text, out_path, target_lang, seg_voice, target_duration)
                if kokoro_ok:
                    return
            except Exception as e:
                print(f"        ⚠ Kokoro TTS failed for clip {idx}: {e!r}")

        # --- Edge-TTS (final fallback) ---
        await synth_one_edge(idx, text, out_path, seg_voice, target_duration)

    # Create all tasks
    for i, seg in enumerate(segments):
        out_path = os.path.join(temp_dir, f"tts_{i:05d}.mp3")
        seg_voice = get_voice_for_seg(seg)
        tts_segments.append({
            **seg,
            "audio_path": out_path,
            "voice": seg_voice,  # store which voice was used
        })

    # Count already-generated clips
    existing = sum(1 for ts in tts_segments if os.path.exists(ts["audio_path"]) and os.path.getsize(ts["audio_path"]) > 0)
    if existing > 0:
        print(f"        Resuming: {existing}/{len(tts_segments)} clips already generated")
    if progress_callback:
        progress_callback(existing, len(tts_segments))

    # Progress tracking with a counter
    done_count = [existing]
    progress_lock = asyncio.Lock()

    # Kokoro TTS integration
    _kokoro_available = False
    try:
        import kokoro_tts
        _kokoro_available = kokoro_tts.is_supported(target_lang)
        if _kokoro_available:
            print(f"        ✓ Kokoro TTS available for '{target_lang}' — will use as primary TTS engine")
    except ImportError:
        pass

    async def loop_run_kokoro(text: str, out_path: str, lang: str,
                               edge_voice: str, target_duration: float = None) -> bool:
        """Run Kokoro TTS in a thread (it's synchronous)."""
        if not _kokoro_available:
            return False
        loop = asyncio.get_event_loop()

        # Determine Kokoro voice
        # For multi-speaker, map edge voice to kokoro voice
        kokoro_voice = None
        if multi_speaker:
            # Get speaker ID from segment and map to Kokoro voice
            spk = None
            for s in segments:
                if s.get("translated") == text:
                    spk = s.get("speaker", 0)
                    break
            if spk is not None:
                kokoro_voice = kokoro_tts.get_voice_for_speaker(
                    spk, len(speaker_voices or {}), lang)
        else:
            kokoro_voice = kokoro_tts.get_default_voice(lang)

        # Generate at normal speed — speed adjustment is done in build_dubbed_audio
        # (single atempo pass) to avoid double speed adjustment and extreme slowdowns
        speed = 1.0

        def _gen():
            return kokoro_tts.generate_speech(
                text, out_path, lang, kokoro_voice, speed=speed)
        return await loop.run_in_executor(None, _gen)

    # Voice cloning concurrency:
    #   OpenVoice V2 uses a single shared model in RAM — serial (1) to avoid
    #   model reload thrashing and OOM on low-RAM VMs.
    # Kokoro is lightweight — 2 concurrent is safe (avoids model reload thrashing)
    # Edge-TTS is very light — 10 in parallel
    if use_voice_cloning:
        # OpenVoice V2 is the only backend now — single model, serial execution
        sem_concurrency = 1  # OpenVoice V2 — serial (shared model in RAM)
    elif _kokoro_available:
        sem_concurrency = 2  # Kokoro — 2 concurrent is safe (lightweight model)
    else:
        sem_concurrency = 10  # Edge-TTS — very light, 10 in parallel
    sem = asyncio.Semaphore(sem_concurrency)

    async def run_with_progress(idx, task):
        async with sem:
            await task
            async with progress_lock:
                done_count[0] += 1
                # Update progress on every clip for Kokoro/voice-cloning (serial, slow)
                # or every 3rd clip for Edge-TTS (parallel, fast)
                update_interval = 1 if sem_concurrency <= 2 else 3
                if progress_callback and (done_count[0] % update_interval == 0 or done_count[0] == len(tts_segments)):
                    progress_callback(done_count[0], len(tts_segments))

    tasks_list = [run_with_progress(i, synth_one(i, seg["translated"], ts["audio_path"], ts["voice"],
                                                   get_ref_audio_for_seg(seg),
                                                   target_duration=(seg["end"] - seg["start"]),
                                                   use_kokoro=_kokoro_available,
                                                   emotion_profile=(emotion_profiles[i] if emotion_profiles and i < len(emotion_profiles) else None)))
             for i, (seg, ts) in enumerate(zip(segments, tts_segments))]

    await asyncio.gather(*tasks_list)

    print(f"        Generated {len(tts_segments)} voice clips")
    if multi_speaker:
        # Print speaker distribution
        spk_counts = {}
        for ts in tts_segments:
            s = ts.get("speaker", "?")
            spk_counts[s] = spk_counts.get(s, 0) + 1
        for s in sorted(spk_counts):
            print(f"        Speaker {s}: {spk_counts[s]} clips (voice: {speaker_voices.get(s, voice)})")
    if progress_callback:
        progress_callback(len(tts_segments), len(tts_segments))
    return tts_segments


# ---------------------------------------------------------------------------
# Create background track by muting speech segments (no Demucs needed)
# ---------------------------------------------------------------------------

def _create_muted_background(audio_path: str, segments: list,
                             temp_dir: str, total_duration: float) -> str:
    """Create a background audio track by muting all speech segments from
    the original audio. Keeps music, ambience, and sound effects. Removes
    original speech so it doesn't bleed through the dub.

    Uses ffmpeg's volume filter with dynamic expressions to mute speech
    time ranges. Much faster than Demucs (single ffmpeg pass).
    """
    if not segments or total_duration <= 0:
        return None

    # Build a volume filter expression that mutes speech segments
    # and keeps full volume during gaps (music/ambience)
    # ffmpeg's volume filter supports 'between(t,start,end)' expressions
    #
    # Strategy: use silencedetect-style approach — create a filter that
    # sets volume=0 during speech segments, volume=1 otherwise.
    # We do this with the 'volume' filter and a complex expression, or
    # more reliably, by generating silence segments and overlaying them.

    # Actually the simplest reliable approach: use ffmpeg's compand or
    # a series of volume=0:enable='between(t,start,end)' filters.
    # But for many segments this creates a very long filter chain.
    #
    # Better approach: generate a "mute mask" audio file (silence during
    # speech, tone during non-speech), then multiply original audio by it.

    bg_path = os.path.join(temp_dir, "bg_muted.wav")
    if os.path.exists(bg_path):
        return bg_path

    # Sort segments by start time
    sorted_segs = sorted(segments, key=lambda s: s["start"])

    # Build ffmpeg filter: for each speech segment, mute it
    # Use volume filter with enable='between(t,start,end)' to set volume=0
    # Chain multiple volume filters — each mutes one speech segment
    filter_parts = []
    for seg in sorted_segs:
        start = seg["start"]
        end = seg["end"]
        # Add small padding to catch the tail of speech
        end_padded = min(end + 0.05, total_duration)
        filter_parts.append(
            f"volume=0:enable='between(t,{start:.3f},{end_padded:.3f})'"
        )

    # If too many segments (>100), the filter chain gets unwieldy.
    # In that case, use a different approach: generate silence segments
    # and overlay them on the original audio.
    if len(filter_parts) > 100:
        # Alternative: use amix with silence overlays
        # Create a silence track that has silence during speech and
        # nothing during gaps, then subtract from original
        silence_inputs = []
        silence_filters = []
        for i, seg in enumerate(sorted_segs):
            start = seg["start"]
            end = min(seg["end"] + 0.05, total_duration)
            dur = end - start
            # Generate silence segment at the right timestamp
            silence_inputs.extend([
                "-f", "lavfi", "-t", f"{dur:.3f}", "-i",
                f"anullsrc=r=48000:cl=stereo"
            ])
            # Delay it to the right position
            delay_ms = int(start * 1000)
            silence_filters.append(
                f"[{i}:a]adelay={delay_ms}|{delay_ms}[s{i}]"
            )
        amix_inputs = "".join(f"[s{i}]" for i in range(len(sorted_segs)))
        filter_complex = (
            ";".join(silence_filters) +
            f";[{-1}]volume=1.0[orig];"  # original at index -1 (last input = original audio)
            f"{amix_inputs}amix=inputs={len(sorted_segs)}:duration=longest:normalize=0[silence_sum];"
            f"[orig][silence_sum]amix=inputs=2:duration=first:normalize=0[a]"
        )
        # This approach is complex — fall back to simpler method below
        pass

    # Simple reliable approach: chain volume filters (works up to ~100 segments)
    # For >100, we still chain but ffmpeg handles it fine (just a long filter string)
    filter_chain = ",".join(filter_parts)

    cmd = [
        "ffmpeg", "-y", "-i", audio_path,
        "-af", filter_chain,
        "-ac", "2", "-ar", "48000", "-sample_fmt", "s16",
        "-t", f"{total_duration:.2f}",
        bg_path
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=max(120, int(total_duration * 1.5)))
        if r.returncode == 0 and os.path.exists(bg_path) and os.path.getsize(bg_path) > 0:
            return bg_path
        else:
            print(f"        ⚠ Muted background creation failed: {r.stderr[-200:] if r.stderr else 'unknown'}")
            return None
    except subprocess.TimeoutExpired:
        print(f"        ⚠ Muted background creation timed out")
        return None


# ---------------------------------------------------------------------------
# Step 5: Get duration of each TTS clip and build the final audio track
# ---------------------------------------------------------------------------

def get_audio_duration(path: str) -> float:
    """Get duration of audio file in seconds using ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True
    )
    try:
        return float(result.stdout.strip())
    except (ValueError, IndexError):
        return 0.0


def atempo_filter(duration: float, target_duration: float) -> str:
    """
    Calculate atempo chain to fit audio into target duration.
    atempo accepts 0.5x to 100x per filter, chain for extreme values.
    """
    if duration <= 0:
        return ""
    ratio = duration / target_duration
    if ratio < 0.5:
        # need to slow down more than 2x — chain atempo
        first = 0.5
        remaining = ratio / first
        # remaining will be > 1.0, meaning we still need to slow down
        # Actually: ratio < 0.5 means audio is SHORTER than target -> slow down
        # atempo=0.5 slows to half speed (doubles duration)
        # We want final_duration = duration / atempo_value
        # So atempo_value = duration / target = ratio
        # If ratio = 0.3, we need atempo=0.3 which is below 0.5 min
        # Chain: 0.5, then 0.6 (0.5*0.6=0.3)
        filters = [0.5]
        remaining = ratio / 0.5
        while remaining < 0.5:
            filters.append(0.5)
            remaining = remaining / 0.5
        filters.append(remaining)
        return ",".join(f"atempo={v}" for v in filters)
    elif ratio > 2.0:
        # audio is LONGER than target -> speed up
        filters = [2.0]
        remaining = ratio / 2.0
        while remaining > 2.0:
            filters.append(2.0)
            remaining = remaining / 2.0
        filters.append(remaining)
        return ",".join(f"atempo={v}" for v in filters)
    else:
        return f"atempo={ratio:.4f}"


def build_dubbed_audio(tts_segments: list, total_duration: float,
                        temp_dir: str, keep_bg: bool = False,
                        original_audio_path: str = None,
                        extend_video: bool = False,
                        bg_volume: float = 0.35,
                        non_speech_clips: list = None) -> tuple:
    """
    Build the final dubbed audio track by placing TTS segments at correct
    timestamps — with LIP SYNC.

    LIP SYNC MODE (default): Each TTS clip is speed-adjusted to fit exactly
    within its original speech segment's time slot. This means:
      - The dubbed audio starts at the exact same moment the original speaker
        started talking → lip movements match audio
      - The dubbed audio ends at the exact same moment → no overlap with next
      - Speed adjustment is kept within 0.7x-1.5x to avoid distortion
      - If the clip is slightly too long, it's trimmed (last few words may be cut)
      - If the clip is slightly too short, silence is padded at the end

    NON-SPEECH PRESERVATION: If non_speech_clips is provided, these clips
    (laughs, sighs, reactions) are mixed into the dubbed track at their
    original timestamps. This makes the dub feel natural.

    BACKGROUND MUSIC: If keep_bg and original_audio_path are provided,
    background music is mixed with sidechain ducking (music dips during
    speech, comes back up during pauses).

    Returns: (path to the final mixed audio WAV, list of video_shift_points)
    """
    print(f"  [5/5] Building dubbed audio track...")

    # Filter to only clips that exist and are non-empty
    valid_clips = []
    for i, seg in enumerate(tts_segments):
        clip_path = seg["audio_path"]
        if os.path.exists(clip_path) and os.path.getsize(clip_path) > 0:
            clip_dur = get_audio_duration(clip_path)
            if clip_dur > 0:
                valid_clips.append((seg, clip_path, clip_dur))

    if not valid_clips:
        raise RuntimeError("No TTS clips were generated successfully.")

    print(f"        {len(valid_clips)}/{len(tts_segments)} valid clips to mix")

    # ===================================================================
    # LIP SYNC MODE: Speed-adjust each clip to fit its original time slot
    # ===================================================================
    # This is how professional dubbing works — the dubbed voice starts at
    # the exact same moment the original speaker's lips move, and ends when
    # they stop. Speed is adjusted (within 0.7x-1.5x) to fit the time slot.
    # If the clip is still too long after max speedup, it's trimmed.
    # If the clip is shorter than the slot, silence is padded at the end.
    #
    # This replaces the old "extend video" approach which pushed clips later
    # and froze video frames — causing audio/video desync.
    # ===================================================================
    adjusted_clips = []
    speed_adjusted = 0
    truncated = 0
    padded = 0
    
    for ci, (seg, clip_path, clip_dur) in enumerate(valid_clips):
        slot_duration = seg["end"] - seg["start"]
        
        # Calculate available time slot (don't overlap with next segment)
        if ci + 1 < len(valid_clips):
            next_start = valid_clips[ci + 1][0]["start"]
        else:
            next_start = seg["end"] + 1.0
        max_dur = max(next_start - seg["start"] - 0.03, 0.2)  # 30ms gap

        idx = seg.get("audio_path", "").split("_")[-1].replace(".mp3", "")
        adj_path = os.path.join(temp_dir, f"adj_{idx}.mp3")

        # Decide if we need speed adjustment
        # If clip is longer than slot → speed up (max 1.4x to avoid distortion)
        # If clip is shorter than slot → keep as-is (pad with silence)
        # Only slow down for very short clips (avoid unnatural slow speech)
        need_speedup = clip_dur > slot_duration * 1.10  # 10% tolerance
        need_slowdown = clip_dur < slot_duration * 0.70 and slot_duration > 1.0

        if need_speedup:
            # Speed up to fit the slot (max 1.4x speedup — 1.5x sounds too fast)
            target_dur = max(slot_duration * 0.95, min(max_dur, clip_dur / 1.2))
            ratio = clip_dur / target_dur
            if ratio > 1.4:
                ratio = 1.4  # Max 1.4x speedup — higher distorts speech
            tempo = f"atempo={ratio:.4f}"
            adj_cmd = ["ffmpeg", "-y", "-i", clip_path, "-filter:a", tempo,
                       "-vn", "-ac", "2", "-ar", "48000", adj_path]
            try:
                r = subprocess.run(adj_cmd, capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired:
                r = None
            if r is None or r.returncode != 0 or not os.path.exists(adj_path) or os.path.getsize(adj_path) == 0:
                # Fallback: no speed adjustment
                adj_cmd2 = ["ffmpeg", "-y", "-i", clip_path, "-vn",
                            "-ac", "2", "-ar", "48000", adj_path]
                try:
                    subprocess.run(adj_cmd2, capture_output=True, text=True, timeout=30)
                except subprocess.TimeoutExpired:
                    pass
            speed_adjusted += 1
        elif need_slowdown:
            # Slow down to fill the slot (min 0.8x — slower sounds unnatural)
            target_dur = slot_duration * 0.92
            ratio = clip_dur / target_dur
            if ratio < 0.8:
                ratio = 0.8  # Min 0.8x — 0.7x sounds too slow/robotic
            tempo = f"atempo={ratio:.4f}"
            adj_cmd = ["ffmpeg", "-y", "-i", clip_path, "-filter:a", tempo,
                       "-vn", "-ac", "2", "-ar", "48000", adj_path]
            try:
                subprocess.run(adj_cmd, capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired:
                pass
            speed_adjusted += 1
        else:
            # No speed adjustment needed — just convert format
            adj_cmd = ["ffmpeg", "-y", "-i", clip_path, "-vn",
                       "-ac", "2", "-ar", "48000", adj_path]
            try:
                subprocess.run(adj_cmd, capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired:
                pass

        # Check if still too long after speedup → trim
        adj_dur = get_audio_duration(adj_path) if os.path.exists(adj_path) else 0
        if adj_dur > max_dur:
            trim_path = os.path.join(temp_dir, f"trim_{idx}.mp3")
            trim_cmd = ["ffmpeg", "-y", "-i", adj_path, "-t", f"{max_dur:.3f}",
                        "-vn", "-ac", "2", "-ar", "48000", "-c:a", "libmp3lame", trim_path]
            try:
                subprocess.run(trim_cmd, capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired:
                pass
            if os.path.exists(trim_path) and os.path.getsize(trim_path) > 0:
                os.replace(trim_path, adj_path)
                truncated += 1

        # If clip is shorter than slot → pad with silence at the end
        # (so the next clip starts at the right time, maintaining sync)
        adj_dur = get_audio_duration(adj_path) if os.path.exists(adj_path) else 0
        if adj_dur > 0 and adj_dur < slot_duration - 0.1 and slot_duration > 0.5:
            pad_dur = slot_duration - adj_dur
            pad_path = os.path.join(temp_dir, f"pad_{idx}.mp3")
            pad_cmd = ["ffmpeg", "-y", "-i", adj_path,
                       "-af", f"apad=pad_dur={pad_dur:.3f}",
                       "-vn", "-ac", "2", "-ar", "48000", "-c:a", "libmp3lame", pad_path]
            try:
                r = subprocess.run(pad_cmd, capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired:
                r = None
            if r is not None and r.returncode == 0 and os.path.exists(pad_path) and os.path.getsize(pad_path) > 0:
                os.replace(pad_path, adj_path)
                padded += 1

        if os.path.exists(adj_path) and os.path.getsize(adj_path) > 0:
            adjusted_clips.append((seg, adj_path))

    print(f"        Lip-sync: {speed_adjusted} speed-adjusted, {truncated} trimmed, {padded} padded")

    if not adjusted_clips:
        raise RuntimeError("No clips could be adjusted.")

    print(f"        Adjusted {len(adjusted_clips)} clips, mixing...")

    mixed_path = os.path.join(temp_dir, "mixed_voice.wav")

    # amix handles all clips in a single ffmpeg call (fast for up to ~200 clips).
    # concat approach processes per-clip (slower but handles very long videos).
    use_concat = len(adjusted_clips) > 200

    # Compute start times for each clip — always use original timestamps
    # (lip sync: each clip starts at the original speech segment's start)
    actual_starts = []
    video_shift_points = []  # empty — no video extension needed
    for seg, adj_path in adjusted_clips:
        actual_starts.append(float(seg["start"]))

    if not use_concat:
        # amix approach: each clip gets adelay + fade in/out, then amix all at once.
        # Fades (30ms in, 20ms out) eliminate clicks/pops at clip boundaries.
        FADE_IN_MS = 30   # 30ms fade-in
        FADE_OUT_MS = 20  # 20ms fade-out
        inputs = []
        filter_parts = []
        for i, (seg, adj_path) in enumerate(adjusted_clips):
            inputs.extend(["-i", adj_path])
            delay_ms = int(actual_starts[i] * 1000)
            # Get clip duration for fade-out calculation
            clip_dur_s = get_audio_duration(adj_path)
            fade_out_start_s = max(clip_dur_s - FADE_OUT_MS / 1000.0, 0)
            # IMPORTANT: fades must be applied BEFORE adelay, because afade's
            # st= parameter is relative to the input stream's timeline. After
            # adelay, the stream has silence prepended, so st=1.98s would point
            # into the silence — killing all clips except the first.
            filter_parts.append(
                f"[{i}:a]afade=t=in:st=0:d={FADE_IN_MS}ms,"
                f"afade=t=out:st={fade_out_start_s:.4f}:d={FADE_OUT_MS}ms,"
                f"adelay={delay_ms}|{delay_ms}[d{i}]"
            )

        amix_inputs = "".join(f"[d{i}]" for i in range(len(adjusted_clips)))
        filter_complex = ";".join(filter_parts) + f";{amix_inputs}amix=inputs={len(adjusted_clips)}:duration=longest:normalize=0[a]"

        cmd = ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", filter_complex,
            "-map", "[a]", "-ac", "2", "-ar", "48000", "-sample_fmt", "s16",
            mixed_path
        ]
        mix_timeout = max(300, int(total_duration * 2))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=mix_timeout)
        except subprocess.TimeoutExpired:
            print(f"        ⚠ amix timed out after {mix_timeout}s, falling back to concat...")
            result = None

        if result is None or result.returncode != 0:
            if result:
                print(f"        amix failed, falling back to concat approach...")
            use_concat = True

    if use_concat:
        # Concat approach for long videos (200+ clips).
        # OPTIMIZED: Instead of spawning 2 ffmpeg processes per clip (silence + convert)
        # which is extremely slow for 500+ clips, we use a single ffmpeg call with
        # adelay+amix for the clips, and a single anullsrc+atrim for silence gaps.
        # This reduces 1000+ ffmpeg calls to just 1-2 calls.
        print(f"        Using optimized concat for {len(adjusted_clips)} clips...")

        # Build a single ffmpeg filter_complex that:
        # 1. Inputs each clip
        # 2. Applies fade in/out
        # 3. Delays each clip to its correct timestamp
        # 4. Mixes all clips together
        # This is the same as amix approach but we also add silence padding
        # at the end to match total_duration
        FADE_IN_MS = 30
        FADE_OUT_MS = 20
        inputs = []
        filter_parts = []
        for i, (seg, adj_path) in enumerate(adjusted_clips):
            inputs.extend(["-i", adj_path])
            delay_ms = int(actual_starts[i] * 1000)
            clip_dur_s = get_audio_duration(adj_path)
            fade_out_start_s = max(clip_dur_s - FADE_OUT_MS / 1000.0, 0)
            filter_parts.append(
                f"[{i}:a]afade=t=in:st=0:d={FADE_IN_MS}ms,"
                f"afade=t=out:st={fade_out_start_s:.4f}:d={FADE_OUT_MS}ms,"
                f"adelay={delay_ms}|{delay_ms}[d{i}]"
            )
        amix_inputs = "".join(f"[d{i}]" for i in range(len(adjusted_clips)))
        # Add padding to total_duration to ensure full length
        pad_dur = max(total_duration + 1.0, 0.1)
        filter_complex = (
            ";".join(filter_parts)
            + f";{amix_inputs}amix=inputs={len(adjusted_clips)}:duration=longest:normalize=0,"
            f"apad=whole_dur={pad_dur:.2f}[a]"
        )

        cmd = ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", filter_complex,
            "-map", "[a]", "-ac", "2", "-ar", "48000", "-sample_fmt", "s16",
            "-t", f"{total_duration:.2f}",  # trim to exact duration
            mixed_path
        ]
        # Use a generous timeout for long videos (10 min per 30 min of audio)
        mix_timeout = max(300, int(total_duration * 2))
        print(f"        Mixing {len(adjusted_clips)} clips (timeout: {mix_timeout}s)...")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=mix_timeout)
        except subprocess.TimeoutExpired:
            print(f"        ⚠ amix timed out after {mix_timeout}s, trying per-clip concat...")
            result = None

        if result is None or result.returncode != 0:
            if result:
                print(f"        amix failed ({result.stderr[-300:]}), falling back to per-clip concat...")
            # Fallback: original per-clip concat approach (slower but reliable)
            list_file = os.path.join(temp_dir, "concat_list.txt")
            with open(list_file, "w") as f:
                current_pos = 0.0
                silence_idx = 0
                clip_idx = 0
                for ci, (seg, adj_path) in enumerate(adjusted_clips):
                    seg_start = actual_starts[ci]
                    if seg_start > current_pos + 0.01:
                        silence_dur = seg_start - current_pos
                        silence_path = os.path.join(temp_dir, f"silence_{silence_idx:05d}.wav")
                        silence_idx += 1
                        s_cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i",
                                 f"anullsrc=r=48000:cl=stereo", "-t", f"{silence_dur:.4f}",
                                 "-c:a", "pcm_s16le", silence_path]
                        try:
                            subprocess.run(s_cmd, capture_output=True, text=True, timeout=30)
                        except subprocess.TimeoutExpired:
                            pass
                        if os.path.exists(silence_path) and os.path.getsize(silence_path) > 0:
                            f.write(f"file '{silence_path}'\n")
                            current_pos = seg_start
                    clip_wav = os.path.join(temp_dir, f"clip_{clip_idx:05d}.wav")
                    clip_idx += 1
                    clip_dur_s = get_audio_duration(adj_path)
                    fade_out_st = max(clip_dur_s - 0.02, 0)
                    c_cmd = ["ffmpeg", "-y", "-i", adj_path, "-vn",
                             "-af", f"afade=t=in:st=0:d=0.03,afade=t=out:st={fade_out_st:.4f}:d=0.02",
                             "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", clip_wav]
                    try:
                        subprocess.run(c_cmd, capture_output=True, text=True, timeout=30)
                    except subprocess.TimeoutExpired:
                        pass
                    if os.path.exists(clip_wav) and os.path.getsize(clip_wav) > 0:
                        f.write(f"file '{clip_wav}'\n")
                        clip_dur = get_audio_duration(clip_wav)
                        current_pos = max(current_pos, seg_start) + clip_dur
                    else:
                        print(f"        ⚠ clip {clip_idx-1} failed to convert, skipping")

            cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
                   "-ac", "2", "-ar", "48000", "-sample_fmt", "s16", mixed_path]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=mix_timeout)
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"Audio mixing timed out after {mix_timeout}s (too many clips)")
            if result.returncode != 0:
                raise RuntimeError(f"Audio mixing (concat) failed:\n{result.stderr}")

    if not os.path.exists(mixed_path) or os.path.getsize(mixed_path) == 0:
        raise RuntimeError("Audio mixing produced empty output")

    # Apply professional audio processing to the voice track:
    # This chain mimics professional dubbing studio processing:
    # 1. High-pass filter (remove rumble below 80Hz)
    # 2. Low-pass filter (remove harsh highs above 15kHz)
    # 3. Noise gate (silence background between words)
    # 4. De-essing (reduce harsh sibilance around 6-8kHz)
    # 5. Gentle compression (even out volume, -18dB threshold, 3:1 ratio)
    # 6. EQ: warmth (200Hz), presence (3kHz), air (10kHz)
    # 7. Subtle room reverb (makes TTS sound like it's in a room, more natural)
    # 8. Soft limiter to prevent clipping
    processed_voice = os.path.join(temp_dir, "voice_processed.wav")
    voice_filter = (
        "highpass=f=80,"                                                # remove sub-bass rumble
        "lowpass=f=15000,"                                              # remove harsh highs
        "agate=threshold=-35dB:range=0.01:attack=5:release=200,"       # noise gate between words
        "acompressor=threshold=-18dB:ratio=3:1:attack=5:release=80:makeup=2,"  # gentle compression
        "equalizer=f=200:width_type=q:w=1:g=2,"                        # warmth
        "equalizer=f=3000:width_type=q:w=1.5:g=3,"                     # presence/clarity
        "equalizer=f=7000:width_type=q:w=2:g=-3,"                      # de-ess
        "equalizer=f=10000:width_type=q:w=2:g=1,"                      # air (subtle high boost)
        # Subtle reverb for natural room sound (makes TTS less "dead")
        # Very short decay (0.3s) and low wet mix (15%) — just adds presence
        "aecho=in_gain=1:out_gain=0.85:delays=20:decays=0.1,"           # subtle room feel
        "alimiter=limit=0.95"                                          # prevent clipping
    )
    proc_cmd = ["ffmpeg", "-y", "-i", mixed_path,
                "-af", voice_filter,
                "-ac", "2", "-ar", "48000", "-sample_fmt", "s16",
                processed_voice]
    try:
        proc_result = subprocess.run(proc_cmd, capture_output=True, text=True,
                                     timeout=max(300, int(total_duration * 2)))
    except subprocess.TimeoutExpired:
        print(f"        ⚠ Audio processing timed out, using raw voice")
        proc_result = None
    if proc_result.returncode == 0 and os.path.exists(processed_voice) and os.path.getsize(processed_voice) > 0:
        # Replace mixed_path with processed version
        os.replace(processed_voice, mixed_path)
        print(f"        ✅ Professional audio processing applied (EQ, compression, de-ess, limiter)")
    else:
        print(f"        ⚠ Audio processing failed, using raw voice")
        if os.path.exists(processed_voice):
            os.remove(processed_voice)

    # Optionally mix with background audio from original
    if keep_bg and original_audio_path and os.path.exists(original_audio_path):
        final_path = os.path.join(temp_dir, "final_audio.wav")
        bg_path = os.path.join(temp_dir, "background.wav")
        cmd = ["ffmpeg", "-y", "-i", original_audio_path,
               "-vn", "-ac", "2", "-ar", "48000", "-sample_fmt", "s16", bg_path]
        subprocess.run(cmd, capture_output=True, text=True)

        if os.path.exists(bg_path):
            # Professional sidechain ducking mix:
            # The voice track automatically "ducks" (lowers) the background
            # music whenever speech is present. This is exactly how
            # professional radio/TV mixes work — music dips during dialogue
            # and comes back up during pauses.
            #
            # Sidechain compression approach:
            # - Background music runs through a compressor
            # - The compressor's sidechain input is the voice track
            # - When voice is present → compressor reduces bg volume
            # - When voice is silent → bg returns to full volume
            # - Attack/release smoothed for natural transitions
            #
            # Additional: EQ the background slightly to "make room" for voice
            # by scooping the 1-4kHz range where speech lives.
            duck_threshold = -20    # dB threshold for sidechain
            duck_ratio = 6           # how much to duck (6:1 = strong duck)
            duck_attack = 20          # ms — fast attack so bg ducks quickly
            duck_release = 400        # ms — slow release so bg fades back gently
            bg_base_volume = bg_volume  # base volume (0.35 for music, 0.15 for legacy)

            filter_complex = (
                # Split voice: one for sidechain control, one for final mix
                "[0:a]asplit=2[voice_sc][voice_out];"
                # Set base volume on background
                f"[1:a]volume={bg_base_volume:.2f}[bg_vol];"
                # Sidechain compress: voice controls bg ducking
                f"[bg_vol][voice_sc]sidechaincompress="
                f"threshold={duck_threshold}dB:ratio={duck_ratio}:"
                f"attack={duck_attack}:release={duck_release}[bg_ducked];"
                # Slight EQ on bg to carve space for voice (dip 2kHz)
                "[bg_ducked]equalizer=f=2000:width_type=q:w=2:g=-3[bg_eq];"
                # Mix voice + ducked bg
                "[voice_out][bg_eq]amix=inputs=2:duration=first:normalize=0[a]"
            )
            cmd = [
                "ffmpeg", "-y",
                "-i", mixed_path,        # [0] voice (processed)
                "-i", bg_path,           # [1] background music
                "-filter_complex", filter_complex,
                "-map", "[a]", "-ac", "2", "-ar", "48000", "-sample_fmt", "s16",
                final_path
            ]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True,
                                        timeout=max(300, int(total_duration * 2)))
            except subprocess.TimeoutExpired:
                print(f"        ⚠ Sidechain mix timed out, trying simple ducking...")
                result = None
            if result is not None and result.returncode != 0:
                # Fallback: simpler sidechain approach
                print(f"        ⚠ Sidechain filter failed, trying simple ducking...")
                filter_simple = (
                    f"[1:a]volume={bg_volume:.2f},apad=whole_dur=90000[bg];"
                    "[0:a]volume=1.0[voice];"
                    "[voice][bg]amix=inputs=2:duration=first:normalize=0[a]"
                )
                cmd = ["ffmpeg", "-y", "-i", mixed_path, "-i", bg_path,
                       "-filter_complex", filter_simple,
                       "-map", "[a]", "-ac", "2", "-ar", "48000", "-sample_fmt", "s16",
                       final_path]
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True,
                                            timeout=max(300, int(total_duration * 2)))
                except subprocess.TimeoutExpired:
                    print(f"        ⚠ Simple ducking also timed out")
                    result = None
            if result is not None and result.returncode == 0 and os.path.exists(final_path):
                # Clean up intermediate files to save disk on long videos
                for p in [mixed_path, bg_path]:
                    if os.path.exists(p):
                        os.remove(p)
                # Mix non-speech sounds (laughs, sighs, reactions)
                if non_speech_clips:
                    ns_output = os.path.join(temp_dir, "with_nonspeech.wav")
                    final_with_ns = mix_non_speech_into_dub(
                        final_path, non_speech_clips, ns_output, volume=0.7)
                    if final_with_ns != final_path and os.path.exists(final_with_ns):
                        os.replace(final_with_ns, final_path)
                    print(f"        ✅ Non-speech sounds (laughs, sighs) preserved in dub")
                return (final_path, video_shift_points)

        return (mixed_path, video_shift_points)
    else:
        # Clean up intermediate adjusted clips to save disk on long videos
        # (the mixed audio is the final output, adjusted clips no longer needed)
        for i, (seg, adj_path) in enumerate(adjusted_clips):
            if os.path.exists(adj_path):
                try:
                    os.remove(adj_path)
                except Exception:
                    pass
        # Clean up silence files
        for f in os.listdir(temp_dir):
            if f.startswith("silence_") and f.endswith(".wav"):
                try:
                    os.remove(os.path.join(temp_dir, f))
                except Exception:
                    pass
        # Mix non-speech sounds (laughs, sighs, reactions)
        if non_speech_clips:
            ns_output = os.path.join(temp_dir, "with_nonspeech_no_bg.wav")
            final_with_ns = mix_non_speech_into_dub(
                mixed_path, non_speech_clips, ns_output, volume=0.7)
            if final_with_ns != mixed_path and os.path.exists(final_with_ns):
                os.replace(final_with_ns, mixed_path)
            print(f"        ✅ Non-speech sounds (laughs, sighs) preserved in dub")
        return (mixed_path, video_shift_points)


# ---------------------------------------------------------------------------
# Anti-copyright video transformation
# ---------------------------------------------------------------------------

def build_anti_copyright_filter(lang_name: str = "Hindi") -> str:
    """Build an ffmpeg -vf filter chain that visually transforms the video
    enough to defeat YouTube Content ID fingerprinting, while remaining
    visually nearly identical to a human viewer.

    Techniques (researched from GitHub repos & community, all subtle, combined):
      1. Horizontal mirror flip — Content ID checks spatial fingerprint;
         mirroring breaks it while viewers barely notice for most content.
      2. Slight zoom-in (1.04x) — crops ~4% off edges, changes pixel
         positions of every frame.
      3. Color grade shift — tiny hue rotation + saturation/brightness
         nudge changes the color fingerprint.
      4. Subtle frame rate change (handled via -r flag, not filter).
      5. Light unsharp mask — adds micro-detail that changes the
         spatial-frequency fingerprint.
      6. Film grain overlay — adds random noise that changes every frame's
         pixel hash, defeating per-frame fingerprinting.
      7. Vignette — subtle darkening at edges changes spatial fingerprint
         and is barely noticeable.
      8. Small "Dubbed in <lang>" text in bottom-right corner — adds new
         visual content that Content ID can't match, and signals
         transformative use to YouTube reviewers.

    Sources: video_copyright_bypass (GitHub), tingplenting/ffmpeg_tuts gist,
    community testing — these techniques together shift enough fingerprint
    dimensions (spatial, color, temporal, frequency) that Content ID won't match.
    """
    # Escape text for ffmpeg drawtext (escape colons and single quotes)
    safe_text = f"Dubbed in {lang_name}".replace(":", r"\:").replace("'", r"'\''")
    return (
        "hflip,"                                    # 1. Mirror
        "scale=trunc(iw*1.04/2)*2:trunc(ih*1.04/2)*2,crop=trunc(iw/1.04/2)*2:trunc(ih/1.04/2)*2,"
                                                    # 2. Zoom 1.04x then crop back
        "hue=h=10:s=6:b=-2,"                         # 3. Hue +10°, sat +6%, bright -2
        "unsharp=3:3:0.6:3:3:0.0,"                   # 5. Mild luma sharpen
        "noise=alls=6:allf=t+u,"                     # 6. Film grain (temporal+uniform)
        "vignette=PI/5,"                             # 7. Subtle vignette
        f"drawtext=text='{safe_text}':"             # 8. Dubbed-in watermark
        f"fontsize=20:fontcolor=white@0.6:"
        f"x=w-tw-15:y=h-th-15:"
        f"shadowcolor=black@0.5:shadowx=1:shadowy=1,"
        "format=yuv420p"                             # Ensure compatibility
    )


def build_audio_anti_copyright_filter() -> str:
    """Build an ffmpeg -af filter chain that slightly alters the audio
    fingerprint without perceptible change.

    Techniques (researched from GitHub repos & community):
      1. Subtle pitch shift (asetrate 1.003x) — changes audio fingerprint.
      2. Slight tempo correction to keep duration same.
      3. High-pass filter at 20Hz — removes inaudible sub-bass that
         Content ID may fingerprint.
      4. Low-pass at 18kHz — removes inaudible ultra-high freq fingerprints.
      5. Channel phase manipulation — subtle stereo channel swap + phase
         inversion changes the stereo fingerprint (from tingplenting gist).
      6. Micro white noise — adds barely-audible noise floor that changes
         the audio waveform hash.
    """
    return (
        "asetrate=44100*1.003,atempo=0.997,"         # 1. Pitch +0.3%, tempo back
        "highpass=f=20,"                             # 3. Remove sub-bass
        "lowpass=f=18000,"                            # 4. Remove ultra-high
        "pan=stereo|c0<c0+0.02*c1|c1<0.02*c0+c1,"    # 5. Subtle channel bleed
        "anoisesrc=0.0001:color=white"                # 6. Micro noise floor
    )


# ---------------------------------------------------------------------------
# Step 6: Mux dubbed audio with original video
# ---------------------------------------------------------------------------

def mux_video_audio(video_path: str, audio_path: str, output_path: str,
                    burn_subtitles: bool = False, srt_path: str = None,
                    extend_video: bool = False,
                    video_shift_points: list = None,
                    anti_copyright: bool = False,
                    blur_original_subtitles: bool = False,
                    target_lang: str = "hi") -> float:
    """Replace video's audio with dubbed audio, optionally burn subtitles.
    
    If extend_video=True: extends the video to match the audio duration.
    If video_shift_points is provided: inserts freeze-frames at each shift
    point (list of (timestamp, freeze_duration) tuples) so the video
    freezes at the right moments instead of just extending the last frame.
    Otherwise: freeze-frames the last frame to match audio duration.

    If anti_copyright=True: applies visual and audio transformations
    (mirror, zoom, color shift, pitch shift) to defeat YouTube Content ID
    fingerprinting. The video looks nearly identical to humans but has a
    different fingerprint from the original.
    Returns the duration of the output file."""
    print(f"  [final] Muxing video with dubbed audio...")

    # Get video and audio durations (needed early for timeout calc + anti-copyright)
    video_dur_result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, timeout=30
    )
    video_dur = float(video_dur_result.stdout.strip() or "0")
    
    audio_dur_result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True, timeout=30
    )
    audio_dur = float(audio_dur_result.stdout.strip() or "0")
    
    # Calculate generous timeout for mux (video encoding is slow on CPU)
    mux_timeout = max(600, int(max(video_dur, audio_dur) * 10))

    # --- Blur original hardcoded subtitles ---
    # If enabled, detect and blur any existing burned-in subtitles before
    # any other processing. This way our own burned subtitles (if any) will
    # be clean, and the old ones won't show through.
    if blur_original_subtitles:
        try:
            from subtitle_blur import detect_and_blur_subtitles
            print(f"        🔍 Detecting and blurring original subtitles...")
            blurred_video = output_path.replace(".mp4", "_subblur.mp4")
            blurred_result = detect_and_blur_subtitles(
                video_path, blurred_video, work_dir=os.path.dirname(output_path))
            if blurred_result and os.path.exists(blurred_result):
                print(f"        ✅ Original subtitles blurred")
                video_path = blurred_result
                # Re-measure video duration
                video_dur_result = subprocess.run(
                    ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", video_path],
                    capture_output=True, text=True, timeout=30
                )
                video_dur = float(video_dur_result.stdout.strip() or "0")
            else:
                print(f"        ℹ No hardcoded subtitles found to blur")
        except Exception as e:
            print(f"        ⚠ Subtitle blur failed ({e}), continuing with original video")

    # --- Anti-copyright pre-processing ---
    # If enabled, create a transformed intermediate video first, then
    # use it as the source for the rest of the mux pipeline. This way all
    # the existing tpad/extend/subtitle logic works unchanged.
    if anti_copyright:
        print(f"        🔒 Applying anti-copyright video transformation...")
        anti_filter = build_anti_copyright_filter(LANG_NAMES.get(target_lang, target_lang.title()))
        anti_audio_filter = build_audio_anti_copyright_filter()
        transformed_video = output_path.replace(".mp4", "_transformed.mp4")
        # Transform video (no audio — we'll add dubbed audio later)
        anti_cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", anti_filter,
            "-an",
            "-c:v", "libx264", "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-r", "24",
            "-map_metadata", "-1",          # Strip ALL metadata (fingerprint)
            "-metadata", "title=",           # Clear title
            "-metadata", "artist=",          # Clear artist
            "-metadata", "comment=",          # Clear comment
            "-metadata", "encoder=",          # Clear encoder
            transformed_video
        ]
        result = subprocess.run(anti_cmd, capture_output=True, text=True, timeout=mux_timeout)
        if result.returncode == 0 and os.path.exists(transformed_video) and os.path.getsize(transformed_video) > 0:
            print(f"        ✅ Video transformed (anti-copyright filters applied)")
            video_path = transformed_video
            # Re-measure video duration (might differ slightly from filters)
            video_dur_result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", video_path],
                capture_output=True, text=True, timeout=30
            )
            video_dur = float(video_dur_result.stdout.strip() or "0")
            # Also apply audio anti-copyright filter to dubbed audio
            transformed_audio = audio_path.replace(".wav", "_ac.wav") if audio_path.endswith(".wav") else audio_path + "_ac.wav"
            anti_audio_cmd = [
                "ffmpeg", "-y", "-i", audio_path,
                "-af", anti_audio_filter,
                "-c:a", "pcm_s16le",
                transformed_audio
            ]
            result_a = subprocess.run(anti_audio_cmd, capture_output=True, text=True, timeout=300)
            if result_a.returncode == 0 and os.path.exists(transformed_audio) and os.path.getsize(transformed_audio) > 0:
                audio_path = transformed_audio
                audio_dur_result = subprocess.run(
                    ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
                    capture_output=True, text=True, timeout=30
                )
                audio_dur = float(audio_dur_result.stdout.strip() or "0")
        else:
            print(f"        ⚠ Anti-copyright transform failed, using original video: {result.stderr[-200:]}")

    # Re-measure durations if video was transformed (path may have changed)
    if anti_copyright and 'transformed_video' in dir() and os.path.exists(video_path) and video_path != video_path:
        pass  # Already updated above
    
    need_extend = extend_video and audio_dur > video_dur + 0.1
    
    if need_extend:
        extra_dur = audio_dur - video_dur
        print(f"        Video: {video_dur:.1f}s, Audio: {audio_dur:.1f}s → extending video by {extra_dur:.1f}s")
        
        # If we have shift points, build a video with freeze-frames at each
        # gap point. Otherwise, just freeze the last frame (simple tpad).
        if video_shift_points:
            # Build extended video using concat: for each shift point, 
            # extract the frame at that timestamp, create a freeze-frame
            # segment, and concat everything together.
            # This gives a natural look — video pauses where the audio 
            # was extended.
            print(f"        Building video with {len(video_shift_points)} freeze-frame points...")
            
            temp_vdir = tempfile.mkdtemp(prefix="video_extend_")
            try:
                # Build a complex filter that:
                # 1. Splits the video at each shift point
                # 2. Inserts freeze frames at each point
                # 3. Concats everything back
                #
                # Using FFmpeg's setpts + tpad approach for each segment
                # 
                # Simpler approach: use setpts to shift video timestamps
                # and tpad to add freeze frames at each gap
                
                # Sort shift points by timestamp
                sorted_shifts = sorted(video_shift_points, key=lambda x: x[0])
                
                # Build filter: for each shift point, freeze the frame at
                # that timestamp for the shift duration
                # We use the trim + tpad + concat approach
                #
                # Actually, the simplest reliable approach:
                # 1. Extract the video into segments at shift points
                # 2. After each segment, add a freeze frame for the shift duration
                # 3. Concat all segments + freeze frames
                
                segments = []
                prev_ts = 0.0
                for ts, freeze_dur in sorted_shifts:
                    if ts > prev_ts:
                        segments.append(("video", prev_ts, ts))
                    if freeze_dur > 0.01:
                        segments.append(("freeze", ts, freeze_dur))
                    prev_ts = ts
                
                # Add remaining video after last shift
                if prev_ts < video_dur:
                    segments.append(("video", prev_ts, video_dur))
                
                # If total is still less than audio, add final freeze
                total_planned = sum(s[2]-s[1] if s[0]=="video" else s[2] for s in segments)
                if total_planned < audio_dur:
                    final_freeze = audio_dur - total_planned
                    segments.append(("freeze", video_dur, final_freeze))
                
                # Build each segment as a separate file, then concat
                concat_list = os.path.join(temp_vdir, "concat.txt")
                with open(concat_list, "w") as f:
                    seg_idx = 0
                    for seg in segments:
                        seg_path = os.path.join(temp_vdir, f"seg_{seg_idx:04d}.mp4")
                        seg_idx += 1
                        if seg[0] == "video":
                            _, start, end = seg
                            # Extract this segment of the video
                            cmd = ["ffmpeg", "-y", "-i", video_path,
                                   "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
                                   "-c:v", "libx264", "-crf", "20", "-an",
                                   seg_path]
                            subprocess.run(cmd, capture_output=True, text=True)
                        else:
                            _, ts, dur = seg
                            # Extract frame at timestamp and create freeze segment
                            frame_path = os.path.join(temp_vdir, f"frame_{seg_idx:04d}.png")
                            extract_cmd = ["ffmpeg", "-y", "-i", video_path,
                                          "-ss", f"{ts:.3f}", "-frames:v", "1",
                                          frame_path]
                            subprocess.run(extract_cmd, capture_output=True, text=True)
                            if os.path.exists(frame_path):
                                cmd = ["ffmpeg", "-y", "-loop", "1", "-i", frame_path,
                                       "-t", f"{dur:.3f}", "-c:v", "libx264", "-crf", "20",
                                       "-r", "25", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                                       seg_path]
                                subprocess.run(cmd, capture_output=True, text=True)
                        
                        if os.path.exists(seg_path) and os.path.getsize(seg_path) > 0:
                            f.write(f"file '{seg_path}'\n")
                
                # Concat all segments — MUST re-encode (not -c copy) because
                # each segment has its own GOP/DTS structure and stream copy
                # produces non-monotonic DTS → player freezes after 1-2s.
                extended_video = os.path.join(temp_vdir, "extended.mp4")
                concat_cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                             "-i", concat_list,
                             "-c:v", "libx264", "-crf", "20",
                             "-pix_fmt", "yuv420p",
                             "-r", "24",
                             "-vsync", "cfr",
                             "-an", extended_video]
                result = subprocess.run(concat_cmd, capture_output=True, text=True)
                
                if result.returncode == 0 and os.path.exists(extended_video):
                    # Now mux with audio — re-encode video if burning subtitles
                    # (can't use -c:v copy with -vf subtitles)
                    if burn_subtitles and srt_path and os.path.exists(srt_path):
                        escaped_srt = srt_path.replace("'", r"'\''")
                        cmd = ["ffmpeg", "-y", "-i", extended_video, "-i", audio_path,
                               "-map", "0:v", "-map", "1:a",
                               "-c:v", "libx264", "-crf", "20",
                               "-vf", f"subtitles='{escaped_srt}'",
                               "-c:a", "aac", "-b:a", "192k",
                               output_path]
                    else:
                        cmd = ["ffmpeg", "-y", "-i", extended_video, "-i", audio_path,
                               "-map", "0:v", "-map", "1:a",
                               "-c:v", "copy",
                               "-c:a", "aac", "-b:a", "192k",
                               output_path]
                    result = subprocess.run(cmd, capture_output=True, text=True)
                    if result.returncode == 0:
                        pass  # success
                    else:
                        # Fallback: re-encode video
                        cmd = ["ffmpeg", "-y", "-i", extended_video, "-i", audio_path,
                               "-map", "0:v", "-map", "1:a",
                               "-c:v", "libx264", "-crf", "20",
                               "-c:a", "aac", "-b:a", "192k",
                               output_path]
                        result = subprocess.run(cmd, capture_output=True, text=True)
                else:
                    # Concat failed, fall back to simple tpad
                    print(f"        Segment concat failed, using simple tpad...")
                    raise RuntimeError("concat failed")
                    
            except Exception as e:
                print(f"        Per-gap freeze failed ({e!r}), using simple last-frame extend...")
                # Fall back to simple tpad
                if burn_subtitles and srt_path and os.path.exists(srt_path):
                    escaped_srt = srt_path.replace("'", r"'\''")
                    vf = f"subtitles='{escaped_srt}',tpad=stop_mode=clone:stop_duration={extra_dur:.3f}"
                    cmd = ["ffmpeg", "-y", "-i", video_path, "-i", audio_path,
                           "-map", "0:v", "-map", "1:a",
                           "-c:v", "libx264", "-crf", "20",
                           "-vf", vf,
                           "-c:a", "aac", "-b:a", "192k",
                           output_path]
                else:
                    cmd = ["ffmpeg", "-y", "-i", video_path, "-i", audio_path,
                           "-map", "0:v", "-map", "1:a",
                           "-c:v", "libx264", "-crf", "20",
                           "-vf", f"tpad=stop_mode=clone:stop_duration={extra_dur:.3f}",
                           "-c:a", "aac", "-b:a", "192k",
                           output_path]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=mux_timeout)
                if result.returncode != 0:
                    cmd_fallback = ["ffmpeg", "-y", "-i", video_path, "-i", audio_path,
                                    "-map", "0:v", "-map", "1:a",
                                    "-c:v", "libx264", "-crf", "20",
                                    "-c:a", "aac", "-b:a", "192k",
                                    output_path]
                    result = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=mux_timeout)
                    if result.returncode != 0:
                        raise RuntimeError(f"FFmpeg muxing failed:\n{result.stderr}")
        else:
            # Simple: just freeze the last frame
            if burn_subtitles and srt_path and os.path.exists(srt_path):
                escaped_srt = srt_path.replace("'", r"'\''")
                vf = f"subtitles='{escaped_srt}',tpad=stop_mode=clone:stop_duration={extra_dur:.3f}"
                cmd = ["ffmpeg", "-y", "-i", video_path, "-i", audio_path,
                       "-map", "0:v", "-map", "1:a",
                       "-c:v", "libx264", "-crf", "20",
                       "-vf", vf,
                       "-c:a", "aac", "-b:a", "192k",
                       output_path]
            else:
                cmd = ["ffmpeg", "-y", "-i", video_path, "-i", audio_path,
                       "-map", "0:v", "-map", "1:a",
                       "-c:v", "libx264", "-crf", "20",
                       "-vf", f"tpad=stop_mode=clone:stop_duration={extra_dur:.3f}",
                       "-c:a", "aac", "-b:a", "192k",
                       output_path]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=mux_timeout)
            if result.returncode != 0:
                # Fallback without tpad
                print(f"        tpad failed, trying alternate approach...")
                cmd_fallback = ["ffmpeg", "-y", "-i", video_path, "-i", audio_path,
                                "-map", "0:v", "-map", "1:a",
                                "-c:v", "libx264", "-crf", "20",
                                "-c:a", "aac", "-b:a", "192k",
                                output_path]
                result = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=mux_timeout)
                if result.returncode != 0:
                    raise RuntimeError(f"FFmpeg muxing failed:\n{result.stderr}")
    else:
        # Standard mux (audio fits within video or extend not requested)
        if burn_subtitles and srt_path and os.path.exists(srt_path):
            escaped_srt = srt_path.replace("'", r"'\''")
            vf = f"subtitles='{escaped_srt}'"
            cmd = ["ffmpeg", "-y", "-i", video_path, "-i", audio_path,
                   "-map", "0:v", "-map", "1:a",
                   "-c:v", "libx264", "-crf", "20",
                   "-vf", vf,
                   "-c:a", "aac", "-b:a", "192k",
                   output_path]
        else:
            cmd = ["ffmpeg", "-y", "-i", video_path, "-i", audio_path,
                   "-map", "0:v", "-map", "1:a",
                   "-c:v", "copy",
                   "-c:a", "aac", "-b:a", "192k",
                   output_path]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=mux_timeout)
        if result.returncode != 0:
            cmd_fallback = ["ffmpeg", "-y", "-i", video_path, "-i", audio_path,
                            "-map", "0:v", "-map", "1:a",
                            "-c:v", "libx264", "-crf", "20",
                            "-c:a", "aac", "-b:a", "192k",
                            output_path]
            result2 = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=mux_timeout)
            if result2.returncode != 0:
                raise RuntimeError(f"FFmpeg muxing failed:\n{result2.stderr}")
    
    # Return output duration
    out_dur_result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", output_path],
        capture_output=True, text=True, timeout=30
    )
    out_dur = float(out_dur_result.stdout.strip() or "0")
    print(f"        Output duration: {out_dur:.1f}s")
    return out_dur


# ---------------------------------------------------------------------------
# Generate SRT subtitle file
# ---------------------------------------------------------------------------

def format_timestamp(seconds: float) -> str:
    """Format seconds as SRT timestamp: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def generate_srt(segments: list, output_path: str, use_translated: bool = True) -> None:
    """Generate SRT subtitle file from segments."""
    with open(output_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            text = seg.get("translated", seg.get("text", "")) if use_translated else seg.get("text", seg.get("translated", ""))
            f.write(f"{i}\n")
            f.write(f"{format_timestamp(seg['start'])} --> {format_timestamp(seg['end'])}\n")
            f.write(f"{text}\n\n")


def _translate_segments_for_subtitles(tts_segments: list, sub_lang: str,
                                       source_lang: str, original_segments: list) -> list:
    """Translate segments to a different language for subtitle generation.
    
    Uses LLM translation (same as dub pipeline) if available, falls back to
    Google Translate. Returns new segment list with 'translated' field set
    to the subtitle language text.
    """
    import copy
    
    # Build a segments list with 'text' field for the LLM translator
    # Use the original (source) text, not the dub translation
    trans_input = []
    for i, seg in enumerate(tts_segments):
        trans_input.append({
            "text": seg.get("text", ""),  # original source text
            "start": seg.get("start", 0),
            "end": seg.get("end", 0),
        })
    
    print(f"        Translating {len(trans_input)} subtitles to '{sub_lang}'...")
    
    translated_texts = None
    
    # Try LLM batch translation first (better quality, context-aware)
    try:
        translated_texts = llm_translate_batch(trans_input, sub_lang, source_lang or "auto")
        if translated_texts and all(t for t in translated_texts):
            print(f"        ✅ LLM translated {len(translated_texts)} subtitles to {sub_lang}")
        else:
            translated_texts = None
            raise Exception("LLM returned empty translations")
    except Exception as e:
        print(f"        LLM subtitle translation failed ({e}), using Google Translate")
        translated_texts = None
    
    # Fallback: Google Translate
    if not translated_texts:
        try:
            from deep_translator import GoogleTranslator
            translator = GoogleTranslator(source="auto", target=sub_lang)
            translated_texts = []
            for seg in trans_input:
                try:
                    translated_texts.append(translator.translate(seg["text"]))
                except Exception:
                    translated_texts.append(seg["text"])  # keep original on failure
            print(f"        ✅ Google Translate: {len(translated_texts)} subtitles to {sub_lang}")
        except Exception as e:
            print(f"        ⚠ Google Translate also failed ({e}), using original text")
            translated_texts = [seg["text"] for seg in trans_input]
    
    # Build new segments with subtitle language text
    result = []
    for i, seg in enumerate(tts_segments):
        new_seg = copy.copy(seg)
        new_seg["translated"] = translated_texts[i] if i < len(translated_texts) else seg.get("text", "")
        result.append(new_seg)
    return result


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def dub_video(
    video_path: str,
    target_lang: str = "hi",
    voice: str = None,
    model_size: str = "base",
    output_path: str = None,
    keep_background: bool = False,
    burn_subtitles: bool = False,
    generate_srt_file: bool = True,
    progress_callback=None,
    job_dir: str = None,
    resume: bool = False,
    multi_speaker: bool = False,
    num_speakers: int = None,
    speaker_voices: dict = None,
    use_voice_cloning: bool = False,
    extend_video: bool = True,
    keep_background_music: bool = True,
    emotion_transfer: bool = True,
    prosody_strength: float = 1.0,
    anti_copyright: bool = False,
    blur_original_subtitles: bool = False,
    subtitle_lang: str = None,
    funny_mode: bool = False,
) -> dict:
    """
    Main function to dub a video.
    Supports checkpoint/resume: if job_dir is provided, intermediate results
    are saved. If resume=True and a checkpoint exists, continues from the
    last completed stage.

    subtitle_lang: if provided, generates subtitles in this language (separate
    from the dub language). Can be any supported language code (e.g. 'en', 'hi',
    'es'). If None, subtitles use the dub language (target_lang).
    The SRT file for the subtitle language is saved separately and burned into
    the video instead of the dub language subtitles.

    Multi-speaker: if multi_speaker=True, runs diarization to detect speakers,
    assigns each a distinct voice from the VOICE_POOL. num_speakers can force
    a specific count. speaker_voices can override auto-assignment
    (mapping speaker_id -> voice_name).

    keep_background_music: if True, uses Demucs to separate vocals from
    background music/SFX. Transcribes from isolated vocals (better accuracy).
    Mixes dubbed TTS with the original background (no_vocals) track.
    This preserves background music and sound effects in the dubbed video.

    emotion_transfer: if True, analyzes each segment's emotion using
    emotion2vec+ and prosody features (pitch, energy, rate), then:
      a) Passes emotion parameters to Chatterbox/IndexTTS-2 for emotion-aware TTS
      b) Post-processes TTS output with prosody transfer (pitch/energy/rate matching)
    This makes the dubbed voice carry the same emotions and delivery as the
    original — like a professional voice artist dub.

    prosody_strength: Controls the strength of prosody transfer (0.0-1.0).
    0.0 = no prosody post-processing, 1.0 = full pitch/energy/rate matching.
    Higher values make the dub sound more like the original speaker's delivery.

    progress_callback(stage, message, sub_progress, sub_total):
      - stage: 1-7 or "done"
      - message: human-readable status
      - sub_progress: current item within stage (e.g. 45 of 425 translated)
      - sub_total: total items in this stage
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

    # Reset voice cloning backend lock for this job — ensures we pick ONE
    # backend and stick with it for the entire video (no voice changes)
    reset_cloning_backend()

    def log(stage, msg, sub_progress=None, sub_total=None):
        if progress_callback:
            progress_callback(stage, msg, sub_progress, sub_total)
        else:
            print(msg)

    # Auto-select voice
    if not voice:
        voice = DEFAULT_VOICES.get(target_lang, "en-US-AriaNeural")

    # Auto-generate output path
    if not output_path:
        base = os.path.splitext(video_path)[0]
        output_path = f"{base}_{target_lang}_dubbed.mp4"

    # --- Load checkpoint if resuming ---
    ckpt = None
    if resume and job_dir:
        ckpt = load_checkpoint(job_dir)
        if ckpt:
            log(ckpt["stage"], f"📂 Resuming from stage {ckpt['stage']}...")
        else:
            log(1, "No checkpoint found, starting fresh...")

    start_time = time.time()

    # Use a persistent temp dir inside job_dir if available, else temp
    if job_dir:
        temp_dir = os.path.join(job_dir, "tmp")
        os.makedirs(temp_dir, exist_ok=True)
    else:
        temp_dir_ctx = tempfile.TemporaryDirectory(prefix="dubber_")
        temp_dir = temp_dir_ctx.__enter__()

    try:
        audio_wav = os.path.join(temp_dir, "audio.wav")
        total_duration = 0.0
        segments = []
        source_lang = ""
        translated_segments = []
        tts_segments = []

        # --- Stage 1: Extract audio ---
        if ckpt is None or ckpt["stage"] < 1:
            log(1, f"\n🎬 Starting video dubbing pipeline...")
            log(1, f"   Input: {video_path}")
            log(1, f"   Target language: {LANG_NAMES.get(target_lang, target_lang)} ({target_lang})")
            log(1, f"   Voice: {voice}")
            log(1, f"   Whisper model: {model_size}")

            extract_audio(video_path, audio_wav)

            dur_result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", video_path],
                capture_output=True, text=True
            )
            total_duration = float(dur_result.stdout.strip() or "0")

            if job_dir:
                save_checkpoint(job_dir, 1, {
                    "audio_wav": audio_wav,
                    "total_duration": total_duration,
                    "target_lang": target_lang,
                    "voice": voice,
                })
        else:
            log(1, f"   ✅ Stage 1 already done (audio extracted)")
            audio_wav = ckpt.get("audio_wav", audio_wav)
            if not os.path.exists(audio_wav):
                # Re-extract if file is gone
                extract_audio(video_path, audio_wav)
            dur_result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", video_path],
                capture_output=True, text=True
            )
            total_duration = float(dur_result.stdout.strip() or "0")

        # --- Stage 1.5: Vocal isolation (optional) ---
        # Separate vocals (speech) from background (music/SFX) using Demucs.
        # Transcribe from isolated vocals for better accuracy.
        # Mix no_vocals track with dubbed TTS to preserve background music.
        no_vocals_path = None
        transcribe_audio_path = audio_wav  # default: transcribe from full audio
        if keep_background_music:
            # Skip Demucs for audio >5 min — it runs at ~1.5x real-time on this CPU,
            # so a 10-min video takes 15 min just for vocal separation.
            # Instead, use the original audio for both transcription (faster-whisper
            # VAD handles background music) and as the background track with ducking.
            try:
                import subprocess as _sp
                dur_r = _sp.run(
                    ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", audio_wav],
                    capture_output=True, text=True, timeout=10)
                audio_dur_sec = float(dur_r.stdout.strip() or "0")
            except Exception:
                audio_dur_sec = 0

            if audio_dur_sec > 300:
                log(1, f"  [1.5] Skipping vocal isolation (audio is {audio_dur_sec:.0f}s — Demucs would take ~{audio_dur_sec*1.5:.0f}s)")
                log(1, f"       Will mute original speech segments from background track")
                # Don't use raw original audio — it contains original speech!
                # The mixing stage will create a background track by muting
                # speech segments, keeping only music/ambience.
                no_vocals_path = "__MUTE_SPEECH__"  # signal to mixing stage
            else:
                log(1, f"  [1.5] Isolating vocals from background music (Demucs)...")
                vocals_path, no_vocals_path = separate_vocals(
                    audio_wav, temp_dir, progress_callback=progress_callback)
                if vocals_path:
                    transcribe_audio_path = vocals_path
                    log(1, f"        ✅ Will transcribe from isolated vocals (cleaner speech)")
                else:
                    log(1, f"        ⚠ Vocal isolation failed, using full audio")
                    no_vocals_path = None

        # --- Stage 2: Transcribe ---
        if ckpt is None or ckpt["stage"] < 2:
            log(2, f"  [2/5] Transcribing audio with Whisper ({model_size})...", 0, 0)

            def transcribe_progress(count, current_ts, total_dur, preview):
                # Timestamp-based progress: current_ts/total_dur = fraction of audio transcribed
                # This NEVER jumps backwards because timestamps are monotonically increasing
                if total_dur and total_dur > 0:
                    msg = f"Transcribing... {count} segments ({current_ts:.0f}s / {total_dur:.0f}s)"
                else:
                    msg = f"Transcribing... {count} segments"
                if preview:
                    msg += f" → \"{preview}\""
                # Send (current_ts, total_dur) as (sub_progress, sub_total)
                # so progress bar = current_ts/total_dur — always monotonic
                log(2, msg, current_ts, total_dur)

            transcription = transcribe_audio(transcribe_audio_path, model_size,
                                             progress_callback=transcribe_progress)
            segments = transcription["segments"]
            source_lang = transcription["source_lang"]

            if not segments:
                raise RuntimeError("No speech detected in the video.")

            # --- Multi-speaker diarization ---
            # Auto-enable diarization when voice cloning is requested,
            # so each speaker gets their own cloned voice.
            if multi_speaker or use_voice_cloning:
                if not multi_speaker:
                    log(2, f"  [2.5] Auto-detecting speakers (needed for voice cloning)...", 0, 0)
                else:
                    log(2, f"  [2.5] Detecting speakers in audio...", 0, 0)

                def diarize_progress(msg):
                    log(2, msg, 0, 1)

                diarized = diarize_audio(audio_wav, num_speakers=num_speakers,
                                         min_speakers=2 if use_voice_cloning else 1,
                                         progress_callback=diarize_progress)
                segments = assign_speakers_to_segments(segments, diarized)

                # Assign voices to speakers
                detected_speakers = sorted(set(s["speaker"] for s in segments))
                num_detected = len(detected_speakers)
                log(2, f"Found {num_detected} speakers: {detected_speakers}", num_detected, num_detected)

                if speaker_voices is None:
                    speaker_voices = {}
                    # --- Intelligent Voice Detection ---
                    # Analyze each speaker's audio to detect gender/age
                    # and assign the best matching TTS voice
                    try:
                        import voice_manager
                        log(2, f"  [2.6] Analyzing speaker voices (gender/age detection)...", 0, len(detected_speakers))
                        
                        def vm_progress(done, total, msg):
                            log(2, f"        {msg}", done, total)
                        
                        # We need reference audio for each speaker
                        # Extract short clips from the original audio for analysis
                        speaker_analysis = {}
                        if not multi_speaker and use_voice_cloning:
                            # Single speaker — use the whole audio
                            pass  # Will extract during stage 3.5
                        
                        # For now, use voice_manager to analyze reference audio
                        # The actual reference extraction happens at stage 3.5
                        # Here we just assign based on pool order (fallback)
                        # Voice_manager analysis happens at stage 3.5
                        for spk_id in detected_speakers:
                            speaker_voices[spk_id] = get_voice_for_speaker(
                                spk_id, num_detected, target_lang)
                        
                        log(2, f"Voice assignments (will be refined after voice analysis):", num_detected, num_detected)
                        for spk_id in detected_speakers:
                            log(2, f"  Speaker {spk_id} → {speaker_voices[spk_id]}")
                    except ImportError:
                        # voice_manager not available — use simple pool assignment
                        for spk_id in detected_speakers:
                            speaker_voices[spk_id] = get_voice_for_speaker(
                                spk_id, num_detected, target_lang)
                        log(2, f"Voice assignments:", num_detected, num_detected)
                        for spk_id in detected_speakers:
                            log(2, f"  Speaker {spk_id} → {speaker_voices[spk_id]}")

            log(2, f"Transcribed {len(segments)} segments (language: {source_lang})",
                total_duration, total_duration)

            # Free transcription/diarization models from memory before next stage
            import gc as _gc
            try:
                from model_manager import _model_manager
                _model_manager.unload_current()
            except Exception:
                pass
            _gc.collect()

            if job_dir:
                save_checkpoint(job_dir, 2, {
                    "segments": segments,
                    "source_lang": source_lang,
                    "total_duration": total_duration,
                    "audio_wav": audio_wav,
                    "target_lang": target_lang,
                    "voice": voice,
                    "multi_speaker": multi_speaker,
                    "speaker_voices": speaker_voices if multi_speaker else None,
                })
        else:
            log(2, f"   ✅ Stage 2 already done (transcribed {len(ckpt.get('segments', ckpt.get('translated_segments', [])))} segments)",
                len(ckpt.get('segments', ckpt.get('translated_segments', []))), len(ckpt.get('segments', ckpt.get('translated_segments', []))))
            # Stage 3+ checkpoints store translated_segments (which include original text)
            # Stage 2 checkpoint stores segments
            segments = ckpt.get("segments") or [s for s in ckpt.get("translated_segments", [])]
            source_lang = ckpt["source_lang"]
            # Restore multi-speaker state
            if ckpt.get("multi_speaker") and speaker_voices is None:
                speaker_voices = ckpt.get("speaker_voices")
            if speaker_voices and not multi_speaker:
                multi_speaker = True  # Auto-detect from checkpoint

        # --- Stage 2.5: Emotion & Prosody Analysis ---
        # Analyze each segment's emotion and prosody (pitch, energy, rate)
        # to guide emotion-aware TTS generation and prosody transfer.
        emotion_profiles = None
        if emotion_transfer:
            if ckpt is None or ckpt["stage"] < 3:
                try:
                    import emotion_analyzer
                    log(2, f"  [2.5] Analyzing emotions & prosody (emotion2vec + librosa)...", 0, len(segments))

                    def emotion_progress(done, total, msg):
                        log(2, msg, done, total)

                    # Use the transcription audio (isolated vocals if available)
                    emotion_audio_path = transcribe_audio_path if 'transcribe_audio_path' in dir() else audio_wav
                    emotion_profiles = emotion_analyzer.analyze_segments(
                        emotion_audio_path, segments,
                        temp_dir=temp_dir,
                        progress_callback=emotion_progress,
                        use_emotion2vec=True,
                    )

                    # Log summary
                    summary = emotion_analyzer.summarize_emotions(emotion_profiles)
                    log(2, f"Emotion analysis done: {summary['total_segments']} segments, "
                        f"dominant: {summary.get('dominant_emotion_display', 'neutral')}",
                        summary['total_segments'], summary['total_segments'])

                    # Print emotion distribution
                    dist = summary.get("emotion_distribution", {})
                    for emo, frac in sorted(dist.items(), key=lambda x: -x[1])[:5]:
                        display = emotion_analyzer.EMOTION_DISPLAY.get(emo, emo)
                        log(2, f"    {display}: {frac*100:.0f}%")

                except ImportError:
                    print("        ⚠ emotion_analyzer module not available, skipping emotion analysis")
                    emotion_transfer = False
                except Exception as e:
                    print(f"        ⚠ Emotion analysis failed: {e!r}")
                    emotion_transfer = False

        # --- Stage 3: Translate ---
        if ckpt is None or ckpt["stage"] < 3:
            if funny_mode:
                log(3, f"  [3/5] Translating to '{target_lang}' (FUNNY/COMEDY MODE)...", 0, len(segments))
            else:
                log(3, f"  [3/5] Translating to '{target_lang}'...", 0, len(segments))

            def translate_progress(done, total, preview):
                msg = f"Translating... {done}/{total} segments"
                if preview:
                    msg += f" → \"{preview}\""
                log(3, msg, done, total)

            translated_segments = translate_segments(
                segments, target_lang, source_lang,
                job_dir=job_dir,
                progress_callback=translate_progress,
                funny_mode=funny_mode,
            )

            log(3, f"Translated {len(translated_segments)} segments",
                len(translated_segments), len(translated_segments))

            if job_dir:
                save_checkpoint(job_dir, 3, {
                    "translated_segments": translated_segments,
                    "source_lang": source_lang,
                    "total_duration": total_duration,
                    "audio_wav": audio_wav,
                    "target_lang": target_lang,
                    "voice": voice,
                })
        else:
            # Stage 3+ checkpoint may store translated_segments directly,
            # or we may need to get them from tts_segments (stage 4+)
            if "translated_segments" in ckpt:
                translated_segments = ckpt["translated_segments"]
            elif "tts_segments" in ckpt:
                # TTS segments contain the translated text
                translated_segments = ckpt["tts_segments"]
            else:
                translated_segments = []
            log(3, f"   ✅ Stage 3 already done (translated {len(translated_segments)} segments)",
                len(translated_segments), len(translated_segments))

        # --- Stage 3.5: Extract speaker reference audio (for voice cloning) ---
        speaker_ref_audios = None
        if use_voice_cloning:
            log(3, f"  [3.5] Extracting reference voice samples for cloning...", 0, 1)
            # Use the segments that have speaker info, or all segments for single speaker
            segs_for_ref = segments if not multi_speaker else segments
            speaker_ref_audios = extract_speaker_reference_audio(
                audio_wav, segs_for_ref, temp_dir
            )
            if speaker_ref_audios:
                log(3, f"Extracted {len(speaker_ref_audios)} reference voice clips", 1, 1)
                
                # --- Intelligent Voice Analysis ---
                # Analyze each speaker's reference audio to detect gender/age
                # and refine the voice assignments
                try:
                    import voice_manager
                    log(3, f"  [3.6] Analyzing speaker characteristics (AI gender/age detection)...", 0, len(speaker_ref_audios))
                    
                    def analyze_progress(done, total, msg):
                        log(3, msg, done, total)
                    
                    speaker_profiles = voice_manager.analyze_all_speakers(
                        speaker_ref_audios, progress_callback=analyze_progress)
                    
                    # Refine voice assignments based on detected gender/age
                    if multi_speaker:
                        log(3, f"  Refining voice assignments based on speaker analysis:", 0, len(speaker_profiles))
                        for spk_id, profile in speaker_profiles.items():
                            gender = profile["gender"]
                            age = profile["age"]
                            
                            # Get gender-appropriate voice
                            if target_lang == "hi":
                                # For Hindi, prefer Edge-TTS gender-appropriate voices
                                new_voice = voice_manager.get_voice_by_profile(
                                    gender, age, target_lang, speaker_index=spk_id)
                            else:
                                new_voice = voice_manager.get_voice_by_profile(
                                    gender, age, target_lang, speaker_index=spk_id)
                            
                            old_voice = speaker_voices.get(spk_id, "unknown")
                            speaker_voices[spk_id] = new_voice
                            log(3, f"    Speaker {spk_id}: {profile['description']} → {new_voice}", spk_id, len(speaker_profiles))
                    
                    # Store profiles for later use
                    for spk_id, profile in speaker_profiles.items():
                        print(f"        🎭 {profile['description']}")
                        
                except ImportError:
                    print("        ⚠ voice_manager not available, using default voice assignment")
                except Exception as e:
                    print(f"        ⚠ Voice analysis failed: {e!r}")
                    
            else:
                log(3, f"⚠ Failed to extract reference audio, will use edge-tts fallback", 1, 1)
                use_voice_cloning = False

        # --- Stage 4: Generate TTS ---
        if ckpt is None or ckpt["stage"] < 4:
            # Determine TTS mode for display
            _kokoro_active = False
            try:
                import kokoro_tts
                _kokoro_active = kokoro_tts.is_supported(target_lang)
            except ImportError:
                pass
            if use_voice_cloning:
                mode_str = "voice cloning"
            elif _kokoro_active:
                mode_str = "Kokoro TTS (SOTA quality)"
            else:
                mode_str = "Edge-TTS"
            if emotion_transfer and emotion_profiles:
                mode_str += " + emotion"
            log(4, f"  [4/5] Generating voice with {mode_str}...", 0, len(translated_segments))

            def tts_progress(done, total):
                log(4, f"Generating voice clips... {done}/{total}", done, total)

            tts_segments = asyncio.run(
                generate_tts_segments(
                    translated_segments, target_lang, voice, temp_dir,
                    job_dir=job_dir,
                    progress_callback=tts_progress,
                    speaker_voices=speaker_voices if multi_speaker else None,
                    use_voice_cloning=use_voice_cloning,
                    speaker_ref_audios=speaker_ref_audios,
                    emotion_profiles=emotion_profiles if emotion_transfer else None,
                )
            )

            log(4, f"Generated {len(tts_segments)} voice clips",
                len(tts_segments), len(tts_segments))

            if job_dir:
                save_checkpoint(job_dir, 4, {
                    "tts_segments": tts_segments,
                    "total_duration": total_duration,
                    "audio_wav": audio_wav,
                    "target_lang": target_lang,
                    "voice": voice,
                    "multi_speaker": multi_speaker,
                    "speaker_voices": speaker_voices if multi_speaker else None,
                    "use_voice_cloning": use_voice_cloning,
                })
        else:
            tts_segments = ckpt.get("tts_segments", [])
            log(4, f"   ✅ Stage 4 already done (generated {len(tts_segments)} voice clips)",
                len(tts_segments), len(tts_segments))
            # Verify audio files exist (but don't fail on missing - use silence)
            missing = 0
            for ts in tts_segments:
                if not os.path.exists(ts["audio_path"]) or os.path.getsize(ts["audio_path"]) == 0:
                    # Generate silence for missing clips
                    silence_cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i",
                                   "anullsrc=r=48000:cl=mono", "-t", "0.3",
                                   "-c:a", "libmp3lame", ts["audio_path"]]
                    subprocess.run(silence_cmd, capture_output=True)
                    missing += 1
            if missing > 0:
                print(f"        Generated {missing} silence clips for missing TTS")

        # --- Stage 4.5: Extract non-speech sounds (laughs, sighs, reactions) ---
        non_speech_clips = None
        if get_non_speech_clips and segments:
            log(4, f"  [4.5] Extracting non-speech sounds (laughs, sighs, reactions)...", 0, 1)
            # Use the ORIGINAL full audio (not isolated vocals) to extract
            # non-speech sounds. Demucs may reduce the volume of non-speech
            # sounds (laughs, sighs) during vocal isolation. Using the original
            # audio preserves the full energy of these sounds.
            ns_audio_path = audio_wav
            try:
                non_speech_clips = get_non_speech_clips(
                    ns_audio_path, segments, total_duration, temp_dir,
                    min_duration=0.2,
                )
                log(4, f"Non-speech sounds: {len(non_speech_clips or [])} clips extracted", 1, 1)
            except Exception as e:
                print(f"        ⚠ Non-speech extraction failed: {e!r}")
                non_speech_clips = None

        # --- Stage 4.6: Prosody Transfer (emotion matching) ---
        # Post-process each TTS clip to match the original speaker's
        # pitch, energy, and dynamic range — making the dub feel natural.
        if emotion_transfer and prosody_strength > 0 and emotion_profiles:
            try:
                import prosody_transfer
                log(4, f"  [4.6] Applying prosody transfer (pitch/energy/emotion matching)...", 0, len(tts_segments))

                def prosody_progress(done, total):
                    log(4, f"Prosody transfer... {done}/{total} clips", done, total)

                tts_segments = prosody_transfer.apply_prosody_to_segments(
                    tts_segments, audio_wav, temp_dir,
                    profiles=emotion_profiles,
                    strength=prosody_strength,
                    progress_callback=prosody_progress,
                )
                log(4, f"Prosody transfer complete", len(tts_segments), len(tts_segments))
            except ImportError:
                print("        ⚠ prosody_transfer module not available, skipping")
            except Exception as e:
                print(f"        ⚠ Prosody transfer failed: {e!r}")

        # Generate SRT (in dub language)
        srt_path = None
        # Subtitle language SRT (separate from dub language, if requested)
        sub_srt_path = None
        if generate_srt_file:
            srt_path = os.path.splitext(output_path)[0] + ".srt"
            generate_srt(tts_segments, srt_path, use_translated=True)
            log(4, f"        Saved subtitles ({target_lang}): {srt_path}")
            
            # Generate separate subtitle language SRT if requested
            if subtitle_lang and subtitle_lang != target_lang:
                sub_srt_path = os.path.splitext(output_path)[0] + f"_subs_{subtitle_lang}.srt"
                try:
                    # Translate segments to subtitle language
                    sub_segments = _translate_segments_for_subtitles(
                        tts_segments, subtitle_lang, source_lang, segments)
                    generate_srt(sub_segments, sub_srt_path, use_translated=True)
                    log(4, f"        Saved subtitles ({subtitle_lang}): {sub_srt_path}")
                    # Use subtitle language SRT for burn-in instead of dub language
                    burn_srt = sub_srt_path
                except Exception as e:
                    print(f"        ⚠ Subtitle language generation failed ({e}), using dub language subtitles")
                    burn_srt = srt_path
            else:
                burn_srt = srt_path

        # --- Stage 5: Build dubbed audio ---
        if ckpt is None or ckpt["stage"] < 5:
            log(5, f"  [5/5] Building dubbed audio track...", 0, 1)
            # When keep_background_music: use isolated no_vocals track (music+SFX only)
            # When keep_background (legacy): use full original audio (vocals+music at low vol)
            # Otherwise: no background
            if keep_background_music and no_vocals_path == "__MUTE_SPEECH__":
                # Long audio: create background by muting speech segments
                # from the original audio (keeps music/ambience, removes original voice)
                bg_audio_path = _create_muted_background(
                    audio_wav, segments, temp_dir, total_duration)
                if bg_audio_path:
                    use_bg = True
                    log(5, f"        ✅ Background track: original speech muted, music/ambience kept")
                else:
                    log(5, f"        ⚠ Failed to create muted background, no background audio")
                    bg_audio_path = None
                    use_bg = False
            elif keep_background_music and no_vocals_path and no_vocals_path != "__MUTE_SPEECH__":
                bg_audio_path = no_vocals_path
                use_bg = True
            elif keep_background_music and not no_vocals_path:
                # Demucs failed — fall back to muted speech background
                bg_audio_path = _create_muted_background(
                    audio_wav, segments, temp_dir, total_duration)
                if bg_audio_path:
                    use_bg = True
                    log(5, f"        ⚠ Vocal isolation failed, using muted-speech background")
                else:
                    bg_audio_path = audio_wav  # last resort
                    use_bg = True
            elif keep_background:
                bg_audio_path = audio_wav
                use_bg = True
            else:
                bg_audio_path = None
                use_bg = False
            final_audio, video_shift_points = build_dubbed_audio(
                tts_segments, total_duration, temp_dir,
                keep_bg=use_bg,
                original_audio_path=bg_audio_path,
                extend_video=extend_video,
                bg_volume=0.45 if keep_background_music else 0.15,
                non_speech_clips=non_speech_clips,
            )
            log(5, f"Dubbed audio track built", 1, 1)

            if job_dir:
                save_checkpoint(job_dir, 5, {
                    "final_audio": final_audio,
                    "srt_path": srt_path,
                    "target_lang": target_lang,
                    "voice": voice,
                })
        else:
            log(5, f"   ✅ Stage 5 already done (dubbed audio built)", 1, 1)
            final_audio = ckpt["final_audio"]
            video_shift_points = []  # not available from checkpoint

        # --- Stage 6: Mux ---
        log(6, f"  [final] Muxing video with dubbed audio...", 0, 1)
        # Use subtitle language SRT for burn-in if available, otherwise dub language
        mux_srt = sub_srt_path if (subtitle_lang and generate_srt_file and 'sub_srt_path' in dir() and sub_srt_path and os.path.exists(sub_srt_path)) else srt_path
        out_dur = mux_video_audio(video_path, final_audio, output_path,
                        burn_subtitles=burn_subtitles, srt_path=mux_srt,
                        extend_video=extend_video,
                        video_shift_points=video_shift_points,
                        anti_copyright=anti_copyright,
                        blur_original_subtitles=blur_original_subtitles,
                        target_lang=target_lang)
        log(6, f"Muxing complete (output: {out_dur:.1f}s)", 1, 1)

        # Clean up checkpoint
        if job_dir:
            ckpt_path = os.path.join(job_dir, CHECKPOINT_FILE)
            if os.path.exists(ckpt_path):
                os.remove(ckpt_path)
            # Clean up temp dir
            tmp_path = os.path.join(job_dir, "tmp")
            if os.path.exists(tmp_path):
                shutil.rmtree(tmp_path, ignore_errors=True)

    finally:
        if not job_dir and 'temp_dir_ctx' in dir():
            temp_dir_ctx.__exit__(None, None, None)

    elapsed = time.time() - start_time
    result = {
        "output_video": output_path,
        "srt_file": srt_path if generate_srt_file else None,
        "subtitle_srt_file": sub_srt_path if (subtitle_lang and generate_srt_file and 'sub_srt_path' in dir() and sub_srt_path) else None,
        "subtitle_language": subtitle_lang if subtitle_lang else target_lang,
        "source_language": source_lang,
        "target_language": target_lang,
        "voice": voice,
        "segments_count": len(segments),
        "elapsed_seconds": round(elapsed, 1),
        "voice_cloned": use_voice_cloning,
        "video_extended": extend_video,
        "emotion_transfer": emotion_transfer,
        "prosody_strength": prosody_strength,
    }
    # Add emotion summary if available
    if emotion_transfer and emotion_profiles:
        try:
            import emotion_analyzer
            result["emotion_summary"] = emotion_analyzer.summarize_emotions(emotion_profiles)
        except Exception:
            pass
    if multi_speaker and speaker_voices:
        result["multi_speaker"] = True
        result["speakers"] = {str(k): v for k, v in speaker_voices.items()}
        result["num_speakers"] = len(speaker_voices)

    log("done", f"\n✅ Dubbing complete in {elapsed:.1f}s!")
    log("done", f"   Output video: {output_path}")
    if srt_path:
        log("done", f"   Subtitles: {srt_path}")

    return result


# ---------------------------------------------------------------------------
# List available voices
# ---------------------------------------------------------------------------

def list_voices(lang: str = None):
    """List available Edge-TTS voices, optionally filtered by language."""
    import edge_tts
    voices = asyncio.run(edge_tts.list_voices())
    if lang:
        lang_lower = lang.lower()
        voices = [v for v in voices if v["Locale"].lower().startswith(lang_lower)]
    return voices


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="🎬 Free Video Dubber - AI-powered video translation & dubbing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dub English video to Hindi
  python dubber.py input.mp4 --target-lang hi

  # Dub to Japanese with specific voice
  python dubber.py input.mp4 --target-lang ja --voice ja-JP-NanamiNeural

  # Dub with burned-in subtitles and small whisper model (faster)
  python dubber.py input.mp4 --target-lang es --burn-subtitles --model tiny

  # List all Hindi voices
  python dubber.py --list-voices hi

  # List all available languages
  python dubber.py --list-langs
        """,
    )

    parser.add_argument("video", nargs="?", help="Input video file path")
    parser.add_argument("--target-lang", "-t", default="hi",
                        help="Target language code (default: hi)")
    parser.add_argument("--voice", "-v", default=None,
                        help="Edge-TTS voice name (auto-selected if not specified)")
    parser.add_argument("--model", "-m", default="base",
                        choices=["tiny", "base", "small", "medium", "large"],
                        help="Whisper model size (default: base)")
    parser.add_argument("--output", "-o", default=None,
                        help="Output video path (auto-generated if not specified)")
    parser.add_argument("--no-background", action="store_true",
                        help="Don't keep original background audio")
    parser.add_argument("--burn-subtitles", action="store_true",
                        help="Burn translated subtitles into video")
    parser.add_argument("--no-srt", action="store_true",
                        help="Don't generate SRT subtitle file")
    parser.add_argument("--job-dir", default=None,
                        help="Job directory for checkpoints (enables resume)")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from last checkpoint in job-dir")
    parser.add_argument("--multi-speaker", action="store_true",
                        help="Enable multi-speaker detection (diarization). "
                             "Each speaker gets a distinct voice.")
    parser.add_argument("--num-speakers", type=int, default=None,
                        help="Force a specific number of speakers (auto-detected if not set)")
    parser.add_argument("--voice-clone", action="store_true",
                        help="Clone original speaker's voice (uses OpenVoice V2 + edge-tts). "
                             "Instead of synthetic TTS, uses the original speaker's voice "
                             "to speak the translated text. Works for all languages.")
    parser.add_argument("--no-emotion-transfer", action="store_true",
                        help="Disable emotion & prosody transfer. By default, the tool "
                             "analyzes each segment's emotion and pitch/energy/rate, then "
                             "applies them to the TTS output for natural-sounding delivery.")
    parser.add_argument("--prosody-strength", type=float, default=1.0,
                        help="Prosody transfer strength (0.0-1.0, default: 1.0). "
                             "0.0 = no prosody post-processing, 1.0 = full emotion matching. "
                             "Higher values make the dub sound more like the original speaker.")
    parser.add_argument("--no-extend-video", action="store_true",
                        help="Don't extend video to fit audio. By default, video is "
                             "extended (freeze-frame) to match dubbed audio duration. "
                             "Use this to truncate audio to fit original video instead.")
    parser.add_argument("--list-voices", nargs="?", const="all",
                        help="List available TTS voices (optionally filter by language)")
    parser.add_argument("--list-langs", action="store_true",
                        help="List supported target languages")

    args = parser.parse_args()

    # List voices
    if args.list_voices is not None:
        voices = list_voices(args.list_voices if args.list_voices != "all" else None)
        print(f"\n🎵 Available Edge-TTS Voices ({len(voices)} total):\n")
        for v in voices:
            print(f"  {v['ShortName']:40s} | {v['Gender']:8s} | {v['Locale']}")
        return

    # List languages
    if args.list_langs:
        print("\n🌍 Supported Target Languages:\n")
        for code, name in sorted(LANG_NAMES.items(), key=lambda x: x[1]):
            voice = DEFAULT_VOICES.get(code, "auto")
            print(f"  {code:6s} | {name:15s} | default voice: {voice}")
        return

    # Dub video
    if not args.video:
        parser.print_help()
        sys.exit(1)

    result = dub_video(
        video_path=args.video,
        target_lang=args.target_lang,
        voice=args.voice,
        model_size=args.model,
        output_path=args.output,
        keep_background=not args.no_background,
        burn_subtitles=args.burn_subtitles,
        generate_srt_file=not args.no_srt,
        job_dir=args.job_dir,
        resume=args.resume,
        multi_speaker=args.multi_speaker,
        num_speakers=args.num_speakers,
        use_voice_cloning=args.voice_clone,
        extend_video=not args.no_extend_video,
        emotion_transfer=not args.no_emotion_transfer,
        prosody_strength=args.prosody_strength,
    )
    print(f"\n📊 Summary: {json.dumps(result, indent=2, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
