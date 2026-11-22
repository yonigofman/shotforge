from __future__ import annotations

import subprocess

from fastapi.testclient import TestClient

from shotforge import actions
from shotforge.media import frame_size
from shotforge.models import ShotStatus, Stage
from shotforge.pipeline import frame_attachments, reopen
from shotforge.state import ProjectState
from shotforge.takes import TakeKind, add_take
from shotforge.web.app import create_app

SCENE = "s01_pub"


def _frame_take(project, shot_id, frame):
    shot = project.shot(SCENE, shot_id)
    shot.status = ShotStatus.frame
    project.save_shot(SCENE, shot)
    add_take(project.paths.shot(SCENE, shot_id), TakeKind.frame, frame)


def _mask(project, shot_id, char):
    mask = project.paths.mask(SCENE, shot_id, char)
    mask.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=100x100",
            "-vf",
            "drawbox=x=15:y=20:w=10:h=60:c=white:t=fill",
            "-frames:v",
            "1",
            str(mask),
        ],
        check=True,
    )


def test_identity_pass_uses_latest_approved_crops(example_project, png):
    project = example_project
    state = ProjectState(project.paths)
    frame = png(width=200, height=200)
    for shot_id in ("sh08", "sh06", "sh07"):
        _frame_take(project, shot_id, frame)
        for char in project.shot(SCENE, shot_id).characters:
            _mask(project, shot_id, char)
    actions.approve_frame(project, state, SCENE, "sh08", 1)
    actions.approve_frame(project, state, SCENE, "sh06", 1)
    actions.advance_frame_pass(project, state, SCENE, "sh07")

    result = actions.build_prompt(project, state, SCENE, "sh07")
    assert "(MISSING)" not in result.text
    items = result.action.attachments
    assert items[0].name == "first_v1.png"
    for index, char in enumerate(("dima", "durik", "zhmurik")):
        crop, mask = items[1 + index * 2 : 3 + index * 2]
        assert crop.path == project.paths.face_crop(SCENE, "sh06", char)
        assert mask.path == project.paths.mask(SCENE, "sh07", char)
        assert frame_size(crop.path) == (20, 40)

    reopen(project, state, SCENE, "sh06", Stage.frame)
    seed = project.paths.face_crops("zhmurik") / "seed.png"
    frame.replace(seed)
    items = frame_attachments(project, SCENE, "sh07", 2)
    assert items[1].path == project.paths.face_crop(SCENE, "sh08", "dima")
    assert items[5].path == seed


def test_approval_skips_missing_and_empty_masks_on_card(example_project, png):
    project = example_project
    paths = project.paths
    frame = png(width=200, height=200)
    _frame_take(project, "sh06", frame)
    _mask(project, "sh06", "dima")
    empty = paths.mask(SCENE, "sh06", "durik")
    png(name="empty.png", width=100, height=100).replace(empty)
    stale = paths.face_crop(SCENE, "sh06", "zhmurik")
    stale.parent.mkdir(parents=True)
    png(name="stale.png", width=20, height=20).replace(stale)

    with TestClient(create_app(project.paths.root)) as client:
        result = client.post(f"/shot/{SCENE}/sh06/approve", data={"take": 1})
        assert result.status_code == 200
        assert "zhmurik: face crop skipped (mask_zhmurik.png is missing)" in result.text
        assert "durik: face crop skipped (mask_durik.png has no visible character" in result.text
    assert project.shot(SCENE, "sh06").status is ShotStatus.frame_approved
    assert paths.face_crop(SCENE, "sh06", "dima").is_file()
    assert not stale.exists()
