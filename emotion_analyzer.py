#!/usr/bin/env python3
"""
Emotion & Prosody Analyzer — Extract emotional and prosodic features from
original speech segments and use them to guide TTS generation and post-processing.

This module makes dubbed audio sound like a professional artist dub:
  - Detects the EMOTION in each original speech segment (happy, sad, angry,
    afraid, surprised, neutral, etc.) using emotion2vec+ (a speech emotion
    recognition foundation model, free, MIT-like license).
  - Extracts PROSODY features: pitch (F0) contour, energy (RMS), speaking rate,
    pause patterns, and intensity dynamics.
  - Maps detected emotions to IndexTTS-2 emotion vectors and Chatterbox
    exaggeration values so the TTS engine reproduces the same feeling.
  - Returns per-segment "emotion profiles" that downstream stages use to:
      a) Choose the right emotion vector / exaggeration for voice cloning
      b) Post-process TTS output to match original pitch, energy, and rate

The result: the dubbed voice carries the same emotions, energy, and rhythm
as the original — making it nearly impossible to tell the video was dubbed.

All tools used here are FREE and open-source:
  - emotion2vec+ (MIT license, HuggingFace)
  - librosa (ISC license)
  - numpy / scipy (BSD license)
"""

import os
import sys
import tempfile
import subprocess
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Emotion taxonomy — unified across emotion2vec, IndexTTS-2, and Chatterbox
# ---------------------------------------------------------------------------

# emotion2vec+ 9-class output labels (in order of model output):
EMOTION2VEC_LABELS = [
    "angry", "happy", "neutral", "sad", "worried",
    "fearful", "surprised", "disgusted", "excited",
]

# Mapping from emotion2vec labels to IndexTTS-2 8 emotion vectors
# IndexTTS-2 vectors (from their demo): vec1-vec8 correspond to:
#   vec1=Happy, vec2=Angry, vec3=Sad, vec4=Afraid, vec5=Surprised,
#   vec6=Disgusted, vec7=Excited, vec8=Neutral (approximate)
# We map our detected emotion to the best matching vector with a weight 0.0-1.0
INDEXTTS_EMOTION_VECTORS = {
    "happy":     {"vec": 1, "default_weight": 0.7},
    "excited":   {"vec": 1, "default_weight": 0.9},   # also Happy vec, higher intensity
    "angry":     {"vec": 2, "default_weight": 0.8},
    "sad":       {"vec": 3, "default_weight": 0.7},
    "worried":   {"vec": 3, "default_weight": 0.5},   # close to Sad
    "fearful":   {"vec": 4, "default_weight": 0.7},
    "surprised": {"vec": 5, "default_weight": 0.7},
    "disgusted": {"vec": 6, "default_weight": 0.7},
    "neutral":   {"vec": 8, "default_weight": 0.3},
}

# Mapping from emotion to Chatterbox exaggeration value
# exaggeration=0.5 = neutral, 0.8 = expressive, 1.0 = exaggerated
CHATTERBOX_EXAGGERATION = {
    "neutral":   0.45,
    "happy":     0.65,
    "excited":   0.85,
    "surprised": 0.75,
    "angry":     0.80,
    "sad":       0.55,
    "worried":   0.50,
    "fearful":   0.70,
    "disgusted": 0.60,
}

# Mapping from emotion to Chatterbox temperature
# Higher temperature = more varied/expressive, lower = more monotone
CHATTERBOX_TEMPERATURE = {
    "neutral":   0.6,
    "happy":     0.8,
    "excited":   0.9,
    "surprised": 0.85,
    "angry":     0.85,
    "sad":       0.5,   # sad = more flat/monotone
    "worried":   0.55,
    "fearful":   0.75,
    "disgusted": 0.65,
}

# Emotion intensity display names for UI
EMOTION_DISPLAY = {
    "neutral":   "😐 Neutral",
    "happy":     "😊 Happy",
    "excited":   "🤩 Excited",
    "surprised": "😮 Surprised",
    "angry":     "😠 Angry",
    "sad":       "😢 Sad",
    "worried":   "😟 Worried",
    "fearful":   "😨 Fearful",
    "disgusted": "🤢 Disgusted",
}


# ---------------------------------------------------------------------------
# Emotion profile data structure
# ---------------------------------------------------------------------------

