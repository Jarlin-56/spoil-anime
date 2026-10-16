"""
Deterministic Thai Subtitle Font Rendering Integration Test Suite (Phase 4)

Verifies:
1. FFmpeg build includes libass and HarfBuzz complex-script shaping support.
2. Bundled font assets (Kanit-Regular.ttf, Kanit-Bold.ttf, OFL.txt) are valid and licensed.
3. Missing font assets produce a clear, actionable RuntimeError.
4. Thai subtitles containing complex vowels, tone marks, and stacked glyphs render deterministically
   in both 16:9 and 9:16 aspect ratios.
5. FFmpeg libass stderr confirms font resolution selects Kanit from FONTS_DIR (not host system fallback).
6. Resulting video stream, frame dimensions, and A/V duration remain strictly within tolerances.
7. Rendered frames contain visibly rendered subtitle graphics.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure core is on path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "core"))

from config import FFMPEG_BIN, FFPROBE_BIN, FONTS_DIR, ASSETS_DIR
from motion_compositor import compose_recap_video, get_tolerances


class TestSubtitleFontRendering(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="font_render_test_"))

    def tearDown(self):
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_synthetic_project(self, aspect="16:9", duration=2.0, thai_text=None):
        """Build an isolated synthetic project directory with frame, audio, and ASS subtitles."""
        if thai_text is None:
            thai_text = "ทดสอบการแสดงผลซับไตเติลภาษาไทย อนิเมะสปอยล์ กา กำ กิน กี่ กึ กือ กุ กู เก แก้ โกะ ไก"

        p_path = self.temp_dir
        frames_dir = p_path / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        # 1. Create a synthetic test image frame (solid dark blue with text label)
        frame_file = frames_dir / "frame_0001.jpg"
        cmd_img = [
            FFMPEG_BIN, "-y",
            "-f", "lavfi", "-i", "color=c=0x111b27:s=1920x1080:d=0.1",
            "-vframes", "1",
            "-q:v", "2",
            str(frame_file)
        ]
        subprocess.run(cmd_img, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        fps = 30
        frame_count = int(round(duration * fps))

        # 2. Manifests
        manifest = [{
            "id": "frame_0001",
            "filename": "frame_0001.jpg",
            "path": f"frames/frame_0001.jpg",
            "index": 1,
            "timestamp": 0.0,
            "time_str": "00:00"
        }]
        with open(p_path / "frames_manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        # 3. Create synthetic master audio (48kHz stereo sine tone)
        master_wav = p_path / "voiceover_master.wav"
        cmd_audio = [
            FFMPEG_BIN, "-y",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration:.4f}:sample_rate=48000",
            "-c:a", "pcm_s16le",
            str(master_wav)
        ]
        subprocess.run(cmd_audio, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # 4. Create voice_timeline.json matching canonical schema
        timeline_items = [{
            "id": 1,
            "order": 1,
            "startTime": 0.0,
            "endTime": round(duration, 4),
            "duration": round(duration, 4),
            "frame_start": 0,
            "frame_end": frame_count,
            "frame_count": frame_count,
            "image_id": "frame_0001",
            "motion_type": "zoom_in",
            "narration": thai_text,
            "text": thai_text
        }]
        tl_data = {
            "fps": fps,
            "total_duration": round(duration, 4),
            "total_frames": frame_count,
            "timeline": timeline_items
        }
        with open(p_path / "voice_timeline.json", "w", encoding="utf-8") as f:
            json.dump(tl_data, f, indent=2)

        # 5. Create styled ASS subtitle file
        ass_content = f"""[Script Info]
