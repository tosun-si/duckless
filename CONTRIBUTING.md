# Contributing to DuckLess

Thanks for considering it. Bug reports, real use cases, docs fixes and code are all welcome.

## Before you start

- **A bug or a question**: open an [issue](https://github.com/tosun-si/duckless/issues) with
  the command you ran, the output of `duckless status <job-id>` and `duckless logs <job-id>`,
  and the DuckLess version (`pip show duckless`). Remove project ids and bucket names you do
  not want public.
- **A use case**: issues are also the place to describe a job you would like to run on
  DuckLess, even if it does not work yet. They shape the roadmap more than anything else.
- **A change bigger than a fix**: open an issue first, so we agree on the approach before you
  spend time on it.

## Setup

Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/tosun-si/duckless.git
cd duckless
uv sync
cp .envrc.example .envrc   # direnv: only needed to run real jobs
```

| Check | Command |
| --- | --- |
| Unit tests | `uv run pytest` |
| Lint and format | `uv run ruff check . && uv run ruff format --check .` |
| Terraform module | `cd duckless/terraform && terraform fmt -check && terraform init -backend=false && terraform validate` |
| Runner image | `docker buildx bake` then `docker run --rm --entrypoint python <image> /usr/local/src/app/smoke_test.py` |
| Agent Skills | `claude plugin validate .` |
| Docs | `cd docs && npm install && npm run dev` |

CI runs the tests, lint, Terraform and runner image checks on every pull request; the docs are built and published from `main`.

## How the code is organized

A light hexagonal layout, written in a functional style:

- `duckless/core/`: pure rules, no I/O. Most changes start here, and most tests live here.
- `duckless/ports.py`: what the service needs from the outside world, as `Protocol`s.
- `duckless/service.py`: operations as functions taking ports as arguments.
- `duckless/adapters/`: Google Cloud implementations of the ports.
- `duckless/wiring.py`: the only place that builds adapters.
- `duckless/cli.py`: the `duckless` command; rendering is done by pure functions.

The dependency rule: `core` imports nothing else from DuckLess, `service` only `core` and
`ports`, and only `wiring` imports `adapters`. Keep functions pure where you can, data immutable
(`@dataclass(frozen=True)`), and side effects at the edges.

`runtime/` is a separate package: the runner image and `duckless_runtime`, the library jobs
import.

## Tests

- Unit tests use fakes of the ports (`tests/conftest.py`), never Google Cloud.
- One behaviour per test, named `test_given_<context>_when_<action>_then_<outcome>`, with
  `# given`, `# when`, `# then` sections.
- Changes that touch Google Cloud (adapters, Terraform, runtime) also need a run on a real
  project. Say in the pull request what you ran and that you deleted what it created
  (`duckless destroy --force`, then check nothing is left).

## Pull requests

- One topic per pull request, branched from `main`.
- Commit titles are plain sentences describing the change ("Retry the IAM grant while a new
  service account is not visible yet"), not `feat:`/`fix:` prefixes. Use the body to say why.
- The description has a summary, what changed, and a test plan with what you ran.
- Update the docs (`docs/`) and, when the behaviour of a command changes, the Agent Skills
  (`duckless/plugin/skills/`) in the same pull request.
- Pull requests are squash-merged.

## Releases

Releases are cut by the maintainer: a release pull request bumps `pyproject.toml`,
`runtime/pyproject.toml` and the plugin version together, then an annotated `vX.Y.Z` tag
publishes the runner image, the PyPI package and the GitHub Release. The release notes are the
tag message; there is no `CHANGELOG.md`.

## License

DuckLess is licensed under the [Apache License 2.0](LICENSE). By contributing, you agree that
your contributions are licensed under the same terms.