@dataclass
class EmotionProfile:
    """Emotional and prosodic analysis of a single speech segment."""
    emotion: str = "neutral"          # Primary detected emotion
    emotion_confidence: float = 0.0  # Confidence (0.0-1.0)
    emotion_scores: Dict[str, float] = field(default_factory=dict)  # All scores

    # Prosody features
    pitch_mean: float = 0.0          # Mean F0 in Hz
    pitch_std: float = 0.0           # F0 standard deviation (pitch variation)
    pitch_range: float = 0.0         # F0 range (max - min)
    energy_mean: float = 0.0          # Mean RMS energy
    energy_std: float = 0.0          # Energy variation
    speaking_rate: float = 0.0        # Syllables per second (approximate)
    intensity: float = 0.5            # Overall intensity (0.0-1.0, normalized)

    # Derived TTS parameters (for emotion-aware generation)
    indextts_vec: int = 8             # IndexTTS-2 emotion vector index (1-8)
    indextts_weight: float = 0.3      # Emotion vector weight (0.0-1.0)
    chatterbox_exaggeration: float = 0.5
    chatterbox_temperature: float = 0.8

    # Prosody transfer targets (for post-processing)
    target_pitch_shift: float = 0.0   # Semitones to shift (relative to TTS output)
    target_energy_factor: float = 1.0  # Energy multiplier
    target_rate_factor: float = 1.0    # Rate multiplier (for atempo)

    # Metadata
    segment_index: int = -1
    start: float = 0.0
    end: float = 0.0
    duration: float = 0.0

    def to_dict(self) -> dict:
        return {
            "emotion": self.emotion,
            "emotion_confidence": round(self.emotion_confidence, 3),
            "emotion_display": EMOTION_DISPLAY.get(self.emotion, self.emotion),
            "pitch_mean": round(self.pitch_mean, 1),
            "pitch_std": round(self.pitch_std, 1),
            "energy_mean": round(self.energy_mean, 4),
            "intensity": round(self.intensity, 3),
            "speaking_rate": round(self.speaking_rate, 2),
            "indextts_vec": self.indextts_vec,
            "indextts_weight": round(self.indextts_weight, 3),
            "chatterbox_exaggeration": round(self.chatterbox_exaggeration, 3),
            "chatterbox_temperature": round(self.chatterbox_temperature, 3),
            "target_pitch_shift": round(self.target_pitch_shift, 2),
            "target_energy_factor": round(self.target_energy_factor, 3),
            "target_rate_factor": round(self.target_rate_factor, 3),
            "segment_index": self.segment_index,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
        }


# ---------------------------------------------------------------------------
# emotion2vec+ model (lazy-loaded singleton)
# ---------------------------------------------------------------------------

_emotion2vec_model = None
_emotion2vec_failed = False
_emotion2vec_lock = None

try:
    import threading
    _emotion2vec_lock = threading.Lock()
except ImportError:
    pass


def _get_emotion2vec():
    """Lazily load the emotion2vec+ model. Returns the model or None."""
    global _emotion2vec_model, _emotion2vec_failed
    if _emotion2vec_model is not None:
        return _emotion2vec_model
    if _emotion2vec_failed:
        return None
    if _emotion2vec_lock:
        with _emotion2vec_lock:
            if _emotion2vec_model is not None:
                return _emotion2vec_model
            if _emotion2vec_failed:
                return None
            try:
                # emotion2vec+ via the ModelScope or HuggingFace transformers interface
                # Try the funasr interface first (most reliable for CPU)
                try:
                    from funasr import AutoModel
                    print("        Loading emotion2vec+ model (speech emotion recognition)...")
                    model = AutoModel(
                        model="iic/emotion2vec_plus_large",
                        trust_remote_code=True,
                        device="cpu",
                    )
                    _emotion2vec_model = model
                    print("        ✓ emotion2vec+ ready")
                    return _emotion2vec_model
                except ImportError:
                    pass

                # Fallback: try transformers directly
                try:
                    from transformers import AutoModelForAudioClassification, Wav2Vec2FeatureExtractor
                    print("        Loading emotion2vec+ via transformers...")
                    model_id = "emotion2vec/emotion2vec_plus_large"
                    model = AutoModelForAudioClassification.from_pretrained(model_id)
                    extractor = Wav2Vec2FeatureExtractor.from_pretrained(model_id)
                    _emotion2vec_model = (model, extractor)
                    print("        ✓ emotion2vec+ ready (transformers)")
                    return _emotion2vec_model
                except Exception:
                    pass

                print("        ⚠ emotion2vec+ not available (funasr/transformers). Will use prosody-only analysis.")
                _emotion2vec_failed = True
                return None
            except Exception as e:
                print(f"        ⚠ Could not load emotion2vec+ ({e!r}). Will use prosody-only analysis.")
                _emotion2vec_failed = True
                return None


