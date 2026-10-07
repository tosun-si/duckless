"""Agent Skills copied into a project or a user's home, from the copy shipped with the CLI.

Two homes: `.claude/skills` (Claude Code) and `.agents/skills` (the cross-agent convention).
Copies are prefixed `duckless-` so they never clash with other skills, and carry the CLI
version so a later CLI can tell they are stale.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

PREFIX = "duckless-"
MARKER = ".duckless-version"
_NAME_LINE = re.compile(r"^name:\s*\S+\s*$", re.MULTILINE)


class SkillTarget(StrEnum):
    CLAUDE = "claude"
    AGENTS = "agents"
    ALL = "all"


TARGET_DIRS = {
    SkillTarget.CLAUDE: Path(".claude") / "skills",
    SkillTarget.AGENTS: Path(".agents") / "skills",
}


@dataclass(frozen=True, slots=True)
class FileWrite:
    path: Path
    text: str


def skill_dirs(target: SkillTarget, base: Path) -> tuple[Path, ...]:
    kinds = tuple(TARGET_DIRS) if target is SkillTarget.ALL else (target,)
    return tuple(base / TARGET_DIRS[kind] for kind in kinds)


def installed_name(skill: str) -> str:
    return f"{PREFIX}{skill}"


def renamed(skill_md: str, skill: str) -> str:
    """The standard wants `name` equal to the folder name: rewrite it for the prefixed folder."""
    return _NAME_LINE.sub(f"name: {installed_name(skill)}", skill_md, count=1)


def install_plan(skills: Mapping[str, str], dirs: tuple[Path, ...], version: str) -> tuple[FileWrite, ...]:
    """Files to write: each skill's SKILL.md (renamed) and its version marker, in every dir."""
    return tuple(
        write
        for base in dirs
        for skill, text in sorted(skills.items())
        for write in (
            FileWrite(base / installed_name(skill) / "SKILL.md", renamed(text, skill)),
            FileWrite(base / installed_name(skill) / MARKER, f"{version}\n"),
        )
    )


def stale(markers: Mapping[Path, str], version: str) -> tuple[Path, ...]:
    """Installed skill folders whose marker is not this CLI's version."""
    return tuple(sorted(path for path, installed in markers.items() if installed.strip() != version))
