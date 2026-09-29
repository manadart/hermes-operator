# OpenViking integration validation — 2026-09-29

Development target: controller `hermes`, model `hermes-dev`, Juju
`4.1-beta3.1`, Ubuntu 24.04 LXD containers. Packaging used the existing
`hermes-charm-builder` with Charmcraft 4.4.2. Both charm and workload installations
use uv and committed dependency locks.

## Initial integration deployment (subsequently destroyed)

The following records the initial integration run. The user subsequently
requested a model reset; that fresh deployment is documented below. Its teardown
removed these three applications, their machines and the test data referenced
in this section.

| Application | Charm revision | Workload | Machine | Purpose |
| --- | --- | --- | --- | --- |
| `hermes` | 4 | Hermes 0.21.3 | 0 / `10.155.5.143` | Existing agent, preserved |
| `hermes-context` | 8 | Hermes 0.21.3 | 2 / `10.155.5.149` | New relation-capable agent |
| `openviking` | 2 | OpenViking 0.4.22 | 1 / `10.155.5.107` | Persistent context provider |

The two new applications are related through `context-store` / `context` and
use `context-id=hermes-dev-context`. OpenRouter uses `z-ai/glm-5.3` for the agent
and extraction, and `openai/text-embedding-3-small` at dimension 1536 for indexing
and search. The user-owned OpenRouter secret was explicitly granted to each new
application. No API key or GitHub private key was copied into the repository.

## Checks completed

- Independently packaged both charms; compared every packaged source file,
  including OpenViking's workload lockfile, with the working tree.
- Hermes: 70 unit tests passed, including existing GitHub App behavior and new
  relation configuration, rotation, endpoint change, disconnect, and local-state
  preservation cases. Ruff lint and formatting passed.
- OpenViking: 18 unit tests passed, covering isolated client secrets, retry-safe
  provisioning, duplicate identities, rotation, revocation retry, startup ordering,
  scale rejection, immutable embedding configuration and data preservation.
  Ruff lint and formatting passed.
- Both charm `check-context` actions passed authenticated access checks.
- Hermes's actual bundled `viking_remember` tool submitted a synthetic Aurora
  Cedar project fact. The extraction task completed; a fresh provider session
  found it through semantic search and read its exact random checklist code.
  Native local memory files were not seeded with that fact.
- A real GLM-5.3 conversation through the running gateway recalled the exact
  code using `viking_read`. Automatic recall had already supplied the URI; the
  test accepts search or read while excluding local file/terminal tools.
- A regular client exported `viking://user/hermes/memories/` with
  `include_vectors=false`. The ZIP stream had 13 entries and contained the fact.
  That credential received 403 for account administration and full-server backup.
- A separately provisioned test account received 404 reading the first account's
  memory URI, while the primary account received 200. The test account was then
  deleted; the agent account was retained.
- `rotate-client-key` invalidated the former key (401); Hermes consumed the new
  Juju secret revision and the replacement key worked (200).
  Provider comparisons and `check-context` explicitly read the latest owned
  secret revision; a regression test covers checks after rotation.
- Removing the relation invalidated the current key (401), removed Hermes's
  OpenViking environment/configuration, and left both applications active.
  Reconnecting with the same identity restored access and semantic recall of
  the previously stored fact.
- With the context service deliberately stopped, Hermes reported
  `Gateway ready; OpenViking access unavailable` and its restart action failed
  with that status. Starting OpenViking and reconciling Hermes restored active
  status. A fresh GLM-5.3 session then used both `viking_search` and `viking_read`
  to recover the exact code after both services had restarted.
- The original `hermes/0` passed its existing non-billable model-smoke persistence
  verification, covering saved conversations and the scratch marker.

The repeatable helpers and lifecycle procedure are in
[tests/integration](../tests/integration/README.md). The non-secret live test report
is `/var/lib/hermes/workspace/context-smoke.json` on `hermes-context/0`.
The final successful gateway recall session was
`context-recall-96d448a7a80f4a01a54dcd3b081cff11`. Both connected applications
and the original agent were active at the end of testing. The packaging
container was stopped. These workloads were subsequently removed by the requested
model reset; the old report and session are historical evidence, not live paths.

