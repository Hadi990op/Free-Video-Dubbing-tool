#!/usr/bin/env python3
"""
Intelligent Voice Manager — AI-powered speaker analysis and voice assignment.

Analyzes each speaker's original audio to determine:
  - Gender (male/female/child) based on fundamental frequency (F0)
  - Age category (adult/child/elderly) based on pitch range and spectral features
  - Emotional tone (neutral/excited/calm) based on pitch variance

Then assigns the best matching TTS voice:
  - For voice cloning: uses Chatterbox Multilingual V3 (ZeroGPU) with reference audio
  - For synthetic TTS: uses gender-appropriate Kokoro/Edge-TTS voices

F0-based gender classification thresholds (well-established in speech science):
  - Male:   F0 = 85-180 Hz (average ~120 Hz)
  - Female: F0 = 165-255 Hz (average ~210 Hz)
  - Child:  F0 = 250-400+ Hz (average ~300 Hz)
  - Elderly male: F0 = 90-160 Hz with more jitter
  - Elderly female: F0 = 150-220 Hz with more jitter
"""

import os
import sys
import tempfile
import subprocess
import numpy as np
from typing import Optional, Tuple, Dict, List


# ---------------------------------------------------------------------------
# Pitch Analysis (F0 estimation via autocorrelation)
# ---------------------------------------------------------------------------

def extract_f0(audio_path: str, sr: int = 16000) -> Tuple[float, float, float]:
    """Extract fundamental frequency statistics from audio.

    Uses simple autocorrelation-based F0 detection (no external deps needed).

    Returns: (median_f0, mean_f0, f0_stddev) in Hz.
    Returns (0, 0, 0) if no voice activity detected.
    """
    try:
        import librosa
    except ImportError:
        return _extract_f0_no_librosa(audio_path)

    try:
        y, sr = librosa.load(audio_path, sr=sr, mono=True)

        # Use pyin for robust F0 estimation
        f0, voiced_flag, voiced_probs = librosa.pyin(
            y,
            fmin=65,      # Below male range
            fmax=500,     # Above child range
            sr=sr,
            frame_length=2048,
            hop_length=512,
        )

        # Only use voiced frames
        voiced_f0 = f0[voiced_flag & ~np.isnan(f0)]
        if len(voiced_f0) == 0:
            return (0.0, 0.0, 0.0)

        median_f0 = float(np.median(voiced_f0))
        mean_f0 = float(np.mean(voiced_f0))
        std_f0 = float(np.std(voiced_f0))

        return (median_f0, mean_f0, std_f0)

    except Exception as e:
        print(f"        [VoiceManager] F0 extraction error: {e!r}")
        return (0.0, 0.0, 0.0)


