#!/usr/bin/env python3
"""
Expressive TTS Provider Proof of Concept (Phase 1.2)
Anime Recap Pipeline — สปอย

A standalone evaluation suite comparing:
  Option A/B/C (blinded):
    - Edge-TTS Baseline (pre-Phase 1 static delivery: +5% rate, +0Hz pitch)
    - Edge-TTS Phase 1 (scene-aware rate & pitch profiles)
    - Gemini TTS Candidate (gemini-3.8-flash-tts with natural-language performance direction)

Usage:
    # 1. Offline Dry-Run (validates plan, structure, and prompts without API calls):
    python scripts/poc_gemini_tts_comparison.py --dry-run

    # 2. Live Synthesis (requires GEMINI_API_KEY environment variable):
    python scripts/poc_gemini_tts_comparison.py --out-dir projects/_temp/gemini_tts_poc
"""

import argparse
import asyncio
import base64
import json
import os
import random
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add core to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
CORE_DIR = BASE_DIR / "core"
sys.path.insert(0, str(CORE_DIR))

import requests
import edge_tts
from config import (
    DEFAULT_VOICE,
    resolve_voice_profile,
    FFMPEG_BIN,
    FFPROBE_BIN
)
from timing_engine import split_narration_sentences
from tts_engine import get_audio_duration

# Pre-Phase 1 static delivery baseline
BASELINE_VOICE = DEFAULT_VOICE  # th-TH-NiwatNeural
BASELINE_RATE = "+5%"
BASELINE_PITCH = "+0Hz"
BASELINE_VOLUME = "+0%"

# Target Gemini TTS model and voice settings
# gemini-2.5-flash-preview-tts has separate quota from 3.8, useful when 3.8 quota is exhausted.
# gemini-3.8-flash-tts supports speech_metadata.style for fine-grained delivery control.
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-preview-tts"
DEFAULT_GEMINI_VOICE = "Puck"  # Upbeat, versatile voice suitable for recap narration

# Models that support speech_metadata.style (3.8 family)
SPEECH_METADATA_MODELS = {"gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"}
# Inter-request delay to respect Free Tier 3 RPM limit
GEMINI_REQUEST_DELAY_SEC = 25

# Controlled Thai Anime Recap Test Scenarios
# Covering: Conversational narration, Energetic battle, Suspenseful reveal,
#           Emotional drama, Dry humor, and Surprised / Plot Twist.
TEST_SCENARIOS = [
    {
        "id": "01",
        "direction": "conversational",
        "scenario": "Story Exposition / World Travel",
        "scene_type": "Conversational narration",
        "action_type": "normal",
        "text": "หลังจากเดินทางข้ามทวีปมาหลายสัปดาห์ ในที่สุดพวกเขาก็มาถึงเมืองหลวงอันเงียบสงบ",
        "gemini_style": "confident, natural storytelling with steady relaxed pacing"
    },
    {
        "id": "02",
        "direction": "energetic",
        "scenario": "Climactic Combat / Battle Charge",
        "scene_type": "Energetic battle",
        "action_type": "battle",
        "text": "ทันใดนั้นเขาก็ชักดาบเพลิงออกมา แล้วพุ่งฝ่าวงล้อมของศัตรูเข้าไปอย่างดุดัน!",
        "gemini_style": "energetic, urgent, and gripping battle narration with brisk exciting delivery"
    },
    {
        "id": "03",
        "direction": "suspenseful",
        "scenario": "Dark Mystery / Stealth Investigation",
        "scene_type": "Suspenseful reveal",
        "action_type": "normal",
        "text": "รอยเท้าปริศนาที่พื้นค่อยๆ เลือนหายไปในเงามืด... ราวกับมีบางอย่างกำลังจับตาดูอยู่",
        "gemini_style": "restrained tension with deliberate slow pacing and eerie mysterious undertone"
    },
    {
        "id": "04",
        "direction": "dramatic",
        "scenario": "Tragic Sacrifice / Bittersweet Resolution",
        "scene_type": "Emotional drama",
        "action_type": "drama",
        "text": "แม้จะต้องสูญเสียความทรงจำทั้งหมดไป แต่เขาก็ยังเลือกที่จะปกป้องทุกคนไว้จนวินาทีสุดท้าย",
        "gemini_style": "warm, subdued emotional gravity with heartfelt sorrow and tender reflective pacing"
    },
    {
        "id": "05",
        "direction": "dry_humor",
        "scenario": "Comical Anticlimax / Absurd Failure",
        "scene_type": "Dry humor",
        "action_type": "normal",
        "text": "เขาอุตส่าห์เตรียมแผนการรบมาอย่างดิบดี แต่ศัตรูดันลื่นล้มหัวฟาดพื้นสลบไปเองซะงั้น",
        "gemini_style": "deadpan comedic timing with understated irony and bemused matter-of-fact delivery"
    },
    {
        "id": "06",
        "direction": "surprised",
        "scenario": "Sudden Plot Twist / Unmasking",
        "scene_type": "Surprised twist",
        "action_type": "shock",
        "text": "แต่เมื่อหน้ากากนั้นหลุดออก ทุกคนต่างตกตะลึง เพราะใบหน้านั้นคือเพื่อนสนิทที่หายตัวไป!",
        "gemini_style": "sudden genuine astonishment with sharp dynamic contrast and crisp articulation"
    }
]