## Fresh model deployment later on 2026-09-29

At the user's request, `hermes:hermes-dev` was gracefully destroyed, including
all three workload machines and their agent/context data, then recreated on the
same controller and LXD cloud. No force removal was needed.

- Previous model UUID: `b30524d6-bfc4-4e54-82d1-fd45bdc5e4a4`.
- New model UUID: `bd7c2210-885e-440c-8765-e9ce85767f79`.
- Fresh applications: `hermes` and `openviking`, each local charm revision 0,
  connected through `hermes:context-store` / `openviking:context`.
- Machines: `juju-767f79-0` and `juju-767f79-1`, each Ubuntu 24.04 with 2 cores,
  4 GiB memory and a 20 GiB root disk. Their private addresses are
  `10.155.5.202` (Hermes) and `10.155.5.83` (OpenViking).
- Packages were checked against all source files in commit `d21881d` before
  deployment; no charm code changes were needed for this reset.
- The existing user-owned OpenRouter key was transferred using a private
  temporary file, imported into a new model-owned `openrouter` secret, verified,
  and explicitly granted to both applications. The temporary credential file
  was deleted. Charm-owned API and relation credentials were generated afresh.
- Hermes retains model `z-ai/glm-5.3`, `max-turns=20`, and staged GitHub App IDs
  `4245402` / `145176996`. There was no GitHub private-key secret configured to
  transfer. The context identity now uses the normal new-model/application
  default; old memories, skills and sessions were not restored.

Both applications reached active with idle unit agents. Their `check-context`
actions passed: Hermes reported context ID
`38a68781-4dba-5b89-8b00-241eaf124200` and endpoint
`http://10.155.5.83:1933`; OpenViking reported one authenticated client.
The old `juju-c5e4a4-*` workload containers are absent, and the builder remains
stopped. The controller and unrelated LXD instances were retained.

A new synthetic Aurora Cedar fact was submitted through Hermes's bundled
provider. Extraction task `c69bcc84-6ba4-4b6b-b3f8-661b1b007004` completed, and a
fresh provider session found the exact random checklist code through semantic
search and read at `viking://user/hermes/memories/entities/project/aurora_cedar.md`.
The test also confirmed that the regular user cannot list accounts or request a
full-server backup. The new report is
`/var/lib/hermes/workspace/context-smoke.json` on `hermes/0`.
This reset reran deployment, authentication, extraction and provider recall;
the broader rotation, outage and gateway-chat checks above describe the initial
integration run of the same charm code.

## Controller refresh limitation

Refreshing the original `hermes` from revision 4 to the relation-capable charm
failed before changing the deployed revision:

```text
ERROR setting application "hermes" charm: one or more of the provided endpoints
"context-store, juju-info, peers" do not exist
```

Retrying with `--bind alpha` produced the same error. Deploying a fresh
`hermes-context` application succeeded, as did subsequent refreshes with an
unchanged endpoint set. The existing agent was not replaced and its state was
verified intact. This is a limitation observed on this development controller,
not evidence that all Juju releases reject endpoint additions. No controller
code or database was modified to work around it.

## Limits and next work

This is a single-unit, trusted-private-network experiment. Context traffic uses
HTTP, without TLS/custom CA exchange. Storage lives on the provider machine;
there is no HA, attachable-storage recovery or machine-loss protection.

The scoped export test establishes API capability, not a coordinated backup:
it does not quiesce every agent writer or provide an atomic cross-store snapshot.
Knowledge backup/restore, mapping an archive to a new provider, and conversion
for a destination without OpenViking remain the next milestones in
[PLAN.md](../PLAN.md). Full Hermes application replacement/handover and live
provider IP changes have not been tested; endpoint changes have unit coverage.
Image/multimodal ingestion and a live GitHub App authentication check were not
part of these integration checks.
