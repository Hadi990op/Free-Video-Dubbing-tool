#!/usr/bin/env python3
"""
Kokoro TTS Engine — SOTA lightweight TTS for the dubbing pipeline.

Kokoro-82M is a 82M parameter TTS model that:
  - Runs entirely on CPU with ~1.1GB RAM
  - Produces natural, expressive speech (comparable to ElevenLabs quality)
  - Supports 10+ languages (en, es, fr, hi, it, pt, zh, ja, + British English)
  - Generates 24kHz audio at ~5x real-time on a single CPU
  - Free and open source (Apache 2.0)

This module provides a clean interface for the dubbing pipeline:
  - Model loading/unloading via ModelManager
  - Language code mapping
  - Voice selection per language
  - Speed/rate control for timing alignment
  - Batch generation with progress tracking
"""

import os
import sys
import tempfile
import subprocess
import gc
from pathlib import Path

# Kokoro language code mapping
# Our target_lang → Kokoro lang_code
KOKORO_LANG_MAP = {
    "en": "a",       # American English
    "es": "e",       # Spanish
    "fr": "f",       # French
    "hi": "h",       # Hindi
    "it": "i",       # Italian
    "pt": "p",       # Portuguese (Brazil)
    "zh": "z",       # Chinese (Mandarin)
    "ja": "j",       # Japanese
    # British English variants for variety
    "en_gb": "b",
}

# Default voices per language (best quality voices from Kokoro's set)
KOKORO_DEFAULT_VOICES = {
    "en": "af_heart",      # Female, warm — American English
    "en_gb": "bf_emma",    # Female, British English
    "es": "ef_dora",       # Female, Spanish
    "fr": "ff_siwis",      # Female, French
    "hi": "hf_alpha",      # Female, Hindi
    "it": "if_sara",       # Female, Italian
    "pt": "pf_dora",       # Female, Portuguese
    "zh": "zf_xiaobei",    # Female, Chinese
    "ja": "jf_alpha",      # Female, Japanese
}

# Voice pools for multi-speaker (alternating male/female)
KOKORO_VOICE_POOL = {
    "en": ["af_heart", "am_adam", "af_bella", "am_michael", "af_sky", "am_eric"],
    "en_gb": ["bf_emma", "bm_george", "bf_alice", "bm_finn"],
    "es": ["ef_dora", "em_alex", "em_santa"],
    "fr": ["ff_siwis"],
    "hi": ["hf_alpha", "hf_beta", "hm_omega", "hm_psi"],
    "it": ["if_sara", "im_nicola"],
    "pt": ["pf_dora", "pm_alex"],
    "zh": ["zf_xiaobei", "zf_xiaoni", "zm_yunjian", "zm_yunxi"],
    "ja": ["jf_alpha", "jf_gongitsuhime", "jf_nezumi", "jm_kumo"],
}

# Languages Kokoro does NOT support — fall back to edge-tts for these
KOKORO_UNSUPPORTED = {
    "ar", "tr", "ru", "de", "nl", "pl", "sv", "da", "fi", "no",
    "ko", "th", "vi", "id", "ms", "bn", "ta", "te", "mr", "gu",
    "pa", "ml", "kn", "or", "uk", "cs", "el", "he", "fa", "sw",
    "fil", "ro", "hu", "sk", "bg", "hr", "lt", "lv", "sl",
}

# Cache the pipeline to avoid reloading
_kokoro_pipeline = None
_kokoro_lang_code = None


def is_supported(target_lang: str) -> bool:
    """Check if Kokoro supports the target language."""
    return target_lang in KOKORO_LANG_MAP and target_lang not in KOKORO_UNSUPPORTED


def get_voice_for_speaker(speaker_id: int, num_speakers: int,
                          target_lang: str) -> str:
    """Get a Kokoro voice for a speaker in a multi-speaker video."""
    pool = KOKORO_VOICE_POOL.get(target_lang, KOKORO_VOICE_POOL.get("en", ["af_heart"]))
    return pool[speaker_id % len(pool)]


def get_default_voice(target_lang: str) -> str:
    """Get the default Kokoro voice for a language."""
    return KOKORO_DEFAULT_VOICES.get(target_lang, "af_heart")


def _load_kokoro(lang_code: str):
    """Load the Kokoro pipeline for a given language code."""
    from kokoro import KPipeline
    return KPipeline(lang_code=lang_code)


def generate_speech(text: str, out_path: str, target_lang: str,
                    voice: str = None, speed: float = 1.0) -> bool:
    """Generate speech using Kokoro TTS.
    
    Args:
        text: Text to synthesize
        out_path: Output audio file path (will be 24kHz mono WAV)
        target_lang: Target language code (e.g., 'en', 'es', 'hi')
        voice: Kokoro voice name (e.g., 'af_heart'). If None, uses default.
        speed: Speed multiplier (1.0 = normal, 1.5 = faster, 0.7 = slower)
        
    Returns:
        True on success, False on failure
    """
    global _kokoro_pipeline, _kokoro_lang_code

    if not is_supported(target_lang):
        return False

    text = text.strip()
    if not text:
        return False
    # Kokoro can handle long text but let's cap to avoid extreme generation times
    if len(text) > 1000:
        text = text[:1000]

    lang_code = KOKORO_LANG_MAP[target_lang]
    if voice is None:
        voice = get_default_voice(target_lang)

    # Load pipeline via model manager (swaps out other models)
    from model_manager import _model_manager

    model_name = f"kokoro-{lang_code}"
    if _kokoro_pipeline is None or _kokoro_lang_code != lang_code:
        _kokoro_pipeline = _model_manager.load_model(model_name, lambda: _load_kokoro(lang_code))
        _kokoro_lang_code = lang_code

    try:
        # Generate audio
        pipeline = _kokoro_pipeline
        audio_chunks = []
        for gs, ps, audio in pipeline(text, voice=voice, speed=speed):
            audio_chunks.append(audio)

        if not audio_chunks:
            return False

        # Concatenate audio chunks and save as WAV
        import numpy as np
        import soundfile as sf
        full_audio = np.concatenate(audio_chunks)

        # Save to temp WAV first, then convert to target format
        tmp_wav = out_path + ".raw.wav"
        sf.write(tmp_wav, full_audio, 24000)

        # Convert to 48kHz stereo (matching the rest of the pipeline)
        acodec = "libmp3lame" if out_path.lower().endswith(".mp3") else "pcm_s16le"
        r = subprocess.run([
            "ffmpeg", "-y", "-i", tmp_wav,
            "-vn", "-ac", "2", "-ar", "48000",
            "-c:a", acodec, out_path
        ], capture_output=True, text=True)

        # Clean up temp
        try:
            os.remove(tmp_wav)
        except OSError:
            pass

        return r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0

    except Exception as e:
        print(f"        [Kokoro] Error generating speech: {e!r}")
        # Reset pipeline on error
        _kokoro_pipeline = None
        _kokoro_lang_code = None
        return False


def cleanup():
    """Unload the Kokoro pipeline to free RAM."""
    global _kokoro_pipeline, _kokoro_lang_code
    _kokoro_pipeline = None
    _kokoro_lang_code = None
    gc.collect()
