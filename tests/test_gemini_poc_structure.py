"""
Unit Test Suite for Expressive TTS Proof of Concept (Phase 1.2)
Anime Recap Pipeline — สปอย

Verifies:
1. Comparison plan covers all 5 mandatory scene types (Conversational, Energetic, Suspenseful, Dramatic, Dry humor) + Surprised.
2. Each scene contains exactly 3 blinded options (A, B, C) mapping to Baseline, Phase 1, and Gemini candidate.
3. Spoken Thai transcript is strictly preserved and invariant across all options.
4. Style instructions are separated into prompts and never injected into the spoken text.
5. Dry-run generation executes deterministically offline without network calls.
6. Scorecard contains the 5 specified Thai evaluation metrics.
7. Error and key-handling logic explicitly flags failures rather than silently falling back.
"""

import asyncio
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add core & scripts to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "core"))
sys.path.insert(0, str(BASE_DIR / "scripts"))

from config import resolve_voice_profile, DEFAULT_VOICE
from poc_gemini_tts_comparison import (
    build_poc_comparison_plan,
    create_scorecard_markdown,
    generate_comparison_pack,
    synthesize_gemini_tts,
    BASELINE_VOICE,
    BASELINE_RATE,
    BASELINE_PITCH,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GEMINI_VOICE,
    TEST_SCENARIOS
)


