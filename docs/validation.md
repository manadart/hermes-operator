# First deployment validation

Recorded 2026-09-21 against the LXD controller `hermes`, model `hermes-dev`.

## Deployment

| Component | Tested value |
| --- | --- |
| Juju client | 4.1-beta3 |
| Juju controller and unit agent | 4.1-beta3.1 |
| Charmcraft | 4.4.2, built inside an Ubuntu 24.04 LXD container |
| Workload machine | Ubuntu 24.04 amd64, 2 CPU / 4 GiB memory / 20 GiB disk constraint |
| Unit / instance | `hermes/0` / `juju-c5e4a4-0` |
| Hermes | 0.21.3, tag `v2026.9.14` |
| Hermes commit | `345cd2b057a452236de401d3534b8502a7465e8d` |
| Runtime | Python 3.12, uv 0.11.6, upstream frozen lock with messaging/MCP extras |
| Selected model | `z-ai/glm-5.3`, verified in OpenRouter's public model catalog |
| Charm artifact | `hermes_amd64.charm`, local revision 1 |
| Artifact SHA-256 | `d0e26620da31257b5f86ece038ac4b0d0cd295ee1e7cf1908a0b2142d66ffea3` |

The existing controller was initially blocked by two possible database bind
addresses. Binding `controller:dbcluster` to the `hermes-db` space containing
`10.155.5.0/24`, then re-running its existing relation-changed handler, brought
the controller workload to active. Other existing LXD instances were not changed.

The workload's first archive download received HTTP 429. Reconciliation on the
next hook retried installation successfully. Installation remains dependent on
external Ubuntu, GitHub, and PyPI availability; this is not an offline charm.

## Passed checks

- All 22 unit tests, Ruff lint, and Ruff format checks.
- Charmcraft packaging and inspection of the built metadata, configuration,
  actions, dependencies, and matching source files.
- Live deployment and installation of the pinned Hermes version.
- Missing OpenRouter configuration produces an actionable blocked status.
- Gateway startup with a temporary, deliberately invalid provider credential;
  no model requests were sent using it.
- Service runs as `hermes:hermes`, with the API bound only to `127.0.0.1:8642`.
- Public `/health` and authenticated `/health/detailed` report healthy.
- `/v1/models` rejects missing authentication with HTTP 401 and accepts the
  separately generated API bearer token.
- `get-api-access` returns the loopback URL, selected model, and API secret
  reference without returning the token itself.
- A saved empty API session, workspace marker, and API token survive the charm's
  `restart` action and an LXD instance restart.
- Updating the temporary Juju secret through the helper's `--update` option
  propagates its new revision into the workload without changing API identity
  or deleting the saved session.
- After reboot, the helper's new-secret workflow starts the gateway, and a
  `max-turns` configuration change reaches the running workload.
- Configured credential values were absent from the service journal examined
  during these checks.

The repeatable local API and persistence check is
[`tests/integration/smoke.py`](../tests/integration/smoke.py). Unit tests also
cover invalid configuration, inaccessible/removed credentials, unsupported
scaling, unchanged configuration avoiding a restart, and safe credential-file
replacement. These unit checks do not establish live multi-unit behavior.

Resetting the secret reference did not promptly produce a configuration hook
during cleanup on this beta controller. Explicit reconciliation through the
`restart` action confirmed that the missing credential stops the service and
removes `.env`; the action correctly failed with the missing-secret message.
Subsequent fresh-secret setup and configuration changes did deliver hooks.
The event delay's cause was not established; immediate credential revocation is
not a validated guarantee.

## Real model acceptance

After the user configured the real OpenRouter secret, the first model request
found a missing managed-home directory (`/var/lib/hermes/memories`). Hermes's
`HERMES_MANAGED` mode requires cron, sessions, logs, and memories directories to
exist, although the gateway can report healthy without them. Revision 1 fixes
initialization on both installation and reconciliation of existing deployments,
preserves existing contents, and rejects directory symlinks before privileged
ownership changes. Two regression tests cover this behavior. The fix was built
and applied with `juju refresh` to the existing unit.

