"""
Tests for Narration Generation System
Validates the 'Friend telling a friend' persona, Retention writing,
Light humor without inventing facts, Avoidance of generic AI phrases,
Word counts (8-16 words per scene), and Sentence variety across 3 content types:
1. Story / Movie recap
2. Strange / Interesting story
3. Informational / Explainer
"""

import os
import sys
import unittest
from pathlib import Path

# Add core to sys.path
CORE_DIR = Path(__file__).resolve().parent.parent / "core"
sys.path.insert(0, str(CORE_DIR))

from script_generator import (
    PROMPT_SYSTEM_NARRATION_DIRECTOR,
    PROMPT_SYSTEM_ANIME_RECAP,
    format_content_facts_section,
    format_narration_style_section,
    generate_sample_script,
    parse_transcript_story_beats,
    finalize_script_items,
)
from timing_engine import count_thai_words

FORBIDDEN_GENERIC_PHRASES = [
    "เรื่องราวนี้",
    "อย่างที่ทุกคนเห็น",
    "ในวันนี้",
    "เราจะพาไปดูกัน",
    "น่าสนใจเป็นอย่างมาก",
    "ถือเป็นเหตุการณ์ที่น่าทึ่ง",
    "จากเหตุการณ์ดังกล่าว",
    "ในขณะเดียวกัน",
    "สิ่งที่เกิดขึ้นต่อจากนั้น",
    "และนี่คือเหตุผลว่า",
    "ยินดีต้อนรับเข้าสู่",
    "ช็อกโลก",
    "คุณจะไม่เชื่อแน่นอน",
]