def convert_audio_to_mp3(raw_bytes: bytes, mime_type: str, dest_mp3_path: Path):
    """Convert raw audio bytes (WAV, PCM, or MP3) to standard MP3 file for blinded listening.

    Handles raw PCM (audio/L16) from gemini-2.5-flash-preview-tts by parsing
    the sample rate from the mime type and passing explicit ffmpeg input flags.
    """
    dest_mp3_path.parent.mkdir(parents=True, exist_ok=True)
    temp_raw = dest_mp3_path.with_suffix(".tmp.audio")
    try:
        with open(temp_raw, "wb") as f:
            f.write(raw_bytes)

        # Detect raw PCM format: "audio/L16;codec=pcm;rate=24000"
        input_flags = []
        if "L16" in mime_type or "pcm" in mime_type.lower():
            # Parse sample rate from mime type (e.g. "rate=24000")
            sample_rate = "24000"  # default for Gemini TTS
            for part in mime_type.split(";"):
                part = part.strip()
                if part.startswith("rate="):
                    sample_rate = part.split("=", 1)[1].strip()
            input_flags = ["-f", "s16le", "-ar", sample_rate, "-ac", "1"]

        cmd = [
            FFMPEG_BIN, "-y",
            *input_flags,
            "-i", str(temp_raw),
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            str(dest_mp3_path)
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    finally:
        if temp_raw.exists():
            temp_raw.unlink()


async def synthesize_edge_tts(text: str, voice: str, rate: str, pitch: str, volume: str, output_path: Path):
    """Synthesize speech file using Edge-TTS matching production clause splitting."""
    sentences = split_narration_sentences(text)
    tts_text = "\n".join(sentences) if sentences else text
    communicate = edge_tts.Communicate(
        text=tts_text,
        voice=voice,
        rate=rate,
        pitch=pitch,
        volume=volume
    )
    await communicate.save(str(output_path))


def synthesize_gemini_tts(
    text: str,
    style_description: str,
    api_key: str,
    model_name: str = DEFAULT_GEMINI_MODEL,
    voice_name: str = DEFAULT_GEMINI_VOICE,
    timeout: int = 60,
    max_retries: int = 3,
    base_delay: float = 25.0
) -> Tuple[bytes, str, dict]:
    """
    Call Google Gemini TTS generateContent endpoint.

    IMPORTANT: gemini-3.8-flash-tts is a pure TTS model:
      - Does NOT support systemInstruction (HTTP 400)
      - Treats ALL text parts as verbatim transcript to speak aloud
      - Style/delivery direction MUST be passed via speech_metadata.style
        field attached to the text part, NOT as a separate text part

    Includes retry with exponential backoff for HTTP 429 (rate limit).
    Free Tier allows only 3 requests/minute for this model.

    Returns: (audio_bytes, mime_type, raw_metadata)
    """
    if not api_key:
        raise ValueError("GEMINI_API_KEY is required for live Gemini TTS synthesis.")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}

    # Build payload based on model capabilities:
    # - gemini-3.8-flash-tts/lite: supports speech_metadata.style on text parts
    # - gemini-2.5-flash-preview-tts: plain text only (no style metadata)
    uses_speech_metadata = any(m in model_name for m in ["3.8-flash-tts", "3.8-flash-lite-tts"])

    if uses_speech_metadata and style_description:
        # 3.8 models: attach style via speech_metadata.style
        text_part = {
            "text": text,
            "speech_metadata": {
                "style": style_description
            }
        }
    else:
        # 2.5 and other models: plain text only
        text_part = {"text": text}

    payload = {
        "contents": [
            {
                "parts": [text_part]
            }
        ],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": voice_name
                    }
                }
            }
        }
    }

    import time as _time

    last_error = None
    for attempt in range(max_retries + 1):
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)

        if resp.status_code == 200:
            break
        elif resp.status_code == 429:
            # Rate limited — wait and retry with exponential backoff
            delay = base_delay * (2 ** attempt)
            last_error = f"HTTP 429 Rate Limited (attempt {attempt + 1}/{max_retries + 1})"
            print(f"     [Rate Limit] HTTP 429 — waiting {delay:.0f}s before retry ({attempt + 1}/{max_retries + 1})...")
            _time.sleep(delay)
            continue
        else:
            err_msg = f"Gemini TTS request failed with HTTP {resp.status_code}: {resp.text}"
            raise RuntimeError(err_msg)
    else:
        # Exhausted all retries on 429
        raise RuntimeError(f"Gemini TTS rate limit exceeded after {max_retries + 1} attempts. {last_error}")

    data = resp.json()
    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError(f"Gemini TTS returned no candidates in response: {data}")

    parts = candidates[0].get("content", {}).get("parts", [])
    audio_part = None
    for p in parts:
        if "inlineData" in p:
            audio_part = p["inlineData"]
            break
        elif "inline_data" in p:
            audio_part = p["inline_data"]
            break

    if not audio_part or "data" not in audio_part:
        raise RuntimeError(f"Gemini TTS returned candidate without audio inlineData: {parts}")

    mime_type = audio_part.get("mimeType", audio_part.get("mime_type", "audio/wav"))
    audio_bytes = base64.b64decode(audio_part["data"])

    raw_meta = {
        "model": model_name,
        "voice": voice_name,
        "finish_reason": candidates[0].get("finishReason", "STOP"),
        "usage_metadata": data.get("usageMetadata", {}),
        "mime_type": mime_type
    }
    return audio_bytes, mime_type, raw_meta


