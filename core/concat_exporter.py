"""
core/concat_exporter.py
=======================
Automated Project Exporter for Concat NLE (https://concatenate.pages.dev)
Adheres strictly to project_rules.md:
- Rule 11: Audio-Driven Master Timeline with Global Snapping and Continuous 48kHz WAV Stems
- Rule 13: Natural Thai Subtitle Segmentation with Kanit Typography

Features:
1. Multi-Track Stems: Generates sample-aligned, continuous 48kHz PCM WAV stems
   (Voiceover, Auto-Ducked BGM, and SFX).
2. Dual Timelines in 1 Project: Exports both 16:9 Landscape and 9:16 Vertical
   into the same concat.json project.
3. Hybrid Media Placement: Uses real moving video cuts (sourceStart + duration)
   when source anime video is available; falls back to candidate still frames.
4. Ken Burns Keyframe Injection: Converts storyboard motion (zoom_in, pan_left,
   shock, tilt) into native Concat ClipKey curves (scale, offsetX, offsetY).
5. Native Thai Text/Caption Clips: Converts subtitle segments into interactive
   text clips with Kanit font, drop-shadow, and stroke.
"""

import os
import sys
import json
import subprocess
import shutil
import re
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

# Fix Windows console encoding for Thai characters
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Ensure core directory is in sys.path so config can be imported from any cwd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import FFMPEG_BIN, BGM_DIR, SFX_DIR, FONTS_DIR


