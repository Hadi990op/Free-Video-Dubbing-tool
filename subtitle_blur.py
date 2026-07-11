"""
Subtitle blur module — detects and blurs hardcoded (burned-in) subtitles
from video frames using OpenCV text detection + ffmpeg delogo/boxblur.

Approach:
  1. Sample several frames from the video at evenly spaced intervals.
  2. For each frame, look in the bottom 30% (typical subtitle area) and
     also top 15% (some videos have top subtitles).
  3. Detect text-like regions using OpenCV:
     - Convert to grayscale
     - Apply adaptive thresholding (subtitles are usually bright text on
       darker background, or dark text on lighter background)
     - Use morphological operations to find text bounding boxes
     - Filter by aspect ratio and size to keep only subtitle-like regions
  4. Merge overlapping/adjacent boxes into one subtitle band.
  5. Return the bounding box (x, y, w, h) of the subtitle region.
  6. Apply ffmpeg delogo or boxblur+overlay to blur that region.

This is intentionally lightweight (no heavy OCR models needed) — we only
need to DETECT where text is, not READ it. OpenCV edge/threshold detection
is sufficient and runs in milliseconds per frame.
"""

import subprocess
import os
import sys
import json
import tempfile
from typing import List, Tuple, Optional


def extract_sample_frames(video_path: str, num_frames: int = 8,
                          work_dir: str = None) -> List[str]:
    """Extract evenly-spaced frames from the video for analysis.

    Returns list of paths to extracted PNG frames.
    """
    if work_dir is None:
        work_dir = tempfile.mkdtemp(prefix="subtitle_blur_")
    os.makedirs(work_dir, exist_ok=True)

    # Get video duration
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, timeout=30
    )
    duration = float(result.stdout.strip() or "0")
    if duration <= 0:
        return []

    frame_paths = []
    # Sample frames from 10% to 90% of the video (avoid intro/outro cards)
    for i in range(num_frames):
        t = duration * (0.1 + 0.8 * i / max(num_frames - 1, 1))
        frame_path = os.path.join(work_dir, f"frame_{i:02d}.png")
        result = subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", video_path,
             "-frames:v", "1", "-vf", "scale=-1:480", frame_path],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0 and os.path.exists(frame_path):
            frame_paths.append(frame_path)

    return frame_paths


def detect_text_in_region(frame, y_start, y_end, w, h):
    """Detect text regions in a horizontal band of the frame.

    Uses row-based brightness analysis: subtitles create rows with
    significantly more bright (or dark) pixels than surrounding rows.

    Returns list of (x, y, w, h) bounding boxes in full-frame coordinates.
    """
    import cv2
    import numpy as np

    if y_end <= y_start:
        return []

    region = frame[y_start:y_end, :]
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)

    # Check the average brightness of the region — if it's very dark or very
    # bright, we can't use that threshold direction.
    region_mean = np.mean(gray)

    boxes = []

    # --- Method 1: Bright text on dark background ---
    # Only if the region is dark enough for bright text to stand out
    if region_mean < 180:
        _, thresh_bright = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
        bright_boxes = _find_text_blocks(thresh_bright, y_start, w)
        # Filter: bright text should be a small fraction of the region
        # (real text is sparse, not solid blocks)
        for box in bright_boxes:
            bx, by, bw, bh = box
            block_region = thresh_bright[by - y_start:by - y_start + bh, bx:bx + bw]
            fill_ratio = np.sum(block_region > 0) / max(block_region.size, 1)
            if fill_ratio < 0.5:  # text should be <50% of the block area
                boxes.append(box)

    # --- Method 2: Dark text on light background ---
    # Only if the region is light enough for dark text to stand out
    if region_mean > 80:
        _, thresh_dark = cv2.threshold(gray, 55, 255, cv2.THRESH_BINARY_INV)
        dark_boxes = _find_text_blocks(thresh_dark, y_start, w)
        # Filter: dark text should be a small fraction of the region
        for box in dark_boxes:
            bx, by, bw, bh = box
            block_region = thresh_dark[by - y_start:by - y_start + bh, bx:bx + bw]
            fill_ratio = np.sum(block_region > 0) / max(block_region.size, 1)
            if fill_ratio < 0.5:  # text should be <50% of the block area
                boxes.append(box)

    return boxes


