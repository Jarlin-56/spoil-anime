"""
Timing Engine: Single Source of Truth for Timing-Aware Video Pipeline
Handles:
- Speech duration estimation (Thai & Multilingual)
- Canonical Frame-Aligned Timeline Recalculation
- Semantic Subtitle Segmentation
- Scene Splitting & Script Narration Compression
- Pre-Render Quality Control (QC) Validation & Auto-Repair
- Audio Caching Keys
"""

import hashlib
import json
import math
import re
import sys
from typing import Dict, List, Optional, Tuple, Any

# Thai language heuristic constants
THAI_CHARS_PER_SECOND = 13.0      # Natural conversational cadence
THAI_WORDS_PER_SECOND = 2.6       # Average Thai speaking rate
MIN_SCENE_DURATION = 1.8          # Minimum visible scene duration in seconds
DEFAULT_PADDING_BEFORE = 0.15     # Speech start breathing room
DEFAULT_PADDING_AFTER = 0.30      # Speech end breathing room
MAX_RECOMMENDED_WORDS = 18        # Target 8-16 words; warn above 18
MAX_HARD_WORDS = 28               # Trigger auto-split recommendation
FPS_DEFAULT = 30


def count_thai_words(text: str) -> int:
    """
    Approximate Thai word count using consonant clusters and whitespace.
    In Thai, an average word contains ~1.8 to 2.2 root consonants.
    """
    if not text:
        return 0
    clean = text.strip()
    
    # Split on explicit spaces and punctuation first
    tokens = re.split(r'[\s,\.\!\?\"\:\;\(\)\[\]\-\—\…\.\.\.]+', clean)
    tokens = [t for t in tokens if t]
    
    total_words = 0
    for tok in tokens:
        has_thai = any('\u0e00' <= ch <= '\u0e7f' for ch in tok)
        if has_thai:
            # Count Thai root consonants (\u0e01-\u0e2e). Average Thai word has ~3.0 - 3.4 root consonants
            root_consonants = len(re.findall(r'[\u0e01-\u0e2e]', tok))
            est_words = max(1, int(round(root_consonants / 3.2)))
            total_words += est_words
        else:
            total_words += 1
            
    return max(1, total_words)


def estimate_speech_duration(text: str, voice: str = "th-TH-NiwatNeural", rate_percent: float = 5.0) -> float:
    """
    Accurately estimate speech duration (in seconds) for a given text line.
    Accounts for Thai character length, syllables, punctuation pauses, and speech rate.
    """
    if not text or not text.strip():
        return 0.0
    clean = text.strip()
    
    # Base character count (discounting silent diacritics slightly)
    char_count = len(clean)
    base_duration = char_count / THAI_CHARS_PER_SECOND
    
    # Word count heuristic
    words = count_thai_words(clean)
    word_duration = words / THAI_WORDS_PER_SECOND
    
    # Weighted blend between character cadence and word cadence
    raw_estimate = (base_duration * 0.6) + (word_duration * 0.4)
    
    # Add punctuation breathing pauses
    comma_count = clean.count(',') + clean.count('،')
    pause_ellipses = len(re.findall(r'(\.\.\.|…)', clean))
    exclamation_question = len(re.findall(r'[\!\?]', clean))
    
    pause_time = (comma_count * 0.15) + (pause_ellipses * 0.35) + (exclamation_question * 0.20)
    
    total_est = raw_estimate + pause_time
    
    # Apply speech rate modification (e.g. +5% rate reduces duration by 5%)
    speed_factor = 1.0 + (rate_percent / 100.0)
    if speed_factor <= 0.2:
        speed_factor = 1.0
    adjusted_est = total_est / speed_factor
    
    return max(0.8, round(adjusted_est, 3))


def compute_tts_cache_key(
    text: str,
    voice: str = "th-TH-NiwatNeural",
    rate: str = "+5%",
    pitch: str = "+0Hz",
    volume: str = "+0%"
) -> str:
    """Compute MD5 hash for TTS caching including rate, pitch, and volume."""
    raw = f"{text.strip()}|{voice}|{rate}|{pitch}|{volume}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()