The real API checks then passed with confirmed runtime metadata identifying
provider `openrouter` and model `z-ai/glm-5.3`:

| Check | Evidence |
| --- | --- |
| Inference | Returned `HERMES_GLM_READY`, no tools, 1.10 seconds |
| File and terminal tools | `write_file`, `terminal` running `cat`, and `read_file` all succeeded; on-disk contents matched the unique marker, 3.69 seconds |
| Delegation | Exactly one persisted child session used GLM-5.3 and completed `17 × 19 = 323`; the parent returned `HERMES_DELEGATION_READY 323` |

These are single-run observations, not latency benchmarks. Top-level delegation
is asynchronous in this release: the child completion is persisted in the
parent's session history after the initial HTTP response. The test polls that
history and submits one parent follow-up to consume the result. A synchronous
first response containing the finished child result is not the API contract.

The successful report is retained on the workload machine at
`/var/lib/hermes/workspace/charm-model-smoke-xvl_onju/report.json`, alongside the
scratch file. The report names all three test sessions; the child session is
`20260921_183323_1e6f07`. The earlier diagnostic sessions are also retained.
No credential values are included in the report.

After these requests completed and the gateway reported idle, the populated
test sessions, exact assistant replies, tool-call history, and generated file
were verified after both the charm's `restart` action and an LXD instance
restart. The gateway returned to active. The real provider key and API token
were absent from the service journal examined after these checks.

The reproducible model check is
[`tests/integration/model_smoke.py`](../tests/integration/model_smoke.py). Its
`run` mode makes billable requests; `verify` only reads saved sessions and files.
The successful sequence reused recorded replies with `resume` after adjusting
the test for asynchronous completion and normal arithmetic-result wording.

The first terminal probe encountered Hermes's interactive approval requirement
for inline Python execution. The final check uses a simple `cat` command and
asserts no tool result is pending approval. An interactive approval workflow is
not provided by this charm.

## Remaining scope

A provider/model configuration change taking effect on a real inference request
has not been tested. The deployed unit is active with the user's OpenRouter
secret configured; temporary test provider secrets from initial deployment
were removed.

The active status and local health checks establish gateway readiness, not
provider authorization, credit, model availability, or successful inference.
Machine-loss recovery, backup/restore, scaling, relations, and Kubernetes remain
outside this first deployment.

## uv migration (2026-09-22)

Development dependencies now live in the `dev` group in `pyproject.toml`, runtime
dependencies live in `project.dependencies`, and `uv.lock` pins both. The two
requirements files were removed. Python 3.12 is selected by `.python-version`.
The existing pinned Hermes workload installer already used uv and is unchanged.

Validation:

- Local uv 0.11.6: `uv sync --locked`, all 22 tests through `uv run --locked`,
  Ruff lint/format, and `uv lock --check --offline` passed.
- A fresh Charmcraft 4.4.2 build used the native `uv` plugin with the `astral-uv`
  snap (uv 0.12.17), the frozen lockfile, and development dependencies excluded.
- The artifact contains exactly five runtime distributions: Ops 3.8.2, PyYAML
  6.0.3, OpenTelemetry API 1.44.0, typing-extensions 4.16.0, and websocket-client
  1.9.2. Its charm source matches the repository; test tools are absent.
- Artifact SHA-256:
  `f9e7cec930bffbb45debdce25402f77b874b4eab484533af37d639377e663939`.
- Refreshed `hermes:hermes-dev` to local charm revision 2. Its upgrade hook
  completed and the unit remained active/idle. The Hermes process PID and start
  time were unchanged, confirming no workload restart was required.
- The existing model acceptance report's `verify` check passed through the
  authenticated API, confirming its saved replies, tool history, and scratch
  file. No new model requests were made for this migration.

## Sources

- [Pinned Hermes source](https://github.com/NousResearch/hermes-agent/tree/345cd2b057a452236de401d3534b8502a7465e8d)
- [OpenRouter model catalog](https://openrouter.ai/api/v1/models)
- [Deployment and operational instructions](../README.md)
