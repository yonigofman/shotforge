"""Taking an approval back, and setting a take aside."""

from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient

from shotforge import actions, undo
from shotforge.models import ShotStatus, Stage
from shotforge.pipeline import TransitionError, approve
from shotforge.takes import TakeKind, add_take, list_takes
from shotforge.web.app import create_app


@pytest.fixture
def client(project):
    return TestClient(create_app(project.paths.root))


def _to_frame_stage(project, state, shot_id="sh01"):
    for _ in range(2):
        approve(project, state, "s01_pub", shot_id)
        actions.begin(project, state, "s01_pub", shot_id)


def _white_mask(path) -> None:
    """A mask the crop step can actually find a character in."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
            "-i", "color=c=white:s=1920x1080", "-frames:v", "1", str(path),
        ],
        check=True,
    )


def _approved_frame(project, state, png, shot_id="sh01"):
    _to_frame_stage(project, state, shot_id)
    for char in project.shot("s01_pub", shot_id).characters:
        _white_mask(project.paths.mask("s01_pub", shot_id, char))
    add_take(project.paths.shot("s01_pub", shot_id), TakeKind.frame, png(f"{shot_id}.png"))
    actions.approve_frame(project, state, "s01_pub", shot_id, 1)


def _approved_video(project, state, png, mp4, shot_id="sh01"):
    _approved_frame(project, state, png, shot_id)
    actions.begin(project, state, "s01_pub", shot_id)
    add_take(project.paths.shot("s01_pub", shot_id), TakeKind.video, mp4(f"{shot_id}.mp4", 10))
    actions.approve_video(project, state, "s01_pub", shot_id, 1)


# -- nothing to undo --------------------------------------------------------


def test_a_shot_with_no_approval_has_nothing_to_take_back(project, state):
    assert not undo.can_undo(project.shot("s01_pub", "sh01").status)
    with pytest.raises(TransitionError, match="nothing has been approved"):
        undo.plan_undo(project, state, "s01_pub", "sh01")


# -- the plan ---------------------------------------------------------------


def test_the_plan_names_the_stage_the_files_and_what_stands_on_them(project, state, png):
    _approved_frame(project, state, png)
    plan = undo.plan_undo(project, state, "s01_pub", "sh01")
    assert plan.stage is Stage.frame
    assert plan.target is ShotStatus.frame
    names = {path.name for path in plan.removes}
    assert "first.png" in names
    assert "s01_pub_sh01.png" in names  # the face crop cut from it
    assert any("face crops" in line for line in plan.dependents)
    assert plan.approved_at is not None


def test_planning_changes_nothing(project, state, png):
    _approved_frame(project, state, png)
    undo.plan_undo(project, state, "s01_pub", "sh01")
    assert project.shot("s01_pub", "sh01").status is ShotStatus.frame_approved
    assert project.paths.shot("s01_pub", "sh01").approved_frame.is_file()


# -- undoing a frame --------------------------------------------------------


def test_undoing_a_frame_removes_what_the_approval_wrote(project, state, png):
    _approved_frame(project, state, png)
    shot_paths = project.paths.shot("s01_pub", "sh01")
    crop = project.paths.face_crop("s01_pub", "sh01", "dima")
    assert shot_paths.approved_frame.is_file()

    undo.undo_approval(project, state, "s01_pub", "sh01", note="identity drifts")

    assert not shot_paths.approved_frame.exists()
    assert not crop.exists()
    assert project.shot("s01_pub", "sh01").status is ShotStatus.frame


def test_the_takes_are_never_deleted(project, state, png):
    _approved_frame(project, state, png)
    add_take(project.paths.shot("s01_pub", "sh01"), TakeKind.frame, png("second.png"))
    undo.undo_approval(project, state, "s01_pub", "sh01")
    kept = list_takes(project.paths.shot("s01_pub", "sh01"), TakeKind.frame)
    numbers = [take.number for take in kept]
    assert numbers == [1, 2]


def test_the_log_pairs_the_undo_with_the_approval_it_took_back(project, state, png):
    _approved_frame(project, state, png)
    approved = next(
        entry for entry in state.approvals("s01_pub", "sh01") if entry.action == "approve"
    )
    undo.undo_approval(project, state, "s01_pub", "sh01", note="wrong face")

    latest = state.approvals("s01_pub", "sh01")[0]
    assert latest.action == "unapprove"
    assert latest.undoes == approved.ts
    assert latest.note == "wrong face"
    assert latest.to_status == "frame"
    assert "first.png" in " ".join(latest.detail["removed"])


def test_the_card_offers_pass_one_again_after_an_undo(project, state, png):
    _approved_frame(project, state, png)
    undo.undo_approval(project, state, "s01_pub", "sh01")
    from shotforge.pipeline import next_action

    action = next_action(project, state, "s01_pub", "sh01")
    assert action.skill in {"chatgpt-first-frame", "chatgpt-identity-pass"}
    assert action.gate == "Approve the first frame"


# -- undoing a video --------------------------------------------------------


def test_undoing_a_video_removes_the_clip_and_the_last_frame(project, state, png, mp4):
    _approved_video(project, state, png, mp4)
    shot_paths = project.paths.shot("s01_pub", "sh01")
    assert shot_paths.approved_video.is_file()
    assert shot_paths.last_frame.is_file()

    plan = undo.undo_approval(project, state, "s01_pub", "sh01")

    assert plan.stage is Stage.video
    assert not shot_paths.approved_video.exists()
    assert not shot_paths.last_frame.exists()
    assert project.shot("s01_pub", "sh01").status is ShotStatus.video
    # The frame approval below it is untouched.
    assert shot_paths.approved_frame.is_file()


def test_the_next_shot_is_named_as_a_dependent(project, state, png, mp4):
    _approved_video(project, state, png, mp4)
    plan = undo.plan_undo(project, state, "s01_pub", "sh01")
    assert any("sh02" in line for line in plan.dependents)


def test_undoing_twice_walks_back_two_gates(project, state, png, mp4):
    _approved_video(project, state, png, mp4)
    undo.undo_approval(project, state, "s01_pub", "sh01")
    undo.undo_approval(project, state, "s01_pub", "sh01")
    shot_paths = project.paths.shot("s01_pub", "sh01")
    assert project.shot("s01_pub", "sh01").status is ShotStatus.frame
    assert not shot_paths.approved_frame.exists()


# -- setting a take aside ---------------------------------------------------


def test_a_take_set_aside_is_not_chosen(project, state, png):
    _to_frame_stage(project, state)
    shot_paths = project.paths.shot("s01_pub", "sh01")
    add_take(shot_paths, TakeKind.frame, png("one.png"))
    add_take(shot_paths, TakeKind.frame, png("two.png"))
    assert actions.latest_take_number(project, state, "s01_pub", "sh01", TakeKind.frame) == 2

    state.set_take_rejected("s01_pub", "sh01", "frame", 2, True)
    assert actions.latest_take_number(project, state, "s01_pub", "sh01", TakeKind.frame) == 1
    assert list_takes(shot_paths, TakeKind.frame)[-1].path.is_file()


def test_restoring_brings_it_back(project, state, png):
    _to_frame_stage(project, state)
    add_take(project.paths.shot("s01_pub", "sh01"), TakeKind.frame, png("one.png"))
    state.set_take_rejected("s01_pub", "sh01", "frame", 1, True)
    with pytest.raises(FileNotFoundError, match="set aside"):
        actions.latest_take_number(project, state, "s01_pub", "sh01", TakeKind.frame)
    state.set_take_rejected("s01_pub", "sh01", "frame", 1, False)
    assert actions.latest_take_number(project, state, "s01_pub", "sh01", TakeKind.frame) == 1


def test_rejections_survive_a_reload(project, state, png):
    _to_frame_stage(project, state)
    add_take(project.paths.shot("s01_pub", "sh01"), TakeKind.frame, png("one.png"))
    state.set_take_rejected("s01_pub", "sh01", "frame", 1, True)
    from shotforge.state import ProjectState

    assert ProjectState(project.paths).rejected_takes("s01_pub", "sh01", "frame") == {1}


# -- through the cockpit ----------------------------------------------------


def test_the_card_offers_undo_once_something_is_approved(client, project, state, png):
    assert "Undo approval" not in client.get("/shot/s01_pub/sh01").text
    _approved_frame(project, state, png)
    assert "Undo approval" in client.get("/shot/s01_pub/sh01").text


def test_the_confirm_lists_the_files_and_the_dependents(client, project, state, png, mp4):
    _approved_video(project, state, png, mp4)
    response = client.get("/shot/s01_pub/sh01/undo")
    assert response.status_code == 200
    assert "shot.mp4" in response.text
    assert "last_frame.png" in response.text
    assert "sh02" in response.text


def test_undoing_from_the_cockpit_moves_the_shot_back(client, project, state, png):
    _approved_frame(project, state, png)
    response = client.post("/shot/s01_pub/sh01/undo", data={"note": "faces"})
    assert response.status_code == 200
    assert project.shot("s01_pub", "sh01").status is ShotStatus.frame
    assert not project.paths.shot("s01_pub", "sh01").approved_frame.exists()


def test_undo_is_refused_when_nothing_is_approved(client):
    assert client.get("/shot/s01_pub/sh01/undo").status_code == 400


def test_setting_a_take_aside_from_the_cockpit(client, project, state, png):
    _to_frame_stage(project, state)
    add_take(project.paths.shot("s01_pub", "sh01"), TakeKind.frame, png("one.png"))
    response = client.post("/shot/s01_pub/sh01/take/frame/1/reject")
    assert response.status_code == 200
    state.reload()
    assert state.rejected_takes("s01_pub", "sh01", "frame") == {1}
    assert "Restore" in response.text

    response = client.post("/shot/s01_pub/sh01/take/frame/1/reject", data={"restore": "true"})
    state.reload()
    assert state.rejected_takes("s01_pub", "sh01", "frame") == set()
    assert "Set aside" in response.text


def test_rejecting_a_take_that_does_not_exist_is_a_404(client, project, state):
    _to_frame_stage(project, state)
    assert client.post("/shot/s01_pub/sh01/take/frame/9/reject").status_code == 404