Title: Anime Recap Thai Subtitles Integration Test
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Kanit,58,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4.5,2.5,2,40,40,65,1
Style: Highlight,Kanit,62,&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5.0,3.0,2,40,40,65,1
Style: RegularStyle,Kanit,54,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,4.0,2.0,2,40,40,65,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:{duration:05.2f},Default,,0,0,0,,{thai_text}
"""
        with open(p_path / "subtitles.ass", "w", encoding="utf-8") as f:
            f.write(ass_content)

        return p_path

    def test_01_ffmpeg_libass_and_complex_shaping_support(self):
        """Verify that the selected FFmpeg binary includes libass and HarfBuzz complex shaping."""
        proc = subprocess.run([FFMPEG_BIN, "-version"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(proc.returncode, 0)
        output = proc.stdout + proc.stderr
        self.assertIn("--enable-libass", output, "FFmpeg must be compiled with --enable-libass")
        self.assertIn("--enable-libharfbuzz", output, "FFmpeg must be compiled with --enable-libharfbuzz for Thai script shaping")

        # Verify ass filter help is available
        help_proc = subprocess.run([FFMPEG_BIN, "-h", "filter=ass"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(help_proc.returncode, 0)
        self.assertIn("fontsdir", help_proc.stdout, "ASS filter must support the fontsdir option")

    def test_02_bundled_font_assets_present_and_licensed(self):
        """Verify that the required Kanit font assets and OFL license exist in assets/fonts/."""
        self.assertTrue(FONTS_DIR.exists(), f"FONTS_DIR does not exist: {FONTS_DIR}")

        regular_ttf = FONTS_DIR / "Kanit-Regular.ttf"
        bold_ttf = FONTS_DIR / "Kanit-Bold.ttf"
        ofl_txt = FONTS_DIR / "OFL.txt"

        self.assertTrue(regular_ttf.exists(), "Kanit-Regular.ttf is missing in assets/fonts")
        self.assertTrue(bold_ttf.exists(), "Kanit-Bold.ttf is missing in assets/fonts")
        self.assertTrue(ofl_txt.exists(), "OFL.txt license notice is missing in assets/fonts")

        self.assertGreater(regular_ttf.stat().st_size, 50000, "Kanit-Regular.ttf appears truncated or invalid")
        self.assertGreater(bold_ttf.stat().st_size, 50000, "Kanit-Bold.ttf appears truncated or invalid")

        with open(ofl_txt, "r", encoding="utf-8") as f:
            license_text = f.read()
        self.assertIn("SIL OPEN FONT LICENSE", license_text)
        self.assertIn("Kanit Project Authors", license_text)

    def test_03_missing_bundled_font_raises_actionable_error(self):
        """Verify that an empty/invalid fonts directory raises a clear, actionable error."""
        p_path = self._create_synthetic_project(duration=2.0)
        empty_fonts_dir = self.temp_dir / "empty_fonts"
        empty_fonts_dir.mkdir(parents=True, exist_ok=True)

        with self.assertRaises(RuntimeError) as ctx:
            compose_recap_video(
                str(p_path),
                aspect_ratio="16:9",
                burn_subtitles=True,
                fonts_dir=empty_fonts_dir
            )
        err_msg = str(ctx.exception)
        self.assertIn("Required bundled font assets missing", err_msg)
        self.assertIn("Kanit fonts", err_msg)

    def test_04_render_thai_subtitles_16_9_with_font_resolution(self):
        """Render a 16:9 sample with Thai stacked vowels and tone marks, verifying stream properties."""
        p_path = self._create_synthetic_project(aspect="16:9", duration=2.0)
        out_path = compose_recap_video(str(p_path), aspect_ratio="16:9", burn_subtitles=True)

        self.assertTrue(Path(out_path).exists(), f"Rendered video does not exist: {out_path}")
        self.assertGreater(Path(out_path).stat().st_size, 10000)

        # Probe output video
        probe_cmd = [
            FFPROBE_BIN, "-v", "error",
            "-show_entries", "stream=width,height,codec_type,duration:format=duration",
            "-of", "json",
            str(out_path)
        ]
        probe = subprocess.run(probe_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(probe.returncode, 0)
        info = json.loads(probe.stdout)

        v_stream = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
        a_stream = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)

        self.assertIsNotNone(v_stream, "Missing video stream")
        self.assertIsNotNone(a_stream, "Missing audio stream")
        self.assertEqual(v_stream["width"], 1920)
        self.assertEqual(v_stream["height"], 1080)

        # A/V Duration drift within tolerances
        v_dur = float(v_stream["duration"])
        a_dur = float(a_stream["duration"])
        self.assertAlmostEqual(v_dur, 2.0, delta=0.05)
        self.assertAlmostEqual(a_dur, 2.0, delta=0.05)
        self.assertLess(abs(v_dur - a_dur), 0.0547, "A/V duration drift must be within alignment tolerance")

    def test_05_render_thai_subtitles_9_16(self):
        """Render a 9:16 vertical sample with Thai text, verifying vertical stream dimensions."""
        p_path = self._create_synthetic_project(aspect="9:16", duration=2.0)
        out_path = compose_recap_video(str(p_path), aspect_ratio="9:16", burn_subtitles=True)

        self.assertTrue(Path(out_path).exists(), f"Rendered 9:16 video does not exist: {out_path}")

        probe_cmd = [
            FFPROBE_BIN, "-v", "error",
            "-show_entries", "stream=width,height,codec_type,duration",
            "-of", "json",
            str(out_path)
        ]
        probe = subprocess.run(probe_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(probe.returncode, 0)
        info = json.loads(probe.stdout)

        v_stream = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
        self.assertIsNotNone(v_stream)
        self.assertEqual(v_stream["width"], 1080)
        self.assertEqual(v_stream["height"], 1920)

    def test_06_font_resolution_log_verification(self):
        """Verify via FFmpeg libass log that Kanit-Bold and Kanit-Regular are loaded directly from FONTS_DIR."""
        ass_path = self.temp_dir / "test_font_resolution.ass"
        ass_path.write_text("""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: BoldStyle,Kanit,60,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,40,40,65,1
