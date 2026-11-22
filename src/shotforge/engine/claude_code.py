"""Claude Code, called headless on your own subscription — no paid API."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import yaml

from shotforge.engine.base import Engine, EngineError, EngineResult
from shotforge.skills.registry import Skill
from shotforge.tools import ToolError, find_tool

#: Headless runs are bounded so a stuck call cannot hold up a scene.
DEFAULT_TIMEOUT_S = 600


class ClaudeCodeEngine(Engine):
    """``claude -p`` with the project's skills, on your Claude subscription."""

    name = "claude-code"

    def __init__(self, executable: Path | None = None, timeout_s: int = DEFAULT_TIMEOUT_S):
        self._executable = executable
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
        command = [
            str(self.executable()),
            "-p",
            prompt,
            "--output-format",
            "json",
        ]
        if files:
            # Headless reviews need to open the images listed in the prompt.
            command.extend(["--tools", "Read", "--allowedTools", "Read"])
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                encoding="utf-8",
                cwd=str(cwd) if cwd else None,
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise EngineError(
                f"claude -p did not finish within {self.timeout_s}s for skill {skill.name}"
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


def _try_structured(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None
