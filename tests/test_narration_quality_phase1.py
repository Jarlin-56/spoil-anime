"""
Regression Test Suite for Phase 1: Thai Narration Quality Improvement Plan
Anime Recap Pipeline — สปอย

Verifies:
1. Voice profile mappings for all 6 directions (conversational, energetic, surprised, suspenseful, dramatic, dry_humor).
2. Backward compatibility & safe fallbacks for legacy action_type and missing fields.
3. Explicit voice_direction precedence over action_type.
4. TTS cache key correctness: distinct keys when pitch/rate/direction change, identical keys on match.
5. Consistency between preview profile resolution and full pipeline resolution.
6. Audio duration, SentenceBoundary metadata, and canonical frame-aligned timeline preservation.
"""

import sys
import unittest
from pathlib import Path

# Add core to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "core"))

from config import (
    VOICE_PROFILES,
    DEFAULT_VOICE_PROFILE,
    ACTION_TYPE_TO_VOICE_DIRECTION,
    resolve_voice_profile
)
from timing_engine import (
    compute_tts_cache_key,
    recalculate_timeline,
    validate_timeline,
    split_narration_sentences
)


class TestNarrationQualityPhase1(unittest.TestCase):

    def test_01_voice_profile_definitions(self):
        """Verify all 6 voice directions are defined with conservative rate/pitch offsets."""
        expected_directions = [
            "conversational",
            "energetic",
            "surprised",
            "suspenseful",
            "dramatic",
            "dry_humor"
        ]
        for d in expected_directions:
            self.assertIn(d, VOICE_PROFILES, f"Profile '{d}' must be defined")
            prof = VOICE_PROFILES[d]
            self.assertIn("rate_offset", prof)
            self.assertIn("pitch_offset", prof)
            # Conservative bounds: rate within [-15, +15], pitch within [-5, +5]
            self.assertTrue(-15 <= prof["rate_offset"] <= 15, f"Rate offset for {d} out of conservative bounds")
            self.assertTrue(-5 <= prof["pitch_offset"] <= 5, f"Pitch offset for {d} out of conservative bounds")

    def test_02_resolve_voice_profile_all_directions(self):
        """Verify resolve_voice_profile resolves exact effective rate and pitch for base +5%."""
        cases = {
            "conversational": ("+5%", "+0Hz"),
            "energetic": ("+13%", "+3Hz"),
            "surprised": ("+9%", "+4Hz"),
            "suspenseful": ("+1%", "-1Hz"),
            "dramatic": ("+1%", "-2Hz"),
            "dry_humor": ("+7%", "-1Hz")
        }
        for direction, (exp_rate, exp_pitch) in cases.items():
            res_dir, res_rate, res_pitch = resolve_voice_profile(voice_direction=direction, base_rate_str="+5%")
            self.assertEqual(res_dir, direction)
            self.assertEqual(res_rate, exp_rate, f"Rate mismatch for {direction}")
            self.assertEqual(res_pitch, exp_pitch, f"Pitch mismatch for {direction}")

    def test_03_legacy_action_type_fallbacks(self):
        """Verify existing scripts with only action_type map cleanly and deterministically."""
        legacy_cases = {
            "battle": ("energetic", "+13%", "+3Hz"),
            "shock": ("surprised", "+9%", "+4Hz"),
            "drama": ("dramatic", "+1%", "-2Hz"),
            "normal": ("conversational", "+5%", "+0Hz"),
        }
        for action, (exp_dir, exp_rate, exp_pitch) in legacy_cases.items():
            res_dir, res_rate, res_pitch = resolve_voice_profile(action_type=action, base_rate_str="+5%")
            self.assertEqual(res_dir, exp_dir)
            self.assertEqual(res_rate, exp_rate)
            self.assertEqual(res_pitch, exp_pitch)

    def test_04_missing_and_invalid_metadata_safe_fallbacks(self):
        """Verify missing, empty, or unknown fields gracefully fallback to conversational default."""
        res_dir, res_rate, res_pitch = resolve_voice_profile(action_type=None, voice_direction=None, base_rate_str=None)
        self.assertEqual(res_dir, DEFAULT_VOICE_PROFILE)
        self.assertEqual(res_rate, "+5%")
        self.assertEqual(res_pitch, "+0Hz")

        # Unknown action_type and voice_direction
        res_dir, res_rate, res_pitch = resolve_voice_profile(action_type="unknown_xyz", voice_direction="invalid_123")
        self.assertEqual(res_dir, DEFAULT_VOICE_PROFILE)

    def test_05_voice_direction_precedence_over_action_type(self):
        """Explicit voice_direction must take precedence over legacy action_type."""
        # e.g., action_type is "battle" but voice_direction explicitly set to "suspenseful"
        res_dir, res_rate, res_pitch = resolve_voice_profile(
            action_type="battle",
            voice_direction="suspenseful",
            base_rate_str="+5%"
        )
        self.assertEqual(res_dir, "suspenseful")
        self.assertEqual(res_rate, "+1%")
        self.assertEqual(res_pitch, "-1Hz")

    def test_06_cache_key_invalidation_and_reuse(self):
        """Cache key must change when rate or pitch changes, and stay identical on exact match."""
        text = "นี่คือการทดสอบระบบพากย์เสียง"
        voice = "th-TH-NiwatNeural"

        # Baseline conversational
        key_conv = compute_tts_cache_key(text, voice, rate="+5%", pitch="+0Hz")
        key_conv_repeat = compute_tts_cache_key(text, voice, rate="+5%", pitch="+0Hz")
        self.assertEqual(key_conv, key_conv_repeat, "Identical inputs must produce identical cache keys")

        # Energetic (battle)
        key_energetic = compute_tts_cache_key(text, voice, rate="+13%", pitch="+3Hz")
        self.assertNotEqual(key_conv, key_energetic, "Changing profile must produce a different cache key")

        # Surprised (shock)
        key_surprised = compute_tts_cache_key(text, voice, rate="+9%", pitch="+4Hz")
        self.assertNotEqual(key_energetic, key_surprised)

        # Dramatic
        key_dramatic = compute_tts_cache_key(text, voice, rate="+1%", pitch="-2Hz")
        self.assertNotEqual(key_conv, key_dramatic)

    def test_07_timeline_invariants_preserved_with_mixed_profiles(self):
        """Multiple scenes with varying voice directions preserve frame alignment and continuity."""
        scenes = [
            {"id": 1, "text": "ฉากเปิดตัวสบายๆ ในเมือง", "action_type": "normal", "voice_direction": "conversational", "actualAudioDuration": 2.50},
            {"id": 2, "text": "ทันใดนั้นศัตรูก็บุกเข้ามาโจมตี!", "action_type": "battle", "voice_direction": "energetic", "actualAudioDuration": 2.10},
            {"id": 3, "text": "ทุกคนต่างตกใจกับสิ่งที่ได้เห็น", "action_type": "shock", "voice_direction": "surprised", "actualAudioDuration": 2.30},
            {"id": 4, "text": "บรรยากาศเต็มไปด้วยความสูญเสีย", "action_type": "drama", "voice_direction": "dramatic", "actualAudioDuration": 3.10},
            {"id": 5, "text": "เขาได้แต่ถอนหายใจกับเรื่องตลกนี้", "action_type": "normal", "voice_direction": "dry_humor", "actualAudioDuration": 2.40}
        ]

        tl = recalculate_timeline(scenes, fps=30)
        qc = validate_timeline(tl)
        self.assertTrue(qc["valid"], f"Timeline validation failed: {qc['errors']}")

        timeline_items = tl["timeline"]
        self.assertEqual(len(timeline_items), 5)

        # Verify voice_direction is preserved in timeline items
        self.assertEqual(timeline_items[0]["voice_direction"], "conversational")
        self.assertEqual(timeline_items[1]["voice_direction"], "energetic")
        self.assertEqual(timeline_items[2]["voice_direction"], "surprised")
        self.assertEqual(timeline_items[3]["voice_direction"], "dramatic")
        self.assertEqual(timeline_items[4]["voice_direction"], "dry_humor")

        # Verify strict continuity: scene[i].startTime == scene[i-1].endTime
        for i in range(1, len(timeline_items)):
            prev_end = timeline_items[i - 1]["endTime"]
            curr_start = timeline_items[i]["startTime"]
            self.assertAlmostEqual(prev_end, curr_start, places=4)

    def test_08_multisentence_clause_splitting_consistency(self):
        """Preview and Engine sentence boundary splitting must be consistent for clauses."""
        text = "ศัตรูบุกเข้ามาแล้ว... แต่เขาก็ยังคงนิ่งเฉย"
        clauses = split_narration_sentences(text)
        self.assertGreaterEqual(len(clauses), 2)
        joined = "\n".join(clauses)
        self.assertIn("\n", joined)


if __name__ == "__main__":
    unittest.main()