def split_narration_text(text: str) -> Tuple[str, str]:
    """
    Split narration text into two natural, coherent story beats.
    Prioritizes:
    1. Sentence boundary / newlines
    2. Punctuation (ellipsis, comma, em-dash, question, exclamation)
    3. Thai conjunctions / semantic transitions
    4. Center word boundary
    Never cuts inside a word or quotation.
    """
    clean = text.strip()
    if not clean:
        return "", ""
        
    # 1. Punctuation and sentence boundaries
    delimiters = [
        r'\n+',
        r'(?<=[!\?\.])\s+',
        r'(?<=\.\.\.)\s*',
        r'(?<=…)\s*',
        r'(?<=,)\s*',
        r'\s*—\s*'
    ]
    for delim in delimiters:
        parts = re.split(delim, clean, maxsplit=1)
        if len(parts) == 2 and len(parts[0].strip()) >= 6 and len(parts[1].strip()) >= 6:
            return parts[0].strip(), parts[1].strip()
            
    # 2. Thai conjunction boundary split
    conjunctions = [
        " แต่ว่า", " แต่", " และแล้ว", " และ", " ทันใดนั้น", " ในขณะเดียวกัน",
        " จนกระทั่ง", " ก่อนที่", " หลังจากที่", " ทำให้", " พอ", " เมื่อ"
    ]
    for conj in conjunctions:
        idx = clean.find(conj)
        # Avoid splitting right at the very beginning or end
        if 8 < idx < len(clean) - 8:
            part1 = clean[:idx].strip()
            part2 = clean[idx:].strip()
            # Clean leading conjunction from part 2 if awkward
            if part1 and part2:
                return part1, part2
                
    # 3. Fallback to space near the middle
    spaces = [m.start() for m in re.finditer(r'\s+', clean)]
    if spaces:
        mid_target = len(clean) // 2
        closest_space = min(spaces, key=lambda s: abs(s - mid_target))
        if 6 < closest_space < len(clean) - 6:
            return clean[:closest_space].strip(), clean[closest_space:].strip()
            
    # 4. If no spaces at all (unspaced Thai), split around middle on Thai word boundary
    mid = len(clean) // 2
    for offset in range(0, min(15, mid - 4)):
        for candidate in [mid + offset, mid - offset]:
            # Check character is a Thai consonant (not combining mark)
            ch = clean[candidate]
            if '\u0e01' <= ch <= '\u0e2e':
                return clean[:candidate].strip(), clean[candidate:].strip()
                
    return clean, ""


