"""Turn one failed frame check into an edit and track its follow-up take."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from shotforge import checks
from shotforge.actions import PromptResult
from shotforge.checks import Finding
from shotforge.models import Shot, ShotStatus
from shotforge.pipeline import ORDER, Action, Attachment, face_crop_refs
from shotforge.project import Project
from shotforge.skills import find_skill, render_prompt, save_prompt
from shotforge.state import ProjectState
from shotforge.takes import TakeKind, get_take, next_number

FIX_SKILL = "chatgpt-fix"
IDENTITY_SKILL = "chatgpt-identity-pass"


@dataclass(frozen=True)
class FixAttempt:
    check: str
    target: str | None
    next_take: int
    skill: str
    rule: str


@dataclass
class FixPrompt(PromptResult):
    selection_guide: Attachment | None


def _words(text: str) -> str:
    return re.sub(r"[\W_]+", " ", text.casefold()).strip()


def _identity_targets(shot: Shot, finding: Finding) -> list[str]:
    visible = [framed.char for framed in shot.in_frame if framed.role != "offscreen"]
    if finding.target:
        return [char for char in visible if _words(char) == _words(finding.target)]
    for detail in (finding.fix, f"{finding.check} {finding.message}"):
        message = f" {_words(detail or '')} "
        named = [char for char in visible if f" {_words(char)} " in message]
        if named:
            return named
    return visible if len(visible) == 1 else []


def _matches_target(shot: Shot, finding: Finding, attempt: FixAttempt) -> bool:
    if attempt.target is None:
        return True
    if attempt.skill == IDENTITY_SKILL:
        targets = _identity_targets(shot, finding)
        # Vision QA can give one verdict for all visible faces.
        return attempt.target in targets or (not targets and finding.target is None)
    return finding.target is None or _words(finding.target) == _words(attempt.target)


def build_fix(
    project: Project, scene_id: str, shot_id: str, number: int, finding_key: str
) -> FixPrompt:
    shot = project.shot(scene_id, shot_id)
    paths = project.paths
    shot_paths = paths.shot(scene_id, shot_id)
    take = get_take(shot_paths, TakeKind.frame, number)
    findings = checks.read_report(take.qa_report(shot_paths)) or []
    finding = next((row for row in findings if row.failed and row.key == finding_key), None)
    if finding is None:
        raise ValueError("That failed finding is no longer in the take's QA report.")

    attachments = [Attachment(1, "Frame to edit", take.path, True)]
    check = _words(finding.check)
    labels = set(check.split())
    target = finding.target or finding.check
    region = None
    framed = None
    crop = None
    mask = None
    guide = None
    rule = finding.fix or f"Check {target} against the shot spec before approving the frame."
    rule_skill = "chatgpt-first-frame"

    if "identity" in labels:
        targets = _identity_targets(shot, finding)
        if len(targets) != 1:
            raise ValueError("Name one in-frame character in the identity finding's target.")
        character = targets[0]
        target = character
        framed = next(item for item in shot.in_frame if item.char == character)
        crop_path = face_crop_refs(project, (scene_id, shot_id), [character])[character]
        crop = Attachment(2, f"{character} face crop", crop_path, crop_path.is_file())
        attachments.append(crop)
        mask_path = paths.mask(scene_id, shot_id, character)
        mask = Attachment(3, f"{character} selection mask", mask_path, mask_path.is_file())
        attachments.append(mask)
        region = finding.region or f"only {character}'s face within the character silhouette"
        rule_skill = IDENTITY_SKILL
        rule = f"Match {character}'s face to the approved face crop and preserve the composition."
        if finding.fix:
            rule = f"{rule} {finding.fix}"
    elif labels & {"prop", "props", "window", "windows"}:
        region = finding.region or f"only the area affected by the {finding.check} finding"

    if region and not (mask and mask.exists):
        blockout = paths.blockout(scene_id, shot_id)
        guide = Attachment(
            len(attachments) + 1, "Blender blockout guide", blockout, blockout.is_file()
        )
        attachments.append(guide)

    mode = "Selection edit" if region else "Full edit"
    skill = find_skill(FIX_SKILL, paths.skills)
    action = Action(
        kind="work",
        title=f"{mode} · {finding.check}",
        tool=skill.tool,
        skill=skill.name,
        attachments=attachments,
    )
    context = {
        "project": project.config,
        "scene_id": scene_id,
        "shot": shot,
        "finding": finding,
        "mode": mode,
        "target": target,
        "framed": framed,
        "region": region,
        "crop": crop,
        "mask": mask,
        "guide": guide,
        "style": project.style(),
        "attachments": attachments,
        "rel": paths.rel,
    }
    text = render_prompt(skill, context)
    attempt = FixAttempt(
        check,
        target if framed else finding.target,
        next_number(shot_paths, TakeKind.frame),
        rule_skill,
        " ".join(rule.split()),
    )
    path = save_prompt(project, scene_id, shot_id, skill, text)
    path.with_suffix(".json").write_text(json.dumps(asdict(attempt)), encoding="utf-8")
    selection_guide = mask if mask and mask.exists else guide
    return FixPrompt(skill, text, path, action, selection_guide)


def rule_offers(
    project: Project, state: ProjectState, scene_id: str, shot: Shot
) -> list[FixAttempt]:
    """Offer a lesson only after the next filed frame passes QA and is approved."""
    if ORDER.index(shot.status) < ORDER.index(ShotStatus.frame_approved):
        return []
    approval = next(
        (
            entry
            for entry in state.approvals(scene_id, shot.id)
            if entry.to_status == ShotStatus.frame_approved
        ),
        None,
    )
    if approval is None or not isinstance(number := approval.detail.get("take"), int):
        return []
    shot_paths = project.paths.shot(scene_id, shot.id)
    try:
        take = get_take(shot_paths, TakeKind.frame, number)
    except FileNotFoundError:
        return []
    findings = checks.read_report(take.qa_report(shot_paths))
    if not findings or any(row.failed for row in findings):
        return []

    offers: dict[tuple[str, str | None], FixAttempt] = {}
    for path in sorted(
        shot_paths.prompts.glob(f"{FIX_SKILL}_v[0-9]*.json"),
        key=lambda path: (len(path.stem), path.stem),
    ):
        try:
            attempt = FixAttempt(**json.loads(path.read_text(encoding="utf-8")))
        except (FileNotFoundError, ValueError, TypeError):
            continue
        if attempt.next_take != number:
            continue
        relevant = [
            row
            for row in findings
            if _words(row.check) == attempt.check and _matches_target(shot, row, attempt)
        ]
        if relevant and all(row.level == checks.PASS for row in relevant):
            offers[(attempt.check, attempt.target)] = attempt
    return [
        attempt
        for attempt in offers.values()
        if attempt.rule not in find_skill(attempt.skill, project.paths.skills).rules()
    ]
