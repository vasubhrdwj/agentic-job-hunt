"""Synthesize a subtle ambient pad for the demo — original, royalty-free, generated.

A slow Am–F–C–G progression of warm, softly-detuned pads with crossfaded chords and
a quiet octave shimmer. Deliberately low and unobtrusive so narration sits on top
without fighting it. Written to build/audio/pad_raw.wav; master() masters it to a
quiet bed (build/audio/bed.wav) that will not distort when speech is mixed over it.

Usage:
  .venv/bin/python film/make_music.py           # -> build/audio/bed.wav (~154s)
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import wave

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "build" / "audio"
SR = 48000
DUR = 158.0
SEG = 8.0            # seconds per chord
CF = 1.8            # crossfade seconds between chords

PROG = {
    "Am": [110.00, 164.81, 220.00, 261.63, 329.63],
    "F":  [87.31, 174.61, 220.00, 261.63, 349.23],
    "C":  [130.81, 196.00, 261.63, 329.63, 392.00],
    "G":  [98.00, 196.00, 246.94, 293.66, 392.00],
}
ORDER = ["Am", "F", "C", "G"]
HARMONICS = [(1, 1.0), (2, 0.26), (3, 0.11), (4, 0.045)]   # warm, slightly saw-ish


def note_wave(t: np.ndarray, f: float, detune: float, phase: float) -> np.ndarray:
    w = np.zeros_like(t)
    for k, a in HARMONICS:
        w += a * np.sin(2 * np.pi * f * k * detune * t + phase * k)
    return w


def synthesize() -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    n = int(DUR * SR)
    left = np.zeros(n)
    right = np.zeros(n)
    n_chords = int(DUR / SEG) + 2
    for i in range(n_chords):
        start = i * SEG
        freqs = PROG[ORDER[i % len(ORDER)]]
        a0 = int(max(0.0, start) * SR)
        a1 = int(min(DUR, start + SEG + CF) * SR)
        if a1 <= a0:
            continue
        tt = np.arange(a0, a1) / SR
        local = tt - start
        # trapezoid crossfade envelope, smoothed
        env = np.clip(local / CF, 0, 1) * np.clip((SEG + CF - local) / CF, 0, 1)
        env = np.clip(env, 0, 1)
        env = env * env * (3 - 2 * env)
        cl = np.zeros_like(tt)
        cr = np.zeros_like(tt)
        for j, f in enumerate(freqs):
            ph = j * 0.7
            cl += note_wave(tt, f, 0.9997, ph)
            cr += note_wave(tt, f, 1.0003, ph + 0.35)
        top = freqs[-1] * 2.0                       # quiet octave shimmer
        cl += 0.055 * np.sin(2 * np.pi * top * 0.9997 * tt)
        cr += 0.055 * np.sin(2 * np.pi * top * 1.0003 * tt + 0.2)
        left[a0:a1] += cl * env
        right[a0:a1] += cr * env

    tg = np.arange(n) / SR
    trem = 1 + 0.045 * np.sin(2 * np.pi * 0.075 * tg)   # slow breathing
    left *= trem
    right *= trem

    fin, fout = int(3.0 * SR), int(5.0 * SR)
    g = np.ones(n)
    g[:fin] = np.linspace(0, 1, fin) ** 2
    g[-fout:] = np.linspace(1, 0, fout) ** 2
    left *= g
    right *= g

    peak = max(np.max(np.abs(left)), np.max(np.abs(right)), 1e-9)
    scale = 0.5 / peak
    stereo = np.stack([left * scale, right * scale], axis=1)
    pcm = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
    raw = OUT / "pad_raw.wav"
    with wave.open(str(raw), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return raw


def master(raw: Path) -> Path:
    """Warm, soften and level the pad to a quiet -20 LUFS bed with true-peak headroom."""
    bed = OUT / "bed.wav"
    chain = (
        "highpass=f=40,"
        "lowpass=f=3400,"
        "aecho=0.85:0.9:120|200|320:0.24|0.15|0.08,"
        "lowpass=f=4000,"
        "loudnorm=I=-20:TP=-3.0:LRA=9"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw),
         "-af", chain, "-ar", str(SR), str(bed)],
        check=True,
    )
    return bed


if __name__ == "__main__":
    bed = master(synthesize())
    print(f"Wrote {bed}")
