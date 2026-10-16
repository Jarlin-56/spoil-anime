import subprocess
from pathlib import Path
from config import FFMPEG_BIN, BGM_DIR, SFX_DIR

def ensure_audio_assets():
    """Generates starter royalty-free BGM and SFX if not present."""
    epic_bgm = BGM_DIR / "epic_anime.mp3"
    if not epic_bgm.exists():
        print("[*] Generating cinematic starter BGM (epic_anime.mp3)...")
        # Generate 60s ambient cinematic drone with chords
        cmd = [
            FFMPEG_BIN, "-y",
            "-f", "lavfi",
            "-i", "anoisesrc=d=60:c=pink:r=44100:a=0.015",
            "-f", "lavfi",
            "-i", "sine=f=110:d=60",
            "-f", "lavfi",
            "-i", "sine=f=164.81:d=60",
            "-f", "lavfi",
            "-i", "sine=f=220:d=60",
            "-filter_complex",
            "[1:a]volume=0.25[b1]; [2:a]volume=0.2[b2]; [3:a]volume=0.15[b3]; [0:a][b1][b2][b3]amix=inputs=4:duration=longest,lowpass=f=800,aecho=0.8:0.88:60:0.4[aout]",
            "-map", "[aout]",
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            str(epic_bgm)
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    suspense_bgm = BGM_DIR / "suspense_theme.mp3"
    if not suspense_bgm.exists():
        print("[*] Generating suspense starter BGM (suspense_theme.mp3)...")
        cmd = [
            FFMPEG_BIN, "-y",
            "-f", "lavfi",
            "-i", "sine=f=73.42:d=60",
            "-f", "lavfi",
            "-i", "sine=f=146.83:d=60",
            "-filter_complex",
            "[0:a]volume=0.3[b1]; [1:a]volume=0.2[b2]; [b1][b2]amix=inputs=2:duration=longest,lowpass=f=500,tremolo=f=4:d=0.5[aout]",
            "-map", "[aout]",
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            str(suspense_bgm)
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    sfx_whoosh = SFX_DIR / "whoosh.mp3"
    if not sfx_whoosh.exists():
        cmd = [
            FFMPEG_BIN, "-y",
            "-f", "lavfi",
            "-i", "anoisesrc=d=0.6:c=white:r=44100:a=0.3",
            "-filter_complex",
            "[0:a]afade=t=in:ss=0:d=0.2,afade=t=out:st=0.2:d=0.4,bandpass=f=1200:w=800[aout]",
            "-map", "[aout]",
            "-c:a", "libmp3lame",
            str(sfx_whoosh)
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

if __name__ == "__main__":
    ensure_audio_assets()
    print("[+] Audio assets verified!")
