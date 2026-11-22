"""``shotforge`` on the command line.

Everything the cockpit does is available here too, because a scene should
never depend on a browser being open.
"""

from __future__ import annotations

import os
from pathlib import Path

import typer
from filelock import Timeout
from jinja2 import TemplateError

from shotforge import (
    actions,
    blender,
    breakdown,
    checks,
    cinematography,
    continuity,
    edit,
    texture,
    tools,
    undo,
)
from shotforge import inbox as inbox_mod
from shotforge.console import setup_console
from shotforge.demo import copy_demo
from shotforge.models import Stage
from shotforge.paths import ProjectPaths
from shotforge.pipeline import TransitionError, next_action, reopen
from shotforge.project import Project, ProjectError, slugify
from shotforge.registry import ProjectRegistry
from shotforge.skills import find_skill, list_skills
from shotforge.skills.registry import SkillError
from shotforge.state import ProjectState
from shotforge.takes import TakeKind, get_take, list_takes
from shotforge.web import lifecycle
from shotforge.yamlio import ProjectFileError

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Turn book text into AI video scenes, with an approval gate at every step.",
)
inbox_app = typer.Typer(no_args_is_help=True, help="The _inbox folder: see it, file it.")
app.add_typer(inbox_app, name="inbox")

LEVEL_MARK = {"pass": "ok  ", "warn": "warn", "fail": "FAIL"}


