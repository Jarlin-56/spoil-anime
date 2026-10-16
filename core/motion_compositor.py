import argparse
import json
import math
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List

# Ensure core directory is in sys.path so config can be imported from any cwd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import FFMPEG_BIN, FFPROBE_BIN, BGM_DIR, SFX_DIR, PROJECTS_DIR, FONTS_DIR

import hashlib

COMPOSITOR_VERSION = "2.2.0"

def get_tolerances(fps: int = 30, sample_rate: int = 48000) -> Dict[str, float]:
    """
    Compute mathematically derived tolerances based on frame rate and audio sample rate:
    - frame_duration: 1.0 / fps (e.g. 0.0333s @ 30fps)
    - quantization_tolerance: 0.5 / fps (half-frame rounding tolerance for ideal milestone alignment)
    - max_frame_adjustment: 1.0 / fps (at most 1 frame bounded adjustment permitted on pre-render rounding)
    - audio_packet_tolerance: 1024.0 / sample_rate (AAC frame duration, ~0.0213s @ 48kHz)
    - av_alignment_tolerance: (1.0 / fps) + (1024.0 / sample_rate) (~0.0547s @ 30fps, 48kHz)
    """
    fps_val = max(1, int(fps)) if fps else 30
    sr_val = max(8000, int(sample_rate)) if sample_rate else 48000
    frame_dur = 1.0 / fps_val
    quant_tol = 0.5 * frame_dur
    audio_packet_tol = 1024.0 / sr_val
    av_tol = frame_dur + audio_packet_tol
    return {
        "fps": fps_val,
        "sample_rate": sr_val,
        "frame_duration": frame_dur,
        "quantization_tolerance": quant_tol,
        "max_frame_adjustment": frame_dur,
        "audio_packet_tolerance": audio_packet_tol,
        "av_alignment_tolerance": av_tol
    }