def _find_text_blocks(thresh, y_offset, frame_width):
    """Find contiguous text blocks in a binary image using row analysis.

    Returns list of (x, y, w, h) in full-frame coordinates.
    """
    import numpy as np

    h, w = thresh.shape[:2]

    # Row density: fraction of bright pixels per row
    row_sums = np.sum(thresh, axis=1)
    row_density = row_sums / (w * 255)

    # Find rows with significant text content (>2% bright pixels)
    text_rows = np.where(row_density > 0.02)[0]

    if len(text_rows) == 0:
        return []

    # Group contiguous rows into blocks
    blocks = []
    block_start = text_rows[0]
    prev = text_rows[0]
    for r in text_rows[1:]:
        if r > prev + 5:  # gap threshold (rows)
            blocks.append((block_start, prev))
            block_start = r
        prev = r
    blocks.append((block_start, prev))

    boxes = []
    for bs, be in blocks:
        height = be - bs + 1
        if height < 4:  # too small to be text (min 4 rows)
            continue

        # Find horizontal extent of text in this block
        block_region = thresh[bs:be + 1, :]
        col_sums = np.sum(block_region, axis=0)
        text_cols = np.where(col_sums > 0)[0]

        if len(text_cols) == 0:
            continue

        x_min = text_cols[0]
        x_max = text_cols[-1]
        width = x_max - x_min + 1

        # Filter: subtitle text should be at least 15% of frame width
        if width < frame_width * 0.15:
            continue

        # Add padding
        pad_x = 8
        pad_y = 4
        x_min = max(0, x_min - pad_x)
        x_max = min(frame_width - 1, x_max + pad_x)
        y_min = max(0, y_offset + bs - pad_y)
        y_max = min(y_offset + be + pad_y, y_offset + h - 1)

        boxes.append((x_min, y_min, x_max - x_min, y_max - y_min))

    return boxes


def detect_subtitle_region(frame_paths: List[str]) -> Optional[List[Tuple[int, int, int, int]]]:
    """Detect subtitle regions from sample frames using OpenCV.

    Returns list of (x, y, w, h) tuples in frame coordinates (at sample height),
    or None if no subtitles detected.

    Strategy: for each frame, look in the bottom 30% and top 15% for text-like
    bright/dark regions using thresholding + row analysis. Collect boxes from
    all frames, then merge overlapping ones. A real subtitle band will appear
    in many frames at roughly the same y-position.
    """
    import cv2
    import numpy as np

    all_boxes = []

    for frame_path in frame_paths:
        frame = cv2.imread(frame_path)
        if frame is None:
            continue

        h, w = frame.shape[:2]

        # Check bottom 30% (primary subtitle area) and top 15% (secondary)
        regions_to_check = [
            ("bottom", int(h * 0.65), h),
            ("top", 0, int(h * 0.15)),
        ]

        for region_name, y_start, y_end in regions_to_check:
            boxes = detect_text_in_region(frame, y_start, y_end, w, h)
            all_boxes.extend(boxes)

    if not all_boxes:
        return None

    # Merge overlapping/nearby boxes
    merged = merge_boxes(all_boxes, gap_threshold=25)

    # Filter: keep only reasonably-sized boxes
    # (subtitles are at least 15% of width, at most 90% of height)
    # We need the frame dimensions; use the first frame's size
    frame = cv2.imread(frame_paths[0])
    if frame is not None:
        fh, fw = frame.shape[:2]
        merged = [b for b in merged
                  if b[2] >= fw * 0.15 and b[2] <= fw * 1.05
                  and b[3] >= 8 and b[3] <= fh * 0.3]

    return merged if merged else None


