"""_inbox: downloads land there and are filed to a shot."""

from __future__ import annotations

import pytest

from shotforge import inbox as inbox_mod
from shotforge.takes import TakeKind, list_takes


def _drop(project, source, name):
    target = project.paths.inbox / name
    target.write_bytes(source.read_bytes())
    return target


def test_scan_classifies_and_skips_partial_downloads(project, png, mp4):
    _drop(project, png("a.png"), "chatgpt.png")
    _drop(project, mp4("b.mp4"), "grok.mp4")
    _drop(project, png("c.png"), "half.png.crdownload")
    found = {item.name: item.filed_as for item in inbox_mod.scan(project)}
    assert found == {"chatgpt.png": "frame", "grok.mp4": "video"}


def test_filing_moves_the_file_in_as_the_next_take(project, png):
    source = _drop(project, png("a.png"), "chatgpt.png")
    take = inbox_mod.file_to_shot(project, source, "s01_pub", "sh01")
    assert take.qa_report(project.paths.shot("s01_pub", "sh01")).is_file()
    assert take.kind is TakeKind.frame
    assert take.path.name == "first_v1.png"
    assert not source.exists()
    assert len(list_takes(project.paths.shot("s01_pub", "sh01"), TakeKind.frame)) == 1


def test_an_unknown_file_type_is_refused(project):
    source = project.paths.inbox / "notes.txt"
    source.write_text("hello", encoding="utf-8")
    with pytest.raises(ValueError, match="not an image or a video"):
        inbox_mod.file_to_shot(project, source, "s01_pub", "sh01")


def test_filing_to_a_missing_shot_is_refused(project, png):
    source = _drop(project, png("a.png"), "chatgpt.png")
    with pytest.raises(FileNotFoundError):
        inbox_mod.file_to_shot(project, source, "s01_pub", "sh99")


def test_draining_files_everything_to_one_shot(project, png, mp4):
    _drop(project, png("a.png"), "one.png")
    _drop(project, png("b.png"), "two.png")
    _drop(project, mp4("c.mp4"), "clip.mp4")
    filed = inbox_mod.drain(project, "s01_pub", "sh01")
    assert sorted(t.path.name for t in filed) == ["first_v1.png", "first_v2.png", "v1.mp4"]
    assert inbox_mod.scan(project) == []
