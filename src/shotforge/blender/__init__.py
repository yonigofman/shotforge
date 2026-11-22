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


def render(
    scene: str, shot: str, *, project: Project | None = None, rebuild: bool = False,
) -> Path:
    """Render one shot in its location's set, with all the scene's cameras."""
    return _render(scene, shot, project=project, rebuild=rebuild)[0]


def render_scene(
    scene: str, *, project: Project | None = None, changed: bool = False,
    rebuild: bool = False,
) -> list[Path]:
    """Render a scene in one job, optionally skipping current outputs."""
    return _render(scene, project=project, changed=changed, rebuild=rebuild)


def _render(
    scene_id: str, shot_id: str | None = None, *, project: Project | None,
    changed: bool = False, rebuild: bool = False,
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
        location = spec["set"]["location"]
        set_key = _signature([_hash_file(_SCRIPT), spec["set"]])
        state = _set_state(project, location)
        born = project.paths.set_file(location).is_file() and state is not None
        fresh = born and state["key"] == set_key
        if born and not fresh and state.get("dressing") and not rebuild:
            raise BlockoutError(
                f"The set for {location} has dressing of its own and the location's geometry "
                f"has changed since it was built. Rebuild the set to take the new room — "
                f"the dressing is kept, but it will not have moved with the walls."
            )

        cache = project.paths.cache / "blockout" / scene_id
        scene_key = _signature([set_key, {k: v for k, v in spec.items() if k != "set"}])
        if changed and fresh and _current(
            cache / ".scene", scene_key, [project.paths.set_file(location)]
        ):
            characters = {
                shot: chars for shot, chars in characters.items()
                if not _current(cache / f"{shot}.stamp", _signature([scene_key, chars]), [
                    project.paths.blockout(scene_id, shot),
                    *(project.paths.mask(scene_id, shot, char) for char in chars),
                ])
            }
        return (
            _run_backends(
                project, scene_id, spec, characters, cache, scene_key,
                set_key=set_key, reuse=fresh or born,
            )
            if characters else []
        )
    except (OSError, ProjectError, ProjectFileError) as exc:
        raise BlockoutError(str(exc)) from exc


def _set_state(project: Project, location: str) -> dict | None:
    """What Shotforge last built into that location's set, if it built one."""
    try:
        state = json.loads(project.paths.set_state(location).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) and isinstance(state.get("key"), str) else None


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
    """Copy every texture the job reads into staging, so a file edited while
    Blender works cannot change the render under it."""
    snapshots: dict[Path, Path] = {}
    place = _snapshot(spec["set"]["materials"], staging, snapshots)
    return {
        **spec,
        "set": {**spec["set"], "materials": place},
        "materials": _snapshot(spec["materials"], staging, snapshots),
    }


def _snapshot(assignments: dict, staging: Path, snapshots: dict[Path, Path]) -> dict:
    materials = {}
    for surface, assignment in assignments.items():
        source = Path(assignment["texture"])
        if source not in snapshots:
            target = staging / "textures" / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if _hash_file(target) != assignment["sha256"]:
                raise BlockoutError(f"{source.name} changed during rendering. Run blockout again.")
            snapshots[source] = target
        materials[surface] = {**assignment, "texture": str(snapshots[source])}
    return materials


def _run_backends(
    project: Project, scene_id: str, spec: dict, characters: dict[str, list[str]],
    cache: Path, scene_key: str, *, set_key: str, reuse: bool,
) -> list[Path]:
    paths = project.paths
    paths.sets.mkdir(parents=True, exist_ok=True)
    location = spec["set"]["location"]
    pngs = {shot: paths.blockout(scene_id, shot) for shot in characters}
    blend = paths.set_file(location)
    timeout = max(180, 60 * len(pngs))
    failures = []
    for backend in (mcp, headless):
        # Each attempt uses new files. A timed-out MCP request can never publish
        # a result over the background process or a previously successful job.
        with TemporaryDirectory(prefix=".blockout-", dir=paths.blender) as scratch:
            staging = Path(scratch)
            staged_blend = staging / blend.name
            # The set is worked on as a copy. A failed or timed-out job cannot
            # touch the file the director has been dressing.
            staged_source = None
            if reuse:
                staged_source = staging / f"source-{blend.name}"
                shutil.copyfile(blend, staged_source)
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
                    "set_blend": str(staged_source) if staged_source else None,
                    "report": str(staging / "report.json"),
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
            _write_set_state(project, location, set_key, staging / "report.json")
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


def _write_set_state(project: Project, location: str, set_key: str, report: Path) -> None:
    """Record what went into the set, and whether it carries dressing of its own.

    The script says whether it found a Dressing collection; a set that has one
    is never rebuilt behind the director's back.
    """
    dressing = False
    try:
        dressing = bool(json.loads(report.read_text(encoding="utf-8")).get("dressing"))
    except (OSError, ValueError):
        pass
    try:
        project.paths.set_state(location).write_text(
            json.dumps({"key": set_key, "dressing": dressing}, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        # The set is rendered and saved; losing the note only costs a rebuild.
        pass


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