def _classify_emotion_emotion2vec(audio_path: str) -> Optional[Tuple[str, float, Dict[str, float]]]:
    """Classify emotion using emotion2vec+.
    Returns (emotion_label, confidence, all_scores) or None."""
    model = _get_emotion2vec()
    if model is None:
        return None
    try:
        import soundfile as sf
        import librosa

        # Load audio at 16kHz mono (emotion2vec expects 16kHz)
        audio, sr = sf.read(audio_path)
        if len(audio.shape) > 1:
            audio = audio[:, 0]
        if sr != 16000:
            audio = librosa.resample(audio.astype(np.float32), orig_sr=sr, target_sr=16000)
        audio = audio.astype(np.float32)

        # funasr interface
        if hasattr(model, 'generate'):
            res = model.generate(audio, sampling_rate=16000, extract_embedding=False)
            if res and isinstance(res, list) and len(res) > 0:
                labels = res[0].get("labels", EMOTION2VEC_LABELS)
                scores_list = res[0].get("scores", [])
                if scores_list:
                    # Labels may be bilingual (e.g., "开心/happy") — extract English part
                    def _normalize_label(l):
                        l = l.lower().strip()
                        if "/" in l:
                            l = l.split("/")[-1]  # take the English part
                        return l
                    scores = {_normalize_label(labels[i]): float(scores_list[i])
                              for i in range(min(len(labels), len(scores_list)))}
                    # Find top emotion
                    top_emotion = max(scores, key=scores.get)
                    confidence = scores[top_emotion]
                    return top_emotion, confidence, scores

        # transformers interface
        if isinstance(model, tuple):
            clf, extractor = model
            import torch
            inputs = extractor(audio, sampling_rate=16000, return_tensors="pt")
            with torch.no_grad():
                logits = clf(**inputs).logits
            probs = torch.softmax(logits, dim=-1).squeeze().tolist()
            labels = [clf.config.id2label[i].lower() for i in range(len(probs))]
            scores = {labels[i]: probs[i] for i in range(len(probs))}
            top_emotion = max(scores, key=scores.get)
            confidence = scores[top_emotion]
            return top_emotion, confidence, scores

    except Exception as e:
        print(f"        ⚠ emotion2vec classification failed: {e!r}")
    return None


def _classify_emotion_prosody(pitch_mean, pitch_std, energy_mean, speaking_rate):
    """Fallback: classify emotion from prosody features alone.
    Less accurate than emotion2vec but works without any model download."""
    scores = {}

    # High pitch + high energy + fast rate → excited/happy
    if pitch_mean > 180 and energy_mean > 0.05 and speaking_rate > 5:
        scores["excited"] = 0.6
        scores["happy"] = 0.3
    # High pitch + high energy → surprised
    elif pitch_mean > 170 and energy_mean > 0.04:
        scores["surprised"] = 0.5
        scores["happy"] = 0.3
    # Low pitch + low energy + slow rate → sad
    elif pitch_mean < 120 and energy_mean < 0.02:
        scores["sad"] = 0.5
        scores["worried"] = 0.3
    # High pitch variation + high energy → angry
    elif pitch_std > 40 and energy_mean > 0.05:
        scores["angry"] = 0.5
        scores["fearful"] = 0.3
    # Low energy + medium pitch → worried
    elif energy_mean < 0.03:
        scores["worried"] = 0.4
        scores["neutral"] = 0.3
    else:
        scores["neutral"] = 0.6

    # Normalize
    total = sum(scores.values()) or 1
    scores = {k: v / total for k, v in scores.items()}

    top_emotion = max(scores, key=scores.get)
    return top_emotion, scores[top_emotion], scores


# ---------------------------------------------------------------------------
# Prosody extraction (librosa)
# ---------------------------------------------------------------------------

