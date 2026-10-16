import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from PIL import Image
import numpy as np

from config import FFMPEG_BIN, FFPROBE_BIN, PROJECTS_DIR

def normalize_path(path_str: str) -> str:
    if not path_str:
        return ""
    p = os.path.abspath(path_str)
    if os.name == 'nt' and len(p) >= 240 and not p.startswith('\\\\?\\'):
        p = '\\\\?\\' + p
    return p

def get_video_duration(video_path: str) -> float:
    try:
        cmd = [
            FFPROBE_BIN, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            normalize_path(video_path)
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace', check=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"[Warn] Could not get duration: {e}", file=sys.stderr)
        return 0.0

def images_are_similar(img1_path: str, img2_path: str, threshold: float = 0.94) -> bool:
    """Check if two consecutive images are near duplicates using downsampled grayscale normalized cross-correlation."""
    try:
        with Image.open(img1_path) as i1, Image.open(img2_path) as i2:
            i1_small = i1.convert('L').resize((64, 64))
            i2_small = i2.convert('L').resize((64, 64))
            arr1 = np.asarray(i1_small, dtype=np.float32)
            arr2 = np.asarray(i2_small, dtype=np.float32)
            
            arr1 -= np.mean(arr1)
            arr2 -= np.mean(arr2)
            std1 = np.std(arr1)
            std2 = np.std(arr2)
            if std1 < 1e-4 or std2 < 1e-4:
                return True
            corr = np.mean(arr1 * arr2) / (std1 * std2)
            return corr > threshold
    except Exception:
        return False

import re

def format_timestamp(seconds: float) -> str:
    """Format seconds into MM:SS string."""
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"

def select_storyboard_scenes(frames: list, target_count: int = 30) -> list:
    """Evenly select target_count frames from frames list covering the whole timeline from beginning to end."""
    if not frames:
        return []
    total = len(frames)
    if total <= target_count:
        return list(frames)
    if target_count <= 1:
        return [frames[0]]
        
    selected_indices = []
    step = (total - 1) / (target_count - 1)
    last_idx = -1
    for i in range(target_count):
        idx = int(round(i * step))
        idx = max(0, min(idx, total - 1))
        if idx <= last_idx and idx + 1 < total:
            idx = last_idx + 1
        last_idx = idx
        selected_indices.append(idx)
        
    return [frames[i] for i in selected_indices]


def parse_time_value(val) -> float:
    """Parse '01:30', '90', or float into seconds."""
    if val is None or val == "":
        return 0.0
    val_str = str(val).strip()
    if ":" in val_str:
        parts = val_str.split(":")
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    try:
        return float(val_str)
    except ValueError:
        return 0.0

def parse_range_value(val_str: str):
    """Parse range string like '01:00-02:30' or '60-150' into tuple (start_sec, end_sec)."""
    if not val_str or not str(val_str).strip():
        return None
    s_val = str(val_str).strip()
    if "-" in s_val or "–" in s_val:
        parts = [p.strip() for p in re.split(r"[-–]", s_val) if p.strip()]
        if len(parts) >= 2:
            s = parse_time_value(parts[0])
            e = parse_time_value(parts[1])
            if e > s:
                return (s, e)
    return None

def extract_keyframes(
    video_path: str,
    output_dir: str,
    scene_threshold: float = 0.28,
    min_interval_sec: float = 2.0,
    skip_intro_sec: float = 90.0,
    skip_outro_sec: float = 90.0,
    intro_range_str: str = "",
    outro_range_str: str = "",
    max_frames: int = 350,
    auto_detect_op_ed: bool = False
) -> list:
    """Extract distinct keyframes from raw anime video, avoiding OP/ED and near duplicates."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    duration = get_video_duration(video_path)
    
    # Parse OP and ED cut ranges
    intro_range = parse_range_value(intro_range_str)
    outro_range = parse_range_value(outro_range_str)
    
    # Auto-detect OP/ED from video if enabled and ranges not manually specified
    if auto_detect_op_ed and (not intro_range or not outro_range):
        try:
            from op_ed_detector import detect_op_ed
            print("[*] 🔍 Auto-detecting OP/ED ranges from video...")
            det = detect_op_ed(video_path)
            if det.get("success"):
                if not intro_range and det.get("op"):
                    intro_range = (det["op"]["start"], det["op"]["end"])
                    print(f"[+] 🎯 Auto-detected OP: {det['op']['start_str']} - {det['op']['end_str']} ({det['op']['start']:.1f}s - {det['op']['end']:.1f}s, Conf: {det['op'].get('confidence')})")
                if not outro_range and det.get("ed"):
                    outro_range = (det["ed"]["start"], det["ed"]["end"])
                    print(f"[+] 🎯 Auto-detected ED: {det['ed']['start_str']} - {det['ed']['end_str']} ({det['ed']['start']:.1f}s - {det['ed']['end']:.1f}s, Conf: {det['ed'].get('confidence')})")
        except Exception as e:
            print(f"[Warn] Auto OP/ED detection skipped: {e}", file=sys.stderr)

    # Determine start_time for FFmpeg seek
    if intro_range:
        op_start, op_end = intro_range
        if op_start <= 2.0:
            start_time = min(op_end, max(0.0, duration - 120.0))
        else:
            # Cold open exists before OP: seek from beginning and filter OP frames in Python loop
            start_time = 0.0
    else:
        start_time = min(skip_intro_sec, max(0.0, duration - 120.0)) if skip_intro_sec > 0 else 0.0
        if start_time > 0:
            intro_range = (0.0, start_time)

    # Determine outro bounds
    has_post_credit = False
    if outro_range:
        ed_start, ed_end = outro_range
        if ed_end < duration - 5.0:
            has_post_credit = True
    else:
        ed_start = max(0.0, duration - skip_outro_sec) if skip_outro_sec > 0 and duration > 0 else duration
        ed_end = duration
        if skip_outro_sec > 0 and duration > 0:
            outro_range = (ed_start, ed_end)
            
    # If post-credit scene exists (ed_end before end of video), scan through full duration so post-credit keyframes are kept!
    if has_post_credit:
        effective_duration = max(10.0, duration - start_time) if duration > 0 else None
    else:
        effective_duration = max(10.0, ed_start - start_time) if duration > 0 else None
    
    safe_video_path = normalize_path(video_path)
    print(f"[*] Analyzing video: {safe_video_path}")
    print(f"[*] Total duration: {duration:.1f}s | Scan window: {start_time:.1f}s to {start_time + (effective_duration or 0):.1f}s")
    if intro_range:
        print(f"[*] ✂️ Filter OP (Opening Theme): {format_timestamp(intro_range[0])} - {format_timestamp(intro_range[1])} ({intro_range[0]:.1f}s - {intro_range[1]:.1f}s)")
    if outro_range:
        print(f"[*] ✂️ Filter ED (Ending Theme): {format_timestamp(outro_range[0])} - {format_timestamp(outro_range[1])} ({outro_range[0]:.1f}s - {outro_range[1]:.1f}s)")
        if has_post_credit:
            print(f"[*] 🛡️ Protected Post-credit scene: {format_timestamp(ed_end)} - {format_timestamp(duration)} (kept in storyboard)")
    
    temp_pattern = str(out_path / "raw_frame_%05d.jpg")
    
    # Clean previous raw frames and final frames
    for old_f in list(out_path.glob("raw_frame_*.jpg")) + list(out_path.glob("frame_*.jpg")):
        try:
            old_f.unlink()
        except OSError:
            pass
            
    ffmpeg_cmd = [
        FFMPEG_BIN, "-y",
        "-ss", str(start_time),
    ]
    if effective_duration:
        ffmpeg_cmd += ["-t", str(effective_duration)]
        
    ffmpeg_cmd += [
        "-i", safe_video_path,
        "-vf", f"select='gt(scene,{scene_threshold})',showinfo,scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black",
        "-vsync", "vfr",
        "-q:v", "2",
        temp_pattern
    ]
    
    print("[*] Running FFmpeg scene detection with timestamp extraction...")
    proc = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
    
    # Parse showinfo pts_time from stderr
    pts_matches = [float(m) for m in re.findall(r"pts_time:([\d\.]+)", proc.stderr or "")]
    raw_frames = sorted(list(out_path.glob("raw_frame_*.jpg")))
    
    # Fallback: if scene detection extracted too few frames (e.g. slow anime episode), do interval sampling
    interval_seconds = 3.5
    if len(raw_frames) < 20:
        print(f"[*] Scene detection yielded few frames ({len(raw_frames)}). Falling back to interval sampling (every {interval_seconds}s)...")
        for f in raw_frames:
            f.unlink()
        interval_cmd = [
            FFMPEG_BIN, "-y",
            "-ss", str(start_time),
        ]
        if effective_duration:
            interval_cmd += ["-t", str(effective_duration)]
        interval_cmd += [
            "-i", video_path,
            "-vf", f"fps=1/{interval_seconds},scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black",
            "-q:v", "2",
            temp_pattern
        ]
        subprocess.run(interval_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
        raw_frames = sorted(list(out_path.glob("raw_frame_*.jpg")))
        pts_matches = [i * interval_seconds for i in range(len(raw_frames))]

    print(f"[*] Extracted {len(raw_frames)} raw candidates. Filtering near-duplicates and mapping timestamps...")
    
    # Map raw frames to their timestamps
    raw_with_ts = []
    for idx, raw in enumerate(raw_frames):
        if idx < len(pts_matches):
            ts = round(start_time + pts_matches[idx], 1)
        else:
            ratio = (idx / max(1, len(raw_frames) - 1))
            ts = round(start_time + ratio * (effective_duration or 60.0), 1)
        raw_with_ts.append((raw, ts))
    
    def is_in_cut_range(ts_val: float) -> bool:
        if intro_range and (intro_range[0] - 0.5 <= ts_val <= intro_range[1] + 0.5):
            return True
        if outro_range and (outro_range[0] - 0.5 <= ts_val <= outro_range[1] + 0.5):
            return True
        return False

    # 1. Filter OP/ED and near-duplicates across ALL candidates from entire timeline
    valid_candidates = []
    last_cand = None
    skipped_op_ed = 0
    skipped_duplicates = 0
    
    for raw, ts in raw_with_ts:
        # Check if frame falls in OP or ED cut range
        if is_in_cut_range(ts):
            try:
                raw.unlink()
            except OSError:
                pass
            skipped_op_ed += 1
            continue

        if last_cand is not None and images_are_similar(str(last_cand[0]), str(raw)):
            try:
                raw.unlink()
            except OSError:
                pass
            skipped_duplicates += 1
            continue
            
        valid_candidates.append((raw, ts))
        last_cand = (raw, ts)
            
    if skipped_op_ed > 0:
        print(f"[+] ✂️ Filtered out {skipped_op_ed} frames belonging to OP/ED ranges.")
    if skipped_duplicates > 0:
        print(f"[+] ✂️ Filtered out {skipped_duplicates} near-duplicate frames.")

    # 2. Evenly select max_frames from valid_candidates spanning the whole timeline
    total_valid = len(valid_candidates)
    if max_frames and total_valid > max_frames:
        print(f"[*] Total valid candidates ({total_valid}) exceeds max limit ({max_frames}). Selecting {max_frames} frames evenly across full timeline...")
        chosen_candidates = select_storyboard_scenes(valid_candidates, target_count=max_frames)
        chosen_paths = {item[0] for item in chosen_candidates}
        for raw, _ in valid_candidates:
            if raw not in chosen_paths:
                try:
                    raw.unlink()
                except OSError:
                    pass
    else:
        chosen_candidates = valid_candidates

    # 3. Rename chosen frames in sequential order and build manifest
    kept_frames = []
    for frame_idx, (raw, ts) in enumerate(chosen_candidates, start=1):
        final_name = out_path / f"frame_{frame_idx:04d}.jpg"
        if raw.resolve() != final_name.resolve():
            if final_name.exists():
                final_name.unlink()
            raw.rename(final_name)
            
        kept_frames.append({
            "id": f"frame_{frame_idx:04d}",
            "filename": final_name.name,
            "path": str(final_name.relative_to(PROJECTS_DIR.parent)),
            "index": frame_idx,
            "timestamp": ts,
            "time_str": format_timestamp(ts)
        })

    # Cleanup any remaining raw_frame files
    for rem in out_path.glob("raw_frame_*.jpg"):
        try:
            rem.unlink()
        except OSError:
            pass

    # Save manifest
    manifest_path = out_path / "frames_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(kept_frames, f, ensure_ascii=False, indent=2)
        
    print(f"[+] Successfully extracted {len(kept_frames)} high-quality keyframes with timestamps to {out_path}")
    return kept_frames

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Anime Keyframe Extractor")
    parser.add_argument("--video", required=True, help="Path to input anime video file")
    parser.add_argument("--out", required=True, help="Output folder to store extracted frames")
    parser.add_argument("--threshold", type=float, default=0.28, help="Scene cut threshold (0.1 - 0.5)")
    parser.add_argument("--max", type=int, default=300, help="Maximum frames to retain")
    parser.add_argument("--skip-intro", default="90", help="Seconds or MM:SS to skip from start for intro/OP (default: 90)")
    parser.add_argument("--skip-outro", default="90", help="Seconds or MM:SS to skip from end for outro/ED (default: 90)")
    parser.add_argument("--intro-range", default="", help="Specific OP range e.g. 01:00-02:30 or 60-150")
    parser.add_argument("--outro-range", default="", help="Specific ED range e.g. 21:30-23:00")
    parser.add_argument("--auto-detect-op-ed", action="store_true", help="Auto-detect OP and ED ranges from video")
    
    args = parser.parse_args()
    skip_intro = parse_time_value(args.skip_intro)
    skip_outro = parse_time_value(args.skip_outro)
    
    frames = extract_keyframes(
        args.video,
        args.out,
        scene_threshold=args.threshold,
        max_frames=args.max,
        skip_intro_sec=skip_intro,
        skip_outro_sec=skip_outro,
        intro_range_str=args.intro_range,
        outro_range_str=args.outro_range,
        auto_detect_op_ed=args.auto_detect_op_ed
    )
    print(f"Done! {len(frames)} frames saved.")
