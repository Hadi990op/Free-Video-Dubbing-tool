#!/usr/bin/env python3
"""Auto-cleanup for old video-dubber jobs.
Removes output and upload directories older than max_age_hours.
Also cleans up any stale temp files.
"""
import os
import time
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).parent
OUTPUTS_DIR = BASE_DIR / "outputs"
UPLOADS_DIR = BASE_DIR / "uploads"
MAX_AGE_HOURS = 24

def cleanup_dir(dir_path: Path, max_age_hours: float):
    """Remove subdirectories older than max_age_hours."""
    if not dir_path.exists():
        return 0
    now = time.time()
    max_age_seconds = max_age_hours * 3600
    removed = 0
    for item in dir_path.iterdir():
        if not item.is_dir():
            continue
        try:
            mtime = item.stat().st_mtime
            if (now - mtime) > max_age_seconds:
                shutil.rmtree(item, ignore_errors=True)
                removed += 1
                print(f"  Removed: {item}")
        except Exception as e:
            print(f"  Error checking {item}: {e}")
    return removed

def main():
    print(f"Cleanup: removing dirs older than {MAX_AGE_HOURS}h")
    out_removed = cleanup_dir(OUTPUTS_DIR, MAX_AGE_HOURS)
    up_removed = cleanup_dir(UPLOADS_DIR, MAX_AGE_HOURS)
    print(f"Done: removed {out_removed} output dirs, {up_removed} upload dirs")

if __name__ == "__main__":
    main()