def extract_prosody(audio_path: str) -> dict:
    """Extract prosody features from an audio file using librosa.

    Returns dict with:
      pitch_mean, pitch_std, pitch_range — F0 statistics (Hz)
      energy_mean, energy_std — RMS energy
      speaking_rate — estimated syllables/sec
      intensity — normalized intensity (0.0-1.0)
    """
    try:
        import librosa
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")

            # Load at 16kHz mono
            y, sr = librosa.load(audio_path, sr=16000, mono=True)

            if len(y) < int(0.1 * sr):  # less than 0.1s
                return {
                    "pitch_mean": 0, "pitch_std": 0, "pitch_range": 0,
                    "energy_mean": 0, "energy_std": 0,
                    "speaking_rate": 0, "intensity": 0.5,
                }

            duration = len(y) / sr

            # --- Pitch (F0) using pyin ---
            f0, voiced_flag, voiced_prob = librosa.pyin(
                y, fmin=60, fmax=500, sr=sr
            )
            f0_voiced = f0[~np.isnan(f0)] if f0 is not None else np.array([])

            if len(f0_voiced) > 0:
                pitch_mean = float(np.mean(f0_voiced))
                pitch_std = float(np.std(f0_voiced))
                pitch_range = float(np.max(f0_voiced) - np.min(f0_voiced))
            else:
                pitch_mean = 150.0  # fallback
                pitch_std = 0.0
                pitch_range = 0.0

            # --- Energy (RMS) ---
            rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
            energy_mean = float(np.mean(rms))
            energy_std = float(np.std(rms))

            # --- Speaking rate (approximate via onset detection) ---
            # Count spectral flux onsets as proxy for syllable rate
            onset_frames = librosa.onset.onset_detect(
                y=y, sr=sr, hop_length=512, units="frames"
            )
            if duration > 0:
                speaking_rate = len(onset_frames) / duration
            else:
                speaking_rate = 0.0

            # --- Intensity (normalized energy, 0.0-1.0) ---
            # Normalize against a reference (typical speech RMS ~0.05)
            intensity = min(1.0, energy_mean / 0.08)

            return {
                "pitch_mean": pitch_mean,
                "pitch_std": pitch_std,
                "pitch_range": pitch_range,
                "energy_mean": energy_mean,
                "energy_std": energy_std,
                "speaking_rate": speaking_rate,
                "intensity": intensity,
            }
    except Exception as e:
        print(f"        ⚠ Prosody extraction failed: {e!r}")
        return {
            "pitch_mean": 150, "pitch_std": 0, "pitch_range": 0,
            "energy_mean": 0.03, "energy_std": 0,
            "speaking_rate": 4.0, "intensity": 0.5,
        }


# ---------------------------------------------------------------------------
# Segment extraction helper
# ---------------------------------------------------------------------------

