"""Effects of `duckless skills install`: read the skills shipped in the package, write copies."""

import shutil
from importlib import resources
from pathlib import Path

from duckless.core.skills import MARKER, PREFIX, TARGET_DIRS, FileWrite


def bundled_skills() -> dict[str, str]:
    """SKILL.md of every skill shipped with the CLI (the same files as the Claude Code plugin)."""
    root = resources.files("duckless") / "plugin" / "skills"
    return {d.name: (d / "SKILL.md").read_text() for d in root.iterdir() if (d / "SKILL.md").is_file()}


def write(plan: tuple[FileWrite, ...], skills: tuple[str, ...]) -> None:
    """Replaces the DuckLess skill folders it writes to (drops files a newer version removed)."""
    folders = {w.path.parent for w in plan if w.path.parent.name.removeprefix(PREFIX) in skills}
    for folder in folders:
        if folder.name.startswith(PREFIX) and folder.is_dir():
            shutil.rmtree(folder)
    for w in plan:
        w.path.parent.mkdir(parents=True, exist_ok=True)
        w.path.write_text(w.text)


def installed_markers(base: Path) -> dict[Path, str]:
    """Version markers of DuckLess skills installed under `base` (a project or the home dir)."""
    return {
        marker.parent: marker.read_text()
        for skills_dir in TARGET_DIRS.values()
        for marker in (base / skills_dir).glob(f"{PREFIX}*/{MARKER}")
    }