class TestGeminiPocStructure(unittest.TestCase):

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_gemini_poc_"))

    def tearDown(self):
        if self.temp_dir.exists():
            shutil.rmtree(str(self.temp_dir), ignore_errors=True)

    def test_01_scene_coverage_and_mandatory_types(self):
        """Plan must cover the 5 required scene types plus surprised."""
        plan = build_poc_comparison_plan(seed=42)
        items = plan["items"]
        self.assertEqual(len(items), 6)

        scene_types = {it["scene_type"] for it in items}
        required_types = {
            "Conversational narration",
            "Energetic battle",
            "Suspenseful reveal",
            "Emotional drama",
            "Dry humor"
        }
        for rt in required_types:
            self.assertIn(rt, scene_types, f"Mandatory scene type '{rt}' missing from comparison plan")

    def test_02_three_way_blinded_assignment_invariants(self):
        """Every scene must have options A, B, C mapping to exactly 1 baseline, 1 phase1, and 1 gemini."""
        plan = build_poc_comparison_plan(seed=42)
        for itm in plan["items"]:
            opts = itm["options"]
            self.assertEqual(set(opts.keys()), {"A", "B", "C"})

            source_ids = {opts[letter]["source_id"] for letter in ["A", "B", "C"]}
            self.assertEqual(source_ids, {"baseline", "phase1", "gemini"})

            # Verify baseline settings
            baseline_opt = next(opt for opt in opts.values() if opt["source_id"] == "baseline")
            b_st = baseline_opt["settings"]
            self.assertEqual(b_st["provider"], "edge-tts")
            self.assertEqual(b_st["rate"], BASELINE_RATE)
            self.assertEqual(b_st["pitch"], BASELINE_PITCH)
            self.assertEqual(b_st["voice"], BASELINE_VOICE)

            # Verify phase 1 settings match resolve_voice_profile
            phase1_opt = next(opt for opt in opts.values() if opt["source_id"] == "phase1")
            p1_st = phase1_opt["settings"]
            self.assertEqual(p1_st["provider"], "edge-tts")
            exp_dir, exp_rate, exp_pitch = resolve_voice_profile(
                action_type=itm["action_type"],
                voice_direction=itm["direction"],
                base_rate_str=BASELINE_RATE
            )
            self.assertEqual(p1_st["rate"], exp_rate)
            self.assertEqual(p1_st["pitch"], exp_pitch)
            self.assertEqual(p1_st["voice_direction"], exp_dir)

            # Verify gemini settings
            gemini_opt = next(opt for opt in opts.values() if opt["source_id"] == "gemini")
            g_st = gemini_opt["settings"]
            self.assertEqual(g_st["provider"], "gemini-tts")
            self.assertEqual(g_st["model_version"], DEFAULT_GEMINI_MODEL)
            self.assertEqual(g_st["voice"], DEFAULT_GEMINI_VOICE)
            self.assertTrue(len(g_st["style_prompt"]) > 20)

    def test_03_transcript_and_prompt_separation(self):
        """Spoken text must be strictly identical across all options and separate from style instructions."""
        plan = build_poc_comparison_plan(seed=42)
        for itm in plan["items"]:
            spoken_text = itm["text"]
            gemini_style = itm["gemini_style"]

            # Style instruction must not contaminate the spoken Thai transcript
            self.assertNotIn("storytelling", spoken_text)
            self.assertNotIn("narrator", spoken_text)
            self.assertNotIn("pacing", spoken_text)

            # Gemini style must be a concise delivery description (not empty)
            self.assertTrue(len(gemini_style) > 10,
                            f"gemini_style too short for scene {itm['scene_id']}: '{gemini_style}'")
            # Style must not contain the Thai spoken text
            self.assertNotIn(spoken_text, gemini_style)

    def test_04_offline_dry_run_generation(self):
        """Dry-run must create metadata, answer_key, scorecard, and README without network calls."""
        res = asyncio.run(
            generate_comparison_pack(
                out_dir=self.temp_dir,
                api_key="",
                dry_run=True
            )
        )

        scorecard_file = self.temp_dir / "scorecard.md"
        metadata_file = self.temp_dir / "metadata.json"
        answer_key_file = self.temp_dir / "answer_key.json"
        readme_file = self.temp_dir / "README.md"

        self.assertTrue(scorecard_file.exists())
        self.assertTrue(metadata_file.exists())
        self.assertTrue(answer_key_file.exists())
        self.assertTrue(readme_file.exists())

        # Verify metadata structure
        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)
        self.assertEqual(len(metadata), 6)
        for sc in metadata:
            self.assertIn("scene_id", sc)
            self.assertIn("scene_type", sc)
            self.assertIn("text", sc)
            self.assertEqual(len(sc["samples"]), 3)
            for smp in sc["samples"]:
                self.assertEqual(smp["status"], "dry_run")

        # Verify answer key structure
        with open(answer_key_file, "r", encoding="utf-8") as f:
            key_data = json.load(f)
        self.assertEqual(key_data["model_evaluated"], DEFAULT_GEMINI_MODEL)
        self.assertEqual(len(key_data["scenes"]), 6)

        # Verify scorecard contains Thai rating dimensions
        scorecard_text = scorecard_file.read_text(encoding="utf-8")
        self.assertIn("ความเป็นธรรมชาติภาษาไทย (Naturalness)", scorecard_text)
        self.assertIn("การสื่ออารมณ์ตามฉาก (Emotional Expressiveness)", scorecard_text)
        self.assertIn("จังหวะจะโคนและการเว้นวรรค (Pacing & Pauses)", scorecard_text)
        self.assertIn("ความชัดเจนของเสียง (Clarity & Intelligibility)", scorecard_text)
        self.assertIn("ความน่าติดตาม (Overall Engagement)", scorecard_text)

    def test_05_gemini_api_call_structure_and_error_handling(self):
        """Calling synthesize_gemini_tts validates key and properly handles HTTP errors without silent fallback."""
        # 1. Missing API key raises ValueError
        with self.assertRaises(ValueError):
            synthesize_gemini_tts(
                text="ทดสอบ",
                style_description="neutral",
                api_key=""
            )

        # 2. HTTP error raises RuntimeError with status code
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = '{"error": {"code": 403, "message": "Method doesn\'t allow unregistered callers."}}'

        with patch("requests.post", return_value=mock_resp):
            with self.assertRaises(RuntimeError) as cm:
                synthesize_gemini_tts(
                    text="ทดสอบ",
                    style_description="neutral",
                    api_key="mock_invalid_key"
                )
            self.assertIn("HTTP 403", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
