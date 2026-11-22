"""Takes are never overwritten; approval copies to the fixed name."""

from __future__ import annotations

import pytest

from shotforge.takes import (
    TakeKind,
    add_take,
    approve_take,
    approved_path,
    approved_take_number,
    list_takes,
    next_number,
)


def test_takes_are_numbered_and_kept(project, png):
    shot = project.paths.shot("s01_pub", "sh01")
    first = add_take(shot, TakeKind.frame, png("a.png"))
    second = add_take(shot, TakeKind.frame, png("b.png"))
    assert first.path.name == "first_v1.png"
    assert second.path.name == "first_v2.png"
    assert first.path.is_file() and second.path.is_file()
    assert next_number(shot, TakeKind.frame) == 3


def test_approval_copies_to_the_fixed_name_and_keeps_the_take(project, png):
    shot = project.paths.shot("s01_pub", "sh01")
    add_take(shot, TakeKind.frame, png("a.png"))
    take = add_take(shot, TakeKind.frame, png("b.png"))
    approved = approve_take(shot, TakeKind.frame, 2)
    assert approved.name == "first.png"
    assert take.path.is_file()
    assert approved_take_number(shot, TakeKind.frame) == 2
    assert len(list_takes(shot, TakeKind.frame)) == 2


def test_the_approved_copy_is_not_itself_a_take(project, png):
    shot = project.paths.shot("s01_pub", "sh01")
    add_take(shot, TakeKind.frame, png("a.png"))
    approve_take(shot, TakeKind.frame, 1)
    assert [t.number for t in list_takes(shot, TakeKind.frame)] == [1]
    assert approved_path(shot, TakeKind.frame).is_file()


def test_a_video_cannot_be_filed_as_a_frame(project, mp4):
    shot = project.paths.shot("s01_pub", "sh01")
    with pytest.raises(ValueError, match="not a frame file"):
        add_take(shot, TakeKind.frame, mp4("clip.mp4"))


def test_an_extension_mismatch_is_refused_rather_than_renamed(project, png):
    shot = project.paths.shot("s01_pub", "sh01")
    add_take(shot, TakeKind.frame, png("a.jpg"))
    with pytest.raises(ValueError, match="approved name"):
        approve_take(shot, TakeKind.frame, 1)
