#!/usr/bin/env python3
"""
Non-Speech Sound Preservation Module

When dubbing a video, we replace spoken dialogue with TTS in the target language.
But the original audio also contains non-speech sounds that should be preserved:
  - Laughs, giggles, chuckles
  - Sighs, gasps, groans
  - Claps, cheers, crowd reactions
  - Coughs, sneezes (optional)
  - Crying, sobbing
  - Animal sounds (if mixed with speech)
  - Sound effects that are part of the vocal track

This module:
  1. Uses WhisperX transcription + VAD to identify which parts of the vocal
     track are ACTUAL SPEECH (being transcribed) vs NON-SPEECH sounds.
  2. Extracts non-speech audio clips from the original vocal track.
  3. Returns these clips so they can be mixed back into the dubbed audio track
     at their original timestamps.

The result: laughs, sighs, and reactions are preserved in the dubbed video,
making it feel much more natural — like a professional artist dub.
"""

import os
import subprocess
import tempfile
import numpy as np
from typing import List, Dict, Tuple, Optional


def find_non_speech_regions(
    audio_path: str,
    speech_segments: List[Dict],
    total_duration: float,
    min_duration: float = 0.15,
    max_duration: float = 10.0,
    padding: float = 0.05,
) -> List[Dict]:
    """
    Find regions in the audio that contain non-speech sounds.

    Non-speech regions = parts of the vocal track that are NOT covered by
    any transcribed speech segment. These gaps contain:
      - Laughs, sighs, gasps
      - Reactions, crowd noise
      - Brief silence (which we filter out by checking energy)

    Args:
        audio_path: Path to the original vocal audio (isolated vocals from Demucs,
                    or full audio if Demucs not used)
        speech_segments: List of transcribed segments with 'start' and 'end' keys
        total_duration: Total audio duration in seconds
        min_duration: Minimum duration for a non-speech clip (default 0.15s)
        max_duration: Maximum duration for a non-speech clip (default 10s)
        padding: Padding around speech segments to avoid cutting speech (0.05s)

    Returns:
        List of non-speech regions: [{start, end, duration, energy}]
        Only includes regions that have actual audio energy (not silence).
    """
    if not speech_segments or total_duration <= 0:
        return []

    # Sort speech segments by start time
    sorted_speech = sorted(speech_segments, key=lambda s: s["start"])

    # Find gaps between speech segments
    gaps = []
    prev_end = 0.0

    for seg in sorted_speech:
        seg_start = max(0, seg["start"] - padding)
        seg_end = seg["end"] + padding

        if seg_start > prev_end + 0.01:
            # Gap between previous segment and this one
            gap_start = prev_end
            gap_end = seg_start
            gap_dur = gap_end - gap_start
            if min_duration <= gap_dur <= max_duration:
                gaps.append({
                    "start": gap_start,
                    "end": gap_end,
                    "duration": gap_dur,
                })

        prev_end = max(prev_end, seg_end)

    # Check for trailing gap after last segment
    if prev_end < total_duration - 0.1:
        gap_dur = total_duration - prev_end
        if min_duration <= gap_dur <= max_duration:
            gaps.append({
                "start": prev_end,
                "end": total_duration,
                "duration": gap_dur,
            })

    if not gaps:
        return []

    # Now filter: only keep gaps that have actual audio energy (not silence)
    # We extract a small sample and measure RMS
    non_speech_regions = []
    for gap in gaps:
        try:
            # Extract the gap audio and measure its energy
            energy = _measure_audio_energy(audio_path, gap["start"], gap["end"])
            if energy > 0.005:  # Threshold: not silence
                gap["energy"] = energy
                non_speech_regions.append(gap)
        except Exception:
            # If energy measurement fails, include the gap anyway
            gap["energy"] = 0.01
            non_speech_regions.append(gap)

    return non_speech_regions


def _measure_audio_energy(audio_path: str, start: float, end: float) -> float:
    """Measure the RMS energy of an audio segment.
    Returns 0.0 for silence, higher values for louder audio."""
    try:
        duration = end - start
        if duration <= 0:
            return 0.0

        # Use ffmpeg to extract audio as raw PCM and compute RMS
        cmd = [
            "ffmpeg", "-y", "-i", audio_path,
            "-ss", f"{start:.4f}", "-t", f"{duration:.4f}",
            "-vn", "-ac", "1", "-ar", "8000",
            "-f", "f32le", "-"  # 32-bit float little-endian
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=10)
        if result.returncode != 0 or len(result.stdout) < 100:
            return 0.0

        # Parse as 32-bit float
        audio = np.frombuffer(result.stdout, dtype=np.float32)
        if len(audio) == 0:
            return 0.0

        rms = float(np.sqrt(np.mean(audio ** 2)))
        return rms
    except Exception:
        return 0.0


