"""Build the scene once and render its requested cameras through either Blender backend."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from shotforge.blender import headless, mcp
from shotforge.blender.errors import BlockoutError
from shotforge.blender.spec import build_spec
from shotforge.models.common import ID_RE
from shotforge.project import Project, ProjectError
from shotforge.yamlio import ProjectFileError

__all__ = ["BlockoutError", "render", "render_scene"]

_SCRIPT = Path(__file__).resolve().parent / "scripts" / "blockout.py"


def render(scene: str, shot: str, *, project: Project | None = None) -> Path:
    """Render one shot and save the shared scene with all its cameras."""
    return _render(scene, shot, project=project)[0]


def render_scene(scene: str, *, project: Project | None = None) -> list[Path]:
    """Render all shots with a single Blender job."""
    return _render(scene, project=project)


def _render(
    scene_id: str, shot_id: str | None = None, *, project: Project | None,
) -> list[Path]:
    if not ID_RE.fullmatch(scene_id) or (shot_id is not None and not ID_RE.fullmatch(shot_id)):
        raise BlockoutError("scene and shot IDs use lowercase letters, digits and underscores")
    try:
        project = project or Project.discover()
        scene = project.scene(scene_id)
        if shot_id is not None and shot_id not in project.shot_ids(scene_id):
            raise BlockoutError(f"no shot {shot_id!r} in scene {scene_id!r}")
        shots = project.shots(scene_id)
        if not shots:
            raise BlockoutError(f"{scene_id} has no shots to render")
        spec = build_spec(project, scene, shots)
        characters = {
            shot.id: list(dict.fromkeys(shot.characters))
            for shot in shots
            if shot_id is None or shot.id == shot_id
        }
        for shot, chars in characters.items():
            for char in chars:
                if not ID_RE.fullmatch(char):
                    raise BlockoutError(f"{shot}: invalid character id {char!r}")
        return _run_backends(project, scene_id, spec, characters)
    except (OSError, ProjectError, ProjectFileError) as exc:
        raise BlockoutError(str(exc)) from exc


def _run_backends(
    project: Project, scene_id: str, spec: dict, characters: dict[str, list[str]],
) -> list[Path]:
    paths = project.paths
    paths.blender.mkdir(parents=True, exist_ok=True)
    pngs = {shot: paths.blockout(scene_id, shot) for shot in characters}
    blend = paths.blend_file(scene_id)
    timeout = max(180, 60 * len(pngs))
    failures = []
    for backend in (mcp, headless):
        # Each attempt uses new files. A timed-out MCP request can never publish
        # a result over the background process or a previously successful job.
        with TemporaryDirectory(prefix=".blockout-", dir=paths.blender) as scratch:
            staging = Path(scratch)
            staged_blend = staging / blend.name
            staged_pngs = {shot: staging / f"{shot}.png" for shot in pngs}
            staged_masks = {
                shot: {
                    char: staging / "masks" / shot / paths.mask(scene_id, shot, char).name
                    for char in chars
                }
                for shot, chars in characters.items()
            }
            outputs = {
                staged: paths.mask(scene_id, shot, char)
                for shot, masks in staged_masks.items() for char, staged in masks.items()
            }
            outputs.update({staged: pngs[shot] for shot, staged in staged_pngs.items()})
            job = staging / "job.json"
            job.write_text(
                json.dumps({
                    "spec": spec,
                    "blend": str(staged_blend),
                    "renders": {shot: str(path) for shot, path in staged_pngs.items()},
                    "masks": {
                        shot: {char: str(path) for char, path in masks.items()}
                        for shot, masks in staged_masks.items()
                    },
                }),
                encoding="utf-8",
            )
            try:
                if backend is mcp:
                    mcp.render(_SCRIPT, job, timeout)
                else:
                    headless.render(_SCRIPT, job, timeout, project.config.tool_paths)
                _check_outputs(staged_blend, outputs)
            except BlockoutError as exc:
                failures.append(str(exc))
                continue

            for path in pngs.values():
                path.parent.mkdir(parents=True, exist_ok=True)
            staged_blend.replace(blend)
            for staged, target in outputs.items():
                staged.replace(target)
            return list(pngs.values())
    raise BlockoutError(
        f"Blender background render failed: {failures[1]}\nBlender MCP: {failures[0]}"
    )


def _check_outputs(blend: Path, outputs: dict[Path, Path]) -> None:
    try:
        if blend.stat().st_size == 0:
            raise BlockoutError("Blender produced an empty scene file")
        for path in outputs:
            with path.open("rb") as image:
                if image.read(8) != b"\x89PNG\r\n\x1a\n":
                    raise BlockoutError(f"Blender produced an invalid {path.name}")
    except FileNotFoundError as exc:
        raise BlockoutError(f"Blender did not produce {Path(exc.filename).name}") from exc
