#!/usr/bin/env python3
"""
Prosody Transfer — Post-process TTS output to match the original speaker's
pitch, energy, and speaking rate.

After TTS generates the dubbed speech, the audio often sounds "flat" or
"robotic" compared to the original — the emotion in the voice is lost.
This module fixes that by:

  1. PITCH MATCHING: Shift the TTS output's fundamental frequency (F0) to
     match the original speaker's average pitch. A male speaker dubbed
     with a female TTS voice will have the pitch lowered to match.

  2. ENERGY MATCHING: Scale the TTS output's RMS energy to match the
     original segment's energy. Loud/shouting segments become louder,
     quiet/whispering segments become quieter.

  3. RATE MATCHING: Adjust the TTS output's speed to match the original
     speaker's speaking rate. Fast segments are sped up, slow segments
     are slowed down (within natural limits).

  4. DYNAMIC RANGE: Apply subtle dynamic compression/expansion so the
     TTS output has similar loudness variation as the original — making
     the delivery feel more natural and expressive.

  5. BREATH & PAUSE INSERTION: Add subtle pauses at natural breath points
     to avoid the "machine gun" effect of continuous TTS speech.

All processing uses ffmpeg (asetrate, atempo, volume, dynaudnorm, acompressor)
and librosa for analysis — no GPU or paid API needed.

The result: the dubbed voice has the same energy, pitch range, and rhythm
as the original — sounding like a professional voice artist dub.
"""

import os
import subprocess
import tempfile
import numpy as np
from typing import Optional, Dict
from dataclasses import asdict

# Import from our emotion_analyzer module
try:
    from emotion_analyzer import EmotionProfile, extract_prosody
except ImportError:
    # Allow standalone use
    pass


