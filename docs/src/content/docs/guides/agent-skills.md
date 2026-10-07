---
title: Agent Skills
description: A Claude Code plugin that helps an agent set DuckLess up, write jobs, size machines and debug runs.
---

The CLI decides what can be decided from facts: the runner (`--on auto`), the smallest valid
local SSD count, the quota check. What takes judgment (which machine for this data, why a job
failed) lives in five Agent Skills, shipped with DuckLess as a Claude Code plugin.

## Install

```
/plugin marketplace add tosun-si/duckless
/plugin install duckless@duckless
```

`claude plugin update duckless@duckless` picks up a newer version. The plugin version follows
the CLI's.

## The skills

| Skill | Use it to |
| --- | --- |
| `/duckless:setup` | install the CLI, run `init`, wire `.envrc`, upgrade or remove an installation |
| `/duckless:writing-jobs` | write SQL, Python or `exec` jobs, with the rules measured on GCS (parallel writes, no httpfs, vectorized UDFs, no secret in a DSN) |
| `/duckless:sizing` | pick the machine, Spot, local SSD and runner, and resize from `duckless status` |
| `/duckless:troubleshooting` | go from `status` / `logs` / `result` to a cause and a fix |
| `/duckless:ducklake` | turn DuckLake on, use `lake` tables, fix a failing attach |

The agent loads a skill on its own when the conversation calls for it; the slash form is for
asking explicitly.

The skills live in the repository, under
[`plugin/skills/`](https://github.com/tosun-si/duckless/tree/main/plugin/skills): they change
with the code, in the same pull requests.