def merge_boxes(boxes: List[Tuple[int, int, int, int]],
                gap_threshold: int = 20) -> List[Tuple[int, int, int, int]]:
    """Merge overlapping or nearby bounding boxes into larger boxes."""
    if not boxes:
        return []

    # Sort by y coordinate
    sorted_boxes = sorted(boxes, key=lambda b: b[1])

    merged = []
    current = list(sorted_boxes[0])

    for box in sorted_boxes[1:]:
        x, y, w, h = box
        cx, cy, cw, ch = current

        # Check if this box overlaps or is close to the current merged box
        if (y <= cy + ch + gap_threshold and
            x <= cx + cw + gap_threshold and
            x + w >= cx - gap_threshold):
            # Merge
            new_x = min(cx, x)
            new_y = min(cy, y)
            new_w = max(cx + cw, x + w) - new_x
            new_h = max(cy + ch, y + h) - new_y
            current = [new_x, new_y, new_w, new_h]
        else:
            merged.append(tuple(current))
            current = list(box)

    merged.append(tuple(current))
    return merged


def build_subtitle_blur_filter(boxes: List[Tuple[int, int, int, int]],
                               video_width: int, video_height: int,
                               sample_height: int = 480,
                               blur_strength: int = 20) -> str:
    """Build an ffmpeg -vf filter chain to blur the detected subtitle regions.

    Coordinates from detection are at sample_height resolution; we scale them
    to the actual video resolution.

    Uses boxblur + overlay for each detected region.
    """
    scale_y = video_height / sample_height
    scale_x = video_width / (sample_height * video_width / video_height) if False else 1.0
    # Actually we scaled with scale=-1:480, so width changed proportionally.
    # We need the actual sample width. Let's compute from the frame.
    # For now, just scale y proportionally and x by the width ratio.
    # The frames were scaled to height=480, so:
    actual_scale = video_height / sample_height

    filters = []
    for i, (x, y, w, h) in enumerate(boxes):
        # Scale to actual video resolution
        bx = int(x * actual_scale)
        by = int(y * actual_scale)
        bw = int(w * actual_scale)
        bh = int(h * actual_scale)

        # Clamp to video bounds
        bx = max(0, min(bx, video_width - 1))
        by = max(0, min(by, video_height - 1))
        bw = min(bw, video_width - bx)
        bh = min(bh, video_height - by)

        if bw < 10 or bh < 5:
            continue

        # Use delogo for each region (simpler than crop/boxblur/overlay chain)
        filters.append(f"delogo=x={bx}:y={by}:w={bw}:h={bh}")

    if not filters:
        return ""

    # If multiple regions, apply them in sequence
    return ",".join(filters)


def build_subtitle_blur_filter_complex(boxes: List[Tuple[int, int, int, int]],
                                       video_width: int, video_height: int,
                                       sample_height: int = 480,
                                       blur_strength: int = 25) -> str:
    """Build a filter_complex chain using crop+boxblur+overlay for stronger blur.

    This produces a heavier blur than delogo and is better for completely
    hiding subtitle text.
    """
    actual_scale = video_height / sample_height

    if not boxes:
        return ""

    # Build overlay chain: for each box, crop → blur → overlay back
    parts = []
    prev_label = "0:v"

    for i, (x, y, w, h) in enumerate(boxes):
        bx = int(x * actual_scale)
        by = int(y * actual_scale)
        bw = int(w * actual_scale)
        bh = int(h * actual_scale)

        bx = max(0, min(bx, video_width - 1))
        by = max(0, min(by, video_height - 1))
        bw = min(bw, video_width - bx)
        bh = min(bh, video_height - by)

        if bw < 10 or bh < 5:
            continue

        blur_label = f"blur{i}"
        overlay_label = f"ovl{i}"

        parts.append(
            f"[{prev_label}]crop={bw}:{bh}:{bx}:{by},"
            f"gblur=sigma={blur_strength}[{blur_label}];"
            f"[{prev_label}][{blur_label}]overlay={bx}:{by}[{overlay_label}]"
        )
        prev_label = overlay_label

    if prev_label == "0:v":
        return ""

    return ";".join(parts), prev_label


