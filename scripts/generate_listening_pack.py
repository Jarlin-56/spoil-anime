#!/usr/bin/env python3
"""
Listening Pack Generator for Thai Narration Quality (Phase 1.1)
Anime Recap Pipeline — สปอย

Generates reproducible, blinded audio samples for human evaluation of Phase 1 voice profiles.
Compares pre-Phase 1 static baseline (+5% rate, +0Hz pitch) against Phase 1 candidate profiles.

Usage:
    python scripts/generate_listening_pack.py [--out-dir DIR] [--dry-run]
"""

import argparse
import asyncio
import json
import os
import random
import sys
from pathlib import Path

# Add core to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
CORE_DIR = BASE_DIR / "core"
sys.path.insert(0, str(CORE_DIR))

import edge_tts
from config import (
    DEFAULT_VOICE,
    resolve_voice_profile,
    VOICE_PROFILES
)
from timing_engine import split_narration_sentences
from tts_engine import get_audio_duration

# Pre-Phase 1 static delivery baseline verified from git history and configuration:
# Every line was synthesized at +5% rate and +0Hz pitch regardless of scene emotion.
BASELINE_VOICE = DEFAULT_VOICE  # th-TH-NiwatNeural
BASELINE_RATE = "+5%"
BASELINE_PITCH = "+0Hz"
BASELINE_VOLUME = "+0%"

# Carefully crafted Thai anime recap test scenarios (8-16 words, unambiguous emotion)
TEST_SCENARIOS = [
    {
        "id": "ref",
        "direction": "conversational",
        "scenario": "Neutral Narrative / Story Exposition",
        "text": "หลังจากเดินทางข้ามทวีปมาหลายสัปดาห์ ในที่สุดพวกเขาก็มาถึงเมืองหลวงอันเงียบสงบ",
        "action_type": "normal",
        "is_reference": True
    },
    {
        "id": "01",
        "direction": "energetic",
        "scenario": "Battle / Combat Climax",
        "text": "ทันใดนั้นเขาก็ชักดาบเพลิงออกมา แล้วพุ่งฝ่าวงล้อมของศัตรูเข้าไปอย่างดุดัน!",
        "action_type": "battle",
        "is_reference": False
    },
    {
        "id": "02",
        "direction": "suspenseful",
        "scenario": "Mystery / Stealth Investigation",
        "text": "รอยเท้าปริศนาที่พื้นค่อยๆ เลือนหายไปในเงามืด... ราวกับมีบางอย่างกำลังจับตาดูอยู่",
        "action_type": "normal",
        "is_reference": False
    },
    {
        "id": "03",
        "direction": "surprised",
        "scenario": "Shock / Sudden Plot Twist",
        "text": "แต่เมื่อหน้ากากนั้นหลุดออก ทุกคนต่างตกตะลึง เพราะใบหน้านั้นคือเพื่อนสนิทที่หายตัวไป!",
        "action_type": "shock",
        "is_reference": False
    },
    {
        "id": "04",
        "direction": "dramatic",
        "scenario": "Emotional Loss / Tragic Sacrifice",
        "text": "แม้จะต้องสูญเสียความทรงจำทั้งหมดไป แต่เขาก็ยังเลือกที่จะปกป้องทุกคนไว้จนวินาทีสุดท้าย",
        "action_type": "drama",
        "is_reference": False
    },
    {
        "id": "05",
        "direction": "dry_humor",
        "scenario": "Deadpan Wit / Comical Anticlimax",
        "text": "เขาอุตส่าห์เตรียมแผนการรบมาอย่างดิบดี แต่ศัตรูดันลื่นล้มหัวฟาดพื้นสลบไปเองซะงั้น",
        "action_type": "normal",
        "is_reference": False
    }
]

async def synthesize_sample(text: str, voice: str, rate: str, pitch: str, volume: str, output_path: Path):
    """Synthesize speech file using Edge-TTS with clause splitting matching production."""
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


