"""The shell around every page: what the rail offers, and where you are.

Pages pass nothing for this. Both are derived from the request path and the
project, so one place decides the cockpit's navigation and every screen
agrees with every other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shotforge.project import Project


@dataclass(frozen=True)
class NavItem:
    """One destination in the rail."""

    key: str
    label: str
    href: str
    icon: str
    key_hint: str = ""


@dataclass(frozen=True)
class NavGroup:
    """A handful of destinations under one word."""

    label: str
    items: tuple[NavItem, ...]


@dataclass(frozen=True)
class Crumb:
    """One step of the trail. No href means it is where you are."""

    label: str
    href: str | None = None


KIND_LABEL = {"character": "Characters", "location": "Locations", "prop": "Props"}
SECTIONS = {
    "breakdown": "Breakdown",
    "bible": "Bible",
    "sets": "Sets",
    "skills": "Skills",
    "log": "Log",
    "settings": "Settings",
    "projects": "Projects",
}
LEAF_LABEL = {
    "compare": "Compare",
    "undo": "Undo",
    "edit": "Edit",
    "cut": "Rough cut",
}


def navigation(prefix: str, scene: str | None = None) -> list[NavGroup]:
    """The rail, grouped: the work, the world it happens in, the project."""
    work = [
        NavItem("scenes", "Now", f"{prefix}/", "home", "n"),
        NavItem("breakdown", "Breakdown", f"{prefix}/breakdown", "lines", "b"),
    ]
    if scene:
        work += [
            NavItem("storyboard", "Storyboard", f"{prefix}/scene/{scene}", "film", "s"),
            NavItem("cut", "Rough cut", f"{prefix}/scene/{scene}/cut", "clapper", "c"),
        ]
    return [
        NavGroup("Work", tuple(work)),
        NavGroup("World", (
            NavItem("bible", "Bible", f"{prefix}/bible", "book", "w"),
            NavItem("sets", "Sets", f"{prefix}/sets", "box", "e"),
            NavItem("skills", "Skills", f"{prefix}/skills", "sparkle", "k"),
        )),
        NavGroup("Project", (
            NavItem("log", "Log", f"{prefix}/log", "clock", "l"),
            NavItem("settings", "Settings", f"{prefix}/settings", "sliders", ","),
        )),
    ]


def current_scene(context: dict[str, Any], project: Project | None) -> str | None:
    """The scene the rail's Storyboard and Rough cut should point at.

    The one on screen, else the one being worked on, else the first there is —
    so those two links are never dead and never surprising.
    """
    scene = context.get("scene")
    if scene is not None and getattr(scene, "id", None):
        return str(scene.id)
    if context.get("scene_id"):
        return str(context["scene_id"])
    active = context.get("active")
    if active:
        return str(active[0])
    if project is None:
        return None
    try:
        ids = project.scene_ids()
    except Exception:  # noqa: BLE001 - a broken project still gets a rail
        return None
    return ids[0] if ids else None


def crumbs(path: str, prefix: str = "") -> list[Crumb]:
    """The trail for a path, read off the URL so no page has to spell it out."""
    tail = path[len(prefix):] if prefix and path.startswith(prefix) else path
    parts = [part for part in tail.split("/") if part]
    if not parts:
        return [Crumb("Now")]

    head, rest = parts[0], parts[1:]
    if head == "scene":
        return _scene_crumbs(prefix, rest)
    if head == "shot":
        return _shot_crumbs(prefix, rest)
    if head == "bible":
        return _bible_crumbs(prefix, rest)
    if head in SECTIONS:
        return [Crumb(SECTIONS[head])]
    return [Crumb(head.replace("_", " ").capitalize())]


def _scene_crumbs(prefix: str, rest: list[str]) -> list[Crumb]:
    if not rest:
        return [Crumb("Storyboard")]
    scene = rest[0]
    if len(rest) == 1:
        return [Crumb("Storyboard"), Crumb(scene)]
    return [Crumb(scene, f"{prefix}/scene/{scene}"), Crumb(_leaf(rest[-1]))]


def _shot_crumbs(prefix: str, rest: list[str]) -> list[Crumb]:
    if len(rest) < 2:
        return [Crumb("Storyboard")]
    scene, shot = rest[0], rest[1]
    trail = [Crumb(scene, f"{prefix}/scene/{scene}")]
    if len(rest) == 2:
        return [*trail, Crumb(shot)]
    return [*trail, Crumb(shot, f"{prefix}/shot/{scene}/{shot}"), Crumb(_leaf(rest[-1]))]


def _bible_crumbs(prefix: str, rest: list[str]) -> list[Crumb]:
    bible = f"{prefix}/bible"
    if not rest:
        return [Crumb("Bible")]
    if rest[0] == "new":
        kind = rest[1] if len(rest) > 1 else "entry"
        return [Crumb("Bible", bible), Crumb(f"New {kind}")]
    kind, entity = rest[0], rest[1] if len(rest) > 1 else ""
    trail = [Crumb("Bible", bible), Crumb(KIND_LABEL.get(kind, kind.capitalize()))]
    if len(rest) <= 2:
        return [*trail, Crumb(entity)]
    return [*trail, Crumb(entity, f"{bible}/{kind}/{entity}"), Crumb(_leaf(rest[-1]))]


def _leaf(segment: str) -> str:
    return LEAF_LABEL.get(segment, segment.replace("_", " ").capitalize())
