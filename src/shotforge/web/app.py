"""The cockpit: FastAPI, Jinja, HTMX, Alpine — on localhost, no build step.

The screens share the CLI's workflows. The project folder stays the source of truth.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import FormData
from starlette.responses import FileResponse, Response

from shotforge import (
    actions,
    blender,
    breakdown,
    checks,
    cinematography,
    compare,
    continuity,
    fixes,
    tools,
    undo,
)
from shotforge import inbox as inbox_mod
from shotforge.console import setup_console
from shotforge.models import Scene, Shot, ShotStatus, Stage, as_plain
from shotforge.pipeline import ORDER, STAGE_LABEL, TransitionError, next_action, reopen
from shotforge.project import Project, ProjectError
from shotforge.registry import ProjectRegistry
from shotforge.skills import find_skill, latest_prompt, prompt_history
from shotforge.skills.registry import SkillError
from shotforge.state import ProjectState
from shotforge.takes import Take, TakeKind, approved_take_number, get_take, list_takes
from shotforge.web.bible import materials_for_page, register_bible
from shotforge.web.edit import register_edit
from shotforge.web.lifecycle import shutting_down
from shotforge.web.local import LocalOnlyMiddleware
from shotforge.web.projects import project_prefix, register_projects
from shotforge.web.settings import register_settings
from shotforge.web.streaming import StreamProgress, render_with_progress
from shotforge.yamlio import ProjectFileError

HERE = Path(__file__).resolve().parent


def _hx(**parts: object) -> dict[str, object]:
    """``hx(post=url, target="#takes")`` → the hx- attributes for a component."""
    return {f"hx-{name.replace('_', '-')}": value for name, value in parts.items()}


def _card_swap(url: str, method: str = "post") -> dict[str, object]:
    """The commonest one: act, then redraw the shot card."""
    return {f"hx-{method}": url, "hx-target": "#shot-card", "hx-swap": "outerHTML"}


def _number(value: float | int) -> str:
    """15 rather than 15.0 — the same spelling as in the prompts."""
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)


STAGES: tuple[Stage, ...] = (Stage.breakdown, Stage.blockout, Stage.frame, Stage.video)

#: The shot card's regions. Each one redraws on its own, so an action never
#: rebuilds the parts of the screen it did not touch.
REGIONS = {
    "head": "partials/_head.html",
    "do": "partials/_do.html",
    "see": "partials/_see.html",
    "judge": "partials/_judge.html",
    "gate": "partials/_gatebar.html",
}

#: How often the event stream looks for a change.
EVENT_POLL_SECONDS = 2.0
#: How long one event stream lives before it ends and the browser reconnects.
#: An endless response keeps the server from shutting down: uvicorn waits for
#: open connections, so Ctrl+C looks like it does nothing while a tab is open.
EVENT_STREAM_SECONDS = 60.0
BREAKDOWN_ERRORS = (breakdown.BreakdownError, ProjectError, ProjectFileError, OSError)
CAMERA_ERRORS = (cinematography.CameraError, ProjectFileError, OSError)


@dataclass
class BreakdownDraft:
    project_root: Path
    chapter: breakdown.Chapter
    book: breakdown.BookBreakdown
    plan: breakdown.WritePlan
    approving: bool = False

    def character_ids(self, project: Project) -> set[str]:
        return set(project.paths.entity_ids("character")) | {c.id for c in self.book.characters}

    def edit_beats(self, project: Project, form: FormData) -> breakdown.WritePlan:
        known_characters = self.character_ids(project)
        files = []
        for entry in self.plan.files:
            shot = entry.spec
            if isinstance(shot, Shot):
                beats = []
                for i, beat in enumerate(shot.beats):
                    key = f"{entry.scene_id}.{shot.id}.{i}"
                    if f"{key}.start" not in form:
                        beats.append(as_plain(beat))
                        continue
                    beats.append(
                        {
                            "t": [form.get(f"{key}.start"), form.get(f"{key}.end")],
                            "action": form.get(f"{key}.action") or None,
                            "speaker": form.get(f"{key}.speaker") or None,
                            "line": form.get(f"{key}.line") or None,
                            "silence": f"{key}.silence" in form,
                        }
                    )
                try:
                    shot = breakdown.validate_shot(
                        shot, beats=beats, known_characters=known_characters
                    )
                except breakdown.BreakdownError as exc:
                    raise breakdown.BreakdownError(f"{entry.scene_id}/{exc}") from exc
                entry = replace(entry, spec=shot)
            files.append(entry)
        return breakdown.WritePlan(files, self.plan.collisions)


def _qa_context(take: Take, findings: list[checks.Finding]) -> dict[str, Any]:
    return {
        "findings": findings,
        "verdict": checks.summarise(findings),
        "subject": f"{take.kind.value} v{take.number}",
        "fix_take": take.number if take.kind is TakeKind.frame else None,
    }


def create_app(
    project_root: Path | None = None,
    *,
    registry_path: Path | None = None,
    enforce_local: bool = False,
) -> FastAPI:
    setup_console()
    project_root = Path(project_root).resolve() if project_root is not None else None
    registry = ProjectRegistry(registry_path)
    app = FastAPI(title="Shotforge cockpit", openapi_url=None)
    app.add_middleware(LocalOnlyMiddleware, enforce_all=enforce_local)
    routes = APIRouter()
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters["n"] = _number
    templates.env.globals["hx"] = _hx
    templates.env.globals["card_swap"] = _card_swap
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    register_projects(app, registry, templates, project_root)
    drafts: OrderedDict[str, BreakdownDraft] = OrderedDict()
    camera_drafts: OrderedDict[
        str, tuple[Path, str, str, cinematography.CameraProposal]
    ] = OrderedDict()
    drafts_lock = Lock()

    # ---- helpers ---------------------------------------------------------
    def load(request: Request) -> tuple[Project, ProjectState]:
        """Resolve the project's URL, then read its current project.yaml."""
        key = request.path_params.get("project_id")
        root = project_root
        if key:
            try:
                entry = registry.find(key)
            except (OSError, ValueError, ProjectError) as exc:
                raise HTTPException(500, f"Could not read the projects registry: {exc}") from exc
            if entry is None:
                raise HTTPException(404, "Project not found. Open it from /projects.")
            root = entry.path
        if root is None:
            raise HTTPException(404, "Choose a project at /projects.")
        try:
            project = Project.open(root)
        except ProjectError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (ProjectFileError, OSError) as exc:
            raise HTTPException(500, str(exc)) from exc
        tools.configure(project.config.tool_paths.model_dump())
        return project, ProjectState(project.paths)

    def render(
        request: Request, name: str, context: dict[str, Any], *, status_code: int = 200
    ) -> HTMLResponse:
        project: Project = context["project"]
        prefix = project_prefix(request)
        context.setdefault("stages", STAGES)
        context.setdefault("stage_label", STAGE_LABEL)
        context.setdefault("project_prefix", prefix)
        context.setdefault("media_url", lambda path: f"{prefix}/media/{project.paths.rel(path)}")
        context.setdefault("rel", project.paths.rel)
        return templates.TemplateResponse(request, name, context, status_code=status_code)

    def inbox_context(project: Project, state: ProjectState) -> dict[str, Any]:
        materials, error = materials_for_page(project)
        names = {Path(item.texture).stem for item in materials.root.values()}
        return {
            "project": project,
            "inbox": inbox_mod.scan(project),
            "active": state.active,
            "texture_targets": sorted(names),
            "material_error": error,
        }

    def shot_card_context(request: Request, scene_id: str, shot_id: str) -> dict[str, Any]:
        project, state = load(request)
        try:
            shot = project.shot(scene_id, shot_id)
        except ProjectError as exc:
            raise HTTPException(404, str(exc)) from exc
        shot_paths = project.paths.shot(scene_id, shot_id)
        action = next_action(project, state, scene_id, shot_id)
        shot_state = state.shot(scene_id, shot_id)
        prompt_path = (
            latest_prompt(project, scene_id, shot_id, action.skill) if action.skill else None
        )
        frame_takes = list_takes(shot_paths, TakeKind.frame)
        video_takes = list_takes(shot_paths, TakeKind.video)
        scene = project.scene(scene_id)
        previous = project.previous_shot(scene_id, shot_id)
        frame_reports = {
            take.number: report
            for take in frame_takes
            if (report := actions.read_qa_report(take.qa_report(shot_paths))) is not None
        }
        video_reports = {
            take.number: report
            for take in video_takes
            if (report := actions.read_qa_report(take.qa_report(shot_paths))) is not None
        }
        qa_takes = video_takes or frame_takes
        if frame_takes and shot.status.stage is Stage.frame:
            qa_takes = frame_takes
        report_context = {}
        if qa_takes:
            latest = qa_takes[-1]
            report_context["qa_kind"] = latest.kind.value
            reports = frame_reports if latest.kind is TakeKind.frame else video_reports
            report = reports.get(latest.number)
            if report is not None:
                report_context.update(_qa_context(latest, report))
        if "findings" not in report_context:
            report_context["findings"] = checks.check_spec(project, scene_id, shot)
        return {
            "request": request,
            "project": project,
            "scene": scene,
            "shot": shot,
            "shot_paths": shot_paths,
            "blockout": project.paths.blockout(scene_id, shot_id),
            "action": action,
            "shot_state": shot_state,
            "stage_index": ORDER.index(shot.status),
            "prompt_text": prompt_path.read_text(encoding="utf-8") if prompt_path else None,
            "prompt_path": prompt_path,
            "frame_takes": frame_takes,
            "frame_verdicts": {n: checks.summarise(report) for n, report in frame_reports.items()},
            "video_takes": video_takes,
            "video_reports": video_reports,
            "video_verdicts": {n: checks.summarise(report) for n, report in video_reports.items()},
            "approved_frame": approved_take_number(shot_paths, TakeKind.frame),
            "approved_video": approved_take_number(shot_paths, TakeKind.video),
            "rejected_frames": state.rejected_takes(scene_id, shot_id, TakeKind.frame.value),
            "rejected_videos": state.rejected_takes(scene_id, shot_id, TakeKind.video.value),
            "frame_origins": state.take_origins(scene_id, shot_id, TakeKind.frame.value),
            "video_origins": state.take_origins(scene_id, shot_id, TakeKind.video.value),
            "prompt_history": (
                prompt_history(project, scene_id, shot_id, action.skill) if action.skill else []
            ),
            "undoable": undo.can_undo(shot.status),
            "rule_offers": fixes.rule_offers(project, state, scene_id, shot),
            "cut_findings": continuity.shot_findings(project, scene, shot, previous),
            "previous": previous,
            "active": state.active == (scene_id, shot_id),
            "stage_label": STAGE_LABEL,
            "label": actions.stage_label(shot),
            **report_context,
        }

    # ---- pages -----------------------------------------------------------
    @routes.get("/", response_class=HTMLResponse)
    def index(request: Request):
        if project_root is None and not project_prefix(request):
            return RedirectResponse("/projects", status_code=303)
        project, state = load(request)
        scenes = []
        for scene_id in project.scene_ids():
            shots = project.shots(scene_id)
            scenes.append(
                {
                    "scene": project.scene(scene_id),
                    "shots": shots,
                    "done": sum(1 for s in shots if s.status.stage is Stage.done),
                    "verdicts": checks.scene_summary(project, project.scene(scene_id)),
                }
            )
        return render(
            request,
            "index.html",
            {
                **inbox_context(project, state),
                "scenes": scenes,
                "log": state.approvals(limit=8),
                "page": "scenes",
            },
        )

    def render_breakdown(
        request: Request, project: Project, *, status_code: int = 200, fragment: bool = False,
        **context: Any
    ) -> HTMLResponse:
        return render(
            request,
            "partials/_breakdown_proposal.html" if fragment else "breakdown.html",
            {
                "project": project,
                "page": "breakdown",
                "chapters": breakdown.chapter_names(project),
                **context,
            },
            status_code=status_code,
        )

    @routes.get("/breakdown", response_class=HTMLResponse)
    def breakdown_page(request: Request, chapter: str | None = None) -> HTMLResponse:
        project, _ = load(request)
        names = breakdown.chapter_names(project)
        selected = chapter or (names[0] if names else None)
        source, error = None, None
        if selected:
            try:
                source = breakdown.read_chapter(project, selected)
            except breakdown.BreakdownError as exc:
                error = str(exc)
        return render_breakdown(request, project, source=source, selected=selected, error=error)

    @routes.post("/breakdown/propose", response_class=HTMLResponse)
    async def generate_breakdown(request: Request, chapter: str = Form(...)) -> Response:
        project, _ = load(request)

        def generate(progress: StreamProgress | None) -> HTMLResponse:
            source = None
            try:
                if progress:
                    progress.status("Reading the chapter…")
                source = breakdown.read_chapter(project, chapter)
                if progress:
                    progress.status("Waiting for the chapter breakdown…")
                book_progress = (
                    progress.engine("Drafting scenes and bible entries…") if progress else None
                )
                book = breakdown.propose_book(
                    project, source, progress=book_progress,
                )
                plan = breakdown.WritePlan.for_book(project, book)
                new_scenes = [entry.spec for entry in plan.files if isinstance(entry.spec, Scene)]
                if progress:
                    progress.send({"type": "scenes", "ids": [scene.id for scene in new_scenes]})
                for index, scene in enumerate(new_scenes, 1):
                    if progress:
                        progress.status(f"Planning scene {index} of {len(new_scenes)}…")
                    try:
                        shots = breakdown.propose_shots(
                            project, scene, source, book=book,
                            progress=progress.engine(f"Drafting shots for {scene.id}…")
                            if progress else None,
                        )
                    except breakdown.BreakdownError as exc:
                        raise breakdown.BreakdownError(f"{scene.id}: {exc}") from exc
                    plan.add(breakdown.WritePlan.for_shots(project, scene.id, shots))
                    if progress:
                        progress.send({"type": "scene", "id": scene.id, "shots": len(shots)})
            except BREAKDOWN_ERRORS as exc:
                return render_breakdown(
                    request,
                    project,
                    source=source,
                    selected=source.name if source else chapter,
                    error=str(exc),
                    status_code=400,
                    fragment=progress is not None,
                )
            draft = BreakdownDraft(project.paths.root, source, book, plan)
            token = uuid4().hex
            with drafts_lock:
                drafts[token] = draft
                if len(drafts) > 16:
                    drafts.popitem(last=False)
            return review_breakdown(request, project, token, draft, fragment=progress is not None)

        return await render_with_progress(request, generate)

    def review_breakdown(
        request: Request, project: Project, token: str, draft: BreakdownDraft, **context: Any
    ) -> HTMLResponse:
        shots: dict[str, list[Shot]] = {}
        for entry in draft.plan.files:
            if isinstance(entry.spec, Shot):
                shots.setdefault(entry.scene_id, []).append(entry.spec)
        return render_breakdown(
            request,
            project,
            source=draft.chapter,
            selected=draft.chapter.name,
            token=token,
            draft=draft,
            shots=shots,
            collisions=draft.plan.collisions,
            characters=sorted(draft.character_ids(project)),
            **context,
        )

    @routes.post("/breakdown/approve", response_class=HTMLResponse)
    async def approve_breakdown(request: Request, token: str = Form(...)) -> HTMLResponse:
        project, _ = load(request)
        form = await request.form()
        with drafts_lock:
            draft = drafts.get(token)
            if draft is not None and draft.project_root != project.paths.root:
                draft = None
            if draft is not None:
                if draft.approving:
                    raise HTTPException(409, "This proposal is already being saved.")
                draft.approving = True
        if draft is None:
            return render_breakdown(
                request,
                project,
                error="This proposal has expired. Generate another to review.",
                status_code=409,
            )
        try:
            plan = draft.edit_beats(project, form)
            written, collisions = await run_in_threadpool(plan.save, project)
        except BREAKDOWN_ERRORS as exc:
            return review_breakdown(
                request, project, token, draft, form=form, error=str(exc), status_code=400
            )
        else:
            with drafts_lock:
                drafts.pop(token, None)
        finally:
            with drafts_lock:
                draft.approving = False
        return render_breakdown(
            request,
            project,
            source=draft.chapter,
            selected=draft.chapter.name,
            written=written,
            saved_scenes=[
                entry.spec for entry in plan.files
                if isinstance(entry.spec, Scene) and entry.path in written
            ],
            collisions=[*plan.collisions, *collisions],
        )

    @routes.get("/scene/{scene_id}", response_class=HTMLResponse)
    def storyboard(request: Request, scene_id: str) -> HTMLResponse:
        project, state = load(request)
        try:
            scene = project.scene(scene_id)
        except ProjectError as exc:
            raise HTTPException(404, str(exc)) from exc
        rows = []
        for shot in project.shots(scene_id):
            shot_paths = project.paths.shot(scene_id, shot.id)
            rows.append(
                {
                    "shot": shot,
                    "label": actions.stage_label(shot),
                    "action": next_action(project, state, scene_id, shot.id),
                    "blockout": project.paths.blockout(scene_id, shot.id),
                    "frame": shot_paths.approved_frame,
                    "video": shot_paths.approved_video,
                    "last_frame": shot_paths.last_frame,
                    "verdict": checks.summarise(checks.check_spec(project, scene_id, shot)),
                    "stage_index": ORDER.index(shot.status),
                }
            )
        return render(
            request,
            "storyboard.html",
            {
                "project": project,
                "scene": scene,
                "rows": rows,
                "findings": checks.check_scene_chain(project, scene_id)
                + continuity.scene_findings(project, scene),
                "active": state.active,
                "page": "storyboard",
            },
        )

    @routes.post("/scene/{scene_id}/continuity", response_class=HTMLResponse)
    async def review_continuity(request: Request, scene_id: str) -> Response:
        project, _ = load(request)
        try:
            scene = project.scene(scene_id)
        except ProjectError as exc:
            raise HTTPException(404, str(exc)) from exc

        def generate(progress: StreamProgress | None) -> HTMLResponse:
            if progress:
                progress.status("Checking scene continuity…")
            findings = checks.check_scene_chain(project, scene_id)
            findings += continuity.scene_findings(
                project, scene, run=True,
                progress=progress.engine("Claude is reviewing the cut…") if progress else None,
            )
            return render(
                request, "partials/_findings.html", {"project": project, "findings": findings}
            )

        return await render_with_progress(request, generate)

    @routes.get("/shot/{scene_id}/{shot_id}", response_class=HTMLResponse)
    def shot_page(request: Request, scene_id: str, shot_id: str) -> HTMLResponse:
        context = shot_card_context(request, scene_id, shot_id)
        context["page"] = "shot"
        return render(request, "shot.html", context)

    @routes.get("/shot/{scene_id}/{shot_id}/card", response_class=HTMLResponse)
    def shot_card(request: Request, scene_id: str, shot_id: str) -> HTMLResponse:
        return render(request, "partials/_card.html", shot_card_context(request, scene_id, shot_id))

    def region(
        request: Request,
        name: str,
        scene_id: str,
        shot_id: str,
        *,
        also: tuple[str, ...] = (),
        context: dict[str, Any] | None = None,
    ) -> HTMLResponse:
        """Redraw one region of the shot card, and tell the others to follow.

        Nothing here redraws the whole card: a prompt lands in `do`, a take in
        `see`, a verdict in `judge`, and each region listens for its own event.
        """
        if name not in REGIONS:
            raise HTTPException(404, f"no region {name!r}")
        response = render(
            request, REGIONS[name], context or shot_card_context(request, scene_id, shot_id)
        )
        if also:
            response.headers["HX-Trigger"] = ", ".join(f"{other}-changed" for other in also)
        return response

    @routes.get("/shot/{scene_id}/{shot_id}/compare", response_class=HTMLResponse)
    def compare_view(
        request: Request,
        scene_id: str,
        shot_id: str,
        left: str | None = None,
        right: str | None = None,
    ) -> HTMLResponse:
        """Two images at one scale, to be judged against each other."""
        project, _ = load(request)
        try:
            project.shot(scene_id, shot_id)
        except ProjectError as exc:
            raise HTTPException(404, str(exc)) from exc
        found = compare.references(project, scene_id, shot_id)
        if not found:
            raise HTTPException(404, "nothing to compare yet for this shot")
        return render(
            request,
            "compare.html",
            {
                "project": project,
                "scene": project.scene(scene_id),
                "shot": project.shot(scene_id, shot_id),
                "references": found,
                "groups": compare.grouped(found),
                # Newest against the one before it: what you reach for most.
                "left": compare.pick(found, left, len(found) - 2),
                "right": compare.pick(found, right, len(found) - 1),
                "page": "shot",
            },
        )

    @routes.get("/shot/{scene_id}/{shot_id}/region/{name}", response_class=HTMLResponse)
    def shot_region(request: Request, scene_id: str, shot_id: str, name: str) -> HTMLResponse:
        return region(request, name, scene_id, shot_id)

    @routes.get("/shot/{scene_id}/{shot_id}/takes", response_class=HTMLResponse)
    def shot_takes(request: Request, scene_id: str, shot_id: str) -> HTMLResponse:
        return region(request, "see", scene_id, shot_id)

    register_bible(routes, load, render)
    register_edit(routes, load, render)
    register_settings(routes, load, render)

    @routes.get("/log", response_class=HTMLResponse)
    def log_page(request: Request) -> HTMLResponse:
        project, state = load(request)
        return render(
            request,
            "log.html",
            {"project": project, "entries": state.approvals(limit=200), "page": "log"},
        )

    @routes.get("/inbox", response_class=HTMLResponse)
    def inbox_partial(request: Request) -> HTMLResponse:
        project, state = load(request)
        return render(
            request,
            "partials/_inbox.html",
            inbox_context(project, state),
        )

    # ---- actions ---------------------------------------------------------
    @routes.post("/shot/{scene_id}/{shot_id}/camera", response_class=HTMLResponse)
    async def propose_camera(request: Request, scene_id: str, shot_id: str) -> Response:
        project, _ = load(request)

        def generate(progress: StreamProgress | None) -> HTMLResponse:
            try:
                if progress:
                    progress.status("Reviewing the scene and neighbouring shots…")
                proposal, diff = cinematography.propose_camera(
                    project, scene_id, shot_id,
                    progress=progress.engine("Drafting camera changes…") if progress else None,
                )
            except ProjectError as exc:
                raise HTTPException(404, str(exc)) from exc
            except CAMERA_ERRORS as exc:
                return render(
                    request,
                    "partials/_camera_proposal.html",
                    {"project": project, "camera_error": str(exc)},
                )
            token = None
            if diff:
                token = uuid4().hex
                with drafts_lock:
                    camera_drafts[token] = (project.paths.root, scene_id, shot_id, proposal)
                    if len(camera_drafts) > 16:
                        camera_drafts.popitem(last=False)
            return render(
                request,
                "partials/_camera_proposal.html",
                {
                    "project": project,
                    "camera_token": token,
                    "camera_diff": diff,
                    "shot_url": f"{project_prefix(request)}/shot/{scene_id}/{shot_id}",
                },
            )

        return await render_with_progress(request, generate)

    @routes.post("/shot/{scene_id}/{shot_id}/camera/apply", response_class=HTMLResponse)
    def apply_camera(
        request: Request, scene_id: str, shot_id: str, token: str = Form(...)
    ) -> HTMLResponse:
        project, _ = load(request)
        with drafts_lock:
            draft = camera_drafts.get(token)
            if draft is None or draft[:3] != (project.paths.root, scene_id, shot_id):
                raise HTTPException(409, "Generate a new camera proposal for this shot.")
            try:
                cinematography.apply_camera(project, scene_id, shot_id, draft[3])
            except ProjectError as exc:
                raise HTTPException(404, str(exc)) from exc
            except CAMERA_ERRORS as exc:
                raise HTTPException(400, str(exc)) from exc
            camera_drafts.pop(token)
        return shot_card(request, scene_id, shot_id)

    @routes.post("/shot/{scene_id}/{shot_id}/prompt", response_class=HTMLResponse)
    def make_prompt(
        request: Request, scene_id: str, shot_id: str, note: str | None = Form(None)
    ) -> HTMLResponse:
        project, state = load(request)
        try:
            actions.build_prompt(project, state, scene_id, shot_id, note=note)
        except (ValueError, TransitionError, SkillError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return region(request, "do", scene_id, shot_id)

    @routes.post("/shot/{scene_id}/{shot_id}/fix", response_class=HTMLResponse)
    def make_fix(
        request: Request,
        scene_id: str,
        shot_id: str,
        take: int = Form(...),
        finding: str = Form(...),
    ) -> HTMLResponse:
        context = shot_card_context(request, scene_id, shot_id)
        project = context["project"]
        shot_paths = context["shot_paths"]
        try:
            context["fix_prompt"] = fixes.build_fix(project, scene_id, shot_id, take, finding)
            source = get_take(shot_paths, TakeKind.frame, take)
        except (FileNotFoundError, ValueError, SkillError) as exc:
            raise HTTPException(400, str(exc)) from exc
        report = actions.read_qa_report(source.qa_report(shot_paths))
        if report is not None:
            context.update(_qa_context(source, report))
        return region(request, "do", scene_id, shot_id, context=context)

    @routes.post("/shot/{scene_id}/{shot_id}/blockout", response_class=HTMLResponse)
    def render_blockout(request: Request, scene_id: str, shot_id: str) -> HTMLResponse:
        project, _ = load(request)
        try:
            shot = project.shot(scene_id, shot_id)
        except ProjectError as exc:
            raise HTTPException(404, str(exc)) from exc
        if shot.status is not ShotStatus.blockout:
            raise HTTPException(400, "Start the Blockout stage before rendering.")
        try:
            blender.render(scene_id, shot_id, project=project)
        except blender.BlockoutError as exc:
            raise HTTPException(400, str(exc)) from exc
        return region(request, "do", scene_id, shot_id, also=("gate",))

    @routes.post("/shot/{scene_id}/{shot_id}/begin", response_class=HTMLResponse)
    def begin_stage(request: Request, scene_id: str, shot_id: str) -> HTMLResponse:
        project, state = load(request)
        try:
            actions.begin(project, state, scene_id, shot_id)
        except TransitionError as exc:
            raise HTTPException(400, str(exc)) from exc
        return shot_card(request, scene_id, shot_id)

    @routes.post("/shot/{scene_id}/{shot_id}/pass2", response_class=HTMLResponse)
    def to_pass_two(request: Request, scene_id: str, shot_id: str) -> HTMLResponse:
        project, state = load(request)
        try:
            actions.advance_frame_pass(project, state, scene_id, shot_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return shot_card(request, scene_id, shot_id)

    @routes.post("/shot/{scene_id}/{shot_id}/approve", response_class=HTMLResponse)
    def approve_stage(
        request: Request,
        scene_id: str,
        shot_id: str,
        take: int | None = Form(None),
        note: str | None = Form(None),
    ) -> HTMLResponse:
        project, state = load(request)
        shot = project.shot(scene_id, shot_id)
        note = (note or "").strip() or None
        try:
            if shot.status.stage is Stage.frame:
                number = take or _latest(project, scene_id, shot_id, TakeKind.frame)
                actions.approve_frame(project, state, scene_id, shot_id, number, note)
            elif shot.status.stage is Stage.video:
                number = take or _latest(project, scene_id, shot_id, TakeKind.video)
                actions.approve_video(project, state, scene_id, shot_id, number, note)
            else:
                from shotforge.pipeline import approve

                with drafts_lock:
                    approve(project, state, scene_id, shot_id, note=note)
        except (TransitionError, ValueError, FileNotFoundError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return shot_card(request, scene_id, shot_id)

    @routes.get("/shot/{scene_id}/{shot_id}/undo", response_class=HTMLResponse)
    def undo_confirm(request: Request, scene_id: str, shot_id: str) -> HTMLResponse:
        project, state = load(request)
        try:
            plan = undo.plan_undo(project, state, scene_id, shot_id)
        except (TransitionError, ProjectError) as exc:
            raise HTTPException(400, str(exc)) from exc
        shot_paths = project.paths.shot(scene_id, shot_id)
        kept = len(list_takes(shot_paths, TakeKind.frame)) + len(
            list_takes(shot_paths, TakeKind.video)
        )
        return render(
            request,
            "partials/_undo.html",
            {
                "project": project,
                "scene": project.scene(scene_id),
                "shot": project.shot(scene_id, shot_id),
                "plan": plan,
                "kept_takes": kept,
            },
        )

    @routes.post("/shot/{scene_id}/{shot_id}/undo", response_class=HTMLResponse)
    def undo_approval(
        request: Request,
        scene_id: str,
        shot_id: str,
        note: str | None = Form(None),
    ) -> HTMLResponse:
        project, state = load(request)
        try:
            undo.undo_approval(project, state, scene_id, shot_id, note=(note or "").strip() or None)
        except (TransitionError, ProjectError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(500, str(exc)) from exc
        return shot_card(request, scene_id, shot_id)

    @routes.post("/shot/{scene_id}/{shot_id}/take/{kind}/{number}/reject")
    def reject_take(
        request: Request,
        scene_id: str,
        shot_id: str,
        kind: TakeKind,
        number: int,
        restore: bool = Form(False),
    ) -> HTMLResponse:
        project, state = load(request)
        try:
            get_take(project.paths.shot(scene_id, shot_id), kind, number)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        state.set_take_rejected(scene_id, shot_id, kind.value, number, not restore)
        return region(request, "see", scene_id, shot_id, also=("judge", "gate"))

    @routes.post("/shot/{scene_id}/{shot_id}/reopen", response_class=HTMLResponse)
    def reopen_stage(
        request: Request,
        scene_id: str,
        shot_id: str,
        stage: str = Form(...),
        note: str | None = Form(None),
    ) -> HTMLResponse:
        project, state = load(request)
        try:
            reopen(project, state, scene_id, shot_id, Stage(stage), note=(note or None))
        except (TransitionError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return shot_card(request, scene_id, shot_id)

    @routes.post("/shot/{scene_id}/{shot_id}/qa", response_class=HTMLResponse)
    async def run_qa(
        request: Request,
        scene_id: str,
        shot_id: str,
        kind: TakeKind = Form(TakeKind.frame),
        take: int | None = Form(None),
        vision: bool | None = Form(None),
    ) -> Response:
        project, state = load(request)

        def generate(progress: StreamProgress | None) -> HTMLResponse:
            number = take if take is not None else _latest(project, scene_id, shot_id, kind)
            model_subject = "the frame" if kind is TakeKind.frame else "the eyelines"
            try:
                findings = actions.run_qa(
                    project, scene_id, shot_id, kind, number, vision=vision,
                    progress=progress.engine(f"Claude is reviewing {model_subject}…")
                    if progress else None,
                )
                source = get_take(project.paths.shot(scene_id, shot_id), kind, number)
            except FileNotFoundError as exc:
                raise HTTPException(400, str(exc)) from exc
            shot = project.shot(scene_id, shot_id)
            response = render(
                request,
                "partials/_findings.html",
                {
                    "project": project,
                    "scene": project.scene(scene_id),
                    "shot": shot,
                    **_qa_context(source, findings),
                    "rule_offers": fixes.rule_offers(project, state, scene_id, shot),
                },
            )
            if take is None:
                # The verdict shows on the take and at the gate; neither needs
                # the whole card redrawn.
                response.headers["HX-Trigger-After-Swap"] = "see-changed, gate-changed"
            return response

        return await render_with_progress(request, generate)

    @routes.post("/shot/{scene_id}/{shot_id}/active", response_class=HTMLResponse)
    def set_active(request: Request, scene_id: str, shot_id: str) -> HTMLResponse:
        project, state = load(request)
        project.shot(scene_id, shot_id)
        state.set_active(scene_id, shot_id)
        return shot_card(request, scene_id, shot_id)

    @routes.post("/shot/{scene_id}/{shot_id}/upload", response_class=HTMLResponse)
    def upload(
        request: Request, scene_id: str, shot_id: str, files: list[UploadFile]
    ) -> HTMLResponse:
        project, state = load(request)
        shot_paths = project.paths.shot(scene_id, shot_id)
        if not shot_paths.spec.is_file():
            raise HTTPException(404, f"no shot {shot_id} in {scene_id}")
        try:
            for upload_file in files:
                with inbox_mod.stage_upload(
                    project, upload_file.filename or "upload.bin", upload_file.file
                ) as source:
                    take = inbox_mod.file_to_shot(project, source, scene_id, shot_id)
                    actions.note_take_origin(project, state, scene_id, shot_id, take)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return region(request, "see", scene_id, shot_id, also=("judge", "gate"))

    @routes.post("/inbox/file", response_class=HTMLResponse)
    def file_from_inbox(
        request: Request,
        name: str = Form(...),
        scene: str | None = Form(None),
        shot: str | None = Form(None),
        texture: str | None = Form(None),
    ) -> HTMLResponse:
        project, state = load(request)
        source = project.paths.inbox / Path(name).name
        try:
            if texture:
                if scene or shot:
                    raise ValueError("choose a texture or a shot as the inbox destination")
                inbox_mod.file_to_texture(project, source, texture)
            else:
                target = (scene, shot) if scene and shot else state.active
                if target is None:
                    raise ValueError("no active shot — open a shot card first")
                take = inbox_mod.file_to_shot(project, source, *target)
                actions.note_take_origin(project, state, *target, take)
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return inbox_partial(request)

    # ---- media and events ------------------------------------------------
    @routes.get("/media/{relative_path:path}")
    def media(request: Request, relative_path: str) -> FileResponse:
        project, _ = load(request)
        target = (project.paths.root / relative_path).resolve()
        try:
            target.relative_to(project.paths.root)
        except ValueError as exc:
            raise HTTPException(403, "outside the project") from exc
        if not target.is_file():
            raise HTTPException(404, relative_path)
        return FileResponse(target)

    @routes.get("/events")
    async def events(request: Request) -> StreamingResponse:
        """Server-sent events: the inbox and the approval log, as they change."""

        async def wait(seconds: float) -> None:
            """Sleep, but notice a shutdown while we are in it."""
            slept = 0.0
            while slept < seconds and not shutting_down():
                step = min(0.25, seconds - slept)
                await asyncio.sleep(step)
                slept += step

        async def stream():
            last = None
            deadline = asyncio.get_running_loop().time() + EVENT_STREAM_SECONDS
            while asyncio.get_running_loop().time() < deadline:
                if shutting_down() or await request.is_disconnected():
                    break
                try:
                    project, state = load(request)
                except HTTPException:
                    break
                payload = {
                    "inbox": len(inbox_mod.scan(project)),
                    "approvals": len(state.approvals()),
                }
                if payload != last:
                    last = payload
                    yield f"data: {json.dumps(payload)}\n\n"
                await wait(EVENT_POLL_SECONDS)
            # The browser's EventSource reconnects on its own.

        return StreamingResponse(stream(), media_type="text/event-stream")

    @routes.get("/skills", response_class=HTMLResponse)
    def skills_page(request: Request) -> HTMLResponse:
        from shotforge.skills import list_skills

        project, _ = load(request)
        return render(
            request,
            "skills.html",
            {
                "project": project,
                "skills": list_skills(project.paths.skills),
                "page": "skills",
            },
        )

    @routes.post("/skills/{name}/rules", response_class=HTMLResponse)
    def add_rule(request: Request, name: str, rule: str = Form(...)) -> HTMLResponse:
        """A fix that worked becomes a rule — on your say-so, never automatically."""
        project, _ = load(request)
        try:
            skill = find_skill(name, project.paths.skills)
            skill.add_rule(rule)
        except SkillError as exc:
            raise HTTPException(400, str(exc)) from exc
        return RedirectResponse(f"{project_prefix(request)}/skills", status_code=303)

    def _latest(project: Project, scene_id: str, shot_id: str, kind: TakeKind) -> int:
        state = ProjectState(project.paths)
        try:
            return actions.latest_take_number(project, state, scene_id, shot_id, kind)
        except FileNotFoundError as exc:
            raise HTTPException(400, str(exc)) from exc

    app.include_router(routes, prefix="/p/{project_id}")
    app.include_router(routes)
    return app


def app_from_env() -> FastAPI:
    """Factory for ``uvicorn --reload``; the project folder comes from the env."""
    root = os.environ.get("SHOTFORGE_PROJECT")
    return create_app(Path(root) if root else None, enforce_local=True)
