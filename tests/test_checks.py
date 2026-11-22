"""The checks that need no model — each one from a real scene-1 failure."""

from __future__ import annotations

from shotforge import checks
from shotforge.models.shot import Beat, Continuity, FramedCharacter


def _levels(findings, check):
    return [f.level for f in findings if f.check == check]


def test_a_continuity_break_between_two_shots_fails(project):
    shot = project.shot("s01_pub", "sh02")
    shot.continuity = Continuity(**{"in": {"beer": "empty"}})
    project.save_shot("s01_pub", shot)
    findings = checks.check_continuity(project, "s01_pub", project.shot("s01_pub", "sh02"))
    assert _levels(findings, "continuity") == ["fail"]
    assert "sh01 goes out on 'half'" in findings[0].message


def test_a_matching_cut_passes(project):
    findings = checks.check_continuity(project, "s01_pub", project.shot("s01_pub", "sh02"))
    assert findings == []


def test_the_scene_chain_covers_every_cut(project):
    shot = project.shot("s01_pub", "sh02")
    shot.continuity = Continuity(**{"in": {"mood": "angry"}})
    project.save_shot("s01_pub", shot)
    chain = checks.check_scene_chain(project, "s01_pub")
    assert any(f.message.startswith("sh02:") for f in chain)


def test_a_character_not_in_the_bible_fails(project):
    shot = project.shot("s01_pub", "sh01")
    shot.in_frame.append(FramedCharacter(char="zhmurik", role="background"))
    project.save_shot("s01_pub", shot)
    findings = checks.check_spec(project, "s01_pub", project.shot("s01_pub", "sh01"))
    assert _levels(findings, "in_frame in the bible") == ["fail"]


def test_a_subject_without_an_eyeline_warns(project):
    shot = project.shot("s01_pub", "sh01")
    shot.in_frame[0].eyeline = None
    project.save_shot("s01_pub", shot)
    findings = checks.check_spec(project, "s01_pub", project.shot("s01_pub", "sh01"))
    assert _levels(findings, "eyeline") == ["warn"]


def test_dialogue_that_cannot_fit_its_beat_warns(project):
    shot = project.shot("s01_pub", "sh01")
    shot.beats = [Beat(t=(0, 2), speaker="dima", line=" ".join(["слово"] * 20))]
    project.save_shot("s01_pub", shot)
    findings = checks.check_spec(project, "s01_pub", project.shot("s01_pub", "sh01"))
    assert _levels(findings, "dialogue fits the beat") == ["warn"]


def test_a_moving_camera_warns_because_the_adapters_are_locked_off(project):
    shot = project.shot("s01_pub", "sh01")
    shot.camera.move = "push_in"
    project.save_shot("s01_pub", shot)
    findings = checks.check_spec(project, "s01_pub", project.shot("s01_pub", "sh01"))
    assert _levels(findings, "static camera") == ["warn"]


def test_the_wrong_aspect_ratio_fails_like_s11(project, png):
    findings = checks.check_frame_file(project, png("wrong.png", 1024, 768))
    assert _levels(findings, "aspect ratio") == ["fail"]
    assert checks.summarise(findings) == "fail"


def test_the_right_aspect_ratio_passes(project, png):
    findings = checks.check_frame_file(project, png("right.png", 1920, 1080))
    assert _levels(findings, "aspect ratio") == ["pass"]


def test_a_clip_much_longer_than_the_spec_is_flagged(project, mp4):
    shot = project.shot("s01_pub", "sh01")
    findings = checks.check_video_file(project, "s01_pub", shot, mp4("long.mp4", 15))
    assert _levels(findings, "duration") == ["fail"]
    findings = checks.check_video_file(project, "s01_pub", shot, mp4("right.mp4", 10))
    assert _levels(findings, "duration") == ["pass"]