def _extract_f0_no_librosa(audio_path: str) -> Tuple[float, float, float]:
    """Fallback F0 extraction using only numpy — less accurate but works without librosa."""
    try:
        import soundfile as sf
        y, sr = sf.read(audio_path)
        if y.ndim > 1:
            y = y[:, 0]

        # Simple autocorrelation-based F0 detection
        frame_size = int(0.04 * sr)  # 40ms frames
        hop_size = int(0.02 * sr)    # 20ms hop

        f0_values = []
        for i in range(0, len(y) - frame_size, hop_size):
            frame = y[i:i + frame_size]
            # Simple energy threshold
            energy = np.sum(frame ** 2) / frame_size
            if energy < 0.001:
                continue

            # Autocorrelation
            autocorr = np.correlate(frame, frame, mode='full')
            autocorr = autocorr[len(autocorr) // 2:]

            # Find first significant peak (F0)
            min_lag = int(sr / 500)   # max F0 = 500 Hz
            max_lag = int(sr / 65)    # min F0 = 65 Hz

            if max_lag >= len(autocorr):
                continue

            peak_idx = np.argmax(autocorr[min_lag:max_lag]) + min_lag
            if autocorr[peak_idx] > 0.3 * autocorr[0]:
                f0 = sr / peak_idx
                if 65 <= f0 <= 500:
                    f0_values.append(f0)

        if not f0_values:
            return (0.0, 0.0, 0.0)

        f0_arr = np.array(f0_values)
        return (float(np.median(f0_arr)), float(np.mean(f0_arr)), float(np.std(f0_arr)))

    except Exception:
        return (0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# Gender/Age Classification
# ---------------------------------------------------------------------------

# Classification thresholds based on speech science research
# Reference: Baken & Orlikoff (2000), "Clinical Measurement of Speech and Voice"
F0_THRESHOLDS = {
    "child":        (250, 500),    # High F0
    "female":       (165, 260),    # Mid-high F0
    "elderly_female": (145, 220),  # Slightly lower with more jitter
    "male":         (85, 180),     # Low F0
    "elderly_male":  (80, 165),    # Slightly lower with more jitter
}


def classify_voice(median_f0: float, mean_f0: float, std_f0: float) -> Dict:
    """Classify a speaker's gender and age from F0 statistics.

    Returns dict with:
        - gender: 'male', 'female', or 'child'
        - age: 'adult', 'child', or 'elderly'
        - confidence: 0.0-1.0
        - f0_median: Hz
    """
    result = {
        "gender": "male",     # default
        "age": "adult",
        "confidence": 0.5,
        "f0_median": median_f0,
        "f0_mean": mean_f0,
        "f0_std": std_f0,
    }

    if median_f0 == 0:
        # No voice detected — default to male adult
        result["confidence"] = 0.1
        return result

    # High jitter (std_f0) suggests elderly voice
    high_jitter = std_f0 > 25

    # Child: very high F0
    if median_f0 >= 250:
        result["gender"] = "child"
        result["age"] = "child"
        result["confidence"] = min(0.95, 0.7 + (median_f0 - 250) / 500)
        return result

    # Female range
    if median_f0 >= 165:
        result["gender"] = "female"
        if high_jitter and median_f0 < 200:
            result["age"] = "elderly"
            result["confidence"] = 0.70
        else:
            result["age"] = "adult"
            result["confidence"] = min(0.90, 0.65 + (median_f0 - 165) / 200)
        return result

    # Male range (85-165 Hz)
    result["gender"] = "male"
    if high_jitter and median_f0 < 120:
        result["age"] = "elderly"
        result["confidence"] = 0.70
    else:
        result["age"] = "adult"
        result["confidence"] = min(0.90, 0.65 + (165 - median_f0) / 200)
    return result


# ---------------------------------------------------------------------------
# Voice Assignment
# ---------------------------------------------------------------------------

# Gender-aware Edge-TTS voice mapping for Hindi
# Each entry: (voice_id, gender, age, description)
HINDI_VOICES_BY_GENDER = {
    "male_adult": [
        "hi-IN-MadhurNeural",       # Adult male, warm
        "en-IN-PrabhatNeural",      # Adult male, clear
        "bn-IN-BashkarNeural",      # Adult male, Bengali-Indian
        "mr-IN-ManoharNeural",      # Adult male, Marathi
    ],
    "male_elderly": [
        "hi-IN-MadhurNeural",       # Same male, rate-adjusted to sound older
        "en-IN-PrabhatNeural",
    ],
    "female_adult": [
        "hi-IN-SwaraNeural",        # Adult female, warm
        "en-IN-NeerjaNeural",      # Adult female, clear
        "bn-IN-TanishaaNeural",    # Adult female, Bengali-Indian
        "mr-IN-AarohiNeural",      # Adult female, Marathi
    ],
    "female_elderly": [
        "hi-IN-SwaraNeural",
        "en-IN-NeerjaNeural",
    ],
    "child_male": [
        "hi-IN-MadhurNeural",      # Use male voice with rate +10%
    ],
    "child_female": [
        "hi-IN-SwaraNeural",       # Use female voice with rate +10%
    ],
}

# Kokoro Hindi voices by gender
KOKORO_HINDI_BY_GENDER = {
    "male_adult": ["hm_omega", "hm_psi"],
    "male_elderly": ["hm_omega"],
    "female_adult": ["hf_alpha", "hf_beta"],
    "female_elderly": ["hf_alpha"],
    "child_male": ["hm_psi"],       # Higher pitched male voice
    "child_female": ["hf_beta"],   # Higher pitched female voice
}

# English voice pool by gender (for en target)
ENGLISH_VOICES_BY_GENDER = {
    "male_adult": ["en-US-AndrewNeural", "en-US-BrianNeural", "en-US-ChristopherNeural", "en-US-EricNeural"],
    "male_elderly": ["en-US-ChristopherNeural", "en-GB-RyanNeural"],
    "female_adult": ["en-US-AriaNeural", "en-US-JennyNeural", "en-US-MichelleNeural", "en-US-EmmaNeural"],
    "female_elderly": ["en-US-MichelleNeural", "en-GB-SoniaNeural"],
    "child_male": ["en-US-AndrewNeural"],
    "child_female": ["en-US-AriaNeural"],
}

KOKORO_ENGLISH_BY_GENDER = {
    "male_adult": ["am_adam", "am_michael", "am_eric"],
    "male_elderly": ["am_adam"],
    "female_adult": ["af_heart", "af_bella", "af_sky"],
    "female_elderly": ["af_heart"],
    "child_male": ["am_eric"],
    "child_female": ["af_sky"],
}


def get_voice_by_profile(gender: str, age: str, target_lang: str,
                         speaker_index: int = 0) -> str:
    """Get the best Edge-TTS voice for a speaker based on detected gender/age.

    Args:
        gender: 'male', 'female', or 'child'
        age: 'adult', 'child', or 'elderly'
        target_lang: Language code (e.g., 'hi', 'en')
        speaker_index: Index for multi-speaker variety

    Returns: Edge-TTS voice ID (e.g., 'hi-IN-MadhurNeural')
    """
    profile_key = f"{gender}_{age}" if gender != "child" else f"child_{gender if gender != 'child' else 'male'}"

    # For children, use child_ prefix
    if gender == "child":
        profile_key = f"child_{'male' if age == 'child' else 'male'}"

    # Get voice pool for the target language
    if target_lang == "hi":
        pool = HINDI_VOICES_BY_GENDER.get(profile_key, HINDI_VOICES_BY_GENDER.get("male_adult"))
    elif target_lang == "en":
        pool = ENGLISH_VOICES_BY_GENDER.get(profile_key, ENGLISH_VOICES_BY_GENDER.get("male_adult"))
    else:
        # For other languages, fall back to the generic VOICE_POOL
        # (imported from dubber.py)
        from dubber import VOICE_POOL
        lang_pool = VOICE_POOL.get(target_lang, [])
        if lang_pool:
            # Try to pick a voice matching the gender
            # Voices in the pool alternate male/female
            if gender == "female":
                return lang_pool[1] if len(lang_pool) > 1 else lang_pool[0]
            elif gender == "child":
                return lang_pool[1] if len(lang_pool) > 1 else lang_pool[0]  # Higher voice
            else:
                return lang_pool[0]
        return "en-US-AriaNeural"

    return pool[speaker_index % len(pool)]


def get_kokoro_voice_by_profile(gender: str, age: str, target_lang: str,
                                speaker_index: int = 0) -> str:
    """Get the best Kokoro TTS voice for a speaker based on detected gender/age.

    Returns: Kokoro voice name (e.g., 'hm_omega' for Hindi male)
    """
    if gender == "child":
        profile_key = f"child_{'male' if age == 'child' else 'male'}"
    else:
        profile_key = f"{gender}_{age}"

    if target_lang == "hi":
        pool = KOKORO_HINDI_BY_GENDER.get(profile_key, KOKORO_HINDI_BY_GENDER.get("male_adult"))
    elif target_lang in ("en", "en_gb"):
        pool = KOKORO_ENGLISH_BY_GENDER.get(profile_key, KOKORO_ENGLISH_BY_GENDER.get("male_adult"))
    else:
        # Fall back to generic Kokoro voice pool
        try:
            import kokoro_tts
            pool = kokoro_tts.KOKORO_VOICE_POOL.get(target_lang, kokoro_tts.KOKORO_VOICE_POOL.get("en"))
            # Pick by gender (alternating male/female in the pool)
            if gender == "female":
                return pool[1] if len(pool) > 1 else pool[0]
            return pool[0]
        except ImportError:
            return "af_heart"

    return pool[speaker_index % len(pool)]


# ---------------------------------------------------------------------------
# Speaker Analysis (combines F0 extraction + classification)
# ---------------------------------------------------------------------------

def analyze_speaker(audio_path: str, speaker_label: str = "Speaker") -> Dict:
    """Analyze a speaker's audio to determine gender, age, and voice profile.

    Args:
        audio_path: Path to audio file containing the speaker's voice
        speaker_label: Label for logging

    Returns: Dict with:
        - gender: 'male' | 'female' | 'child'
        - age: 'adult' | 'child' | 'elderly'
        - confidence: 0.0-1.0
        - f0_median: Hz
        - f0_mean: Hz
        - f0_std: Hz
        - description: Human-readable description
    """
    median_f0, mean_f0, std_f0 = extract_f0(audio_path)
    profile = classify_voice(median_f0, mean_f0, std_f0)

    # Build human-readable description
    gender_map = {"male": "Male", "female": "Female", "child": "Child"}
    age_map = {"adult": "Adult", "child": "Child", "elderly": "Elderly"}
    gender_str = gender_map.get(profile["gender"], "Unknown")
    age_str = age_map.get(profile["age"], "Adult")

    profile["description"] = f"{speaker_label}: {gender_str} {age_str} (F0={median_f0:.0f}Hz, conf={profile['confidence']:.0%})"

    return profile


def analyze_all_speakers(speaker_ref_audios: Dict[int, str],
                         progress_callback=None) -> Dict[int, Dict]:
    """Analyze all speakers in a multi-speaker video.

    Args:
        speaker_ref_audios: Mapping of speaker_id -> reference audio path
        progress_callback: Optional callback(done, total, message)

    Returns: Mapping of speaker_id -> speaker profile dict
    """
    results = {}
    total = len(speaker_ref_audios)

    for i, (speaker_id, audio_path) in enumerate(speaker_ref_audios.items()):
        if not audio_path or not os.path.exists(audio_path):
            # Default to male adult if no audio
            results[speaker_id] = {
                "gender": "male",
                "age": "adult",
                "confidence": 0.1,
                "f0_median": 0,
                "f0_mean": 0,
                "f0_std": 0,
                "description": f"Speaker {speaker_id}: Unknown (no audio)",
            }
            continue

        label = f"Speaker {speaker_id}"
        profile = analyze_speaker(audio_path, label)
        print(f"        🎭 {profile['description']}")
        results[speaker_id] = profile

        if progress_callback:
            progress_callback(i + 1, total, profile["description"])

    return results


# ---------------------------------------------------------------------------
# TTS Rate Adjustment for Age
# ---------------------------------------------------------------------------

def get_rate_for_age(age: str, gender: str) -> str:
    """Get TTS rate adjustment to make a voice sound younger or older.

    Children speak faster with higher pitch.
    Elderly speak slower with slightly lower pitch.

    Returns: Edge-TTS rate string (e.g., '+10%', '-5%')
    """
    if age == "child":
        return "+15%"   # Faster for children
    elif age == "elderly":
        return "-10%"   # Slower for elderly
    return "+0%"        # Normal for adults
