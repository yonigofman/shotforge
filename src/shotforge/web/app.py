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
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import FormData
from starlette.responses import FileResponse

from shotforge import actions, blender, breakdown, checks, continuity, tools
from shotforge import inbox as inbox_mod
from shotforge.console import setup_console
from shotforge.models import Scene, Shot, ShotStatus, Stage, as_plain
from shotforge.pipeline import ORDER, STAGE_LABEL, TransitionError, next_action, reopen
from shotforge.project import Project, ProjectError
from shotforge.skills import find_skill, latest_prompt
from shotforge.skills.registry import SkillError
from shotforge.state import ProjectState
from shotforge.takes import TakeKind, approved_take_number, list_takes
from shotforge.yamlio import ProjectFileError

HERE = Path(__file__).resolve().parent


def _number(value: float | int) -> str:
    """15 rather than 15.0 — the same spelling as in the prompts."""
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)


STAGES: tuple[Stage, ...] = (Stage.breakdown, Stage.blockout, Stage.frame, Stage.video)
BREAKDOWN_ERRORS = (breakdown.BreakdownError, ProjectError, ProjectFileError, OSError)


@dataclass
class BreakdownDraft:
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


def create_app(project_root: Path) -> FastAPI:
    setup_console()
    project_root = Path(project_root).resolve()
    app = FastAPI(title="Shotforge cockpit", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters["n"] = _number
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    drafts: OrderedDict[str, BreakdownDraft] = OrderedDict()
    drafts_lock = Lock()

    # ---- helpers ---------------------------------------------------------
    def load() -> tuple[Project, ProjectState]:
        """Re-read the project on every request: files are the source of truth."""
        try:
            project = Project.open(project_root)
        except (ProjectError, ProjectFileError) as exc:
            raise HTTPException(500, str(exc)) from exc
        tools.configure(project.config.tool_paths.model_dump())
        return project, ProjectState(project.paths)

    def media_url(project: Project, path: Path) -> str:
        return f"/media/{project.paths.rel(path)}"

    def render(
        request: Request, name: str, context: dict[str, Any], *, status_code: int = 200
    ) -> HTMLResponse:
        project: Project = context["project"]
        context.setdefault("stages", STAGES)
        context.setdefault("stage_label", STAGE_LABEL)
        context.setdefault("media_url", lambda path: media_url(project, path))
        context.setdefault("rel", project.paths.rel)
        context.setdefault("vendored", (HERE / "static" / "vendor" / "htmx.min.js").is_file())
        return templates.TemplateResponse(request, name, context, status_code=status_code)

    def shot_card_context(request: Request, scene_id: str, shot_id: str) -> dict[str, Any]:
        project, state = load()
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
        qa_takes = video_takes or frame_takes
        if frame_takes and shot.status.stage is Stage.frame:
            qa_takes = frame_takes
        report_context = {}
        if qa_takes:
            latest = qa_takes[-1]
            report_context["qa_kind"] = latest.kind.value
            report = (
                frame_reports.get(latest.number) if latest.kind is TakeKind.frame
                else actions.read_qa_report(latest.qa_report(shot_paths))
            )
            if report is not None:
                report_context.update(
                    findings=report, verdict=checks.summarise(report),
                    subject=f"{latest.kind.value} v{latest.number}",
                )
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
            "approved_frame": approved_take_number(shot_paths, TakeKind.frame),
            "approved_video": approved_take_number(shot_paths, TakeKind.video),
            "findings": checks.check_spec(project, scene_id, shot),
            "cut_findings": continuity.shot_findings(project, scene, shot, previous),
            "previous": previous,
            "active": state.active == (scene_id, shot_id),
            "stage_label": STAGE_LABEL,
            "label": actions.stage_label(shot),
            **report_context,
        }

    # ---- pages -----------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        project, state = load()
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
                "project": project,
                "scenes": scenes,
                "inbox": inbox_mod.scan(project),
                "active": state.active,
                "log": state.approvals(limit=8),
                "page": "projects",
            },
        )

    def render_breakdown(
        request: Request, project: Project, *, status_code: int = 200, **context: Any
    ) -> HTMLResponse:
        return render(
            request,
            "breakdown.html",
            {
                "project": project,
                "page": "breakdown",
                "chapters": breakdown.chapter_names(project),
                **context,
            },
            status_code=status_code,
        )

    @app.get("/breakdown", response_class=HTMLResponse)
    def breakdown_page(request: Request, chapter: str | None = None) -> HTMLResponse:
        project, _ = load()
        names = breakdown.chapter_names(project)
        selected = chapter or (names[0] if names else None)
        source, error = None, None
        if selected:
            try:
                source = breakdown.read_chapter(project, selected)
            except breakdown.BreakdownError as exc:
                error = str(exc)
        return render_breakdown(request, project, source=source, selected=selected, error=error)

    @app.post("/breakdown/propose", response_class=HTMLResponse)
    def generate_breakdown(request: Request, chapter: str = Form(...)) -> HTMLResponse:
        project, _ = load()
        source = None
        try:
            source = breakdown.read_chapter(project, chapter)
            book = breakdown.propose_book(project, source)
            plan = breakdown.WritePlan.for_book(project, book)
            new_scenes = [entry.spec for entry in plan.files if isinstance(entry.spec, Scene)]
            for scene in new_scenes:
                try:
                    shots = breakdown.propose_shots(project, scene, source, book=book)
                except breakdown.BreakdownError as exc:
                    raise breakdown.BreakdownError(f"{scene.id}: {exc}") from exc
                plan.add(breakdown.WritePlan.for_shots(project, scene.id, shots))
        except BREAKDOWN_ERRORS as exc:
            return render_breakdown(
                request,
                project,
                source=source,
                selected=source.name if source else chapter,
                error=str(exc),
                status_code=400,
            )
        draft = BreakdownDraft(source, book, plan)
        token = uuid4().hex
        with drafts_lock:
            drafts[token] = draft
            if len(drafts) > 16:
                drafts.popitem(last=False)
        return review_breakdown(request, project, token, draft)

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

    @app.post("/breakdown/approve", response_class=HTMLResponse)
    async def approve_breakdown(request: Request, token: str = Form(...)) -> HTMLResponse:
        project, _ = load()
        form = await request.form()
        with drafts_lock:
            draft = drafts.get(token)
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

    @app.get("/scene/{scene_id}", response_class=HTMLResponse)
    def storyboard(request: Request, scene_id: str) -> HTMLResponse:
        project, state = load()
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

    @app.post("/scene/{scene_id}/continuity", response_class=HTMLResponse)
    def review_continuity(request: Request, scene_id: str) -> HTMLResponse:
        project, _ = load()
        try:
            scene = project.scene(scene_id)
        except ProjectError as exc:
            raise HTTPException(404, str(exc)) from exc
        findings = checks.check_scene_chain(project, scene_id)
        findings += continuity.scene_findings(project, scene, run=True)
        return render(
            request, "partials/_findings.html", {"project": project, "findings": findings}
        )

    @app.get("/shot/{scene_id}/{shot_id}", response_class=HTMLResponse)
    def shot_page(request: Request, scene_id: str, shot_id: str) -> HTMLResponse:
        context = shot_card_context(request, scene_id, shot_id)
        context["page"] = "shot"
        return render(request, "shot.html", context)

    @app.get("/shot/{scene_id}/{shot_id}/card", response_class=HTMLResponse)
    def shot_card(request: Request, scene_id: str, shot_id: str) -> HTMLResponse:
        return render(request, "partials/_card.html", shot_card_context(request, scene_id, shot_id))

    @app.get("/shot/{scene_id}/{shot_id}/takes", response_class=HTMLResponse)
    def shot_takes(request: Request, scene_id: str, shot_id: str) -> HTMLResponse:
        context = shot_card_context(request, scene_id, shot_id)
        return render(request, "partials/_takes.html", context)

    @app.get("/bible", response_class=HTMLResponse)
    def bible(request: Request) -> HTMLResponse:
        project, _ = load()
        kinds = []
        for kind in ("character", "location", "prop"):
            entities = []
            for entity_id, entity in project.entities(kind).items():
                entities.append(
                    {
                        "entity": entity,
                        "sheet": project.paths.sheet(kind, entity_id),
                        "ref": project.paths.ref(kind, entity_id),
                    }
                )
            kinds.append({"kind": kind, "entities": entities})
        return render(
            request,
            "bible.html",
            {"project": project, "kinds": kinds, "style": project.style(), "page": "bible"},
        )

    @app.get("/log", response_class=HTMLResponse)
    def log_page(request: Request) -> HTMLResponse:
        project, state = load()
        return render(
            request,
            "log.html",
            {"project": project, "entries": state.approvals(limit=200), "page": "log"},
        )

    @app.get("/inbox", response_class=HTMLResponse)
    def inbox_partial(request: Request) -> HTMLResponse:
        project, state = load()
        return render(
            request,
            "partials/_inbox.html",
            {"project": project, "inbox": inbox_mod.scan(project), "active": state.active},
        )

    # ---- actions ---------------------------------------------------------
    @app.post("/shot/{scene_id}/{shot_id}/prompt", response_class=HTMLResponse)
    def make_prompt(request: Request, scene_id: str, shot_id: str) -> HTMLResponse:
        project, state = load()
        try:
            actions.build_prompt(project, state, scene_id, shot_id)
        except (ValueError, TransitionError, SkillError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return shot_card(request, scene_id, shot_id)

    @app.post("/shot/{scene_id}/{shot_id}/blockout", response_class=HTMLResponse)
    def render_blockout(request: Request, scene_id: str, shot_id: str) -> HTMLResponse:
        project, _ = load()
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
        return shot_card(request, scene_id, shot_id)

    @app.post("/shot/{scene_id}/{shot_id}/begin", response_class=HTMLResponse)
    def begin_stage(request: Request, scene_id: str, shot_id: str) -> HTMLResponse:
        project, state = load()
        try:
            actions.begin(project, state, scene_id, shot_id)
        except TransitionError as exc:
            raise HTTPException(400, str(exc)) from exc
        return shot_card(request, scene_id, shot_id)

    @app.post("/shot/{scene_id}/{shot_id}/pass2", response_class=HTMLResponse)
    def to_pass_two(request: Request, scene_id: str, shot_id: str) -> HTMLResponse:
        project, state = load()
        try:
            actions.advance_frame_pass(project, state, scene_id, shot_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return shot_card(request, scene_id, shot_id)

    @app.post("/shot/{scene_id}/{shot_id}/approve", response_class=HTMLResponse)
    def approve_stage(
        request: Request,
        scene_id: str,
        shot_id: str,
        take: int | None = Form(None),
        note: str | None = Form(None),
    ) -> HTMLResponse:
        project, state = load()
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

                approve(project, state, scene_id, shot_id, note=note)
        except (TransitionError, ValueError, FileNotFoundError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return shot_card(request, scene_id, shot_id)

    @app.post("/shot/{scene_id}/{shot_id}/reopen", response_class=HTMLResponse)
    def reopen_stage(
        request: Request,
        scene_id: str,
        shot_id: str,
        stage: str = Form(...),
        note: str | None = Form(None),
    ) -> HTMLResponse:
        project, state = load()
        try:
            reopen(project, state, scene_id, shot_id, Stage(stage), note=(note or None))
        except (TransitionError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return shot_card(request, scene_id, shot_id)

    @app.post("/shot/{scene_id}/{shot_id}/qa", response_class=HTMLResponse)
    def run_qa(
        request: Request,
        scene_id: str,
        shot_id: str,
        kind: str = Form("frame"),
        take: int | None = Form(None),
        vision: bool | None = Form(None),
    ) -> HTMLResponse:
        project, _ = load()
        take_kind = TakeKind(kind)
        number = take or _latest(project, scene_id, shot_id, take_kind)
        try:
            findings = actions.run_qa(project, scene_id, shot_id, take_kind, number, vision=vision)
        except FileNotFoundError as exc:
            raise HTTPException(400, str(exc)) from exc
        response = render(
            request,
            "partials/_findings.html",
            {
                "project": project,
                "findings": findings,
                "verdict": checks.summarise(findings),
                "subject": f"{take_kind.value} v{number}",
            },
        )
        if take_kind is TakeKind.frame and take is None:
            response.headers["HX-Trigger-After-Swap"] = "card-changed"
        return response

    @app.post("/shot/{scene_id}/{shot_id}/active", response_class=HTMLResponse)
    def set_active(request: Request, scene_id: str, shot_id: str) -> HTMLResponse:
        project, state = load()
        project.shot(scene_id, shot_id)
        state.set_active(scene_id, shot_id)
        return shot_card(request, scene_id, shot_id)

    @app.post("/shot/{scene_id}/{shot_id}/upload", response_class=HTMLResponse)
    async def upload(
        request: Request, scene_id: str, shot_id: str, files: list[UploadFile]
    ) -> HTMLResponse:
        project, _ = load()
        shot_paths = project.paths.shot(scene_id, shot_id)
        if not shot_paths.spec.is_file():
            raise HTTPException(404, f"no shot {shot_id} in {scene_id}")
        staging = project.paths.inbox
        staging.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=staging, prefix=".upload-") as folder:
            for upload_file in files:
                name = Path(upload_file.filename or "upload.bin").name
                temporary = Path(folder) / name
                kind = inbox_mod.classify(temporary)
                if kind is None:
                    raise HTTPException(400, f"{name} is not an image or a video Shotforge files")
                temporary.write_bytes(await upload_file.read())
                await run_in_threadpool(
                    inbox_mod.file_to_shot, project, temporary, scene_id, shot_id, kind
                )
        context = shot_card_context(request, scene_id, shot_id)
        return render(request, "partials/_takes.html", context)

    @app.post("/inbox/file", response_class=HTMLResponse)
    def file_from_inbox(
        request: Request,
        name: str = Form(...),
        scene: str | None = Form(None),
        shot: str | None = Form(None),
    ) -> HTMLResponse:
        project, state = load()
        target = (scene, shot) if scene and shot else state.active
        if target is None:
            raise HTTPException(400, "no active shot — open a shot card first")
        try:
            inbox_mod.file_to_shot(project, project.paths.inbox / Path(name).name, *target)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return inbox_partial(request)

    # ---- media and events ------------------------------------------------
    @app.get("/media/{relative_path:path}")
    def media(relative_path: str) -> FileResponse:
        project, _ = load()
        target = (project.paths.root / relative_path).resolve()
        try:
            target.relative_to(project.paths.root)
        except ValueError as exc:
            raise HTTPException(403, "outside the project") from exc
        if not target.is_file():
            raise HTTPException(404, relative_path)
        return FileResponse(target)

    @app.get("/events")
    async def events(request: Request) -> StreamingResponse:
        """Server-sent events: the inbox and the approval log, as they change."""

        async def stream():
            last = None
            while True:
                if await request.is_disconnected():
                    break
                project, state = load()
                payload = {
                    "inbox": len(inbox_mod.scan(project)),
                    "approvals": len(state.approvals()),
                }
                if payload != last:
                    last = payload
                    yield f"data: {json.dumps(payload)}\n\n"
                await asyncio.sleep(2)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/skills", response_class=HTMLResponse)
    def skills_page(request: Request) -> HTMLResponse:
        from shotforge.skills import list_skills

        project, _ = load()
        return render(
            request,
            "skills.html",
            {
                "project": project,
                "skills": list_skills(project.paths.skills),
                "page": "skills",
            },
        )

    @app.post("/skills/{name}/rules", response_class=HTMLResponse)
    def add_rule(request: Request, name: str, rule: str = Form(...)) -> HTMLResponse:
        """A fix that worked becomes a rule — on your say-so, never automatically."""
        project, _ = load()
        try:
            skill = find_skill(name, project.paths.skills)
            skill.add_rule(rule)
        except SkillError as exc:
            raise HTTPException(400, str(exc)) from exc
        return RedirectResponse("/skills", status_code=303)

    def _latest(project: Project, scene_id: str, shot_id: str, kind: TakeKind) -> int:
        takes = list_takes(project.paths.shot(scene_id, shot_id), kind)
        if not takes:
            raise HTTPException(400, f"no {kind.value} take yet for {shot_id}")
        return takes[-1].number

    return app


def app_from_env() -> FastAPI:
    """Factory for ``uvicorn --reload``; the project folder comes from the env."""
    root = os.environ.get("SHOTFORGE_PROJECT")
    if not root:
        raise RuntimeError("set SHOTFORGE_PROJECT to the project folder")
    return create_app(Path(root))
