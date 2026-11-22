"""Find likely speech in the mono audio track with a lightweight voiced detector."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from shotforge.checks import FAIL, PASS, WARN, Finding
from shotforge.models import Shot
from shotforge.tools import ToolError, require_tool

RATE = 8000
WINDOW = 320  # 40 ms
HOP = 80  # 10 ms
MIN_SPEECH_S = 0.12
MAX_GAP_S = 0.09
TAIL_S = 0.07
SILENCE = "speech during silence"
CUTOFF = "dialogue cutoff"


def _read_audio(video: Path, duration: float) -> NDArray:
    ffprobe = require_tool("ffprobe")
    stream = subprocess.run(
        [
            str(ffprobe), "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=index", "-of", "csv=p=0", str(video),
        ],
        capture_output=True, check=True, timeout=30,
    )
    if not stream.stdout.strip():
        return np.empty(0, dtype=np.float32)

    ffmpeg = require_tool("ffmpeg")
    pcm = subprocess.run(
        [
            str(ffmpeg), "-nostdin", "-v", "error", "-i", str(video), "-map", "0:a:0",
            "-vn", "-t", str(duration), "-ac", "1", "-af", f"aresample={RATE}:async=1:first_pts=0",
            "-c:a", "pcm_f32le", "-f", "f32le", "pipe:1",
        ],
        capture_output=True, check=True, timeout=60,
    )
    if not pcm.stdout:
        raise ToolError("the audio track yielded no samples")
    return np.frombuffer(pcm.stdout, dtype="<f4")


def _voice_spans(samples: NDArray) -> list[tuple[float, float]]:
    if len(samples) < WINDOW:
        return []
    frames = np.lib.stride_tricks.sliding_window_view(samples, WINDOW)[::HOP]
    frames = frames - frames.mean(axis=1, keepdims=True)
    rms = np.sqrt(np.mean(frames**2, axis=1))
    floor = max(0.006, min(0.02, np.quantile(rms, 0.2) * 2.5))

    # Voiced syllables are periodic and carry energy across multiple frequencies.
    # Keep persistent tones, low-frequency rumble and broadband noise out of the mask.
    spectrum = np.abs(np.fft.rfft(frames * np.hanning(WINDOW), n=512)) ** 2
    bins = np.fft.rfftfreq(512, d=1 / RATE)
    speech_band = spectrum[:, (bins >= 250) & (bins <= 3500)].sum(axis=1)
    total = spectrum.sum(axis=1)
    pitch = np.fft.irfft(spectrum, n=512)[:, :115]
    periodic = pitch[:, 20:].max(axis=1) > 0.3 * pitch[:, 0]
    strongest_band = np.lib.stride_tricks.sliding_window_view(spectrum, 5, axis=1).sum(axis=2)
    spread = strongest_band.max(axis=1) < 0.8 * total
    voiced = (rms >= floor) & (speech_band > 0.15 * total) & spread & periodic
    indices = np.flatnonzero(voiced)
    groups = np.split(indices, np.flatnonzero(np.diff(indices) * HOP / RATE > MAX_GAP_S) + 1)
    return [
        (group[0] * HOP / RATE, (group[-1] * HOP + WINDOW) / RATE)
        for group in groups
        if len(group) * HOP / RATE >= MIN_SPEECH_S
    ]


def check_speech(video: Path, shot: Shot, duration: float) -> list[Finding]:
    try:
        samples = _read_audio(video, duration)
    except (ToolError, OSError, subprocess.SubprocessError, ValueError):
        return [
            Finding(SILENCE, WARN, "could not read the audio track"),
            Finding(CUTOFF, WARN, "could not read the audio track"),
        ]
    if not len(samples):
        cutoff = (
            Finding(CUTOFF, WARN, "clip has no audio track and the spec calls for dialogue")
            if any(beat.line for beat in shot.beats)
            else Finding(CUTOFF, PASS, "clip has no audio track")
        )
        return [Finding(SILENCE, PASS, "clip has no audio track"), cutoff]

    spans = _voice_spans(samples)
    findings = []
    for beat in shot.beats:
        if not beat.silence:
            continue
        start, end = beat.t
        overlap = sum(max(0, min(b, end) - max(a, start)) for a, b in spans)
        if overlap >= MIN_SPEECH_S:
            findings.append(
                Finding(
                    SILENCE, FAIL,
                    f"likely speech during the {start:g}–{end:g}s silent beat"
                    f" ({overlap:.2f}s overlap)",
                    fix="regenerate or remove dialogue from the silent beat in the edit",
                )
            )
    if not findings:
        message = (
            "no sustained speech detected in silent beats"
            if any(beat.silence for beat in shot.beats)
            else "spec has no silent beats"
        )
        findings.append(Finding(SILENCE, PASS, message))
    if spans and duration - spans[-1][1] <= TAIL_S:
        findings.append(
            Finding(
                CUTOFF, WARN, f"likely speech reaches the cut at {duration:.2f}s",
                fix="listen to the cut and leave room for the line to finish",
            )
        )
    else:
        findings.append(Finding(CUTOFF, PASS, "no sustained speech detected at the cut"))
    return findings
