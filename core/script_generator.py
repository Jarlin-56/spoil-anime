import argparse
import base64
from io import BytesIO
import json
import os
import sys
from pathlib import Path
from PIL import Image
import requests

from config import PROJECTS_DIR, ACTION_TYPE_TO_VOICE_DIRECTION
from timing_engine import count_thai_words, estimate_speech_duration, compress_narration

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

try:
    from audio_extractor import get_context_for_timestamp, get_available_gemini_models
except ImportError:
    def get_context_for_timestamp(project_dir: str, timestamp: float, window_sec: float = 40.0) -> str:
        return ""
    def get_available_gemini_models(api_key: str) -> list:
        return ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash-exp"]

def is_error_transcript(text: str) -> bool:
    """Check if transcript text is actually an error message, not real content."""
    if not text or not text.strip():
        return True
    error_markers = [
        "เกิดข้อผิดพลาด", "503 Service Unavailable", "429 Too Many Requests",
        "500 Internal Server Error", "All Gemini models failed",
        "Gemini servers are temporarily overloaded",
        "สกัดเสียงเรียบร้อยแล้ว (ยังไม่มีการเชื่อมต่อ",
        "Audio file processing failed",
        "Failed to get file URI"
    ]
    for marker in error_markers:
        if marker in text:
            return True
    return False


