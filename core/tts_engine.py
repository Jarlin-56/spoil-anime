import asyncio
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
import edge_tts

from config import FFMPEG_BIN, FFPROBE_BIN, VOICES, DEFAULT_VOICE
from timing_engine import (
    recalculate_timeline,
    validate_timeline,
    compute_tts_cache_key,
    format_timing_log_table,
    split_narration_sentences,
    DEFAULT_PADDING_BEFORE,
    DEFAULT_PADDING_AFTER
)

def get_audio_duration(audio_path: str) -> float:
    try:
        cmd = [
            FFPROBE_BIN, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path)
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"[Warn] Could not get audio duration: {e}", file=sys.stderr)
        return 2.5

def format_timestamp_srt(seconds: float) -> str:
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"

def format_timestamp_ass(seconds: float) -> str:
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int((seconds - int(seconds)) * 100)
    return f"{hrs:d}:{mins:02d}:{secs:02d}.{centis:02d}"

def normalize_and_pad_audio(input_path: str, output_wav_path: str, target_duration: float) -> float:
    """
    Convert synthesized speech audio to standard PCM WAV (48000 Hz, 16-bit stereo)
    and pad with exact silence to match target_duration with sample precision.
    """
    cmd = [
        FFMPEG_BIN, "-y",
        "-i", str(input_path),
        "-af", f"aresample=48000,aformat=sample_fmts=s16:channel_layouts=stereo,apad,atrim=0:{target_duration:.6f}",
        "-c:a", "pcm_s16le",
        str(output_wav_path)
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return get_audio_duration(output_wav_path)

def convert_wav_to_mp3(wav_path: str, mp3_path: str):
    """Convert PCM WAV to MP3 for lightweight web UI playback."""
    cmd = [
        FFMPEG_BIN, "-y",
        "-i", str(wav_path),
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        str(mp3_path)
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

async def synthesize_line_cached(
    text: str,
    voice: str,
    rate: str,
    output_path: str,
    cache_dir: Path
):
    """
    Synthesize line with content-based MD5 caching and capture exact sentence boundaries.
    Avoids re-calling TTS provider when narration is unchanged.
    """
    sentences = split_narration_sentences(text)
    # Join with newline so Edge-TTS creates natural pause and emits distinct SentenceBoundary
    tts_text = "\n".join(sentences) if sentences else text
    cache_key = compute_tts_cache_key(tts_text, voice, rate)
    cache_file = cache_dir / f"{cache_key}.mp3"
    meta_file = cache_dir / f"{cache_key}_meta.json"
    
    # 1. Check cache
    if cache_file.exists() and cache_file.stat().st_size > 500:
        import shutil
        shutil.copy2(cache_file, output_path)
        sentence_boundaries = []
        if meta_file.exists():
            try:
                with open(meta_file, "r", encoding="utf-8") as mf:
                    sentence_boundaries = json.load(mf)
            except Exception as e:
                print(f"[Warn] Could not read TTS cache meta: {e}", file=sys.stderr)
        
        dur = get_audio_duration(output_path)
        if not sentence_boundaries:
            sentence_boundaries = [{
                "text": text,
                "offset": 0.1,
                "duration": round(max(0.1, dur - 0.2), 4)
            }]
        return dur, sentence_boundaries
        
    # 2. Synthesize using edge-tts stream to capture SentenceBoundary
    communicate = edge_tts.Communicate(text=tts_text, voice=voice, rate=rate)
    sentence_boundaries = []
    
    with open(output_path, "wb") as audio_fp:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_fp.write(chunk["data"])
            elif chunk["type"] == "SentenceBoundary":
                b_text = chunk.get("text", "").strip()
                b_offset = round(chunk["offset"] / 10_000_000, 4)
                b_duration = round(chunk["duration"] / 10_000_000, 4)
                if b_text:
                    sentence_boundaries.append({
                        "text": b_text,
                        "offset": b_offset,
                        "duration": b_duration
                    })
                    
    dur = get_audio_duration(output_path)
    
    if not sentence_boundaries:
        sentence_boundaries = [{
            "text": text,
            "offset": 0.1,
            "duration": round(max(0.1, dur - 0.2), 4)
        }]

    # Save to cache
    try:
        import shutil
        cache_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(output_path, cache_file)
        with open(meta_file, "w", encoding="utf-8") as mf:
            json.dump(sentence_boundaries, mf, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Warn] Could not write TTS cache: {e}", file=sys.stderr)
        
    return dur, sentence_boundaries

async def build_voiceover_timeline(
    script_items: list,
    output_dir: str,
    voice: str = DEFAULT_VOICE,
    rate: str = "+5%",
    pitch: str = "+0Hz",
    pause_between_sec: float = DEFAULT_PADDING_AFTER,
    fps: int = 30
) -> dict:
    """
    Synthesize all lines with TTS caching, measure exact audio durations,
    and recalculate a canonical frame-aligned timeline with semantic subtitles.
    """
    out_path = Path(output_dir)
    audio_dir = out_path / "audio_segments"
    cache_dir = audio_dir / "_cache"
    audio_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    valid_items = [item for item in script_items if (item.get("text") or "").strip()]
    print(f"[*] Synthesizing {len(valid_items)} lines using {voice} (Rate: {rate}, FPS: {fps})...")
    
    # Step 1: Synthesize all speech segments and collect actual audio durations
    prepared_scenes = []
    for idx, item in enumerate(valid_items, start=1):
        line_text = item.get("text", "").strip()
        img_id = item.get("image_id")
        action_type = item.get("action_type", "normal")
        
        raw_segment_filepath = audio_dir / f"raw_seg_{idx:04d}.mp3"
        raw_meta_filepath = audio_dir / f"raw_seg_{idx:04d}.json"
        
        speech_dur, sentence_boundaries = await synthesize_line_cached(
            line_text, voice, rate, str(raw_segment_filepath), cache_dir
        )
        
        # Save per-segment metadata alongside audio segment for persistence
        try:
            with open(raw_meta_filepath, "w", encoding="utf-8") as rmf:
                json.dump(sentence_boundaries, rmf, ensure_ascii=False, indent=2)
        except Exception:
            pass
        
        scene_obj = {
            "id": item.get("id", idx),
            "order": idx,
            "storyBeat": item.get("storyBeat", f"Beat_{idx}"),
            "narration": line_text,
            "text": line_text,
            "imageSource": img_id,
            "image_id": img_id,
            "action_type": action_type,
            "actualAudioDuration": speech_dur,
            "speech_duration": speech_dur,
            "sentence_boundaries": sentence_boundaries,
            "_raw_audio_path": str(raw_segment_filepath)
        }
        prepared_scenes.append(scene_obj)
        
    # Step 2: Canonical Timeline Recalculation (Single Source of Truth)
    recalculated = recalculate_timeline(
        prepared_scenes,
        fps=fps,
        padding_before=DEFAULT_PADDING_BEFORE,
        padding_after=DEFAULT_PADDING_AFTER
    )
    timeline_items = recalculated["timeline"]
    
    concat_list_path = out_path / "audio_concat.txt"
    concat_lines = []
    srt_lines = []
    ass_events = []
    
    srt_counter = 1
    
    # Step 3: Pad audio to exact canonical frame duration and assemble subtitles
    for idx, entry in enumerate(timeline_items, start=1):
        canonical_dur = entry["duration"]
        speech_dur = entry["speech_duration"]
        start_time = entry["startTime"]
        raw_audio_path = prepared_scenes[idx - 1]["_raw_audio_path"]
        
        segment_wav_filepath = audio_dir / f"seg_{idx:04d}.wav"
        segment_mp3_filepath = audio_dir / f"seg_{idx:04d}.mp3"
        
        normalize_and_pad_audio(raw_audio_path, segment_wav_filepath, canonical_dur)
        convert_wav_to_mp3(segment_wav_filepath, segment_mp3_filepath)
        
        # Relative paths for portable storage
        entry["audio_file"] = str(segment_mp3_filepath.relative_to(out_path.parent.parent))
        entry["audio_wav"] = str(segment_wav_filepath.relative_to(out_path.parent.parent))
        entry["audioUrl"] = entry["audio_file"]
        
        concat_lines.append(f"file '{segment_wav_filepath.resolve()}'\n")
        
        # Subtitles: use semantic phrase segments (Phase 11 & 12)
        sub_segs = entry.get("subtitleSegments", [])
        if sub_segs:
            for sub in sub_segs:
                s_txt = sub["text"]
                s_st = sub["start_time"]
                s_et = sub["end_time"]
                srt_txt = s_txt.replace(r'\N', '\n')
                ass_txt = s_txt.replace('\n', r'\N')
                srt_lines.append(f"{srt_counter}\n{format_timestamp_srt(s_st)} --> {format_timestamp_srt(s_et)}\n{srt_txt}\n\n")
                ass_events.append(f"Dialogue: 0,{format_timestamp_ass(s_st)},{format_timestamp_ass(s_et)},Default,,0,0,0,,{ass_txt}\n")
                srt_counter += 1
        else:
            s_txt = entry['text']
            srt_txt = s_txt.replace(r'\N', '\n')
            ass_txt = s_txt.replace('\n', r'\N')
            srt_lines.append(f"{srt_counter}\n{format_timestamp_srt(start_time)} --> {format_timestamp_srt(start_time + speech_dur)}\n{srt_txt}\n\n")
            ass_events.append(f"Dialogue: 0,{format_timestamp_ass(start_time)},{format_timestamp_ass(start_time + speech_dur)},Default,,0,0,0,,{ass_txt}\n")
            srt_counter += 1

    # Write concat list for master audio concatenation (WAV)
    with open(concat_list_path, "w", encoding="utf-8") as f:
        f.writelines(concat_lines)
        
    master_wav_path = out_path / "voiceover_master.wav"
    master_mp3_path = out_path / "voiceover_master.mp3"
    
    # Lossless PCM master concatenation
    concat_cmd = [
        FFMPEG_BIN, "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list_path),
        "-c:a", "pcm_s16le",
        str(master_wav_path)
    ]
    subprocess.run(concat_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    convert_wav_to_mp3(master_wav_path, master_mp3_path)
    
    master_dur = get_audio_duration(master_wav_path)
    total_time = recalculated["total_duration"]
    audio_drift = abs(master_dur - total_time)
    print(f"[+] Master Audio Created: {master_dur:.4f}s (Timeline: {total_time:.4f}s, Diff: {audio_drift:.4f}s)")
    
    # Write SRT
    srt_path = out_path / "subtitles.srt"
    with open(srt_path, "w", encoding="utf-8") as f:
        f.writelines(srt_lines)
        
    # Write styled ASS subtitle
    ass_path = out_path / "subtitles.ass"
    ass_header = """[Script Info]
Title: Anime Recap Thai Subtitles
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Kanit,58,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4.5,2.5,2,40,40,65,1
Style: Highlight,Kanit,62,&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5.0,3.0,2,40,40,65,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_header)
        f.writelines(ass_events)
        
    result_data = {
        "voice": voice,
        "rate": rate,
        "fps": fps,
        "sample_rate": 48000,
        "total_duration": total_time,
        "total_frames": recalculated["total_frames"],
        "lines_count": len(timeline_items),
        "master_audio": str(master_mp3_path.relative_to(out_path.parent.parent)),
        "master_wav": str(master_wav_path.relative_to(out_path.parent.parent)),
        "srt_file": str(srt_path.relative_to(out_path.parent.parent)),
        "ass_file": str(ass_path.relative_to(out_path.parent.parent)),
        "timeline": timeline_items
    }
    
    # Quality Control Validation check
    qc = validate_timeline(result_data)
    print(f"\n[QC Validation] Status: {'PASS' if qc['valid'] else 'FAIL'}")
    if qc['warnings']:
        for w in qc['warnings']:
            print(f"  [QC Warn] {w}")
    if qc['errors']:
        for e in qc['errors']:
            print(f"  [QC Error] {e}")
            
    # Structured Timing Logging (Phase 27)
    print("\n" + format_timing_log_table(result_data) + "\n")
    
    timeline_path = out_path / "voice_timeline.json"
    with open(timeline_path, "w", encoding="utf-8") as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)
        
    print(f"[+] Voiceover generated successfully! Total length: {total_time:.2f}s ({len(timeline_items)} segments)")
    return result_data

def run_tts(script_json_path: str, output_dir: str, voice: str = DEFAULT_VOICE, rate: str = "+5%", fps: int = 30):
    with open(script_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("items", data) if isinstance(data, dict) else data
    return asyncio.run(build_voiceover_timeline(items, output_dir, voice=voice, rate=rate, fps=fps))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Edge-TTS Voiceover Generator")
    parser.add_argument("--script", required=True, help="Path to script JSON file")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help="Voice name (th-TH-NiwatNeural, th-TH-PremwadeeNeural)")
    parser.add_argument("--rate", default="+5%", help="Speed rate (+0%, +5%, +10%)")
    parser.add_argument("--fps", type=int, default=30, help="Target video FPS (default: 30)")
    
    args = parser.parse_args()
    res = run_tts(args.script, args.out, voice=args.voice, rate=args.rate, fps=args.fps)
    print(f"Master voiceover ready at: {res['master_audio']}")

