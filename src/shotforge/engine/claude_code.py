"""Claude Code, called headless on your own subscription — no paid API."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from queue import Empty, SimpleQueue
from tempfile import TemporaryFile
from threading import Thread
from time import monotonic
from typing import Any

import yaml

from shotforge.claude_settings import ClaudeSettings, read_claude_settings
from shotforge.engine.base import Engine, EngineError, EngineProgress, EngineResult
from shotforge.paths import ProjectPaths
from shotforge.skills.registry import Skill
from shotforge.tools import ToolError, find_tool
from shotforge.yamlio import ProjectFileError

DEFAULT_TIMEOUT_S = 600
TEXT_ONLY_ARGS = ("--tools", "", "--strict-mcp-config", "--setting-sources", "")


class ClaudeCodeEngine(Engine):
    """``claude -p`` with the project's skills, on your Claude subscription."""

    name = "claude-code"

    def __init__(
        self, executable: Path | None = None, timeout_s: int | None = None,
        *, settings: ClaudeSettings | None = None,
    ):
        self._executable = executable
        self._settings = settings
        self.timeout_s = timeout_s

    # -- plumbing ----------------------------------------------------------
    def executable(self) -> Path:
        found = self._executable or find_tool("claude")
        if found is None:
            raise ToolError(
                "Claude Code not found. Install it from claude.ai/code, or set "
                "tool_paths.claude in project.yaml."
            )
        return found

    def available(self) -> bool:
        try:
            self.executable()
        except ToolError:
            return False
        return True

    def _run_options(self, cwd: Path | None) -> tuple[list[str], int]:
        settings = self._settings
        if settings is None:
            try:
                settings = (
                    read_claude_settings(ProjectPaths(cwd).claude_settings)
                    if cwd is not None else ClaudeSettings()
                )
            except (ProjectFileError, OSError) as exc:
                raise EngineError(str(exc)) from exc
        args = []
        for flag, value in (
            ("--model", settings.model),
            ("--effort", settings.effort),
            ("--fallback-model", ",".join(settings.fallback_models)),
        ):
            if value:
                args.extend([flag, value])
        timeout_s = self.timeout_s if self.timeout_s is not None else (
            settings.timeout_s or DEFAULT_TIMEOUT_S
        )
        return args, timeout_s

    def build_prompt(self, skill: Skill, inputs: dict[str, Any], files: list[Path] | None) -> str:
        """SKILL.md is the instruction; the inputs go in as YAML."""
        parts = [
            f"You are running the Shotforge skill `{skill.name}`.",
            "Follow its instructions exactly and return only what the Output section asks for.",
            "",
            "## Skill",
            skill.body.strip(),
        ]
        rules = skill.rules()
        if rules:
            parts += ["", "## Rules learned so far", *[f"- {rule}" for rule in rules]]
        if files:
            parts += [
                "",
                "## Files",
                *[f"- {path}" for path in files],
            ]
        parts += [
            "",
            "## Inputs",
            "```yaml",
            yaml.safe_dump(inputs, allow_unicode=True, sort_keys=False).strip(),
            "```",
        ]
        return "\n".join(parts)

    # -- the interface -----------------------------------------------------
    def run_skill(
        self,
        skill: Skill,
        inputs: dict[str, Any],
        *,
        cwd: Path | None = None,
        files: list[Path] | None = None,
    ) -> EngineResult:
        prompt = self.build_prompt(skill, inputs, files)
        model_args, timeout_s = self._run_options(cwd)
        command = [str(self.executable()), "-p"]
        if files:
            command.append(prompt)
        command.extend(["--output-format", "json", *model_args])
        options = {}
        if files:
            # Headless reviews need to open the images listed in the prompt.
            command.extend(["--tools", "Read", "--allowedTools", "Read"])
        else:
            # A whole chapter can exceed the OS argument limit. Text skills need no tools.
            options["input"] = prompt
            command.extend(TEXT_ONLY_ARGS)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                encoding="utf-8",
                cwd=str(cwd) if cwd else None,
                timeout=timeout_s,
                **options,
            )
        except subprocess.TimeoutExpired as exc:
            raise EngineError(
                f"claude -p did not finish within {timeout_s}s for skill {skill.name}"
            ) from exc
        except OSError as exc:
            raise EngineError(f"could not start Claude Code for skill {skill.name}: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or self._parse(completed.stdout).text
            raise EngineError(
                f"claude -p failed for skill {skill.name} (exit {completed.returncode}):\n"
                f"{detail[:2000]}"
            )
        return self._parse(completed.stdout)

    def run_skill_with_progress(
        self, skill: Skill, inputs: dict[str, Any], *, cwd: Path, progress: EngineProgress,
    ) -> EngineResult:
        if progress.cancelled.is_set():
            raise EngineError("Proposal generation was cancelled.")
        model_args, timeout_s = self._run_options(cwd)
        command = [
            str(self.executable()), "-p", "--output-format", "stream-json", "--verbose",
            "--include-partial-messages", *model_args, *TEXT_ONLY_ARGS,
        ]
        prompt = self.build_prompt(skill, inputs, None)
        try:
            # File-backed stdin and stderr avoid blocking on a long prompt or error.
            with TemporaryFile() as source, TemporaryFile() as errors:
                source.write(prompt.encode("utf-8"))
                source.seek(0)
                with subprocess.Popen(
                    command, cwd=cwd, stdin=source, stderr=errors, stdout=subprocess.PIPE,
                    encoding="utf-8", errors="replace",
                ) as process:
                    result = self._read_stream(process, skill, progress, timeout_s)
                    errors.seek(0)
                    detail = errors.read(2000).decode("utf-8", errors="replace").strip()
                    if process.returncode:
                        detail = detail or (result.text if result else "No result was returned.")
                        raise EngineError(
                            f"claude -p failed for skill {skill.name} "
                            f"(exit {process.returncode}):\n{detail[:2000]}"
                        )
                    if result is None:
                        raise EngineError(
                            f"Claude Code ended without a result for {skill.name}. "
                            f"{detail or 'Check that Claude Code is up to date and signed in.'}"
                        )
                    return result
        except OSError as exc:
            raise EngineError(f"could not start Claude Code for skill {skill.name}: {exc}") from exc

    def _read_stream(
        self, process: subprocess.Popen[str], skill: Skill, progress: EngineProgress,
        timeout_s: int,
    ) -> EngineResult | None:
        stdout = process.stdout
        assert stdout is not None
        lines: SimpleQueue[str | None] = SimpleQueue()

        def read_lines() -> None:
            try:
                for line in stdout:
                    lines.put(line)
            finally:
                lines.put(None)

        reader = Thread(target=read_lines, daemon=True)
        reader.start()
        deadline = monotonic() + timeout_s
        result = None
        reported = False
        eof = False
        try:
            while True:
                if progress.cancelled.is_set():
                    raise EngineError("Proposal generation was cancelled.")
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise EngineError(
                        f"claude -p did not finish within {timeout_s}s for skill {skill.name}"
                    )
                interval = min(0.2, remaining)
                if eof:
                    try:
                        process.wait(timeout=interval)
                    except subprocess.TimeoutExpired:
                        continue
                    return result
                try:
                    line = lines.get(timeout=interval)
                except Empty:
                    continue
                if line is None:
                    eof = True
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "result":
                    result = self._parse(line)
                elif not reported and _has_text_output(event):
                    reported = True
                    progress.on_output()
        finally:
            if process.poll() is None:
                process.kill()
            reader.join()

    @staticmethod
    def _parse(stdout: str) -> EngineResult:
        raw = stdout.strip()
        text = raw
        data: dict[str, Any] = {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            text = str(payload.get("result") or payload.get("text") or raw)
            data = payload
        fenced = _fenced_block(text)
        if fenced is not None:
            parsed = _try_structured(fenced)
            if isinstance(parsed, dict):
                data = {**data, "parsed": parsed}
        return EngineResult(text=text, data=data, raw=raw)


def _fenced_block(text: str) -> str | None:
    if "```" not in text:
        return None
    parts = text.split("```")
    if len(parts) < 3:
        return None
    block = parts[1]
    first_newline = block.find("\n")
    return block[first_newline + 1 :] if first_newline != -1 else block


def _has_text_output(event: dict) -> bool:
    if event.get("type") == "stream_event":
        partial = event.get("event", {})
        delta = partial.get("delta", {})
        return partial.get("type") == "content_block_delta" and bool(delta.get("text"))
    if event.get("type") == "assistant":
        return any(
            content.get("type") == "text" and content.get("text")
            for content in event.get("message", {}).get("content", [])
        )
    return False


def _try_structured(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None
