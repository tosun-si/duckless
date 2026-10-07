---
title: Agent Skills
description: A Claude Code plugin that helps an agent set DuckLess up, write jobs, size machines and debug runs.
---

The CLI decides what can be decided from facts: the runner (`--on auto`), the smallest valid
local SSD count, the quota check. What takes judgment (which machine for this data, why a job
failed) lives in five Agent Skills, shipped with DuckLess as a Claude Code plugin.

## Install

Two ways, same skills.

**From the CLI**, the skills of the version you run, into the project or your home directory:

```bash
duckless skills install                  # this project: .claude/skills and .agents/skills
duckless skills install --user           # your home: ~/.claude/skills and ~/.agents/skills
duckless skills install --target claude  # claude | agents | all (default)
```

`.claude/skills` is read by Claude Code, `.agents/skills` by the other agents that follow the
Agent Skills convention. The copies are named `duckless-<skill>` and replace only DuckLess's
own folders. Committed in a project, they give the whole team the same skills, matching the
CLI version the project uses; when the CLI moves on, `duckless` says so and
`duckless skills install` refreshes them.

**As a Claude Code plugin**, kept up to date from the repository:

```
/plugin marketplace add tosun-si/duckless
/plugin install duckless@duckless
```

`claude plugin update duckless@duckless` picks up a newer version. The plugin version follows
the CLI's.

## The skills

| Skill | Use it to |
| --- | --- |
| `setup` | install the CLI, run `init`, wire `.envrc`, upgrade or remove an installation |
| `writing-jobs` | write SQL, Python or `exec` jobs, with the rules measured on GCS (parallel writes, no httpfs, vectorized UDFs, no secret in a DSN) |
| `sizing` | pick the machine, Spot, local SSD and runner, and resize from `duckless status` |
| `troubleshooting` | go from `status` / `logs` / `result` to a cause and a fix |
| `ducklake` | turn DuckLake on, use `lake` tables, fix a failing attach |

The agent loads a skill on its own when the conversation calls for it. To ask for one
explicitly: `/duckless:sizing` with the plugin, `/duckless-sizing` with the copies.

The skills live in the repository, under
[`duckless/plugin/skills/`](https://github.com/tosun-si/duckless/tree/main/duckless/plugin/skills): they change
with the code, in the same pull requests.