PROMPT_SYSTEM_NARRATION_DIRECTOR = """คุณคือ "สุดยอดนักเล่าเรื่องและผู้กำกับเสียงพากย์ (Narration Director & Storyteller)" มืออาชีพระดับท็อป
หน้าที่ของคุณคือเปลี่ยนข้อมูลเนื้อหาดิบ ให้กลายเป็น "บทพูดที่น่าติดตาม ฟังเพลิน มีชีวิตชีวา และทำให้คนดูอยากฟังประโยคต่อไป"

==================================================
1. CORE WRITING PERSONA (บุคลิกผู้เล่า)
==================================================
คุณคือ: "เพื่อนสนิทที่ดูเรื่องนี้มาก่อนแล้ว และกำลังนั่งเล่าให้เพื่อนฟังอย่างออกรส"
ไม่ใช่: "AI กำลังอ่านบทสรุป Wikipedia" หรือ "ผู้บรรยายสารคดีทางการ"

บุคลิกของเสียง (Voice Personality):
- Casual & Conversational: ภาษาพูดธรรมชาติร่วมสมัย เหมือนคนคุยกันจริงๆ ห้ามใช้ภาษาเขียนทางการ
- Clever & Observational: ช่างสังเกต จับจุดน่าสนใจหรือจุดพิลึกในฉากมาสะกิดคนดู
- Slightly Playful & Light Humor: ขี้เล่นเล็กน้อย มีอารมณ์ขันแบบผู้ดี ไม่ฝืนตลก
- Curious & Retention-Driven: เล่าแล้วทำให้คนดูอยากรู้ประโยคถัดไปตลอดเวลา ("แล้วไงต่อ?")
- Subtle Reaction: มีรีแอ็กชันเบาๆ เมื่อเจอเรื่องแปลกหรือการกระทำแปลกๆ ของตัวละคร แต่ไม่ overacting เกินจริง
- Confident & Engaging: เล่าอย่างมั่นใจ รู้จังหวะผ่อนหนักผ่อนเบา

เปรียบเทียบสไตล์ภาษา (ห้ามใช้ภาษาทางการ):
- ไม่ดี: "จากเหตุการณ์ดังกล่าว ส่งผลให้สถานการณ์มีความเปลี่ยนแปลงอย่างมีนัยสำคัญ"
- ดีกว่า: "แล้วจากตรงนี้แหละ... เรื่องก็เริ่มวุ่นขึ้นมาแล้ว"
- ไม่ดี: "เขาจึงได้ทำการดำเนินการตรวจสอบสิ่งผิดปกติภายในสถานที่"
- ดีกว่า: "เขาเลยลองย่องเข้าไปเช็กดูเงียบๆ... เผื่อจะเจออะไร"

==================================================
2. MAIN OBJECTIVE & RETENTION (เป้าหมายหลัก)
==================================================
- เป้าหมายไม่ใช่ "ยัดข้อมูลให้ครบที่สุด" แต่เป็น "ทำให้คนดูอยากฟังประโยคต่อไป"
- กฎแห่งความอยากรู้: ทุก Scene ต้องมีหน้าที่อย่างน้อยหนึ่งอย่าง:
  * ทำให้สงสัย (Curiosity)
  * ทำให้ประหลาดใจ (Surprise)
  * ทำให้ขำเบาๆ (Light Humor)
  * ทำให้เข้าใจปมสำคัญ (Insight)
  * ทำให้ลุ้นระทึก (Suspense)
  * ผลักดันเรื่องไปข้างหน้า (Progression)
- ห้ามมี Scene ที่เป็น Filler ว่างเปล่า หรือพูดบรรยายภาพซ้ำซ้อนโดยไม่มีประเด็นเด็ดขาด

==================================================
3. HUMOR STYLE (สไตล์ความตลกที่ถูกต้อง)
==================================================
- สัดส่วนความพอดี: 70–80% Story & Information / 20–30% Personality & Humor
- ความตลกต้องเป็น "Light Humor / Situational Wit" เกิดจากมุมมองการเล่า ไม่ใช่มุกคาเฟ่
- ประเภทของ Humor ที่เลือกใช้ให้เหมาะกับบริบท:
  * TYPE A (Reaction): "เอาจริงนะ... ใครจะคิดว่ามันจะเป็นแบบนี้"
  * TYPE B (Dry Humor): "ดูเหมือนเป็นแผนที่ดีนะ... อย่างน้อยก็ในตอนแรก"
  * TYPE C (Sarcasm): "ฟังดูเป็นไอเดียที่ยอดเยี่ยมมาก... ถ้าไม่นับเรื่องที่มันเกือบพัง"
  * TYPE D (Observation): "ตรงนี้แหละที่น่าสนใจ เพราะทุกคนคิดว่าเขาจะทำอีกอย่าง"
  * TYPE E (Unexpected Contrast): "ดูเหมือนทุกอย่างจะเรียบร้อย... ซึ่งแน่นอนว่าไม่ใช่เลย"
  * TYPE F (Casual Reaction): "แล้วเขาก็ทำสิ่งที่คนปกติทั่วไปเขาไม่ทำกัน"
- [กฎเหล็ก]: ห้ามใส่ 55555, ฮ่าๆๆๆ, โอ้โห, OMG ซ้ำซาก ห้ามพยายามตลกทุกประโยค

==================================================
4. STRICT FACTUALITY (ห้ามแต่งเรื่องเพิ่มเด็ดขาด)
==================================================
- ความตลกและเสน่ห์ต้องเกิดจาก "วิธีเล่า (Style)" เท่านั้น
- ห้ามสร้างเหตุการณ์ใหม่ที่ไม่มีในบริบท
- ห้ามสร้างบทสนทนาใหม่ของตัวละครที่ไม่มีในต้นฉบับ
- ห้ามสร้างความคิดในหัวของตัวละครที่ไม่มีหลักฐาน
- ห้ามเปลี่ยนลำดับเหตุการณ์ หรือเปลี่ยนผลลัพธ์ของเรื่องจริง

==================================================
5. STORYTELLING RHYTHM & SENTENCE VARIETY (จังหวะการเล่า)
==================================================
- ห้ามให้ทุกฉากขึ้นต้นหรือลงท้ายด้วยโครงสร้างประโยคเดิมๆ (ห้ามรายงานเหตุการณ์ซ้ำๆ แบบ เขาทำ X / เขาทำ Y / เขาทำ Z)
- สลับจังหวะอารมณ์: FACT -> REACTION -> CURIOSITY -> FACT -> HUMOR -> CONSEQUENCE -> REVEAL
- ผสมผสานรูปแบบประโยค:
  * Short punch: "แล้วมันก็พัง", "จบเลย"
  * Normal: "เขาเลยตัดสินใจกลับไปดูอีกครั้ง"
  * Suspense: "แต่สิ่งที่เขายังไม่รู้ก็คือ..."
  * Reaction: "ตรงนี้แหละ... ผมว่าเริ่มไม่ปกติแล้ว"
  * Question: "แล้วทำไมเขาถึงเลือกทำแบบนั้น?"

==================================================
6. HOOK GENERATION (3 ฉากแรกต้องดึงดูดทันที)
==================================================
- 3 ฉากแรกคือจุดชี้ชะตา: ต้องมี Hook ที่ทรงพลัง ชวนสงสัย หรือเปิดด้วยสถานการณ์ที่แปลกประหลาด
- เช่น "เรื่องนี้ดูเหมือนจะเริ่มแบบปกติ... จนกระทั่งเขาหันกลับไปมองอีกครั้ง" หรือ "ทุกคนในห้องทำเหมือนไม่มีอะไรเกิดขึ้น ปัญหาคือ... มันไม่ปกติเลย"
- ห้ามใช้ Hook ซ้ำแบบเดิมในทุกคลิป

==================================================
7. "SHOW, THEN INTERPRET" (ต่อยอดภาพ ไม่พากย์ซ้ำสิ่งที่เห็นตรงๆ)
==================================================
- ถ้าภาพเห็นชัดอยู่แล้วว่าตัวละครทำอะไร ให้บรรยาย "ความหมาย/แรงจูงใจ/ผลลัพธ์" แทน
- เช่น ภาพคนเดินออกจากห้อง: แทนที่จะบอกว่า "เขาเดินออกจากห้อง" ให้พูดว่า "เขาเลือกเดินออกมา... ก่อนที่เรื่องมันจะบานปลายไปกว่านี้"

==================================================
8. FORBIDDEN GENERIC AI PHRASES (คำต้องห้าม)
==================================================
- หลีกเลี่ยงคำพูดแบบ AI สำเร็จรูป:
  "เรื่องราวนี้", "อย่างที่ทุกคนเห็น", "ในวันนี้", "เราจะพาไปดูกัน", "น่าสนใจเป็นอย่างมาก",
  "ถือเป็นเหตุการณ์ที่น่าทึ่ง", "จากเหตุการณ์ดังกล่าว", "ในขณะเดียวกัน", "สิ่งที่เกิดขึ้นต่อจากนั้น",
  "และนี่คือเหตุผลว่า", "เรียกได้ว่า", "อย่างไรก็ตาม", "ยินดีต้อนรับเข้าสู่"
- ห้าม Over-dramatization คำใหญ่เกินจริงถ้าเนื้อหาไม่ได้บอก:
  "ช็อกโลก", "เปลี่ยนชีวิต", "ไม่มีใครคาดคิด", "น่าตกใจอย่างเหลือเชื่อ", "สุดช็อก", "คุณจะไม่เชื่อแน่นอน"

==================================================
9. TIMING & SCENE CONSTRAINTS (ความยาวและจังหวะ TTS)
==================================================
- กฎ 1 Scene = 1 Idea = 1 Emotional Beat
- ความยาวเป้าหมาย: 8–16 คำภาษาไทยต่อ Scene (กระชับ ไม่เยิ่นเย้อ ห้ามเขียนเกิน 18 คำเด็ดขาด)
- Punctuation as Performance: ใช้เครื่องหมาย , ... ? ! ในจุดที่ต้องการให้เสียง TTS หยุดเว้นจังหวะหายใจ (Pause), สร้างความสงสัย หรือเน้นจุดตกใจ แต่ห้ามใส่จุดไข่ปลา ... ทุกประโยคพร่ำเพรื่อ
- Natural Transitions: หลีกเลี่ยง "ต่อมา...", "จากนั้น...", "หลังจากนั้น..." ซ้ำๆ ให้ใช้ "แต่ปัญหาคือ...", "ตรงนี้แหละ...", "ทีนี้เริ่มแปลกแล้ว", "และเรื่องกลับไม่จบแค่นั้น"

==================================================
10. INTERNAL CANDIDATE GENERATION & SELF-CRITIQUE (ขั้นตอนคิดในใจ)
==================================================
ก่อนสรุปเป็นข้อความสุดท้ายในแต่ละฉาก ให้คุณประเมินในใจ:
1. สร้าง Candidate 3 แบบในใจ:
   - Candidate A: เน้นความคืบหน้าของเรื่อง (Story-focused)
   - Candidate B: เน้นสร้างความอยากรู้ (Curiosity-focused)
   - Candidate C: เน้นความขี้เล่น/ข้อสังเกตคมๆ (Playful/Humor)
2. รวมจุดเด่นของทั้ง 3 แบบให้เป็นประโยคเดียวที่ไหลลื่น เป็นธรรมชาติ
3. ตรวจสอบคุณภาพภายใน (Self-Critique Scoring 0-10):
   - Naturalness (ความเป็นธรรมชาติของภาษาพูด) ต้อง >= 7
   - Curiosity (ความอยากฟังประโยคถัดไป) ต้อง >= 6
   - Personality (มีตัวตนของผู้เล่า ไม่แข็งทื่อ) ต้อง >= 6
   - ถ้าไม่ผ่านเกณฑ์ ให้ Rewrite ทันทีในใจก่อนส่ง JSON ออกมา
"""