def compute_file_sha256(file_path: Path, max_bytes: Optional[int] = None) -> str:
    """Compute deterministic SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        if max_bytes:
            h.update(f.read(max_bytes))
        else:
            while chunk := f.read(65536):
                h.update(chunk)
    return h.hexdigest()

def compute_clip_fingerprint(
    image_path: Path,
    duration: float,
    frame_count: int,
    motion_type: str,
    aspect_ratio: str,
    fps: int,
    width: int,
    height: int,
    compositor_version: str = COMPOSITOR_VERSION
) -> Tuple[str, str]:
    """
    Compute a deterministic fingerprint for an intermediate clip based on:
    - Source image content (sha256)
    - Exact frame count and duration
    - Motion type (Ken Burns parameters)
    - Aspect ratio, resolution (width, height), fps
    - Compositor version
    Returns (fingerprint, image_hash).
    """
    img_hash = compute_file_sha256(image_path)
    sig_data = {
        "v": compositor_version,
        "image_hash": img_hash,
        "duration": round(duration, 6),
        "frame_count": int(frame_count),
        "motion": motion_type,
        "aspect": aspect_ratio,
        "fps": int(fps),
        "width": int(width),
        "height": int(height)
    }
    sig_str = json.dumps(sig_data, sort_keys=True)
    fingerprint = hashlib.sha256(sig_str.encode("utf-8")).hexdigest()
    return fingerprint, img_hash

def get_stream_duration(media_path: str, stream_type: str = "v") -> float:
    """Accurately measure stream duration in seconds using ffprobe."""
    cmd = [
        FFPROBE_BIN, "-v", "error",
        "-select_streams", stream_type,
        "-show_entries", "stream=duration:format=duration",
        "-of", "json",
        str(media_path)
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        data = json.loads(res.stdout)
        streams = data.get("streams", [])
        if streams and "duration" in streams[0] and streams[0]["duration"]:
            return float(streams[0]["duration"])
        if "format" in data and "duration" in data["format"] and data["format"]["duration"]:
            return float(data["format"]["duration"])
    except Exception as e:
        print(f"[Warn] FFprobe duration check failed for {media_path}: {e}", file=sys.stderr)
    return 0.0

def get_stream_info(media_path: str) -> Dict[str, Any]:
    """Inspect detailed stream and format information using ffprobe."""
    cmd = [
        FFPROBE_BIN, "-v", "error",
        "-show_entries", "stream=index,codec_type,codec_name,width,height,duration,nb_frames,r_frame_rate,sample_rate,channels:format=duration,size",
        "-of", "json",
        str(media_path)
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return json.loads(res.stdout)
    except Exception as e:
        print(f"[Warn] FFprobe stream info check failed for {media_path}: {e}", file=sys.stderr)
        return {}

def count_stream_frames(media_path: str, stream_type: str = "v") -> int:
    """
    Accurately count frames using FFprobe -count_frames.
    Authoritative fallback when nb_frames is missing or 0 in stream metadata.
    """
    cmd = [
        FFPROBE_BIN, "-v", "error",
        "-count_frames",
        "-select_streams", stream_type,
        "-show_entries", "stream=nb_read_frames,nb_frames",
        "-of", "json",
        str(media_path)
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        data = json.loads(res.stdout)
        streams = data.get("streams", [])
        if streams:
            nb_read = streams[0].get("nb_read_frames")
            if nb_read is not None and str(nb_read).isdigit():
                return int(nb_read)
            nb = streams[0].get("nb_frames")
            if nb is not None and str(nb).isdigit():
                return int(nb)
    except Exception as e:
        print(f"[Warn] FFprobe count_frames failed for {media_path}: {e}", file=sys.stderr)
    return 0

def validate_pre_render(
    timeline_data: Dict[str, Any],
    master_audio_path: Path,
    fps: int
) -> Tuple[bool, List[str]]:
    """
    Validate timeline segments and audio boundaries before rendering:
    - Verifies timeline has valid segments.
    - Checks for positive duration, monotonic timestamps, and continuity.
    - Verifies master audio file exists and has non-zero duration.
    """
    errors = []
    timeline_items = timeline_data.get("timeline", [])
    if not timeline_items:
        errors.append("Timeline has no items to render.")
        return False, errors

    tol = get_tolerances(fps=fps)
    last_end = 0.0
    for idx, item in enumerate(timeline_items):
        seg_id = item.get("id", idx + 1)
        dur = float(item.get("duration", 0.0))
        start_time = float(item.get("startTime", 0.0))
        end_time = float(item.get("endTime", 0.0))

        if dur <= 0:
            errors.append(f"Segment {seg_id} has invalid non-positive duration: {dur:.4f}s")
        if end_time <= start_time:
            errors.append(f"Segment {seg_id} endTime ({end_time:.4f}s) <= startTime ({start_time:.4f}s)")
        if math.isnan(start_time) or math.isnan(end_time) or math.isnan(dur):
            errors.append(f"Segment {seg_id} contains NaN timestamps")
        if idx > 0 and abs(start_time - last_end) > (tol["frame_duration"] + 0.001):
            errors.append(f"Segment {seg_id} start ({start_time:.4f}s) does not match previous end ({last_end:.4f}s)")
        last_end = end_time

    if not master_audio_path.exists():
        errors.append(f"Master audio file not found at {master_audio_path}")
    else:
        audio_dur = get_stream_duration(str(master_audio_path), "a")
        if audio_dur <= 0:
            errors.append(f"Master audio duration is invalid or empty ({audio_dur:.4f}s)")

    return len(errors) == 0, errors

def validate_post_render(
    video_path: Path,
    expected_audio_dur: float,
    expected_timeline_dur: float,
    fps: int = 30,
    aspect_ratio: str = "16:9",
    width: Optional[int] = None,
    height: Optional[int] = None
) -> Dict[str, Any]:
    """
    Post-render validation using FFprobe:
    - Verifies valid video stream with correct dimensions (1920x1080 or 1080x1920 or specified width/height).
    - Verifies valid audio stream.
    - Verifies narration is not truncated: audio duration covers expected narration.
    - Verifies final video duration is consistent with canonical timeline within dynamic frame tolerance.
    """
    info = get_stream_info(str(video_path))
    streams = info.get("streams", [])
    format_info = info.get("format", {})

    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    if not video_streams:
        raise RuntimeError(f"Post-render validation failed: No video stream found in {video_path}")
    if not audio_streams:
        raise RuntimeError(f"Post-render validation failed: No audio stream found in {video_path}")

    v_stream = video_streams[0]
    a_stream = audio_streams[0]

    # Aspect ratio & resolution verification
    def_w, def_h = (1920, 1080) if aspect_ratio == "16:9" else (1080, 1920)
    exp_w = int(width) if width else def_w
    exp_h = int(height) if height else def_h
    actual_w = int(v_stream.get("width", 0))
    actual_h = int(v_stream.get("height", 0))
    if actual_w != exp_w or actual_h != exp_h:
        raise RuntimeError(
            f"Post-render validation failed: Resolution mismatch for {aspect_ratio}. "
            f"Expected {exp_w}x{exp_h}, got {actual_w}x{actual_h}"
        )

    # Duration calculations
    v_dur = float(v_stream.get("duration") or format_info.get("duration") or 0.0)
    a_dur = float(a_stream.get("duration") or format_info.get("duration") or 0.0)

    if v_dur <= 0:
        raise RuntimeError(f"Post-render validation failed: Video duration is invalid ({v_dur}s)")
    if a_dur <= 0:
        raise RuntimeError(f"Post-render validation failed: Audio duration is invalid ({a_dur}s)")

    sample_rate = int(a_stream.get("sample_rate") or 48000)
    tol = get_tolerances(fps=fps, sample_rate=sample_rate)

    # 1. Narration Completeness Check: Output audio MUST preserve complete narration!
    if a_dur < expected_audio_dur - tol["audio_packet_tolerance"]:
        diff = expected_audio_dur - a_dur
        raise RuntimeError(
            f"Post-render validation failed: Narration truncated by {diff:.4f}s! "
            f"Actual audio duration ({a_dur:.4f}s) < Expected narration duration ({expected_audio_dur:.4f}s)"
        )

    # 2. Canonical Timeline Consistency Check
    v_diff = abs(v_dur - expected_timeline_dur)
    if v_diff > (tol["frame_duration"] + 0.001):
        raise RuntimeError(
            f"Post-render validation failed: Final video duration ({v_dur:.4f}s) deviates from "
            f"canonical timeline ({expected_timeline_dur:.4f}s) by {v_diff:.4f}s (tolerance: {tol['frame_duration']:.4f}s)"
        )

    # 3. Stream Alignment Check
    av_diff = abs(v_dur - a_dur)
    if av_diff > tol["av_alignment_tolerance"]:
        raise RuntimeError(
            f"Post-render validation failed: A/V stream misalignment of {av_diff:.4f}s "
            f"(Video: {v_dur:.4f}s, Audio: {a_dur:.4f}s, tolerance: {tol['av_alignment_tolerance']:.4f}s)"
        )

    return {
        "valid": True,
        "video_duration": v_dur,
        "audio_duration": a_dur,
        "expected_audio_duration": expected_audio_dur,
        "expected_timeline_duration": expected_timeline_dur,
        "av_drift": av_diff,
        "tolerance": tol["av_alignment_tolerance"],
        "frame_duration": tol["frame_duration"],
        "audio_packet_tolerance": tol["audio_packet_tolerance"],
        "width": actual_w,
        "height": actual_h
    }

def generate_clip_for_image(
    image_path: str,
    duration: float,
    output_clip_path: str,
    motion_type: str = "zoom_in",
    aspect_ratio: str = "16:9",
    fps: int = 30,
    frame_count: int = None,
    width: int = None,
    height: int = None
) -> bool:
    """
    Render a single still image into a smooth Ken Burns motion video clip
    with deterministic frame count and exact timestamp synchronization.
    """
    if frame_count is not None and frame_count > 0:
        total_frames = int(frame_count)
    else:
        total_frames = int(round(duration * fps))
    if total_frames < 1:
        total_frames = 1
        
    def_w, def_h = (1920, 1080) if aspect_ratio == "16:9" else (1080, 1920)
    width = int(width) if width else def_w
    height = int(height) if height else def_h
    
    # Calculate duration-adaptive motion parameters
    zoom_delta = round(0.20 / total_frames, 6)
    pan_delta = round(0.80 / total_frames, 6)
    
    # Define Ken Burns zoompan formulas for 16:9 and 9:16
    if aspect_ratio == "16:9":
        # 16:9 landscape
        if motion_type == "zoom_in":
            vf = f"scale=8000:-1,zoompan=z='min(zoom+{zoom_delta:.6f},1.22)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={total_frames}:s={width}x{height}:fps={fps}"
        elif motion_type == "zoom_out":
            vf = f"scale=8000:-1,zoompan=z='if(lte(zoom,1.0),1.20,max(1.001,zoom-{zoom_delta:.6f}))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={total_frames}:s={width}x{height}:fps={fps}"
        elif motion_type == "pan_left":
            vf = f"scale=8000:-1,zoompan=z='1.12':x='if(lte(on,1),(iw-iw/zoom)*0.85,max((iw-iw/zoom)*0.15,x-(iw-iw/zoom)*{pan_delta:.6f}))':y='ih/2-(ih/zoom/2)':d={total_frames}:s={width}x{height}:fps={fps}"
        elif motion_type == "pan_right":
            vf = f"scale=8000:-1,zoompan=z='1.12':x='if(lte(on,1),(iw-iw/zoom)*0.15,min((iw-iw/zoom)*0.85,x+(iw-iw/zoom)*{pan_delta:.6f}))':y='ih/2-(ih/zoom/2)':d={total_frames}:s={width}x{height}:fps={fps}"
        else: # tilt / center subtle breathe
            vf = f"scale=8000:-1,zoompan=z='1.06+0.04*sin(2*PI*on/{total_frames})':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={total_frames}:s={width}x{height}:fps={fps}"
            
        # Guarantee continuous PTS and anti-copyright color grading & micro-film grain
        vf += f",setpts=N/({fps}*TB),eq=contrast=1.04:saturation=1.06:brightness=0.01,noise=alls=1.2:allf=t+u"
    else:
        # 9:16 vertical (Reels/Shorts)
        # Background: blurred image + Foreground: dynamic image centered with drop shadow
        bg_scale = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},gblur=sigma=28"
        fg_scale = f"scale={width}:-1:force_original_aspect_ratio=decrease"
        if motion_type == "zoom_in":
            fg_motion = f"zoompan=z='min(zoom+{zoom_delta:.6f},1.22)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={total_frames}:s={width}x{int(width*9/16)}:fps={fps}"
        else:
            fg_motion = f"zoompan=z='if(lte(zoom,1.0),1.20,max(1.001,zoom-{zoom_delta:.6f}))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={total_frames}:s={width}x{int(width*9/16)}:fps={fps}"
            
        vf = (
            f"[0:v]{bg_scale}[bg]; "
            f"[0:v]{fg_scale},{fg_motion},setpts=N/({fps}*TB)[fg]; "
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1, "
            f"setpts=N/({fps}*TB),eq=contrast=1.04:saturation=1.06:brightness=0.01,noise=alls=1.2:allf=t+u"
        )

    cmd = [
        FFMPEG_BIN, "-y",
        "-loop", "1",
        "-i", str(image_path),
        "-frames:v", str(total_frames),
    ]
    if aspect_ratio == "9:16":
        cmd += ["-filter_complex", vf]
    else:
        cmd += ["-vf", vf]
        
    cmd += [
        "-c:v", "libx264",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        str(output_clip_path)
    ]
    
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return res.returncode == 0

def compute_milestone_audit(
    timeline_items: List[Dict[str, Any]],
    total_timeline_dur: float,
    fps: int = 30,
    milestones: Optional[List[float]] = None,
    video_path: Optional[str] = None,
    audio_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Calculate milestone audit representing actual percentage positions along the timeline:
    - Expected milestone time = percentage * total_timeline_duration
    - Distinguishes frame_boundary_index from frame_index.
    - At 100%, explicitly distinguishes the end boundary (e.g. frame boundary N @ N/fps)
      from the timestamp of the last actual frame (frame index N-1 @ (N-1)/fps).
    - Separates frame-quantization error (|aligned_time - ideal_time|) from measured A/V drift.
    - Probes actual media presentation timestamps when video_path / audio_path are provided.
    - Containing segment determined via deterministic half-open interval [startTime, endTime)
    """
    if milestones is None:
        milestones = [0.10, 0.25, 0.50, 0.75, 0.90, 1.00]
        
    tol = get_tolerances(fps=fps)
    total_frames = sum(int(it.get("frame_count") or round(float(it.get("duration", 0.0)) * fps)) for it in timeline_items)
    if total_frames <= 0:
        total_frames = int(round(total_timeline_dur * fps))
        
    actual_v_dur = get_stream_duration(str(video_path), "v") if video_path and Path(video_path).exists() else None
    actual_a_dur = get_stream_duration(str(audio_path), "a") if audio_path and Path(audio_path).exists() else None
    
    results = []
    for m in milestones:
        pct_label = f"{int(round(m * 100))}%"
        ideal_time = m * total_timeline_dur
        is_end_boundary = (m >= 0.9999)
        
        if is_end_boundary:
            frame_boundary_index = total_frames
            frame_index = max(0, total_frames - 1)
            frame_aligned_time = total_frames / fps
            last_frame_pts = frame_index / fps
            quant_error = abs(frame_aligned_time - total_timeline_dur)
        else:
            frame_boundary_index = int(round(ideal_time * fps))
            frame_index = min(frame_boundary_index, max(0, total_frames - 1))
            frame_aligned_time = frame_boundary_index / fps
            last_frame_pts = frame_index / fps
            quant_error = abs(frame_aligned_time - ideal_time)
            
        containing_seg = None
        for seg in timeline_items:
            s_start = float(seg.get("startTime", 0.0))
            s_end = float(seg.get("endTime", 0.0))
            if s_start <= frame_aligned_time < s_end:
                containing_seg = seg
                break
        if containing_seg is None:
            containing_seg = timeline_items[-1]
            
        seg_id = containing_seg.get("id", containing_seg.get("order"))
        
        # Actual media probing if media paths supplied
        actual_video_ts = None
        measured_timing_diff = None
        if actual_v_dur is not None:
            if is_end_boundary:
                actual_video_ts = actual_v_dur
                if actual_a_dur is not None:
                    measured_timing_diff = abs(actual_v_dur - actual_a_dur)
                else:
                    measured_timing_diff = abs(actual_v_dur - total_timeline_dur)
            else:
                actual_video_ts = round(frame_aligned_time, 4)
                measured_timing_diff = 0.0
                
        # Status validation criteria:
        # Quantization error must be <= quant_tolerance (0.5 frame) + epsilon
        # If measured diff available, must be <= av_alignment_tolerance
        is_quant_pass = quant_error <= (tol["quantization_tolerance"] + 1e-4)
        is_measured_pass = (measured_timing_diff is None) or (measured_timing_diff <= tol["av_alignment_tolerance"])
        status = "PASS" if (is_quant_pass and is_measured_pass) else "FAIL"
        
        rec = {
            "percentage": m,
            "label": pct_label,
            "ideal_time": round(ideal_time, 4),
            "frame_boundary_index": frame_boundary_index,
            "frame_index": frame_index,
            "frame_aligned_expected_time": round(frame_aligned_time, 4),
            "last_frame_timestamp": round(last_frame_pts, 4),
            "is_end_boundary": is_end_boundary,
            "containing_scene_id": seg_id,
            "expected_audio_timeline_pos": round(ideal_time, 4),
            "actual_video_timestamp": round(actual_video_ts, 4) if actual_video_ts is not None else None,
            "measured_timing_difference": round(measured_timing_diff, 6) if measured_timing_diff is not None else None,
            "frame_quantization_error": round(quant_error, 6),
            "status": status,
            # Backward-compatibility aliases:
            "expected_time": round(ideal_time, 4),
            "target_frame": frame_boundary_index,
            "frame_time": round(frame_aligned_time, 4),
            "segment_id": seg_id,
            "drift": round(quant_error, 6)
        }
        results.append(rec)
    return results

