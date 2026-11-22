"""Review the pictures on either side of each cut and cache the findings."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from itertools import pairwise
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import TypeAdapter

from shotforge.checks import FAIL, PASS, WARN, Finding
from shotforge.engine import (
    Engine,
    EngineCancelled,
    EngineError,
    EngineProgress,
    EngineResult,
    get_engine,
)
from shotforge.models import Scene, Shot
from shotforge.models.common import as_plain
from shotforge.project import Project
from shotforge.skills import find_skill
from shotforge.skills.registry import SkillError
from shotforge.tools import ToolError
from shotforge.yamlio import ProjectFileError

SKILL_NAME = "continuity-check"
ENGINE_NAME = "claude-code"
REVIEW = "visual continuity"
FINDINGS = TypeAdapter(list[Finding])


@dataclass
class _CutReview:
    findings: list[Finding] = field(default_factory=list)
    status: Finding | None = None


def scene_findings(
    project: Project, scene: Scene, *, run: bool = False, progress: EngineProgress | None = None,
) -> list[Finding]:
    """Read cached reviews, or run the missing reviews when explicitly requested."""
    engine = get_engine(ENGINE_NAME) if run else None
    findings = []
    for earlier, later in pairwise(project.shots(scene.id)):
        if progress:
            progress.status(f"Checking cut {earlier.id} → {later.id}…")
        review = _review_cut(project, scene, earlier, later, engine, progress=progress)
        entries = review.findings
        if review.status is not None:
            entries = [*entries, review.status]
        findings.extend(_label_cut(earlier, later, entries))
    return findings


def shot_findings(
    project: Project, scene: Scene, shot: Shot, earlier: Shot | None
) -> list[Finding]:
    """Show cached gaps on the shot receiving the cut."""
    if earlier is None:
        return []
    review = _review_cut(project, scene, earlier, shot, engine=None)
    return _label_cut(earlier, shot, review.findings)


FRAME_WORDS = {
    "earlier_approved_frame": "approved frame",
    "earlier_last_frame": "last frame",
    "later_approved_frame": "approved frame",
}


def _missing_frames(frames: dict[str, Path], earlier: Shot, later: Shot) -> str:
    """Name the frames that are not there yet. A path tells the director
    nothing they can act on; the shot and the word for the frame does."""
    per_shot: dict[str, list[str]] = {}
    for role, path in frames.items():
        if path.is_file():
            continue
        shot = earlier.id if role.startswith("earlier") else later.id
        per_shot.setdefault(shot, []).append(FRAME_WORDS[role])
    parts = [f"{shot} has no {' or '.join(words)}" for shot, words in per_shot.items()]
    return f"Nothing to compare yet: {', and '.join(parts)}." if parts else ""


def _label_cut(earlier: Shot, later: Shot, findings: list[Finding]) -> list[Finding]:
    return [replace(f, message=f"{earlier.id} → {later.id}: {f.message}") for f in findings]


def _review_cut(
    project: Project, scene: Scene, earlier: Shot, later: Shot, engine: Engine | None, *,
    progress: EngineProgress | None = None,
) -> _CutReview:
    before = project.paths.shot(scene.id, earlier.id)
    after = project.paths.shot(scene.id, later.id)
    frames = {
        "earlier_approved_frame": before.approved_frame,
        "earlier_last_frame": before.last_frame,
        "later_approved_frame": after.approved_frame,
    }
    try:
        missing = _missing_frames(frames, earlier, later)
        if missing:
            return _CutReview(status=Finding(REVIEW, WARN, f"Skipped. {missing}"))

        skill = find_skill(SKILL_NAME, project.paths.skills)
        inputs = {
            "scene": as_plain(scene),
            "earlier_shot": _shot_spec(earlier),
            "later_shot": _shot_spec(later),
            "frames": {role: project.paths.rel(path) for role, path in frames.items()},
        }
        files = list(frames.values())
        digests = _frame_digests(files)
        settings = project.claude_settings()
        options = settings.model_dump(exclude_defaults=True, exclude={"timeout_s"})
        cache_inputs = [ENGINE_NAME, skill.body, skill.rules(), inputs, digests]
        if options:
            cache_inputs.append(options)
        content = json.dumps(cache_inputs, sort_keys=True)
        key = hashlib.sha256(content.encode("utf-8")).hexdigest()
        cache = project.paths.cache / SKILL_NAME / f"{key}.json"
        findings = _read_cache(cache)
        if findings is None:
            if engine is None:
                return _CutReview(status=Finding(REVIEW, WARN, "Ready for a continuity check."))
            if progress:
                progress.status("Waiting for Claude to review the cut…")
                result = engine.run_skill_with_progress(
                    skill, inputs, cwd=project.paths.root, files=files, progress=progress,
                )
            else:
                result = engine.run_skill(skill, inputs, cwd=project.paths.root, files=files)
            findings = _parse_result(result)
            if _frame_digests(files) != digests:
                raise EngineError("the frames changed during the review. Run the check again.")
            if project.claude_settings() != settings:
                raise EngineError("Claude settings changed during the review. Run the check again.")
            if progress:
                progress.check_cancelled()
            _write_cache(cache, findings)
    except EngineCancelled:
        raise
    except (EngineError, OSError, ProjectFileError, SkillError, ToolError) as exc:
        return _CutReview(status=Finding(REVIEW, FAIL, f"Review failed. {exc}"))

    status = None if findings else Finding(REVIEW, PASS, "No visual continuity gaps found.")
    return _CutReview(findings, status)


def _shot_spec(shot: Shot) -> dict:
    spec = as_plain(shot)
    spec.pop("status", None)
    return spec


def _frame_digests(files: list[Path]) -> list[str]:
    digests = []
    for path in files:
        with path.open("rb") as image:
            digests.append(hashlib.file_digest(image, "sha256").hexdigest())
    return digests


def _validate_findings(payload: object) -> list[Finding]:
    if not isinstance(payload, list):
        raise ValueError("expected a list of findings")
    findings = FINDINGS.validate_python(payload)
    for finding in findings:
        if finding.level not in (PASS, WARN, FAIL) or not (
            finding.check.strip() and finding.message.strip()
        ):
            raise ValueError("each finding needs a check, a valid level and a message")
    return findings


def _parse_result(result: EngineResult) -> list[Finding]:
    if result.data.get("is_error"):
        raise EngineError(f"Claude Code reported an error: {result.text[:500]}")
    if result.data.get("permission_denials"):
        raise EngineError("Claude Code could not read all the frames.")
    try:
        payload = result.data.get("parsed")
        if payload is None:
            payload = json.loads(result.text)
        if not isinstance(payload, dict):
            raise ValueError("expected a JSON object")
        if isinstance(error := payload.get("error"), str) and error.strip():
            raise EngineError(error[:500])
        return _validate_findings(payload.get("findings"))
    except ValueError as exc:
        raise EngineError("the continuity skill returned invalid findings") from exc


def _read_cache(path: Path) -> list[Finding] | None:
    try:
        return _validate_findings(json.loads(path.read_text(encoding="utf-8")))
    except (FileNotFoundError, ValueError):
        return None


def _write_cache(path: Path, findings: list[Finding]) -> None:
    payload = json.dumps([asdict(finding) for finding in findings], ensure_ascii=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    file = NamedTemporaryFile(dir=path.parent, encoding="utf-8", mode="w", delete=False)
    tmp = Path(file.name)
    try:
        with file:
            file.write(payload)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
