"""Every path in a project, in one place.

The folder tree is fixed (see ``shotforge new``) and file names inside it are
fixed too, so you always know what to attach to a prompt. Paths are built with
:mod:`pathlib` only — no shell, no string joining — so Mac and Windows behave
the same.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROJECT_FILE = "project.yaml"
STATE_DIR = ".shotforge"
INBOX_DIR = "_inbox"

SOURCE_DIR = "00_source"
BIBLE_DIR = "01_bible"
DESIGN_DIR = "02_design"
BLENDER_DIR = "03_blender"
SCENES_DIR = "04_scenes"
EDIT_DIR = "05_edit"

#: Approved file names the later stages refer to.
APPROVED_FRAME = "first.png"
APPROVED_VIDEO = "shot.mp4"
LAST_FRAME = "last_frame.png"


@dataclass(frozen=True)
class ShotPaths:
    """The folder of one shot and the fixed names inside it."""

    root: Path

    @property
    def spec(self) -> Path:
        return self.root / "shot.yaml"

    @property
    def prompts(self) -> Path:
        return self.root / "prompts"

    @property
    def frames(self) -> Path:
        return self.root / "frames"

    @property
    def video(self) -> Path:
        return self.root / "video"

    @property
    def qa(self) -> Path:
        return self.root / "qa"

    @property
    def approved_frame(self) -> Path:
        return self.frames / APPROVED_FRAME

    @property
    def approved_video(self) -> Path:
        return self.video / APPROVED_VIDEO

    @property
    def last_frame(self) -> Path:
        return self.root / LAST_FRAME

    def dirs(self) -> list[Path]:
        return [self.root, self.prompts, self.frames, self.video, self.qa]


@dataclass(frozen=True)
class ScenePaths:
    root: Path

    @property
    def spec(self) -> Path:
        return self.root / "scene.yaml"

    @property
    def shots_dir(self) -> Path:
        return self.root / "shots"

    def shot(self, shot_id: str) -> ShotPaths:
        return ShotPaths(self.shots_dir / shot_id)

    def shot_ids(self) -> list[str]:
        if not self.shots_dir.is_dir():
            return []
        return sorted(p.name for p in self.shots_dir.iterdir() if (p / "shot.yaml").is_file())


@dataclass(frozen=True)
class ProjectPaths:
    """Resolves every file in a project from its root folder."""

    root: Path

    @classmethod
    def find(cls, start: Path | None = None) -> ProjectPaths | None:
        """Walk up from ``start`` until a folder holds ``project.yaml``."""
        here = (start or Path.cwd()).resolve()
        for candidate in [here, *here.parents]:
            if (candidate / PROJECT_FILE).is_file():
                return cls(candidate)
        return None

    # -- top level ---------------------------------------------------------
    @property
    def config(self) -> Path:
        return self.root / PROJECT_FILE

    @property
    def state_dir(self) -> Path:
        return self.root / STATE_DIR

    @property
    def cache(self) -> Path:
        return self.state_dir / "cache"

    @property
    def state_file(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def approvals_log(self) -> Path:
        return self.state_dir / "approvals.jsonl"

    @property
    def inbox(self) -> Path:
        return self.root / INBOX_DIR

    @property
    def source(self) -> Path:
        return self.root / SOURCE_DIR

    @property
    def edit(self) -> Path:
        return self.root / EDIT_DIR

    @property
    def skills(self) -> Path:
        """Project-local skill overrides; they win over the bundled library."""
        return self.root / "skills"

    # -- bible -------------------------------------------------------------
    @property
    def bible(self) -> Path:
        return self.root / BIBLE_DIR

    @property
    def style(self) -> Path:
        return self.bible / "style.yaml"

    def bible_kind(self, kind: str) -> Path:
        return self.bible / f"{kind}s"

    def entity_file(self, kind: str, entity_id: str) -> Path:
        return self.bible_kind(kind) / f"{entity_id}.yaml"

    def entity_ids(self, kind: str) -> list[str]:
        folder = self.bible_kind(kind)
        if not folder.is_dir():
            return []
        return sorted(p.stem for p in folder.glob("*.yaml"))

    # -- design ------------------------------------------------------------
    @property
    def design(self) -> Path:
        return self.root / DESIGN_DIR

    def design_dir(self, kind: str, entity_id: str) -> Path:
        return self.design / f"{kind}s" / entity_id

    def sheet(self, kind: str, entity_id: str) -> Path:
        return self.design_dir(kind, entity_id) / "sheet.png"

    def ref(self, kind: str, entity_id: str) -> Path:
        return self.design_dir(kind, entity_id) / "ref.png"

    def wall_ref(self, location_id: str) -> Path:
        return self.design_dir("location", location_id) / "wall_ref.png"

    def face_crops(self, character_id: str) -> Path:
        return self.design_dir("character", character_id) / "face_crops"

    def face_crop(self, scene_id: str, shot_id: str, character_id: str) -> Path:
        return self.face_crops(character_id) / f"{scene_id}_{shot_id}.png"

    # -- blender -----------------------------------------------------------
    @property
    def blender(self) -> Path:
        return self.root / BLENDER_DIR

    def blend_file(self, scene_id: str) -> Path:
        return self.blender / f"{scene_id}.blend"

    def renders(self, scene_id: str, shot_id: str) -> Path:
        return self.blender / "renders" / scene_id / shot_id

    def blockout(self, scene_id: str, shot_id: str) -> Path:
        return self.renders(scene_id, shot_id) / "blockout.png"

    def mask(self, scene_id: str, shot_id: str, character_id: str) -> Path:
        return self.renders(scene_id, shot_id) / f"mask_{character_id}.png"

    @property
    def textures(self) -> Path:
        return self.blender / "textures"

    @property
    def materials(self) -> Path:
        return self.textures / "materials.yaml"

    # -- scenes ------------------------------------------------------------
    @property
    def scenes(self) -> Path:
        return self.root / SCENES_DIR

    def scene(self, scene_id: str) -> ScenePaths:
        return ScenePaths(self.scenes / scene_id)

    def scene_ids(self) -> list[str]:
        if not self.scenes.is_dir():
            return []
        return sorted(p.name for p in self.scenes.iterdir() if (p / "scene.yaml").is_file())

    def shot(self, scene_id: str, shot_id: str) -> ShotPaths:
        return self.scene(scene_id).shot(shot_id)

    def rel(self, path: Path) -> str:
        """A project-relative, forward-slash path — what prompts and YAML carry."""
        try:
            return path.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return path.as_posix()
