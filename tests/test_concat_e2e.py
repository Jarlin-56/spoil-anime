"""
End-to-End (E2E) Validation Suite for Concat NLE Integration
Covers:
  1. Full Project Export with Candidate Stills + Ken Burns Easing (demo_project)
  2. Full Project Export with Anime Video Source Cuts (test_00)
  3. Continuous 48kHz WAV Stems & Zero Cumulative Drift Audio Audit
  4. Concat JSON Schema, Kanit Subtitle Typography & Dual Timeline Verification
  5. Live HTTP REST API Integration (POST /export-concat & POST /open-in-concat)
  6. Web QC Studio UI Markup & Handler Integrity Check
"""

import os
import sys
import json
import wave
import time
import urllib.request
import urllib.error
import subprocess
import unittest
from pathlib import Path

# Ensure UTF-8 output on Windows console
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.concat_exporter import export_to_concat_project as export_to_concat


class TestConcatE2E(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.demo_dir = PROJECT_ROOT / "projects" / "demo_project"
        cls.test00_dir = PROJECT_ROOT / "projects" / "test_00"
        cls.api_base = "http://127.0.0.1:3000"

    def test_01_e2e_export_demo_project_stills_kenburns(self):
        """Tier 1: E2E Export demo_project (Stills + Ken Burns + Dual Timelines)"""
        print("\n--- [E2E 1] Exporting demo_project (Stills + Ken Burns) ---")
        self.assertTrue(self.demo_dir.exists(), "demo_project directory must exist")
        
        result = export_to_concat(str(self.demo_dir))
        self.assertTrue(result.get("success"), f"Export failed: {result.get('error')}")
        
        concat_json_path = Path(result["concat_json_path"])
        self.assertTrue(concat_json_path.exists(), "concat.json must be generated on disk")
        
        with open(concat_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        # 1. Dual Timelines
        self.assertIn("timelines", data)
        timeline_ids = [tl["id"] for tl in data["timelines"]]
        self.assertIn("TL_16_9", timeline_ids, "Must contain TL_16_9")
        self.assertIn("TL_9_16", timeline_ids, "Must contain TL_9_16")
        
        tl_169 = next(tl for tl in data["timelines"] if tl["id"] == "TL_16_9")
        tl_916 = next(tl for tl in data["timelines"] if tl["id"] == "TL_9_16")
        self.assertEqual(tl_169["video"]["width"], 1920)
        self.assertEqual(tl_169["video"]["height"], 1080)
        self.assertEqual(tl_916["video"]["width"], 1080)
        self.assertEqual(tl_916["video"]["height"], 1920)
        
        # 2. Check Stills and Ken Burns effects
        video_clips = [c for c in tl_169["clips"] if c.get("trackId") == "T_VIDEO"]
        self.assertGreater(len(video_clips), 0, "Must have video/still clips on T_VIDEO")
        for clip in video_clips:
            self.assertEqual(clip.get("kind"), "image", "Clips should be images in demo_project")
            self.assertIn("keys", clip, "Clips must have keys for Ken Burns transform")
            keys = clip.get("keys", [])
            self.assertGreater(len(keys), 0, "Must have Ken Burns keyframes in keys")
            for k in keys:
                self.assertIn("property", k)
                self.assertIn("at", k)
                self.assertIn("value", k)
                self.assertEqual(k.get("ease"), {"x1": 0.25, "y1": 0.1, "x2": 0.25, "y2": 1.0})
            
        # 3. Kanit Subtitles
        sub_clips = [c for c in tl_169["clips"] if c.get("trackId") == "T_SUB"]
        self.assertGreater(len(sub_clips), 0, "Must have subtitle clips on T_SUB")
        for sub in sub_clips:
            self.assertEqual(sub.get("kind"), "text")
            self.assertIn("text", sub)
            text_obj = sub["text"]
            self.assertEqual(text_obj.get("fontFamily"), "Kanit")
            self.assertEqual(text_obj.get("align"), "center")
            self.assertIn("content", text_obj)
            self.assertGreater(len(text_obj["content"]), 0)

        # 4. Strict Track Order Invariant: T_VIDEO before T_SUB
        top_tracks = [t["id"] for t in data.get("tracks", [])]
        self.assertIn("T_VIDEO", top_tracks)
        self.assertIn("T_SUB", top_tracks)
        self.assertLess(top_tracks.index("T_VIDEO"), top_tracks.index("T_SUB"), "T_VIDEO must be before T_SUB in top-level tracks")

        tl169_tracks = [t["id"] for t in tl_169.get("tracks", [])]
        self.assertLess(tl169_tracks.index("T_VIDEO"), tl169_tracks.index("T_SUB"), "T_VIDEO must be before T_SUB in TL_16_9 tracks")

        print(f"  [PASS] demo_project exported: {len(video_clips)} scenes, {len(sub_clips)} subs, Dual Timelines verified")

    def test_02_e2e_audio_stems_and_zero_drift_audit(self):
        """Tier 2: Audio Stems Properties & Zero Cumulative Drift Audit"""
        print("\n--- [E2E 2] Audio Stems & Zero Drift Audit ---")
        concat_dir = self.demo_dir / "concat_project"
        audio_dir = concat_dir / "audio"
        voice_stem = audio_dir / "voice_stem.wav"
        bgm_stem = audio_dir / "bgm_stem.wav"
        sfx_stem = audio_dir / "sfx_stem.wav"
        
        self.assertTrue(voice_stem.exists(), "voice_stem.wav must exist")
        self.assertTrue(bgm_stem.exists(), "bgm_stem.wav must exist")
        self.assertTrue(sfx_stem.exists(), "sfx_stem.wav must exist")
        
        stems_info = {}
        for name, p in [("voice", voice_stem), ("bgm", bgm_stem), ("sfx", sfx_stem)]:
            with wave.open(str(p), "rb") as wf:
                channels = wf.getnchannels()
                sample_rate = wf.getframerate()
                sample_width = wf.getsampwidth()
                n_frames = wf.getnframes()
                duration = n_frames / float(sample_rate)
                stems_info[name] = {
                    "channels": channels,
                    "sample_rate": sample_rate,
                    "sample_width": sample_width,
                    "frames": n_frames,
                    "duration": duration
                }
                # Rule 11 Compliance Check
                self.assertEqual(sample_rate, 48000, f"{name} sample rate must be exactly 48000 Hz")
                self.assertEqual(channels, 2, f"{name} channels must be stereo (2)")
                self.assertEqual(sample_width, 2, f"{name} sample width must be 16-bit (2 bytes)")
                
        # Compare durations across all stems (Zero cumulative drift requirement)
        v_dur = stems_info["voice"]["duration"]
        b_dur = stems_info["bgm"]["duration"]
        s_dur = stems_info["sfx"]["duration"]
        
        drift_bgm = abs(v_dur - b_dur)
        drift_sfx = abs(v_dur - s_dur)
        
        print(f"  Voice Stem Duration: {v_dur:.6f}s (Frames: {stems_info['voice']['frames']})")
        print(f"  BGM Stem Duration  : {b_dur:.6f}s (Frames: {stems_info['bgm']['frames']})")
        print(f"  SFX Stem Duration  : {s_dur:.6f}s (Frames: {stems_info['sfx']['frames']})")
        print(f"  Cumulative Drift BGM: {drift_bgm:.6f}s")
        print(f"  Cumulative Drift SFX: {drift_sfx:.6f}s")
        
        self.assertLessEqual(drift_bgm, 0.001, "BGM stem drift must be less than 1ms")
        self.assertLessEqual(drift_sfx, 0.001, "SFX stem drift must be less than 1ms")
        print("  [PASS] Zero Drift Audit: 100% compliant with Rule 11 (48kHz WAV continuous stems)")

    def test_03_e2e_export_with_video_cuts(self):
        """Tier 3: E2E Export with Actual Anime Video Source Cuts (test_00)"""
        print("\n--- [E2E 3] Exporting test_00 (Video Cuts) ---")
        if not self.test00_dir.exists():
            self.skipTest("test_00 does not exist in workspace")
            
        result = export_to_concat(str(self.test00_dir))
        self.assertTrue(result.get("success"), f"Export failed: {result.get('error')}")
        
        concat_json_path = Path(result["concat_json_path"])
        self.assertTrue(concat_json_path.exists())
        
        with open(concat_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        # Verify video media registration
        video_media = [m for m in data.get("media", []) if m.get("kind") == "video"]
        self.assertGreater(len(video_media), 0, "Source video must be registered in media")
        
        # Verify video clips on T_VIDEO track
        clips = data.get("clips", [])
        video_clips = [c for c in clips if c.get("trackId") == "T_VIDEO" and c.get("kind") == "video"]
        self.assertGreater(len(video_clips), 0, "Must have video clips with source cuts")
        
        for c in video_clips:
            self.assertIn("sourceStart", c)
            self.assertIn("duration", c)
            self.assertGreaterEqual(c["sourceStart"], 0)
            self.assertEqual(c.get("volume"), 0.0, "Video source audio must be muted to prevent clashing with voiceover")
            
        print(f"  [PASS] test_00 exported with {len(video_clips)} dynamic video cuts from source anime")

    def test_04_e2e_live_rest_api_export_and_launch(self):
        """Tier 4: Live HTTP REST API Integration (Export & Open Concat)"""
        print("\n--- [E2E 4] Live REST API Integration ---")
        
        # 1. Test POST /api/projects/:id/export-concat
        export_url = f"{self.api_base}/api/projects/demo_project/export-concat"
        req = urllib.request.Request(
            export_url,
            data=json.dumps({}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                self.assertEqual(resp.status, 200)
                body = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(body.get("success"))
                self.assertIn("timelines", body)
                self.assertIn("concatProjectDir", body)
                self.assertIn("concatJsonPath", body)
                self.assertIn("scenesCount", body)
                print(f"  [PASS] POST /export-concat succeeded: {body['timelines']}")
        except urllib.error.URLError as e:
            self.skipTest(f"Test server not running at {export_url} ({e}); skipping live REST API test")

        # 2. Test POST /api/projects/:id/open-in-concat
        open_url = f"{self.api_base}/api/projects/demo_project/open-in-concat"
        req2 = urllib.request.Request(
            open_url,
            data=json.dumps({}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req2, timeout=15) as resp2:
                self.assertEqual(resp2.status, 200)
                body2 = json.loads(resp2.read().decode("utf-8"))
                self.assertTrue(body2.get("success"))
                self.assertIsNotNone(body2.get("pid"))
                self.assertIn("concatProjectDir", body2)
                pid = body2.get("pid")
                print(f"  [PASS] POST /open-in-concat succeeded (PID: {pid})")
                
                # Cleanup the test spawned Concat process gracefully
                if pid and sys.platform == 'win32':
                    try:
                        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
                        print(f"  [CLEANUP] Gracefully closed test Concat instance (PID: {pid})")
                    except Exception:
                        pass
        except urllib.error.URLError as e:
            self.fail(f"Could not connect to test server at {open_url}: {e}")

    def test_05_e2e_frontend_markup_and_integrity(self):
        """Tier 5: Web QC Studio Frontend Assets Integrity Check"""
        print("\n--- [E2E 5] Web QC Studio Frontend Markup & Styles Integrity ---")
        index_html = PROJECT_ROOT / "web" / "index.html"
        style_css = PROJECT_ROOT / "web" / "style.css"
        app_js = PROJECT_ROOT / "web" / "app.js"
        
        # Check index.html elements
        html_content = index_html.read_text(encoding="utf-8")
        self.assertIn('class="concat-nle-card"', html_content)
        self.assertIn('id="btnOpenConcat"', html_content)
        self.assertIn('id="btnExportConcat"', html_content)
        self.assertIn('id="concatSyncStatus"', html_content)
        self.assertIn('id="modalConcatNotFound"', html_content)
        self.assertIn('id="toastContainer"', html_content)
        
        # Check style.css definitions
        css_content = style_css.read_text(encoding="utf-8")
        self.assertIn('.concat-nle-card', css_content)
        self.assertIn('.btn-concat-primary', css_content)
        self.assertIn('.btn-concat-outline', css_content)
        self.assertIn('.toast-container', css_content)
        self.assertIn('.toast-success', css_content)
        
        # Check app.js logic
        js_content = app_js.read_text(encoding="utf-8")
        self.assertIn('btnOpenConcat', js_content)
        self.assertIn('btnExportConcat', js_content)
        self.assertIn('/api/projects/${currentProjectId}/open-in-concat', js_content)
        self.assertIn('/api/projects/${currentProjectId}/export-concat', js_content)
        self.assertIn('showToast', js_content)
        
        print("  [PASS] Frontend Markup, Styles & Event Bindings fully intact")


if __name__ == "__main__":
    unittest.main()
