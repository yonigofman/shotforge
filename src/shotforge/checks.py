"""Shot spec, continuity and media QA."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from math import copysign
from pathlib import Path

from shotforge.blender.errors import BlockoutError
from shotforge.blender.spec import camera_axis_positions
from shotforge.engine import EngineProgress
from shotforge.media import frame_size, probe_duration
from shotforge.models import Scene, Shot
from shotforge.models.blockout import signed_distance
from shotforge.models.shot import Technique
from shotforge.project import Project, ProjectError
from shotforge.tools import ToolError
from shotforge.yamlio import ProjectFileError

PASS, WARN, FAIL = "pass", "warn", "fail"


@dataclass
class Finding:
    check: str
    level: str
    message: str
    fix: str | None = None
    target: str | None = None
    region: str | None = None

    @property
    def failed(self) -> bool:
        return self.level == FAIL

    @property
    def key(self) -> str:
        """Identify a row even if a new report has reordered its findings."""
        return sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


def check_spec(project: Project, scene_id: str, shot: Shot) -> list[Finding]:
    """Everything the shot spec alone can tell us, plus the cut into this shot."""
    findings: list[Finding] = []
    scene = project.scene(scene_id)
    known_characters = set(project.paths.entity_ids("character"))

    for framed in shot.in_frame:
        if framed.char not in known_characters:
            findings.append(
                Finding(
                    "in_frame in the bible",
                    FAIL,
                    f"{framed.char} is in frame but has no file in 01_bible/characters",
                    fix=f"create 01_bible/characters/{framed.char}.yaml",
                )
            )
        elif scene.seating and scene.seat_of(framed.char) is None:
            findings.append(
                Finding(
                    "seating",
                    WARN,
                    f"{framed.char} is in frame but has no seat in {scene_id}",
                    fix="add them to scene.yaml seating, or remove them from in_frame",
                )
            )

    subject = shot.subject
    if subject is not None and not subject.eyeline:
        findings.append(
            Finding(
                "eyeline",
                WARN,
                f"the subject ({subject.char}) has no eyeline — this is how S06 "
                "looked the wrong way",
                fix="give in_frame[subject].eyeline a frame-relative anchor",
            )
        )

    for speaker in shot.speakers():
        if speaker not in shot.characters:
            findings.append(
                Finding(
                    "speaker in frame",
                    WARN,
                    f"{speaker} speaks but is not listed in in_frame",
                    fix="add them to in_frame (role: offscreen is fine)",
                )
            )

    for beat in shot.beats:
        overrun = beat.line_overruns()
        if overrun:
            findings.append(
                Finding(
                    "dialogue fits the beat",
                    WARN,
                    f"beat {beat.t} is {beat.duration_s}s but the line needs about "
                    f"{overrun}s more at 2.5 words a second",
                    fix="shorten the line or lengthen the beat",
                )
            )

    covered = sum(beat.duration_s for beat in shot.beats)
    if shot.beats and covered < shot.duration_s - 0.5:
        findings.append(
            Finding(
                "beats cover the shot",
                WARN,
                f"beats cover {covered}s of a {shot.duration_s}s shot",
                fix="add a beat or shorten duration_s",
            )
        )

    if shot.camera.blender_cam is None:
        findings.append(
            Finding(
                "camera",
                WARN,
                "no blender_cam: the blockout cannot be rendered from this spec",
                fix="name the camera in camera.blender_cam",
            )
        )
    if shot.camera.move and shot.camera.move != "static":
        findings.append(
            Finding(
                "static camera",
                WARN,
                f"camera.move is {shot.camera.move!r}; the video adapters are locked-off "
                "and camera moves are done in the edit",
                fix="set move: static and plan the move in the edit",
            )
        )

    previous = project.previous_shot(scene_id, shot.id)
    findings.extend(check_camera(project, scene, shot, previous))
    findings.extend(_check_continuity(previous, shot))
    return findings


def _techniques(shot: Shot) -> set[str]:
    names = {"_".join(name.lower().replace("-", " ").split()) for name in shot.technique}
    if "ots" in names:
        names.add(Technique.over_the_shoulder)
    return names


def check_camera(
    project: Project,
    scene: Scene,
    shot: Shot,
    previous: Shot | None,
    *,
    following: Shot | None = None,
    proposal: bool = False,
) -> list[Finding]:
    """Check the foreground and the axis for the cuts affected by this camera."""
    findings: list[Finding] = []
    techniques = _techniques(shot)
    subject = shot.subject
    if Technique.over_the_shoulder in techniques and not (
        subject
        and any(
            framed.char != subject.char and framed.role.startswith("foreground")
            for framed in shot.in_frame
        )
    ):
        findings.append(
            Finding(
                "technique",
                FAIL,
                f"{shot.id}: over-the-shoulder needs another character in the foreground",
                fix="set a foreground role in in_frame or choose another technique",
            )
        )

    matched_reverse = Technique.matched_reverse in techniques
    matched_following = (
        following if following and Technique.matched_reverse in _techniques(following) else None
    )
    if not (proposal or matched_reverse or matched_following):
        return findings

    cameras = [shot]
    if matched_reverse:
        if previous is None:
            findings.append(
                Finding(
                    "axis_180",
                    FAIL,
                    f"{shot.id}: matched reverse needs a preceding shot to match",
                    fix="choose a different technique or establish an angle in an earlier shot",
                )
            )
        else:
            cameras.append(previous)
    if matched_following is not None:
        cameras.append(matched_following)

    try:
        axis, points = camera_axis_positions(project, scene, cameras)
    except (BlockoutError, ProjectError, ProjectFileError, OSError) as exc:
        findings.append(
            Finding(
                "axis_180",
                FAIL,
                f"Could not resolve the 180° line and camera positions: {exc}",
                fix="provide measurable axis_180 and camera.position values, or use blockout cues",
            )
        )
        return findings

    room = copysign(1, signed_distance(axis.start, axis.end, axis.room_side))
    for camera_shot in cameras:
        point = points[camera_shot.id]
        if room * signed_distance(axis.start, axis.end, point) <= 1e-6:
            findings.append(
                Finding(
                    "axis_180",
                    FAIL,
                    f"{camera_shot.id}: camera at XY ({point[0]:.3g}, {point[1]:.3g}) "
                    "must stay on the room side of the 180° line",
                    fix="move the camera to the room side of scene.axis_180",
                )
            )
    return findings


def check_continuity(project: Project, scene_id: str, shot: Shot) -> list[Finding]:
    """``continuity.in`` of this shot against ``continuity.out`` of the one before."""
    return _check_continuity(project.previous_shot(scene_id, shot.id), shot)


def _check_continuity(previous: Shot | None, shot: Shot) -> list[Finding]:
    if previous is None:
        return []
    findings: list[Finding] = []
    out_state = previous.continuity.out
    in_state = shot.continuity.in_
    for key, value in in_state.items():
        if key not in out_state:
            findings.append(
                Finding(
                    "continuity",
                    WARN,
                    f"{key} is set coming in ({value}) but {previous.id} does not say what it "
                    "goes out on",
                    fix=f"add {key} to {previous.id} continuity.out",
                )
            )
        elif out_state[key] != value:
            findings.append(
                Finding(
                    "continuity",
                    FAIL,
                    f"{key}: {previous.id} goes out on {out_state[key]!r} but {shot.id} comes in "
                    f"on {value!r}",
                    fix="fix one of the two specs, or plan the change in a beat",
                )
            )
    return findings


def check_scene_chain(project: Project, scene_id: str) -> list[Finding]:
    """Every cut in the scene, in order — the S08→S09 case."""
    findings: list[Finding] = []
    for shot in project.shots(scene_id):
        findings.extend(
            Finding(f.check, f.level, f"{shot.id}: {f.message}", f.fix)
            for f in check_continuity(project, scene_id, shot)
        )
    return findings


def check_frame_file(project: Project, image: Path) -> list[Finding]:
    """Code-level frame checks: the aspect ratio that bit S11."""
    findings: list[Finding] = []
    size = frame_size(image)
    if size is None:
        return [Finding(
            "frame readable", WARN, f"could not read the image dimensions of {image.name}",
            fix="export a fresh PNG and file it as a new take",
        )]
    width, height = size
    want_w, want_h = project.config.aspect_tuple
    actual = width / height
    wanted = want_w / want_h
    if abs(actual - wanted) > 0.02:
        findings.append(
            Finding(
                "aspect ratio",
                FAIL,
                f"{image.name} is {width}x{height} ({actual:.2f}:1), the project is "
                f"{project.config.aspect_ratio} ({wanted:.2f}:1)",
                fix="regenerate at the project's aspect ratio, or crop to it",
            )
        )
    else:
        findings.append(
            Finding("aspect ratio", PASS, f"{width}x{height} matches {project.config.aspect_ratio}")
        )
    return findings


def check_video_file(
    project: Project, scene_id: str, shot: Shot, video: Path, *, vision: bool = True,
    progress: EngineProgress | None = None,
) -> list[Finding]:
    """Check the clip's duration, camera motion, speech and eyeline."""
    findings: list[Finding] = []
    if progress:
        progress.status("Checking video duration…")
    try:
        duration = probe_duration(video)
    except ToolError as exc:
        return [Finding(
            "video tools", WARN, f"could not check {video.name}. {exc}",
            fix="run shotforge doctor or set the executable paths under tool_paths in project.yaml",
        )]
    if duration is None:
        return [Finding(
            "video readable", WARN, f"could not read the duration of {video.name}",
            fix="open the video in a player and export a fresh MP4 if it does not play",
        )]
    delta = duration - shot.duration_s
    if abs(delta) > 1.0:
        findings.append(
            Finding(
                "duration",
                WARN if abs(delta) < 2.5 else FAIL,
                f"{video.name} is {duration:.1f}s, the spec says {shot.duration_s}s",
                fix="trim in the edit, or adjust duration_s and the beats",
            )
        )
    else:
        findings.append(
            Finding("duration", PASS, f"{duration:.1f}s against {shot.duration_s}s in the spec")
        )

    install_qa = (
        "install shotforge[qa] in the environment running Shotforge. "
        "See Review a video take in the README for uvx and checkout commands."
    )
    try:
        from shotforge.qa.motion import check_motion
    except (ImportError, OSError):
        findings.append(
            Finding("camera motion", WARN, "OpenCV and NumPy are needed", fix=install_qa)
        )
    else:
        if progress:
            progress.status("Checking camera motion…")
        findings.append(check_motion(video, shot))

    try:
        from shotforge.qa.speech import check_speech
    except (ImportError, OSError):
        findings.append(Finding("speech", WARN, "NumPy is needed", fix=install_qa))
    else:
        if progress:
            progress.status("Checking speech timing…")
        findings.extend(check_speech(video, shot, duration))

    if vision:
        from shotforge.qa.eyeline import check_eyeline

        findings.append(check_eyeline(project, shot, video, duration, progress=progress))
    return findings


def summarise(findings: list[Finding]) -> str:
    if any(f.level == FAIL for f in findings):
        return FAIL
    if any(f.level == WARN for f in findings):
        return WARN
    return PASS


def scene_summary(project: Project, scene: Scene) -> dict[str, str]:
    """One verdict per shot, for the storyboard row."""
    out: dict[str, str] = {}
    for shot in project.shots(scene.id):
        out[shot.id] = summarise(check_spec(project, scene.id, shot))
    return out