PROMPT_SYSTEM_ANIME_RECAP = PROMPT_SYSTEM_NARRATION_DIRECTOR

def format_content_facts_section(title: str, full_transcript: str) -> str:
    """Format the purely factual content source."""
    return f"""### [CONTENT FACTS - ข้อมูลจริงของเรื่อง]
ชื่อเรื่อง/หัวข้อ: "{title}"
เนื้อเรื่องย่อ / บทสนทนา / ข้อมูลจริงจากต้นฉบับ:
{full_transcript}
(หมายเหตุสำคัญ: ยึดถือข้อมูลข้างต้นเป็น Fact 100% ห้ามบิดเบือน ห้ามสลับลำดับเหตุการณ์ และห้ามแต่งข้อมูลเท็จขึ้นมาเด็ดขาด)"""

def format_narration_style_section(total_scenes: int, is_vision: bool = False, example_image_id: str = "frame_0001", example_timestamp: str = "00:15") -> str:
    """Format narration style, voice persona, and formatting instructions."""
    visual_note = (
        "2. จงดูภาพแต่ละภาพ และเขียนบทพากย์ให้สอดคล้องกับภาพจริง พร้อมตีความอารมณ์/ความหมาย (Show, then Interpret)\n"
        if is_vision else
        "2. เขียนบทพากย์ให้ตรงกับบริบทและ Timestamp ของแต่ละฉากอย่างต่อเนื่อง\n"
    )
    return f"""### [NARRATION STYLE & VOICE DIRECTION - คำสั่งการเล่าเรื่อง]
จงนำ [CONTENT FACTS] ด้านบน มาถ่ายทอดเป็นบทพูดจำนวน {total_scenes} ฉาก โดยปฏิบัติตามกฎ Voice Director อย่างเคร่งครัด:

1. สวมบทบาท "เพื่อนเล่าให้เพื่อนฟัง": ภาษาพูดเป็นธรรมชาติ มีลูกล่อลูกชน สนุกสนานน่าติดตาม ไม่เหมือนอ่านสรุป AI
{visual_note}3. One Scene, One Idea: แต่ละฉากเป้าหมายความยาว 8-16 คำภาษาไทย (ห้ามเกิน 18 คำ)
4. Hook และ Retention: 3 ฉากแรกต้อง Hook คนดูทันที และทุกฉากต้องทำให้คนดูอยากรู้ประโยคต่อไป ("แล้วไงต่อ?")
5. Light Humor & Witticism: สอดแทรกอารมณ์ขัน/ข้อสังเกตเบาๆ เมื่อเข้ากับสถานการณ์ ห้ามฝืนตลก และห้ามแต่ง Fact ใหม่
6. หลีกเลี่ยงคำ Generic AI: ห้ามเปิดด้วย "ยินดีต้อนรับ...", "ในวันนี้...", "เรื่องราวนี้..." หรือใช้ "ต่อมา/จากนั้น" ซ้ำๆ
7. จังหวะเสียง TTS: ใช้เครื่องหมาย , ... ? ! ช่วยกำหนดจังหวะหายใจและอารมณ์
8. กฎตัดเพลงเปิด/เพลงปิด: ห้ามพูดถึง Opening/Ending theme, Sponsor, เครดิตผู้สร้าง ให้เข้าเนื้อเรื่องจริงทันที
9. จำนวน items ต้องครบ {total_scenes} ฉากพอดี เรียงตามลำดับเวลาตั้งแต่ต้นจนจบ
10. กำหนด action_type ให้ตรงกับอารมณ์ฉาก: "normal" | "battle" | "shock" | "drama"

ตอบกลับเฉพาะ JSON ที่ถูกต้อง 100% ตามโครงสร้างนี้:
{{
  "title": "ชื่อเรื่อง",
  "voice": "th-TH-NiwatNeural",
  "items": [
    {{
      "id": 1,
      "image_id": "{example_image_id}",
      "timestamp": "{example_timestamp}",
      "text": "ข้อความบทพูดภาษาพูดที่เป็นธรรมชาติ ชวนติดตาม และเข้ากับภาพ...",
      "action_type": "shock"
    }}
  ]
}}"""


