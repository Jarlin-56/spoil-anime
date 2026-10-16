import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path
import requests

from config import FFMPEG_BIN, FFPROBE_BIN, PROJECTS_DIR

def normalize_path(path_str: str) -> str:
    if not path_str:
        return ""
    p = os.path.abspath(path_str)
    if os.name == 'nt' and len(p) >= 240 and not p.startswith('\\\\?\\'):
        p = '\\\\?\\' + p
    return p

def detect_subtitles(video_path: str) -> list:
    """Check if video file has embedded subtitle streams."""
    try:
        cmd = [
            FFPROBE_BIN, "-v", "error",
            "-select_streams", "s",
            "-show_entries", "stream=index:stream_tags=language,title",
            "-of", "json",
            normalize_path(video_path)
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace', check=True)
        data = json.loads(result.stdout)
        return data.get("streams", [])
    except Exception as e:
        print(f"[Warn] Subtitle detection failed: {e}", file=sys.stderr)
        return []

def extract_embedded_subtitles(video_path: str, output_srt_path: str, stream_index: int = 0) -> bool:
    """Extract embedded subtitle track to SRT file."""
    try:
        cmd = [
            FFMPEG_BIN, "-y",
            "-i", video_path,
            "-map", f"0:s:{stream_index}",
            output_srt_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
        return res.returncode == 0 and os.path.exists(output_srt_path) and os.path.getsize(output_srt_path) > 0
    except Exception as e:
        print(f"[Warn] Could not extract subtitles: {e}", file=sys.stderr)
        return False

def get_video_duration(video_path: str) -> float:
    try:
        cmd = [
            FFPROBE_BIN, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace', check=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"[Warn] Could not get duration: {e}", file=sys.stderr)
        return 0.0

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
    import re
    if "-" in s_val or "–" in s_val:
        parts = [p.strip() for p in re.split(r"[-–]", s_val) if p.strip()]
        if len(parts) >= 2:
            s = parse_time_value(parts[0])
            e = parse_time_value(parts[1])
            if e > s:
                return (s, e)
    return None

def is_op_ed_text(text: str) -> bool:
    """Check if dialogue / scene line corresponds to OP/ED music or sponsor ad."""
    if not text:
        return False
    t_low = text.lower()
    markers = [
        "เพลงเปิด", "เพลงปิด", "intro", "outro", "opening theme", "ending theme",
        "animegogo", "สปอนเซอร์", "โฆษณาเว็บไซต์", "theme song", "เครดิตจบ"
    ]
    return any(m in t_low for m in markers)

def extract_audio_from_video(
    video_path: str,
    output_mp3_path: str,
    max_duration_sec: float = None,
    skip_intro_sec: float = 0.0,
    skip_outro_sec: float = 0.0,
    intro_range_str: str = "",
    outro_range_str: str = "",
    auto_detect_op_ed: bool = False
) -> bool:
    """Extract audio from video to a lightweight MP3 file for speech transcription, muting OP/ED sections."""
    try:
        duration = get_video_duration(video_path)
        intro_range = parse_range_value(intro_range_str)
        outro_range = parse_range_value(outro_range_str)
        
        # Auto-detect OP/ED from video if enabled and ranges not manually specified
        if auto_detect_op_ed and (not intro_range or not outro_range):
            try:
                from op_ed_detector import detect_op_ed
                det = detect_op_ed(video_path)
                if det.get("success"):
                    if not intro_range and det.get("op"):
                        intro_range = (det["op"]["start"], det["op"]["end"])
                    if not outro_range and det.get("ed"):
                        outro_range = (det["ed"]["start"], det["ed"]["end"])
            except Exception as e:
                print(f"[Warn] Audio auto OP/ED detection skipped: {e}", file=sys.stderr)

        if not intro_range and skip_intro_sec > 0:
            intro_range = (0.0, skip_intro_sec)
        if not outro_range and skip_outro_sec > 0 and duration > 0:
            outro_range = (max(0.0, duration - skip_outro_sec), duration)
            
        cmd = [
            FFMPEG_BIN, "-y",
            "-i", video_path,
            "-vn",
            "-acodec", "libmp3lame",
            "-ar", "16000",
            "-ac", "1",
            "-b:a", "64k"
        ]
        
        # Mute audio during OP and ED to prevent speech model from transcribing music/lyrics/ads
        cut_conditions = []
        if intro_range and intro_range[1] > intro_range[0]:
            cut_conditions.append(f"between(t,{intro_range[0]:.2f},{intro_range[1]:.2f})")
            print(f"[*] ✂️ Audio Mute OP (Intro): {intro_range[0]:.1f}s - {intro_range[1]:.1f}s")
        if outro_range and outro_range[1] > outro_range[0]:
            cut_conditions.append(f"between(t,{outro_range[0]:.2f},{outro_range[1]:.2f})")
            print(f"[*] ✂️ Audio Mute ED (Outro): {outro_range[0]:.1f}s - {outro_range[1]:.1f}s")
            
        if cut_conditions:
            cond_str = "+".join(cut_conditions)
            cmd += ["-af", f"volume=enable='{cond_str}':volume=0"]
            
        if max_duration_sec:
            cmd += ["-t", str(max_duration_sec)]
        cmd.append(output_mp3_path)
        
        print(f"[*] Extracting audio to: {output_mp3_path}")
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
        return res.returncode == 0
    except Exception as e:
        print(f"[Error] Audio extraction failed: {e}", file=sys.stderr)
        return False

def _exponential_backoff_wait(attempt: int, base_sec: float = 3.0, max_sec: float = 30.0) -> float:
    """Calculate exponential backoff delay with random jitter."""
    delay = min(base_sec * (2 ** attempt), max_sec)
    jitter = random.uniform(-2.0, 2.0)
    return max(1.0, delay + jitter)

def get_available_gemini_models(api_key: str) -> list:
    """
    Query Gemini API to get the list of active models that support generateContent for this API key.
    Automatically prioritizes fast & reliable Flash models, then Pro models.
    """
    discovered = []
    # Query v1beta then v1
    for api_ver in ["v1beta", "v1"]:
        try:
            url = f"https://generativelanguage.googleapis.com/{api_ver}/models?key={api_key}"
            resp = requests.get(url, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                for m in data.get("models", []):
                    methods = m.get("supportedGenerationMethods", [])
                    if "generateContent" in methods:
                        m_name = m.get("name", "").replace("models/", "")
                        if m_name and m_name not in discovered:
                            discovered.append(m_name)
                if discovered:
                    print(f"[+] Found {len(discovered)} supported Gemini models via {api_ver}.")
                    break
        except Exception as e:
            print(f"[Warn] Could not list models on {api_ver}: {e}", file=sys.stderr)

    # Sort discovered models by priority: flash first, then pro
    def model_priority(name: str) -> int:
        n = name.lower()
        if "2.5-flash" in n: return 0
        if "2.0-flash" in n: return 1
        if "flash" in n and "lite" not in n: return 2
        if "2.5-pro" in n: return 3
        if "pro" in n: return 4
        if "flash-lite" in n: return 5
        return 10

    discovered.sort(key=model_priority)

    # Robust modern fallbacks (2025/2026) in case ListModels is restricted or offline
    fallbacks = [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.0-flash-exp",
        "gemini-1.5-flash-latest",
        "gemini-1.5-flash-002",
        "gemini-1.5-pro-002",
    ]
    for fb in fallbacks:
        if fb not in discovered:
            discovered.append(fb)

    return discovered


def transcribe_audio_with_gemini(api_key: str, audio_path: str) -> str:
    """
    Transcribe and summarize anime audio dialogue directly using Gemini Multimodal Audio API.
    Handles Japanese, English, and Thai speech, returning structured transcript & plot points.
    Includes file state polling and automatic retries with exponential backoff on 503/429/500.
    """
    print("[*] Uploading audio to Gemini API for speech transcription...")
    
    # 1. Upload file using Gemini File API
    file_size = os.path.getsize(audio_path)
    upload_url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={api_key}"
    headers = {
        "X-Goog-Upload-Command": "start, upload, finalize",
        "X-Goog-Upload-Header-Content-Length": str(file_size),
        "X-Goog-Upload-Header-Content-Type": "audio/mp3",
        "Content-Type": "audio/mp3"
    }
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()
        
    upload_resp = requests.post(upload_url, headers=headers, data=audio_bytes, timeout=120)
    upload_resp.raise_for_status()
    file_data = upload_resp.json()
    file_obj = file_data.get("file", {})
    file_uri = file_obj.get("uri")
    file_name = file_obj.get("name")
    
    if not file_uri:
        raise RuntimeError(f"Failed to get file URI from upload response: {file_data}")
        
    print(f"[+] Audio uploaded successfully: {file_uri}. Checking file processing state...")
    
    # Wait for file to become ACTIVE if in PROCESSING state
    if file_name:
        check_url = f"https://generativelanguage.googleapis.com/v1beta/{file_name}?key={api_key}"
        for _ in range(15):
            try:
                chk_resp = requests.get(check_url, timeout=15)
                if chk_resp.status_code == 200:
                    st = chk_resp.json().get("state")
                    if st == "ACTIVE":
                        print("[+] Audio file is ACTIVE and ready.")
                        break
                    elif st == "FAILED":
                        raise RuntimeError("Audio file processing failed on Gemini server.")
            except Exception as ce:
                print(f"[Warn] File state check: {ce}")
            time.sleep(2)
    
    # Automatically discover active models for this API key
    candidate_models = get_available_gemini_models(api_key)
    print(f"[*] Candidate models to try ({len(candidate_models)}): {candidate_models[:6]}")
    
    MAX_RETRIES_PER_MODEL = 5
    
    prompt = """คุณคือนักถอดเสียงบทสนทนาและนักวิเคราะห์เหตุการณ์อนิเมะระดับมืออาชีพ เพื่อนำข้อมูลไปเขียนบทสรุป/สปอยล์แบบละเอียด
กรุณาฟังเสียงในไฟล์นี้อย่างละเอียดตั้งแต่ต้นจนจบ (รองรับภาษาญี่ปุ่น, อังกฤษ, และไทย):

1. **ถอดลำดับเหตุการณ์ตามช่วงเวลาอย่างละเอียด (Granular Timestamps):**
   - แบ่งฉากเป็นช่วงเวลาย่อยๆ ทุก 20-45 วินาที เช่น (00:00 - 00:35), (00:36 - 01:15) ต่อเนื่องไปจนจบทั้งคลิป ห้ามตัดจบกลางเรื่องเด็ดขาด

2. **ในแต่ละช่วงเวลา ให้ระบุโครงสร้างชัดเจนดังนี้:**
   #### [ลำดับ]. ชื่อฉาก/สถานการณ์ (MM:SS - MM:SS)
   * **ช่วงเวลา:** MM:SS - MM:SS
   * **ตัวละครหลัก:** (ระบุชื่อตัวละครที่พูดหรือปรากฏตัว)
   * **เหตุการณ์และการกระทำ:** (อธิบายสิ่งที่เกิดขึ้น ใครทำอะไร สู้กันอย่างไร แผนการคืออะไร อย่างละเอียด)
   * **บทสนทนาสำคัญ (Dialogue Quotes):** (ถอดประโยคคำพูดสำคัญเป็นภาษาไทย เช่น *ชื่อตัวละคร:* "คำพูดตรงๆ...")
   * **จุดพลิกผัน/อารมณ์:** (ระบุอารมณ์ เช่น ตกใจ, ตึงเครียด, ดุเดือด, หักมุม, ตลก)

3. **เขียนผลลัพธ์เป็นภาษาไทยที่สมบูรณ์ ชัดเจน ไม่สรุปสั้นจนเกินไป** เพื่อเป็นข้อมูลตั้งต้นที่ละเอียดที่สุดสำหรับการเขียนบทสปอยล์

4. **กฎเหล็ก: ตัดเพลงเปิดและเพลงปิด (STRICT OP/ED CUT):**
   - ห้ามถอดเสียงหรือสร้างฉากสำหรับเพลงเปิด (Intro / Opening Theme song), เพลงปิด (Outro / Ending Theme song), โฆษณาเว็บไซต์สปอนเซอร์ หรือเครดิตผู้ผลิต
   - หากช่วงเวลาใดเป็นช่วงเพลงเปิดหรือเพลงปิด ให้ข้ามไปโดยสิ้นเชิง ไม่ต้องบันทึกเป็นฉาก
   - เริ่มต้นบันทึกจากฉากเนื้อเรื่องจริงของอนิเมะ และจบที่ฉากเนื้อเรื่องจริงก่อนขึ้นเพลงปิด"""

    payload = {
        "contents": [{
            "parts": [
                {"file_data": {"mime_type": "audio/mp3", "file_uri": file_uri}},
                {"text": prompt}
            ]
        }]
    }
    
    last_err = None
    total_attempts = 0
    for model_name in candidate_models:
        model_is_404 = False
        for attempt in range(MAX_RETRIES_PER_MODEL):
            if model_is_404:
                break
            total_attempts += 1
            
            # Try v1beta first, fallback to v1 if needed
            for api_ver in ["v1beta", "v1"]:
                gen_url = f"https://generativelanguage.googleapis.com/{api_ver}/models/{model_name}:generateContent?key={api_key}"
                try:
                    print(f"[*] Requesting transcription via {model_name} [{api_ver}] (attempt {attempt+1}/{MAX_RETRIES_PER_MODEL}, total #{total_attempts})...")
                    gen_resp = requests.post(gen_url, headers={"Content-Type": "application/json"}, json=payload, timeout=180)
                    if gen_resp.status_code == 200:
                        result = gen_resp.json()
                        candidates = result.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            text_parts = [p.get("text", "") for p in parts if "text" in p]
                            if text_parts:
                                print(f"[+] Successfully transcribed with {model_name} [{api_ver}] after {total_attempts} total attempt(s)!")
                                return "".join(text_parts)
                    elif gen_resp.status_code in [503, 500, 429]:
                        wait_sec = _exponential_backoff_wait(attempt)
                        print(f"[Warn] {model_name} [{api_ver}] returned {gen_resp.status_code} (server busy/overloaded). "
                              f"Exponential backoff: waiting {wait_sec:.1f}s before retry...")
                        last_err = f"{gen_resp.status_code}: Server busy/overloaded"
                        time.sleep(wait_sec)
                        break  # Retry in next attempt loop
                    elif gen_resp.status_code == 404:
                        print(f"[Warn] {model_name} [{api_ver}] returned 404: model not found.")
                        last_err = f"404: {model_name} [{api_ver}] not found"
                        if api_ver == "v1":
                            model_is_404 = True  # Both endpoints 404, skip this model
                        continue
                    else:
                        print(f"[Warn] {model_name} [{api_ver}] status {gen_resp.status_code}: {gen_resp.text[:120]}")
                        last_err = f"{gen_resp.status_code}: {gen_resp.text[:200]}"
                        model_is_404 = True
                        break
                except requests.exceptions.Timeout:
                    wait_sec = _exponential_backoff_wait(attempt)
                    last_err = f"Timeout after 180s with {model_name}"
                    print(f"[Warn] {model_name} request timed out. Waiting {wait_sec:.1f}s before retry...")
                    time.sleep(wait_sec)
                    break
                except Exception as e:
                    last_err = str(e)
                    print(f"[Warn] Error with {model_name}: {e}. Retrying...")
                    time.sleep(2)
                    break
                
    if last_err:
        raise RuntimeError(f"All Gemini models failed after {total_attempts} attempts. Last error: {last_err}")
    raise RuntimeError("503 Service Unavailable: Gemini servers are temporarily overloaded. Please try again in a moment.")

def process_video_transcription(
    video_path: str,
    project_dir: str,
    api_key: str = "",
    check_subtitles: bool = True,
    skip_intro_sec: float = 0.0,
    skip_outro_sec: float = 0.0,
    intro_range_str: str = "",
    outro_range_str: str = "",
    auto_detect_op_ed: bool = False
) -> dict:
    """
    Unified extraction & transcription workflow:
    1. Checks for embedded subtitles first (instant, 100% accurate, cuts OP/ED).
    2. If no subtitles and API key provided, extracts audio (muting OP/ED) and transcribes via Gemini Audio.
    3. Saves results to project directory.
    """
    p_path = Path(project_dir)
    p_path.mkdir(parents=True, exist_ok=True)
    
    transcript_text = ""
    source_type = "none"
    
    # Check embedded subtitles first
    if check_subtitles:
        subs = detect_subtitles(video_path)
        if subs:
            print(f"[+] Found {len(subs)} embedded subtitle stream(s) in video.")
            sub_file = p_path / "extracted_subtitles.srt"
            if extract_embedded_subtitles(video_path, str(sub_file)):
                print(f"[+] Successfully extracted embedded subtitles to: {sub_file}")
                # Clean SRT tags to plain dialogue text
                try:
                    with open(sub_file, "r", encoding="utf-8", errors="replace") as sf:
                        lines = sf.readlines()
                    clean_lines = []
                    for line in lines:
                        l = line.strip()
                        if l and not l.isdigit() and "-->" not in l:
                            if not is_op_ed_text(l):
                                clean_lines.append(l)
                    transcript_text = "\n".join(clean_lines)
                    source_type = "embedded_subtitles"
                except Exception as e:
                    print(f"[Warn] SRT cleanup error: {e}")

    # Extract audio if no subtitles or audio requested
    audio_path = p_path / "extracted_audio.mp3"
    audio_extracted = extract_audio_from_video(
        video_path,
        str(audio_path),
        skip_intro_sec=skip_intro_sec,
        skip_outro_sec=skip_outro_sec,
        intro_range_str=intro_range_str,
        outro_range_str=outro_range_str,
        auto_detect_op_ed=auto_detect_op_ed
    )
    
    # Clear any previous error log
    error_log_file = p_path / "transcript_error.json"
    
    # If we have an API key and transcript isn't already extracted from subtitles
    if not transcript_text and api_key and audio_extracted:
        try:
            transcript_text = transcribe_audio_with_gemini(api_key, str(audio_path))
            source_type = "gemini_audio_transcription"
            # Clear error log on success
            if error_log_file.exists():
                error_log_file.unlink()
        except Exception as e:
            print(f"[Error] Gemini audio transcription failed: {e}", file=sys.stderr)
            # DO NOT save error message as transcript — save error separately
            error_info = {
                "error": str(e),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "audio_path": str(audio_path),
                "api_key_provided": bool(api_key)
            }
            with open(error_log_file, "w", encoding="utf-8") as ef:
                json.dump(error_info, ef, ensure_ascii=False, indent=2)
            transcript_text = ""  # Leave transcript empty — NOT error message
            source_type = "failed"
            
    if not transcript_text and source_type == "none":
        transcript_text = ""
        source_type = "no_api_key"
        
    # Save transcript file (only real transcript content, never error messages)
    transcript_file = p_path / "transcript.txt"
    with open(transcript_file, "w", encoding="utf-8") as f:
        f.write(transcript_text)

    # Parse and save structured timed scenes if any
    timed_scenes = parse_transcript_scenes(str(transcript_file))
    if not timed_scenes and (p_path / "extracted_subtitles.srt").exists():
        timed_scenes = parse_srt_to_timed_chunks(str(p_path / "extracted_subtitles.srt"))
    if timed_scenes:
        with open(p_path / "timed_scenes.json", "w", encoding="utf-8") as sf:
            json.dump(timed_scenes, sf, ensure_ascii=False, indent=2)
    
    # Read error info if transcription failed
    error_message = ""
    if error_log_file.exists():
        try:
            with open(error_log_file, "r", encoding="utf-8") as ef:
                err_data = json.load(ef)
                error_message = err_data.get("error", "")
        except Exception:
            pass
        
    manifest = {
        "source_type": source_type,
        "has_audio": audio_extracted,
        "audio_path": str(audio_path.relative_to(p_path.parent.parent)) if audio_extracted else None,
        "transcript_path": str(transcript_file.relative_to(p_path.parent.parent)),
        "transcript_preview": transcript_text[:300] if transcript_text else "",
        "timed_scenes_count": len(timed_scenes),
        "error": error_message
    }
    
    with open(p_path / "transcript_meta.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        
    return manifest

def parse_time_str_to_seconds(t_str: str) -> float:
    """Parse MM:SS or HH:MM:SS into seconds."""
    parts = t_str.strip().split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    return 0.0

def parse_srt_to_timed_chunks(srt_path: str) -> list:
    """Parse SRT file into list of dicts: [{'start': sec, 'end': sec, 'text': str}]"""
    chunks = []
    if not os.path.exists(srt_path):
        return chunks
    try:
        with open(srt_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        import re
        pattern = r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})\r?\n([\s\S]*?)(?=\n\s*\n|\Z)"
        for m in re.finditer(pattern, content):
            start_str = m.group(1).replace(",", ".")
            end_str = m.group(2).replace(",", ".")
            text = " ".join([l.strip() for l in m.group(3).strip().splitlines() if l.strip()])
            if text:
                s_parts = start_str.split(":")
                start_sec = int(s_parts[0]) * 3600 + int(s_parts[1]) * 60 + float(s_parts[2])
                e_parts = end_str.split(":")
                end_sec = int(e_parts[0]) * 3600 + int(e_parts[1]) * 60 + float(e_parts[2])
                chunks.append({"start": round(start_sec, 2), "end": round(end_sec, 2), "text": text})
    except Exception as e:
        print(f"[Warn] Failed parsing SRT timed chunks: {e}")
    return chunks

def parse_transcript_scenes(transcript_path: str) -> list:
    """Parse transcript text for timestamped scene blocks e.g. (00:46 - 01:43), skipping OP/ED scenes."""
    scenes = []
    if not os.path.exists(transcript_path):
        return scenes
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            
        import re
        current_scene = None
        time_pat = r"\((\d{1,2}:\d{2}(?::\d{2})?)\s*[-–]\s*(\d{1,2}:\d{2}(?::\d{2})?)\)"
        
        for line in lines:
            match = re.search(time_pat, line)
            if match:
                if current_scene:
                    scenes.append(current_scene)
                s_sec = parse_time_str_to_seconds(match.group(1))
                e_sec = parse_time_str_to_seconds(match.group(2))
                title = line.strip().lstrip("#* ").split("(")[0].strip()
                current_scene = {
                    "title": title,
                    "start": s_sec,
                    "end": e_sec,
                    "content": []
                }
            elif current_scene:
                clean = line.strip()
                if clean and not clean.startswith("---") and not clean.startswith("###"):
                    current_scene["content"].append(clean)
                    
        if current_scene:
            scenes.append(current_scene)
            
        filtered_scenes = []
        for sc in scenes:
            text = "\n".join(sc.pop("content", []))
            sc["text"] = text
            # Filter out scenes that are OP/ED songs or sponsor ads
            if not is_op_ed_text(f"{sc['title']} {text}"):
                filtered_scenes.append(sc)
            else:
                print(f"[*] ✂️ Filtered out OP/ED scene from transcript: {sc['title']}")
        scenes = filtered_scenes
    except Exception as e:
        print(f"[Warn] Failed parsing transcript scenes: {e}")
    return scenes

def get_context_for_timestamp(project_dir: str, timestamp: float, window_sec: float = 40.0) -> str:
    """Find the most relevant dialogue / scene synopsis for a given timestamp."""
    p_path = Path(project_dir)
    
    # 1. Try transcript scene blocks
    transcript_file = p_path / "transcript.txt"
    if transcript_file.exists():
        scenes = parse_transcript_scenes(str(transcript_file))
        for sc in scenes:
            if sc["start"] <= timestamp <= sc["end"] or abs(sc["start"] - timestamp) <= 15:
                res = f"[{sc['title']}]\n{sc['text'][:1500]}"
                return res.strip()
                
    # 2. Try SRT subtitle chunks
    srt_file = p_path / "extracted_subtitles.srt"
    if srt_file.exists():
        chunks = parse_srt_to_timed_chunks(str(srt_file))
        matched = [c["text"] for c in chunks if abs(c["start"] - timestamp) <= window_sec]
        if matched:
            return " / ".join(matched[:5])
            
    return ""

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Anime Video Audio & Subtitle Extractor")
    parser.add_argument("--video", required=True, help="Path to input video file")
    parser.add_argument("--project", required=True, help="Project output directory")
    parser.add_argument("--key", default=os.environ.get("GEMINI_API_KEY", ""), help="Gemini API Key")
    parser.add_argument("--skip-intro", default="0", help="Seconds or MM:SS to mute from start for intro/OP")
    parser.add_argument("--skip-outro", default="0", help="Seconds or MM:SS to mute from end for outro/ED")
    parser.add_argument("--intro-range", default="", help="Specific OP range e.g. 01:00-02:30 or 60-150")
    parser.add_argument("--outro-range", default="", help="Specific ED range e.g. 21:30-23:00")
    parser.add_argument("--auto-detect-op-ed", action="store_true", help="Auto-detect OP and ED ranges from video")
    
    args = parser.parse_args()
    skip_intro = parse_time_value(args.skip_intro)
    skip_outro = parse_time_value(args.skip_outro)
    
    res = process_video_transcription(
        args.video,
        args.project,
        api_key=args.key,
        skip_intro_sec=skip_intro,
        skip_outro_sec=skip_outro,
        intro_range_str=args.intro_range,
        outro_range_str=args.outro_range,
        auto_detect_op_ed=args.auto_detect_op_ed
    )
    print(f"Transcription processed. Source: {res['source_type']}")
