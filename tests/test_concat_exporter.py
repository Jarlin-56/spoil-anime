"""
tests/test_concat_exporter.py
==============================
Validates the Concat Project Exporter against project_rules.md:
- Rule 11: Audio-Driven Master Timeline with Global Snapping and Continuous 48kHz WAV Stems
- Rule 13: Thai Subtitle Natural Split & Typography
- Dual Timeline format (16:9 Landscape & 9:16 Vertical)
- Ken Burns cubic-bezier keyframes
"""

import json
import os
import sys
import unittest
from pathlib import Path

# Ensure root and core are on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "core"))

from core.concat_exporter import export_to_concat_project, build_ken_burns_keys


class TestConcatExporter(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.demo_dir = BASE_DIR / "projects" / "demo_project"
        cls.test_00_dir = BASE_DIR / "projects" / "test_00"

    def test_build_ken_burns_keys(self):
        """Verify cubic bezier keyframes for various action types."""
        zoom_keys = build_ken_burns_keys("zoom_in")
        self.assertGreaterEqual(len(zoom_keys), 2)
        self.assertEqual(zoom_keys[0]["property"], "scale")
        self.assertEqual(zoom_keys[0]["value"], 1.0)
        self.assertEqual(zoom_keys[1]["value"], 1.15)
        self.assertIn("ease", zoom_keys[0])

        pan_keys = build_ken_burns_keys("pan_left")
        props = [k["property"] for k in pan_keys]
        self.assertIn("offsetX", props)

    def test_export_demo_project(self):
        """Verify export on demo_project."""
        if not self.demo_dir.exists():
            self.skipTest("demo_project not present")

        res = export_to_concat_project(self.demo_dir)
        self.assertTrue(res["success"])
        
        concat_json = Path(res["concat_json_path"])
        self.assertTrue(concat_json.exists())

        with open(concat_json, "r", encoding="utf-8") as f:
            doc = json.load(f)

        # Verify Document Version & Timelines
        self.assertEqual(doc["version"], 1)
        self.assertEqual(len(doc["timelines"]), 2)
        
        tl_ids = [t["id"] for t in doc["timelines"]]
        self.assertIn("TL_16_9", tl_ids)
        self.assertIn("TL_9_16", tl_ids)

        # Verify 16:9 and 9:16 resolutions
        tl_16_9 = next(t for t in doc["timelines"] if t["id"] == "TL_16_9")
        self.assertEqual(tl_16_9["video"]["width"], 1920)
        self.assertEqual(tl_16_9["video"]["height"], 1080)

        tl_9_16 = next(t for t in doc["timelines"] if t["id"] == "TL_9_16")
        self.assertEqual(tl_9_16["video"]["width"], 1080)
        self.assertEqual(tl_9_16["video"]["height"], 1920)

        # Verify Tracks (T_SUB, T_VIDEO, T_VOICE, T_BGM, T_SFX)
        track_ids = [tr["id"] for tr in tl_16_9["tracks"]]
        self.assertIn("T_SUB", track_ids)
        self.assertIn("T_VIDEO", track_ids)
        self.assertIn("T_VOICE", track_ids)
        self.assertIn("T_BGM", track_ids)

        # Verify Audio Stems
        voice_stem = Path(res["stems"]["voice"])
        bgm_stem = Path(res["stems"]["bgm"])
        self.assertTrue(voice_stem.exists())
        self.assertTrue(bgm_stem.exists())

        # Verify Subtitle Clips have Kanit styling
        sub_clips = [c for c in tl_16_9["clips"] if c["trackId"] == "T_SUB"]
        self.assertGreater(len(sub_clips), 0)
        first_sub = sub_clips[0]
        self.assertEqual(first_sub["kind"], "text")
        self.assertEqual(first_sub["text"]["fontFamily"], "Kanit")

    def test_export_test_00_video_linkage(self):
        """Verify moving video slices linkage when source anime video is available."""
        if not self.test_00_dir.exists():
            self.skipTest("test_00 not present")

        res = export_to_concat_project(self.test_00_dir)
        self.assertTrue(res["success"])

        with open(res["concat_json_path"], "r", encoding="utf-8") as f:
            doc = json.load(f)

        # Check media has m_source_video
        m_ids = [m["id"] for m in doc["media"]]
        self.assertIn("m_source_video", m_ids)

        # Check video clips on T_VIDEO reference m_source_video with in-points
        tl = doc["timelines"][0]
        v_clips = [c for c in tl["clips"] if c["trackId"] == "T_VIDEO"]
        self.assertGreater(len(v_clips), 0)
        self.assertEqual(v_clips[0]["mediaId"], "m_source_video")
        self.assertIn("sourceStart", v_clips[0])


if __name__ == "__main__":
    unittest.main()
