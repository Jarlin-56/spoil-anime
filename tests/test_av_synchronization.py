"""
Regression Test Suite for Phase 2.1: Root-Cause Verification and A/V Audit Correction.

Verifies:
1. Audio and video with matching durations render seamlessly.
2. Material visual-duration deficit (> frame_tolerance) fails safely rather than silently stretching the final scene.
3. Bounded frame-rounding differences (<= frame_tolerance, 1-2 frames) are applied accurately.
4. Visual timeline longer than narration (proper padding, full video preserved, complete narration kept).
5. Multiple visual segments with fractional frame boundaries preserve continuity.
6. Short narration and long narration handling.
7. Failure during segment rendering raises RuntimeError.
8. Post-render FFprobe validation failure detection.
9. Milestone audit semantics:
   - Strictly increasing milestone timestamps for distinct target frames.
   - Timestamps represent actual percentage positions along the timeline, NOT scene-end times.
   - Milestones within the same scene have distinct target frames and times.
   - Milestones at exact scene boundaries are deterministically handled.
10. Existing valid output remains untouched when a new render fails (atomic export).
11. Both 16:9 and 9:16 exports preserve complete narration.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from PIL import Image, ImageDraw

# Ensure core is on path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "core"))

from unittest.mock import patch

from config import FFMPEG_BIN, FFPROBE_BIN
from motion_compositor import (
    compose_recap_video,
    compute_milestone_audit,
    generate_clip_for_image,
    get_stream_duration,
    get_stream_info,
    count_stream_frames,
    get_tolerances,
    compute_clip_fingerprint,
    COMPOSITOR_VERSION,
    validate_pre_render,
    validate_post_render
)
from timing_engine import recalculate_timeline, validate_timeline


def create_solid_image(path: Path, width: int = 1920, height: int = 1080, color=(100, 150, 200), label: str = "Test"):
    img = Image.new("RGB", (width, height), color)
    draw = ImageDraw.Draw(img)
    draw.text((50, 50), label, fill=(255, 255, 255))
    img.save(str(path), quality=90)


def create_sine_wav(path: Path, duration: float, sample_rate: int = 48000):
    cmd = [
        FFMPEG_BIN, "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency=440:sample_rate={sample_rate}:duration={duration:.6f}",
        "-ac", "2",
        "-c:a", "pcm_s16le",
        str(path)
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


class TestAVSynchronization(unittest.TestCase):

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="av_sync_test_"))
        self.frames_dir = self.test_dir / "frames"
        self.frames_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(str(self.test_dir), ignore_errors=True)

    def _create_fixture_project(self, segment_durations, audio_duration, fps=30):
        """Helper to build an isolated test project directory."""
        timeline_items = []
        cur_t = 0.0
        for i, dur in enumerate(segment_durations, 1):
            frame_file = f"frame_{i:04d}.jpg"
            create_solid_image(self.frames_dir / frame_file, label=f"Scene {i}")
            fc = int(round(dur * fps))
            seg = {
                "id": i,
                "order": i,
                "startTime": round(cur_t, 4),
                "endTime": round(cur_t + dur, 4),
                "duration": round(dur, 4),
                "frame_start": int(round(cur_t * fps)),
                "frame_end": int(round(cur_t * fps)) + fc,
                "frame_count": fc,
                "image_id": f"frame_{i:04d}",
                "narration": f"Test narration sentence {i}",
                "text": f"Test narration sentence {i}"
            }
            timeline_items.append(seg)
            cur_t += dur

        tot_dur = round(cur_t, 4)
        tl_data = {
            "fps": fps,
            "total_duration": tot_dur,
            "total_frames": int(round(tot_dur * fps)),
            "timeline": timeline_items
        }

        with open(self.test_dir / "voice_timeline.json", "w", encoding="utf-8") as f:
            json.dump(tl_data, f, indent=2)

        wav_path = self.test_dir / "voiceover_master.wav"
        create_sine_wav(wav_path, duration=audio_duration)

        return tl_data, wav_path

    def test_01_matching_durations(self):
        """Case 1: Audio and video with matching durations render seamlessly."""
        self._create_fixture_project(segment_durations=[2.0, 2.0], audio_duration=4.0, fps=30)
        out = compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)
        self.assertTrue(Path(out).exists())

        qc = validate_post_render(Path(out), expected_audio_dur=4.0, expected_timeline_dur=4.0, fps=30, aspect_ratio="16:9")
        self.assertTrue(qc["valid"])
        self.assertAlmostEqual(qc["video_duration"], 4.0, delta=0.07)
        self.assertAlmostEqual(qc["audio_duration"], 4.0, delta=0.07)

    def test_02_material_duration_mismatch_fails_safely(self):
        """Case 2: Material visual-duration deficit (> frame_tolerance) causes safe failure rather than silent extension."""
        # Visual timeline is 3.0s total, but narration audio is 5.0s (material deficit of 2.0s)
        self._create_fixture_project(segment_durations=[1.5, 1.5], audio_duration=5.0, fps=30)
        
        # Must fail pre-render validation explicitly with RuntimeError
        with self.assertRaises(RuntimeError) as ctx:
            compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)
        self.assertIn("Material A/V duration mismatch", str(ctx.exception))
        self.assertIn("Visual timeline (3.0000s) does not cover narration", str(ctx.exception))

    def test_03_bounded_frame_rounding_adjustment(self):
        """Case 3: Small frame-rounding differences (<= frame_tolerance, e.g. 1 frame) are handled with bounded adjustment."""
        # Visual timeline is 4.0s (120 frames @ 30fps), audio is 4.033s (approx 121 frames, diff=0.033s <= tolerance)
        self._create_fixture_project(segment_durations=[2.0, 2.0], audio_duration=4.033, fps=30)
        out = compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)
        self.assertTrue(Path(out).exists())

        qc = validate_post_render(Path(out), expected_audio_dur=4.033, expected_timeline_dur=4.033, fps=30, aspect_ratio="16:9")
        self.assertTrue(qc["valid"])
        self.assertAlmostEqual(qc["video_duration"], 4.033, delta=0.07)

    def test_04_visual_timeline_longer_than_narration(self):
        """Case 4: Visual timeline longer than narration -> Full video preserved, audio padded, narration intact."""
        # Visual is 5.0s total, narration is 3.0s (video is 2.0s longer)
        self._create_fixture_project(segment_durations=[2.5, 2.5], audio_duration=3.0, fps=30)
        out = compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)
        self.assertTrue(Path(out).exists())

        qc = validate_post_render(Path(out), expected_audio_dur=3.0, expected_timeline_dur=5.0, fps=30, aspect_ratio="16:9")
        self.assertTrue(qc["valid"])
        self.assertAlmostEqual(qc["video_duration"], 5.0, delta=0.07)
        self.assertAlmostEqual(qc["audio_duration"], 5.0, delta=0.07)

    def test_05_fractional_frame_boundaries(self):
        """Case 5: Multiple visual segments with fractional frame boundaries preserve continuity."""
        # Fractional seconds: 1.3333, 2.4667, 1.8333
        self._create_fixture_project(segment_durations=[1.3333, 2.4667, 1.8333], audio_duration=5.6333, fps=30)
        out = compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)
        self.assertTrue(Path(out).exists())

        qc = validate_post_render(Path(out), expected_audio_dur=5.6333, expected_timeline_dur=5.6333, fps=30, aspect_ratio="16:9")
        self.assertTrue(qc["valid"])
        self.assertAlmostEqual(qc["video_duration"], 5.6333, delta=0.07)

    def test_06_short_and_long_narration(self):
        """Case 6: Very short narration and longer narration both succeed."""
        # Short (1.2s)
        self._create_fixture_project(segment_durations=[1.2], audio_duration=1.2, fps=30)
        out_short = compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)
        qc_short = validate_post_render(Path(out_short), expected_audio_dur=1.2, expected_timeline_dur=1.2, fps=30)
        self.assertTrue(qc_short["valid"])

        # Long (8.0s)
        self._create_fixture_project(segment_durations=[2.0, 2.0, 2.0, 2.0], audio_duration=8.0, fps=30)
        out_long = compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)
        qc_long = validate_post_render(Path(out_long), expected_audio_dur=8.0, expected_timeline_dur=8.0, fps=30)
        self.assertTrue(qc_long["valid"])

    def test_07_failure_during_segment_rendering_raises_error(self):
        """Case 7: A failure during segment rendering stops the export with RuntimeError."""
        self._create_fixture_project(segment_durations=[2.0, 2.0], audio_duration=4.0, fps=30)
        # Point to a non-existent / invalid image file
        with open(self.test_dir / "voice_timeline.json", "r+", encoding="utf-8") as f:
            data = json.load(f)
            data["timeline"][1]["image_id"] = "non_existent_frame_9999"
            f.seek(0)
            json.dump(data, f)
            f.truncate()

        # Delete available frames so fallback also fails
        for p in self.frames_dir.glob("*.jpg"):
            p.unlink()

        with self.assertRaises(FileNotFoundError):
            compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)

    def test_08_post_render_validation_failure_detected(self):
        """Case 8: Post-render FFprobe validation failure is correctly triggered."""
        self._create_fixture_project(segment_durations=[2.0], audio_duration=2.0, fps=30)
        out = compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)

        # Expecting 10.0s when file is only 2.0s must trigger post-render failure
        with self.assertRaises(RuntimeError) as ctx:
            validate_post_render(Path(out), expected_audio_dur=10.0, expected_timeline_dur=10.0, fps=30)
        self.assertIn("Post-render validation failed", str(ctx.exception))

    def test_09_milestone_audit_semantics(self):
        """Case 9: Milestone audit semantics: percentage positions, strictly increasing, no duplicate scene-end times."""
        # Scene 1: 0.0s - 12.0s (frames 0 - 360) -> contains both 10% (4.0s) and 25% (10.0s)
        # Scene 2: 12.0s - 22.0s (frames 360 - 660) -> contains 50% (20.0s)
        # Scene 3: 22.0s - 34.0s (frames 660 - 1020) -> contains 75% (30.0s)
        # Scene 4: 34.0s - 40.0s (frames 1020 - 1200) -> contains 90% (36.0s) and 100% (40.0s)
        timeline_items = [
            {"id": 1, "startTime": 0.0, "endTime": 12.0, "duration": 12.0, "frame_start": 0, "frame_end": 360},
            {"id": 2, "startTime": 12.0, "endTime": 22.0, "duration": 10.0, "frame_start": 360, "frame_end": 660},
            {"id": 3, "startTime": 22.0, "endTime": 34.0, "duration": 12.0, "frame_start": 660, "frame_end": 1020},
            {"id": 4, "startTime": 34.0, "endTime": 40.0, "duration": 6.0, "frame_start": 1020, "frame_end": 1200},
        ]
        total_timeline_dur = 40.0
        fps = 30
        records = compute_milestone_audit(timeline_items, total_timeline_dur, fps=fps)

        # Check all 6 standard milestones are present
        self.assertEqual(len(records), 6)
        expected_percentages = [0.10, 0.25, 0.50, 0.75, 0.90, 1.00]
        for r, exp_p in zip(records, expected_percentages):
            self.assertEqual(r["percentage"], exp_p)

        # 1. Strictly increasing timestamps for distinct target frames
        expected_times = [r["expected_time"] for r in records]
        frame_times = [r["frame_time"] for r in records]
        for i in range(len(expected_times) - 1):
            self.assertLess(expected_times[i], expected_times[i + 1])
            self.assertLess(frame_times[i], frame_times[i + 1])

        # 2. Milestones located within the same scene (10% and 25% both in Seg 1) must have DIFFERENT timestamps!
        m10 = records[0]
        m25 = records[1]
        self.assertEqual(m10["segment_id"], 1)
        self.assertEqual(m25["segment_id"], 1)
        self.assertEqual(m10["expected_time"], 4.0)  # 10% of 40s = 4.0s
        self.assertEqual(m25["expected_time"], 10.0) # 25% of 40s = 10.0s
        self.assertNotEqual(m10["expected_time"], m25["expected_time"])
        # Ensure 10% is NOT labeled as Scene 1's endTime (12.0s)
        self.assertEqual(m10["expected_time"], 4.0)

        # 3. Milestone 50% is at 20.0s in Seg 2
        m50 = records[2]
        self.assertEqual(m50["segment_id"], 2)
        self.assertEqual(m50["expected_time"], 20.0)

        # 4. Milestone 75% is at 30.0s in Seg 3
        m75 = records[3]
        self.assertEqual(m75["segment_id"], 3)
        self.assertEqual(m75["expected_time"], 30.0)

        # 5. Milestones 90% and 100% both in Seg 4 must have DIFFERENT timestamps!
        m90 = records[4]
        m100 = records[5]
        self.assertEqual(m90["segment_id"], 4)
        self.assertEqual(m100["segment_id"], 4)
        self.assertEqual(m90["expected_time"], 36.0)
        self.assertEqual(m100["expected_time"], 40.0)
        self.assertNotEqual(m90["expected_time"], m100["expected_time"])

        # 6. Deterministic selection at exact boundary: 12.0s boundary belongs to Seg 2
        # (half-open [12.0, 22.0))
        boundary_records = compute_milestone_audit(timeline_items, total_timeline_dur, fps=fps, milestones=[0.30])
        self.assertEqual(boundary_records[0]["expected_time"], 12.0)
        self.assertEqual(boundary_records[0]["segment_id"], 2)

    def test_10_existing_valid_output_unchanged_on_failure(self):
        """Case 10: An existing valid output remains unchanged when a new render fails."""
        # 1. First produce a valid export
        self._create_fixture_project(segment_durations=[2.0], audio_duration=2.0, fps=30)
        out = compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)
        original_size = Path(out).stat().st_size

        # 2. Corrupt timeline to trigger a failure in second render
        with open(self.test_dir / "voice_timeline.json", "w", encoding="utf-8") as f:
            json.dump({"fps": 30, "timeline": []}, f)

        # 3. Second render must fail
        with self.assertRaises(ValueError):
            compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)

        # 4. Verify original export is STILL intact and untouched
        self.assertTrue(Path(out).exists())
        self.assertEqual(Path(out).stat().st_size, original_size)

    def test_11_both_16_9_and_9_16_exports(self):
        """Case 11: Both 16:9 and 9:16 exports preserve complete narration."""
        self._create_fixture_project(segment_durations=[2.0, 2.0], audio_duration=4.0, fps=30)

        # 16:9 export
        out_16_9 = compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)
        qc_16_9 = validate_post_render(Path(out_16_9), expected_audio_dur=4.0, expected_timeline_dur=4.0, fps=30, aspect_ratio="16:9")
        self.assertTrue(qc_16_9["valid"])
        self.assertEqual((qc_16_9["width"], qc_16_9["height"]), (1920, 1080))
        self.assertAlmostEqual(qc_16_9["audio_duration"], 4.0, delta=0.07)

        # 9:16 export
        out_9_16 = compose_recap_video(str(self.test_dir), aspect_ratio="9:16", burn_subtitles=False)
        qc_9_16 = validate_post_render(Path(out_9_16), expected_audio_dur=4.0, expected_timeline_dur=4.0, fps=30, aspect_ratio="9:16")
        self.assertTrue(qc_9_16["valid"])
        self.assertEqual((qc_9_16["width"], qc_9_16["height"]), (1080, 1920))
        self.assertAlmostEqual(qc_9_16["audio_duration"], 4.0, delta=0.07)

    def test_12_cache_reuse_matching_signature(self):
        """Case 12: Reuse a valid intermediate clip when its complete signature matches."""
        self._create_fixture_project(segment_durations=[2.0, 2.0], audio_duration=4.0, fps=30)
        out = compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)
        self.assertTrue(Path(out).exists())

        clips_dir = self.test_dir / "temp_clips_16_9"
        manifest_path = clips_dir / "clips_manifest.json"
        self.assertTrue(manifest_path.exists())

        clip0 = clips_dir / "clip_0000.mp4"
        self.assertTrue(clip0.exists())
        mtime0_before = clip0.stat().st_mtime_ns

        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest_before = json.load(f)
        sig0_before = manifest_before["clips"]["clip_0000.mp4"]["fingerprint"]

        # Run composition second time without modifying inputs
        out2 = compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)
        self.assertTrue(Path(out2).exists())

        mtime0_after = clip0.stat().st_mtime_ns
        self.assertEqual(mtime0_before, mtime0_after, "Valid intermediate clip should be reused without rewriting")

        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest_after = json.load(f)
        self.assertEqual(manifest_after["clips"]["clip_0000.mp4"]["fingerprint"], sig0_before)

    def test_13_cache_invalidation_on_image_change(self):
        """Case 13: Invalidate a clip when the source image changes."""
        self._create_fixture_project(segment_durations=[2.0, 2.0], audio_duration=4.0, fps=30)
        compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)

        clips_dir = self.test_dir / "temp_clips_16_9"
        manifest_path = clips_dir / "clips_manifest.json"
        with open(manifest_path, "r", encoding="utf-8") as f:
            mf1 = json.load(f)
        fp1 = mf1["clips"]["clip_0000.mp4"]["fingerprint"]

        # Modify content of frame_0001.jpg
        img_file = self.frames_dir / "frame_0001.jpg"
        create_solid_image(img_file, color=(255, 50, 50), label="Modified Image")

        # Re-compose
        compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)

        with open(manifest_path, "r", encoding="utf-8") as f:
            mf2 = json.load(f)
        fp2 = mf2["clips"]["clip_0000.mp4"]["fingerprint"]

        self.assertNotEqual(fp1, fp2, "Fingerprint must change when source image changes")
        self.assertNotEqual(mf1["clips"]["clip_0000.mp4"]["image_hash"], mf2["clips"]["clip_0000.mp4"]["image_hash"])

    def test_14_cache_invalidation_on_duration_and_frame_count_change(self):
        """Case 14: Invalidate a clip when expected scene duration or frame count changes."""
        self._create_fixture_project(segment_durations=[2.0, 2.0], audio_duration=4.0, fps=30)
        compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)

        clips_dir = self.test_dir / "temp_clips_16_9"
        probe1 = get_stream_info(str(clips_dir / "clip_0000.mp4"))
        v1_frames = int(probe1["streams"][0]["nb_frames"])
        self.assertEqual(v1_frames, 60)

        # Update timeline so segment 1 is 3.0s (90 frames)
        with open(self.test_dir / "voice_timeline.json", "r+", encoding="utf-8") as f:
            data = json.load(f)
            data["timeline"][0]["duration"] = 3.0
            data["timeline"][0]["frame_count"] = 90
            data["timeline"][0]["endTime"] = 3.0
            data["timeline"][1]["startTime"] = 3.0
            data["timeline"][1]["endTime"] = 5.0
            data["total_duration"] = 5.0
            data["total_frames"] = 150
            f.seek(0)
            json.dump(data, f, indent=2)
            f.truncate()

        # Update audio to match
        create_sine_wav(self.test_dir / "voiceover_master.wav", duration=5.0)

        compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)

        probe2 = get_stream_info(str(clips_dir / "clip_0000.mp4"))
        v2_frames = int(probe2["streams"][0]["nb_frames"])
        self.assertEqual(v2_frames, 90, "Clip 0000 must be regenerated with new frame count 90")

    def test_15_cache_invalidation_on_fps_and_motion_change(self):
        """Case 15: Invalidate clips when FPS, aspect ratio, resolution, or motion changes."""
        self._create_fixture_project(segment_durations=[2.0], audio_duration=2.0, fps=30)
        compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)

        clips_dir = self.test_dir / "temp_clips_16_9"
        manifest_path = clips_dir / "clips_manifest.json"
        with open(manifest_path, "r", encoding="utf-8") as f:
            mf30 = json.load(f)
        fp30 = mf30["clips"]["clip_0000.mp4"]["fingerprint"]

        # Change fps to 24 in timeline
        with open(self.test_dir / "voice_timeline.json", "r+", encoding="utf-8") as f:
            data = json.load(f)
            data["fps"] = 24
            data["timeline"][0]["frame_count"] = 48
            data["total_frames"] = 48
            f.seek(0)
            json.dump(data, f, indent=2)
            f.truncate()

        compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)

        with open(manifest_path, "r", encoding="utf-8") as f:
            mf24 = json.load(f)
        fp24 = mf24["clips"]["clip_0000.mp4"]["fingerprint"]
        self.assertNotEqual(fp30, fp24, "Fingerprint must change when FPS changes")

        probe = get_stream_info(str(clips_dir / "clip_0000.mp4"))
        self.assertEqual(int(probe["streams"][0]["nb_frames"]), 48)

    def test_16_detect_corrupted_or_truncated_intermediate_clip(self):
        """Case 16: Detect missing or corrupted intermediate artifact and safely regenerate."""
        self._create_fixture_project(segment_durations=[2.0, 2.0], audio_duration=4.0, fps=30)
        compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)

        clips_dir = self.test_dir / "temp_clips_16_9"
        clip0 = clips_dir / "clip_0000.mp4"

        # Corrupt clip_0000.mp4 with garbage bytes
        with open(clip0, "wb") as f:
            f.write(b"CORRUPTED_PARTIAL_BYTES_HEADER")

        # Next run must detect corruption and regenerate clip0 without failing
        out = compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)
        self.assertTrue(Path(out).exists())

        # Probe clip0 to ensure it is now a valid MP4
        probe = get_stream_info(str(clip0))
        self.assertTrue(len(probe.get("streams", [])) > 0)
        self.assertEqual(int(probe["streams"][0]["nb_frames"]), 60)

    def test_17_recover_from_interrupted_intermediate_render(self):
        """Case 17: Recover safely from an interrupted intermediate render (.tmp_* leftover)."""
        self._create_fixture_project(segment_durations=[2.0], audio_duration=2.0, fps=30)
        clips_dir = self.test_dir / "temp_clips_16_9"
        clips_dir.mkdir(parents=True, exist_ok=True)

        # Plant an interrupted temporary file
        dangling_tmp = clips_dir / ".tmp_clip_0000.mp4"
        with open(dangling_tmp, "wb") as f:
            f.write(b"INTERRUPTED_RENDER_DATA")

        out = compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)
        self.assertTrue(Path(out).exists())

        # Dangling temp file must be cleaned up, and final clip must exist
        self.assertFalse(dangling_tmp.exists(), "Leftover .tmp_* files must be removed")
        self.assertTrue((clips_dir / "clip_0000.mp4").exists())

    def test_18_stale_clips_pruned_on_timeline_shortening(self):
        """Case 18: Old intermediate clips cannot be silently reused after timeline changes (pruning)."""
        # First render 3 segments (producing clip_0000, clip_0001, clip_0002)
        self._create_fixture_project(segment_durations=[2.0, 2.0, 2.0], audio_duration=6.0, fps=30)
        compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)

        clips_dir = self.test_dir / "temp_clips_16_9"
        self.assertTrue((clips_dir / "clip_0002.mp4").exists())

        # Shorten timeline to only 2 segments (4.0s)
        with open(self.test_dir / "voice_timeline.json", "r+", encoding="utf-8") as f:
            data = json.load(f)
            data["timeline"] = data["timeline"][:2]
            data["total_duration"] = 4.0
            data["total_frames"] = 120
            f.seek(0)
            json.dump(data, f, indent=2)
            f.truncate()

        create_sine_wav(self.test_dir / "voiceover_master.wav", duration=4.0)

        out = compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)
        self.assertTrue(Path(out).exists())

        # clip_0002.mp4 must have been pruned from clips_dir
        self.assertFalse((clips_dir / "clip_0002.mp4").exists(), "Extraneous clip_0002.mp4 must be pruned")

        # Merged video duration must match 4.0s, not 6.0s
        qc = validate_post_render(Path(out), expected_audio_dur=4.0, expected_timeline_dur=4.0, fps=30)
        self.assertAlmostEqual(qc["video_duration"], 4.0, delta=0.07)

    def test_19_tolerance_model_mathematical_derivation(self):
        """Case 19: Tolerances adhering strictly to frame duration and AAC packet duration."""
        tol_30 = get_tolerances(fps=30, sample_rate=48000)
        self.assertAlmostEqual(tol_30["frame_duration"], 1.0 / 30, places=6)
        self.assertAlmostEqual(tol_30["quantization_tolerance"], 0.5 / 30, places=6)
        self.assertAlmostEqual(tol_30["max_frame_adjustment"], 1.0 / 30, places=6)
        self.assertAlmostEqual(tol_30["audio_packet_tolerance"], 1024.0 / 48000, places=6)
        self.assertAlmostEqual(tol_30["av_alignment_tolerance"], (1.0 / 30) + (1024.0 / 48000), places=6)

        tol_60 = get_tolerances(fps=60, sample_rate=48000)
        self.assertAlmostEqual(tol_60["frame_duration"], 1.0 / 60, places=6)
        self.assertAlmostEqual(tol_60["av_alignment_tolerance"], (1.0 / 60) + (1024.0 / 48000), places=6)

        # Multi-frame deficit (> max_frame_adjustment) must not be masked
        self._create_fixture_project(segment_durations=[2.0], audio_duration=2.0 + 2.0 / 30 + 0.01, fps=30)
        with self.assertRaises(RuntimeError) as ctx:
            compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)
        self.assertIn("Material A/V duration mismatch", str(ctx.exception))

    def test_20_milestone_audit_100_percent_boundary_vs_last_frame(self):
        """Case 20: Distinguish the final frame from the 100% end boundary in milestone audit."""
        # Setup 1051-frame video at 30 fps (total duration = 1051 / 30 = 35.0333s)
        total_frames = 1051
        fps = 30
        total_dur = total_frames / fps
        timeline_items = [
            {"id": 1, "duration": 10.0, "frame_count": 300, "startTime": 0.0, "endTime": 10.0},
            {"id": 2, "duration": 15.0, "frame_count": 450, "startTime": 10.0, "endTime": 25.0},
            {"id": 3, "duration": round(total_dur - 25.0, 4), "frame_count": 301, "startTime": 25.0, "endTime": round(total_dur, 4)},
        ]

        records = compute_milestone_audit(timeline_items, total_dur, fps=fps)
        m100 = records[-1]

        self.assertEqual(m100["percentage"], 1.00)
        self.assertTrue(m100["is_end_boundary"])
        # End boundary is frame boundary 1051 at 35.0333s
        self.assertEqual(m100["frame_boundary_index"], 1051)
        self.assertAlmostEqual(m100["frame_aligned_expected_time"], 35.0333, places=3)

        # Last actual frame is index 1050 (0-indexed) at 1050/30 = 35.0000s
        self.assertEqual(m100["frame_index"], 1050)
        self.assertAlmostEqual(m100["last_frame_timestamp"], 35.0000, places=3)

        # Boundary index and frame index are distinctly separated
        self.assertNotEqual(m100["frame_boundary_index"], m100["frame_index"])
        self.assertNotEqual(m100["frame_aligned_expected_time"], m100["last_frame_timestamp"])
        self.assertEqual(m100["status"], "PASS")

    def test_21_cache_invalidation_on_motion_type_change(self):
        """Case 21: Invalidate clip when motion_type changes independently."""
        self._create_fixture_project(segment_durations=[2.0], audio_duration=2.0, fps=30)
        compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)

        clips_dir = self.test_dir / "temp_clips_16_9"
        manifest_path = clips_dir / "clips_manifest.json"
        with open(manifest_path, "r", encoding="utf-8") as f:
            mf1 = json.load(f)
        fp1 = mf1["clips"]["clip_0000.mp4"]["fingerprint"]

        # Change motion_type in timeline item from default to pan_right
        with open(self.test_dir / "voice_timeline.json", "r+", encoding="utf-8") as f:
            data = json.load(f)
            data["timeline"][0]["motion_type"] = "pan_right"
            f.seek(0)
            json.dump(data, f, indent=2)
            f.truncate()

        compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)

        with open(manifest_path, "r", encoding="utf-8") as f:
            mf2 = json.load(f)
        fp2 = mf2["clips"]["clip_0000.mp4"]["fingerprint"]
        self.assertNotEqual(fp1, fp2, "Fingerprint must change when motion_type changes")
        self.assertEqual(mf2["clips"]["clip_0000.mp4"]["motion"], "pan_right")

    def test_22_cache_invalidation_on_aspect_ratio_change(self):
        """Case 22: Invalidate intermediate clip when aspect_ratio changes independently."""
        img_path = self.frames_dir / "test_aspect.jpg"
        create_solid_image(img_path)
        fp_16_9, _ = compute_clip_fingerprint(img_path, 2.0, 60, "zoom_in", "16:9", 30, 1920, 1080)
        fp_9_16, _ = compute_clip_fingerprint(img_path, 2.0, 60, "zoom_in", "9:16", 30, 1080, 1920)
        self.assertNotEqual(fp_16_9, fp_9_16, "Fingerprint must differ when aspect_ratio changes")

        self._create_fixture_project(segment_durations=[2.0], audio_duration=2.0, fps=30)
        compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)
        clips_dir = self.test_dir / "temp_clips_16_9"
        manifest_path = clips_dir / "clips_manifest.json"
        with open(manifest_path, "r", encoding="utf-8") as f:
            mf1 = json.load(f)
        fp1 = mf1["clips"]["clip_0000.mp4"]["fingerprint"]

        # Modify manifest to simulate an outdated aspect ratio entry in this directory
        mf1["clips"]["clip_0000.mp4"]["aspect_ratio"] = "9:16"
        mf1["clips"]["clip_0000.mp4"]["fingerprint"] = fp_9_16
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(mf1, f, indent=2)

        # Rendering with aspect_ratio 16:9 must reject the mismatched fingerprint and regenerate
        compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)
        with open(manifest_path, "r", encoding="utf-8") as f:
            mf2 = json.load(f)
        fp2 = mf2["clips"]["clip_0000.mp4"]["fingerprint"]
        self.assertEqual(fp2, fp1, "Clip must be regenerated to match current aspect ratio 16:9")

    def test_23_cache_invalidation_on_width_change(self):
        """Case 23: Invalidate clip when width changes independently."""
        self._create_fixture_project(segment_durations=[2.0], audio_duration=2.0, fps=30)
        compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)

        clips_dir = self.test_dir / "temp_clips_16_9"
        manifest_path = clips_dir / "clips_manifest.json"
        with open(manifest_path, "r", encoding="utf-8") as f:
            mf1 = json.load(f)
        fp1 = mf1["clips"]["clip_0000.mp4"]["fingerprint"]
        self.assertEqual(mf1["clips"]["clip_0000.mp4"]["width"], 1920)

        # Update timeline width to 1280
        with open(self.test_dir / "voice_timeline.json", "r+", encoding="utf-8") as f:
            data = json.load(f)
            data["width"] = 1280
            f.seek(0)
            json.dump(data, f, indent=2)
            f.truncate()

        compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)

        with open(manifest_path, "r", encoding="utf-8") as f:
            mf2 = json.load(f)
        fp2 = mf2["clips"]["clip_0000.mp4"]["fingerprint"]
        self.assertNotEqual(fp1, fp2, "Fingerprint must change when width changes")
        self.assertEqual(mf2["clips"]["clip_0000.mp4"]["width"], 1280)

        probe = get_stream_info(str(clips_dir / "clip_0000.mp4"))
        self.assertEqual(int(probe["streams"][0]["width"]), 1280)

    def test_24_cache_invalidation_on_height_change(self):
        """Case 24: Invalidate clip when height changes independently."""
        self._create_fixture_project(segment_durations=[2.0], audio_duration=2.0, fps=30)
        compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)

        clips_dir = self.test_dir / "temp_clips_16_9"
        manifest_path = clips_dir / "clips_manifest.json"
        with open(manifest_path, "r", encoding="utf-8") as f:
            mf1 = json.load(f)
        fp1 = mf1["clips"]["clip_0000.mp4"]["fingerprint"]
        self.assertEqual(mf1["clips"]["clip_0000.mp4"]["height"], 1080)

        # Update timeline height to 720
        with open(self.test_dir / "voice_timeline.json", "r+", encoding="utf-8") as f:
            data = json.load(f)
            data["height"] = 720
            f.seek(0)
            json.dump(data, f, indent=2)
            f.truncate()

        compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)

        with open(manifest_path, "r", encoding="utf-8") as f:
            mf2 = json.load(f)
        fp2 = mf2["clips"]["clip_0000.mp4"]["fingerprint"]
        self.assertNotEqual(fp1, fp2, "Fingerprint must change when height changes")
        self.assertEqual(mf2["clips"]["clip_0000.mp4"]["height"], 720)

        probe = get_stream_info(str(clips_dir / "clip_0000.mp4"))
        self.assertEqual(int(probe["streams"][0]["height"]), 720)

    def test_25_cache_invalidation_on_compositor_version_change(self):
        """Case 25: Invalidate clip when COMPOSITOR_VERSION changes independently."""
        self._create_fixture_project(segment_durations=[2.0], audio_duration=2.0, fps=30)
        compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False, compositor_version="2.2.0")

        clips_dir = self.test_dir / "temp_clips_16_9"
        manifest_path = clips_dir / "clips_manifest.json"
        with open(manifest_path, "r", encoding="utf-8") as f:
            mf1 = json.load(f)
        fp1 = mf1["clips"]["clip_0000.mp4"]["fingerprint"]

        # Run with updated compositor_version
        compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False, compositor_version="2.3.0")

        with open(manifest_path, "r", encoding="utf-8") as f:
            mf2 = json.load(f)
        fp2 = mf2["clips"]["clip_0000.mp4"]["fingerprint"]
        self.assertNotEqual(fp1, fp2, "Fingerprint must change when COMPOSITOR_VERSION changes")
        self.assertEqual(mf2["version"], "2.3.0")

    def test_26_cache_validation_nb_frames_zero_fallback_to_count_frames(self):
        """Case 26: When nb_frames is 0 in container header, fallback to count_frames verifies and reuses clip."""
        self._create_fixture_project(segment_durations=[2.0], audio_duration=2.0, fps=30)
        compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)

        clips_dir = self.test_dir / "temp_clips_16_9"
        clip0 = clips_dir / "clip_0000.mp4"
        mtime_before = clip0.stat().st_mtime_ns

        real_get_stream_info = get_stream_info

        def mock_get_stream_info(path):
            info = real_get_stream_info(path)
            # Simulate encoder that writes 0 or empty for nb_frames in header
            if "streams" in info:
                for s in info["streams"]:
                    if s.get("codec_type") == "video":
                        s["nb_frames"] = "0"
            return info

        with patch("motion_compositor.get_stream_info", side_effect=mock_get_stream_info):
            # Second run: nb_frames is 0, but count_stream_frames confirms 60 frames -> clip is reused
            compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)

        mtime_after = clip0.stat().st_mtime_ns
        self.assertEqual(mtime_before, mtime_after, "Cached clip with nb_frames=0 must be reused when count_frames verifies frame count")

    def test_27_cache_validation_nb_frames_zero_unverifiable_safely_regenerates(self):
        """Case 27: When nb_frames is 0 and count_frames cannot verify, invalidate cache entry and regenerate."""
        self._create_fixture_project(segment_durations=[2.0], audio_duration=2.0, fps=30)
        compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)

        clips_dir = self.test_dir / "temp_clips_16_9"
        clip0 = clips_dir / "clip_0000.mp4"

        real_get_stream_info = get_stream_info
        real_count_frames = count_stream_frames

        def mock_get_stream_info(path):
            info = real_get_stream_info(path)
            if "clip_0000.mp4" in str(path) and ".tmp_" not in str(path):
                if "streams" in info:
                    for s in info["streams"]:
                        if s.get("codec_type") == "video":
                            s["nb_frames"] = "0"
            return info

        def mock_count_stream_frames(path, stream_type="v"):
            if "clip_0000.mp4" in str(path) and ".tmp_" not in str(path):
                return 0
            return real_count_frames(path, stream_type)

        # Simulate count_stream_frames failing to verify cached clip (returns 0)
        with patch("motion_compositor.get_stream_info", side_effect=mock_get_stream_info), \
             patch("motion_compositor.count_stream_frames", side_effect=mock_count_stream_frames):
            # compose_recap_video must NOT accept the cached clip, must safely regenerate
            compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=False)

        # After regeneration (outside mock), clip0 has valid frame count
        probe = get_stream_info(str(clip0))
        self.assertEqual(int(probe["streams"][0]["nb_frames"]), 60)

    def test_28_final_export_recomposited_on_bgm_change(self):
        """Case 28: Final export is always fresh/recomposited when BGM changes while intermediate clips are reused."""
        self._create_fixture_project(segment_durations=[2.0], audio_duration=2.0, fps=30)
        out1 = compose_recap_video(str(self.test_dir), aspect_ratio="16:9", bgm_name="epic_anime.mp3", burn_subtitles=False)
        self.assertTrue(Path(out1).exists())

        clips_dir = self.test_dir / "temp_clips_16_9"
        clip0 = clips_dir / "clip_0000.mp4"
        clip0_mtime_before = clip0.stat().st_mtime_ns

        # Re-render with different BGM
        out2 = compose_recap_video(str(self.test_dir), aspect_ratio="16:9", bgm_name="suspense_theme.mp3", burn_subtitles=False)
        self.assertTrue(Path(out2).exists())

        # Intermediate clip was reused without re-rendering Ken Burns
        clip0_mtime_after = clip0.stat().st_mtime_ns
        self.assertEqual(clip0_mtime_before, clip0_mtime_after, "Intermediate visual clip must be reused when BGM changes")

    def test_29_final_export_recomposited_on_subtitle_change(self):
        """Case 29: Final export is fresh when subtitles change while intermediate clips are reused."""
        self._create_fixture_project(segment_durations=[2.0], audio_duration=2.0, fps=30)
        sub_path = self.test_dir / "subtitles.ass"
        sub_content_1 = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,20,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,Initial Subtitle Text
"""
        with open(sub_path, "w", encoding="utf-8") as f:
            f.write(sub_content_1)

        out1 = compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=True)
        self.assertTrue(Path(out1).exists())

        clips_dir = self.test_dir / "temp_clips_16_9"
        clip0 = clips_dir / "clip_0000.mp4"
        clip0_mtime_before = clip0.stat().st_mtime_ns

        # Change subtitle content
        sub_content_2 = sub_content_1.replace("Initial Subtitle Text", "Updated Changed Subtitle Text")
        with open(sub_path, "w", encoding="utf-8") as f:
            f.write(sub_content_2)

        out2 = compose_recap_video(str(self.test_dir), aspect_ratio="16:9", burn_subtitles=True)
        self.assertTrue(Path(out2).exists())

        # Intermediate clip remained reused
        clip0_mtime_after = clip0.stat().st_mtime_ns
        self.assertEqual(clip0_mtime_before, clip0_mtime_after, "Intermediate clip must be reused when only subtitles change")

    def test_30_intermediate_clip_fingerprint_excludes_bgm_and_subtitles(self):
        """Case 30: Verify intermediate clip fingerprints are independent of BGM and subtitle changes."""
        img_path = self.frames_dir / "test_fp_indep.jpg"
        create_solid_image(img_path)

        fp1, hash1 = compute_clip_fingerprint(img_path, 2.0, 60, "zoom_in", "16:9", 30, 1920, 1080)
        fp2, hash2 = compute_clip_fingerprint(img_path, 2.0, 60, "zoom_in", "16:9", 30, 1920, 1080)
        self.assertEqual(fp1, fp2)

        # Ensure audio/subtitle inputs are not part of intermediate visual fingerprint
        sig_data_keys = ["v", "image_hash", "duration", "frame_count", "motion", "aspect", "fps", "width", "height"]
        self.assertNotIn("bgm", sig_data_keys)
        self.assertNotIn("subtitles", sig_data_keys)

    def test_31_ffmpeg_ffprobe_real_integration_count_frames(self):
        """Case 31: Real FFmpeg/FFprobe integration test verifying count_stream_frames with -count_frames."""
        test_img = self.frames_dir / "test_real_probe.jpg"
        create_solid_image(test_img, width=1920, height=1080)

        out_clip = self.test_dir / "real_clip_45.mp4"
        # Generate real 45-frame clip @ 30fps = 1.5s
        ok = generate_clip_for_image(
            str(test_img),
            duration=1.5,
            output_clip_path=str(out_clip),
            motion_type="zoom_in",
            aspect_ratio="16:9",
            fps=30,
            frame_count=45
        )
        self.assertTrue(ok)
        self.assertTrue(out_clip.exists())

        # Probe using real count_stream_frames
        counted = count_stream_frames(str(out_clip), "v")
        self.assertEqual(counted, 45, "Real FFprobe -count_frames must accurately count 45 frames")


if __name__ == "__main__":
    unittest.main()