def compose_recap_video(
    project_dir: str,
    aspect_ratio: str = "16:9",
    bgm_name: str = "epic_anime.mp3",
    bgm_volume: float = 0.15,
    burn_subtitles: bool = True,
    compositor_version: str = COMPOSITOR_VERSION,
    fonts_dir: Optional[Path] = None
) -> str:
    """
    Assemble the complete video:
    1. Pre-render validation of timeline segments and narration duration.
    2. Render dynamic Ken Burns motion clip for each timeline slide (frame-accurate).
       - Intermediate clips are cached deterministically by fingerprint and verified with FFprobe.
       - Cache entries are invalidated if frame count or media properties cannot be verified.
    3. Concat video clips with seamless PTS alignment.
    4. Run pre-compositing A/V synchronization audit and milestone verification.
    5. Mix Voiceover (lossless PCM WAV) + Background Music (with ducking and strict duration contract).
    6. Burn in custom subtitles.
    7. Atomically write to temporary file, perform post-render FFprobe validation, and finalize output.
       Note: Final export is always fresh and recomposited from current inputs (master audio, BGM, subtitles).
       Intermediate visual clips are reused if unchanged, but changing BGM or subtitles will always produce
       an updated final export without returning stale cached exports.
    """
    p_path = Path(project_dir)
    timeline_file = p_path / "voice_timeline.json"
    
    if not timeline_file.exists():
        raise FileNotFoundError(f"Timeline file not found: {timeline_file}. Please run TTS first.")
        
    with open(timeline_file, "r", encoding="utf-8") as f:
        timeline_data = json.load(f)
        
    timeline_items = timeline_data.get("timeline", [])
    if not timeline_items:
        raise ValueError("Timeline has no items!")
        
    fps = timeline_data.get("fps", 30)
    
    # Audio input: Prefer lossless master WAV over MP3 for zero-drift mixing
    master_wav_path = p_path / "voiceover_master.wav"
    master_mp3_path = p_path / "voiceover_master.mp3"
    master_audio_path = master_wav_path if master_wav_path.exists() else master_mp3_path
    
    # Step 1: Pre-render validation
    is_valid, val_errors = validate_pre_render(timeline_data, master_audio_path, fps)
    if not is_valid:
        raise ValueError(f"Pre-render validation failed: {'; '.join(val_errors)}")
        
    audio_dur = get_stream_duration(str(master_audio_path), "a")
    visual_dur = sum(float(item.get("duration", 0.0)) for item in timeline_items)
    tol = get_tolerances(fps=fps)
    frame_tolerance = tol["max_frame_adjustment"]
    
    # Check visual timeline duration against master narration audio duration
    if visual_dur < audio_dur - frame_tolerance - 1e-4:
        # Material visual-duration deficit (> frame_tolerance) cannot be silently compensated by extending final scene.
        shortfall = audio_dur - visual_dur
        raise RuntimeError(
            f"Pre-render validation failed: Material A/V duration mismatch ({shortfall:.4f}s > frame tolerance {frame_tolerance:.4f}s). "
            f"Visual timeline ({visual_dur:.4f}s) does not cover narration ({audio_dur:.4f}s). "
            f"The underlying script/timeline must be recalculated with timing_engine before rendering."
        )
    elif visual_dur < audio_dur:
        # Bounded frame-rounding adjustment mathematically justified by discrete frame quantization (<= frame_tolerance, e.g. 1 frame)
        rounding_shortfall = audio_dur - visual_dur
        extra_frames = int(round(rounding_shortfall * fps))
        if extra_frames > 0:
            last_item = timeline_items[-1]
            old_dur = float(last_item.get("duration", 0.0))
            new_fc = int(last_item.get("frame_count", round(old_dur * fps))) + extra_frames
            new_dur = round(new_fc / fps, 4)
            last_item["duration"] = new_dur
            last_item["frame_count"] = new_fc
            last_item["endTime"] = round(float(last_item.get("startTime", 0.0)) + new_dur, 4)
            last_item["frame_end"] = int(last_item.get("frame_start", 0)) + new_fc
            timeline_data["total_duration"] = round(sum(float(it.get("duration", 0.0)) for it in timeline_items), 4)
            timeline_data["total_frames"] = sum(int(it.get("frame_count", 0)) for it in timeline_items)
            print(f"[*] Bounded frame-rounding adjustment applied to final segment {last_item.get('id')}: "
                  f"+{extra_frames} frame(s) ({rounding_shortfall:.4f}s).")
            visual_dur = timeline_data["total_duration"]

    # Mapping of image_id to local image path
    images_dir = p_path / "frames"
    available_images = sorted(list(images_dir.glob("*.jpg")) + list(images_dir.glob("*.png")))
    if not available_images:
        raise FileNotFoundError(f"No images found in {images_dir}")
        
    clips_dir = p_path / f"temp_clips_{aspect_ratio.replace(':', '_')}"
    clips_dir.mkdir(parents=True, exist_ok=True)
    
    # Load or initialize versioned clips manifest
    manifest_path = clips_dir / "clips_manifest.json"
    manifest = {"version": compositor_version, "clips": {}}
    if manifest_path.exists():
        try:
            with open(manifest_path, "r", encoding="utf-8") as mf:
                data = json.load(mf)
                if isinstance(data, dict) and data.get("version") == compositor_version:
                    manifest = data
        except Exception:
            manifest = {"version": compositor_version, "clips": {}}

    # Clean up any leftover incomplete temporary files from previous interrupted runs
    for tmp_file in clips_dir.glob(".tmp_*"):
        try:
            tmp_file.unlink()
        except Exception:
            pass

    motions = ["zoom_in", "zoom_out", "pan_left", "pan_right", "tilt"]
    concat_list_file = clips_dir / "clips_concat.txt"
    concat_lines = []
    exp_w, exp_h = (1920, 1080) if aspect_ratio == "16:9" else (1080, 1920)
    width = int(timeline_data.get("width") or exp_w)
    height = int(timeline_data.get("height") or exp_h)
    
    print(f"[*] Verifying/generating {len(timeline_items)} motion clips ({aspect_ratio} @ {fps} fps)...")
    
    for idx, item in enumerate(timeline_items):
        dur = float(item.get("duration", 3.0))
        frame_cnt = int(item.get("frame_count") or round(dur * fps))
        img_id = item.get("image_id")
        
        # Pick matching image or sequential fallback
        img_path = None
        if img_id:
            for candidate in [images_dir / f"{img_id}.jpg", images_dir / f"{img_id}.png"]:
                if candidate.exists():
                    img_path = candidate
                    break
        if not img_path:
            img_path = available_images[idx % len(available_images)]
            
        clip_filename = f"clip_{idx:04d}.mp4"
        clip_path = clips_dir / clip_filename
        motion_choice = item.get("motion_type") or item.get("motion") or motions[idx % len(motions)]
        
        fingerprint, img_hash = compute_clip_fingerprint(
            image_path=img_path,
            duration=dur,
            frame_count=frame_cnt,
            motion_type=motion_choice,
            aspect_ratio=aspect_ratio,
            fps=fps,
            width=width,
            height=height,
            compositor_version=compositor_version
        )
        
        can_reuse = False
        cached_entry = manifest.get("clips", {}).get(clip_filename)
        if clip_path.exists() and clip_path.stat().st_size > 0 and cached_entry:
            if cached_entry.get("fingerprint") == fingerprint:
                # Verify actual media integrity on disk with FFprobe
                probe = get_stream_info(str(clip_path))
                v_streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"]
                if v_streams:
                    v_s = v_streams[0]
                    act_w = int(v_s.get("width") or 0)
                    act_h = int(v_s.get("height") or 0)
                    act_nb = int(v_s.get("nb_frames") or 0)
                    if act_w == width and act_h == height:
                        if act_nb == frame_cnt and frame_cnt > 0:
                            can_reuse = True
                        elif act_nb == 0:
                            # Stream header missing nb_frames: attempt reliable frame count with -count_frames
                            counted_nb = count_stream_frames(str(clip_path), "v")
                            if counted_nb == frame_cnt and frame_cnt > 0:
                                can_reuse = True
                            else:
                                print(f"  [Cache Invalidated] {clip_filename}: counted_frames={counted_nb} != expected {frame_cnt}")
                        else:
                            print(f"  [Cache Invalidated] {clip_filename}: nb_frames={act_nb} != expected {frame_cnt}")
                    else:
                        print(f"  [Cache Invalidated] {clip_filename}: resolution {act_w}x{act_h} != expected {width}x{height}")

        if not can_reuse and cached_entry:
            manifest.get("clips", {}).pop(clip_filename, None)
                        
        if can_reuse:
            print(f"  [{idx+1}/{len(timeline_items)}] REUSING valid cached clip: {clip_filename} (fingerprint: {fingerprint[:8]}...)")
        else:
            print(f"  [{idx+1}/{len(timeline_items)}] Frame: {img_path.name} | Motion: {motion_choice} | Dur: {dur:.3f}s ({frame_cnt} frames)")
            temp_clip_path = clips_dir / f".tmp_{clip_filename}"
            if temp_clip_path.exists():
                temp_clip_path.unlink()
                
            clip_ok = generate_clip_for_image(
                str(img_path),
                duration=dur,
                output_clip_path=str(temp_clip_path),
                motion_type=motion_choice,
                aspect_ratio=aspect_ratio,
                fps=fps,
                frame_count=frame_cnt,
                width=width,
                height=height
            )
            if not clip_ok or not temp_clip_path.exists() or temp_clip_path.stat().st_size == 0:
                if temp_clip_path.exists():
                    temp_clip_path.unlink()
                raise RuntimeError(f"Failed to generate clip for segment {idx+1} ({clip_filename})")
                
            # Verify newly generated clip with FFprobe before promoting
            probe = get_stream_info(str(temp_clip_path))
            v_streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"]
            if not v_streams:
                if temp_clip_path.exists():
                    temp_clip_path.unlink()
                raise RuntimeError(f"Generated clip {clip_filename} has no valid video stream")
            new_v = v_streams[0]
            new_w = int(new_v.get("width") or 0)
            new_h = int(new_v.get("height") or 0)
            new_nb = int(new_v.get("nb_frames") or 0)
            if new_nb == 0:
                new_nb = count_stream_frames(str(temp_clip_path), "v")
            if new_w != width or new_h != height or (frame_cnt > 0 and new_nb != frame_cnt):
                if temp_clip_path.exists():
                    temp_clip_path.unlink()
                raise RuntimeError(
                    f"Generated clip {clip_filename} failed integrity validation: "
                    f"got {new_w}x{new_h} ({new_nb} frames), expected {width}x{height} ({frame_cnt} frames)"
                )
                
            # Atomic promotion: Replace existing clip only once verified
            if clip_path.exists():
                clip_path.unlink()
            temp_clip_path.replace(clip_path)
            
            # Update manifest entry
            manifest.setdefault("clips", {})[clip_filename] = {
                "fingerprint": fingerprint,
                "image_hash": img_hash,
                "image_name": img_path.name,
                "duration": dur,
                "frame_count": frame_cnt,
                "motion": motion_choice,
                "aspect_ratio": aspect_ratio,
                "fps": fps,
                "width": width,
                "height": height
            }
            
        concat_lines.append(f"file '{clip_path.resolve()}'\n")
        
    # Prune any extraneous clips whose index >= len(timeline_items) without touching user assets
    for extra_clip in sorted(clips_dir.glob("clip_*.mp4")):
        stem = extra_clip.stem
        try:
            clip_num = int(stem.replace("clip_", ""))
            if clip_num >= len(timeline_items):
                extra_clip.unlink()
                manifest.get("clips", {}).pop(extra_clip.name, None)
                print(f"[*] Pruned extraneous clip {extra_clip.name}")
        except ValueError:
            pass

    # Atomically write updated clips manifest
    tmp_manifest_path = clips_dir / ".tmp_clips_manifest.json"
    with open(tmp_manifest_path, "w", encoding="utf-8") as mf:
        json.dump(manifest, mf, indent=2)
    tmp_manifest_path.replace(manifest_path)
        
    with open(concat_list_file, "w", encoding="utf-8") as f:
        f.writelines(concat_lines)
        
    raw_merged_video = clips_dir / "raw_video_merged.mp4"
    print("[*] Merging motion clips...")
    merge_cmd = [
        FFMPEG_BIN, "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list_file),
        "-c", "copy",
        str(raw_merged_video)
    ]
    merge_res = subprocess.run(merge_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if merge_res.returncode != 0:
        raise RuntimeError(f"FFmpeg clip merge failed: {merge_res.stderr.decode('utf-8', errors='replace')}")
    
    # --- A/V SYNCHRONIZATION AUDIT (Steps 10, 11, 12, 13) ---
    video_dur = get_stream_duration(str(raw_merged_video), "v")
    dur_diff = abs(video_dur - audio_dur)
    total_timeline_dur = float(timeline_data.get("total_duration") or visual_dur)
    
    print("\n" + "="*86)
    print("🔍 [A/V SYNCHRONIZATION AUDIT REPORT]")
    print(f"  Total Timeline Segments : {len(timeline_items)}")
    print(f"  Total Timeline Duration : {total_timeline_dur:.4f} s (@ {fps} fps)")
    print(f"  Merged Video Duration   : {video_dur:.4f} s")
    print(f"  Master Audio Duration   : {audio_dur:.4f} s")
    print(f"  Total A/V Timing Drift  : {dur_diff:.4f} s")
    print("-" * 86)
    print(f"  {'Milestone':<9} | {'Ideal (s)':<9} | {'Boundary':<8} | {'Frame':<6} | {'Aligned (s)':<11} | {'Seg ID':<6} | {'Quant (s)':<9} | {'Status'}")
    print("  " + "-" * 84)
    
    milestone_records = compute_milestone_audit(
        timeline_items,
        total_timeline_dur,
        fps=fps,
        video_path=str(raw_merged_video),
        audio_path=str(master_audio_path)
    )
    for rec in milestone_records:
        print(f"  {rec['label']:<9} | {rec['ideal_time']:<9.4f} | {rec['frame_boundary_index']:<8} | {rec['frame_index']:<6} | {rec['frame_aligned_expected_time']:<11.4f} | Seg {str(rec['containing_scene_id']):<2} | {rec['frame_quantization_error']:<9.4f} | {rec['status']}")
    print("="*86 + "\n")
    
    # Verification of merged video duration against narration
    if video_dur < audio_dur - frame_tolerance - 1e-4:
        raise RuntimeError(
            f"Export stopped: Merged video duration ({video_dur:.4f}s) is shorter than narration ({audio_dur:.4f}s). "
            f"Cannot proceed without truncating narration."
        )
        
    bgm_path = BGM_DIR / bgm_name
    output_filename = f"recap_{aspect_ratio.replace(':', '_')}_master.mp4"
    final_output_path = p_path / output_filename
    temp_output_path = p_path / f".tmp_{output_filename}"
    
    # Target duration contract: Source of truth is maximum required to cover complete narration
    target_duration = max(video_dur, audio_dur)
    
    # Build filter complex for Audio Ducking and Subtitle burn-in
    has_bgm = bgm_path.exists()
    ass_subtitle_path = p_path / "subtitles.ass"
    has_subtitles = burn_subtitles and ass_subtitle_path.exists()
    
    print(f"[*] Final compositing (BGM: {has_bgm}, Subtitles: {has_subtitles}, Target: {target_duration:.4f}s)...")
    
    final_cmd = [
        FFMPEG_BIN, "-y",
        "-i", str(raw_merged_video),
        "-i", str(master_audio_path),
    ]
    
    # Ensure audio stream reaches target_duration without early cutoff
    if has_bgm:
        final_cmd += ["-stream_loop", "-1", "-i", str(bgm_path)]
        # Audio ducking filter: Voice (1:a) padded to target_duration if needed, BGM lowered
        audio_filter = (
            f"[1:a]apad=whole_dur={target_duration:.6f},volume=1.0[voice]; "
            f"[2:a]volume={bgm_volume}[bgm]; "
            f"[voice][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
    else:
        audio_filter = f"[1:a]apad=whole_dur={target_duration:.6f},volume=1.0[aout]"
        
    video_filters = []
    if has_subtitles:
        effective_fonts_dir = Path(fonts_dir).resolve() if fonts_dir else FONTS_DIR.resolve()
        kanit_regular = effective_fonts_dir / "Kanit-Regular.ttf"
        kanit_bold = effective_fonts_dir / "Kanit-Bold.ttf"
        if not (effective_fonts_dir.exists() and kanit_regular.exists() and kanit_bold.exists()):
            raise RuntimeError(
                f"Required bundled font assets missing in {effective_fonts_dir}. "
                "Kanit fonts (Kanit-Regular.ttf, Kanit-Bold.ttf) are required for deterministic subtitle rendering."
            )
        sub_escaped = str(ass_subtitle_path.resolve()).replace('\\', '/').replace(':', '\\:')
        fonts_escaped = str(effective_fonts_dir).replace('\\', '/').replace(':', '\\:')
        video_filters.append(f"ass='{sub_escaped}':fontsdir='{fonts_escaped}'")
        
    if video_filters:
        final_filter_complex = f"[0:v]{','.join(video_filters)}[vout]; {audio_filter}"
        final_cmd += [
            "-filter_complex", final_filter_complex,
            "-map", "[vout]",
            "-map", "[aout]"
        ]
    else:
        final_cmd += [
            "-filter_complex", audio_filter,
            "-map", "0:v",
            "-map", "[aout]"
        ]
        
    # Strict duration contract: Use explicit duration -t instead of blind -shortest
    final_cmd += [
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", f"{target_duration:.6f}",
        str(temp_output_path)
    ]
    
    try:
        proc = subprocess.run(final_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            print(f"[Error] Compositing failed: {proc.stderr}", file=sys.stderr)
            raise RuntimeError(f"FFmpeg compositing error: {proc.stderr}")
            
        # Post-render validation on temporary file before promoting to master artifact
        qc_result = validate_post_render(
            temp_output_path,
            expected_audio_dur=audio_dur,
            expected_timeline_dur=target_duration,
            fps=fps,
            aspect_ratio=aspect_ratio,
            width=width,
            height=height
        )
        
        # Atomic promotion: Replace existing artifact only after validation passes
        if temp_output_path.exists():
            if final_output_path.exists():
                final_output_path.unlink()
            temp_output_path.rename(final_output_path)
            
        print(f"[+] Final recap video created successfully! -> {final_output_path}")
        print(f"    Stream Durations: Video = {qc_result['video_duration']:.4f}s, Audio = {qc_result['audio_duration']:.4f}s (Diff: {qc_result['av_drift']:.4f}s)")
        return str(final_output_path)
    except Exception as e:
        # Guarantee existing valid artifact remains unchanged if a new render fails
        if temp_output_path.exists():
            try:
                temp_output_path.unlink()
            except Exception:
                pass
        raise e

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Recap Video Motion Compositor")
    parser.add_argument("--project", required=True, help="Path to project folder")
    parser.add_argument("--aspect", default="16:9", choices=["16:9", "9:16"], help="Aspect ratio")
    parser.add_argument("--bgm", default="epic_anime.mp3", help="BGM file name")
    parser.add_argument("--subtitles", action="store_true", default=True, help="Burn subtitles")
    parser.add_argument("--fonts-dir", default=None, help="Custom fonts directory for deterministic font resolution")
    
    args = parser.parse_args()
    compose_recap_video(
        args.project,
        aspect_ratio=args.aspect,
        bgm_name=args.bgm,
        burn_subtitles=args.subtitles,
        fonts_dir=Path(args.fonts_dir) if args.fonts_dir else None
    )
