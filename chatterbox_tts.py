#!/usr/bin/env python3
"""
Chatterbox TTS Module — Zero-shot voice cloning via HuggingFace ZeroGPU.

Chatterbox Multilingual V3 is a SOTA TTS model that:
  - Supports 23+ languages including Hindi (near-human quality)
  - Performs zero-shot voice cloning from a short reference audio clip
  - Preserves the original speaker's voice characteristics across languages
  - Runs on HuggingFace ZeroGPU (free A10G GPU) — no local GPU needed
  - Supports emotion control (exaggeration) and temperature for expressiveness

This is the PRIMARY voice cloning engine for the dubbing pipeline.
It produces much better quality than OpenVoice V2 for cross-lingual cloning
(e.g., cloning a Japanese voice to speak Hindi).

API: https://huggingface.co/spaces/ResembleAI/Chatterbox-Multilingual-TTS
      https://huggingface.co/spaces/ResembleAI/Chatterbox-Multilingual-TTS-hi (Hindi-specific)
"""

import os
import sys
import time
import shutil
import tempfile
import subprocess
from typing import Optional


# HF Space IDs for Chatterbox Multilingual
CHATTERBOX_SPACES = {
    "hi": "ResembleAI/Chatterbox-Multilingual-TTS-hi",    # Hindi-specific
    "multi": "ResembleAI/Chatterbox-Multilingual-TTS",     # All 23 languages
}

# Languages supported by Chatterbox Multilingual V3
CHATTERBOX_SUPPORTED_LANGS = {
    "hi", "en", "es", "fr", "de", "it", "pt", "nl", "pl", "tr",
    "ru", "ar", "ko", "ja", "zh", "vi", "id", "th", "sv", "da",
    "fi", "no", "cs",
}

# Rate limiting: ZeroGPU spaces have ~60s timeout and may queue
_MAX_RETRIES = 3
_RETRY_DELAY = 5  # seconds between retries


def is_supported(target_lang: str) -> bool:
    """Check if Chatterbox supports the target language."""
    return target_lang in CHATTERBOX_SUPPORTED_LANGS


def get_space_for_lang(target_lang: str) -> str:
    """Get the best HuggingFace Space for a target language."""
    if target_lang == "hi":
        return CHATTERBOX_SPACES["hi"]
    if target_lang in CHATTERBOX_SUPPORTED_LANGS:
        return CHATTERBOX_SPACES["multi"]
    return CHATTERBOX_SPACES["multi"]  # Fallback