def extract_non_speech_clips(
    audio_path: str,
    non_speech_regions: List[Dict],
    temp_dir: str,
) -> List[Dict]:
    """Extract non-speech audio clips from the original audio.

    Args:
        audio_path: Path to the original audio (vocals track preferred)
        non_speech_regions: List of regions from find_non_speech_regions()
        temp_dir: Directory to save extracted clips

    Returns:
        List of non-speech clips: [{start, end, duration, audio_path, energy}]
    """
    clips = []
    clips_dir = os.path.join(temp_dir, "non_speech_clips")
    os.makedirs(clips_dir, exist_ok=True)

    for i, region in enumerate(non_speech_regions):
        clip_path = os.path.join(clips_dir, f"nonspeech_{i:04d}.wav")
        cmd = [
            "ffmpeg", "-y", "-i", audio_path,
            "-ss", f"{region['start']:.4f}",
            "-t", f"{region['duration']:.4f}",
            "-vn", "-ac", "2", "-ar", "48000",
            "-c:a", "pcm_s16le", clip_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and os.path.exists(clip_path) and os.path.getsize(clip_path) > 0:
            clips.append({
                "start": region["start"],
                "end": region["end"],
                "duration": region["duration"],
                "audio_path": clip_path,
                "energy": region.get("energy", 0.01),
            })

    return clips


def get_non_speech_clips(
    audio_path: str,
    speech_segments: List[Dict],
    total_duration: float,
    temp_dir: str,
    min_duration: float = 0.15,
) -> List[Dict]:
    """One-call convenience function: find and extract non-speech clips.

    This is the main entry point. Call this with the original (or isolated vocal)
    audio and the transcribed speech segments. Returns a list of non-speech
    audio clips that can be mixed into the dubbed audio track.

    Args:
        audio_path: Path to the original audio or isolated vocals
        speech_segments: Transcribed segments with 'start'/'end' keys
        total_duration: Total audio duration
        temp_dir: Temp directory for clip storage
        min_duration: Minimum clip duration (shorter = silence, not worth keeping)

    Returns:
        List of non-speech clips with audio_path, start, end, duration
    """
    regions = find_non_speech_regions(
        audio_path, speech_segments, total_duration, min_duration=min_duration
    )
    if not regions:
        return []

    print(f"        Found {len(regions)} non-speech regions (laughs, sighs, reactions, etc.)")
    clips = extract_non_speech_clips(audio_path, regions, temp_dir)

    # Verify clips
    valid_clips = []
    for clip in clips:
        if os.path.exists(clip["audio_path"]) and os.path.getsize(clip["audio_path"]) > 100:
            valid_clips.append(clip)

    if valid_clips:
        total_dur = sum(c["duration"] for c in valid_clips)
        print(f"        Extracted {len(valid_clips)} non-speech clips ({total_dur:.1f}s total)")

    return valid_clips


def mix_non_speech_into_dub(
    dubbed_audio_path: str,
    non_speech_clips: List[Dict],
    output_path: str,
    volume: float = 0.7,
) -> str:
    """Mix non-speech clips (laughs, sighs) into the dubbed audio track.

    Each clip is placed at its original timestamp in the dubbed track.
    This makes the dubbed video feel natural — when the speaker laughs,
    the laugh is still there, just the words are dubbed.

    Args:
        dubbed_audio_path: The fully dubbed audio (TTS + background music)
        non_speech_clips: Clips from get_non_speech_clips()
        output_path: Where to save the mixed output
        volume: Volume for non-speech clips (0.7 = slightly below speech level)

    Returns:
        Path to the output file, or dubbed_audio_path if no clips to mix
    """
    if not non_speech_clips:
        return dubbed_audio_path

    # Build ffmpeg filter: overlay each non-speech clip at its timestamp
    # Use amix with adelay for each clip
    inputs = ["-i", dubbed_audio_path]
    filter_parts = []
    amix_inputs = "[0:a]"

    for i, clip in enumerate(non_speech_clips):
        inputs.extend(["-i", clip["audio_path"]])
        delay_ms = int(clip["start"] * 1000)
        # Apply volume adjustment and delay
        filter_parts.append(
            f"[{i+1}:a]volume={volume:.2f},"
            f"afade=t=in:st=0:d=0.03,afade=t=out:st={max(clip['duration']-0.05, 0):.4f}:d=0.05,"
            f"adelay={delay_ms}|{delay_ms}[ns{i}]"
        )
        amix_inputs += f"[ns{i}]"

    # Mix: dubbed audio + all non-speech clips
    total_inputs = 1 + len(non_speech_clips)
    filter_complex = ";".join(filter_parts) + f";{amix_inputs}amix=inputs={total_inputs}:duration=first:normalize=0[a]"

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[a]",
        "-ac", "2", "-ar", "48000", "-sample_fmt", "s16",
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        return output_path

    # Fallback: try concat approach for many clips
    print(f"        ⚠ Non-speech mix failed ({result.stderr[:200]}), trying alternate approach...")
    return _mix_non_speech_concat(dubbed_audio_path, non_speech_clips, output_path, volume)


def _mix_non_speech_concat(
    dubbed_audio_path: str,
    non_speech_clips: List[Dict],
    output_path: str,
    volume: float = 0.7,
) -> str:
    """Fallback: overlay non-speech clips one at a time."""
    current = dubbed_audio_path
    temp_dir = os.path.dirname(output_path)

    for i, clip in enumerate(non_speech_clips):
        delay_ms = int(clip["start"] * 1000)
        next_path = os.path.join(temp_dir, f"with_nonspeech_{i:04d}.wav")
        cmd = [
            "ffmpeg", "-y",
            "-i", current,
            "-i", clip["audio_path"],
            "-filter_complex",
            f"[1:a]volume={volume:.2f},"
            f"afade=t=in:st=0:d=0.03,afade=t=out:st={max(clip['duration']-0.05, 0):.4f}:d=0.05,"
            f"adelay={delay_ms}|{delay_ms}[ns];"
            f"[0:a][ns]amix=inputs=2:duration=first:normalize=0[a]",
            "-map", "[a]",
            "-ac", "2", "-ar", "48000", "-sample_fmt", "s16",
            next_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and os.path.exists(next_path) and os.path.getsize(next_path) > 0:
            if current != dubbed_audio_path:
                os.remove(current)
            current = next_path
        # If failed, just skip this clip

    if current != dubbed_audio_path:
        os.rename(current, output_path)
        return output_path
    return dubbed_audio_path
