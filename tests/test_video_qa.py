"""Video QA on short generated clips, with the eyeline engine stubbed."""

from __future__ import annotations

import builtins
import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from shotforge import actions, checks
from shotforge.cli import app
from shotforge.engine import EngineResult
from shotforge.models import Shot
from shotforge.models.shot import Beat
from shotforge.qa import eyeline
from shotforge.yamlio import dump


def test_video_qa_without_the_extra_retains_duration(project, mp4, monkeypatch):
    import_module = builtins.__import__

    def without_qa(name, *args, **kwargs):
        if name in {"shotforge.qa.motion", "shotforge.qa.speech"}:
            raise ImportError("optional QA packages are unavailable")
        return import_module(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_qa)
    findings = checks.check_video_file(
        project, "s01_pub", Shot(id="silent", duration_s=5), mp4(seconds=5), vision=False
    )

    assert [(f.check, f.level) for f in findings] == [
        ("duration", "pass"), ("camera motion", "warn"), ("speech", "warn"),
    ]
    assert all("shotforge[qa]" in f.fix for f in findings if f.level == "warn")


@pytest.mark.parametrize("direct", [False, True])
def test_eyeline_reviews_a_dozen_frames_in_one_call(project, mp4, monkeypatch, direct):
    monkeypatch.setattr(eyeline, "find_tool", lambda *args, **kwargs: Path("fake-claude"))

    def review(engine, skill, inputs, *, cwd, files):
        assert skill.name == "eyeline-qa"
        assert len(files) == 12 and all(path.is_file() for path in files)
        assert all(path.parent == cwd for path in files)
        assert list(inputs["frames"].values()) == [path.name for path in files]
        assert inputs["in_frame"][0]["eyeline"] == "Durik's face"
        return EngineResult(text=json.dumps({"frames": [
            {
                "frame": index,
                "status": "lens_contact" if direct and index == 8 else "clear",
                "evidence": "Gaze relative to the lens.",
            }
            for index in inputs["frames"]
        ]}))

    monkeypatch.setattr(eyeline.ClaudeCodeEngine, "run_skill", review)
    shot = project.shot("s01_pub", "sh01")
    finding = eyeline.check_eyeline(project, shot, mp4(seconds=5), 5)
    assert finding.level == ("fail" if direct else "pass")
    assert ("8" if direct else "12") in finding.message


def test_video_filing_and_reruns_share_the_project_vision_setting(project, mp4, monkeypatch):
    project.config.vision_qa = False
    dump(project.paths.config, project.config)
    monkeypatch.setattr(
        eyeline, "check_eyeline", lambda *args: checks.Finding(eyeline.CHECK, checks.PASS, "Clear")
    )
    scene, shot = "s01_pub", "sh01"
    clip = mp4(seconds=10)
    qa = project.paths.shot(scene, shot).qa
    runner = CliRunner()

    for number, options in enumerate(([], ["--vision"]), 1):
        incoming = project.paths.inbox / "video.mp4"
        incoming.write_bytes(clip.read_bytes())
        result = runner.invoke(app, [
            "inbox", "file", incoming.name, "-C", str(project.paths.root),
            "--scene", scene, "--shot", shot, *options,
        ])
        assert result.exit_code == 0, result.output
        report = actions.read_qa_report(qa / f"video_v{number}.json")
        assert report is not None
        assert any(f.check == "duration" for f in report)
        assert any(f.check == eyeline.CHECK for f in report) == bool(options)

    result = runner.invoke(app, [
        "qa", scene, shot, "-C", str(project.paths.root),
        "--kind", "video", "--take", "2", "--no-vision",
    ])
    assert result.exit_code == 0, result.output
    report = actions.read_qa_report(qa / "video_v2.json")
    assert report is not None
    assert all(f.check != eyeline.CHECK for f in report)


@pytest.fixture
def generated_clip(tmp_path):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from shotforge.qa.speech import RATE

    def make(*, push_in=False, voice_at=None):
        width, height, fps, duration = 320, 180, 12, 5
        # A textured set surrounds a moving foreground patch.
        rng = np.random.default_rng(42)
        texture = rng.integers(25, 190, (height, width), np.uint8)
        background = cv2.GaussianBlur(texture, (5, 5), 0)
        frames = []
        for index in range(fps * duration):
            fraction = index / (fps * duration - 1)
            scale = 1 + 0.42 * fraction**2 if push_in else 1
            transform = cv2.getRotationMatrix2D((width / 2, height / 2), 0, scale)
            frame = cv2.warpAffine(background, transform, (width, height))
            offset = round(35 * fraction)
            cv2.rectangle(frame, (105 + offset, 55), (145 + offset, 110), 220, -1)
            frames.append(frame)

        audio = rng.normal(0, 0.004, RATE * duration)
        if voice_at is not None:
            start = round(voice_at * RATE)
            times = np.arange(min(RATE, len(audio) - start)) / RATE
            tone = 2 * np.pi * 125 * times
            syllables = 0.55 + 0.45 * np.sin(2 * np.pi * 5 * times)
            audio[start : start + len(times)] += syllables * (
                0.16 * np.sin(tone) + 0.09 * np.sin(3 * tone) + 0.07 * np.sin(7 * tone)
            )
        raw_audio = tmp_path / "track.f32"
        raw_audio.write_bytes(audio.astype("<f4").tobytes())
        clip = tmp_path / "take.mp4"
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
                "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{width}x{height}",
                "-r", str(fps), "-i", "pipe:0",
                "-f", "f32le", "-ar", str(RATE), "-ac", "1", "-i", str(raw_audio),
                "-c:v", "mpeg4", "-c:a", "aac", "-threads", "1", str(clip),
            ],
            input=np.stack(frames).tobytes(), check=True,
        )
        return clip

    return make


@pytest.mark.parametrize(
    ("push_in", "voice_at", "expected"),
    [
        (False, None, {}),
        (True, None, {"camera motion": "fail"}),
        (False, 2.2, {"speech during silence": "fail"}),
        (False, 4.4, {"dialogue cutoff": "warn"}),
    ],
)
def test_video_qa_detects_motion_and_speech_independently(
    project, generated_clip, push_in, voice_at, expected
):
    shot = Shot(
        id="pub_take", duration_s=5,
        beats=[Beat(t=(0, 2), action="Pause"), Beat(t=(2, 4), silence=True),
               Beat(t=(4, 5), speaker="durik", line="A toast")],
    )
    clip = generated_clip(push_in=push_in, voice_at=voice_at)
    findings = checks.check_video_file(project, "s01_pub", shot, clip, vision=False)

    assert {f.check: f.level for f in findings if f.level != "pass"} == expected
    if push_in:
        motion = next(f for f in findings if f.check == "camera motion")
        percent = float(motion.message.split("%")[0].split()[-1])
        assert percent == pytest.approx(42, abs=4)
        assert "Accelerating" in motion.message
