import os
import shutil
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if sys.stdout:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr:
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR = Path(__file__).resolve().parent.parent
CORE_DIR = BASE_DIR / "core"
SERVER_DIR = BASE_DIR / "server"
WEB_DIR = BASE_DIR / "web"
ASSETS_DIR = BASE_DIR / "assets"
PROJECTS_DIR = BASE_DIR / "projects"

BGM_DIR = ASSETS_DIR / "bgm"
SFX_DIR = ASSETS_DIR / "sfx"
FONTS_DIR = ASSETS_DIR / "fonts"
BORDERS_DIR = ASSETS_DIR / "borders"

for d in [PROJECTS_DIR, ASSETS_DIR, BGM_DIR, SFX_DIR, FONTS_DIR, BORDERS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Find FFmpeg binary
WINGET_FFMPEG = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages/Gyan.FFmpeg.Essentials_Microsoft.Winget.Source_8wekyb3d8bbwe/ffmpeg-8.1.1-essentials_build/bin/ffmpeg.exe"
WINGET_FFPROBE = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages/Gyan.FFmpeg.Essentials_Microsoft.Winget.Source_8wekyb3d8bbwe/ffmpeg-8.1.1-essentials_build/bin/ffprobe.exe"

if WINGET_FFMPEG.exists():
    FFMPEG_BIN = str(WINGET_FFMPEG)
    FFPROBE_BIN = str(WINGET_FFPROBE)
else:
    FFMPEG_BIN = shutil.which("ffmpeg") or "ffmpeg"
    FFPROBE_BIN = shutil.which("ffprobe") or "ffprobe"

PYTHON_BIN = sys.executable

# Default Thai Voices
VOICES = {
    "niwat": "th-TH-NiwatNeural",        # Male narrative, energetic
    "premwadee": "th-TH-PremwadeeNeural" # Female clear, expressive
}

DEFAULT_VOICE = VOICES["niwat"]
