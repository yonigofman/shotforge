"""Fixtures: a small project on disk, built the way `shotforge new` builds one."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from shotforge.models import Character, Location, Scene, Seat, Style
from shotforge.models.shot import Beat, Camera, Continuity, FramedCharacter, Shot
from shotforge.project import Project
from shotforge.state import ProjectState

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "ulybka_vavilony"


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not installed; these tests make real media files")


def _png(path: Path, width: int, height: int) -> Path:
    """A real image file, made with the ffmpeg the project already needs."""
    _require_ffmpeg()
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
            "-i", f"color=c=0x303030:s={width}x{height}", "-frames:v", "1", str(path),
        ],
        check=True,
    )
    return path


def _mp4(path: Path, seconds: float) -> Path:
    _require_ffmpeg()
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
            "-i", "color=c=0x303030:s=640x360", "-t", str(seconds), "-r", "24",
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )
    return path


@pytest.fixture
def png(tmp_path):
    def make(name: str = "frame.png", width: int = 1920, height: int = 1080) -> Path:
        return _png(tmp_path / "fixtures" / name, width, height)

    return make


@pytest.fixture
def mp4(tmp_path):
    def make(name: str = "clip.mp4", seconds: float = 15) -> Path:
        return _mp4(tmp_path / "fixtures" / name, seconds)

    return make


@pytest.fixture
def project(tmp_path) -> Project:
    """A two-shot scene with the files the frame stage attaches."""
    project = Project.create(tmp_path / "proj", name="Test film", language="ru")
    paths = project.paths
    project.save_entity("character", Character(id="dima", tag="@Dima"))
    project.save_entity("character", Character(id="durik", tag="@Durik"))
    project.save_entity("location", Location(id="pub"))
    from shotforge.yamlio import dump

    dump(paths.style, Style(art_style="plain", negative=["no subtitles"]))
    project.save_scene(
        Scene(
            id="s01_pub",
            location="pub",
            seating=[Seat(char="dima", seat="table.left"), Seat(char="durik", seat="table.center")],
            axis_180="room side",
        )
    )
    first = Shot(
        id="sh01",
        duration_s=10,
        camera=Camera(blender_cam="CAM_01"),
        in_frame=[FramedCharacter(char="dima", role="subject", eyeline="Durik's face")],
        beats=[Beat(t=(0, 10), action="Dima waits")],
        continuity=Continuity(out={"beer": "half"}),
    )
    second = Shot(
        id="sh02",
        duration_s=5,
        camera=Camera(blender_cam="CAM_02"),
        in_frame=[FramedCharacter(char="durik", role="subject", eyeline="Dima's face")],
        beats=[Beat(t=(0, 5), action="Durik raises a finger")],
        continuity=Continuity(**{"in": {"beer": "half"}, "out": {"beer": "low"}}),
    )
    project.save_shot("s01_pub", first)
    project.save_shot("s01_pub", second)
    for shot_id in ("sh01", "sh02"):
        _png(paths.blockout("s01_pub", shot_id), 1920, 1080)
        for character_id in ("dima", "durik"):
            _png(paths.mask("s01_pub", shot_id, character_id), 1920, 1080)
    for character_id in ("dima", "durik"):
        _png(paths.sheet("character", character_id), 1024, 1024)
        _png(paths.ref("character", character_id), 512, 512)
        _png(paths.face_crops(character_id) / "seed.png", 512, 512)
    _png(paths.design_dir("location", "pub") / "wall_ref.png", 1920, 1080)
    return project


@pytest.fixture
def state(project) -> ProjectState:
    return ProjectState(project.paths)


@pytest.fixture
def example_project(tmp_path) -> Project:
    """A copy of the shipped example, so tests never write to the repo."""
    target = tmp_path / "example"
    shutil.copytree(EXAMPLE, target)
    return Project.open(target)