Style: RegularStyle,Kanit,60,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,4,2,2,40,40,65,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:00.50,BoldStyle,,0,0,0,,วรรณยุกต์และสระภาษาไทย กิ กี กึ กื กุ กู
Dialogue: 0,0:00:00.50,0:00:01.00,RegularStyle,,0,0,0,,ข้อความแบบตัวปกติ
""", encoding="utf-8")

        sub_escaped = str(ass_path.resolve()).replace('\\', '/').replace(':', '\\:')
        fonts_escaped = str(FONTS_DIR.resolve()).replace('\\', '/').replace(':', '\\:')
        vf_filter = f"ass='{sub_escaped}':fontsdir='{fonts_escaped}'"

        out_mp4 = self.temp_dir / "font_test_out.mp4"
        cmd = [
            FFMPEG_BIN, "-y",
            "-f", "lavfi", "-i", "color=c=black:s=1920x1080:d=1",
            "-vf", vf_filter,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(out_mp4)
        ]

        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(res.returncode, 0, f"FFmpeg execution failed: {res.stderr}")

        # Inspect stderr for font loading and fontselect
        stderr = res.stderr
        self.assertIn("Kanit-Bold.ttf", stderr, "libass must load Kanit-Bold.ttf from FONTS_DIR")
        self.assertIn("Kanit-Regular.ttf", stderr, "libass must load Kanit-Regular.ttf from FONTS_DIR")

        # Assert fontselect chose Kanit-Bold for bold style
        self.assertTrue(
            bool(re.search(r"fontselect: \(Kanit,\s*700,\s*0\)\s*->\s*Kanit-Bold", stderr)),
            f"Expected fontselect for (Kanit, 700, 0) to resolve to Kanit-Bold. Stderr was:\n{stderr[:2000]}"
        )

        # Assert fontselect chose Kanit-Regular for regular style
        self.assertTrue(
            bool(re.search(r"fontselect: \(Kanit,\s*400,\s*0\)\s*->\s*Kanit-Regular", stderr)),
            f"Expected fontselect for (Kanit, 400, 0) to resolve to Kanit-Regular. Stderr was:\n{stderr[:2000]}"
        )

    def test_07_rendered_frame_content_inspection(self):
        """Extract a frame from rendered video and inspect pixel variation to ensure subtitles are visibly drawn."""
        p_path = self._create_synthetic_project(aspect="16:9", duration=2.0)
        out_path = compose_recap_video(str(p_path), aspect_ratio="16:9", burn_subtitles=True)

        # Extract frame at 1.0s to PNG
        extracted_frame = self.temp_dir / "subtitle_inspect.png"
        extract_cmd = [
            FFMPEG_BIN, "-y",
            "-ss", "1.0",
            "-i", str(out_path),
            "-vframes", "1",
            str(extracted_frame)
        ]
        subprocess.run(extract_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertTrue(extracted_frame.exists())

        # Inspect using Pillow
        from PIL import Image
        img = Image.open(extracted_frame)
        self.assertEqual(img.size, (1920, 1080))

        # The background is solid dark color (0x111b27 -> rgb(17, 27, 39)).
        # Subtitles are white (255, 255, 255) with outline near the bottom (MarginV=65).
        # We sample the bottom subtitle region (y from 950 to 1050, x from 200 to 1720)
        # to ensure that non-background (bright/white) pixels exist where subtitles are rendered.
        sub_crop = img.crop((200, 950, 1720, 1050))
        colors = sub_crop.getcolors(maxcolors=1000000)
        
        # Check that there are bright pixels (e.g. R > 200, G > 200, B > 200) representing text
        bright_pixels = sum(count for count, col in colors if (col[0] > 200 and col[1] > 200 and col[2] > 200))
        self.assertGreater(bright_pixels, 50, "Rendered subtitle must have bright text pixels visibly drawn on frame")


if __name__ == "__main__":
    unittest.main()