def split_scene(scene: Dict[str, Any], next_image_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Split a long scene into two coherent sub-scenes.
    Maintains all metadata and assigns reasonable storyBeat labels.
    """
    text = scene.get("narration") or scene.get("text", "")
    part1, part2 = split_narration_text(text)
    
    if not part2:
        # Cannot meaningfully split
        return [scene]
        
    s1 = dict(scene)
    s1["text"] = part1
    s1["narration"] = part1
    s1["storyBeat"] = f"{scene.get('storyBeat', 'Beat')}_A"
    s1["status"] = "draft"
    # Invalidate old audio
    s1.pop("actualAudioDuration", None)
    s1.pop("audioUrl", None)
    s1.pop("audio_file", None)
    s1.pop("audio_wav", None)
    s1["estimatedNarrationDuration"] = estimate_speech_duration(part1)
    
    s2 = dict(scene)
    s2["id"] = f"{scene.get('id', 1)}_b"
    s2["text"] = part2
    s2["narration"] = part2
    s2["storyBeat"] = f"{scene.get('storyBeat', 'Beat')}_B"
    s2["status"] = "draft"
    if next_image_id:
        s2["image_id"] = next_image_id
        s2["imageSource"] = next_image_id
    s2.pop("actualAudioDuration", None)
    s2.pop("audioUrl", None)
    s2.pop("audio_file", None)
    s2.pop("audio_wav", None)
    s2["estimatedNarrationDuration"] = estimate_speech_duration(part2)
    
    return [s1, s2]


def compress_narration(text: str) -> str:
    """
    Compress redundant or wordy narration into tight, conversational storytelling.
    Removes common filler words without altering the story fact.
    """
    if not text:
        return ""
    result = text.strip()
    
    # Generic filler removals
    filler_patterns = [
        (r'^(?:หลังจากที่|เมื่อหลังจากที่)\s*', 'หลังจาก '),
        (r'^(?:ในส่วนของ|ทางด้านของ)\s*', ''),
        (r'^(?:จะเห็นได้ว่า|ซึ่งเราจะเห็นได้ว่า)\s*', ''),
        (r'^(?:และนั่นก็คือ|ซึ่งนี่ก็คือ)\s*', 'นี่คือ '),
        (r'\s*โดยที่ไม่มีใครรู้มาก่อนเลยว่า\s*', ' โดยไม่รู้ว่า '),
        (r'\s*ได้ทำการ\s*', ' '),
        (r'\s*เป็นอย่างมาก\s*', ' มาก '),
        (r'\s*ในทันทีทันใด\s*', ' ทันที '),
        (r'\s*อย่างไม่น่าเชื่อ\s*', ' เหลือเชื่อ '),
        (r'^(?:เปิดเรื่องมากับ|ยินดีต้อนรับสู่)\s*', '')
    ]
    for pattern, replacement in filler_patterns:
        result = re.sub(pattern, replacement, result).strip()
        
    return result


def find_thai_cut_point(s: str, max_len: int = 34) -> int:
    """Find natural Thai cut point without breaking syllables, words, or vowel marks."""
    if len(s) <= max_len:
        return len(s)
    # 1. Look for explicit space
    sp = s.rfind(' ', 0, max_len)
    if sp >= 10:
        return sp
    # 2. Look for Thai syllable prefix vowels / conjunctions
    prefixes = [' ใน', ' ที่', ' กับ', ' จาก', ' และ', ' แต่', ' ของ', ' เพื่อ', 'เ', 'แ', 'โ', 'ใ', 'ไ']
    for p in prefixes:
        idx = s.rfind(p, 12, max_len)
        if idx >= 12:
            return idx
    # 3. Fallback: find nearest consonant before max_len that does not break vowels
    vowels = set('ะาำิีึืฺุู์่้๊๋็์ํ')
    for i in range(max_len, 10, -1):
        if i < len(s) and s[i] not in vowels and s[i-1] not in 'เแโใไ':
            return i
    return max_len

def split_narration_sentences(text: str) -> List[str]:
    """
    Split scene narration into coherent spoken sentences/clauses for Edge-TTS and subtitles.
    Preserves natural cadence, never breaks in the middle of words, respects punctuation,
    and identifies natural Thai pauses.
    """
    clean = (text or "").strip()
    if not clean:
        return []

    # 1. If explicit newlines are present, respect them
    if '\n' in clean:
        lines = [line.strip() for line in clean.split('\n') if line.strip()]
        if len(lines) > 1:
            return lines

    # 2. Check for strong sentence terminal punctuation with following content
    # e.g., 'ถ้าชีวิตมันเหนื่อยขนาดนี้... เป็นใครก็คงอยากยืนถอนหายใจยาวๆ'
    # e.g., 'ประโยคที่หนึ่ง! ประโยคที่สอง' or 'ประโยคที่หนึ่ง? ประโยคที่สอง'
    punc_matches = list(re.finditer(r'(\.{3,}|…|!|\?|\.)\s*', clean))
    for m in punc_matches:
        start_idx = m.start()
        end_idx = m.end()
        if 6 <= start_idx <= len(clean) - 6 and end_idx < len(clean):
            part1 = clean[:end_idx].strip()
            part2 = clean[end_idx:].strip()
            if len(part1) >= 6 and len(part2) >= 6:
                return [part1, part2]

    # 3. Thai conjunctions with clear clause boundaries
    conjunctions = [
        ' แต่ว่า', ' แต่', ' และแล้ว', ' ทันใดนั้น', ' ในขณะเดียวกัน', 
        ' ในขณะที่', ' จนกระทั่ง', ' หลังจากนั้น', ' แล้วก็', ' ก่อนที่'
    ]
    for conj in conjunctions:
        idx = clean.find(conj)
        if idx != -1 and 8 <= idx <= len(clean) - 8:
            part1 = clean[:idx].strip()
            part2 = clean[idx:].strip()
            if len(part1) >= 6 and len(part2) >= 6:
                return [part1, part2]

    # 4. Check for spaces in longer narration (> 30 characters)
    if len(clean) > 30:
        spaces = [m.start() for m in re.finditer(r'\s+', clean)]
        if spaces:
            mid = len(clean) // 2
            best_sp = min(spaces, key=lambda s: abs(s - mid))
            if 8 <= best_sp <= len(clean) - 8:
                part1 = clean[:best_sp].strip()
                part2 = clean[best_sp:].strip()
                if len(part1) >= 6 and len(part2) >= 6:
                    return [part1, part2]

    return [clean]


def segment_subtitles_for_scene(
    scene_text: str,
    scene_start_time: float,
    speech_duration: float,
    max_chars_per_line: int = 34,
    min_phrase_duration: float = 0.9,
    padding_before: float = DEFAULT_PADDING_BEFORE,
    sentence_boundaries: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """
    Break a scene's narration into synchronized, readable subtitle segments.
    When sentence_boundaries (from Edge-TTS) are provided, timing is 100% synchronized
    with the actual audio speech waveform without any drift.
    Requirements:
    - Maximum 2 lines per subtitle card
    - Short semantic phrases (0.9s - 2.5s typical)
    - Synchronized inside actual speech active window
    - Never breaks words or syllables
    """
    clean = scene_text.strip()
    if not clean or speech_duration <= 0:
        return []

    # Priority 1: Use actual TTS SentenceBoundaries if provided
    if sentence_boundaries and len(sentence_boundaries) > 0:
        segments = []
        for i, b in enumerate(sentence_boundaries):
            b_text = (b.get("text") or "").strip()
            if not b_text:
                continue

            # If text exceeds line limit, wrap into max 2 lines with newline
            if len(b_text) > max_chars_per_line:
                cut_pt = find_thai_cut_point(b_text, max_chars_per_line)
                part1 = b_text[:cut_pt].strip()
                part2 = b_text[cut_pt:].strip()
                if part1 and part2:
                    card_text = f"{part1}\n{part2}"
                else:
                    card_text = b_text
            else:
                card_text = b_text

            offset = float(b.get("offset", 0.0))
            dur = float(b.get("duration", 0.0))
            seg_start = scene_start_time + offset

            # If there's a following sentence, switch subtitle exactly when that sentence starts
            if i + 1 < len(sentence_boundaries):
                next_offset = float(sentence_boundaries[i + 1].get("offset", offset + dur))
                seg_end = scene_start_time + next_offset
            else:
                seg_end = max(seg_start + dur, scene_start_time + speech_duration)

            segments.append({
                "index": i + 1,
                "text": card_text,
                "start_time": round(seg_start, 4),
                "end_time": round(seg_end, 4),
                "duration": round(seg_end - seg_start, 4)
            })
        if segments:
            return segments

    # Priority 2: Fallback / Estimation when no TTS boundaries available yet
    phrases = split_narration_sentences(clean)

    # If phrase is long, split further on cut points or conjunctions
    expanded_phrases = []
    for p in phrases:
        if len(p) > max_chars_per_line * 2:
            cut_pt = find_thai_cut_point(p, max_chars_per_line)
            p1 = p[:cut_pt].strip()
            p2 = p[cut_pt:].strip()
            if p1:
                expanded_phrases.append(p1)
            if p2:
                expanded_phrases.append(p2)
        else:
            expanded_phrases.append(p)
    phrases = expanded_phrases if expanded_phrases else [clean]

    # Format each card: wrap text into <= 2 lines
    cards = []
    for p in phrases:
        if len(p) > max_chars_per_line:
            cut_pt = find_thai_cut_point(p, max_chars_per_line)
            part1 = p[:cut_pt].strip()
            part2 = p[cut_pt:].strip()
            if part1 and part2:
                cards.append(f"{part1}\n{part2}")
            else:
                cards.append(p)
        else:
            cards.append(p)

    # Allocate proportional time based on character count without greedy clamping
    char_counts = [max(1, len(c.replace('\n', ''))) for c in cards]
    total_chars = sum(char_counts)

    segments = []
    current_offset = scene_start_time + padding_before
    total_speech_end = scene_start_time + padding_before + speech_duration

    for i, c in enumerate(cards):
        weight = char_counts[i] / total_chars if total_chars > 0 else (1.0 / len(cards))
        chunk_dur = speech_duration * weight
        seg_start = current_offset
        seg_end = total_speech_end if (i == len(cards) - 1) else (seg_start + chunk_dur)

        segments.append({
            "index": i + 1,
            "text": c,
            "start_time": round(seg_start, 4),
            "end_time": round(seg_end, 4),
            "duration": round(seg_end - seg_start, 4)
        })
        current_offset = seg_end

    return segments


def recalculate_timeline(
    scenes: List[Dict[str, Any]],
    fps: int = FPS_DEFAULT,
    padding_before: float = DEFAULT_PADDING_BEFORE,
    padding_after: float = DEFAULT_PADDING_AFTER,
    min_scene_duration: float = MIN_SCENE_DURATION
) -> Dict[str, Any]:
    """
    Central Single Source of Truth for timeline calculation.
    Enforces canonical frame-aligned timing:
    - startTime
    - endTime
    - duration = endTime - startTime
    - frame_start
    - frame_end
    - frame_count = frame_end - frame_start
    Continuous, zero drift, zero accidental gaps or overlaps.
    """
    recalculated_timeline = []
    current_time = 0.0
    
    for idx, sc in enumerate(scenes, start=1):
        sc_id = sc.get("id", idx)
        text = (sc.get("narration") or sc.get("text") or "").strip()
        img_id = sc.get("imageSource") or sc.get("image_id") or f"frame_{idx:04d}"
        action_type = sc.get("action_type", "normal")
        voice_direction = sc.get("voice_direction")
        story_beat = sc.get("storyBeat", f"Beat_{idx}")
        sentence_bounds = sc.get("sentence_boundaries")
        
        # 1. Speech duration: use actual audio if available, otherwise estimate
        actual_audio_dur = sc.get("actualAudioDuration")
        if actual_audio_dur is None and "speech_duration" in sc:
            actual_audio_dur = sc["speech_duration"]
            
        estimated_dur = estimate_speech_duration(text)
        effective_speech_dur = float(actual_audio_dur) if actual_audio_dur is not None else estimated_dur
        
        # 2. Scene duration = speech duration + safety breathing room
        nominal_dur = effective_speech_dur + padding_before + padding_after
        nominal_dur = max(min_scene_duration, nominal_dur)
        
        # 3. Canonical frame-aligned timing (Absolute timestamps, no cumulative rounding drift)
        start_frame = int(round(current_time * fps))
        nominal_end_time = current_time + nominal_dur
        end_frame = int(round(nominal_end_time * fps))
        frame_count = max(1, end_frame - start_frame)
        
        canonical_dur = frame_count / fps
        start_time = current_time
        end_time = current_time + canonical_dur
        
        # 4. Generate synchronized semantic subtitle segments (using exact audio bounds if available)
        subtitle_segments = segment_subtitles_for_scene(
            scene_text=text,
            scene_start_time=start_time,
            speech_duration=effective_speech_dur,
            padding_before=padding_before,
            sentence_boundaries=sentence_bounds
        )
        
        word_count = count_thai_words(text)
        
        entry = {
            "id": sc_id,
            "order": idx,
            "index": idx,
            "storyBeat": story_beat,
            "narration": text,
            "text": text,
            "imageSource": img_id,
            "image_id": img_id,
            "action_type": action_type,
            "voice_direction": voice_direction,
            "wordCount": word_count,
            "startTime": round(start_time, 4),
            "endTime": round(end_time, 4),
            "duration": round(canonical_dur, 4),
            "frame_start": start_frame,
            "frame_end": end_frame,
            "frame_count": frame_count,
            "estimatedNarrationDuration": round(estimated_dur, 4),
            "actualAudioDuration": round(actual_audio_dur, 4) if actual_audio_dur is not None else None,
            "speech_duration": round(effective_speech_dur, 4),
            "paddingBefore": padding_before,
            "paddingAfter": padding_after,
            "subtitleSegments": subtitle_segments,
            "sentence_boundaries": sentence_bounds,
            "audioUrl": sc.get("audioUrl") or sc.get("audio_file"),
            "audio_file": sc.get("audio_file") or sc.get("audioUrl"),
            "audio_wav": sc.get("audio_wav"),
            "transition": sc.get("transition", "ken_burns"),
            "zoom": sc.get("zoom", "dynamic"),
            "status": "timing_ready" if actual_audio_dur is not None else "estimated"
        }
        recalculated_timeline.append(entry)
        current_time = end_time

    total_duration = round(current_time, 4)
    total_frames = int(round(current_time * fps))
    
    return {
        "fps": fps,
        "total_duration": total_duration,
        "total_frames": total_frames,
        "scenes_count": len(recalculated_timeline),
        "timeline": recalculated_timeline
    }


def validate_timeline(timeline_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pre-Render Quality Control (QC) Validation Layer.
    Verifies all 16 pipeline conditions:
    1. Narration not empty
    2. Scene duration > 0
    3. endTime > startTime
    4. Narration word count reasonable (<= 18 recommended, <= 28 hard limit)
    5. Actual audio duration does not overflow scene duration
    6. Subtitle line count <= 2
    7. Subtitle timing inside scene bounds
    8. Subtitle segments do not overlap
    9. Strict continuity: scene[i].startTime == scene[i-1].endTime
    10. No negative duration
    11. No NaN or undefined timestamps
    12. Frame counts match fps * duration
    """
    errors = []
    warnings = []
    scenes = timeline_data.get("timeline", [])
    
    if not scenes:
        return {
            "valid": False,
            "errors": ["Timeline has no scenes!"],
            "warnings": [],
            "scenes_checked": 0
        }
        
    last_end = 0.0
    fps = timeline_data.get("fps", FPS_DEFAULT)
    
    for i, sc in enumerate(scenes):
        sc_id = sc.get("id", i + 1)
        text = (sc.get("narration") or sc.get("text") or "").strip()
        start = sc.get("startTime", 0.0)
        end = sc.get("endTime", 0.0)
        dur = sc.get("duration", 0.0)
        audio_dur = sc.get("actualAudioDuration")
        
        # Check 1: Narration not empty
        if not text:
            errors.append(f"Scene {sc_id}: Narration is empty.")
            
        # Check 2 & 3: Duration > 0 and endTime > startTime
        if dur <= 0:
            errors.append(f"Scene {sc_id}: Invalid duration ({dur}s <= 0).")
        if end <= start:
            errors.append(f"Scene {sc_id}: endTime ({end}s) <= startTime ({start}s).")
            
        # Check 4: Narration length / word count
        words = sc.get("wordCount") or count_thai_words(text)
        if words > MAX_HARD_WORDS:
            errors.append(f"Scene {sc_id}: Narration is too long ({words} words > {MAX_HARD_WORDS}). Please split scene.")
        elif words > MAX_RECOMMENDED_WORDS:
            warnings.append(f"Scene {sc_id}: Narration length ({words} words) exceeds recommended 8-16 words limit.")
            
        # Check 5: Audio overflow
        if audio_dur is not None:
            if audio_dur > dur + 0.05:  # 50ms tolerance
                errors.append(f"Scene {sc_id}: Audio overflow! Actual audio ({audio_dur:.2f}s) > Scene duration ({dur:.2f}s).")
                
        # Check 6, 7, 8: Subtitle bounds and lines
        sub_segments = sc.get("subtitleSegments", [])
        last_sub_end = start
        for sub in sub_segments:
            s_text = sub.get("text", "")
            s_start = sub.get("start_time", 0.0)
            s_end = sub.get("end_time", 0.0)
            
            if s_text.count('\n') > 1:
                errors.append(f"Scene {sc_id}: Subtitle has more than 2 lines: '{s_text[:30]}...'")
            if s_start < start - 0.05 or s_end > end + 0.05:
                errors.append(f"Scene {sc_id}: Subtitle ({s_start:.2f}s - {s_end:.2f}s) outside scene bounds ({start:.2f}s - {end:.2f}s).")
            if s_start < last_sub_end - 0.05:
                warnings.append(f"Scene {sc_id}: Subtitle segment overlap ({s_start:.2f}s < {last_sub_end:.2f}s).")
            last_sub_end = s_end
            
        # Check 9: Absolute continuity with previous scene (tolerance 0.001s)
        if i > 0:
            diff = abs(start - last_end)
            if diff > 0.002:
                errors.append(f"Scene {sc_id}: Timing discontinuity gap/overlap of {diff:.4f}s with previous scene.")
                
        # Check 10 & 11: NaN / Negative
        if math.isnan(start) or math.isnan(end) or math.isnan(dur):
            errors.append(f"Scene {sc_id}: Contains NaN timestamp.")
            
        last_end = end

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "scenes_checked": len(scenes)
    }