def encode_image_thumbnail_b64(image_path: Path, max_dim: int = 480, quality: int = 70) -> str:
    """Resize image to lightweight thumbnail and return base64 string for Gemini Vision."""
    try:
        if not image_path.exists():
            return ""
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as e:
        print(f"[Warn] Could not encode thumbnail for {image_path}: {e}", file=sys.stderr)
        return ""

def find_frame_image_file(project_dir: str, frame_item: dict) -> Path:
    """Locate local image file on disk for a given frame item."""
    p_path = Path(project_dir) if project_dir else None
    
    # 1. Check relative path from root or project
    if frame_item.get("path"):
        raw_path = Path(frame_item["path"])
        if raw_path.exists():
            return raw_path
        if p_path and (PROJECTS_DIR.parent / raw_path).exists():
            return PROJECTS_DIR.parent / raw_path
        if p_path and (p_path / raw_path.name).exists():
            return p_path / raw_path.name
        if p_path and (p_path / "frames" / raw_path.name).exists():
            return p_path / "frames" / raw_path.name

    # 2. Check filename or id in frames dir
    fname = frame_item.get("filename") or f"{frame_item.get('id', 'frame_0001')}.jpg"
    if p_path:
        f_in_frames = p_path / "frames" / fname
        if f_in_frames.exists():
            return f_in_frames
        f_in_root = p_path / fname
        if f_in_root.exists():
            return f_in_root
            
    return None

def assign_story_beat_name(idx: int, total: int) -> str:
    """Determine dynamic story beat category based on story progression position."""
    if total <= 1:
        return "Hook"
    ratio = idx / max(1, total - 1)
    if idx == 0:
        return "Hook"
    elif ratio < 0.20:
        return "Context"
    elif ratio < 0.40:
        return "Curiosity"
    elif ratio < 0.60:
        return "Development"
    elif ratio < 0.80:
        return "Reveal"
    elif ratio < 0.95:
        return "Consequence"
    else:
        return "Conclusion"

def finalize_script_items(items: list) -> list:
    """Enforce 8-16 words per scene, estimate speech durations, and set story beats."""
    total = len(items)
    for idx, itm in enumerate(items):
        text = itm.get("text", "").strip()
        words = count_thai_words(text)
        if words > 18:
            text = compress_narration(text)
            words = count_thai_words(text)
        itm["text"] = text
        itm["narration"] = text
        itm["wordCount"] = words
        itm["estimatedNarrationDuration"] = estimate_speech_duration(text)
        itm["storyBeat"] = assign_story_beat_name(idx, total)
        if "voice_direction" not in itm:
            itm["voice_direction"] = ACTION_TYPE_TO_VOICE_DIRECTION.get(itm.get("action_type", "normal"), "conversational")
        itm["status"] = "script_ready"
    return items