def build_evaluation_pack_plan(seed: int = 42) -> dict:
    """Build reproducible pairing plan with deterministic blind assignment."""
    rng = random.Random(seed)
    items = []
    
    for item in TEST_SCENARIOS:
        text = item["text"]
        direction = item["direction"]
        
        # Resolve Phase 1 candidate settings
        cand_dir, cand_rate, cand_pitch = resolve_voice_profile(
            action_type=item["action_type"],
            voice_direction=direction,
            base_rate_str=BASELINE_RATE
        )
        cand_settings = {
            "profile_type": "phase1_candidate",
            "voice_direction": cand_dir,
            "voice": BASELINE_VOICE,
            "rate": cand_rate,
            "pitch": cand_pitch,
            "volume": BASELINE_VOLUME
        }
        
        base_settings = {
            "profile_type": "pre_phase1_baseline",
            "voice_direction": "conversational",
            "voice": BASELINE_VOICE,
            "rate": BASELINE_RATE,
            "pitch": BASELINE_PITCH,
            "volume": BASELINE_VOLUME
        }
        
        if item.get("is_reference"):
            items.append({
                "type": "reference",
                "id": "ref",
                "direction": direction,
                "scenario": item["scenario"],
                "text": text,
                "sample_filename": "sample_ref_conversational.mp3",
                "settings": cand_settings
            })
        else:
            # Deterministic A/B coin flip
            candidate_is_A = rng.choice([True, False])
            pair_id = item["id"]
            
            sample_A_settings = cand_settings if candidate_is_A else base_settings
            sample_B_settings = base_settings if candidate_is_A else cand_settings
            
            items.append({
                "type": "pair",
                "id": pair_id,
                "direction": direction,
                "scenario": item["scenario"],
                "text": text,
                "sample_A": {
                    "filename": f"sample_{pair_id}_A.mp3",
                    "settings": sample_A_settings,
                    "is_candidate": candidate_is_A
                },
                "sample_B": {
                    "filename": f"sample_{pair_id}_B.mp3",
                    "settings": sample_B_settings,
                    "is_candidate": not candidate_is_A
                }
            })
            
    return {"seed": seed, "items": items}


def create_scorecard_markdown(plan: dict) -> str:
    """Generate Markdown scorecard for human evaluator."""
    lines = [
        "# แบบฟอร์มประเมินคุณภาพเสียงพากย์อนิเมะ (Phase 1.1 Acoustic Validation Scorecard)",
        "",
        "> คำแนะนำ: ให้ฟังเปรียบเทียบทีละคู่ (ตัวอย่าง A และ ตัวอย่าง B) โดยเปิดฟังหลายๆ รอบ",
        "> ให้คะแนน 1 ถึง 5 ดาว ตามเกณฑ์ต่อไปนี้ก่อนเปิดดูเฉลยใน `answer_key.json`",
        "",
        "### เกณฑ์การให้คะแนน (1 - 5 คะแนน):",
        "- **1 คะแนน (แย่มาก)**: เสียงแข็ง ทื่อ ผิดธรรมชาติ หรือมีเสียงกระตุก/แหลมแปลกปลอม",
        "- **2 คะแนน (พอใช้)**: ฟังรู้เรื่องแต่เสียงโมโนโทน ขาดอารมณ์ หรือพูดเร็ว/ช้าเกินไป",
        "- **3 คะแนน (ปานกลาง)**: เป็นธรรมชาติในระดับมาตรฐาน AI ทั่วไป อารมณ์พอเข้ากับฉาก",
        "- **4 คะแนน (ดี)**: มีจังหวะจะโคน น้ำเสียงสอดคล้องกับอารมณ์ฉาก ฟังเพลิน",
        "- **5 คะแนน (ยอดเยี่ยม)**: สื่ออารมณ์ฉากได้เด่นชัด จังหวะหายใจเป็นธรรมชาติ เหมือนนักพากย์สปอยล์จริง",
        "",
        "---",
        "",
        "## 0. ตัวอย่างอ้างอิงระดับเสียงมาตรฐาน (Neutral Reference)",
        "- **ไฟล์**: `sample_ref_conversational.mp3`",
        "- **ฉาก**: เล่าเรื่องทั่วไป (Conversational)",
        "- **บทพูด**: *\"หลังจากเดินทางข้ามทวีปมาหลายสัปดาห์ ในที่สุดพวกเขาก็มาถึงเมืองหลวงอันเงียบสงบ\"*",
        "- *หมายเหตุ: ใช้ฟังเพื่อตั้งระดับเสียงและความคุ้นเคยก่อนเริ่มประเมินคู่เปรียบเทียบ*",
        "",
        "---",
        ""
    ]
    
    pair_count = 1
    for itm in plan["items"]:
        if itm["type"] == "reference":
            continue
        p_id = itm["id"]
        dir_name = itm["direction"]
        scen = itm["scenario"]
        txt = itm["text"]
        
        lines.append(f"## คู่ที่ {pair_count}: ฉาก {scen} (`{dir_name}`)")
        lines.append(f"**บทบรรยาย**: *\"{txt}\"*")
        lines.append("")
        lines.append("| หัวข้อการประเมิน | ตัวอย่าง A (`sample_{}_A.mp3`) | ตัวอย่าง B (`sample_{}_B.mp3`) |".format(p_id, p_id))
        lines.append("| :--- | :---: | :---: |")
        lines.append("| 1. ความเป็นธรรมชาติ (Naturalness) | [ ] / 5 | [ ] / 5 |")
        lines.append("| 2. การสื่ออารมณ์ตามฉาก (Emotional Delivery) | [ ] / 5 | [ ] / 5 |")
        lines.append("| 3. จังหวะและการเว้นวรรค (Pacing & Cadence) | [ ] / 5 | [ ] / 5 |")
        lines.append("| 4. ความชัดเจนของเสียงพูด (Clarity & Articulation) | [ ] / 5 | [ ] / 5 |")
        lines.append("| 5. ความน่าติดตาม (Desire to Keep Listening) | [ ] / 5 | [ ] / 5 |")
        lines.append("| **คะแนนรวมเฉลี่ย** | **[ ] / 5** | **[ ] / 5** |")
        lines.append("")
        lines.append(f"**ตัวอย่างที่ชอบมากกว่าในคู่นี้**: [ ] ตัวอย่าง A  |  [ ] ตัวอย่าง B  |  [ ] ไม่แตกต่าง")
        lines.append(f"**บันทึกข้อสังเกตของผู้ฟัง**: __________________________________________________")
        lines.append("")
        lines.append("---")
        lines.append("")
        pair_count += 1
        
    lines.append("## สรุปผลการประเมินภาพรวม")
    lines.append("- จำนวนคู่ที่ผู้ฟังชอบ Candidate (Phase 1) มากกว่า: ____ / 5 คู่")
    lines.append("- จำนวนคู่ที่ผู้ฟังชอบ Baseline มากกว่า: ____ / 5 คู่")
    lines.append("- ข้อเสนอแนะสำหรับการปรับแต่ง Phase 2: ___________________________________")
    return "\n".join(lines)