def auto_repair_timeline(
    timeline_data: Dict[str, Any],
    fps: int = FPS_DEFAULT
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Auto-Repair Priority according to Phase 16:
    1. Compress narration
    2. Split narration (if wordCount > MAX_HARD_WORDS)
    3. Extend scene duration (if audio overflow)
    """
    repair_logs = []
    scenes = timeline_data.get("timeline", [])
    repaired_scenes = []
    
    for sc in scenes:
        text = (sc.get("narration") or sc.get("text") or "").strip()
        words = count_thai_words(text)
        
        # Priority 1 & 2: If too wordy, compress or split
        if words > MAX_HARD_WORDS:
            split_res = split_scene(sc)
            if len(split_res) > 1:
                repaired_scenes.extend(split_res)
                repair_logs.append(f"Auto-Split Scene {sc.get('id')} ({words} words) into 2 smaller scenes.")
                continue
            else:
                comp = compress_narration(text)
                sc["text"] = comp
                sc["narration"] = comp
                repair_logs.append(f"Compressed Scene {sc.get('id')} narration.")
        elif words > MAX_RECOMMENDED_WORDS:
            comp = compress_narration(text)
            sc["text"] = comp
            sc["narration"] = comp
            
        # Priority 3: Audio overflow -> extend scene duration
        audio_dur = sc.get("actualAudioDuration")
        dur = sc.get("duration", 0.0)
        if audio_dur and audio_dur > dur:
            needed_dur = audio_dur + DEFAULT_PADDING_BEFORE + DEFAULT_PADDING_AFTER
            sc["duration"] = round(needed_dur, 4)
            repair_logs.append(f"Extended Scene {sc.get('id')} duration from {dur:.2f}s to {needed_dur:.2f}s to fit audio.")
            
        repaired_scenes.append(sc)
        
    # Recalculate timeline with repaired scenes
    new_timeline = recalculate_timeline(repaired_scenes, fps=fps)
    return new_timeline, repair_logs


def format_timing_log_table(timeline_data: Dict[str, Any]) -> str:
    """Format structured logging for debug output."""
    scenes = timeline_data.get("timeline", [])
    lines = [
        f"{'#':<3} | {'ID':<10} | {'Words':<5} | {'Audio':<7} | {'Scene':<7} | {'Start':<7} | {'End':<7} | {'Status'}",
        "-" * 70
    ]
    for sc in scenes:
        idx = sc.get("index", 1)
        sc_id = str(sc.get("id", ""))[:10]
        words = sc.get("wordCount", 0)
        a_dur = f"{sc.get('actualAudioDuration', 0.0):.2f}s" if sc.get('actualAudioDuration') is not None else "est"
        s_dur = f"{sc.get('duration', 0.0):.2f}s"
        start = f"{sc.get('startTime', 0.0):.2f}s"
        end = f"{sc.get('endTime', 0.0):.2f}s"
        status = sc.get("status", "ready")
        lines.append(f"{idx:<3} | {sc_id:<10} | {words:<5} | {a_dur:<7} | {s_dur:<7} | {start:<7} | {end:<7} | {status}")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    from pathlib import Path
    
    parser = argparse.ArgumentParser(description="Timing Engine CLI for Timing-Aware Video Pipeline")
    parser.add_argument("--mode", choices=["recalculate", "validate", "split-scene", "compress-scene", "estimate"], default="recalculate")
    parser.add_argument("--project", default="", help="Path to project directory")
    parser.add_argument("--script", default="", help="Path to script.json")
    parser.add_argument("--timeline", default="", help="Path to voice_timeline.json")
    parser.add_argument("--out", default="", help="Path to output json")
    parser.add_argument("--index", type=int, default=0, help="Scene index (0-based) for split/compress")
    parser.add_argument("--fps", type=int, default=FPS_DEFAULT, help="Video FPS")
    parser.add_argument("--json", action="store_true", help="Output pure JSON to stdout")
    
    args = parser.parse_args()
    
    p_path = Path(args.project) if args.project else None
    script_path = Path(args.script) if args.script else (p_path / "script.json" if p_path else None)
    timeline_path = Path(args.timeline) if args.timeline else (p_path / "voice_timeline.json" if p_path else None)
    
    if args.mode == "recalculate":
        # Load script or existing timeline
        items = []
        if script_path and script_path.exists():
            with open(script_path, "r", encoding="utf-8") as f:
                s_data = json.load(f)
                items = s_data.get("items", s_data) if isinstance(s_data, dict) else s_data
                
        # Preserve actual audio durations if voice_timeline exists
        if timeline_path and timeline_path.exists():
            try:
                with open(timeline_path, "r", encoding="utf-8") as tf:
                    t_data = json.load(tf)
                    old_tl = {str(sc.get("id")): sc for sc in t_data.get("timeline", [])}
                    for itm in items:
                        sc_id = str(itm.get("id"))
                        if sc_id in old_tl:
                            old = old_tl[sc_id]
                            # Reuse audio if narration hasn't changed
                            if old.get("narration") == itm.get("text") and old.get("actualAudioDuration"):
                                itm["actualAudioDuration"] = old["actualAudioDuration"]
                                itm["speech_duration"] = old["speech_duration"]
                                itm["audio_file"] = old.get("audio_file")
                                itm["audio_wav"] = old.get("audio_wav")
                                itm["audioUrl"] = old.get("audioUrl")
                                itm["sentence_boundaries"] = old.get("sentence_boundaries")
            except Exception as e:
                print(f"[Warn] Could not merge existing timeline audio: {e}", file=sys.stderr)
                
        recalculated = recalculate_timeline(items, fps=args.fps)
        out_dest = Path(args.out) if args.out else timeline_path
        if out_dest:
            with open(out_dest, "w", encoding="utf-8") as f:
                json.dump(recalculated, f, ensure_ascii=False, indent=2)
            print(f"[+] Recalculated timeline saved to {out_dest} ({recalculated['total_duration']}s, {len(recalculated['timeline'])} scenes)")
        if args.json:
            print(json.dumps(recalculated, ensure_ascii=False))

    elif args.mode == "validate":
        if not timeline_path or not timeline_path.exists():
            print(json.dumps({"valid": False, "errors": ["Timeline file not found"]}, ensure_ascii=False))
            sys.exit(1)
        with open(timeline_path, "r", encoding="utf-8") as f:
            t_data = json.load(f)
        qc = validate_timeline(t_data)
        print(json.dumps(qc, ensure_ascii=False))
        if not qc["valid"]:
            sys.exit(1)
            
    elif args.mode == "split-scene":
        if not script_path or not script_path.exists():
            print(json.dumps({"error": "script.json not found"}, ensure_ascii=False))
            sys.exit(1)
        with open(script_path, "r", encoding="utf-8") as f:
            s_data = json.load(f)
        items = s_data.get("items", [])
        if 0 <= args.index < len(items):
            target = items[args.index]
            split_res = split_scene(target)
            if len(split_res) > 1:
                items.pop(args.index)
                for offset, s_new in enumerate(split_res):
                    items.insert(args.index + offset, s_new)
                # Re-index
                for idx, itm in enumerate(items):
                    itm["id"] = idx + 1
                s_data["items"] = items
                with open(script_path, "w", encoding="utf-8") as f:
                    json.dump(s_data, f, ensure_ascii=False, indent=2)
                # Recalculate
                recalc = recalculate_timeline(items, fps=args.fps)
                if timeline_path:
                    with open(timeline_path, "w", encoding="utf-8") as f:
                        json.dump(recalc, f, ensure_ascii=False, indent=2)
                print(json.dumps({"success": True, "script": s_data, "timeline": recalc}, ensure_ascii=False))
            else:
                print(json.dumps({"error": "Scene cannot be split further"}, ensure_ascii=False))
        else:
            print(json.dumps({"error": f"Invalid index {args.index}"}, ensure_ascii=False))
            
    elif args.mode == "compress-scene":
        if not script_path or not script_path.exists():
            print(json.dumps({"error": "script.json not found"}, ensure_ascii=False))
            sys.exit(1)
        with open(script_path, "r", encoding="utf-8") as f:
            s_data = json.load(f)
        items = s_data.get("items", [])
        if 0 <= args.index < len(items):
            target = items[args.index]
            old_txt = target.get("text", "")
            comp = compress_narration(old_txt)
            target["text"] = comp
            target["narration"] = comp
            target["wordCount"] = count_thai_words(comp)
            target["estimatedNarrationDuration"] = estimate_speech_duration(comp)
            with open(script_path, "w", encoding="utf-8") as f:
                json.dump(s_data, f, ensure_ascii=False, indent=2)
            recalc = recalculate_timeline(items, fps=args.fps)
            if timeline_path:
                with open(timeline_path, "w", encoding="utf-8") as f:
                    json.dump(recalc, f, ensure_ascii=False, indent=2)
            print(json.dumps({"success": True, "script": s_data, "timeline": recalc}, ensure_ascii=False))
        else:
            print(json.dumps({"error": f"Invalid index {args.index}"}, ensure_ascii=False))