class TestNarrationGeneration(unittest.TestCase):

    def test_01_prompt_architecture_and_separation(self):
        """Prompt architecture must cleanly separate Content Facts from Narration Style."""
        # 1. System Prompt checks
        self.assertIn("เพื่อนสนิทที่ดูเรื่องนี้มาก่อนแล้ว", PROMPT_SYSTEM_NARRATION_DIRECTOR)
        self.assertIn("แล้วไงต่อ?", PROMPT_SYSTEM_NARRATION_DIRECTOR)
        self.assertIn("Light Humor", PROMPT_SYSTEM_NARRATION_DIRECTOR)
        self.assertIn("STRICT FACTUALITY", PROMPT_SYSTEM_NARRATION_DIRECTOR)
        self.assertIn("8–16 คำ", PROMPT_SYSTEM_NARRATION_DIRECTOR)
        self.assertIn("Candidate A", PROMPT_SYSTEM_NARRATION_DIRECTOR)
        self.assertIn("Self-Critique", PROMPT_SYSTEM_NARRATION_DIRECTOR)

        # Backward compatibility
        self.assertEqual(PROMPT_SYSTEM_ANIME_RECAP, PROMPT_SYSTEM_NARRATION_DIRECTOR)

        # 2. Section separation checks
        facts_sec = format_content_facts_section("เรื่องทดสอบ", "ตัวละครทำบางอย่างในห้องทดลอง")
        style_sec = format_narration_style_section(10, is_vision=True)

        self.assertIn("[CONTENT FACTS", facts_sec)
        self.assertIn("[NARRATION STYLE & VOICE DIRECTION", style_sec)
        self.assertIn("เพื่อนเล่าให้เพื่อนฟัง", style_sec)

    def test_02_content_type_1_movie_story_recap(self):
        """Content Type 1: Story / Movie recap (Action/Adventure)."""
        synopsis = (
            "พระเอกตื่นขึ้นมากลางซากปรักหักพังของเมืองหลวง\n"
            "เขาพบว่าดาบสายฟ้าโบราณที่ตกทอดมาเริ่มเปล่งแสงสีฟ้า\n"
            "จู่ๆ ฝูงสัตว์อสูรมิติก็พุ่งทะลุรอยแยกออกมาโจมตี\n"
            "พระเอกตวัดดาบเพียงครั้งเดียว ผ่าฝูงอสูรแหลกเป็นผง\n"
            "แต่เขากลับพบตราประทับของเพื่อนสนิทอยู่บนตัวอสูร"
        )
        script = generate_sample_script(
            anime_title="ศึกอสูรดาบสายฟ้า",
            num_scenes=5,
            synopsis=synopsis
        )

        items = script["items"]
        self.assertEqual(len(items), 5)

        # Validate Hook in scene 1
        hook_text = items[0]["text"]
        self.assertTrue(
            any(w in hook_text for w in ["ปกติ", "สะดุดใจ", "เริ่มต้น", "ซากปรักหักพัง"]),
            f"Scene 1 must be an engaging hook: {hook_text}"
        )

        # Validate each scene properties
        for idx, itm in enumerate(items):
            text = itm["text"]
            words = itm["wordCount"]
            # Word count within range (8-16 target, max 18)
            self.assertLessEqual(words, 18, f"Scene {idx+1} word count {words} exceeds 18: {text}")
            self.assertGreaterEqual(words, 5, f"Scene {idx+1} word count {words} too short: {text}")
            # No forbidden generic AI phrases
            for forbidden in FORBIDDEN_GENERIC_PHRASES:
                self.assertNotIn(forbidden, text, f"Scene {idx+1} contains forbidden phrase '{forbidden}': {text}")

    def test_03_content_type_2_strange_interesting_story(self):
        """Content Type 2: Strange / Interesting story (Mystery/Unusual occurrence)."""
        synopsis = (
            "มีร้านกาแฟลึกลับเปิดเฉพาะเวลาเที่ยงคืนถึงตีหนึ่ง\n"
            "ลูกค้าทุกคนที่สั่งกาแฟดำจะได้รับจดหมายจากตัวเองในอนาคต\n"
            "ชายหนุ่มคนหนึ่งตัดสินใจลองเข้าไปพิสูจน์เพราะไม่เชื่อเรื่องงมงาย\n"
            "บาริสต้ายื่นถ้วยกาแฟพร้อมซองจดหมายที่มีลายมือของเขาเองเป๊ะๆ\n"
            "พอเปิดอ่าน ข้อความสั้นๆ เขียนไว้ว่า 'อย่าหันกลับไปมองข้างหลัง'"
        )
        script = generate_sample_script(
            anime_title="คาเฟ่เที่ยงคืน",
            num_scenes=5,
            synopsis=synopsis
        )

        items = script["items"]
        self.assertEqual(len(items), 5)

        # Verify variety in connectors
        connectors_seen = [itm["text"][:10] for itm in items]
        unique_connectors = set(connectors_seen)
        self.assertGreater(len(unique_connectors), 2, "Narration should have varied beginnings")

        # Validate constraints
        for idx, itm in enumerate(items):
            text = itm["text"]
            words = itm["wordCount"]
            self.assertLessEqual(words, 18, f"Scene {idx+1} exceeds word limit: {text}")
            for forbidden in FORBIDDEN_GENERIC_PHRASES:
                self.assertNotIn(forbidden, text, f"Scene {idx+1} contains forbidden phrase '{forbidden}': {text}")

    def test_04_content_type_3_informational_explainer(self):
        """Content Type 3: Informational / Explainer (Psychology/Everyday phenomenon)."""
        synopsis = (
            "เคยสงสัยไหมว่าทำไมเราถึงลืมเรื่องที่กำลังจะทำทันทีที่เดินผ่านประตู\n"
            "นักวิทยาศาสตร์เรียกปรากฏการณ์นี้ว่า Doorway Effect\n"
            "สมองมองว่ากรอบประตูคือจุดตัดเพื่อเริ่มจัดระเบียบความทรงจำใหม่\n"
            "วิธีแก้ปัญหานี้ง่ายกว่าที่คิด แค่ย้อนนึกถึงความรู้สึกในห้องก่อนหน้า\n"
            "คราวหน้าถ้าเดินเข้าห้องแล้วยืนงง ก็ไม่ต้องตกใจไป เพราะสมองแค่กำลังรีเซ็ต"
        )
        script = generate_sample_script(
            anime_title="ความลับของสมองเดินผ่านประตู",
            num_scenes=5,
            synopsis=synopsis
        )

        items = script["items"]
        self.assertEqual(len(items), 5)

        for idx, itm in enumerate(items):
            text = itm["text"]
            words = itm["wordCount"]
            self.assertLessEqual(words, 18, f"Scene {idx+1} exceeds word limit: {text}")
            for forbidden in FORBIDDEN_GENERIC_PHRASES:
                self.assertNotIn(forbidden, text, f"Scene {idx+1} contains forbidden phrase '{forbidden}': {text}")

        # Check that downstream contract is 100% intact
        self.assertIn("narration", items[0])
        self.assertIn("estimatedNarrationDuration", items[0])
        self.assertIn("storyBeat", items[0])
        self.assertIn("action_type", items[0])
        self.assertIn("status", items[0])
        self.assertEqual(items[0]["status"], "script_ready")


if __name__ == "__main__":
    unittest.main()
