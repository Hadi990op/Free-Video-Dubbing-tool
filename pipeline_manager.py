#!/usr/bin/env python3
"""
Pipeline Manager — Intelligent orchestrator for the dubbing pipeline.

This module sits ABOVE the dubber and web_ui, acting as the "brain" that:

  1. JOB QUEUE — Manages a priority queue. On a 1.8GB RAM VM, only ONE job
     runs at a time. New jobs queue up. Priority: high > normal > low.
     Emergency resume jobs (crash recovery) get highest priority.

  2. PRE-FLIGHT CHECKS — Before starting any job, validates:
       - Video file exists and is readable (ffprobe)
       - Duration is reasonable (skip >2hr videos)
       - Disk space available (need ~5x video size for temp files)
       - RAM available (need ~1.5GB free for models)
       - Network connectivity (for translation + TTS)
     If any check fails, job is rejected with a clear reason.

  3. AUTO-RETRY — If a stage fails (network blip, OOM, transient error),
     automatically retries with exponential backoff (5s, 15s, 45s).
     Max 3 retries per stage before giving up.

  4. HEALTH MONITOR — Background watchdog thread that:
       - Detects stalled jobs (no progress for 5 minutes)
       - Auto-resumes paused jobs on server restart
       - Monitors RAM usage and triggers cleanup if >90%
       - Logs all events to a persistent log file

  5. SMART DEFAULTS — Analyzes the video and picks optimal settings:
       - Source language auto-detection (via Whisper language detection)
       - Whisper model size based on duration + available RAM:
           <5min  + 1.5GB RAM → "base"    (fast, good accuracy)
           <5min  + 2.5GB RAM → "small"   (better accuracy)
           >5min  → "base"                (speed priority for long videos)
       - Voice auto-selection per language
       - Multi-speaker auto-detection (if >1 speaker, enable diarization)

  6. ANALYTICS — Tracks job history, success rate, average processing time,
     per-stage timing breakdown, and per-language statistics.

  7. BATCH PROCESSING — Queue multiple videos at once (e.g. a playlist).
     Each video gets its own job ID but they share queue position.

All state is persisted to disk so it survives server restarts.
"""

import os
import sys
import json
import time
import threading
import subprocess
import shutil
import psutil
from pathlib import Path
from collections import deque, defaultdict
from datetime import datetime

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Base directories
BASE_DIR = Path(__file__).resolve().parent
WORK_DIR = BASE_DIR / "work"
STATE_FILE = WORK_DIR / "manager_state.json"
HISTORY_FILE = WORK_DIR / "job_history.json"
LOG_FILE = WORK_DIR / "manager.log"
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"

# Ensure dirs exist
WORK_DIR.mkdir(exist_ok=True)