def build_poc_comparison_plan(seed: int = 42) -> dict:
    """
    Build reproducible 3-way comparison plan with deterministic blinded shuffling (A, B, C).
    Guarantees every scene contains:
      - 1 Edge-TTS Baseline
      - 1 Edge-TTS Phase 1 Candidate
      - 1 Gemini TTS Candidate
    """
    plan_items = []
    
    for idx, sc in enumerate(TEST_SCENARIOS):
        sc_id = sc["id"]
        text = sc["text"]
        direction = sc["direction"]

        # 1. Edge-TTS Baseline settings
        base_settings = {
            "provider": "edge-tts",
            "model_version": "pre_phase1_baseline",
            "voice": BASELINE_VOICE,
            "rate": BASELINE_RATE,
            "pitch": BASELINE_PITCH,
            "volume": BASELINE_VOLUME,
            "style_prompt": None,
            "description": "Pre-Phase 1 static baseline (+5% rate, +0Hz pitch)"
        }

        # 2. Edge-TTS Phase 1 Candidate settings
        cand_dir, cand_rate, cand_pitch = resolve_voice_profile(
            action_type=sc["action_type"],
            voice_direction=direction,
            base_rate_str=BASELINE_RATE
        )
        phase1_settings = {
            "provider": "edge-tts",
            "model_version": "phase1_scene_profile",
            "voice": BASELINE_VOICE,
            "voice_direction": cand_dir,
            "rate": cand_rate,
            "pitch": cand_pitch,
            "volume": BASELINE_VOLUME,
            "style_prompt": None,
            "description": f"Phase 1 resolved profile ({cand_dir}: rate={cand_rate}, pitch={cand_pitch})"
        }

        # 3. Gemini TTS Candidate settings
        gemini_settings = {
            "provider": "gemini-tts",
            "model_version": DEFAULT_GEMINI_MODEL,
            "voice": DEFAULT_GEMINI_VOICE,
            "style_prompt": sc["gemini_style"],
            "description": "Gemini 3.8 Flash TTS with natural-language performance direction"
        }

        options_pool = [
            {"source_id": "baseline", "settings": base_settings},
            {"source_id": "phase1", "settings": phase1_settings},
            {"source_id": "gemini", "settings": gemini_settings}
        ]

        # Deterministic shuffle for this scene
        rng = random.Random(seed + idx * 101)
        shuffled = list(options_pool)
        rng.shuffle(shuffled)

        assigned_options = {}
        for letter, opt in zip(["A", "B", "C"], shuffled):
            assigned_options[letter] = {
                "letter": letter,
                "source_id": opt["source_id"],
                "filename": f"scene_{sc_id}_opt_{letter}.mp3",
                "settings": opt["settings"]
            }

        plan_items.append({
            "scene_id": sc_id,
            "scene_type": sc["scene_type"],
            "scenario": sc["scenario"],
            "direction": direction,
            "action_type": sc["action_type"],
            "text": text,
            "gemini_style": sc["gemini_style"],
            "options": assigned_options
        })

    return {
        "seed": seed,
        "model_candidate": DEFAULT_GEMINI_MODEL,
        "gemini_voice": DEFAULT_GEMINI_VOICE,
        "items": plan_items
    }


