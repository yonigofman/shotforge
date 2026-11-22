"""Finding skills: the project's own folder first, then the bundled library."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml


class SkillError(Exception):
    pass


@dataclass
class Skill:
    """One skill folder, loaded."""

    name: str
    root: Path
    meta: dict
    body: str

    @property
    def stage(self) -> str:
        return str(self.meta.get("stage", ""))

    @property
    def kind(self) -> str:
        """``craft`` (knows what to do) or ``adapter`` (knows how to say it to a tool)."""
        return str(self.meta.get("kind", "craft"))

    @property
    def tool(self) -> str:
        return str(self.meta.get("tool", ""))

    @property
    def description(self) -> str:
        return str(self.meta.get("description", ""))

    @property
    def template_path(self) -> Path:
        return self.root / "template.j2"

    @property
    def rules_path(self) -> Path:
        return self.root / "rules.md"

    @property
    def examples_dir(self) -> Path:
        return self.root / "examples"

    def rules(self) -> list[str]:
        """The accumulated lessons, as single lines (``- `` bullets in rules.md)."""
        if not self.rules_path.is_file():
            return []
        rules: list[str] = []
        for line in self.rules_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(("- ", "* ")):
                rules.append(stripped[2:].strip())
        return rules

    def constraints(self) -> list[str]:
        """The rules as they go into a prompt: the constraint, without the case note.

        A rule is written ``constraint — (case: what went wrong)``. The case is
        why the rule exists and belongs in the file and on the Skills screen;
        the prompt only carries the constraint.
        """
        out: list[str] = []
        for rule in self.rules():
            out.append(re.split(r"\s+—\s+\(case:", rule)[0].strip())
        return out

    def add_rule(self, rule: str) -> Path:
        """Append a lesson that worked, so the next scene starts from it."""
        rule = rule.strip().lstrip("-* ").strip()
        if not rule:
            raise SkillError("a rule cannot be empty")
        existing = self.rules()
        if rule in existing:
            return self.rules_path
        if not self.rules_path.is_file():
            self.rules_path.write_text(f"# Rules — {self.name}\n\n- {rule}\n", encoding="utf-8")
        else:
            text = self.rules_path.read_text(encoding="utf-8").rstrip("\n")
            self.rules_path.write_text(f"{text}\n- {rule}\n", encoding="utf-8")
        return self.rules_path


def bundled_root() -> Path:
    """The skill library that ships with Shotforge."""
    installed = Path(__file__).resolve().parents[1] / "_skills"
    if installed.is_dir():
        return installed
    repo = Path(__file__).resolve().parents[3] / "skills"
    if repo.is_dir():
        return repo
    raise SkillError("the bundled skill library is missing from this install")


def search_roots(project_skills: Path | None = None) -> list[Path]:
    roots: list[Path] = []
    if project_skills is not None and project_skills.is_dir():
        roots.append(project_skills)
    roots.append(bundled_root())
    return roots


def _parse_skill_md(path: Path) -> tuple[dict, str]:
    """Split YAML front matter from the body of a ``SKILL.md``."""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            meta = yaml.safe_load(parts[1]) or {}
            if not isinstance(meta, dict):
                raise SkillError(f"{path}: front matter must be a mapping")
            return meta, parts[2].lstrip("\n")
    return {}, text


def load_skill(root: Path) -> Skill:
    skill_md = root / "SKILL.md"
    if not skill_md.is_file():
        raise SkillError(f"{root} has no SKILL.md")
    meta, body = _parse_skill_md(skill_md)
    return Skill(name=meta.get("name", root.name), root=root, meta=meta, body=body)


def find_skill(name: str, project_skills: Path | None = None) -> Skill:
    """A project-local skill of the same name wins over the bundled one."""
    for root in search_roots(project_skills):
        candidate = root / name
        if (candidate / "SKILL.md").is_file():
            return load_skill(candidate)
    looked = ", ".join(str(root) for root in search_roots(project_skills))
    raise SkillError(f"no skill named {name!r} (looked in {looked})")


def list_skills(project_skills: Path | None = None) -> list[Skill]:
    """Every skill available, project overrides shadowing the library."""
    found: dict[str, Skill] = {}
    for root in reversed(search_roots(project_skills)):
        if not root.is_dir():
            continue
        for candidate in sorted(root.iterdir()):
            if (candidate / "SKILL.md").is_file():
                skill = load_skill(candidate)
                found[candidate.name] = skill
    return [found[key] for key in sorted(found)]


@lru_cache(maxsize=1)
def bundled_names() -> tuple[str, ...]:
    return tuple(s.name for s in list_skills())
