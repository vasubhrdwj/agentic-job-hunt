"""Generate premium narration locally with Kokoro neural TTS."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from kokoro_onnx import Kokoro
import soundfile as sf


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "demo" / "video" / "narration.json"
OUTPUT_DIR = ROOT / "demo" / "video" / "build" / "audio"
MODEL_PATH = ROOT / "demo" / "video" / "models" / "kokoro-v1.0.fp16.onnx"
VOICES_PATH = ROOT / "demo" / "video" / "models" / "voices-v1.0.bin"
VOICE = os.getenv("KOKORO_TTS_VOICE", "am_michael")
SPEED = float(os.getenv("KOKORO_TTS_SPEED", "1.02"))


def main() -> None:
    beats = json.loads(SCRIPT_PATH.read_text(encoding="utf-8"))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    kokoro = Kokoro(str(MODEL_PATH), str(VOICES_PATH))

    for beat in beats:
        samples, sample_rate = kokoro.create(
            beat["text"],
            voice=VOICE,
            speed=SPEED,
            lang="en-us",
        )
        raw = OUTPUT_DIR / f"{beat['id']}-raw.wav"
        output = OUTPUT_DIR / f"{beat['id']}.wav"
        sf.write(raw, samples, sample_rate)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(raw),
                "-af",
                (
                    "highpass=f=65,lowpass=f=15500,"
                    "acompressor=threshold=-18dB:ratio=2:attack=12:release=160"
                ),
                "-ar",
                "48000",
                str(output),
            ],
            check=True,
        )
        print(f"Wrote {output} with local Kokoro voice {VOICE}")


if __name__ == "__main__":
    main()