def generate_script_with_gemini(
    api_key: str,
    anime_title: str,
    synopsis_or_transcript: str,
    target_scenes: int = 15,
    storyboard_scenes: list = None,
    project_dir: str = ""
) -> dict:
    models_to_try = get_available_gemini_models(api_key)
    
    total_scenes = len(storyboard_scenes) if storyboard_scenes else target_scenes
    
    # Build multimodal content parts
    parts = []
    
    # Use full transcript without 4000 char cutoff so AI has entire context; guard against error text
    if synopsis_or_transcript and not is_error_transcript(synopsis_or_transcript):
        full_transcript = synopsis_or_transcript.strip()
    else:
        full_transcript = "(ไม่มีบทถอดเสียง ใช้การวิเคราะห์จากภาพ Storyboard)"
    
    content_facts = format_content_facts_section(anime_title, full_transcript)
    example_img = storyboard_scenes[0].get('id', 'frame_0001') if storyboard_scenes else 'frame_0001'
    example_time = storyboard_scenes[0].get('time_str', '00:15') if storyboard_scenes else '00:15'
    style_section = format_narration_style_section(
        total_scenes=total_scenes,
        is_vision=True,
        example_image_id=example_img,
        example_timestamp=example_time
    )
    intro_prompt = f"{content_facts}\n\n{style_section}"
    parts.append({"text": intro_prompt})
    
    # Attach each scene frame + description to parts (Multimodal Vision)
    if storyboard_scenes and len(storyboard_scenes) > 0:
        print(f"[*] Preparing {len(storyboard_scenes)} visual scenes for Gemini Multimodal Vision...")
        for idx, sc in enumerate(storyboard_scenes):
            t_str = sc.get("time_str", format_timestamp(sc.get("timestamp", 0)))
            f_id = sc.get("id", f"frame_{idx+1:04d}")
            ctx = sc.get("context", "")
            ctx_str = f" | บทสนทนา/บริบท: {ctx}" if ctx else ""
            
            # Text label for this specific frame
            parts.append({
                "text": f"\n--- [ฉากที่ {idx+1}/{total_scenes}] ID: {f_id} | เวลา: {t_str}{ctx_str} ---"
            })
            
            # Find image and encode thumbnail
            img_file = find_frame_image_file(project_dir, sc)
            if img_file and img_file.exists():
                b64 = encode_image_thumbnail_b64(img_file, max_dim=480, quality=70)
                if b64:
                    parts.append({
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": b64
                        }
                    })
    
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{
            "parts": parts
        }],
        "systemInstruction": {
            "parts": [{"text": PROMPT_SYSTEM_ANIME_RECAP}]
        },
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.7
        }
    }
    
    last_err = None
    for model_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        for attempt in range(2):
            try:
                print(f"[*] Trying Gemini Vision model: {model_name} (attempt {attempt+1})...")
                resp = requests.post(url, headers=headers, json=payload, timeout=140)
                if resp.status_code == 200:
                    result = resp.json()
                    raw_text = result["candidates"][0]["content"]["parts"][0]["text"]
                    data = json.loads(raw_text)
                    
                    items = data.get("items", [])
                    # Re-align strictly with storyboard scenes to guarantee 100% accurate frame pairing
                    if storyboard_scenes and len(storyboard_scenes) > 0:
                        aligned_items = []
                        for idx, sc in enumerate(storyboard_scenes):
                            f_id = sc.get("id", f"frame_{idx+1:04d}")
                            t_str = sc.get("time_str", format_timestamp(sc.get("timestamp", 0)))
                            if idx < len(items):
                                itm = items[idx]
                                itm["id"] = idx + 1
                                itm["image_id"] = f_id
                                itm["timestamp"] = t_str
                                if "context" in sc:
                                    itm["scene_context"] = sc["context"]
                                aligned_items.append(itm)
                            else:
                                aligned_items.append({
                                    "id": idx + 1,
                                    "image_id": f_id,
                                    "timestamp": t_str,
                                    "text": sc.get("context", f"และแล้วเหตุการณ์สำคัญในฉากที่ {idx+1} ก็ดำเนินต่อไป..."),
                                    "action_type": "normal"
                                })
                        data["items"] = aligned_items
                    data["items"] = finalize_script_items(data.get("items", []))
                    return data
                elif resp.status_code in [503, 429]:
                    print(f"[!] Model {model_name} returned status {resp.status_code}. Waiting 3s...")
                    last_err = f"{resp.status_code}: {resp.text}"
                    import time
                    time.sleep(3)
                    continue
                else:
                    print(f"[!] Model {model_name} returned status {resp.status_code}: {resp.text[:120]}")
                    last_err = f"{resp.status_code}: {resp.text}"
                    break
            except Exception as e:
                last_err = str(e)
                import time
                time.sleep(2)
            
    raise RuntimeError(f"All Gemini Vision models failed. Last error: {last_err}")

def generate_script_with_gemini_text(
    api_key: str,
    anime_title: str,
    synopsis_or_transcript: str,
    target_scenes: int = 15,
    storyboard_scenes: list = None
) -> dict:
    """
    High-reliability, text-based Gemini recap writer using the full audio transcript & scene timestamps.
    Avoids vision upload limits and guarantees a coherent, progressive, non-repeating recap story.
    """
    models_to_try = get_available_gemini_models(api_key)
    total_scenes = len(storyboard_scenes) if storyboard_scenes else target_scenes

    scene_cues = []
    if storyboard_scenes:
        for idx, sc in enumerate(storyboard_scenes):
            t_str = sc.get("time_str", format_timestamp(sc.get("timestamp", 0)))
            f_id = sc.get("id", f"frame_{idx+1:04d}")
            ctx = sc.get("context", "")
            ctx_snip = f" (บริบทเหตุการณ์: {ctx[:120]})" if ctx else ""
            scene_cues.append(f"- ฉากที่ {idx+1}/{total_scenes} [เวลา {t_str}] (ID: {f_id}){ctx_snip}")
    else:
        for idx in range(total_scenes):
            scene_cues.append(f"- ฉากที่ {idx+1}/{total_scenes}")
            
    cues_text = "\n".join(scene_cues)

    clean_synopsis = synopsis_or_transcript.strip() if synopsis_or_transcript and not is_error_transcript(synopsis_or_transcript) else "(ไม่มีบทถอดเสียง ดำเนินเรื่องตามลำดับภาพ Storyboard)"

    content_facts = format_content_facts_section(anime_title, clean_synopsis)
    example_img = storyboard_scenes[0].get('id', 'frame_0001') if storyboard_scenes else 'frame_0001'
    example_time = storyboard_scenes[0].get('time_str', '00:15') if storyboard_scenes else '00:15'
    style_section = format_narration_style_section(
        total_scenes=total_scenes,
        is_vision=False,
        example_image_id=example_img,
        example_timestamp=example_time
    )
    prompt = f"{content_facts}\n\n### รายการลำดับเวลาฉาก ({total_scenes} ฉาก):\n{cues_text}\n\n{style_section}"

    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "systemInstruction": {"parts": [{"text": PROMPT_SYSTEM_ANIME_RECAP}]},
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.7
        }
    }

    last_err = None
    for model_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        for attempt in range(2):
            try:
                print(f"[*] Trying Gemini Text Recap model: {model_name} (attempt {attempt+1})...")
                resp = requests.post(url, headers=headers, json=payload, timeout=120)
                if resp.status_code == 200:
                    raw_text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
                    data = json.loads(raw_text)
                    items = data.get("items", [])
                    if storyboard_scenes and len(storyboard_scenes) > 0:
                        aligned = []
                        for idx, sc in enumerate(storyboard_scenes):
                            f_id = sc.get("id", f"frame_{idx+1:04d}")
                            t_str = sc.get("time_str", format_timestamp(sc.get("timestamp", 0)))
                            if idx < len(items):
                                itm = items[idx]
                                itm["id"] = idx + 1
                                itm["image_id"] = f_id
                                itm["timestamp"] = t_str
                                if "context" in sc:
                                    itm["scene_context"] = sc["context"]
                                aligned.append(itm)
                            else:
                                aligned.append({
                                    "id": idx + 1,
                                    "image_id": f_id,
                                    "timestamp": t_str,
                                    "text": sc.get("context", f"เรื่องราวความเข้มข้นดำเนินมาถึงฉากที่ {idx+1}..."),
                                    "action_type": "normal"
                                })
                        data["items"] = finalize_script_items(aligned)
                    else:
                        data["items"] = finalize_script_items(data.get("items", []))
                    return data
                elif resp.status_code in [503, 429]:
                    time.sleep(3)
                    continue
                else:
                    break
            except Exception as e:
                last_err = str(e)
                time.sleep(2)

    raise RuntimeError(f"Gemini Text Recap failed. Last error: {last_err}")

