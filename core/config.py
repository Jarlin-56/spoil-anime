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

# Voice Delivery Profiles for Edge-TTS (Thai)
# Maps voice directions to conservative rate and pitch offsets relative to baseline
VOICE_PROFILES = {
    "conversational": {
        "rate_offset": 0,    # +0% (neutral narrative baseline)
        "pitch_offset": 0,   # +0Hz (natural baseline)
        "description": "น้ำเสียงเล่าเรื่องเป็นธรรมชาติ สบายๆ เป็นกันเอง"
    },
    "energetic": {
        "rate_offset": 8,    # +8% (brisk, engaging, battle/action)
        "pitch_offset": 3,   # +3Hz (slightly lifted tone for excitement)
        "description": "น้ำเสียงกระฉับกระเฉง ตื่นเต้น เหมาะกับฉากต่อสู้/ไคลแม็กซ์"
    },
    "surprised": {
        "rate_offset": 4,    # +4% (alert, sudden turn)
        "pitch_offset": 4,   # +4Hz (noticeably raised pitch for shock/reveal)
        "description": "น้ำเสียงประหลาดใจ ตกใจ ตื่นเต้นกับจุดหักมุม"
    },
    "suspenseful": {
        "rate_offset": -4,   # -4% (slightly slowed, deliberate pacing)
        "pitch_offset": -1,  # -1Hz (subtle drop for tension/mystery)
        "description": "น้ำเสียงลุ้นระทึก น่าสงสัย ชวนติดตาม"
    },
    "dramatic": {
        "rate_offset": -4,   # -4% (slower cadence, emotional weight)
        "pitch_offset": -2,  # -2Hz (somber, grounded tone for emotional beats)
        "description": "น้ำเสียงซึ้ง ดราม่า จริงจัง มีน้ำหนักทางอารมณ์"
    },
    "dry_humor": {
        "rate_offset": 2,    # +2% (matter-of-fact, deadpan)
        "pitch_offset": -1,  # -1Hz (lowered monotone inflection for witty remarks)
        "description": "น้ำเสียงตลกหน้าตาย เหน็บแนมเบาๆ อารมณ์ขันเรียบเฉย"
    }
}

DEFAULT_VOICE_PROFILE = "conversational"

# Fallback mapping from legacy action_type to voice_direction
ACTION_TYPE_TO_VOICE_DIRECTION = {
    "normal": "conversational",
    "battle": "energetic",
    "shock": "surprised",
    "drama": "dramatic"
}

def resolve_voice_profile(
    action_type: str = "normal",
    voice_direction: str = None,
    base_rate_str: str = "+5%"
) -> tuple:
    """
    Resolves the effective voice_direction, rate string, and pitch string.
    Ensures safe bounded values and full backward compatibility with legacy action_type.
    Returns: (resolved_direction, effective_rate_str, effective_pitch_str)
    """
    direction = None
    if voice_direction and isinstance(voice_direction, str):
        v_clean = voice_direction.strip().lower()
        if v_clean in VOICE_PROFILES:
            direction = v_clean
    
    if not direction and action_type and isinstance(action_type, str):
        a_clean = action_type.strip().lower()
        direction = ACTION_TYPE_TO_VOICE_DIRECTION.get(a_clean)
        
    if not direction:
        direction = DEFAULT_VOICE_PROFILE
        
    # Parse base rate
    base_rate_val = 5
    if base_rate_str and isinstance(base_rate_str, str):
        try:
            cleaned = base_rate_str.replace("%", "").replace("+", "").strip()
            base_rate_val = int(cleaned)
        except Exception:
            base_rate_val = 5
            
    profile = VOICE_PROFILES[direction]
    
    # Calculate effective rate with conservative bounds (-30% to +60%)
    eff_rate_num = max(-30, min(60, base_rate_val + profile["rate_offset"]))
    eff_rate_str = f"{eff_rate_num:+d}%"
    
    # Calculate effective pitch with conservative bounds (-15Hz to +15Hz)
    eff_pitch_num = max(-15, min(15, profile["pitch_offset"]))
    eff_pitch_str = f"{eff_pitch_num:+d}Hz"
    
    return direction, eff_rate_str, eff_pitch_str