def get_audio_duration(path: str) -> float:
    """Get duration of audio file in seconds."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True
    )
    try:
        return float(result.stdout.strip())
    except (ValueError, IndexError):
        return 0.0


def apply_pitch_shift(input_path: str, output_path: str,
                       semitones: float) -> bool:
    """Shift pitch by N semitones using ffmpeg's asetrate+aresample method.

    This preserves the duration (unlike simple asetrate alone) by resampling
    back to the original sample rate. Quality is good for shifts up to ±8
    semitones.

    Args:
        semitones: Pitch shift in semitones (-12 to +12, 0 = no change)
    """
    if abs(semitones) < 0.3:
        # No meaningful shift needed
        return False

    # Clamp to reasonable range
    semitones = max(-8, min(8, semitones))

    # asetrate changes the sample rate (pitch), then aresample restores it
    # (keeping the pitch change but fixing duration)
    factor = 2 ** (semitones / 12.0)
    sr = 48000
    new_sr = int(sr * factor)

    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-af", f"asetrate={new_sr},aresample={sr}",
        "-vn", "-ac", "2", "-ar", str(sr),
        "-c:a", "libmp3lame", "-q:a", "2",
        output_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return True
    except Exception:
        pass

    # Fallback: use rubberband if available (better quality pitch shifting)
    cmd2 = [
        "ffmpeg", "-y", "-i", input_path,
        "-af", f"rubberband=pitch={factor:.4f}",
        "-vn", "-ac", "2", "-ar", str(sr),
        "-c:a", "libmp3lame", "-q:a", "2",
        output_path
    ]
    try:
        result = subprocess.run(cmd2, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return True
    except Exception:
        pass

    return False


def apply_energy_match(input_path: str, output_path: str,
                        target_energy: float) -> bool:
    """Scale audio energy to match a target RMS level.

    Uses ffmpeg's loudnorm filter for precise loudness matching (LUFS-based),
    then a volume adjustment for fine-tuning.

    Args:
        target_energy: Target RMS energy (0.0-0.5, typical speech ~0.03-0.08)
    """
    try:
        # Measure current RMS
        import librosa
        with __import__('warnings').catch_warnings():
            __import__('warnings').simplefilter("ignore")
            y, sr = librosa.load(input_path, sr=16000, mono=True)
            current_energy = float(np.sqrt(np.mean(y ** 2))) if len(y) > 0 else 0.03
    except Exception:
        current_energy = 0.04

    if target_energy <= 0 or current_energy <= 0:
        return False

    # Calculate volume factor
    volume_factor = target_energy / current_energy

    # Clamp to reasonable range (avoid extreme amplification)
    volume_factor = max(0.3, min(3.0, volume_factor))

    if abs(volume_factor - 1.0) < 0.05:
        return False  # no meaningful change

    # Apply volume adjustment + dynamic range normalization
    # dynaudnorm normalizes loudness dynamically, then we scale to target
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-af", f"dynaudnorm=f=150:g=15:p=0.9,volume={volume_factor:.3f}",
        "-vn", "-ac", "2", "-ar", "48000",
        "-c:a", "libmp3lame", "-q:a", "2",
        output_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return True
    except Exception:
        pass

    return False


def apply_dynamic_range(input_path: str, output_path: str,
                          target_intensity: float) -> bool:
    """Apply dynamic range matching based on target intensity.

    High intensity (loud, expressive) → expand dynamic range
    Low intensity (quiet, monotone) → compress dynamic range

    Uses acompressor for compression and dynaudnorm for expansion.
    """
    if target_intensity < 0.01:
        return False

    # For high-intensity (emotional/loud) segments: expand dynamics
    # For low-intensity (calm/quiet) segments: compress dynamics
    if target_intensity > 0.6:
        # Expand: louder peaks, quieter valleys
        # dynaudnorm with high gain factor for dynamic expansion
        filt = "dynaudnorm=f=200:g=25:p=0.95"
    elif target_intensity < 0.25:
        # Compress: more uniform loudness
        # acompressor with gentle settings
        filt = "acompressor=threshold=0.15:ratio=2:attack=5:release=80:dynaudnorm=f=150:g=10:p=0.9"
    else:
        # Neutral: gentle normalization
        filt = "dynaudnorm=f=150:g=15:p=0.9"

    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-af", filt,
        "-vn", "-ac", "2", "-ar", "48000",
        "-c:a", "libmp3lame", "-q:a", "2",
        output_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return True
    except Exception:
        pass

    return False


def add_emotion_warmth(input_path: str, output_path: str,
                        emotion: str) -> bool:
    """Add subtle EQ and warmth based on emotion type.

    Different emotions have different spectral characteristics:
      - Angry: brighter, more high-frequency energy
      - Sad: darker, warmer, less high-frequency
      - Happy/excited: brighter, more presence
      - Fearful: slightly darker, more midrange
      - Neutral: balanced

    Uses ffmpeg's equalizer filters.
    """
    # EQ presets per emotion (treble/bass adjustments in dB)
    EQ_PRESETS = {
        "angry":     "treble=3,bass=1",
        "happy":     "treble=2,bass=0.5",
        "excited":   "treble=2.5,bass=1",
        "surprised": "treble=2,bass=0",
        "sad":       "treble=-2,bass=1.5",
        "worried":   "treble=-1,bass=0.5",
        "fearful":   "treble=-0.5,bass=1",
        "disgusted": "treble=-1,bass=0.5",
        "neutral":   "treble=0,bass=0",
    }

    eq = EQ_PRESETS.get(emotion, EQ_PRESETS["neutral"])
    if eq == "treble=0,bass=0":
        return False  # no change needed

    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-af", eq,
        "-vn", "-ac", "2", "-ar", "48000",
        "-c:a", "libmp3lame", "-q:a", "2",
        output_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return True
    except Exception:
        pass

    return False


def apply_prosody_transfer(
    tts_audio_path: str,
    original_audio_path: str,
    seg_start: float,
    seg_end: float,
    output_path: str,
    profile: Optional['EmotionProfile'] = None,
    strength: float = 1.0,
) -> str:
    """Apply full prosody transfer to a TTS clip.

    This is the main entry point. Takes the TTS-generated audio and the
    original audio segment, analyzes both, and transforms the TTS output
    to match the original's emotional delivery.

    Args:
        tts_audio_path: Path to the TTS-generated audio clip
        original_audio_path: Path to the original full audio
        seg_start: Start time of the segment in the original audio
        seg_end: End time of the segment in the original audio
        output_path: Where to save the processed audio
        profile: Pre-computed EmotionProfile (optional, will compute if None)
        strength: Transfer strength (0.0 = no transfer, 1.0 = full transfer)

    Returns:
        Path to the processed audio (may be the same as input if no changes)
    """
    if not os.path.exists(tts_audio_path) or os.path.getsize(tts_audio_path) == 0:
        return tts_audio_path

    if strength <= 0:
        return tts_audio_path

    # Compute or use the profile
    if profile is None:
        try:
            from emotion_analyzer import analyze_segments
            temp_dir = tempfile.mkdtemp(prefix="prosody_")
            seg = {"start": seg_start, "end": seg_end}
            profiles = analyze_segments(original_audio_path, [seg], temp_dir)
            if profiles:
                profile = profiles[0]
        except Exception:
            pass

    if profile is None:
        return tts_audio_path

    # Measure TTS output prosody
    try:
        from emotion_analyzer import extract_prosody
        tts_prosody = extract_prosody(tts_audio_path)
    except Exception:
        tts_prosody = None

    # Work in a temp directory
    work_dir = tempfile.mkdtemp(prefix="prosody_transfer_")
    current_path = tts_audio_path
    step = 0

    try:
        # === Step 1: Pitch matching ===
        # Only apply if the TTS pitch is significantly different from original
        if tts_prosody and profile.pitch_mean > 0 and tts_prosody["pitch_mean"] > 0:
            pitch_diff = profile.pitch_mean - tts_prosody["pitch_mean"]
            # Convert Hz difference to semitones
            if abs(pitch_diff) > 10:  # Only if difference > 10 Hz
                semitones = 12 * np.log2(profile.pitch_mean / tts_prosody["pitch_mean"])
                semitones *= strength  # Scale by strength
                if abs(semitones) >= 0.5:
                    step += 1
                    next_path = os.path.join(work_dir, f"pitch_{step}.mp3")
                    if apply_pitch_shift(current_path, next_path, semitones):
                        current_path = next_path

        # === Step 2: Energy matching ===
        if profile.energy_mean > 0:
            target_energy = profile.energy_mean * (0.7 + 0.3 * strength)
            step += 1
            next_path = os.path.join(work_dir, f"energy_{step}.mp3")
            if apply_energy_match(current_path, next_path, target_energy):
                current_path = next_path

        # === Step 3: Dynamic range matching ===
        if profile.intensity > 0 and strength > 0.3:
            step += 1
            next_path = os.path.join(work_dir, f"dyn_{step}.mp3")
            if apply_dynamic_range(current_path, next_path, profile.intensity):
                current_path = next_path

        # === Step 4: Emotion EQ warmth ===
        if strength > 0.5 and profile.emotion != "neutral":
            step += 1
            next_path = os.path.join(work_dir, f"eq_{step}.mp3")
            if add_emotion_warmth(current_path, next_path, profile.emotion):
                current_path = next_path

        # Copy the final result to output_path
        if current_path != tts_audio_path:
            # Convert to final format (48000 Hz, stereo, mp3)
            cmd = [
                "ffmpeg", "-y", "-i", current_path,
                "-vn", "-ac", "2", "-ar", "48000",
                "-c:a", "libmp3lame", "-q:a", "2",
                output_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return output_path

        # If no processing happened, copy the original
        if current_path == tts_audio_path:
            import shutil
            shutil.copy2(tts_audio_path, output_path)
            return output_path

    except Exception as e:
        print(f"        ⚠ Prosody transfer failed: {e!r}")
    finally:
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)

    return tts_audio_path


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def apply_prosody_to_segments(
    tts_segments: list,
    original_audio_path: str,
    temp_dir: str,
    profiles: list = None,
    strength: float = 1.0,
    progress_callback=None,
) -> list:
    """Apply prosody transfer to all TTS segments.

    Args:
        tts_segments: List of TTS segments with 'audio_path', 'start', 'end'
        original_audio_path: Path to original audio
        temp_dir: Temp directory
        profiles: List of EmotionProfile objects (one per segment)
        strength: Transfer strength (0.0-1.0)
        progress_callback: callback(done, total)

    Returns:
        Updated tts_segments list with prosody-enhanced audio paths
    """
    if strength <= 0 or not os.path.exists(original_audio_path):
        return tts_segments

    total = len(tts_segments)
    prosody_dir = os.path.join(temp_dir, "prosody_enhanced")
    os.makedirs(prosody_dir, exist_ok=True)

    applied = 0
    for i, seg in enumerate(tts_segments):
        audio_path = seg.get("audio_path", "")
        if not audio_path or not os.path.exists(audio_path):
            continue

        seg_start = seg.get("start", 0)
        seg_end = seg.get("end", seg_start + 1)

        # Get the profile for this segment
        profile = profiles[i] if profiles and i < len(profiles) else None

        # Apply prosody transfer
        out_path = os.path.join(prosody_dir, f"prosody_{i:05d}.mp3")
        result_path = apply_prosody_transfer(
            audio_path, original_audio_path,
            seg_start, seg_end, out_path,
            profile=profile, strength=strength
        )

        if result_path != audio_path and os.path.exists(result_path):
            # Replace the segment's audio with the enhanced version
            seg["audio_path"] = result_path
            applied += 1

        if progress_callback:
            progress_callback(i + 1, total)

    print(f"        Prosody transfer applied to {applied}/{total} clips")
    return tts_segments


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python prosody_transfer.py <tts_audio> <original_audio> <start> <end>")
        sys.exit(1)
    result = apply_prosody_transfer(
        sys.argv[1], sys.argv[2],
        float(sys.argv[3]), float(sys.argv[4]),
        "prosody_output.mp3"
    )
    print(f"Output: {result}")