def detect_and_blur_subtitles(video_path: str, output_path: str = None,
                             work_dir: str = None,
                             num_sample_frames: int = 10) -> Optional[str]:
    """Full pipeline: detect subtitle regions and produce a blurred video.

    Returns path to the blurred video, or None if no subtitles detected.
    """
    import cv2

    # Get video dimensions
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-of", "csv=p=0", video_path],
        capture_output=True, text=True, timeout=30
    )
    parts = result.stdout.strip().split(",")
    if len(parts) < 2:
        return None
    video_width, video_height = int(parts[0]), int(parts[1])

    # Extract sample frames
    if work_dir is None:
        work_dir = tempfile.mkdtemp(prefix="subtitle_blur_")

    frame_paths = extract_sample_frames(video_path, num_sample_frames, work_dir)

    if not frame_paths:
        print("  [subtitle-blur] Could not extract frames for analysis")
        return None

    # Detect subtitle regions
    boxes = detect_subtitle_region(frame_paths)

    if not boxes:
        print("  [subtitle-blur] No hardcoded subtitles detected in sample frames")
        return None

    print(f"  [subtitle-blur] Detected {len(boxes)} subtitle region(s):")
    for i, (x, y, w, h) in enumerate(boxes):
        print(f"    Region {i+1}: ({x}, {y}) — {w}x{h}px (at 480px height)")

    # Build blur filter
    sample_height = 480  # frames were scaled to this height
    actual_scale = video_height / sample_height

    # Use delogo for simplicity and speed
    delogo_filters = []
    for x, y, w, h in boxes:
        bx = int(x * actual_scale)
        by = int(y * actual_scale)
        bw = int(w * actual_scale)
        bh = int(h * actual_scale)
        bx = max(0, min(bx, video_width - 1))
        by = max(0, min(by, video_height - 1))
        bw = min(bw, video_width - bx)
        bh = min(bh, video_height - by)
        if bw >= 10 and bh >= 5:
            delogo_filters.append(f"delogo=x={bx}:y={by}:w={bw}:h={bh}:show=0")

    if not delogo_filters:
        return None

    vf = ",".join(delogo_filters)

    # Apply blur
    if output_path is None:
        base, ext = os.path.splitext(video_path)
        output_path = f"{base}_subtitledit{ext}"

    # Get video duration for timeout
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, timeout=30
    )
    duration = float(result.stdout.strip() or "0")
    timeout = max(300, int(duration * 5))

    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        output_path
    ]

    print(f"  [subtitle-blur] Applying blur filter: {vf}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    if result.returncode != 0:
        print(f"  [subtitle-blur] ffmpeg failed: {result.stderr[-300:]}")
        return None

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        return None

    print(f"  [subtitle-blur] Subtitles blurred successfully → {output_path}")
    return output_path


def has_hardcoded_subtitles(video_path: str, work_dir: str = None,
                            num_sample_frames: int = 8) -> bool:
    """Quick check: does this video have hardcoded subtitles?

    Samples frames and checks for text-like regions.
    """
    if work_dir is None:
        work_dir = tempfile.mkdtemp(prefix="subtitle_check_")

    frame_paths = extract_sample_frames(video_path, num_sample_frames, work_dir)

    if not frame_paths:
        return False

    boxes = detect_subtitle_region(frame_paths)

    # Clean up
    for f in frame_paths:
        try:
            os.remove(f)
        except OSError:
            pass

    return boxes is not None and len(boxes) > 0


if __name__ == "__main__":
    # CLI test
    if len(sys.argv) < 2:
        print("Usage: python subtitle_blur.py <video_path> [output_path]")
        sys.exit(1)

    video = sys.argv[1]
    output = sys.argv[2] if len(sys.argv) > 2 else None

    result = detect_and_blur_subtitles(video, output)
    if result:
        print(f"\n✅ Subtitles blurred: {result}")
    else:
        print("\nℹ No subtitles detected or blur failed")
