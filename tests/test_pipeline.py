"""Every stage closes on an approval, and nothing moves without one."""

from __future__ import annotations

import pytest

from shotforge import actions
from shotforge.models import ShotStatus, Stage
from shotforge.pipeline import TransitionError, approve, begin_next_stage, next_action, reopen
from shotforge.takes import TakeKind, add_take


def test_a_stage_cannot_be_skipped(project, state):
    with pytest.raises(TransitionError, match="still working"):
        begin_next_stage(project, state, "s01_pub", "sh01")


def test_approval_is_the_only_way_forward(project, state):
    approve(project, state, "s01_pub", "sh01", note="beats fine")
    assert project.shot("s01_pub", "sh01").status is ShotStatus.breakdown_approved
    with pytest.raises(TransitionError, match="not waiting"):
        approve(project, state, "s01_pub", "sh01")


def test_every_gate_is_written_to_the_log(project, state):
    approve(project, state, "s01_pub", "sh01", note="looks right")
    entries = state.approvals("s01_pub", "sh01")
    assert entries[0].action == "approve"
    assert entries[0].to_status == "breakdown_approved"
    assert entries[0].note == "looks right"


def test_the_full_walk_from_breakdown_to_done(project, state, png, mp4):
    scene, shot_id = "s01_pub", "sh01"
    approve(project, state, scene, shot_id)                 # breakdown
    actions.begin(project, state, scene, shot_id)            # → blockout
    approve(project, state, scene, shot_id)                  # blockout (rendered in the fixture)
    actions.begin(project, state, scene, shot_id)             # → frame
    shot_paths = project.paths.shot(scene, shot_id)
    add_take(shot_paths, TakeKind.frame, png("f.png"))
    actions.approve_frame(project, state, scene, shot_id, 1)
    assert shot_paths.approved_frame.is_file()
    actions.begin(project, state, scene, shot_id)             # → video
    add_take(shot_paths, TakeKind.video, mp4("v.mp4", 10))
    shot, last_frame = actions.approve_video(project, state, scene, shot_id, 1)
    assert shot.status is ShotStatus.video_approved
    assert shot_paths.approved_video.is_file()
    assert last_frame is not None and last_frame.is_file()
    shot = actions.begin(project, state, scene, shot_id)
    assert shot.status is ShotStatus.done
    assert next_action(project, state, scene, shot_id).kind == "finished"


def test_the_blockout_gate_waits_for_the_render(project, state):
    project.paths.blockout("s01_pub", "sh01").unlink()
    approve(project, state, "s01_pub", "sh01")
    actions.begin(project, state, "s01_pub", "sh01")
    action = next_action(project, state, "s01_pub", "sh01")
    assert action.tool == "Blender"
    assert not action.ready
    assert "not rendered" in action.blocked_by[0]


def test_the_frame_gate_waits_for_a_take(project, state):
    scene, shot_id = "s01_pub", "sh01"
    approve(project, state, scene, shot_id)
    actions.begin(project, state, scene, shot_id)
    approve(project, state, scene, shot_id)
    actions.begin(project, state, scene, shot_id)
    action = next_action(project, state, scene, shot_id)
    assert action.skill == "chatgpt-first-frame"
    assert not action.ready


def test_reopening_goes_back_and_is_logged(project, state):
    approve(project, state, "s01_pub", "sh01")
    shot = reopen(project, state, "s01_pub", "sh01", Stage.breakdown, note="beats were wrong")
    assert shot.status is ShotStatus.breakdown
    assert state.approvals("s01_pub", "sh01")[0].action == "reopen"


def test_reopening_forward_is_refused(project, state):
    with pytest.raises(TransitionError, match="ahead of"):
        reopen(project, state, "s01_pub", "sh01", Stage.video)


def test_pass_two_needs_a_pass_one_frame(project, state, png):
    scene, shot_id = "s01_pub", "sh01"
    for _ in range(2):
        approve(project, state, scene, shot_id)
        actions.begin(project, state, scene, shot_id)
    with pytest.raises(ValueError, match="file one from the inbox"):
        actions.advance_frame_pass(project, state, scene, shot_id)
    add_take(project.paths.shot(scene, shot_id), TakeKind.frame, png("f.png"))
    assert actions.advance_frame_pass(project, state, scene, shot_id) == 2
    action = next_action(project, state, scene, shot_id)
    assert action.skill == "chatgpt-identity-pass"
