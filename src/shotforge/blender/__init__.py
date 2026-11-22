"""Build the scene once and render its requested cameras through either Blender backend."""

from __future__ import annotations

import json
import shutil
from hashlib import file_digest, sha256
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


def render_scene(
    scene: str, *, project: Project | None = None, changed: bool = False,
) -> list[Path]:
    """Render a scene in one job, optionally skipping current outputs."""
    return _render(scene, project=project, changed=changed)


def _render(
    scene_id: str, shot_id: str | None = None, *, project: Project | None,
    changed: bool = False,
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
        cache = project.paths.cache / "blockout" / scene_id
        scene_key = _signature([_hash_file(_SCRIPT), spec])
        if changed and _current(cache / ".scene", scene_key, [project.paths.blend_file(scene_id)]):
            characters = {
                shot: chars for shot, chars in characters.items()
                if not _current(cache / f"{shot}.stamp", _signature([scene_key, chars]), [
                    project.paths.blockout(scene_id, shot),
                    *(project.paths.mask(scene_id, shot, char) for char in chars),
                ])
            }
        return (
            _run_backends(project, scene_id, spec, characters, cache, scene_key)
            if characters else []
        )
    except (OSError, ProjectError, ProjectFileError) as exc:
        raise BlockoutError(str(exc)) from exc


def _hash_file(path: Path) -> str:
    with path.open("rb") as fh:
        return file_digest(fh, "sha256").hexdigest()


def _signature(data: list) -> str:
    return sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()


def _artifact_key(key: str, outputs: list[Path]) -> str:
    return _signature([key, *map(_hash_file, outputs)])


def _current(stamp: Path, key: str, outputs: list[Path]) -> bool:
    try:
        return stamp.read_text(encoding="ascii") == _artifact_key(key, outputs)
    except (OSError, UnicodeError):
        return False


def _snapshot_spec(spec: dict, staging: Path) -> dict:
    materials = {}
    snapshots = {}
    for surface, assignment in spec["materials"].items():
        source = Path(assignment["texture"])
        if source not in snapshots:
            target = staging / "textures" / source.name
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(source, target)
            if _hash_file(target) != assignment["sha256"]:
                raise BlockoutError(f"{source.name} changed during rendering. Run blockout again.")
            snapshots[source] = target
        materials[surface] = {**assignment, "texture": str(snapshots[source])}
    return {**spec, "materials": materials}


def _run_backends(
    project: Project, scene_id: str, spec: dict, characters: dict[str, list[str]],
    cache: Path, scene_key: str,
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
                    "spec": _snapshot_spec(spec, staging),
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
            # The stamp covers the files this job produced. A manual edit or an
            # interleaved job leaves a mismatch and the next incremental run retries.
            stamps = {".scene": _artifact_key(scene_key, [staged_blend])}
            for shot, chars in characters.items():
                stamps[f"{shot}.stamp"] = _artifact_key(
                    _signature([scene_key, chars]),
                    [staged_pngs[shot], *staged_masks[shot].values()],
                )
            staged_blend.replace(blend)
            for staged, target in outputs.items():
                staged.replace(target)
            try:
                cache.mkdir(parents=True, exist_ok=True)
                for name, signature in stamps.items():
                    stamp = staging / name
                    stamp.write_text(signature, encoding="ascii")
                    stamp.replace(cache / name)
            except OSError:
                # The renders are usable even if the optional cache is unavailable.
                pass
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