def _open(project_dir: Path | None) -> tuple[Project, ProjectState]:
    try:
        project = Project.open(project_dir) if project_dir else Project.discover()
    except (ProjectError, ProjectFileError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    tools.configure(project.config.tool_paths.model_dump())
    return project, ProjectState(project.paths)


ProjectOption = typer.Option(
    None, "--project", "-C", help="Project folder (default: this folder or one above it)."
)


@app.command()
def new(
    name: str = typer.Argument(..., help="Project name, e.g. 'Ulybka Vavilony'."),
    path: Path | None = typer.Option(None, "--path", help="Where to create it."),
    language: str = typer.Option("ru", help="Language of the dialogue."),
    aspect: str = typer.Option("16:9", help="Aspect ratio of every frame."),
    fps: int = typer.Option(24, help="Frames per second."),
    media: str = typer.Option("lfs", help="How media is stored: lfs | external | git."),
) -> None:
    """Create a project: the fixed folder tree, ready for the bible."""
    root = Path(path) if path else Path.cwd() / slugify(name)
    try:
        project = Project.create(
            root, name=name, language=language, aspect_ratio=aspect, fps=fps, media=media
        )
    except ProjectError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Created {project.name} in {project.paths.root}")
    typer.echo("Next: put your chapter in 00_source/ch01.md, then run `shotforge breakdown ch01`.")


@app.command()
def demo(
    path: Path = typer.Argument(Path("last_light"), help="New folder for the public example."),
) -> None:
    """Copy Last Light, a two-shot project with rendered stills and video takes."""
    try:
        project = copy_demo(path)
    except (ProjectError, OSError) as exc:
        raise typer.BadParameter(str(exc), param_hint="path") from exc
    typer.echo(f"Created {project.name} in {project.paths.root}")
    typer.echo(f'Open it with: shotforge serve -C "{project.paths.root}"')


@app.command()
def doctor(project_dir: Path | None = ProjectOption) -> None:
    """Check the tools Shotforge needs, and say what is missing."""
    if project_dir is not None or ProjectPaths.find() is not None:
        _open(project_dir)
    missing_required = False
    for status in tools.doctor():
        tag = "required" if status.required else "optional"
        if status.ok:
            typer.echo(f"ok    {status.name:8} {tag:8} {status.path}")
        else:
            typer.echo(f"MISS  {status.name:8} {tag:8} {status.hint}")
            missing_required = missing_required or status.required
    if missing_required:
        typer.echo("\nSomething required is missing — install it and run doctor again.")
        raise typer.Exit(1)


@app.command()
def validate(project_dir: Path | None = ProjectOption) -> None:
    """Load every file in the project and report what does not match its schema."""
    project, _ = _open(project_dir)
    problems = project.validate()
    for scene_id in project.scene_ids():
        for finding in checks.check_scene_chain(project, scene_id):
            if finding.failed:
                problems.append(f"{scene_id}: {finding.message}")
    if problems:
        for problem in problems:
            typer.echo(f"FAIL  {problem}")
        raise typer.Exit(1)
    scenes = len(project.scene_ids())
    shots = sum(len(project.shot_ids(s)) for s in project.scene_ids())
    typer.echo(f"ok    {project.name}: {scenes} scene(s), {shots} shot(s), every file valid")


@app.command("continuity")
def continuity_cmd(
    scene: str,
    project_dir: Path | None = ProjectOption,
) -> None:
    """Review visual continuity across the scene's cuts with Claude Code."""
    project, _ = _open(project_dir)
    try:
        scene_spec = project.scene(scene)
    except (ProjectError, ProjectFileError) as exc:
        raise typer.BadParameter(str(exc), param_hint="scene") from exc
    findings = continuity.scene_findings(project, scene_spec, run=True)
    if not findings:
        typer.echo("No neighboring shots to review.")
    for finding in findings:
        typer.echo(f"{LEVEL_MARK[finding.level]} {finding.check}: {finding.message}")
        if finding.fix and finding.level != checks.PASS:
            typer.echo(f"      fix: {finding.fix}")
    if any(finding.failed for finding in findings):
        raise typer.Exit(1)


@app.command()
def cut(scene: str, project_dir: Path | None = ProjectOption) -> None:
    """Assemble the scene's approved video, EDL and editable reels."""
    project, _ = _open(project_dir)
    try:
        output = edit.assemble(project, scene)
    except (edit.EditError, ProjectError, ProjectFileError, tools.ToolError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    for name in (edit.VIDEO, edit.EDL, edit.EXPORT):
        typer.echo(project.paths.rel(output.with_name(name)))


@app.command("breakdown")
def breakdown_cmd(chapter: str, project_dir: Path | None = ProjectOption) -> None:
    """Propose scenes and bible entries from a chapter in 00_source."""
    project, _ = _open(project_dir)
    try:
        source = breakdown.read_chapter(project, chapter)
        book = breakdown.propose_book(project, source)
        plan = breakdown.WritePlan.for_book(project, book)
        if _review_proposal(project, plan):
            _save_proposal(project, plan)
    except (breakdown.BreakdownError, ProjectFileError, ProjectError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@app.command("shots")
def shots_cmd(
    scene: str,
    project_dir: Path | None = ProjectOption,
    chapter: str | None = typer.Option(None, help="Chapter filename or stem for a legacy scene."),
) -> None:
    """Propose new shots with timed beats and dialogue for a scene."""
    project, _ = _open(project_dir)
    try:
        spec = breakdown.load_scene(project, scene)
        source = breakdown.read_chapter(project, chapter or spec.chapter)
        shots = breakdown.propose_shots(project, spec, source)
        plan = breakdown.WritePlan.for_shots(project, scene, shots)
        if _review_proposal(project, plan):
            if breakdown.load_scene(project, scene) != spec:
                raise breakdown.BreakdownError(f"{scene}/scene.yaml changed. Generate shots again.")
            _save_proposal(project, plan)
    except (breakdown.BreakdownError, ProjectFileError, ProjectError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


def _review_proposal(project: Project, plan: breakdown.WritePlan) -> bool:
    for entry in plan.files:
        typer.echo(entry.diff(project), nl=False)
    _show_collisions(project, plan.collisions)
    if not plan.files:
        typer.echo("No new files to write.")
        return False
    return typer.confirm("Write these files?", default=False)


def _save_proposal(project: Project, plan: breakdown.WritePlan) -> None:
    written, collisions = plan.save(project)
    _show_collisions(project, collisions)
    typer.echo(f"Wrote {len(written)} file(s).")


def _show_collisions(project: Project, collisions: list[Path]) -> None:
    for path in collisions:
        typer.echo(f"Skipped (id collision): {project.paths.rel(path)}")


@app.command()
def status(
    scene: str | None = typer.Argument(None, help="Scene id; omit for all scenes."),
    project_dir: Path | None = ProjectOption,
) -> None:
    """Where every shot stands, and what it is waiting for."""
    project, state = _open(project_dir)
    scene_ids = [scene] if scene else project.scene_ids()
    if not scene_ids:
        typer.echo("No scenes yet. Add 04_scenes/<id>/scene.yaml.")
        return
    active = state.active
    for scene_id in scene_ids:
        typer.echo(f"\n{scene_id}")
        for shot in project.shots(scene_id):
            action = next_action(project, state, scene_id, shot.id)
            mark = "*" if active == (scene_id, shot.id) else " "
            verdict = checks.summarise(checks.check_spec(project, scene_id, shot))
            typer.echo(
                f" {mark} {shot.id:6} {actions.stage_label(shot):22} "
                f"{LEVEL_MARK[verdict]}  {action.title} ({action.tool})"
            )


@app.command()
def shot(
    scene: str,
    shot_id: str,
    project_dir: Path | None = ProjectOption,
) -> None:
    """The shot card in the terminal: what now, in which tool, with what attached."""
    project, state = _open(project_dir)
    shot = project.shot(scene, shot_id)
    action = next_action(project, state, scene, shot_id)
    typer.echo(f"{scene}/{shot_id} — {actions.stage_label(shot)}")
    typer.echo(f"\n{action.title}  [{action.tool}]")
    if action.detail:
        typer.echo(action.detail)
    if shot.camera.why:
        typer.echo(f"\nCamera — {shot.camera.why}")
    if shot.technique_why:
        typer.echo(f"Techniques — {shot.technique_why}")
    for warning in action.warnings:
        typer.echo(f"  - {warning}")
    if action.skill:
        typer.echo(f"skill: {action.skill}")
    if action.attachments:
        typer.echo("\nattach in this order:")
        for attachment in action.attachments:
            flag = "" if attachment.exists else "   (MISSING)"
            typer.echo(
                f"  {attachment.order}. {attachment.label} — "
                f"{project.paths.rel(attachment.path)}{flag}"
            )
    for kind in (TakeKind.frame, TakeKind.video):
        takes = list_takes(project.paths.shot(scene, shot_id), kind)
        if takes:
            typer.echo(f"\n{kind.value} takes: " + ", ".join(f"v{t.number}" for t in takes))
    if action.blocked_by:
        typer.echo("\nwaiting for:")
        for reason in action.blocked_by:
            typer.echo(f"  - {reason}")
    if action.gate:
        typer.echo(f"\ngate: {action.gate}  (`shotforge approve {scene} {shot_id}`)")
    findings = checks.check_spec(project, scene, shot) + continuity.shot_findings(
        project, project.scene(scene), shot, project.previous_shot(scene, shot.id)
    )
    if findings:
        typer.echo("\nchecks:")
        for finding in findings:
            typer.echo(f"  {LEVEL_MARK[finding.level]} {finding.check}: {finding.message}")
            if finding.fix and finding.level != checks.PASS:
                typer.echo(f"       fix: {finding.fix}")


@app.command()
def prompt(
    scene: str,
    shot_id: str,
    note: str | None = typer.Option(
        None, "--note", help='What to change this time: "warmer, less rain on the glass".'
    ),
    project_dir: Path | None = ProjectOption,
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Print only the prompt."),
) -> None:
    """Render the prompt for this shot's next step and save it next to the shot."""
    project, state = _open(project_dir)
    try:
        result = actions.build_prompt(project, state, scene, shot_id, note=note)
    except (ValueError, TransitionError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if quiet:
        typer.echo(result.text)
        return
    typer.echo(f"# {result.skill.name} → {project.paths.rel(result.path)}\n")
    typer.echo(result.text)


@app.command()
def camera(
    scene: str,
    shot_id: str = typer.Argument(..., metavar="SHOT"),
    project_dir: Path | None = ProjectOption,
) -> None:
    """Propose a camera during Breakdown and save it on confirmation."""
    project, _ = _open(project_dir)
    try:
        proposal, diff = cinematography.propose_camera(project, scene, shot_id)
        if not diff:
            typer.echo("The camera and techniques already match the proposal.")
            return
        typer.echo(diff, nl=False)
        if typer.confirm("Apply this camera proposal?"):
            cinematography.apply_camera(project, scene, shot_id, proposal)
            typer.echo(f"Saved camera proposal for {scene}/{shot_id}.")
    except (cinematography.CameraError, ProjectError, ProjectFileError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@app.command()
def blockout(
    scene: str,
    shot_id: str | None = typer.Argument(None, metavar="SHOT", help="Omit to render every shot."),
    changed: bool = typer.Option(False, "--changed", help="Skip current renders in a scene."),
    project_dir: Path | None = ProjectOption,
) -> None:
    """Render one shot or an entire scene in Blender."""
    project, _ = _open(project_dir)
    if changed and shot_id is not None:
        raise typer.BadParameter("omit SHOT to use --changed")
    try:
        paths = (
            [blender.render(scene, shot_id, project=project)]
            if shot_id is not None
            else blender.render_scene(scene, project=project, changed=changed)
        )
    except blender.BlockoutError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    location = project.scene(scene).location
    typer.echo(f"Set → {project.paths.rel(project.paths.set_file(location))}")
    if not paths:
        typer.echo("All blockout renders are current.")
    for path in paths:
        typer.echo(f"Rendered → {project.paths.rel(path)}")


@app.command()
def texture_prompt(
    surface: str = typer.Argument(..., help="E.g. location.pub.wall or character.dima.garment."),
    name: str = typer.Argument(..., help="Texture name without .png."),
    uv_scale: float = typer.Option(1.0, help="Texture repeats per metre."),
    source: str | None = typer.Option(None, help="Source sheet as kind.id (defaults to target)."),
    project_dir: Path | None = ProjectOption,
) -> None:
    """Prepare a texture prompt and a materials.yaml entry from an approved sheet."""
    project, _ = _open(project_dir)
    try:
        sheet, prompt, assignment = texture.build_prompt(project, surface, name, uv_scale, source)
    except (OSError, ValueError, ProjectError, ProjectFileError, SkillError, TemplateError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Attach {project.paths.rel(sheet)}\n\n{prompt}")
    typer.echo(f"Add to {project.paths.rel(project.paths.materials)}\n\n{assignment}")


@app.command()
def begin(scene: str, shot_id: str, project_dir: Path | None = ProjectOption) -> None:
    """Open the next stage of a shot after its approval."""
    project, state = _open(project_dir)
    try:
        shot = actions.begin(project, state, scene, shot_id)
    except TransitionError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"{shot_id} is now at {shot.status.value}")


@app.command()
def approve(
    scene: str,
    shot_id: str,
    take: int | None = typer.Option(None, "--take", help="Take number to approve."),
    note: str | None = typer.Option(None, "--note", help="Why, for the log."),
    project_dir: Path | None = ProjectOption,
) -> None:
    """Close the current stage. This is the only way a shot moves forward."""
    project, state = _open(project_dir)
    shot = project.shot(scene, shot_id)
    stage = shot.status.stage
    try:
        if stage is Stage.frame:
            number = take or _last_take(project, scene, shot_id, TakeKind.frame, state)
            shot = actions.approve_frame(project, state, scene, shot_id, number, note)
            typer.echo(f"frame take v{number} approved → frames/first.png")
            for warning in next_action(project, state, scene, shot_id).warnings:
                typer.echo(warning)
        elif stage is Stage.video:
            number = take or _last_take(project, scene, shot_id, TakeKind.video, state)
            shot, last_frame = actions.approve_video(project, state, scene, shot_id, number, note)
            typer.echo(f"video take v{number} approved → video/shot.mp4")
            if last_frame:
                typer.echo(f"last frame → {project.paths.rel(last_frame)}")
            else:
                typer.echo("last frame not extracted (ffmpeg missing) — run doctor")
        else:
            from shotforge.pipeline import approve as approve_stage

            shot = approve_stage(project, state, scene, shot_id, note=note)
    except (TransitionError, ValueError, FileNotFoundError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"{shot_id} is now at {shot.status.value}")


def _last_take(
    project: Project, scene: str, shot_id: str, kind: TakeKind, state: ProjectState
) -> int:
    try:
        return actions.latest_take_number(project, state, scene, shot_id, kind)
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command()
def qa(
    scene: str,
    shot_id: str,
    kind: str = typer.Option("frame", help="frame | video"),
    take: int | None = typer.Option(None, "--take", help="Take number (default: the latest)."),
    vision: bool | None = typer.Option(
        None, "--vision/--no-vision", help="Override the project's vision QA setting."
    ),
    project_dir: Path | None = ProjectOption,
) -> None:
    """Run QA on a take and write its report."""
    project, state = _open(project_dir)
    take_kind = TakeKind(kind)
    number = take or _last_take(project, scene, shot_id, take_kind, state)
    findings = actions.run_qa(project, scene, shot_id, take_kind, number, vision=vision)
    for finding in findings:
        typer.echo(f"{LEVEL_MARK[finding.level]} {finding.check}: {finding.message}")
        if finding.fix and finding.level != "pass":
            typer.echo(f"      fix: {finding.fix}")
    verdict = checks.summarise(findings)
    typer.echo(f"\nverdict: {verdict}")
    if verdict == "fail":
        raise typer.Exit(1)


@app.command()
def unapprove(
    scene: str,
    shot_id: str,
    note: str | None = typer.Option(None, "--note", help="Why, for the log."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
    project_dir: Path | None = ProjectOption,
) -> None:
    """Take the last approval back, and remove what it wrote."""
    project, state = _open(project_dir)
    try:
        plan = undo.plan_undo(project, state, scene, shot_id)
    except TransitionError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Undo the {plan.label} approval of {shot_id}, back to {plan.target.value}.")
    if plan.approved_at:
        typer.echo(f"approved {plan.approved_at}")
    for path in plan.removes:
        typer.echo(f"  removes  {project.paths.rel(path)}")
    if not plan.removes:
        typer.echo("  removes  nothing — this gate wrote no file")
    for line in plan.dependents:
        typer.echo(f"  WARN     {line}")
    takes_kept = len(list_takes(project.paths.shot(scene, shot_id), TakeKind.frame)) + len(
        list_takes(project.paths.shot(scene, shot_id), TakeKind.video)
    )
    typer.echo(f"  keeps    {takes_kept} take(s) — nothing generated is ever deleted")
    if not yes and not typer.confirm("Undo it?", default=False):
        typer.echo("Left as it was.")
        raise typer.Exit(1)
    try:
        undo.undo_approval(project, state, scene, shot_id, note=note)
    except (TransitionError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"{shot_id} is back at {plan.target.value}")


@app.command()
def reject(
    scene: str,
    shot_id: str,
    take: int = typer.Argument(..., help="Take number to set aside."),
    kind: TakeKind = typer.Option(TakeKind.frame, help="frame | video"),
    restore: bool = typer.Option(False, "--restore", help="Bring it back into the running."),
    project_dir: Path | None = ProjectOption,
) -> None:
    """Set a take aside so it is never chosen again. The file stays."""
    project, state = _open(project_dir)
    try:
        get_take(project.paths.shot(scene, shot_id), kind, take)
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc
    state.set_take_rejected(scene, shot_id, kind.value, take, not restore)
    verb = "back in the running" if restore else "set aside"
    typer.echo(f"{kind.value} take v{take} of {shot_id} is {verb}")


@app.command("reopen")
def reopen_cmd(
    scene: str,
    shot_id: str,
    stage: str = typer.Argument(..., help="breakdown | blockout | frame | video"),
    note: str | None = typer.Option(None, "--note"),
    project_dir: Path | None = ProjectOption,
) -> None:
    """Go back to an earlier stage. Logged, because later work may rest on it."""
    project, state = _open(project_dir)
    try:
        shot = reopen(project, state, scene, shot_id, Stage(stage), note=note)
    except (TransitionError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"{shot_id} reopened at {shot.status.value}")


@inbox_app.command("list")
def inbox_list(project_dir: Path | None = ProjectOption) -> None:
    """What is waiting in _inbox."""
    project, state = _open(project_dir)
    items = inbox_mod.scan(project)
    if not items:
        typer.echo("_inbox is empty.")
        return
    active = state.active
    for item in items:
        typer.echo(f"{item.filed_as:7} {item.size_kb:>6} KB  {item.name}")
    if active:
        typer.echo(f"\nactive shot: {active[0]}/{active[1]}")


@inbox_app.command("file")
def inbox_file(
    name: str = typer.Argument(..., help="File name in _inbox."),
    scene: str | None = typer.Option(None, help="Scene id (default: the active shot)."),
    shot_id: str | None = typer.Option(None, "--shot", help="Shot id."),
    texture_name: str | None = typer.Option(None, "--texture", help="Texture name without .png."),
    vision: bool | None = typer.Option(
        None, "--vision/--no-vision", help="Override the project's vision QA setting."
    ),
    project_dir: Path | None = ProjectOption,
) -> None:
    """File one download as the next take of a shot or texture."""
    project, state = _open(project_dir)
    source = project.paths.inbox / name
    if texture_name is not None:
        if scene or shot_id:
            raise typer.BadParameter("choose a texture or a shot as the inbox destination")
        try:
            path = inbox_mod.file_to_texture(project, source, texture_name)
        except (ValueError, OSError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(f"{name} → {project.paths.rel(path)}")
        return

    target = (scene, shot_id) if scene and shot_id else state.active
    if target is None:
        raise typer.BadParameter("no active shot — pass --scene and --shot")
    try:
        take = inbox_mod.file_to_shot(project, source, *target, vision=vision)
        actions.note_take_origin(project, state, *target, take)
    except (ValueError, FileNotFoundError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"{name} → {project.paths.rel(take.path)}")
    if take.kind is TakeKind.video:
        report_path = take.qa_report(project.paths.shot(*target))
        if (report := actions.read_qa_report(report_path)) is not None:
            typer.echo(f"QA {checks.summarise(report)} · {project.paths.rel(report_path)}")


@app.command()
def active(
    scene: str,
    shot_id: str,
    project_dir: Path | None = ProjectOption,
) -> None:
    """Set the shot the inbox defaults to."""
    project, state = _open(project_dir)
    project.shot(scene, shot_id)
    state.set_active(scene, shot_id)
    typer.echo(f"active shot: {scene}/{shot_id}")


@app.command()
def skills(
    name: str | None = typer.Argument(None, help="Show one skill's rules."),
    project_dir: Path | None = ProjectOption,
) -> None:
    """The skill library: what is available, and the rules each one carries."""
    project_skills = None
    try:
        project, _ = _open(project_dir)
        project_skills = project.paths.skills
    except typer.BadParameter:
        pass  # the library is readable without a project
    if name:
        skill = find_skill(name, project_skills)
        typer.echo(f"{skill.name} — {skill.kind}, {skill.stage}, {skill.tool}")
        typer.echo(f"{skill.description}\n")
        for rule in skill.rules():
            typer.echo(f"- {rule}")
        return
    for skill in list_skills(project_skills):
        typer.echo(f"{skill.name:26} {skill.kind:8} {skill.stage:10} {skill.description}")


@app.command()
def log(
    scene: str | None = typer.Option(None, help="Only this scene."),
    shot_id: str | None = typer.Option(None, "--shot", help="Only this shot."),
    limit: int = typer.Option(20, help="How many entries."),
    project_dir: Path | None = ProjectOption,
) -> None:
    """The approval log, newest first."""
    _, state = _open(project_dir)
    entries = state.approvals(scene, shot_id, limit)
    if not entries:
        typer.echo("Nothing approved yet.")
        return
    for entry in entries:
        note = f"  — {entry.note}" if entry.note else ""
        typer.echo(
            f"{entry.ts}  {entry.subject}  {entry.action}: "
            f"{entry.from_status} → {entry.to_status}{note}"
        )


@app.command()
def serve(
    project_dir: Path | None = ProjectOption,
    host: str = typer.Option("127.0.0.1", help="Loopback address."),
    port: int = typer.Option(8765, help="Port."),
    reload: bool = typer.Option(False, "--reload", help="Reload on code changes."),
) -> None:
    """Open the cockpit in your browser."""
    import uvicorn

    from shotforge.web.app import create_app
    from shotforge.web.local import is_loopback

    if not is_loopback(host):
        raise typer.BadParameter("The cockpit requires a loopback address.", param_hint="--host")
    root = None
    title = "Projects"
    if project_dir is None and (discovered := ProjectPaths.find()) is not None:
        project_dir = discovered.root
    if project_dir is not None:
        project, _ = _open(project_dir)
        root = project.paths.root
        title = project.name
        try:
            ProjectRegistry().remember(project)
        except (OSError, ValueError, ProjectError, Timeout) as exc:
            typer.echo(f"Could not update the projects registry: {exc}", err=True)
    if reload:
        if root is None:
            os.environ.pop("SHOTFORGE_PROJECT", None)
        else:
            os.environ["SHOTFORGE_PROJECT"] = str(root)
    address = f"[{host}]" if ":" in host else host
    typer.echo(f"{title} — cockpit on http://{address}:{port}")
    lifecycle.watch_for_shutdown(uvicorn)
    uvicorn.run(
        "shotforge.web.app:app_from_env" if reload else create_app(root, enforce_local=True),
        host=host,
        port=port,
        reload=reload,
        factory=reload,
        log_level="info",
        proxy_headers=False,
        # Ctrl+C must mean Ctrl+C, even with a cockpit tab still open.
        timeout_graceful_shutdown=5,
    )


def main() -> None:
    setup_console()
    app()


if __name__ == "__main__":
    main()
