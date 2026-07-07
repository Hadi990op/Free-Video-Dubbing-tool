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
    """Extract audio from video as 16kHz mono WAV (Whisper format)."""
    print(f"  [1/5] Extracting audio from video...")
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-ac", "1", "-ar", "16000",
        "-f", "wav", output_wav
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg audio extraction failed:\n{result.stderr}")


# ---------------------------------------------------------------------------
# Step 1.5: Speaker Diarization (detect who speaks when)
# ---------------------------------------------------------------------------

def diarize_audio(audio_path: str, num_speakers: int = None,
                  max_speakers: int = 8,
                  progress_callback=None) -> list:
    """
    Run speaker diarization on audio file.
    Returns list of {start, end, speaker} segments.
    Uses simple-diarizer (Silero VAD + SpeechBrain embeddings).
    No HuggingFace token required.

    num_speakers: if known, forces that many speakers.
                  if None, auto-detects using silhouette score.
    max_speakers: max speakers to consider for auto-detection.
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
            if best_score < 0.05 and best_n > 1:
                best_n = 1

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

def transcribe_audio(audio_path: str, model_size: str = "base",
                      progress_callback=None) -> dict:
    """
    Transcribe audio using faster-whisper.
    Returns dict with segments: [{start, end, text}, ...]
    progress_callback(current_count, total_estimate, preview_text) called periodically.

    Progress is timestamp-based: seg.start / audio_duration.
    This NEVER jumps backwards because audio timestamps are monotonically increasing.
    """
    print(f"  [2/5] Transcribing audio with Whisper ({model_size})...")
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")

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
    print(f"        Detected source language: {detected_lang}")
    print(f"        Found {len(segments)} speech segments")

    # Free Whisper model from memory — important for long videos.
    # The model can use 500MB-2GB RAM depending on size.
    del model
    import gc
    gc.collect()

    if progress_callback:
        progress_callback(len(segments), audio_duration, audio_duration, None)
    return {"segments": segments, "source_lang": detected_lang}


# ---------------------------------------------------------------------------
# Step 3: Translate text segments
# ---------------------------------------------------------------------------

def translate_segments(segments: list, target_lang: str, source_lang: str = None,
                      job_dir: str = None, progress_callback=None) -> list:
    """Translate each segment's text to target language using Google Translate.
    Supports per-segment checkpointing: if job_dir is provided, saves progress
    to translation_checkpoint.json so it can resume if internet drops.
    progress_callback(done, total, preview) called per segment."""
    print(f"  [3/5] Translating to '{LANG_NAMES.get(target_lang, target_lang)}'...")
    from deep_translator import GoogleTranslator

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

    def make_translator():
        return GoogleTranslator(source="auto", target=target_lang)

    translator = make_translator()

    for i, seg in enumerate(segments):
        # Skip if already translated from checkpoint
        if translated[i] is not None:
            continue

        # Retry loop for network resilience
        max_retries = 5
        translated_text = None
        for attempt in range(max_retries):
            try:
                translated_text = translator.translate(seg["text"])
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 5  # 5s, 10s, 15s, 20s, 25s
                    print(f"        Translation retry {attempt + 1}/{max_retries} for segment {i} (waiting {wait_time}s): {e}")
                    time.sleep(wait_time)
                    # Recreate translator on retry — fresh session avoids stale state
                    translator = make_translator()
                else:
                    print(f"        Warning: translation failed for segment {i} after {max_retries} retries: {e}")
                    translated_text = seg["text"]  # fallback to original

        new_seg = dict(seg)
        new_seg["translated"] = translated_text
        translated[i] = new_seg

        # Save checkpoint every 5 segments
        if trans_ckpt_path and ((i + 1) % 5 == 0 or i == len(segments) - 1):
            done = {str(j): translated[j]["translated"] for j in range(i + 1) if translated[j]}
            with open(trans_ckpt_path, "w") as f:
                json.dump({"translated": done}, f, ensure_ascii=False)

        # Rate limit protection: small pause every 50 segments to avoid
        # Google Translate temporarily blocking the IP on long videos.
        if (i + 1) % 50 == 0 and i < len(segments) - 1:
            time.sleep(1)

        # progress indicator
        if (i + 1) % 10 == 0 or i == len(segments) - 1:
            print(f"        Translated {i + 1}/{len(segments)} segments")
        if progress_callback:
            progress_callback(i + 1, len(segments), translated[i]["translated"][:60] if translated[i] else "")

    # Clean up translation checkpoint after success
    if trans_ckpt_path and os.path.exists(trans_ckpt_path):
        os.remove(trans_ckpt_path)

    return translated


# ---------------------------------------------------------------------------
# Step 3.5: Extract reference voice clips for voice cloning
# ---------------------------------------------------------------------------

def extract_speaker_reference_audio(audio_wav: str, segments: list,
                                     temp_dir: str, max_duration: float = 15.0) -> dict:
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
        if seg_dur < 5.0:
            # Concatenate up to 3 segments to get at least 5 seconds
            sorted_segs = sorted(segs, key=lambda s: s["start"])
            clips_to_concat = []
            total_dur = 0
            for s in sorted_segs:
                if total_dur >= 15.0:
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

# Global HF client instance (reused across calls)
_hf_client = None
_hf_client_lock = threading.Lock()

def _get_hf_client():
    """Get or create a Gradio client for the voice-clone HF space."""
    global _hf_client
    if _hf_client is not None:
        return _hf_client
    with _hf_client_lock:
        if _hf_client is not None:
            return _hf_client
        from gradio_client import Client
        # Try with HF token if available (more GPU quota)
        hf_token = os.environ.get("HF_TOKEN", "")
        # Check token file
        token_path = os.path.join(os.path.dirname(__file__), ".hf_token")
        if not hf_token and os.path.exists(token_path):
            with open(token_path) as f:
                hf_token = f.read().strip()
        if hf_token:
            os.environ["HF_TOKEN"] = hf_token
            _hf_client = Client("tonyassi/voice-clone", verbose=False, hf_token=hf_token)
        else:
            _hf_client = Client("tonyassi/voice-clone", verbose=False)
        return _hf_client


def clone_voice_tts(text: str, ref_audio_path: str, out_path: str,
                     max_retries: int = 3, xtts_lang: str = "en") -> bool:
    """Generate speech using voice cloning via HuggingFace XTTS space.
    
    Uses the tonyassi/voice-clone Gradio space which runs Coqui XTTS-v2.
    Falls back to edge-tts if voice cloning fails (quota exhaustion etc).
    """
    from gradio_client import Client, handle_file
    
    for attempt in range(max_retries):
        try:
            client = _get_hf_client()
            result = client.predict(
                text=text,
                audio=handle_file(ref_audio_path),
                api_name="/clone"
            )
            if result and os.path.exists(result) and os.path.getsize(result) > 0:
                # Convert to mp3 at 24kHz mono
                subprocess.run([
                    "ffmpeg", "-y", "-i", result,
                    "-vn", "-ac", "1", "-ar", "24000",
                    "-c:a", "libmp3lame", out_path
                ], capture_output=True, text=True)
                if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    return True
        except Exception as e:
            err_msg = str(e)
            if "quota" in err_msg.lower():
                # ZeroGPU quota exhausted — no point retrying immediately
                print(f"        ⚠ HF ZeroGPU quota exhausted, will use edge-tts fallback")
                return False
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 3
                print(f"        Voice clone retry {attempt + 1}/{max_retries} (waiting {wait}s): {e}")
                time.sleep(wait)
            else:
                print(f"        ⚠ Voice clone failed after {max_retries} retries: {e}")
    return False


async def generate_tts_segments(segments: list, target_lang: str,
                                 voice: str, temp_dir: str,
                                 job_dir: str = None,
                                 progress_callback=None,
                                 speaker_voices: dict = None,
                                 use_voice_cloning: bool = False,
                                 speaker_ref_audios: dict = None) -> list:
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
    if use_voice_cloning:
        mode_str = "voice cloning (original speaker voice)"
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

    async def synth_one_edge(idx: int, text: str, out_path: str, seg_voice: str):
        """Synthesize using edge-tts."""
        # Skip if already generated (resume support)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return
        max_retries = 3
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
                                   "anullsrc=r=24000:cl=mono", "-t", "0.3",
                                   "-c:a", "libmp3lame", out_path]
                    subprocess.run(silence_cmd, capture_output=True)
                    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                        return
                    raise RuntimeError(f"TTS failed for clip {idx}: {e}")

    # Map our target_lang codes to XTTS language codes
    XTTS_LANG_MAP = {
        "hi": "hi", "es": "es", "fr": "fr", "de": "de", "ar": "ar",
        "zh": "zh", "ja": "ja", "ko": "ko", "ru": "ru", "pt": "pt",
        "it": "it", "tr": "tr", "id": "id", "nl": "nl", "pl": "pl",
        "uk": "uk", "el": "el", "cs": "cs", "ro": "ro", "hu": "hu",
        "sk": "sk", "bg": "bg", "hr": "hr", "lt": "lt", "lv": "lv",
        "sl": "sl", "sv": "sv", "fi": "fi", "da": "da", "vi": "vi",
    }
    xtts_lang = XTTS_LANG_MAP.get(target_lang, "en")

    def synth_one_clone(idx: int, text: str, out_path: str, ref_path: str) -> bool:
        """Synthesize using voice cloning. Returns True on success."""
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return True
        if not ref_path or not os.path.exists(ref_path):
            return False
        return clone_voice_tts(text, ref_path, out_path, max_retries=3,
                               xtts_lang=xtts_lang)

    async def synth_one(idx: int, text: str, out_path: str, seg_voice: str, ref_path: str = None):
        """Main synthesis dispatcher: voice cloning or edge-tts."""
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return
        if use_voice_cloning and ref_path:
            # Run voice cloning in a thread (it's synchronous)
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(None, synth_one_clone, idx, text, out_path, ref_path)
            if success:
                return
            # Fallback to edge-tts
            print(f"        ⚠ Voice clone failed for clip {idx}, falling back to edge-tts")
        await synth_one_edge(idx, text, out_path, seg_voice)

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
    # For voice cloning, use lower concurrency (2) since the HF space is slower
    sem_concurrency = 2 if use_voice_cloning else 8
    sem = asyncio.Semaphore(sem_concurrency)

    async def run_with_progress(idx, task):
        async with sem:
            await task
            async with progress_lock:
                done_count[0] += 1
                if progress_callback and (done_count[0] % 3 == 0 or done_count[0] == len(tts_segments)):
                    progress_callback(done_count[0], len(tts_segments))

    tasks_list = [run_with_progress(i, synth_one(i, seg["translated"], ts["audio_path"], ts["voice"],
                                                   get_ref_audio_for_seg(seg)))
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
                        extend_video: bool = False) -> str:
    """
    Build the final dubbed audio track by placing TTS segments at correct
    timestamps.

    If extend_video=True: clips are NOT truncated or speed-adjusted.
    The audio track will be as long as the longest clip placement requires.
    The video will be extended (freeze frames) to match the audio duration.
    
    If extend_video=False: clips are speed-adjusted and truncated to fit
    the original video timeline (legacy behavior).

    Returns path to the final mixed audio WAV.
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

    if extend_video:
        # EXTEND VIDEO MODE: Don't speed-adjust. Each clip at original timestamp.
        # If clip is longer than gap to next clip, TRIM it to fit (no overlap).
        # Video extends only if last clip ends after original video duration.
        adjusted_clips = []
        truncated = 0
        for ci, (seg, clip_path, clip_dur) in enumerate(valid_clips):
            idx = seg.get("audio_path", "").split("_")[-1].replace(".mp3", "")
            adj_path = os.path.join(temp_dir, f"adj_{idx}.mp3")
            # Convert to standard format
            adj_cmd = ["ffmpeg", "-y", "-i", clip_path, "-vn",
                       "-ac", "1", "-ar", "24000", adj_path]
            subprocess.run(adj_cmd, capture_output=True, text=True)
            if not (os.path.exists(adj_path) and os.path.getsize(adj_path) > 0):
                continue

            # Trim to not overlap with next clip
            if ci + 1 < len(valid_clips):
                next_start = valid_clips[ci + 1][0]["start"]
                max_dur = next_start - seg["start"] - 0.05  # 50ms gap
            else:
                max_dur = clip_dur  # last clip: no trim

            adj_dur = get_audio_duration(adj_path) if os.path.exists(adj_path) else 0
            if adj_dur > max_dur and max_dur > 0.3:
                trim_path = os.path.join(temp_dir, f"trim_{idx}.mp3")
                trim_cmd = ["ffmpeg", "-y", "-i", adj_path, "-t", f"{max_dur:.3f}",
                            "-vn", "-ac", "1", "-ar", "24000",
                            "-c:a", "libmp3lame", trim_path]
                subprocess.run(trim_cmd, capture_output=True, text=True)
                if os.path.exists(trim_path) and os.path.getsize(trim_path) > 0:
                    os.replace(trim_path, adj_path)
                    truncated += 1

            adjusted_clips.append((seg, adj_path))

        print(f"        Extend video mode — {len(adjusted_clips)} clips, trimmed {truncated} to prevent overlap")
    else:
        # LEGACY BEHAVIOR: Speed-adjust and truncate to fit original timeline
        adjusted_clips = []
        speed_adjusted = 0
        truncated = 0
        for ci, (seg, clip_path, clip_dur) in enumerate(valid_clips):
            slot_duration = seg["end"] - seg["start"]

            if ci + 1 < len(valid_clips):
                next_start = valid_clips[ci + 1][0]["start"]
            else:
                next_start = seg["end"] + 1.0
            max_dur = max(next_start - seg["start"] - 0.05, 0.2)

            idx = seg.get("audio_path", "").split("_")[-1].replace(".mp3", "")
            adj_path = os.path.join(temp_dir, f"adj_{idx}.mp3")

            need_speedup = clip_dur > slot_duration * 1.15
            need_slowdown = clip_dur < slot_duration * 0.8 and slot_duration > 0.5

            if need_speedup:
                target_dur = max(slot_duration * 0.95, clip_dur / 1.3)
                ratio = clip_dur / target_dur
                if ratio > 1.3:
                    ratio = 1.3
                tempo = f"atempo={ratio:.4f}"
                adj_cmd = ["ffmpeg", "-y", "-i", clip_path, "-filter:a", tempo,
                           "-vn", "-ac", "1", "-ar", "24000", adj_path]
                r = subprocess.run(adj_cmd, capture_output=True, text=True)
                if r.returncode != 0 or not os.path.exists(adj_path) or os.path.getsize(adj_path) == 0:
                    adj_cmd2 = ["ffmpeg", "-y", "-i", clip_path, "-vn",
                                "-ac", "1", "-ar", "24000", adj_path]
                    subprocess.run(adj_cmd2, capture_output=True, text=True)
                speed_adjusted += 1
            elif need_slowdown:
                target_dur = slot_duration * 0.95
                ratio = clip_dur / target_dur
                if ratio < 0.7:
                    ratio = 0.7
                tempo = atempo_filter(clip_dur, clip_dur / ratio)
                if tempo:
                    adj_cmd = ["ffmpeg", "-y", "-i", clip_path, "-filter:a", tempo,
                               "-vn", "-ac", "1", "-ar", "24000", adj_path]
                    subprocess.run(adj_cmd, capture_output=True, text=True)
                else:
                    adj_cmd = ["ffmpeg", "-y", "-i", clip_path, "-vn",
                               "-ac", "1", "-ar", "24000", adj_path]
                    subprocess.run(adj_cmd, capture_output=True, text=True)
                speed_adjusted += 1
            else:
                adj_cmd = ["ffmpeg", "-y", "-i", clip_path, "-vn",
                           "-ac", "1", "-ar", "24000", adj_path]
                subprocess.run(adj_cmd, capture_output=True, text=True)

            adj_dur = get_audio_duration(adj_path) if os.path.exists(adj_path) else 0
            if adj_dur > max_dur:
                trim_path = os.path.join(temp_dir, f"trim_{idx}.mp3")
                trim_cmd = ["ffmpeg", "-y", "-i", adj_path, "-t", f"{max_dur:.3f}",
                            "-vn", "-ac", "1", "-ar", "24000", "-c:a", "libmp3lame", trim_path]
                subprocess.run(trim_cmd, capture_output=True, text=True)
                if os.path.exists(trim_path) and os.path.getsize(trim_path) > 0:
                    os.replace(trim_path, adj_path)
                    truncated += 1

            if os.path.exists(adj_path) and os.path.getsize(adj_path) > 0:
                adjusted_clips.append((seg, adj_path))

        print(f"        Speed-adjusted {speed_adjusted}, truncated {truncated} clips to prevent overlap")

    if not adjusted_clips:
        raise RuntimeError("No clips could be adjusted.")

    print(f"        Adjusted {len(adjusted_clips)} clips, mixing...")

    mixed_path = os.path.join(temp_dir, "mixed_voice.wav")

    # For long videos with many clips (>200), use concat approach which is
    # more memory-efficient than amix with hundreds of inputs.
    # For shorter videos, amix is faster and simpler.
    use_concat = len(adjusted_clips) > 200

    if not use_concat:
        # amix approach: each clip gets adelay, then amix all at once
        inputs = []
        filter_parts = []
        for i, (seg, adj_path) in enumerate(adjusted_clips):
            inputs.extend(["-i", adj_path])
            delay_ms = int(seg["start"] * 1000)
            filter_parts.append(f"[{i}:a]adelay={delay_ms}|{delay_ms}[d{i}]")

        amix_inputs = "".join(f"[d{i}]" for i in range(len(adjusted_clips)))
        filter_complex = ";".join(filter_parts) + f";{amix_inputs}amix=inputs={len(adjusted_clips)}:duration=longest:normalize=0[a]"

        cmd = ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", filter_complex,
            "-map", "[a]", "-ac", "2", "-ar", "44100",
            mixed_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"        amix failed, falling back to concat approach...")
            use_concat = True

    if use_concat:
        # Concat approach: build a single audio track by placing clips at
        # their correct timestamps with silence in between.
        # More memory-efficient for 200+ clips.
        print(f"        Using concat approach for {len(adjusted_clips)} clips...")
        list_file = os.path.join(temp_dir, "concat_list.txt")
        with open(list_file, "w") as f:
            current_pos = 0.0
            for seg, adj_path in adjusted_clips:
                # Add silence if there's a gap
                if seg["start"] > current_pos:
                    silence_dur = seg["start"] - current_pos
                    silence_path = os.path.join(temp_dir, f"silence_{current_pos:.3f}.wav")
                    s_cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i",
                             f"anullsrc=r=44100:cl=stereo", "-t", str(silence_dur),
                             "-c:a", "pcm_s16le", silence_path]
                    subprocess.run(s_cmd, capture_output=True)
                    f.write(f"file '{silence_path}'\n")
                # Convert clip to same format as silence
                clip_wav = adj_path.replace(".mp3", "_wav.wav")
                c_cmd = ["ffmpeg", "-y", "-i", adj_path, "-vn",
                         "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", clip_wav]
                subprocess.run(c_cmd, capture_output=True)
                if os.path.exists(clip_wav) and os.path.getsize(clip_wav) > 0:
                    f.write(f"file '{clip_wav}'\n")
                current_pos = seg["start"] + get_audio_duration(clip_wav)

        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
               "-ac", "2", "-ar", "44100", mixed_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Audio mixing (concat) failed:\n{result.stderr}")

    if not os.path.exists(mixed_path) or os.path.getsize(mixed_path) == 0:
        raise RuntimeError("Audio mixing produced empty output")

    # Optionally mix with background audio from original
    if keep_bg and original_audio_path and os.path.exists(original_audio_path):
        final_path = os.path.join(temp_dir, "final_audio.wav")
        bg_path = os.path.join(temp_dir, "background.wav")
        cmd = ["ffmpeg", "-y", "-i", original_audio_path,
               "-vn", "-ac", "2", "-ar", "44100", bg_path]
        subprocess.run(cmd, capture_output=True, text=True)

        if os.path.exists(bg_path):
            cmd = [
                "ffmpeg", "-y",
                "-i", mixed_path,
                "-i", bg_path,
                "-filter_complex",
                "[0:a]volume=1.0[voice];[1:a]volume=0.15[bg];"
                "[voice][bg]amix=inputs=2:duration=longest:normalize=0[a]",
                "-map", "[a]", "-ac", "2", "-ar", "44100",
                final_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0 and os.path.exists(final_path):
                # Clean up intermediate files to save disk on long videos
                for p in [mixed_path, bg_path]:
                    if os.path.exists(p):
                        os.remove(p)
                return final_path

        return mixed_path
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
        return mixed_path


# ---------------------------------------------------------------------------
# Step 6: Mux dubbed audio with original video
# ---------------------------------------------------------------------------

def mux_video_audio(video_path: str, audio_path: str, output_path: str,
                    burn_subtitles: bool = False, srt_path: str = None,
                    extend_video: bool = False) -> float:
    """Replace video's audio with dubbed audio, optionally burn subtitles.
    
    If extend_video=True: extends the video by freeze-framing the last frame
    to match the audio duration. The video is NOT cut short.
    Returns the duration of the output file."""
    print(f"  [final] Muxing video with dubbed audio...")

    # Get video and audio durations
    video_dur_result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True
    )
    video_dur = float(video_dur_result.stdout.strip() or "0")
    
    audio_dur_result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True
    )
    audio_dur = float(audio_dur_result.stdout.strip() or "0")
    
    need_extend = extend_video and audio_dur > video_dur + 0.1
    
    if need_extend:
        extra_dur = audio_dur - video_dur
        print(f"        Video: {video_dur:.1f}s, Audio: {audio_dur:.1f}s → extending video by {extra_dur:.1f}s")
        
        # Strategy: extract last frame, create a frozen frame video segment,
        # concat with original video, then mux audio
        # Use tpad filter which freezes the last frame for extra_dur seconds
        
        if burn_subtitles and srt_path and os.path.exists(srt_path):
            escaped_srt = srt_path.replace("'", r"'\''")
            # tpad=stop_mode=clone:stop_duration=X extends the last frame
            vf = f"subtitles='{escaped_srt}',tpad=stop_mode=clone:stop_duration={extra_dur:.3f}"
            cmd = ["ffmpeg", "-y", "-i", video_path, "-i", audio_path,
                   "-map", "0:v", "-map", "1:a",
                   "-c:v", "libx264", "-crf", "20",
                   "-vf", vf,
                   "-c:a", "aac", "-b:a", "192k",
                   output_path]
        else:
            # No subtitles — use tpad to extend, copy audio codec
            cmd = ["ffmpeg", "-y", "-i", video_path, "-i", audio_path,
                   "-map", "0:v", "-map", "1:a",
                   "-c:v", "libx264", "-crf", "20",
                   "-vf", f"tpad=stop_mode=clone:stop_duration={extra_dur:.3f}",
                   "-c:a", "aac", "-b:a", "192k",
                   output_path]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            # Fallback without tpad
            print(f"        tpad failed, trying alternate approach...")
            cmd_fallback = ["ffmpeg", "-y", "-i", video_path, "-i", audio_path,
                            "-map", "0:v", "-map", "1:a",
                            "-c:v", "libx264", "-crf", "20",
                            "-c:a", "aac", "-b:a", "192k",
                            output_path]
            result = subprocess.run(cmd_fallback, capture_output=True, text=True)
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
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            cmd_fallback = ["ffmpeg", "-y", "-i", video_path, "-i", audio_path,
                            "-map", "0:v", "-map", "1:a",
                            "-c:v", "libx264", "-crf", "20",
                            "-c:a", "aac", "-b:a", "192k",
                            output_path]
            result2 = subprocess.run(cmd_fallback, capture_output=True, text=True)
            if result2.returncode != 0:
                raise RuntimeError(f"FFmpeg muxing failed:\n{result2.stderr}")
    
    # Return output duration
    out_dur_result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", output_path],
        capture_output=True, text=True
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
) -> dict:
    """
    Main function to dub a video.
    Supports checkpoint/resume: if job_dir is provided, intermediate results
    are saved. If resume=True and a checkpoint exists, continues from the
    last completed stage.

    Multi-speaker: if multi_speaker=True, runs diarization to detect speakers,
    assigns each a distinct voice from the VOICE_POOL. num_speakers can force
    a specific count. speaker_voices can override auto-assignment
    (mapping speaker_id -> voice_name).

    progress_callback(stage, message, sub_progress, sub_total):
      - stage: 1-7 or "done"
      - message: human-readable status
      - sub_progress: current item within stage (e.g. 45 of 425 translated)
      - sub_total: total items in this stage
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

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

            transcription = transcribe_audio(audio_wav, model_size,
                                             progress_callback=transcribe_progress)
            segments = transcription["segments"]
            source_lang = transcription["source_lang"]

            if not segments:
                raise RuntimeError("No speech detected in the video.")

            # --- Multi-speaker diarization ---
            if multi_speaker:
                log(2, f"  [2.5] Detecting speakers in audio...", 0, 0)

                def diarize_progress(msg):
                    log(2, msg, 0, 1)

                diarized = diarize_audio(audio_wav, num_speakers=num_speakers,
                                         progress_callback=diarize_progress)
                segments = assign_speakers_to_segments(segments, diarized)

                # Assign voices to speakers
                detected_speakers = sorted(set(s["speaker"] for s in segments))
                num_detected = len(detected_speakers)
                log(2, f"Found {num_detected} speakers: {detected_speakers}", num_detected, num_detected)

                if speaker_voices is None:
                    speaker_voices = {}
                    for spk_id in detected_speakers:
                        speaker_voices[spk_id] = get_voice_for_speaker(
                            spk_id, num_detected, target_lang)
                    log(2, f"Voice assignments:", num_detected, num_detected)
                    for spk_id in detected_speakers:
                        log(2, f"  Speaker {spk_id} → {speaker_voices[spk_id]}")

            log(2, f"Transcribed {len(segments)} segments (language: {source_lang})",
                total_duration, total_duration)

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

        # --- Stage 3: Translate ---
        if ckpt is None or ckpt["stage"] < 3:
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
            else:
                log(3, f"⚠ Failed to extract reference audio, will use edge-tts fallback", 1, 1)
                use_voice_cloning = False

        # --- Stage 4: Generate TTS ---
        if ckpt is None or ckpt["stage"] < 4:
            mode_str = "voice cloning" if use_voice_cloning else "Edge-TTS"
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
                                   "anullsrc=r=24000:cl=mono", "-t", "0.3",
                                   "-c:a", "libmp3lame", ts["audio_path"]]
                    subprocess.run(silence_cmd, capture_output=True)
                    missing += 1
            if missing > 0:
                print(f"        Generated {missing} silence clips for missing TTS")

        # Generate SRT
        srt_path = None
        if generate_srt_file:
            srt_path = os.path.splitext(output_path)[0] + ".srt"
            generate_srt(tts_segments, srt_path, use_translated=True)
            log(4, f"        Saved subtitles: {srt_path}")

        # --- Stage 5: Build dubbed audio ---
        if ckpt is None or ckpt["stage"] < 5:
            log(5, f"  [5/5] Building dubbed audio track...", 0, 1)
            bg_audio_path = audio_wav if keep_background else None
            final_audio = build_dubbed_audio(
                tts_segments, total_duration, temp_dir,
                keep_bg=keep_background,
                original_audio_path=bg_audio_path,
                extend_video=extend_video,
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

        # --- Stage 6: Mux ---
        log(6, f"  [final] Muxing video with dubbed audio...", 0, 1)
        out_dur = mux_video_audio(video_path, final_audio, output_path,
                        burn_subtitles=burn_subtitles, srt_path=srt_path,
                        extend_video=extend_video)
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
        "source_language": source_lang,
        "target_language": target_lang,
        "voice": voice,
        "segments_count": len(segments),
        "elapsed_seconds": round(elapsed, 1),
        "voice_cloned": use_voice_cloning,
        "video_extended": extend_video,
    }
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
                        help="Clone original speaker's voice (uses HuggingFace XTTS). "
                             "Instead of synthetic TTS, uses the original speaker's voice "
                             "to speak the translated text.")
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
    )
    print(f"\n📊 Summary: {json.dumps(result, indent=2, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