def parse_transcript_story_beats(transcript: str) -> list:
    """Extract individual meaningful story beats, events, and dialogues in chronological order."""
    import re
    if not transcript:
        return []
    lines = transcript.split("\n")
    beats = []
    for line in lines:
        clean = line.strip()
        clean = re.sub(r"^[\*\-\d\.\s]+", "", clean).strip()
        clean = re.sub(r"[\*\_#]", "", clean).strip()
        
        # Filter out markdown headers, meta remarks, timestamps, sponsor mentions, OP/ED
        if any(w in clean for w in [
            "นี่คือข้อมูล", "Timestamps", "Scene Breakdown", "สปอนเซอร์",
            "อินโทรเปิดรายการ", "ช่วงเวลา:", "ตัวละครหลัก:", "สรุปเนื้อหาหลัก", "Overall Synopsis",
            "เพลงเปิด", "เพลงปิด", "Intro", "Outro", "Opening Theme", "Ending Theme",
            "animegogo", "โฆษณาเว็บไซต์", "theme song", "เครดิตจบ"
        ]):
            continue
        if any(clean.startswith(k) for k in ["[", "ช่วงเวลา", "ตัวละครหลัก", "บทสนทนาสำคัญ", "สรุปเนื้อหา"]):
            continue
        if re.match(r"^\d+\.\s+", clean) and ("(" in clean and ")" in clean):
            continue
            
        # Strip common bullet prefixes like "เหตุการณ์:"
        clean = re.sub(r"^(?:เหตุการณ์\/การต่อสู้|การต่อสู้\/เหตุการณ์|เหตุการณ์|การต่อสู้)\s*:\s*", "", clean).strip()
        clean = re.sub(r"^บทสนทนาสำคัญ\s*:\s*", "", clean).strip()
        
        # Skip if line is just a category label or too short
        if clean.rstrip(":") in ["เหตุการณ์/การต่อสู้", "การต่อสู้/เหตุการณ์", "เหตุการณ์", "การต่อสู้", "บทสนทนาสำคัญ"]:
            continue
        if not clean or len(clean) < 10:
            continue
            
        # If a single beat is long (> 16 words), split into distinct sub-clause beats
        words_in_beat = count_thai_words(clean)
        if words_in_beat > 16:
            raw_chunks = re.split(r'[\.!?\r\n]+|\s+', clean)
            curr = ""
            for chk in raw_chunks:
                chk = chk.strip()
                if not chk:
                    continue
                if not curr:
                    curr = chk
                elif count_thai_words(curr + " " + chk) <= 15:
                    curr += " " + chk
                else:
                    if len(curr) >= 8:
                        beats.append(curr)
                    curr = chk
            if curr and len(curr) >= 8:
                beats.append(curr)
        else:
            beats.append(clean)
    return beats

