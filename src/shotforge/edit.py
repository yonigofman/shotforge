"""Assemble approved shots and export a frame-accurate CMX 3600 cut."""

from __future__ import annotations

import json
import math
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from filelock import FileLock, Timeout
from PIL import Image, ImageDraw, ImageFont
from pydantic import Field, FiniteFloat

from shotforge import tools
from shotforge.models import Shot, ShotStatus
from shotforge.models.common import ID_RE, SFModel
from shotforge.project import Project
from shotforge.yamlio import dump, load

VIDEO = "rough_cut.mp4"
EDL = "rough_cut.edl"
EXPORT = "rough_cut.zip"
MANIFEST = ".rough_cut.json"
APPROVED = {ShotStatus.video_approved, ShotStatus.done}
TITLE_FONT = Path(__file__).parent / "assets" / "DejaVuSans.ttf"


class EditError(Exception):
    pass


class EditSettings(SFModel):
    bridges: dict[str, FiniteFloat] = Field(default_factory=dict)
    end_title: str = Field(min_length=1, max_length=120, pattern=r"^[^\r\n\x00]+$")
    end_seconds: FiniteFloat = Field(default=2, gt=0, le=30)


@dataclass(frozen=True)
class CutShot:
    shot: Shot
    path: Path

    @property
    def problem(self) -> str | None:
        if self.shot.status not in APPROVED:
            return f"{self.shot.id} is {self.shot.status.value}; approve its video first."
        if not self.path.is_file():
            return f"{self.shot.id} is missing its approved video/shot.mp4."
        return None


@dataclass(frozen=True)
class Clip:
    reel: str
    label: str
    frames: int
    duration: float
    has_audio: bool


def timeline(project: Project, scene_id: str) -> list[CutShot]:
    if not ID_RE.fullmatch(scene_id):
        raise EditError(f"Invalid scene ID: {scene_id!r}.")
    project.scene(scene_id)
    return [
        CutShot(project.shot(scene_id, shot_id),
                project.paths.shot(scene_id, shot_id).approved_video)
        for shot_id in project.shot_ids(scene_id)
    ]


def settings_path(project: Project, scene_id: str) -> Path:
    return project.paths.scene(scene_id).root / "rough_cut.yaml"


def read_settings(project: Project, scene_id: str) -> EditSettings:
    path = settings_path(project, scene_id)
    title = " ".join(project.name.split())[:120] or "The End"
    return load(path, EditSettings) if path.is_file() else EditSettings(end_title=title)


def validate_settings(settings: EditSettings, shots: list[CutShot]) -> None:
    outgoing = {item.shot.id for item in shots[:-1]}
    for shot_id, seconds in settings.bridges.items():
        if shot_id not in outgoing:
            raise EditError(f"{shot_id} has no following shot for a sound bridge.")
        if seconds < 0 or seconds > 5:
            raise EditError(f"The bridge after {shot_id} must be between 0 and 5 seconds.")


def _snapshot(
    project: Project, scene_id: str, settings: EditSettings, shots: list[CutShot]
) -> dict:
    sources = []
    for item in shots:
        if problem := item.problem:
            raise EditError(problem)
        stat = item.path.stat()
        sources.append([item.shot.id, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns])
    return {
        "scene": scene_id,
        "fps": project.config.fps,
        "aspect": project.config.aspect_ratio,
        "settings": settings.model_dump(mode="json"),
        "sources": sources,
    }


def current_cut(
    project: Project, scene_id: str, settings: EditSettings, shots: list[CutShot]
) -> list[int] | None:
    """Lengths in frames of the current cut, if all of its inputs still match."""
    folder = project.paths.edit
    try:
        manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
        frames = manifest["frames"]
        if (
            manifest["inputs"] == _snapshot(project, scene_id, settings, shots)
            and len(frames) == len(shots) + 1
            and all((folder / name).is_file() for name in (VIDEO, EDL, EXPORT))
        ):
            return frames
    except (OSError, ValueError, TypeError, KeyError, EditError):
        pass
    return None