def clone_voice(text: str, ref_audio_path: str, out_path: str,
                target_lang: str = "hi",
                exaggeration: float = 0.5,
                temperature: float = 0.8,
                cfgw: float = 0.5,
                seed: int = 0,
                max_retries: int = _MAX_RETRIES) -> bool:
    """Clone a voice and generate speech using Chatterbox Multilingual V3.

    Args:
        text: Text to synthesize in the target language
        ref_audio_path: Path to reference audio (original speaker's voice, 5-15s)
        out_path: Output audio file path
        target_lang: Target language code (e.g., 'hi', 'en')
        exaggeration: Emotion intensity (0.5 = neutral, 0.8 = expressive, 1.0 = exaggerated)
        temperature: Sampling temperature (0.8 = natural, lower = more deterministic)
        cfgw: CFG weight / pace (0.5 = normal)
        seed: Random seed (0 = random each time)
        max_retries: Number of retry attempts on failure

    Returns:
        True on success, False on failure
    """
    if not ref_audio_path or not os.path.exists(ref_audio_path):
        print("        [Chatterbox] No reference audio provided")
        return False

    text = text.strip()
    if not text:
        return False
    # Chatterbox Hindi space limits to 300 chars
    if len(text) > 290:
        text = text[:290]
        print(f"        [Chatterbox] Text truncated to 290 chars (space limit)")

    try:
        from gradio_client import Client, handle_file
    except ImportError:
        print("        [Chatterbox] gradio_client not installed")
        return False

    space_id = get_space_for_lang(target_lang)

    # Prepare reference audio — ensure it's a valid WAV file
    # Chatterbox works best with 24kHz mono WAV
    ref_wav = ref_audio_path
    if not ref_audio_path.lower().endswith('.wav'):
        tmpdir = tempfile.mkdtemp(prefix="chatterbox_ref_")
        ref_wav = os.path.join(tmpdir, "ref.wav")
        try:
            subprocess.run([
                "ffmpeg", "-y", "-i", ref_audio_path,
                "-vn", "-ac", "1", "-ar", "24000",
                "-c:a", "pcm_s16le", ref_wav
            ], capture_output=True, text=True, timeout=10)
        except Exception:
            shutil.rmtree(tmpdir, ignore_errors=True)
            return False

    if not os.path.exists(ref_wav) or os.path.getsize(ref_wav) == 0:
        print(f"        [Chatterbox] Reference audio conversion failed")
        return False

    # Truncate reference audio to 15 seconds (Chatterbox recommendation)
    # Also trim silence from start/end for better cloning
    ref_trimmed = os.path.join(os.path.dirname(ref_wav), "ref_trimmed.wav")
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", ref_wav,
            "-vn", "-ac", "1", "-ar", "24000",
            "-t", "15",                  # Max 15 seconds
            "-af", "silenceremove=start_periods=1:start_silence=0.1:stop_silence=0.3:threshold=-40dB",
            "-c:a", "pcm_s16le", ref_trimmed
        ], capture_output=True, text=True, timeout=15)
        if os.path.exists(ref_trimmed) and os.path.getsize(ref_trimmed) > 0:
            ref_wav = ref_trimmed
    except Exception:
        pass  # Use untrimmed if trimming fails

    for attempt in range(max_retries):
        try:
            client = Client(space_id, verbose=False)

            result = client.predict(
                text_input=text,
                audio_prompt_path_input=handle_file(ref_wav),
                exaggeration_input=exaggeration,
                temperature_input=temperature,
                seed_num_input=seed if seed > 0 else 0,
                cfgw_input=cfgw,
                api_name="/generate_tts_audio"
            )

            # Extract the audio path from the result
            if isinstance(result, tuple) and len(result) >= 1:
                audio_path = result[0]
            elif isinstance(result, list) and len(result) >= 1:
                audio_path = result[0]
            elif isinstance(result, str):
                audio_path = result
            else:
                # Try to extract from dict-like
                audio_path = getattr(result, 'path', None) or str(result)

            if not audio_path or not isinstance(audio_path, str):
                print(f"        [Chatterbox] No audio in result (attempt {attempt+1})")
                continue

            if not os.path.exists(audio_path):
                print(f"        [Chatterbox] Output file not found: {audio_path}")
                continue

            # Copy and convert to the target format
            tmp_wav = out_path + ".chatterbox_raw.wav"
            shutil.copy(audio_path, tmp_wav)

            # Convert to 48kHz stereo (matching pipeline standard)
            acodec = "libmp3lame" if out_path.lower().endswith(".mp3") else "pcm_s16le"
            r = subprocess.run([
                "ffmpeg", "-y", "-i", tmp_wav,
                "-vn", "-ac", "2", "-ar", "48000",
                "-c:a", acodec, out_path
            ], capture_output=True, text=True, timeout=15)

            try:
                os.remove(tmp_wav)
            except OSError:
                pass

            if r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                print(f"        [Chatterbox] ✅ Voice cloned successfully ({os.path.getsize(out_path)} bytes)")
                return True
            else:
                print(f"        [Chatterbox] FFmpeg conversion failed (attempt {attempt+1})")

        except Exception as e:
            err_str = str(e).lower()
            if "timeout" in err_str or "queue" in err_str or "503" in err_str or "gpu" in err_str:
                wait = _RETRY_DELAY * (attempt + 1)
                print(f"        [Chatterbox] GPU busy (attempt {attempt+1}/{max_retries}), retrying in {wait}s...")
                time.sleep(wait)
            elif "gated" in err_str or "401" in err_str or "access" in err_str:
                print(f"        [Chatterbox] Access denied: {e!r}")
                return False  # Don't retry auth errors
            else:
                print(f"        [Chatterbox] Error (attempt {attempt+1}/{max_retries}): {e!r}")
                if attempt < max_retries - 1:
                    time.sleep(_RETRY_DELAY)

    # Clean up temp files
    if ref_wav != ref_audio_path and os.path.dirname(ref_wav) != os.path.dirname(ref_audio_path):
        try:
            shutil.rmtree(os.path.dirname(ref_wav), ignore_errors=True)
        except Exception:
            pass

    return False


def clone_voice_batch(segments: list, speaker_ref_audios: dict,
                      target_lang: str, temp_dir: str,
                      progress_callback=None) -> list:
    """Generate voice-cloned TTS for multiple segments.

    Args:
        segments: List of segment dicts with 'text' and 'speaker' fields
        speaker_ref_audios: Mapping of speaker_id -> reference audio path
        target_lang: Target language code
        temp_dir: Temp directory for output files
        progress_callback: Optional callback(done, total)

    Returns:
        List of segment dicts with 'audio_path' added
    """
    results = []
    total = len(segments)

    for i, seg in enumerate(segments):
        text = seg.get("translated_text", seg.get("text", ""))
        speaker_id = seg.get("speaker", 0)
        ref_audio = speaker_ref_audios.get(speaker_id, speaker_ref_audios.get(0))

        out_path = os.path.join(temp_dir, f"chatterbox_{i:04d}.mp3")

        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            # Already generated (resume support)
            seg["audio_path"] = out_path
            results.append(seg)
            if progress_callback:
                progress_callback(i + 1, total)
            continue

        success = clone_voice(text, ref_audio, out_path, target_lang=target_lang)

        if success:
            seg["audio_path"] = out_path
        else:
            seg["audio_path"] = None  # Caller should handle fallback

        results.append(seg)

        if progress_callback:
            progress_callback(i + 1, total)

    return results
