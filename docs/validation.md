# OpenViking integration validation — 2026-09-29

Development target: controller `hermes`, model `hermes-dev`, Juju
`4.1-beta3.1`, Ubuntu 24.04 LXD containers. Packaging used the existing
`hermes-charm-builder` with Charmcraft 4.4.2. Both charm and workload installations
use uv and committed dependency locks.

## Deployed applications

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
container was stopped; the workloads remain running.

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