def generate_sample_script(
    anime_title: str = "อนิเมะสุดมันส์",
    num_scenes: int = 15,
    storyboard_scenes: list = None,
    synopsis: str = ""
) -> dict:
    """
    Conversational Storyteller fallback engine adhering strictly to the "Friend telling a friend" persona.
    Generates natural spoken Thai narration with curiosity loops, subtle observations, light situational wit,
    and rhythm variation, while strictly avoiding repetitive robotic transitions.
    """
    count = len(storyboard_scenes) if storyboard_scenes else num_scenes
    items = []
    
    all_beats = parse_transcript_story_beats(synopsis)
    used_narrations = set()
    beat_cursor = 0
    
    # Conversational bridges (Natural spoken observations & retention prompts instead of robotic reports)
    transitional_bridges = [
        "ตรงนี้แหละที่น่าสนใจ... เพราะเรื่องเริ่มไม่เป็นไปตามแผนแล้ว",
        "ดูเหมือนจะไม่มีอะไร... แต่ความจริงคือปัญหากำลังจะตามมา",
        "และนี่ทำให้สถานการณ์เริ่มเปลี่ยนทิศทางทันที",
        "ฟังดูเหมือนจะเรียบร้อย... แต่เดี๋ยวก่อน เรื่องยังไม่จบง่ายๆ",
        "จุดนี้ทำเอาทุกคนเริ่มตั้งข้อสงสัยว่าเกิดอะไรขึ้นกันแน่",
        "แล้วเขาก็เลือกทำสิ่งที่ไม่มีใครคาดคิดในจังหวะนั้น",
        "เอาเป็นว่า... สถานการณ์ตอนนี้เริ่มตึงมือขึ้นเรื่อยๆ แล้ว",
        "ซึ่งแน่นอนว่า... เรื่องราวกลับยิ่งทวีความวุ่นวายขึ้นไปอีก"
    ]
    bridge_idx = 0

    # Spoken conversational connectors (Type A-F humor & retention style)
    spoken_connectors = [
        "ตรงนี้แหละ... ",
        "แต่ปัญหาคือ ",
        "ซึ่งจริงๆ แล้ว ",
        "และเรื่องกลับไม่จบแค่นั้น เมื่อ ",
        "ทีนี้เริ่มแปลกแล้ว เพราะ ",
        "สิ่งที่ยังไม่มีใครรู้ก็คือ ",
        "เอาจริงนะ... ",
        "แล้วจุดสำคัญก็คือ ",
        "แต่เดี๋ยวก่อน... ",
        "และนั่นทำให้ "
    ]
    
    for i in range(count):
        sc = storyboard_scenes[i] if storyboard_scenes and i < len(storyboard_scenes) else {}
        frame_id = sc.get("id", f"frame_{i+1:04d}")
        t_str = sc.get("time_str", format_timestamp(sc.get("timestamp", 0)))
        ctx = sc.get("context", "")
        
        # Extract beats specifically for this scene's context
        scene_beats = parse_transcript_story_beats(ctx)
        
        text = ""
        action = "normal"
        
        # 1. First scene special hook (Engaging & Retention-Driven)
        if i == 0:
            if scene_beats or all_beats:
                first_beat = (scene_beats or all_beats)[0]
                # If first beat already poses an engaging question or hook
                if any(first_beat.startswith(q) for q in ["เคยสงสัย", "ทำไม", "รู้หรือไม่", "คุณเคย"]):
                    text = first_beat
                else:
                    hook_options = [
                        f"เรื่องนี้เริ่มแบบปกติ... จนกระทั่ง {first_beat}",
                        f"เปิดมาก็สะดุดใจทันที เมื่อ {first_beat}",
                        f"จุดเริ่มต้นมาจากตอนที่ {first_beat}"
                    ]
                    chosen = hook_options[abs(hash(anime_title)) % len(hook_options)]
                    text = chosen if count_thai_words(chosen) <= 16 else first_beat
                used_narrations.add(first_beat)
            else:
                text = f"เรื่องนี้ดูเผินๆ เหมือนไม่มีอะไร... จนกระทั่งความผิดปกติแรกเริ่มโผล่มา"
            action = "shock"
            
        # 2. Last scene outro (Curious closing hook)
        elif i == count - 1:
            text = "และนี่คือจุดเปลี่ยนสำคัญของเรื่องนี้... ต้องมารอดูว่าสุดท้ายจะลงเอยยังไง"
            action = "normal"
            
        # 3. Middle scenes: pick unused beat from scene_beats or all_beats
        else:
            candidate = None
            for b in scene_beats:
                if b not in used_narrations and len(b) > 8:
                    candidate = b
                    break
                    
            if not candidate:
                while beat_cursor < len(all_beats):
                    b = all_beats[beat_cursor]
                    beat_cursor += 1
                    if b not in used_narrations and len(b) > 8:
                        candidate = b
                        break
                        
            if candidate:
                used_narrations.add(candidate)
                
                # Check if it is dialogue
                if ":" in candidate and ('"' in candidate or '“' in candidate or "'" in candidate):
                    parts = candidate.split(":", 1)
                    speaker = parts[0].strip()
                    dialogue = parts[1].strip().strip('"“”\'')
                    # Conversational human narration of dialogue
                    text = f'{speaker} ถึงกับหลุดปากว่า "{dialogue}"'
                    action = "drama"
                else:
                    cand_words = count_thai_words(candidate)
                    if cand_words >= 14:
                        text = candidate
                    else:
                        # Alternating conversational rhythm with concise spoken connectors
                        rhythm_mod = i % 4
                        if rhythm_mod == 1:
                            conn = "ตรงนี้แหละ... " if i % 2 == 0 else "แต่ปัญหาคือ "
                            text = f"{conn}{candidate}"
                        elif rhythm_mod == 2:
                            text = f"จุดที่น่าสนใจคือ {candidate}"
                        elif rhythm_mod == 3:
                            text = f"แต่สิ่งที่ตามมาคือ {candidate}"
                        else:
                            conn = "ซึ่งจริงๆ แล้ว " if i % 2 == 0 else "ทีนี้เริ่มแปลกเพราะ "
                            text = f"{conn}{candidate}"
                            
                    # Guard: if connector pushed text over 18 words, fallback to candidate
                    if count_thai_words(text) > 18 and count_thai_words(candidate) <= 18:
                        text = candidate
                    
                    # Detect action type
                    if any(w in candidate for w in ["ต่อสู้", "โจมตี", "ฟัน", "ปะทะ", "ดาบ", "พลัง", "บุก", "ฆ่า"]):
                        action = "battle"
                    elif any(w in candidate for w in ["ตกใจ", "ตะลึง", "ไม่คาดคิด", "จู่ๆ", "ความจริง", "ผิดปกติ"]):
                        action = "shock"
                    elif any(w in candidate for w in ["เสียใจ", "สัญญา", "รัก", "แค้น", "วิกฤต", "แต่งงาน"]):
                        action = "drama"
            else:
                # No unused beats left: conversational observation bridge
                text = transitional_bridges[bridge_idx % len(transitional_bridges)]
                bridge_idx += 1
                action = "normal" if i % 2 == 0 else "shock"
                
        # Deduplication safety check: if exact text was already used or empty, pick unique observation bridge
        if (not text or text in used_narrations) and i > 0 and i < count - 1:
            text = transitional_bridges[bridge_idx % len(transitional_bridges)]
            bridge_idx += 1
            
        used_narrations.add(text)
        
        item_obj = {
            "id": i + 1,
            "image_id": frame_id,
            "timestamp": t_str,
            "text": text,
            "action_type": action
        }
        if sc and "context" in sc:
            item_obj["scene_context"] = sc["context"]
        items.append(item_obj)
        
    return {
        "title": anime_title,
        "voice": "th-TH-NiwatNeural",
        "items": finalize_script_items(items)
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Script Generator for Anime Recap")
    parser.add_argument("--title", default="Anime Recap Episode", help="Anime title")
    parser.add_argument("--out", required=True, help="Output script JSON file path")
    parser.add_argument("--key", default=os.environ.get("GEMINI_API_KEY", ""), help="Gemini API Key")
    parser.add_argument("--synopsis", default="", help="Synopsis text or transcript")
    parser.add_argument("--project", default="", help="Path to project directory to read transcript/frames")
    parser.add_argument("--count", type=int, default=15, help="Number of scene items")
    parser.add_argument("--resample", action="store_true", help="Force resample frames covering beginning to end")
    
    args = parser.parse_args()
    
    synopsis_text = args.synopsis
    storyboard_scenes = []
    available_frames = []
    
    # Guard: filter out error transcripts passed as synopsis
    if is_error_transcript(synopsis_text):
        print(f"[Warn] Synopsis contains error message or is empty. Clearing to avoid polluting AI script generation.")
        synopsis_text = ""
    
    if args.project:
        p_path = Path(args.project)
        transcript_file = p_path / "transcript.txt"
        manifest_file = p_path / "frames_manifest.json"
        storyboard_file = p_path / "storyboard.json"
        
        if not synopsis_text and transcript_file.exists():
            with open(transcript_file, "r", encoding="utf-8", errors="replace") as tf:
                raw_transcript = tf.read().strip()
                if is_error_transcript(raw_transcript):
                    print(f"[Warn] Transcript file contains error message. Skipping and relying on Storyboard images.")
                    synopsis_text = ""
                else:
                    synopsis_text = raw_transcript
                    print(f"[*] Loaded transcript from {transcript_file} ({len(synopsis_text)} chars)")
                
        if manifest_file.exists():
            with open(manifest_file, "r", encoding="utf-8") as mf:
                available_frames = json.load(mf)
                
        # 1. Check if we need to select args.count frames evenly across video from beginning to end
        need_resample = args.resample or not storyboard_file.exists()
        if not need_resample and storyboard_file.exists():
            try:
                with open(storyboard_file, "r", encoding="utf-8") as sf:
                    existing = json.load(sf)
                    if len(existing) == args.count:
                        storyboard_scenes = existing
                        print(f"[*] Loaded existing storyboard with {len(storyboard_scenes)} scenes.")
                    else:
                        need_resample = True
                        print(f"[*] Existing storyboard count ({len(existing)}) differs from requested count ({args.count}). Re-sampling across full timeline...")
            except Exception as e:
                need_resample = True
                print(f"[Warn] Could not read storyboard.json: {e}")
                
        # 2. Select args.count frames evenly across video from beginning to end
        if (need_resample or not storyboard_scenes) and available_frames:
            storyboard_scenes = select_storyboard_scenes(available_frames, target_count=args.count)
            print(f"[*] Auto-selected {len(storyboard_scenes)} scenes from {len(available_frames)} available frames spanning whole timeline.")
            
        # Enrich storyboard scenes with timestamp context
        for sc in storyboard_scenes:
            ts = sc.get("timestamp")
            if ts is not None and "context" not in sc:
                ctx = get_context_for_timestamp(args.project, float(ts))
                if ctx:
                    sc["context"] = ctx
                    
        # Save updated storyboard.json
        if storyboard_scenes:
            with open(p_path / "storyboard.json", "w", encoding="utf-8") as sf:
                json.dump(storyboard_scenes, sf, ensure_ascii=False, indent=2)
                
    target_count = len(storyboard_scenes) if storyboard_scenes else args.count
    
    if args.key:
        print(f"[*] Calling Gemini to write recap script for '{args.title}' with {target_count} scenes...")
        script_data = None
        # Tier 1: Multimodal Vision
        try:
            print("[*] Tier 1: Trying Gemini Multimodal Vision (Image-First)...")
            script_data = generate_script_with_gemini(
                api_key=args.key,
                anime_title=args.title,
                synopsis_or_transcript=synopsis_text,
                target_scenes=target_count,
                storyboard_scenes=storyboard_scenes,
                project_dir=args.project
            )
        except Exception as e_vis:
            print(f"[Warn] Gemini Vision failed: {e_vis}")
            # Tier 2: Gemini Text with full transcript & timestamps
            try:
                print("[*] Tier 2: Trying Gemini Text Recap with full transcript and timestamps...")
                script_data = generate_script_with_gemini_text(
                    api_key=args.key,
                    anime_title=args.title,
                    synopsis_or_transcript=synopsis_text,
                    target_scenes=target_count,
                    storyboard_scenes=storyboard_scenes
                )
            except Exception as e_text:
                print(f"[Warn] Gemini Text Recap failed: {e_text}. Falling back to Smart Anti-Repetition Generator...")
                script_data = generate_sample_script(args.title, num_scenes=target_count, storyboard_scenes=storyboard_scenes, synopsis=synopsis_text)
    else:
        print(f"[*] Generating progressive, non-repeating recap script ({target_count} scenes)...")
        script_data = generate_sample_script(args.title, num_scenes=target_count, storyboard_scenes=storyboard_scenes, synopsis=synopsis_text)
        
    out_file = Path(args.out)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(script_data, f, ensure_ascii=False, indent=2)
        
    print(f"[+] Script saved to: {out_file} ({len(script_data['items'])} scenes)")