def create_scorecard_markdown(plan: dict) -> str:
    """Generate Markdown evaluation scorecard in Thai for blinded A/B/C human listening test."""
    lines = [
        "# แบบฟอร์มประเมินคุณภาพเสียงพากย์ 3 ระบบ (Phase 1.2 TTS Expressive Comparison Scorecard)",
        "",
        "> **คำแนะนำสำหรับผู้ฟังทดสอบ**:",
        "> 1. ในแต่ละฉาก จะมีตัวอย่างเสียง 3 ตัวเลือก ได้แก่ `opt_A`, `opt_B`, และ `opt_C` โดยมีการสุ่มปิดป้าย (Blinded)",
        "> 2. ให้เปิดฟังวนซ้ำอย่างน้อย 2 รอบต่อตัวเลือก โดยใช้หูฟังหรือลำโพงคุณภาพมาตรฐาน",
        "> 3. ให้คะแนน 1 ถึง 5 ดาว ในแต่ละหัวข้อการประเมินอย่างเป็นกลาง",
        "> 4. **ห้ามเปิดดูเฉลยใน `answer_key.json` ก่อนประเมินเสร็จสิ้น**",
        "",
        "### เกณฑ์การให้คะแนน (1 - 5 คะแนน):",
        "- **1 คะแนน (แย่มาก)**: เสียงแข็ง ทื่อ ผิดธรรมชาติอย่างชัดเจน มีเสียงเพี้ยน กระตุก หรือออกเสียงภาษาไทยผิด",
        "- **2 คะแนน (พอใช้)**: ฟังรู้เรื่องแต่โมโนโทน ขาดอารมณ์ ไม่เข้ากับบรรยากาศฉาก หรือมีเสียงสังเคราะห์ชัดเจน",
        "- **3 คะแนน (ปานกลาง)**: มาตรฐาน AI สังเคราะห์ทั่วไป ออกเสียงถูกต้อง สื่ออารมณ์ได้ระดับผิวเผิน",
        "- **4 คะแนน (ดี)**: มีน้ำเสียงและการเว้นวรรคเข้ากับฉาก น้ำเสียงดึงดูด น่าติดตาม ฟังเพลิน",
        "- **5 คะแนน (ยอดเยี่ยม)**: สื่ออารมณ์ฉากได้สมจริง จังหวะหายใจและน้ำเสียงดึงอารมณ์เทียบเท่านักพากย์สปอยล์มืออาชีพ",
        "",
        "---",
        ""
    ]

    for item in plan["items"]:
        s_id = item["scene_id"]
        scen_type = item["scene_type"]
        scen_name = item["scenario"]
        txt = item["text"]

        lines.append(f"## ฉากที่ {s_id}: {scen_type} — {scen_name}")
        lines.append(f"**บทบรรยาย (Spoken Text)**: *\"{txt}\"*")
        lines.append("")
        lines.append("| หัวข้อการประเมิน | ตัวเลือก A (`scene_{}_opt_A.mp3`) | ตัวเลือก B (`scene_{}_opt_B.mp3`) | ตัวเลือก C (`scene_{}_opt_C.mp3`) |".format(s_id, s_id, s_id))
        lines.append("| :--- | :---: | :---: | :---: |")
        lines.append("| 1. ความเป็นธรรมชาติภาษาไทย (Naturalness) | [ ] / 5 | [ ] / 5 | [ ] / 5 |")
        lines.append("| 2. การสื่ออารมณ์ตามฉาก (Emotional Expressiveness) | [ ] / 5 | [ ] / 5 | [ ] / 5 |")
        lines.append("| 3. จังหวะจะโคนและการเว้นวรรค (Pacing & Pauses) | [ ] / 5 | [ ] / 5 | [ ] / 5 |")
        lines.append("| 4. ความชัดเจนของเสียง (Clarity & Intelligibility) | [ ] / 5 | [ ] / 5 | [ ] / 5 |")
        lines.append("| 5. ความน่าติดตาม (Overall Engagement) | [ ] / 5 | [ ] / 5 | [ ] / 5 |")
        lines.append("| **คะแนนรวมเฉลี่ย** | **[ ] / 5** | **[ ] / 5** | **[ ] / 5** |")
        lines.append("")
        lines.append(f"**ตัวเลือกที่ผู้ฟังชอบและรู้สึกว่ามีคุณภาพการพากย์ดีที่สุดในฉากนี้**:")
        lines.append(f"- [ ] ตัวเลือก A")
        lines.append(f"- [ ] ตัวเลือก B")
        lines.append(f"- [ ] ตัวเลือก C")
        lines.append(f"- [ ] ไม่แตกต่างกันอย่างมีนัยสำคัญ")
        lines.append("")
        lines.append(f"**บันทึกความคิดเห็นเชิงคุณภาพ (Qualitative Notes)**:")
        lines.append(f"*(ระบุว่าตัวเลือกใดถ่ายทอดอารมณ์ได้ตรงบทจริง ไม่ใช่แค่เร่งความเร็วหรือเร่งเสียงดัง)*: _________________________________________________________________")
        lines.append("")
        lines.append("---",)
        lines.append("")

    lines.append("## สรุปภาพรวมการประเมิน (Overall Evaluation Summary)")
    lines.append("- ตัวเลือกที่ได้รับคะแนนความน่าประทับใจสูงสุดโดยรวม: _____________________")
    lines.append("- ข้อสังเกตด้านการออกเสียงภาษาไทยของระบบที่ให้การสื่ออารมณ์ดีที่สุด: _____________________")
    lines.append("- ข้อสรุปว่าควรนำระบบ candidate เข้าสู่กระบวนการทดสอบ Alignment สำหรับ Subtitle หรือไม่: [ ] ควร  |  [ ] ยังไม่พร้อม")
    return "\n".join(lines)


