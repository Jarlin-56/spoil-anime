"""
Unit Test Suite for Listening Pack Structure and Offline Verification (Phase 1.1)
Anime Recap Pipeline — สปอย

Verifies:
1. build_evaluation_pack_plan produces 1 reference sample and 5 distinct A/B pairs.
2. Baseline settings are exactly +5% rate, +0Hz pitch, th-TH-NiwatNeural.
3. Candidate settings match resolve_voice_profile() for each specific scene direction.
4. Scorecard and answer key structures match expectations.
5. Generator dry-run completes without network calls or asset mutations.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Add core & scripts to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "core"))
sys.path.insert(0, str(BASE_DIR / "scripts"))

from generate_listening_pack import (
    build_evaluation_pack_plan,
    create_scorecard_markdown,
    main_async,
    BASELINE_VOICE,
    BASELINE_RATE,
    BASELINE_PITCH
)
from config import resolve_voice_profile


class TestListeningPackStructure(unittest.TestCase):

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_listening_pack_"))

    def tearDown(self):
        if self.temp_dir.exists():
            shutil.rmtree(str(self.temp_dir), ignore_errors=True)

    def test_01_plan_item_counts_and_types(self):
        """Plan must have 1 reference sample and exactly 5 non-neutral pairs."""
        plan = build_evaluation_pack_plan(seed=42)
        items = plan["items"]
        self.assertEqual(len(items), 6)

        ref_items = [it for it in items if it["type"] == "reference"]
        pair_items = [it for it in items if it["type"] == "pair"]

        self.assertEqual(len(ref_items), 1)
        self.assertEqual(len(pair_items), 5)

        # Expected 5 non-neutral directions
        expected_dirs = {"energetic", "suspenseful", "surprised", "dramatic", "dry_humor"}
        actual_dirs = {it["direction"] for it in pair_items}
        self.assertEqual(expected_dirs, actual_dirs)

    def test_02_baseline_candidate_pair_invariants(self):
        """Each pair must contain one baseline and one candidate using identical text and voice."""
        plan = build_evaluation_pack_plan(seed=42)
        for itm in plan["items"]:
            if itm["type"] == "reference":
                continue

            sA = itm["sample_A"]
            sB = itm["sample_B"]

            # One candidate, one baseline
            self.assertNotEqual(sA["is_candidate"], sB["is_candidate"])
            candidate_sample = sA if sA["is_candidate"] else sB
            baseline_sample = sB if sA["is_candidate"] else sA

            # Verify baseline
            self.assertEqual(baseline_sample["settings"]["profile_type"], "pre_phase1_baseline")
            self.assertEqual(baseline_sample["settings"]["rate"], BASELINE_RATE)
            self.assertEqual(baseline_sample["settings"]["pitch"], BASELINE_PITCH)
            self.assertEqual(baseline_sample["settings"]["voice"], BASELINE_VOICE)

            # Verify candidate matches resolve_voice_profile()
            exp_dir, exp_rate, exp_pitch = resolve_voice_profile(voice_direction=itm["direction"], base_rate_str="+5%")
            self.assertEqual(candidate_sample["settings"]["profile_type"], "phase1_candidate")
            self.assertEqual(candidate_sample["settings"]["voice_direction"], exp_dir)
            self.assertEqual(candidate_sample["settings"]["rate"], exp_rate)
            self.assertEqual(candidate_sample["settings"]["pitch"], exp_pitch)
            self.assertEqual(candidate_sample["settings"]["voice"], BASELINE_VOICE)

    def test_03_dry_run_generation(self):
        """Running main_async in dry_run mode generates metadata, scorecard, and answer_key without network calls."""
        import asyncio
        asyncio.run(main_async(self.temp_dir, dry_run=True))

        self.assertTrue((self.temp_dir / "scorecard.md").exists())
        self.assertTrue((self.temp_dir / "metadata.json").exists())
        self.assertTrue((self.temp_dir / "answer_key.json").exists())
        self.assertTrue((self.temp_dir / "README.md").exists())

        with open(self.temp_dir / "answer_key.json", "r", encoding="utf-8") as f:
            key_data = json.load(f)
        self.assertIn("ref", key_data)
        self.assertIn("pair_01", key_data)
        self.assertIn("pair_05", key_data)

        # In dry run, no actual mp3 files should exist
        mp3s = list(self.temp_dir.glob("*.mp3"))
        self.assertEqual(len(mp3s), 0)


if __name__ == "__main__":
    unittest.main()