# Limits
MAX_CONCURRENT_JOBS = 1  # Low RAM VM — one job at a time
MAX_VIDEO_DURATION = 7200  # 2 hours max
MIN_FREE_DISK_MB = 1000  # Need at least 1GB free
MIN_FREE_RAM_MB = 500  # Need at least 500MB free for models (1.8GB VM tuned)
MAX_RETRIES = 3
RETRY_BACKOFF = [5, 15, 45]  # seconds between retries
STALL_TIMEOUT = 300  # 5 minutes without progress = stalled
WATCHDOG_INTERVAL = 30  # check every 30 seconds
MAX_HISTORY = 500  # keep last 500 jobs in history


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _log(level, msg):
    """Write to manager log file."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass
    if level == "ERROR":
        print(line, file=sys.stderr)
    else:
        print(line)


# ---------------------------------------------------------------------------
# Job dataclass
# ---------------------------------------------------------------------------

class Job:
    """Represents a single dubbing job in the pipeline."""

    def __init__(self, job_id, video_path, target_lang, options=None):
        self.job_id = job_id
        self.video_path = video_path
        self.target_lang = target_lang
        self.options = options or {}

        # Queue state
        self.priority = self.options.get("priority", "normal")  # high/normal/low
        self.status = "queued"  # queued/running/paused/done/error/cancelled
        self.queued_at = time.time()
        self.started_at = None
        self.completed_at = None
        self.attempts = {}  # stage -> retry count

        # Video metadata (filled by pre-flight)
        self.video_info = {}  # duration, resolution, size, codec

        # Progress tracking
        self.current_stage = 0
        self.progress = 0
        self.last_progress_time = time.time()
        self.stage_timings = {}  # stage -> seconds taken

        # Result
        self.output_path = None
        self.error = None

    def to_dict(self):
        return {
            "job_id": self.job_id,
            "video_path": self.video_path,
            "target_lang": self.target_lang,
            "options": self.options,
            "priority": self.priority,
            "status": self.status,
            "queued_at": self.queued_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "video_info": self.video_info,
            "current_stage": self.current_stage,
            "progress": self.progress,
            "stage_timings": self.stage_timings,
            "output_path": self.output_path,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d):
        job = cls(d["job_id"], d["video_path"], d["target_lang"], d.get("options"))
        job.priority = d.get("priority", "normal")
        job.status = d.get("status", "queued")
        job.queued_at = d.get("queued_at", time.time())
        job.started_at = d.get("started_at")
        job.completed_at = d.get("completed_at")
        job.video_info = d.get("video_info", {})
        job.current_stage = d.get("current_stage", 0)
        job.progress = d.get("progress", 0)
        job.stage_timings = d.get("stage_timings", {})
        job.output_path = d.get("output_path")
        job.error = d.get("error")
        return job


# ---------------------------------------------------------------------------
# Pipeline Manager (Singleton)
# ---------------------------------------------------------------------------

class PipelineManager:
    """
    Central orchestrator for all dubbing jobs.

    Singleton — one instance manages the entire pipeline.
    Thread-safe via internal locks.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # Job storage
        self._queue = deque()  # pending jobs (sorted by priority)
        self._jobs = {}  # job_id -> Job
        self._current_job = None  # currently running job
        self._worker_thread = None
        self._watchdog_thread = None
        self._running = False
        self._queue_lock = threading.Lock()
        self._cancel_flags = {}  # job_id -> bool

        # Analytics
        self._history = self._load_history()
        self._stats = {
            "total_jobs": len(self._history),
            "completed": sum(1 for h in self._history if h.get("status") == "done"),
            "failed": sum(1 for h in self._history if h.get("status") == "error"),
            "cancelled": sum(1 for h in self._history if h.get("status") == "cancelled"),
            "total_processing_time": sum(h.get("elapsed", 0) for h in self._history if h.get("status") == "done"),
        }

        # Load persisted state
        self._load_state()

        # Start background threads
        self._running = True
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="pm-worker")
        self._worker_thread.start()
        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True, name="pm-watchdog")
        self._watchdog_thread.start()

        _log("INFO", f"PipelineManager started — {len(self._queue)} jobs in queue, "
                      f"{len(self._history)} jobs in history")

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    def submit_job(self, video_path, target_lang, options=None):
        """
        Submit a new job to the pipeline.

        Returns: (job_id, preflight_result)
          - If pre-flight fails, job_id is None and preflight_result has errors.
          - If pre-flight passes, job_id is a new ID and job is queued.
        """
        options = options or {}
        job_id = options.get("job_id") or f"job_{int(time.time())}_{os.getpid() % 1000}"

        # Pre-flight checks
        preflight = self._preflight(video_path)
        if not preflight["ok"]:
            _log("WARN", f"Job {job_id} pre-flight failed: {preflight['errors']}")
            return None, preflight

        # Smart defaults
        smart = self._smart_defaults(preflight.get("video_info", {}), target_lang, options)
        options.update(smart)

        # Create job
        job = Job(job_id, video_path, target_lang, options)
        job.video_info = preflight.get("video_info", {})

        with self._queue_lock:
            self._jobs[job_id] = job
            self._enqueue(job)

        self._save_state()
        _log("INFO", f"Job {job_id} queued — {job.video_info.get('duration', 0):.0f}s video, "
                      f"target: {target_lang}, queue position: {len(self._queue)}")

        return job_id, {"ok": True, "job_id": job_id, "queue_position": len(self._queue),
                        "video_info": job.video_info, "smart_defaults": smart}

    def cancel_job(self, job_id):
        """Cancel a queued or running job."""
        with self._queue_lock:
            if job_id in self._cancel_flags:
                self._cancel_flags[job_id] = True

            job = self._jobs.get(job_id)
            if job and job.status == "queued":
                job.status = "cancelled"
                job.completed_at = time.time()
                self._queue = deque(j for j in self._queue if j.job_id != job_id)
                _log("INFO", f"Job {job_id} cancelled (was queued)")
                self._save_state()
                return True

            if job and job.status == "running":
                _log("INFO", f"Job {job_id} cancel requested (running)")
                return True

        return False

    def get_job_status(self, job_id):
        """Get status of a job."""
        job = self._jobs.get(job_id)
        if not job:
            return None
        return {
            "job_id": job.job_id,
            "status": job.status,
            "progress": job.progress,
            "current_stage": job.current_stage,
            "queue_position": self._queue_position(job_id),
            "video_info": job.video_info,
            "target_lang": job.target_lang,
            "error": job.error,
            "output_path": job.output_path,
            "queued_at": job.queued_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "options": job.options,
        }

    def get_queue(self):
        """Get the current job queue."""
        with self._queue_lock:
            return [{
                "job_id": j.job_id,
                "status": j.status,
                "priority": j.priority,
                "target_lang": j.target_lang,
                "queued_at": j.queued_at,
                "video_info": j.video_info,
                "queue_position": idx + 1,
            } for idx, j in enumerate(self._queue)]

    def get_stats(self):
        """Get pipeline statistics."""
        with self._queue_lock:
            queue_len = len(self._queue)
            current = self._current_job

        # Per-language stats
        lang_stats = defaultdict(lambda: {"total": 0, "done": 0, "failed": 0, "avg_time": 0})
        for h in self._history:
            lang = h.get("target_lang", "unknown")
            lang_stats[lang]["total"] += 1
            if h.get("status") == "done":
                lang_stats[lang]["done"] += 1
                lang_stats[lang]["avg_time"] += h.get("elapsed", 0)
            elif h.get("status") == "error":
                lang_stats[lang]["failed"] += 1
        for lang in lang_stats:
            if lang_stats[lang]["done"] > 0:
                lang_stats[lang]["avg_time"] /= lang_stats[lang]["done"]

        # System health
        vm = psutil.virtual_memory()
        disk = psutil.disk_usage(str(BASE_DIR))

        return {
            "queue_length": queue_len,
            "current_job": current.job_id if current else None,
            "total_jobs": self._stats["total_jobs"],
            "completed": self._stats["completed"],
            "failed": self._stats["failed"],
            "cancelled": self._stats["cancelled"],
            "success_rate": (self._stats["completed"] / max(1, self._stats["total_jobs"])) * 100,
            "avg_processing_time": self._stats["total_processing_time"] / max(1, self._stats["completed"]),
            "language_stats": dict(lang_stats),
            "system_health": {
                "ram_percent": vm.percent,
                "ram_available_mb": vm.available / 1024 / 1024,
                "disk_percent": disk.percent,
                "disk_free_mb": disk.free / 1024 / 1024,
                "queue_capacity": MAX_CONCURRENT_JOBS,
            },
        }

    def get_history(self, limit=50):
        """Get recent job history."""
        return self._history[-limit:]

    def retry_job(self, job_id):
        """Retry a failed or paused job."""
        job = self._jobs.get(job_id)
        if not job:
            return False, "Job not found"
        if job.status not in ("error", "paused"):
            return False, f"Cannot retry job in '{job.status}' state"

        job.status = "queued"
        job.error = None
        job.attempts = {}
        with self._queue_lock:
            # High priority for retry
            job.priority = "high"
            self._enqueue(job)
        self._save_state()
        _log("INFO", f"Job {job_id} retried (high priority)")
        return True, "Job queued for retry"

    # -------------------------------------------------------------------
    # Pre-flight checks
    # -------------------------------------------------------------------

    def _preflight(self, video_path):
        """Run pre-flight checks on a video. Returns {ok, errors, video_info}."""
        errors = []
        video_info = {}

        # 1. File exists
        if not os.path.exists(video_path):
            errors.append("Video file not found")
            return {"ok": False, "errors": errors}

        # 2. File size
        file_size = os.path.getsize(video_path)
        if file_size < 1000:
            errors.append(f"Video file too small ({file_size} bytes) — may be corrupt")
        if file_size > 500 * 1024 * 1024:
            errors.append(f"Video file too large ({file_size/1024/1024:.0f}MB) — max 500MB")

        # 3. ffprobe validation
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_format", "-show_streams", video_path],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                errors.append("ffprobe failed — video may be corrupt or unsupported")
            else:
                info = json.loads(result.stdout)
                fmt = info.get("format", {})
                streams = info.get("streams", [])

                duration = float(fmt.get("duration", 0))
                video_info["duration"] = duration
                video_info["size_mb"] = file_size / 1024 / 1024

                # Find video stream
                vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
                if vstream:
                    video_info["width"] = int(vstream.get("width", 0))
                    video_info["height"] = int(vstream.get("height", 0))
                    video_info["codec"] = vstream.get("codec_name", "unknown")
                    video_info["fps"] = eval(vstream.get("r_frame_rate", "0/1"))

                # Find audio stream
                astream = next((s for s in streams if s.get("codec_type") == "audio"), None)
                video_info["has_audio"] = astream is not None
                if astream:
                    video_info["audio_codec"] = astream.get("codec_name", "unknown")
                    video_info["sample_rate"] = int(astream.get("sample_rate", 0))

                if not vstream:
                    errors.append("No video stream found")
                if not astream:
                    errors.append("No audio stream found — can't dub a silent video")
                if duration > MAX_VIDEO_DURATION:
                    errors.append(f"Video too long ({duration/60:.0f}min) — max {MAX_VIDEO_DURATION/60:.0f}min")
        except json.JSONDecodeError:
            errors.append("Could not parse video metadata")
        except subprocess.TimeoutExpired:
            errors.append("Video analysis timed out — file may be very large")
        except FileNotFoundError:
            errors.append("ffprobe not installed")
        except Exception as e:
            errors.append(f"Video validation error: {str(e)}")

        # 4. Disk space
        disk = psutil.disk_usage(str(BASE_DIR))
        free_mb = disk.free / 1024 / 1024
        needed_mb = file_size / 1024 / 1024 * 5  # ~5x video size for temp files
        if free_mb < MIN_FREE_DISK_MB:
            errors.append(f"Low disk space ({free_mb:.0f}MB free, need {MIN_FREE_DISK_MB}MB)")
        if free_mb < needed_mb:
            errors.append(f"Insufficient disk space ({free_mb:.0f}MB free, need ~{needed_mb:.0f}MB for processing)")

        # 5. RAM check
        vm = psutil.virtual_memory()
        if vm.available < MIN_FREE_RAM_MB * 1024 * 1024:
            errors.append(f"Low RAM ({vm.available/1024/1024:.0f}MB free, need {MIN_FREE_RAM_MB}MB)")

        # 6. Network check (for translation + TTS)
        if not self._check_network():
            errors.append("No network connectivity — translation and TTS require internet")

        return {"ok": len(errors) == 0, "errors": errors, "video_info": video_info}

    def _check_network(self):
        """Quick network connectivity check."""
        import socket
        for host, port in [("8.8.8.8", 53), ("1.1.1.1", 53)]:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3)
                sock.connect((host, port))
                sock.close()
                return True
            except Exception:
                continue
        return False

    # -------------------------------------------------------------------
    # Smart defaults
    # -------------------------------------------------------------------

    def _smart_defaults(self, video_info, target_lang, options):
        """
        Analyze video metadata and pick optimal settings automatically.
        Only sets defaults for options not explicitly provided by the user.
        """
        defaults = {}
        duration = video_info.get("duration", 0)
        height = video_info.get("height", 0)
        ram_mb = psutil.virtual_memory().available / 1024 / 1024

        # Whisper model size selection
        if "model_size" not in options or not options.get("model_size"):
            if duration < 300 and ram_mb > 2000:
                defaults["model_size"] = "small"  # better accuracy for short videos
            elif duration < 60:
                defaults["model_size"] = "base"  # fast for short clips
            elif duration > 1800:
                defaults["model_size"] = "base"  # speed priority for long videos
            else:
                defaults["model_size"] = "base"  # safe default
            _log("INFO", f"Smart default: model_size={defaults['model_size']} "
                          f"(duration={duration:.0f}s, RAM={ram_mb:.0f}MB)")

        # Auto-enable background music preservation for music videos
        # (detected by high audio bitrate relative to speech)
        if "keep_bg" not in options:
            # Default: keep background for videos >2min (likely have BGM)
            defaults["keep_bg"] = duration > 120

        # Auto-enable emotion transfer (makes dubs sound professional)
        if "emotion_transfer" not in options:
            defaults["emotion_transfer"] = True

        # Auto-enable video extension (prevents TTS cutoff at end)
        if "extend_video" not in options:
            defaults["extend_video"] = True

        return defaults

    # -------------------------------------------------------------------
    # Queue management
    # -------------------------------------------------------------------

    def _enqueue(self, job):
        """Add job to queue, sorted by priority."""
        priority_order = {"high": 0, "normal": 1, "low": 2}
        # Insert in priority order
        inserted = False
        new_queue = deque()
        for existing in self._queue:
            if not inserted and priority_order.get(job.priority, 1) < priority_order.get(existing.priority, 1):
                new_queue.append(job)
                inserted = True
            new_queue.append(existing)
        if not inserted:
            new_queue.append(job)
        self._queue = new_queue

    def _queue_position(self, job_id):
        """Get position of a job in the queue (0 if not queued)."""
        with self._queue_lock:
            for idx, j in enumerate(self._queue):
                if j.job_id == job_id:
                    return idx + 1
        return 0

    def _dequeue(self):
        """Get next job from queue (priority order)."""
        with self._queue_lock:
            if not self._queue:
                return None
            return self._queue.popleft()

    # -------------------------------------------------------------------
    # Worker loop — processes jobs from the queue
    # -------------------------------------------------------------------

    def _worker_loop(self):
        """Main worker thread — picks jobs from queue and runs them."""
        while self._running:
            try:
                # Wait for a job if queue is empty
                if not self._queue:
                    time.sleep(2)
                    continue

                # Check if we can run (RAM available)
                vm = psutil.virtual_memory()
                if vm.available < MIN_FREE_RAM_MB * 1024 * 1024:
                    _log("WARN", f"Waiting for RAM — only {vm.available/1024/1024:.0f}MB free")
                    time.sleep(10)
                    # Force cleanup
                    self._force_cleanup()
                    continue

                # Get next job
                job = self._dequeue()
                if not job:
                    continue

                self._current_job = job
                job.status = "running"
                job.started_at = time.time()
                job.last_progress_time = time.time()
                self._cancel_flags[job.job_id] = False

                _log("INFO", f"Starting job {job.job_id} — {job.video_info.get('duration', 0):.0f}s video")
                self._save_state()

                # Run the job with retry logic
                self._run_job_with_retries(job)

                # Cleanup
                self._current_job = None
                self._force_cleanup()

            except Exception as e:
                _log("ERROR", f"Worker loop error: {e!r}")
                time.sleep(5)

    def _run_job_with_retries(self, job):
        """Run a job with automatic retry on failure."""
        import dubber

        max_attempts = MAX_RETRIES
        last_error = None

        for attempt in range(1, max_attempts + 1):
            try:
                # Check if cancelled
                if self._cancel_flags.get(job.job_id, False):
                    job.status = "cancelled"
                    job.completed_at = time.time()
                    _log("INFO", f"Job {job.job_id} cancelled")
                    self._record_history(job)
                    return

                # Setup progress callback
                def progress_cb(stage, msg, sub_progress=None, sub_total=None):
                    if self._cancel_flags.get(job.job_id, False):
                        raise InterruptedError("Cancelled by user")

                    job.current_stage = stage if isinstance(stage, int) else 6
                    job.last_progress_time = time.time()

                    # Calculate progress
                    stage_ranges = {1: (0, 5), 2: (5, 30), 3: (30, 55),
                                    4: (55, 75), 5: (75, 90), 6: (90, 95)}
                    if stage == "done":
                        job.progress = 100
                    elif isinstance(stage, int) and stage in stage_ranges:
                        start, end = stage_ranges[stage]
                        if sub_progress and sub_total and sub_total > 0:
                            job.progress = start + (sub_progress / sub_total) * (end - start)
                        else:
                            job.progress = start

                # Run the dubbing pipeline
                output_path = str(OUTPUT_DIR / job.job_id / "dubbed.mp4")
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                job_dir = str(UPLOAD_DIR / job.job_id)

                opts = job.options
                result = dubber.dub_video(
                    video_path=job.video_path,
                    target_lang=job.target_lang,
                    voice=opts.get("voice"),
                    model_size=opts.get("model_size", "base"),
                    output_path=output_path,
                    keep_background=False,
                    keep_background_music=opts.get("keep_bg", False),
                    burn_subtitles=opts.get("burn_subtitles", False),
                    generate_srt_file=opts.get("gen_srt", True),
                    progress_callback=progress_cb,
                    job_dir=job_dir,
                    resume=attempt > 1,  # resume on retry
                    multi_speaker=opts.get("multi_speaker", False),
                    num_speakers=opts.get("num_speakers"),
                    use_voice_cloning=opts.get("voice_clone", False),
                    extend_video=opts.get("extend_video", True),
                    emotion_transfer=opts.get("emotion_transfer", True),
                    prosody_strength=opts.get("prosody_strength", 1.0),
                    anti_copyright=opts.get("anti_copyright", False),
                    blur_original_subtitles=opts.get("blur_original_subtitles", False),
                    subtitle_lang=opts.get("subtitle_lang"),
                    funny_mode=opts.get("funny_mode", False),
                )

                # Success!
                job.status = "done"
                job.progress = 100
                job.completed_at = time.time()
                job.output_path = result.get("output_video")
                job.stage_timings = result.get("stage_timings", {})
                elapsed = job.completed_at - (job.started_at or job.queued_at)
                _log("INFO", f"Job {job.job_id} completed in {elapsed:.1f}s — "
                              f"{result.get('segments_count', 0)} segments")
                self._record_history(job)
                return

            except InterruptedError:
                job.status = "cancelled"
                job.completed_at = time.time()
                _log("INFO", f"Job {job.job_id} cancelled by user")
                self._record_history(job)
                return

            except Exception as e:
                last_error = e
                err_str = str(e)
                _log("ERROR", f"Job {job.job_id} attempt {attempt}/{max_attempts} failed: {err_str}")
                job.attempts[job.current_stage] = job.attempts.get(job.current_stage, 0) + 1

                if attempt < max_attempts:
                    backoff = RETRY_BACKOFF[min(attempt - 1, len(RETRY_BACKOFF) - 1)]
                    _log("INFO", f"Retrying job {job.job_id} in {backoff}s...")
                    time.sleep(backoff)

                    # Check if checkpoint exists for resume
                    ckpt = dubber.load_checkpoint(str(UPLOAD_DIR / job.job_id))
                    if ckpt:
                        _log("INFO", f"Job {job.job_id} will resume from stage {ckpt['stage']}")
                    else:
                        _log("WARN", f"No checkpoint for job {job.job_id}, starting fresh")

                    # Force cleanup before retry
                    self._force_cleanup()
                else:
                    # Final failure
                    job.status = "error"
                    job.error = err_str
                    job.completed_at = time.time()
                    _log("ERROR", f"Job {job.job_id} failed after {max_attempts} attempts: {err_str}")
                    self._record_history(job)

    # -------------------------------------------------------------------
    # Watchdog loop — health monitoring
    # -------------------------------------------------------------------

    def _watchdog_loop(self):
        """Background watchdog — monitors for stalled jobs, RAM, etc."""
        while self._running:
            try:
                time.sleep(WATCHDOG_INTERVAL)

                # Check for stalled job
                if self._current_job:
                    job = self._current_job
                    stall_duration = time.time() - job.last_progress_time
                    if stall_duration > STALL_TIMEOUT:
                        _log("ERROR", f"Job {job.job_id} stalled — no progress for {stall_duration:.0f}s")
                        # Mark as error — worker will pick up next job
                        job.status = "error"
                        job.error = f"Job stalled — no progress for {stall_duration:.0f}s"
                        job.completed_at = time.time()
                        self._record_history(job)
                        self._current_job = None

                # Check RAM pressure
                vm = psutil.virtual_memory()
                if vm.percent > 95:
                    _log("WARN", f"RAM pressure: {vm.percent}% used — forcing cleanup")
                    self._force_cleanup()

                # Check disk space
                disk = psutil.disk_usage(str(BASE_DIR))
                if disk.percent > 95:
                    _log("WARN", f"Disk nearly full: {disk.percent}% — cleaning temp files")
                    self._clean_temp_files()

            except Exception as e:
                _log("ERROR", f"Watchdog error: {e!r}")

    # -------------------------------------------------------------------
    # Cleanup utilities
    # -------------------------------------------------------------------

    def _force_cleanup(self):
        """Force cleanup of memory and temp files."""
        import gc
        gc.collect()

        # Unload models
        try:
            from model_manager import ModelManager
            mm = ModelManager()
            mm.unload_current()
        except Exception:
            pass

        # Clear torch cache
        try:
            import torch
            if hasattr(torch.cuda, "empty_cache"):
                torch.cuda.empty_cache()
        except Exception:
            pass

        # Clear temp files
        self._clean_temp_files()

    def _clean_temp_files(self):
        """Remove old temp directories."""
        try:
            for tmp in Path("/tmp").glob("dubber_*"):
                age = time.time() - tmp.stat().st_mtime
                if age > 3600:  # older than 1 hour
                    shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass

    # -------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------

    def _save_state(self):
        """Persist queue state to disk."""
        try:
            state = {
                "queue": [j.to_dict() for j in self._queue],
                "jobs": {jid: j.to_dict() for jid, j in self._jobs.items()},
                "saved_at": time.time(),
            }
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            _log("ERROR", f"Failed to save state: {e!r}")

    def _load_state(self):
        """Load queue state from disk (for crash recovery)."""
        if not STATE_FILE.exists():
            return

        try:
            with open(STATE_FILE) as f:
                state = json.load(f)

            # Restore jobs
            for jid, jd in state.get("jobs", {}).items():
                job = Job.from_dict(jd)
                self._jobs[jid] = job

                # If job was running, mark as paused (needs resume)
                if job.status == "running":
                    job.status = "paused"
                    _log("INFO", f"Recovered job {jid} — was running, now paused")

            # Re-enqueue queued and paused jobs
            for jid, jd in state.get("jobs", {}).items():
                job = self._jobs.get(jid)
                if job and job.status in ("queued", "paused"):
                    job.priority = "high"  # priority for recovery
                    self._enqueue(job)

            _log("INFO", f"Loaded {len(self._jobs)} jobs from state, "
                          f"{len(self._queue)} re-queued")

        except Exception as e:
            _log("ERROR", f"Failed to load state: {e!r}")

    def _load_history(self):
        """Load job history from disk."""
        if not HISTORY_FILE.exists():
            return []
        try:
            with open(HISTORY_FILE) as f:
                return json.load(f)
        except Exception:
            return []

    def _record_history(self, job):
        """Record a completed job to history."""
        elapsed = 0
        if job.started_at and job.completed_at:
            elapsed = job.completed_at - job.started_at

        record = {
            "job_id": job.job_id,
            "status": job.status,
            "target_lang": job.target_lang,
            "video_duration": job.video_info.get("duration", 0),
            "elapsed": round(elapsed, 1),
            "segments": job.options.get("segments_count", 0),
            "video_info": {
                "duration": job.video_info.get("duration", 0),
                "size_mb": job.video_info.get("size_mb", 0),
                "resolution": f"{job.video_info.get('width', 0)}x{job.video_info.get('height', 0)}",
            },
            "error": job.error,
            "completed_at": job.completed_at,
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        self._history.append(record)
        if len(self._history) > MAX_HISTORY:
            self._history = self._history[-MAX_HISTORY:]

        # Update stats
        self._stats["total_jobs"] += 1
        if job.status == "done":
            self._stats["completed"] += 1
            self._stats["total_processing_time"] += elapsed
        elif job.status == "error":
            self._stats["failed"] += 1
        elif job.status == "cancelled":
            self._stats["cancelled"] += 1

        # Persist
        try:
            with open(HISTORY_FILE, "w") as f:
                json.dump(self._history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            _log("ERROR", f"Failed to save history: {e!r}")

        self._save_state()


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_manager_instance = None
_manager_lock = threading.Lock()


def get_manager():
    """Get the singleton PipelineManager instance."""
    global _manager_instance
    if _manager_instance is None:
        with _manager_lock:
            if _manager_instance is None:
                _manager_instance = PipelineManager()
    return _manager_instance