async def generate_comparison_pack(
    out_dir: Path,
    api_key: str,
    gemini_model: str = DEFAULT_GEMINI_MODEL,
    gemini_voice: str = DEFAULT_GEMINI_VOICE,
    seed: int = 42,
    dry_run: bool = False
) -> dict:
    """Generate audio files, metadata, answer key, and scorecard."""
    out_dir.mkdir(parents=True, exist_ok=True)
    plan = build_poc_comparison_plan(seed=seed)

    metadata_records = []
    answer_key = {
        "seed": seed,
        "model_evaluated": gemini_model,
        "gemini_voice": gemini_voice,
        "scenes": {}
    }

    print(f"[*] Initializing Expressive TTS PoC Generation into: {out_dir}")
    if dry_run:
        print("[!] Running in DRY-RUN mode. No external network calls will be made.")
    else:
        print(f"[*] Target Gemini Model: {gemini_model} | Voice: {gemini_voice}")
        if not api_key:
            print("[!] Warning: GEMINI_API_KEY is not set. Gemini synthesis will be skipped/mocked if called without key.")

    for item in plan["items"]:
        s_id = item["scene_id"]
        scen_type = item["scene_type"]
        text = item["text"]
        style = item["gemini_style"]

        print(f"\n--- Scene {s_id}: {scen_type} ---")
        print(f"  Transcript: \"{text}\"")

        scene_meta_samples = []
        scene_answer_options = {}

        for letter in ["A", "B", "C"]:
            opt = item["options"][letter]
            fname = opt["filename"]
            source_id = opt["source_id"]
            st = opt["settings"]
            out_file = out_dir / fname

            duration = 0.0
            status = "dry_run" if dry_run else "pending"
            error_msg = None

            print(f"  -> Option {letter} ({fname}): Provider={st['provider']} ({source_id})")

            if not dry_run:
                try:
                    if st["provider"] == "edge-tts":
                        await synthesize_edge_tts(
                            text=text,
                            voice=st["voice"],
                            rate=st["rate"],
                            pitch=st["pitch"],
                            volume=st["volume"],
                            output_path=out_file
                        )
                        duration = get_audio_duration(str(out_file))
                        status = "success"
                    elif st["provider"] == "gemini-tts":
                        if not api_key:
                            status = "skipped_missing_api_key"
                            error_msg = "GEMINI_API_KEY environment variable is missing"
                            print(f"     [!] Skipped Gemini TTS: {error_msg}")
                        else:
                            import time as _delay
                            # Respect Free Tier 3 RPM limit with inter-request delay
                            if hasattr(generate_comparison_pack, '_last_gemini_call'):
                                elapsed = _delay.time() - generate_comparison_pack._last_gemini_call
                                if elapsed < GEMINI_REQUEST_DELAY_SEC:
                                    wait = GEMINI_REQUEST_DELAY_SEC - elapsed
                                    print(f"     [Throttle] Waiting {wait:.0f}s to respect rate limit...")
                                    _delay.sleep(wait)
                            audio_bytes, mime, raw_m = synthesize_gemini_tts(
                                text=text,
                                style_description=style,
                                api_key=api_key,
                                model_name=gemini_model,
                                voice_name=gemini_voice
                            )
                            generate_comparison_pack._last_gemini_call = _delay.time()
                            convert_audio_to_mp3(audio_bytes, mime, out_file)
                            duration = get_audio_duration(str(out_file))
                            status = "success"
                except Exception as ex:
                    status = "failed"
                    error_msg = str(ex)
                    print(f"     [!] Synthesis failed for Option {letter}: {error_msg}")

            scene_meta_samples.append({
                "option": letter,
                "filename": fname,
                "status": status,
                "duration_seconds": duration,
                "error": error_msg
            })

            scene_answer_options[f"opt_{letter}"] = {
                "filename": fname,
                "provider": st["provider"],
                "source_id": source_id,
                "model_version": st.get("model_version"),
                "voice": st.get("voice"),
                "rate": st.get("rate"),
                "pitch": st.get("pitch"),
                "style_prompt": st.get("style_prompt"),
                "description": st.get("description"),
                "status": status,
                "duration_seconds": duration,
                "error": error_msg
            }

        metadata_records.append({
            "scene_id": s_id,
            "scene_type": scen_type,
            "scenario": item["scenario"],
            "direction": item["direction"],
            "text": text,
            "gemini_style": style,
            "samples": scene_meta_samples
        })

        answer_key["scenes"][f"scene_{s_id}"] = {
            "scene_type": scen_type,
            "scenario": item["scenario"],
            "direction": item["direction"],
            "text": text,
            "options": scene_answer_options
        }

    # 1. Save metadata.json (public listening pack info)
    meta_path = out_dir / "metadata.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata_records, f, ensure_ascii=False, indent=2)

    # 2. Save answer_key.json (unblinded provider mapping)
    key_path = out_dir / "answer_key.json"
    with open(key_path, "w", encoding="utf-8") as f:
        json.dump(answer_key, f, ensure_ascii=False, indent=2)

    # 3. Save scorecard.md (Thai evaluation form)
    scorecard_path = out_dir / "scorecard.md"
    scorecard_text = create_scorecard_markdown(plan)
    with open(scorecard_path, "w", encoding="utf-8") as f:
        f.write(scorecard_text)

    # 4. Save README.md (instructions)
    readme_path = out_dir / "README.md"
    readme_text = f"""# Thai Expressive TTS Proof of Concept (Phase 1.2)
Anime Recap Pipeline — สปอย

ชุดทดสอบเปรียบเทียบคุณภาพเสียงพากย์อนิเมะภาษาไทยแบบ 3 ตัวเลือก (Blinded A/B/C):
- **ระบบที่ 1**: Edge-TTS Baseline (การตั้งค่าคงที่ดั้งเดิม: +5% rate, +0Hz pitch)
- **ระบบที่ 2**: Edge-TTS Phase 1 (ปรับ rate และ pitch ตามอารมณ์ฉาก)
- **ระบบที่ 3**: Google Gemini TTS Candidate (`{gemini_model}` Voice: `{gemini_voice}`) ด้วย Natural-Language Style Prompting

## ขั้นตอนการฟังและประเมินผล
1. เปิดไฟล์แบบฟอร์มประเมินใน [scorecard.md](file:///{scorecard_path.as_posix()})
2. เปิดฟังไฟล์เสียงในโฟลเดอร์นี้ทีละฉาก (`scene_XX_opt_A.mp3`, `scene_XX_opt_B.mp3`, `scene_XX_opt_C.mp3`)
3. บันทึกคะแนนและข้อสังเกตลงใน `scorecard.md`
4. เมื่อประเมินครบทุกฉากแล้ว จึงเปิดดูเฉลยใน [answer_key.json](file:///{key_path.as_posix()}) เพื่อตรวจสอบว่าแต่ละตัวเลือกคือระบบใด
"""
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme_text)

    print(f"\n[+] PoC files successfully generated in: {out_dir}")
    print(f"    - Scorecard : {scorecard_path.name}")
    print(f"    - Metadata  : {meta_path.name}")
    print(f"    - Answer Key: {key_path.name}")
    print(f"    - README    : {readme_path.name}")

    return {
        "out_dir": str(out_dir),
        "plan": plan,
        "metadata_path": str(meta_path),
        "answer_key_path": str(key_path),
        "scorecard_path": str(scorecard_path)
    }


