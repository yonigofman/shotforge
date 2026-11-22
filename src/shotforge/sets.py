"""Where each location's Blender set stands.

A set is the place, not the scene: every shot in the pub is shot in one
file. This reads what is on disk against what the bible now says, so the
cockpit can tell the difference between a set that has not been built, one
that is current, and one the location has moved on from.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from shotforge.blender import set_signature
from shotforge.blender.errors import BlockoutError
from shotforge.blender.spec import set_spec
from shotforge.project import Project

# The names the build script gives its own collections. They live in Blender's
# own Python, which cannot import this package, so the two are kept in step by
# a test rather than by sharing a constant.
SET = "Shotforge Set"
DRESSING = "Dressing"
SCENE_PREFIX = "Shotforge Scene "
CAMERA_PREFIX = "Shotforge Cameras "


@dataclass(frozen=True)
class SetStatus:
    """One location's set, as it stands."""

    location: str
    name: str
    path: Path
    exists: bool
    behind: bool
    dressing: bool
    scenes: list[str]
    shots: int
    rendered: int
    error: str | None = None

    @property
    def owns(self) -> list[str]:
        """The collections Shotforge rebuilds, and so will throw away."""
        return [SET, *(
            prefix + scene_id
            for scene_id in self.scenes
            for prefix in (SCENE_PREFIX, CAMERA_PREFIX)
        )]

    @property
    def state(self) -> str:
        if self.error:
            return "error"
        if not self.exists:
            return "none"
        return "behind" if self.behind else "built"

    @property
    def blocked(self) -> bool:
        """A rebuild that would move the walls under the director's own work."""
        return self.behind and self.dressing


def status(project: Project, location_id: str) -> SetStatus:
    scenes = [
        scene_id for scene_id in project.scene_ids()
        if project.scene(scene_id).location == location_id
    ]
    shots = [
        (scene_id, shot.id) for scene_id in scenes for shot in project.shots(scene_id)
    ]
    entity = project.entity("location", location_id)
    path = project.paths.set_file(location_id)
    common = {
        "location": location_id,
        "name": next(iter(entity.names.values()), location_id) if entity.names else location_id,
        "path": path,
        "scenes": scenes,
        "shots": len(shots),
        "rendered": sum(
            1 for scene_id, shot_id in shots
            if project.paths.blockout(scene_id, shot_id).is_file()
        ),
    }
    try:
        key = set_signature(project, set_spec(project, location_id))
    except BlockoutError as exc:
        return SetStatus(
            **common, exists=path.is_file(), behind=False, dressing=False, error=str(exc)
        )
    state = _state(project, location_id)
    return SetStatus(
        **common,
        exists=path.is_file() and state is not None,
        behind=state is not None and state.get("key") != key,
        dressing=bool(state and state.get("dressing")),
    )


def every(project: Project) -> list[SetStatus]:
    """Every location in the bible, the ones a scene is shot in first."""
    rows = [status(project, entity_id) for entity_id in project.paths.entity_ids("location")]
    return sorted(rows, key=lambda row: (not row.scenes, row.location))


def _state(project: Project, location_id: str) -> dict | None:
    try:
        state = json.loads(project.paths.set_state(location_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None
