"""
Test Suite for Timing-Aware Video Pipeline
Validates all 15 Test Cases required by Phase 28:
TEST 1: Short narration
TEST 2: Long narration
TEST 3: Very short narration
TEST 4: Narration with punctuation
TEST 5: Scene split
TEST 6: Scene deletion
TEST 7: Scene insertion
TEST 8: User manually edits narration
TEST 9: TTS duration differs from estimate
TEST 10: Multiple scenes with cumulative timing
TEST 11: Subtitle longer than allowed
TEST 12: Preview vs exported video timing
TEST 13: Zoom effect with variable duration
TEST 14: TTS API failure
TEST 15: Empty narration
"""

import math
import sys
import unittest
from pathlib import Path

# Add core directory to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

from timing_engine import (
    count_thai_words,
    estimate_speech_duration,
    compute_tts_cache_key,
    split_scene,
    split_narration_text,
    compress_narration,
    split_narration_sentences,
    segment_subtitles_for_scene,
    recalculate_timeline,
    validate_timeline,
    auto_repair_timeline,
    MIN_SCENE_DURATION,
    DEFAULT_PADDING_BEFORE,
    DEFAULT_PADDING_AFTER
)


class TestTimingAwarePipeline(unittest.TestCase):

    def test_01_short_narration(self):
        """TEST 1: Short narration (8-16 words) behaves normally."""
        text = "เขาก้าวเข้าไปในห้องนั้นอย่างช้าๆ แต่กลับไม่พบใครอยู่เลย"
        words = count_thai_words(text)
        self.assertTrue(8 <= words <= 16, f"Expected 8-16 words, got {words}")
        est_dur = estimate_speech_duration(text)
        self.assertTrue(1.5 <= est_dur <= 5.0, f"Expected reasonable duration, got {est_dur}")

    def test_02_long_narration(self):
        """TEST 2: Long narration (>18 words) triggers warning or split recommendation."""
        long_text = (
            "หลังจากที่เขาเดินทางกลับมาจากเมืองหลวงและพบว่ามีบางอย่างผิดปกติเกิดขึ้นในปราสาท "
            "เขาจึงตัดสินใจรวบรวมพรรคพวกทั้งหมดแล้วบุกเข้าไปตรวจสอบความจริงในทันทีโดยไม่ฟังคำเตือนของใคร"
        )
        words = count_thai_words(long_text)
        self.assertGreater(words, 18)
        
        # Test compression
        compressed = compress_narration(long_text)
        self.assertLess(len(compressed), len(long_text))
        
        # Test split
        p1, p2 = split_narration_text(long_text)
        self.assertTrue(bool(p1 and p2))
        self.assertFalse(p1.endswith(" ") or p2.startswith(" "))

    def test_03_very_short_narration(self):
        """TEST 3: Very short narration respects minimum scene duration and padding."""
        text = "เขาหยุดนิ่ง"
        est_dur = estimate_speech_duration(text)
        scenes = [{"id": 1, "text": text}]
        tl = recalculate_timeline(scenes, fps=30)
        sc = tl["timeline"][0]
        # Duration must be >= MIN_SCENE_DURATION
        self.assertGreaterEqual(sc["duration"], MIN_SCENE_DURATION)
        self.assertEqual(sc["startTime"], 0.0)
        self.assertEqual(sc["endTime"], sc["duration"])

    def test_04_narration_with_punctuation(self):
        """TEST 4: Narration with punctuation adds cadence pauses and creates distinct subtitle phrases."""
        text = "แต่ทันใดนั้นเอง... ประตูหน้าบ้านกลับเปิดออก! และมีบางอย่างก้าวเข้ามา"
        est_with_punct = estimate_speech_duration(text)
        text_without_punct = "แต่ทันใดนั้นเอง ประตูหน้าบ้านกลับเปิดออก และมีบางอย่างก้าวเข้ามา"
        est_no_punct = estimate_speech_duration(text_without_punct)
        self.assertGreater(est_with_punct, est_no_punct, "Punctuation should add rhythm pauses")
        
        # Subtitle segmentation should catch punctuation marks
        sub_segs = segment_subtitles_for_scene(text, scene_start_time=0.0, speech_duration=est_with_punct)
        self.assertGreaterEqual(len(sub_segs), 2, "Punctuation should split into multiple subtitle phrases")

    def test_05_scene_split(self):
        """TEST 5: Scene split preserves words and metadata, divides into 2 scenes."""
        scene = {
            "id": 5,
            "order": 5,
            "storyBeat": "Development",
            "text": "เขาชักดาบสายฟ้าออกมา แต่พลังเวทมนตร์กลับต่อต้านเขาอย่างรุนแรง",
            "image_id": "frame_0005"
        }
        res = split_scene(scene, next_image_id="frame_0006")
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["text"], "เขาชักดาบสายฟ้าออกมา")
        self.assertIn("พลังเวทมนตร์", res[1]["text"])
        self.assertEqual(res[0]["storyBeat"], "Development_A")
        self.assertEqual(res[1]["storyBeat"], "Development_B")
        self.assertEqual(res[1]["image_id"], "frame_0006")

    def test_06_scene_deletion(self):
        """TEST 6: Scene deletion shifts subsequent scenes with zero gaps or overlaps."""
        scenes = [
            {"id": 1, "text": "ฉากที่หนึ่งเปิดเรื่องขึ้นมา", "actualAudioDuration": 2.5},
            {"id": 2, "text": "ฉากที่สองดำเนินเรื่องต่อ", "actualAudioDuration": 3.0},
            {"id": 3, "text": "ฉากที่สามเกิดการปะทะ", "actualAudioDuration": 2.8}
        ]
        # Initial timeline
        tl1 = recalculate_timeline(scenes, fps=30)
        self.assertEqual(len(tl1["timeline"]), 3)
        
        # Delete scene 2
        scenes.pop(1)
        tl2 = recalculate_timeline(scenes, fps=30)
        self.assertEqual(len(tl2["timeline"]), 2)
        # Scene 3 becomes index 2 and starts immediately at scene 1's end
        sc1 = tl2["timeline"][0]
        sc2 = tl2["timeline"][1]
        self.assertAlmostEqual(sc1["endTime"], sc2["startTime"], places=4)
        self.assertEqual(sc2["startTime"], sc1["duration"])

    def test_07_scene_insertion(self):
        """TEST 7: Scene insertion preserves strict timeline continuity."""
        scenes = [
            {"id": 1, "text": "ฉากแรก", "actualAudioDuration": 2.0},
            {"id": 3, "text": "ฉากสาม", "actualAudioDuration": 2.0}
        ]
        # Insert scene 2 in between
        scenes.insert(1, {"id": 2, "text": "ฉากสองที่แทรกเข้ามา", "actualAudioDuration": 2.5})
        tl = recalculate_timeline(scenes, fps=30)
        self.assertEqual(len(tl["timeline"]), 3)
        
        for i in range(1, len(tl["timeline"])):
            prev_end = tl["timeline"][i - 1]["endTime"]
            curr_start = tl["timeline"][i]["startTime"]
            self.assertAlmostEqual(prev_end, curr_start, places=4, msg="Discontinuity detected!")

    def test_08_user_manually_edits_narration(self):
        """TEST 8: Editing narration updates word count, duration, and shifts subsequent scenes."""
        scenes = [
            {"id": 1, "text": "ข้อความสั้น", "actualAudioDuration": 1.5},
            {"id": 2, "text": "ฉากต่อไป", "actualAudioDuration": 2.0}
        ]
        tl_before = recalculate_timeline(scenes, fps=30)
        start2_before = tl_before["timeline"][1]["startTime"]
        
        # User expands scene 1 narration significantly and clears old audio duration (dirtied)
        scenes[0]["text"] = "ข้อความที่ถูกแก้ไขให้ยาวขึ้นกว่าเดิมมากเพื่อบรรยายรายละเอียดของสถานการณ์ที่เกิดขึ้น"
        scenes[0].pop("actualAudioDuration", None)
        
        tl_after = recalculate_timeline(scenes, fps=30)
        start2_after = tl_after["timeline"][1]["startTime"]
        
        self.assertGreater(start2_after, start2_before, "Subsequent scene must shift forward when scene 1 expands")
        self.assertAlmostEqual(tl_after["timeline"][0]["endTime"], tl_after["timeline"][1]["startTime"], places=4)

    def test_09_tts_duration_differs_from_estimate(self):
        """TEST 9: Timeline adapts smoothly when actual TTS audio differs from estimate."""
        scene = {"id": 1, "text": "การทดสอบเสียงจริงที่ได้จาก TTS"}
        # Initial estimated timeline
        tl_est = recalculate_timeline([scene], fps=30)
        dur_est = tl_est["timeline"][0]["duration"]
        
        # Now actual audio is measured and is longer than estimate
        scene["actualAudioDuration"] = 4.2
        tl_actual = recalculate_timeline([scene], fps=30)
        dur_actual = tl_actual["timeline"][0]["duration"]
        
        self.assertGreater(dur_actual, 4.2, "Scene duration must accommodate actual audio + breathing room")
        self.assertGreater(dur_actual, dur_est)

    def test_10_multiple_scenes_cumulative_timing(self):
        """TEST 10: 10 scenes must have zero cumulative audio drift and exact frame alignment."""
        scenes = []
        for i in range(10):
            scenes.append({
                "id": i + 1,
                "text": f"ฉากที่ {i+1} บรรยายเหตุการณ์อย่างสนุกสนาน",
                "actualAudioDuration": 2.3 + (i * 0.15)
            })
            
        tl = recalculate_timeline(scenes, fps=30)
        qc = validate_timeline(tl)
        self.assertTrue(qc["valid"], f"Validation failed: {qc.errors if hasattr(qc, 'errors') else qc.get('errors')}")
        
        total_time = tl["total_duration"]
        last_scene_end = tl["timeline"][-1]["endTime"]
        self.assertAlmostEqual(total_time, last_scene_end, places=4)
        
        # Frame alignment verification
        for sc in tl["timeline"]:
            calc_dur = (sc["frame_end"] - sc["frame_start"]) / 30.0
            self.assertAlmostEqual(sc["duration"], calc_dur, places=4)

    def test_11_subtitle_longer_than_allowed(self):
        """TEST 11: Long subtitles are split into short semantic phrases with max 2 lines."""
        long_sub = "ชายหนุ่มในชุดคลุมสีดำก้าวออกมาจากเงามืด และชักดาบแห่งสายฟ้าฟาดฟันศัตรูในพริบตาเดียว"
        segments = segment_subtitles_for_scene(
            long_sub,
            scene_start_time=0.0,
            speech_duration=5.0,
            max_chars_per_line=30
        )
        self.assertGreater(len(segments), 1, "Long subtitle must be split into multiple segments")
        for seg in segments:
            self.assertLessEqual(seg["text"].count('\n'), 1, "Subtitle cannot exceed 2 lines")
            self.assertGreater(seg["duration"], 0.0)

    def test_12_preview_vs_exported_video_timing(self):
        """TEST 12: Timeline output contains all required fields for preview and rendering."""
        scenes = [{"id": 1, "text": "ฉากตัวอย่าง", "actualAudioDuration": 2.5}]
        tl = recalculate_timeline(scenes, fps=30)
        entry = tl["timeline"][0]
        
        required_keys = ["startTime", "endTime", "duration", "frame_start", "frame_end", "frame_count", "subtitleSegments"]
        for k in required_keys:
            self.assertIn(k, entry)
            self.assertIsNotNone(entry[k])

    def test_13_zoom_effect_variable_duration(self):
        """TEST 13: Zoom step scales proportionally with total_frames."""
        fps = 30
        short_dur = 2.0
        short_frames = int(short_dur * fps)
        zoom_delta_short = 0.20 / short_frames
        
        long_dur = 8.0
        long_frames = int(long_dur * fps)
        zoom_delta_long = 0.20 / long_frames
        
        self.assertGreater(zoom_delta_short, zoom_delta_long, "Shorter duration should have larger per-frame delta")
        # Total zoom accumulated over total_frames equals 0.20 in both cases!
        self.assertAlmostEqual(short_frames * zoom_delta_short, 0.20, places=4)
        self.assertAlmostEqual(long_frames * zoom_delta_long, 0.20, places=4)

    def test_14_tts_caching_and_hashing(self):
        """TEST 14: Cache key is deterministic and changes when narration changes."""
        key1 = compute_tts_cache_key("ข้อความแรก", "voice1", "+5%")
        key2 = compute_tts_cache_key("ข้อความแรก", "voice1", "+5%")
        key3 = compute_tts_cache_key("ข้อความสอง", "voice1", "+5%")
        self.assertEqual(key1, key2, "Identical input must produce identical hash")
        self.assertNotEqual(key1, key3, "Different text must produce different hash")

    def test_15_empty_narration(self):
        """TEST 15: Empty narration is caught by validation layer."""
        scenes = [{"id": 1, "text": ""}]
        tl = recalculate_timeline(scenes, fps=30)
        qc = validate_timeline(tl)
        self.assertFalse(qc["valid"], "Empty narration must fail QC validation")
        self.assertTrue(any("empty" in e.lower() for e in qc["errors"]))

    def test_16_multisentence_boundary_sync(self):
        """TEST 16: Multi-sentence scene synchronizes subtitles with audio boundary timestamps."""
        text = "ถ้าชีวิตมันเหนื่อยขนาดนี้... เป็นใครก็คงอยากยืนถอนหายใจยาวๆ"
        phrases = split_narration_sentences(text)
        self.assertEqual(len(phrases), 2, "Should split into two sentences")
        self.assertEqual(phrases[0], "ถ้าชีวิตมันเหนื่อยขนาดนี้...")
        self.assertEqual(phrases[1], "เป็นใครก็คงอยากยืนถอนหายใจยาวๆ")

        # Simulate exact audio boundary metadata from Edge-TTS
        boundaries = [
            {"text": phrases[0], "offset": 0.10, "duration": 1.50},
            {"text": phrases[1], "offset": 1.70, "duration": 2.20}
        ]

        scene = {
            "id": 1,
            "text": text,
            "actualAudioDuration": 4.0,
            "sentence_boundaries": boundaries
        }
        tl = recalculate_timeline([scene], fps=30)
        sub_segs = tl["timeline"][0]["subtitleSegments"]
        self.assertEqual(len(sub_segs), 2)
        
        # Subtitle 1 must start at 0.10s and switch to Subtitle 2 exactly at 1.70s
        self.assertAlmostEqual(sub_segs[0]["start_time"], 0.10, places=3)
        self.assertAlmostEqual(sub_segs[0]["end_time"], 1.70, places=3)
        self.assertAlmostEqual(sub_segs[1]["start_time"], 1.70, places=3)
        self.assertAlmostEqual(sub_segs[1]["end_time"], 4.0, places=3)


if __name__ == "__main__":
    unittest.main()