def main():
    parser = argparse.ArgumentParser(description="Expressive TTS Provider Proof of Concept (Phase 1.2)")
    parser.add_argument(
        "--out-dir",
        type=str,
        default=str(BASE_DIR / "projects" / "_temp" / "gemini_tts_poc"),
        help="Target output directory for PoC samples and scorecard"
    )
    parser.add_argument(
        "--key",
        type=str,
        default=os.environ.get("GEMINI_API_KEY", ""),
        help="Google Gemini API key (defaults to GEMINI_API_KEY env var)"
    )
    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_GEMINI_MODEL,
        help=f"Gemini model ID to evaluate (default: {DEFAULT_GEMINI_MODEL})"
    )
    parser.add_argument(
        "--voice",
        type=str,
        default=DEFAULT_GEMINI_VOICE,
        help=f"Prebuilt voice name (default: {DEFAULT_GEMINI_VOICE})"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for deterministic option blinding"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run mode: generates metadata, scorecard, and answer key without synthesis"
    )

    args = parser.parse_args()
    out_dir = Path(args.out_dir)

    asyncio.run(
        generate_comparison_pack(
            out_dir=out_dir,
            api_key=args.key,
            gemini_model=args.model,
            gemini_voice=args.voice,
            seed=args.seed,
            dry_run=args.dry_run
        )
    )


if __name__ == "__main__":
    main()
