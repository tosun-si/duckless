from pathlib import Path

import pytest

from duckless.cli import stale_skills_lines
from duckless.core.skills import MARKER, SkillTarget, install_plan, renamed, skill_dirs, stale
from duckless.skills import bundled_skills, installed_markers, write

SKILL = "---\nname: sizing\ndescription: Pick a machine.\n---\n\n# Sizing\n\nname: not frontmatter\n"


class TestSkillDirs:
    @pytest.mark.parametrize(
        ("target", "expected"),
        [
            (SkillTarget.CLAUDE, ("p/.claude/skills",)),
            (SkillTarget.AGENTS, ("p/.agents/skills",)),
            (SkillTarget.ALL, ("p/.claude/skills", "p/.agents/skills")),
        ],
    )
    def test_given_target_when_resolving_then_its_skill_homes(self, target: SkillTarget, expected: tuple) -> None:
        # when / then
        assert skill_dirs(target, Path("p")) == tuple(Path(e) for e in expected)


class TestInstallPlan:
    def test_given_skill_when_renaming_then_only_the_frontmatter_name_matches_the_prefixed_folder(self) -> None:
        # when
        text = renamed(SKILL, "sizing")

        # then
        assert text.startswith("---\nname: duckless-sizing\n")
        assert "name: not frontmatter" in text

    def test_given_skills_and_two_homes_when_planning_then_skill_and_marker_in_each(self) -> None:
        # when
        plan = install_plan({"sizing": SKILL}, (Path("a"), Path("b")), "0.3.0")

        # then
        assert [str(w.path) for w in plan] == [
            "a/duckless-sizing/SKILL.md",
            f"a/duckless-sizing/{MARKER}",
            "b/duckless-sizing/SKILL.md",
            f"b/duckless-sizing/{MARKER}",
        ]
        assert plan[1].text == "0.3.0\n"


class TestStale:
    def test_given_markers_when_checking_then_only_other_versions_are_stale(self) -> None:
        # when / then
        assert stale({Path("x/duckless-a"): "0.2.0\n", Path("x/duckless-b"): "0.3.0\n"}, "0.3.0") == (
            Path("x/duckless-a"),
        )

    def test_given_stale_dirs_when_rendering_then_one_note_naming_their_homes(self) -> None:
        # when
        lines = stale_skills_lines((Path("p/.claude/skills/duckless-a"), Path("p/.claude/skills/duckless-b")), "0.3.0")

        # then
        assert len(lines) == 1 and "p/.claude/skills" in lines[0] and "duckless skills install" in lines[0]
        assert stale_skills_lines((), "0.3.0") == []


class TestWrite:
    def test_given_shipped_skills_when_installing_twice_then_replaced_and_other_skills_untouched(
        self, tmp_path: Path
    ) -> None:
        # given: a user's own skill, and a file an older DuckLess version left behind
        skills = bundled_skills()
        dirs = skill_dirs(SkillTarget.ALL, tmp_path)
        own = tmp_path / ".claude/skills/my-skill/SKILL.md"
        own.parent.mkdir(parents=True)
        own.write_text("mine")
        leftover = tmp_path / ".claude/skills/duckless-sizing/old.md"
        leftover.parent.mkdir(parents=True)
        leftover.write_text("old")

        # when
        write(install_plan(skills, dirs, "0.3.0"), tuple(skills))

        # then
        assert own.read_text() == "mine"
        assert not leftover.exists()
        assert set(installed_markers(tmp_path).values()) == {"0.3.0\n"}
        assert len(installed_markers(tmp_path)) == 2 * len(skills)

    def test_given_package_when_reading_skills_then_the_five_shipped_skills(self) -> None:
        # when / then
        assert set(bundled_skills()) == {"setup", "writing-jobs", "sizing", "troubleshooting", "ducklake"}