def _run(command: list[str], *, label: str, cwd: Path, timeout: int = 300) -> str:
    try:
        process = subprocess.run(
            command, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EditError(f"{label}: {exc}") from exc
    if process.returncode:
        details = process.stderr.strip().splitlines()
        raise EditError(f"{label}: {' '.join(details[-3:]) or 'media tool failed'}")
    return process.stdout


def _probe(ffprobe: Path, item: CutShot, folder: Path, number: int, fps: int) -> Clip:
    output = _run(
        [str(ffprobe), "-v", "error", "-show_entries",
         "stream=codec_type,duration:format=duration", "-of", "json", str(item.path)],
        label=f"Could not read {item.shot.id}", cwd=folder, timeout=30,
    )
    try:
        metadata = json.loads(output)
        streams = metadata["streams"]
        video = next(stream for stream in streams if stream["codec_type"] == "video")
        duration = float(video.get("duration") or metadata["format"]["duration"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError
    except (KeyError, ValueError, TypeError, StopIteration) as exc:
        raise EditError(f"{item.shot.id} has no video stream with a readable duration.") from exc
    return Clip(
        f"SH{number:06}", item.shot.id, max(1, round(duration * fps)), duration,
        any(stream["codec_type"] == "audio" for stream in streams),
    )


def _timecode(frames: int, fps: int) -> str:
    seconds, frame = divmod(frames, fps)
    minutes, second = divmod(seconds, 60)
    hour, minute = divmod(minutes, 60)
    return f"{hour:02}:{minute:02}:{second:02}:{frame:02}"


def _edl(scene_id: str, clips: list[Clip], fps: int) -> str:
    lines = [f"TITLE: {scene_id}", "FCM: NON-DROP FRAME", ""]
    cursor = fps * 3600
    if cursor + sum(clip.frames for clip in clips) >= fps * 24 * 3600:
        raise EditError("The edit exceeds the 24-hour timecode range.")
    for index, clip in enumerate(clips, start=1):
        end = cursor + clip.frames
        lines.extend([
            f"{index:03}  {clip.reel} AA/V C        "
            f"{_timecode(0, fps)} {_timecode(clip.frames, fps)} "
            f"{_timecode(cursor, fps)} {_timecode(end, fps)}",
            f"* FROM CLIP NAME: {clip.reel}.mov",
            f"* COMMENT: {clip.label}",
            "",
        ])
        cursor = end
    return "\n".join(lines)


def _dimensions(project: Project) -> tuple[int, int]:
    width, height = project.config.aspect_tuple
    if not width or not height:
        raise EditError("The project aspect ratio must have two positive dimensions.")
    scale = min(1280 / width, 1280 / height, 720 / min(width, height))
    return max(2, 2 * round(width * scale / 2)), max(2, 2 * round(height * scale / 2))


def _render_clip(
    ffmpeg: Path, folder: Path, clip: Clip, fps: int, dimensions: tuple[int, int],
    inputs: list[str], filters: list[str],
) -> None:
    timecode = ["-timecode", "00:00:00:00"] if fps in {24, 25, 30, 48, 50, 60} else []
    _run(
        [str(ffmpeg), "-nostdin", "-v", "error", "-y", "-filter_complex_threads", "1",
         *inputs, "-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-threads", "4", "-c:a", "pcm_s16le", "-ar", "48000",
         "-ac", "2", "-video_track_timescale", str(fps * 1000),
         *timecode, "-movflags", "+faststart", f"{clip.reel}.mov"],
        label=f"Could not render {clip.label}", cwd=folder,
    )


def _shot_media(
    ffmpeg: Path, folder: Path, fps: int, size: tuple[int, int], item: CutShot, clip: Clip,
    bridge: float, previous: tuple[CutShot, Clip] | None,
) -> None:
    width, height = size
    duration = clip.frames / fps
    inputs = ["-i", str(item.path)]
    audio = "[0:a:0]" if clip.has_audio else "anullsrc=r=48000:cl=stereo,"
    filters = [
        f"[0:v:0]setpts=PTS-STARTPTS,fps={fps},"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
        f"tpad=stop_mode=clone:stop_duration=1,trim=end_frame={clip.frames}[v]",
        f"{audio}asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,"
        f"apad,atrim=duration={duration:.9f}" + ("[incoming]" if bridge else "[a]"),
    ]
    if bridge and previous:
        source, outgoing = previous
        if not outgoing.has_audio:
            raise EditError(f"Cannot bridge after {outgoing.label}: its clip has no audio.")
        if bridge > min(outgoing.duration, duration):
            raise EditError(f"The bridge after {outgoing.label} is longer than one of its clips.")
        inputs += ["-i", str(source.path)]
        filters += [
            "[1:a:0]asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,apad,"
            f"atrim=start={outgoing.duration - bridge:.9f}:duration={bridge:.9f},"
            f"asetpts=PTS-STARTPTS,volume=0.35,afade=t=out:st=0:d={bridge:.9f}[tail]",
            "[incoming][tail]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.98:"
            "level=false:latency=true[a]",
        ]
    _render_clip(ffmpeg, folder, clip, fps, size, inputs, filters)


def _end_card(
    ffmpeg: Path, folder: Path, card: Clip, settings: EditSettings,
    size: tuple[int, int], fps: int,
) -> None:
    width, height = size
    image = Image.new("RGB", size, "#141419")
    draw = ImageDraw.Draw(image)
    font_size = max(1, height // 12)
    font = ImageFont.truetype(str(TITLE_FONT), font_size)
    left, top, right, bottom = draw.textbbox((0, 0), settings.end_title, font=font)
    max_width = width * 0.85
    if right - left > max_width:
        fitted_size = max(1, int(font_size * max_width / (right - left)))
        font = ImageFont.truetype(str(TITLE_FONT), fitted_size)
        left, top, right, bottom = draw.textbbox((0, 0), settings.end_title, font=font)
    center = ((width - right - left) / 2, (height - bottom - top) / 2)
    draw.text(center, settings.end_title, font=font, fill="white")
    image.save(folder / "end_card.png")
    duration = card.frames / fps
    _render_clip(
        ffmpeg, folder, card, fps, size,
        ["-loop", "1", "-framerate", str(fps), "-i", "end_card.png",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"],
        [f"[0:v]trim=end_frame={card.frames}[v]", f"[1:a]atrim=duration={duration:.9f}[a]"],
    )


def _assemble(
    project: Project, scene_id: str, folder: Path, settings: EditSettings | None,
) -> Path:
    shots = timeline(project, scene_id)
    if not shots:
        raise EditError(f"{scene_id} has no shots to assemble.")
    if len(shots) >= 999:
        raise EditError("A CMX 3600 edit can contain up to 998 shots and an end card.")
    settings = settings if settings is not None else read_settings(project, scene_id)
    validate_settings(settings, shots)
    inputs = _snapshot(project, scene_id, settings, shots)
    fps = project.config.fps
    if fps > 99:
        raise EditError("CMX 3600 uses a two-digit frame field. Use a project rate below 100 fps.")
    size = _dimensions(project)
    overrides = project.config.tool_paths.model_dump()
    ffmpeg = tools.require_tool("ffmpeg", overrides=overrides).absolute()
    ffprobe = tools.require_tool("ffprobe", overrides=overrides).absolute()
    with TemporaryDirectory(prefix=".cut-", dir=folder) as temp:
        work = Path(temp)
        clips = [_probe(ffprobe, shot, work, i, fps) for i, shot in enumerate(shots, 1)]
        card_frames = max(1, round(fps * settings.end_seconds))
        card = Clip("ENDCARD", "End card", card_frames, card_frames / fps, False)
        sequence = [*clips, card]
        (work / EDL).write_text(_edl(scene_id, sequence, fps), encoding="ascii")
        previous = None
        for item, clip in zip(shots, clips, strict=True):
            bridge = settings.bridges.get(previous[0].shot.id, 0) if previous else 0
            _shot_media(ffmpeg, work, fps, size, item, clip, bridge, previous)
            previous = (item, clip)
        _end_card(ffmpeg, work, card, settings, size, fps)
        concat = [
            f"file '{clip.reel}.mov'\nduration {clip.frames / fps:.9f}\n" for clip in sequence
        ]
        (work / "concat.txt").write_text("".join(concat), encoding="ascii")
        _run(
            [str(ffmpeg), "-nostdin", "-v", "error", "-f", "concat", "-safe", "1",
             "-i", "concat.txt", "-map", "0:v:0", "-map", "0:a:0",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", VIDEO],
            label="Could not join the rough cut", cwd=work,
        )
        with ZipFile(work / EXPORT, "w") as archive:
            archive.write(work / EDL, EDL)
            for clip in sequence:
                filename = f"{clip.reel}.mov"
                archive.write(work / filename, f"reels/{filename}")

        refreshed = Project.open(project.paths.root)
        if inputs != _snapshot(refreshed, scene_id, settings, timeline(refreshed, scene_id)):
            raise EditError("The approved footage or project settings changed. Assemble again.")
        (work / MANIFEST).write_text(
            json.dumps({"inputs": inputs, "frames": [clip.frames for clip in sequence]}),
            encoding="utf-8",
        )
        dump(settings_path(project, scene_id), settings)
        (folder / MANIFEST).unlink(missing_ok=True)
        for name in (VIDEO, EDL, EXPORT, MANIFEST):
            os.replace(work / name, folder / name)
    return folder / VIDEO


def assemble(project: Project, scene_id: str, settings: EditSettings | None = None) -> Path:
    folder = project.paths.edit
    folder.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(folder / ".rough_cut.lock"), timeout=0):
            return _assemble(project, scene_id, folder, settings)
    except Timeout as exc:
        raise EditError("A rough cut is already being assembled for this project.") from exc