async def main_async(out_dir: Path, dry_run: bool = False):
    out_dir.mkdir(parents=True, exist_ok=True)
    plan = build_evaluation_pack_plan(seed=42)
    
    metadata_records = []
    answer_key = {}
    
    print(f"[*] Generating Thai Narration Listening Pack into: {out_dir}")
    if dry_run:
        print("[!] Running in DRY-RUN mode. No network calls or audio files will be created.")
        
    for itm in plan["items"]:
        if itm["type"] == "reference":
            f_name = itm["sample_filename"]
            out_file = out_dir / f_name
            st = itm["settings"]
            print(f"  -> Reference: {f_name} (Voice: {st['voice']}, Rate: {st['rate']}, Pitch: {st['pitch']})")
            
            dur = 0.0
            if not dry_run:
                await synthesize_sample(itm["text"], st["voice"], st["rate"], st["pitch"], st["volume"], out_file)
                dur = get_audio_duration(str(out_file))
                
            metadata_records.append({
                "sample_id": "ref",
                "filename": f_name,
                "role": "reference",
                "scenario": itm["scenario"],
                "text": itm["text"],
                "voice": st["voice"],
                "rate": st["rate"],
                "pitch": st["pitch"],
                "volume": st["volume"],
                "duration_seconds": dur
            })
            answer_key["ref"] = {
                "filename": f_name,
                "profile_type": st["profile_type"],
                "voice_direction": st["voice_direction"],
                "rate": st["rate"],
                "pitch": st["pitch"]
            }
        else:
            p_id = itm["id"]
            sA = itm["sample_A"]
            sB = itm["sample_B"]
            
            fA = out_dir / sA["filename"]
            fB = out_dir / sB["filename"]
            
            print(f"  -> Pair {p_id} ({itm['direction']}): {sA['filename']} vs {sB['filename']}")
            
            durA = 0.0
            durB = 0.0
            if not dry_run:
                stA = sA["settings"]
                stB = sB["settings"]
                await synthesize_sample(itm["text"], stA["voice"], stA["rate"], stA["pitch"], stA["volume"], fA)
                durA = get_audio_duration(str(fA))
                
                await synthesize_sample(itm["text"], stB["voice"], stB["rate"], stB["pitch"], stB["volume"], fB)
                durB = get_audio_duration(str(fB))
                
            metadata_records.append({
                "pair_id": p_id,
                "scenario": itm["scenario"],
                "direction": itm["direction"],
                "text": itm["text"],
                "samples": [
                    {"filename": sA["filename"], "duration_seconds": durA},
                    {"filename": sB["filename"], "duration_seconds": durB}
                ]
            })
            
            answer_key[f"pair_{p_id}"] = {
                "scenario": itm["scenario"],
                "target_direction": itm["direction"],
                "sample_A": {
                    "filename": sA["filename"],
                    "is_candidate": sA["is_candidate"],
                    "profile_type": sA["settings"]["profile_type"],
                    "rate": sA["settings"]["rate"],
                    "pitch": sA["settings"]["pitch"]
                },
                "sample_B": {
                    "filename": sB["filename"],
                    "is_candidate": sB["is_candidate"],
                    "profile_type": sB["settings"]["profile_type"],
                    "rate": sB["settings"]["rate"],
                    "pitch": sB["settings"]["pitch"]
                }
            }

    # Write public metadata
    with open(out_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata_records, f, ensure_ascii=False, indent=2)
        
    # Write secret answer key
    with open(out_dir / "answer_key.json", "w", encoding="utf-8") as f:
        json.dump(answer_key, f, ensure_ascii=False, indent=2)
        
    # Write scorecard
    scorecard_content = create_scorecard_markdown(plan)
    with open(out_dir / "scorecard.md", "w", encoding="utf-8") as f:
        f.write(scorecard_content)
        
    # Write README
    readme_text = f"""# Thai Narration Acoustic Evaluation Pack (Phase 1.1)

Generated into: `{out_dir.resolve()}`

## Files in this Directory:
1. `sample_ref_conversational.mp3`: Neutral reference audio.
2. `sample_01_A.mp3`, `sample_01_B.mp3`: Blinded Pair 1 (Energetic Battle).
3. `sample_02_A.mp3`, `sample_02_B.mp3`: Blinded Pair 2 (Suspenseful Investigation).
4. `sample_03_A.mp3`, `sample_03_B.mp3`: Blinded Pair 3 (Surprised Reveal).
5. `sample_04_A.mp3`, `sample_04_B.mp3`: Blinded Pair 4 (Dramatic Event).
6. `sample_05_A.mp3`, `sample_05_B.mp3`: Blinded Pair 5 (Dry Humor).
7. `scorecard.md`: Scoring form for evaluator.
8. `metadata.json`: Scenario description and measured durations.
9. `answer_key.json`: Secret answer key revealing which sample is Baseline vs Phase 1 Candidate.

## How to Conduct Human Listening:
1. Open `scorecard.md`.
2. Listen to `sample_ref_conversational.mp3` first to establish a listening baseline.
3. For each pair (01 to 05), listen to Sample A and Sample B back-to-back.
4. Record your ratings (1–5) and indicate which sample you prefer.
5. After completing all ratings, open `answer_key.json` to reveal which letter was Phase 1 Candidate and which was Pre-Phase 1 Baseline.
"""
    with open(out_dir / "README.md", "w", encoding="utf-8") as f:
        f.write(readme_text)

    print(f"\n[+] Listening pack generated successfully at: {out_dir.resolve()}")
    print(f"    - Reference sample: 1")
    print(f"    - Blinded A/B pairs: 5 (10 samples)")
    print(f"    - Scorecard: {out_dir / 'scorecard.md'}")
    print(f"    - Metadata: {out_dir / 'metadata.json'}")
    print(f"    - Answer Key: {out_dir / 'answer_key.json'}")


def main():
    parser = argparse.ArgumentParser(description="Generate Thai Narration Listening Pack (Phase 1.1)")
    parser.add_argument(
        "--out-dir",
        default=str(BASE_DIR / "projects" / "_temp" / "listening_pack_phase1"),
        help="Target output directory for evaluation files (default: projects/_temp/listening_pack_phase1)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate metadata, scorecard, and answer key without calling TTS API"
    )
    args = parser.parse_args()
    asyncio.run(main_async(Path(args.out_dir), dry_run=args.dry_run))


if __name__ == "__main__":
    main()