def generate_audio_stems(
    project_dir: Path,
    out_audio_dir: Path,
    total_duration: float,
    bgm_path: Optional[Path] = None,
    bgm_volume: float = 0.35,
    ducking_ratio: float = 5.0
) -> Dict[str, Path]:
    """
    Produces continuous, sample-aligned 48kHz PCM WAV stems:
    - voice_stem.wav
    - bgm_stem.wav (with automatic voice ducking via sidechain compression)
    - sfx_stem.wav
    """
    out_audio_dir.mkdir(parents=True, exist_ok=True)
    voice_stem_path = out_audio_dir / "voice_stem.wav"
    bgm_stem_path = out_audio_dir / "bgm_stem.wav"
    sfx_stem_path = out_audio_dir / "sfx_stem.wav"

    # 1. Voiceover stem
    existing_wav = project_dir / "voiceover_master.wav"
    existing_mp3 = project_dir / "voiceover_master.mp3"
    
    if existing_wav.exists():
        # Convert/re-encode to standardized stereo 48kHz 16-bit PCM WAV
        cmd = [
            FFMPEG_BIN, "-y",
            "-i", str(existing_wav),
            "-ar", "48000",
            "-ac", "2",
            "-c:a", "pcm_s16le",
            str(voice_stem_path)
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    elif existing_mp3.exists():
        cmd = [
            FFMPEG_BIN, "-y",
            "-i", str(existing_mp3),
            "-ar", "48000",
            "-ac", "2",
            "-c:a", "pcm_s16le",
            str(voice_stem_path)
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    else:
        # Create silent reference stem if audio not yet synthesized
        cmd = [
            FFMPEG_BIN, "-y",
            "-f", "lavfi",
            "-i", f"anullsrc=r=48000:cl=stereo",
            "-t", f"{total_duration:.4f}",
            "-c:a", "pcm_s16le",
            str(voice_stem_path)
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

    # 2. BGM stem with Smart Auto-Ducking
    resolved_bgm = None
    if bgm_path and Path(bgm_path).exists():
        resolved_bgm = Path(bgm_path)
    else:
        # Default starter BGM
        candidate_bgm = BGM_DIR / "epic_anime.mp3"
        if candidate_bgm.exists():
            resolved_bgm = candidate_bgm

    if resolved_bgm and resolved_bgm.exists():
        # Use sidechain compression to duck BGM whenever voiceover is active
        # BGM is lowered by ducking_ratio (e.g. -14dB) and smoothly released in 250ms
        cmd = [
            FFMPEG_BIN, "-y",
            "-i", str(voice_stem_path),
            "-stream_loop", "-1",
            "-i", str(resolved_bgm),
            "-filter_complex",
            (
                f"[1:a]volume={bgm_volume:.3f}[bgm_raw]; "
                f"[bgm_raw][0:a]sidechaincompress=threshold=0.08:ratio={ducking_ratio:.1f}:attack=20:release=250[bgm_ducked]"
            ),
            "-map", "[bgm_ducked]",
            "-t", f"{total_duration:.4f}",
            "-ar", "48000",
            "-ac", "2",
            "-c:a", "pcm_s16le",
            str(bgm_stem_path)
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    else:
        # Generate silence stem for BGM
        cmd = [
            FFMPEG_BIN, "-y",
            "-f", "lavfi",
            "-i", f"anullsrc=r=48000:cl=stereo",
            "-t", f"{total_duration:.4f}",
            "-c:a", "pcm_s16le",
            str(bgm_stem_path)
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

    # 3. SFX stem (empty starter track)
    cmd = [
        FFMPEG_BIN, "-y",
        "-f", "lavfi",
        "-i", f"anullsrc=r=48000:cl=stereo",
        "-t", f"{total_duration:.4f}",
        "-c:a", "pcm_s16le",
        str(sfx_stem_path)
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

    return {
        "voice": voice_stem_path,
        "bgm": bgm_stem_path,
        "sfx": sfx_stem_path
    }


def safe_path_exists(p: Path) -> bool:
    """Checks if a file exists, safely handling Windows 260 MAX_PATH limit via \\\\?\\ prefix."""
    if not p:
        return False
    try:
        if p.exists():
            return True
    except Exception:
        pass
    try:
        s = str(p.resolve())
        if os.name == "nt" and not s.startswith("\\\\?\\"):
            s = "\\\\?\\" + s
        return Path(s).exists()
    except Exception:
        return False


def build_ken_burns_keys(action_type: str) -> List[Dict[str, Any]]:
    """
    Translates storyboard motion directives into Concat cubic-bezier ClipKey objects.
    Properties: Scale, OffsetX, OffsetY
    """
    keys = []
    action = (action_type or "normal").lower()
    
    # Smooth cubic bezier curve (ease-in-out)
    smooth_ease = {"x1": 0.25, "y1": 0.1, "x2": 0.25, "y2": 1.0}

    if "zoom_in" in action or action in ("shock", "dramatic"):
        keys.append({
            "property": "scale",
            "at": 0.0,
            "value": 1.0,
            "ease": smooth_ease
        })
        keys.append({
            "property": "scale",
            "at": 1.0,
            "value": 1.15,
            "ease": smooth_ease
        })
    elif "zoom_out" in action:
        keys.append({
            "property": "scale",
            "at": 0.0,
            "value": 1.15,
            "ease": smooth_ease
        })
        keys.append({
            "property": "scale",
            "at": 1.0,
            "value": 1.0,
            "ease": smooth_ease
        })
    elif "pan_left" in action:
        keys.append({"property": "scale", "at": 0.0, "value": 1.08, "ease": smooth_ease})
        keys.append({"property": "scale", "at": 1.0, "value": 1.08, "ease": smooth_ease})
        keys.append({"property": "offsetX", "at": 0.0, "value": 0.035, "ease": smooth_ease})
        keys.append({"property": "offsetX", "at": 1.0, "value": -0.035, "ease": smooth_ease})
    elif "pan_right" in action:
        keys.append({"property": "scale", "at": 0.0, "value": 1.08, "ease": smooth_ease})
        keys.append({"property": "scale", "at": 1.0, "value": 1.08, "ease": smooth_ease})
        keys.append({"property": "offsetX", "at": 0.0, "value": -0.035, "ease": smooth_ease})
        keys.append({"property": "offsetX", "at": 1.0, "value": 0.035, "ease": smooth_ease})
    elif "tilt" in action or "pan_up" in action:
        keys.append({"property": "scale", "at": 0.0, "value": 1.08, "ease": smooth_ease})
        keys.append({"property": "scale", "at": 1.0, "value": 1.08, "ease": smooth_ease})
        keys.append({"property": "offsetY", "at": 0.0, "value": 0.035, "ease": smooth_ease})
        keys.append({"property": "offsetY", "at": 1.0, "value": -0.035, "ease": smooth_ease})
    else:
        # Default gentle breathing zoom
        keys.append({
            "property": "scale",
            "at": 0.0,
            "value": 1.0,
            "ease": smooth_ease
        })
        keys.append({
            "property": "scale",
            "at": 1.0,
            "value": 1.05,
            "ease": smooth_ease
        })

    return keys


def export_to_concat_project(
    project_dir: str | Path,
    output_dir: Optional[str | Path] = None,
    options: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Main export entry point: Converts a spoil-anime project into a complete,
    fully functional Concat NLE project directory ready for opening.
    """
    project_dir = Path(project_dir).resolve()
    options = options or {}
    
    if output_dir:
        concat_proj_dir = Path(output_dir).resolve()
    else:
        concat_proj_dir = project_dir / "concat_project"

    concat_proj_dir.mkdir(parents=True, exist_ok=True)
    out_audio_dir = concat_proj_dir / "audio"
    out_audio_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load project data
    voice_tl_path = project_dir / "voice_timeline.json"
    timed_scenes_path = project_dir / "timed_scenes.json"
    frames_manifest_path = project_dir / "frames_manifest.json"
    video_meta_path = project_dir / "video_meta.json"
    
    timeline_items = []
    total_duration = 30.0
    fps = 30

    if voice_tl_path.exists():
        with open(voice_tl_path, "r", encoding="utf-8") as f:
            vdata = json.load(f)
            total_duration = float(vdata.get("total_duration", 30.0))
            fps = int(vdata.get("fps", 30))
            timeline_items = vdata.get("timeline", [])
    elif timed_scenes_path.exists():
        with open(timed_scenes_path, "r", encoding="utf-8") as f:
            timeline_items = json.load(f)
            if timeline_items:
                total_duration = float(timeline_items[-1].get("endTime", 30.0))

    # Fallback if no scenes found
    if not timeline_items:
        raise ValueError(f"No timeline data found in {project_dir}. Run script/voice generation first.")

    # 2. Check source video vs image frames
    source_video_path = None
    if video_meta_path.exists():
        try:
            with open(video_meta_path, "r", encoding="utf-8") as f:
                vmeta = json.load(f)
                cand_vpath = vmeta.get("videoPath") or ""
                if cand_vpath:
                    cand_path = Path(cand_vpath)
                    if safe_path_exists(cand_path):
                        source_video_path = cand_path.resolve()

                    if not source_video_path:
                        import re
                        match = re.search(r'(\d{10,15}(?:-\d+)?)', str(cand_vpath))
                        uploads_dir = project_dir.parent / "_uploads"
                        if match and uploads_dir.exists():
                            ts_id = match.group(1).split("-")[0]
                            for f in uploads_dir.iterdir():
                                lower_name = f.name.lower()
                                if ts_id in f.name and lower_name.endswith((".mp4", ".mkv", ".mov", ".avi", ".webm")):
                                    source_video_path = f.resolve()
                                    break
        except Exception as e:
            print(f"[!] Warning: video_meta resolution: {e}")

    if source_video_path and safe_path_exists(source_video_path):
        print(f"[*] Found source anime video: {source_video_path.name[:35]}...")
    else:
        print("[*] No source anime video found, falling back to image frames.")

    # Load frames manifest mapping (image_id -> timestamp / file path)
    frames_map = {}
    if frames_manifest_path.exists():
        try:
            with open(frames_manifest_path, "r", encoding="utf-8") as f:
                f_list = json.load(f)
                for item in f_list:
                    fid = item.get("id") or item.get("filename", "").replace(".jpg", "")
                    frames_map[fid] = item
        except Exception:
            pass

    # 3. Generate Audio Stems (Voice, BGM, SFX)
    stems = generate_audio_stems(
        project_dir=project_dir,
        out_audio_dir=out_audio_dir,
        total_duration=total_duration
    )

    # 4. Construct Concat Media Catalog
    media_items = []
    
    # Audio media items
    media_items.append({
        "id": "m_voice",
        "name": "Narration Voiceover (48kHz)",
        "path": str(stems["voice"].resolve()),
        "kind": "audio",
        "duration": round(total_duration, 4),
        "hasAudio": True,
        "audioCodec": "pcm_s16le",
        "audioTracks": [
            {
                "channels": 2,
                "codec": "pcm_s16le",
                "index": 0,
                "language": "tha",
                "sampleRate": 48000
            }
        ]
    })
    
    media_items.append({
        "id": "m_bgm",
        "name": "BGM Auto-Ducked (48kHz)",
        "path": str(stems["bgm"].resolve()),
        "kind": "audio",
        "duration": round(total_duration, 4),
        "hasAudio": True,
        "audioCodec": "pcm_s16le",
        "audioTracks": [
            {
                "channels": 2,
                "codec": "pcm_s16le",
                "index": 0,
                "language": "und",
                "sampleRate": 48000
            }
        ]
    })

    # Video or Image media items
    video_media_id = "m_source_video"
    use_moving_video = source_video_path and safe_path_exists(source_video_path)
    if use_moving_video:
        media_items.append({
            "id": video_media_id,
            "name": source_video_path.name,
            "path": str(source_video_path.resolve()),
            "kind": "video",
            "duration": 7200.0, # Will be clamped by Concat
            "hasAudio": True,
            "width": 1920,
            "height": 1080,
            "frameRate": float(fps)
        })
    else:
        # Register image files
        frames_dir = project_dir / "frames"
        if frames_dir.exists():
            for img_file in frames_dir.glob("*.jpg"):
                media_items.append({
                    "id": f"m_{img_file.stem}",
                    "name": img_file.name,
                    "path": str(img_file.resolve()),
                    "kind": "image",
                    "duration": 0.0,
                    "width": 1920,
                    "height": 1080
                })

    # 5. Standard Tracks definition
    # IMPORTANT: Concat renders tracks in array order (index 0 = bottom layer).
    # T_VIDEO must be BEFORE T_SUB so subtitles render ON TOP of video.
    standard_tracks = [
        {"id": "T_VIDEO", "muted": False, "visible": True},
        {"id": "T_SUB", "muted": False, "visible": True},
        {"id": "T_VOICE", "muted": False, "visible": True},
        {"id": "T_BGM", "muted": False, "visible": True},
        {"id": "T_SFX", "muted": False, "visible": True}
    ]

    # 6. Generate Clips for Both Timelines (16:9 and 9:16)
    def create_timeline_clips(is_vertical: bool) -> List[Dict[str, Any]]:
        clips = []
        clip_counter = 1

        # A. Voice stem clip
        clips.append({
            "id": f"c_voice_{clip_counter}",
            "trackId": "T_VOICE",
            "mediaId": "m_voice",
            "name": "Voiceover Track",
            "kind": "audio",
            "start": 0.0,
            "duration": round(total_duration, 4),
            "sourceStart": 0.0,
            "volume": 1.0,
            "fadeIn": 0.0,
            "fadeOut": 0.0,
            "filters": [],
            "videoEffects": []
        })
        clip_counter += 1

        # B. BGM stem clip
        clips.append({
            "id": f"c_bgm_{clip_counter}",
            "trackId": "T_BGM",
            "mediaId": "m_bgm",
            "name": "BGM Auto-Ducked",
            "kind": "audio",
            "start": 0.0,
            "duration": round(total_duration, 4),
            "sourceStart": 0.0,
            "volume": 1.0,
            "fadeIn": 0.5,
            "fadeOut": 1.0,
            "filters": [],
            "videoEffects": []
        })
        clip_counter += 1

        # C. Video / Scene clips
        for idx, sc in enumerate(timeline_items, start=1):
            sc_start = float(sc.get("startTime", sc.get("start", 0.0)))
            sc_end = float(sc.get("endTime", sc.get("end", sc_start + 3.0)))
            sc_dur = float(sc.get("duration", max(1.0, sc_end - sc_start)))
            action_type = sc.get("action_type", "normal")
            img_id = sc.get("imageSource") or sc.get("image_id") or f"frame_{idx:04d}"
            
            # Determine sourceStart in original video
            frame_info = frames_map.get(img_id, {})
            video_ts = float(frame_info.get("timestamp", 0.0))
            if "start" in sc and isinstance(sc["start"], (int, float)):
                video_ts = float(sc["start"])

            # Ken Burns keyframes
            keys = build_ken_burns_keys(action_type)

            # In 9:16 vertical mode, scale up to fill screen height or fit
            base_scale = 1.0 if not is_vertical else 1.35

            if use_moving_video:
                clip_entry = {
                    "id": f"c_v_{idx}_{clip_counter}",
                    "trackId": "T_VIDEO",
                    "mediaId": video_media_id,
                    "name": f"Scene {idx} ({action_type})",
                    "kind": "video",
                    "start": round(sc_start, 4),
                    "duration": round(sc_dur, 4),
                    "sourceStart": round(video_ts, 4),
                    "volume": 0.0,
                    "speed": 1.0,
                    "scale": base_scale,
                    "offsetX": 0.0,
                    "offsetY": 0.0,
                    "opacity": 1.0,
                    "rotation": 0.0,
                    "fadeIn": 0.0,
                    "fadeOut": 0.0,
                    "keys": keys,
                    "filters": [],
                    "videoEffects": []
                }
            else:
                target_m_id = f"m_{img_id}"
                clip_entry = {
                    "id": f"c_img_{idx}_{clip_counter}",
                    "trackId": "T_VIDEO",
                    "mediaId": target_m_id,
                    "name": f"Frame {idx} ({action_type})",
                    "kind": "image",
                    "start": round(sc_start, 4),
                    "duration": round(sc_dur, 4),
                    "sourceStart": 0.0,
                    "volume": 0.0,
                    "speed": 1.0,
                    "scale": base_scale,
                    "offsetX": 0.0,
                    "offsetY": 0.0,
                    "opacity": 1.0,
                    "rotation": 0.0,
                    "fadeIn": 0.0,
                    "fadeOut": 0.0,
                    "keys": keys,
                    "filters": [],
                    "videoEffects": []
                }
            clips.append(clip_entry)
            clip_counter += 1

            # D. Subtitle Text Clips
            sub_segments = sc.get("subtitleSegments") or []
            if not sub_segments and (sc.get("narration") or sc.get("text")):
                sub_segments = [{
                    "index": 1,
                    "text": (sc.get("narration") or sc.get("text")).strip(),
                    "start_time": sc_start + 0.1,
                    "end_time": sc_start + sc_dur - 0.1
                }]

            sub_offset_y = 0.38 if not is_vertical else 0.28
            sub_font_size = 0.055 if not is_vertical else 0.045

            for s_idx, seg in enumerate(sub_segments, start=1):
                seg_text = seg.get("text", "").replace("\\N", "\n").strip()
                if not seg_text:
                    continue
                seg_start = float(seg.get("start_time", sc_start))
                seg_end = float(seg.get("end_time", sc_start + sc_dur))
                seg_dur = max(0.4, seg_end - seg_start)

                text_clip = {
                    "id": f"c_sub_{idx}_{s_idx}_{clip_counter}",
                    "trackId": "T_SUB",
                    "mediaId": "",
                    "name": seg_text.split("\n")[0][:25],
                    "kind": "text",
                    "start": round(seg_start, 4),
                    "duration": round(seg_dur, 4),
                    "sourceStart": 0.0,
                    "volume": 0.0,
                    "scale": 1.0,
                    "offsetX": 0.0,
                    "offsetY": sub_offset_y,
                    "opacity": 1.0,
                    "rotation": 0.0,
                    "fadeIn": 0.08,
                    "fadeOut": 0.08,
                    "text": {
                        "content": seg_text,
                        "fontFamily": "Kanit",
                        "fontSize": sub_font_size,
                        "fontWeight": 700.0,
                        "italic": False,
                        "color": "#ffffff",
                        "align": "center",
                        "opacity": 1.0,
                        "strokeWidth": 0.005,
                        "strokeColor": "#000000",
                        "shadow": True,
                        "background": ""
                    },
                    "filters": [],
                    "videoEffects": []
                }
                clips.append(text_clip)
                clip_counter += 1

        return clips

    # Build 16:9 and 9:16 timelines
    clips_16_9 = create_timeline_clips(is_vertical=False)
    clips_9_16 = create_timeline_clips(is_vertical=True)

    timelines = [
        {
            "id": "TL_16_9",
            "name": "16:9 Landscape (YouTube)",
            "video": {
                "width": 1920,
                "height": 1080,
                "rateNum": fps,
                "rateDen": 1
            },
            "tracks": standard_tracks,
            "clips": clips_16_9
        },
        {
            "id": "TL_9_16",
            "name": "9:16 Vertical (Shorts/Reels)",
            "video": {
                "width": 1080,
                "height": 1920,
                "rateNum": fps,
                "rateDen": 1
            },
            "tracks": standard_tracks,
            "clips": clips_9_16
        }
    ]

    # Enforce Track Layering Invariant:
    # Concat renders tracks in ascending array index order (0 = background, 1 = overlay).
    # T_VIDEO must strictly come before T_SUB so video does not occlude subtitles.
    vid_idx = next(i for i, t in enumerate(standard_tracks) if t["id"] == "T_VIDEO")
    sub_idx = next(i for i, t in enumerate(standard_tracks) if t["id"] == "T_SUB")
    if vid_idx >= sub_idx:
        raise ValueError(f"Invariant Violation: T_VIDEO index ({vid_idx}) must be < T_SUB index ({sub_idx})")

    # 7. Assembling Concat Project Document
    concat_doc = {
        "version": 1,
        "name": project_dir.name,
        "concat": "0.1.0",
        "activeTimelineId": "TL_16_9",
        "video": {
            "width": 1920,
            "height": 1080,
            "rateNum": fps,
            "rateDen": 1
        },
        "media": media_items,
        "fonts": [
            {
                "family": "Kanit",
                "path": str((FONTS_DIR / "Kanit-Regular.ttf").resolve())
            }
        ] if (FONTS_DIR / "Kanit-Regular.ttf").exists() else [],
        "tracks": standard_tracks,
        "clips": clips_16_9, # Mirrored for backwards compatibility
        "timelines": timelines
    }

    # Write concat.json
    concat_json_path = concat_proj_dir / "concat.json"
    with open(concat_json_path, "w", encoding="utf-8") as f:
        json.dump(concat_doc, f, indent=2, ensure_ascii=False)

    return {
        "success": True,
        "concat_project_dir": str(concat_proj_dir.resolve()),
        "concat_json_path": str(concat_json_path.resolve()),
        "timelines": ["TL_16_9 (1920x1080)", "TL_9_16 (1080x1920)"],
        "total_duration": total_duration,
        "scenes_count": len(timeline_items),
        "stems": {
            "voice": str(stems["voice"].resolve()),
            "bgm": str(stems["bgm"].resolve()),
            "sfx": str(stems["sfx"].resolve())
        }
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m core.concat_exporter <project_id_or_path>")
        sys.exit(1)
        
    target_arg = sys.argv[1]
    p_path = Path(target_arg)
    if not p_path.is_dir():
        p_path = Path("projects") / target_arg
        
    if not p_path.exists():
        print(f"Error: Project directory {p_path} does not exist.")
        sys.exit(1)
        
    print(f"[*] Exporting project to Concat format: {p_path}...")
    res = export_to_concat_project(p_path)
    print("[+] Successfully exported to Concat!")
    print(json.dumps(res, indent=2, ensure_ascii=False))
