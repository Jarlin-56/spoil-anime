import asyncio
import json
import os
import re
import sys
from pathlib import Path
import edge_tts

from timing_engine import split_narration_sentences
from config import resolve_voice_profile

# Reconfigure stdout/stderr to utf-8
if sys.stdout:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr:
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

ALLOWED_VOICES = {
    "th-TH-NiwatNeural",
    "th-TH-PremwadeeNeural"
}

RATE_REGEX = re.compile(r"^[+-]?\d{1,3}%$")
PITCH_REGEX = re.compile(r"^[+-]?\d{1,2}Hz$")
MAX_TEXT_LEN = 500
MAX_OUTPUT_BYTES = 10 * 1024 * 1024  # 10 MB

async def generate_preview(text: str, voice: str, rate: str, output_path: str, pitch: str = "+0Hz"):
    sentences = split_narration_sentences(text)
    tts_text = "\n".join(sentences) if sentences else text
    communicate = edge_tts.Communicate(text=tts_text, voice=voice, rate=rate, pitch=pitch)
    await communicate.save(output_path)


def main():
    try:
        raw_input = sys.stdin.read()
        if not raw_input.strip():
            print(json.dumps({"success": False, "error": "No input payload provided"}), file=sys.stderr)
            sys.exit(1)

        payload = json.loads(raw_input)
        text = payload.get("text")
        voice = payload.get("voice", "th-TH-NiwatNeural")
        rate = payload.get("rate", "+5%")
        action_type = payload.get("action_type")
        voice_direction = payload.get("voice_direction")
        pitch = payload.get("pitch")
        output_path = payload.get("output_path")

        if not text or not isinstance(text, str):
            print(json.dumps({"success": False, "error": "Invalid or missing text"}), file=sys.stderr)
            sys.exit(1)

        text = text.strip()
        if len(text) == 0 or len(text) > MAX_TEXT_LEN:
            print(json.dumps({"success": False, "error": f"Text length must be between 1 and {MAX_TEXT_LEN} characters"}), file=sys.stderr)
            sys.exit(1)

        if voice not in ALLOWED_VOICES:
            print(json.dumps({"success": False, "error": "Voice not allowed"}), file=sys.stderr)
            sys.exit(1)

        if not rate or not isinstance(rate, str) or not RATE_REGEX.match(rate):
            print(json.dumps({"success": False, "error": "Invalid speech rate format"}), file=sys.stderr)
            sys.exit(1)

        rate_val = int(rate.replace("%", "").replace("+", ""))
        if rate_val < -50 or rate_val > 100:
            print(json.dumps({"success": False, "error": "Speech rate out of bounds (-50% to +100%)"}), file=sys.stderr)
            sys.exit(1)

        if pitch and (not isinstance(pitch, str) or not PITCH_REGEX.match(pitch)):
            print(json.dumps({"success": False, "error": "Invalid pitch format"}), file=sys.stderr)
            sys.exit(1)

        if not output_path or not isinstance(output_path, str):
            print(json.dumps({"success": False, "error": "Invalid or missing output path"}), file=sys.stderr)
            sys.exit(1)

        # Resolve effective rate and pitch using voice profile
        if pitch:
            effective_rate = rate
            effective_pitch = pitch
        else:
            _, effective_rate, effective_pitch = resolve_voice_profile(
                action_type=action_type or "normal",
                voice_direction=voice_direction,
                base_rate_str=rate
            )

        out_file = Path(output_path).resolve()
        # Ensure parent exists
        out_file.parent.mkdir(parents=True, exist_ok=True)

        asyncio.run(generate_preview(text, voice, effective_rate, str(out_file), pitch=effective_pitch))

        if not out_file.exists() or out_file.stat().st_size == 0:
            print(json.dumps({"success": False, "error": "Generated audio file is empty"}), file=sys.stderr)
            sys.exit(1)

        if out_file.stat().st_size > MAX_OUTPUT_BYTES:
            try:
                out_file.unlink()
            except Exception:
                pass
            print(json.dumps({"success": False, "error": "Generated audio exceeds maximum size limit"}), file=sys.stderr)
            sys.exit(1)

        print(json.dumps({"success": True, "size": out_file.stat().st_size}))
        sys.exit(0)

    except json.JSONDecodeError:
        print(json.dumps({"success": False, "error": "Malformed JSON input"}), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"success": False, "error": "TTS synthesis failed"}), file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