def _extract_segment_audio(audio_path: str, start: float, end: float,
                            temp_dir: str, idx: int) -> Optional[str]:
    """Extract a segment from an audio file for analysis."""
    duration = end - start
    if duration < 0.2:
        return None
    out_path = os.path.join(temp_dir, f"seg_{idx:05d}.wav")
    try:
        cmd = [
            "ffmpeg", "-y", "-i", audio_path,
            "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
            "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "pcm_s16le", out_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 100:
            return out_path
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Main analysis function — analyze all segments
# ---------------------------------------------------------------------------

def analyze_segments(
    audio_path: str,
    segments: List[dict],
    temp_dir: str = None,
    progress_callback=None,
    use_emotion2vec: bool = True,
) -> List[EmotionProfile]:
    """Analyze emotion and prosody for each speech segment.

    Args:
        audio_path: Path to the original audio (full audio or isolated vocals)
        segments: List of segments with 'start' and 'end' keys
        temp_dir: Directory for temporary audio clips (default: system temp)
        progress_callback: callback(done, total, message)
        use_emotion2vec: Whether to try loading emotion2vec model (needs ~500MB download)

    Returns:
        List of EmotionProfile objects, one per segment
    """
    if not segments:
        return []

    if temp_dir is None:
        temp_dir = tempfile.mkdtemp(prefix="emotion_")
    else:
        os.makedirs(temp_dir, exist_ok=True)

    total = len(segments)
    profiles = []

    # Try to load emotion2vec once (lazy)
    if use_emotion2vec:
        _get_emotion2vec()

    for i, seg in enumerate(segments):
        start = seg.get("start", 0)
        end = seg.get("end", start + 1)

        profile = EmotionProfile(
            segment_index=i,
            start=start,
            end=end,
            duration=end - start,
        )

        # Extract this segment's audio
        seg_audio = _extract_segment_audio(audio_path, start, end, temp_dir, i)

        if seg_audio and os.path.exists(seg_audio):
            # Extract prosody
            prosody = extract_prosody(seg_audio)
            profile.pitch_mean = prosody["pitch_mean"]
            profile.pitch_std = prosody["pitch_std"]
            profile.pitch_range = prosody["pitch_range"]
            profile.energy_mean = prosody["energy_mean"]
            profile.energy_std = prosody["energy_std"]
            profile.speaking_rate = prosody["speaking_rate"]
            profile.intensity = prosody["intensity"]

            # Classify emotion
            emotion_result = None
            if use_emotion2vec:
                emotion_result = _classify_emotion_emotion2vec(seg_audio)

            if emotion_result:
                profile.emotion, profile.emotion_confidence, profile.emotion_scores = emotion_result
            else:
                # Fallback: prosody-based classification
                emo, conf, scores = _classify_emotion_prosody(
                    profile.pitch_mean, profile.pitch_std,
                    profile.energy_mean, profile.speaking_rate
                )
                profile.emotion = emo
                profile.emotion_confidence = conf
                profile.emotion_scores = scores

            # Map to TTS parameters
            vec_info = INDEXTTS_EMOTION_VECTORS.get(profile.emotion,
                                                     INDEXTTS_EMOTION_VECTORS["neutral"])
            profile.indextts_vec = vec_info["vec"]
            # Scale weight by confidence and intensity
            profile.indextts_weight = vec_info["default_weight"] * (0.5 + 0.5 * profile.emotion_confidence)

            profile.chatterbox_exaggeration = CHATTERBOX_EXAGGERATION.get(profile.emotion, 0.5)
            profile.chatterbox_temperature = CHATTERBOX_TEMPERATURE.get(profile.emotion, 0.8)

            # Adjust exaggeration by intensity
            profile.chatterbox_exaggeration = min(1.0,
                profile.chatterbox_exaggeration * (0.7 + 0.3 * profile.intensity))

            # Clean up temp file
            try:
                os.remove(seg_audio)
            except OSError:
                pass
        else:
            profile.emotion = "neutral"
            profile.emotion_confidence = 0.3

        profiles.append(profile)

        if progress_callback:
            progress_callback(i + 1, total,
                f"Analyzing emotion... {i + 1}/{total} segments "
                f"(current: {EMOTION_DISPLAY.get(profile.emotion, profile.emotion)})")

    return profiles


# ---------------------------------------------------------------------------
# Aggregate statistics for a full video
# ---------------------------------------------------------------------------

def summarize_emotions(profiles: List[EmotionProfile]) -> dict:
    """Get aggregate emotion statistics for the whole video."""
    if not profiles:
        return {"total_segments": 0, "emotion_distribution": {}, "dominant_emotion": "neutral"}

    emotion_counts = {}
    for p in profiles:
        emotion_counts[p.emotion] = emotion_counts.get(p.emotion, 0) + 1

    total = len(profiles)
    distribution = {k: round(v / total, 3) for k, v in emotion_counts.items()}
    dominant = max(emotion_counts, key=emotion_counts.get)

    # Average prosody
    avg_pitch = np.mean([p.pitch_mean for p in profiles if p.pitch_mean > 0]) if profiles else 0
    avg_energy = np.mean([p.energy_mean for p in profiles]) if profiles else 0
    avg_rate = np.mean([p.speaking_rate for p in profiles]) if profiles else 0

    return {
        "total_segments": total,
        "emotion_distribution": distribution,
        "dominant_emotion": dominant,
        "dominant_emotion_display": EMOTION_DISPLAY.get(dominant, dominant),
        "avg_pitch": round(avg_pitch, 1),
        "avg_energy": round(avg_energy, 4),
        "avg_speaking_rate": round(avg_rate, 2),
    }


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python emotion_analyzer.py <audio_file>")
        sys.exit(1)
    audio = sys.argv[1]
    if not os.path.exists(audio):
        print(f"File not found: {audio}")
        sys.exit(1)

    # Test with a single segment spanning the whole file
    import ffprobe
    print(f"Analyzing: {audio}")
    segments = [{"start": 0, "end": 30}]  # first 30 seconds
    profiles = analyze_segments(audio, segments, use_emotion2vec=True)
    for p in profiles:
        print(f"\nSegment {p.segment_index}: {p.to_dict()}")
    print(f"\nSummary: {summarize_emotions(profiles)}")
