# Initial development plan

## Agreed direction

- Build a machine charm for Hermes Agent, targeting LXD first.
- Use the existing `hermes` Juju controller for development.
- Start with an unprivileged LXD system container running Ubuntu 24.04.
- Use OpenRouter as the initial model provider.
- Start with one persistent agent identity and one Juju unit.
- Let Hermes manage subagents within that instance.
- Keep independently buildable Hermes and OpenViking charms in this experimental
  repository, with a shared relation specification and integration tests.
- Establish the OpenViking relation before implementing knowledge backup/restore,
  so portability is tested against both local and remote knowledge.

The first milestone is a deployed charm that installs and configures Hermes,
successfully runs a model-and-tool task, and preserves its state across service
restarts and an instance reboot.

## 1. Establish the development environment

**Implemented and deployed on 2026-09-21.** The charm is packaged and installed as
`hermes/0` in `hermes:hermes-dev`. The local API, secret
rotation, real GLM-5.3 inference, file/terminal tools, and a child result consumed
by its parent passed. The populated sessions and scratch file survived service
restart and LXD reboot. Provider/model switching remains untested. See
[Hermes validation](charms/hermes/docs/validation.md) for evidence and limits.

On 2026-09-22, development and charm packaging migrated to uv with dependencies
in `pyproject.toml` and `uv.lock`. Local revision 2 was built and refreshed on the
development unit; tests and the existing API/state checks passed.

GitHub App support was added on 2026-09-22 and deployed as local revision 4.
It manages the RSA key through a separate Juju secret, renders the `GH_APP_*`
settings, and authenticates `gh` using a fresh installation token per invocation.
The supplied App/installation IDs are staged on the development unit. Unit tests,
real terminal wrapper resolution, and existing API/state checks passed; GitHub
authentication awaits the user's private-key secret and `check-github-app` action.

Use controller `hermes` and a dedicated development model named `hermes-dev`.
Explicitly target `hermes:hermes-dev` in deployment and test commands. Install
Charmcraft if needed and record the tested tool versions and deployment commands.

Initial read-only verification on 2026-09-21 found, before implementation:

- The controller API is reachable on cloud `lxd`, region `default`.
- The controller runs Juju `4.1-beta3.1` on an Ubuntu 24.04 instance.
- Only the `controller` model exists; `hermes-dev` has not been created.
- The controller machine is started and the unit agent is idle, but the
  `juju-controller` workload reports blocked status:
  `multiple possible DB bind addresses; set a suitable dbcluster network binding`.
- `lxc`, `juju`, and Python are on PATH; Charmcraft and uv were not found on PATH.

The controller's `dbcluster` endpoint is now bound to the `hermes-db` space
(`10.155.5.0/24`), and its workload reports active. The development model and
Ubuntu 24.04 workload machine have been created; dependency installation and
gateway startup were verified there.

Development model commands (already executed):

```sh
juju add-model --controller hermes hermes-dev
juju status --model hermes:hermes-dev
```

## 2. Establish the Hermes workload contract

Select and pin a Hermes release or source commit, its Python runtime, and the
required dependencies. Establish a repeatable, noninteractive installation in a
virtual environment under `/opt/hermes`. Verify the selected release's minimal
dependencies for the gateway, API, terminal/file tools, and delegation.

Run Hermes in the foreground under a charm-managed systemd system service with
a dedicated unprivileged `hermes` account. Verify required environment,
initialization, shutdown behavior, and health reporting against the selected
release. The s6 supervisor used by Hermes's Docker image is not part of this
native installation.

Use `/var/lib/hermes` for persistent agent state and set `HERMES_HOME` explicitly.
Provide `/var/lib/hermes/workspace` for the first tool-use checks, with ownership
appropriate for the service account. Keep installed code owned by the
administrator; the charm controls version changes.

Agent commands initially execute as the service user within the LXD instance.
Per-task sandbox provisioning is a later design decision. If Docker-based task
environments become an early requirement, evaluate an LXD VM before introducing
nested-container configuration.

Use the gateway with its authenticated HTTP API as the first persistent service.
Confirm the selected release's API configuration and health behavior. Bind it to
instance loopback initially and access it through an SSH tunnel during
development.

## 3. Implement installation and configuration in a minimal charm

Scaffold an Ops machine charm for Ubuntu 24.04 with repeatable installation,
service-account and directory creation, configuration rendering, systemd service
management, and workload health reporting.

Expose an explicit model ID and an initial finite agent turn limit as charm
configuration. Use OpenRouter as the provider. Accept the provider credential
through a Juju secret reference, retrieve it in the charm, and deliver it through
a restricted workload credential file readable by the service account. Keep
runtime state and real credentials out of the repository.

The selected OpenRouter model is `z-ai/glm-5.3`, verified against its public
model catalog. Authentication for the Hermes API is separate from the OpenRouter
credential and stored in a unit-owned Juju secret.

Reconcile configuration changes and secret revisions with a controlled workload
restart where the selected release requires it. Report missing configuration,
unavailable credentials, and workload health through meaningful Juju status.

Implement an explicit single-unit constraint for this initial version. Scaling
requires a separate decision about task assignment and state ownership.

## 4. Validate the first deployed unit

Acceptance checks:

- The charm deploys to `hermes:hermes-dev` and starts the pinned Hermes version.
- Repeated reconciliation preserves the installed version and existing state.
- Missing required configuration or credentials produces an actionable status.
- An authenticated request completes through the configured OpenRouter model.
- A bounded task writes a file in the scratch workspace and verifies its contents.
- A bounded delegated subtask returns its result to the parent agent.
- A saved conversation and workspace file survive service restart and an LXD
  instance reboot.
- A model configuration change takes effect for a new session.
- Replacing the OpenRouter secret revision takes effect for subsequent requests.
- The API rejects unauthenticated requests, and service logs omit credentials.

Add focused charm tests for configuration, secret handling, and systemd
reconciliation, plus a repeatable live smoke-test procedure. Record which checks
actually ran and the model and Hermes versions used.

Restart and reboot persistence do not establish recovery after unit removal or
machine loss. Establish remote context ownership through the OpenViking relation
first, then validate knowledge backup/restore across both stores. Full state
recovery and attachable-storage requirements remain later work.

## 5. Restructure the repository

**Implemented on 2026-09-29.** The move to separate build contexts preserved the
Hermes charm name and workload paths. Existing tests and packaging passed.

Later that day, Charmhub publication changed the Hermes package name to
`hermes-operator` because `hermes` and `hermes-agent` were unavailable. Deployment
examples explicitly retain `hermes` as the application name. The published pair
is `hermes-operator` and `openviking` on `latest/edge`; see
[publication details](docs/publishing.md).

```text
charms/
  hermes/             # Existing charm, dependency lock, tests, scripts, and README
  openviking/         # Independently buildable OpenViking charm
interfaces/
  openviking/         # Versioned relation specification and schemas
tests/
  integration/        # Tests exercising both charms
README.md             # Overall architecture and deployment instructions
PLAN.md
```

Keep each charm's `charmcraft.yaml`, `pyproject.toml`, `uv.lock`, source, and unit
tests together. Hermes-specific smoke tests remain with Hermes. Future build,
test, and documentation changes should retain the published charm names and
workload paths.

## 6. OpenViking charm and relation

**Implemented and deployed on 2026-09-29.** OpenViking 0.4.22 is running with
Hermes in `hermes:hermes-dev`. The initial integration used a separate
`hermes-context` application because the controller rejected adding the endpoint
to the existing charm during refresh. Later that day, at the user's request,
the model and its three workload machines were destroyed and the model recreated
with fresh `hermes` and `openviking` applications. The OpenRouter credential was
re-imported; previous agent and context data was deliberately discarded.
See [integration validation](docs/validation.md) for the reset, controller error
and measured integration results.

The implemented interface uses isolated accounts, regular-user Juju secrets,
stable context IDs, credential rotation, retained data on disconnect, and
Hermes's existing provider. The initial transport is HTTP on the trusted private
LXD network; TLS/custom CA exchange is deferred. Storage is persistent on the
machine but has no machine-loss recovery yet.

Live extraction, semantic recall, real GLM-5.3 tool-based recall, scoped export,
account isolation, rotation, disconnect/reconnect retention and service-outage
recovery passed. There are 88 passing unit tests across both charms. Full
application replacement and live endpoint/IP changes remain untested; endpoint
changes have unit coverage. Backup/restore is now the next implementation step.

Build a minimal single-unit OpenViking machine charm on LXD, using a pinned
release in its own environment, an unprivileged systemd service, and persistent
storage. Manage its embedding/extraction model configuration and credentials
separately from the Hermes workload. Clustering and high availability are later
scope.

Define a versioned `openviking` interface between the
`hermes:context-store` consumer and `openviking:context` provider. Configure
Hermes's existing HTTP memory provider from relation data: endpoint, a Juju
secret reference for a dedicated client key, stable context identity, protocol
compatibility, and TLS trust where required. Support one external memory
relation per Hermes application initially.

Establish these lifecycle and portability requirements before backup work:

- OpenViking owns remote context storage and its recovery; Hermes retains local
  memory, skills, persona, and its independently managed credentials.
- Client identity survives unit replacement and deliberate reconnection to
  existing context. Do not derive durable identity solely from a unit name or
  relation ID.
- Isolate each Hermes application's context by default. Shared resources and
  permitted export scopes are explicit choices; do not grant a consumer the
  server's root credential.
- Rotate client credentials and reconcile endpoint changes through the relation.
  Removing a relation revokes access and disconnects the provider without
  deleting accumulated context.
- Define how pending conversation synchronization and asynchronous memory
  extraction are drained or reported before a consistent export. Connection
  health alone does not establish that knowledge has been stored.

Acceptance checks:

- Deploy and relate both charms on LXD; verify authenticated access and the
  rendered Hermes provider settings.
- Store representative knowledge through Hermes, wait for extraction, and
  verify retrieval in a fresh session.
- Restart or replace the Hermes instance and verify recall using the same
  context identity.
- Verify credential rotation, isolation between consumers, endpoint changes,
  and relation removal retaining remote data.
- Verify that provider failures are reported and are distinguishable from
  gateway health; establish the scoped export capability needed by milestone 7.

## 7. Next step: knowledge backup and restore across both stores

**Follows the OpenViking milestone; not yet implemented.** Add `backup` and
`restore` actions to move an agent's accumulated knowledge onto a fresh Hermes
deployment. Preserve support for deployments with only local knowledge.

The local scope is an explicit allowlist under `HERMES_HOME`: `memories/`,
`skills/`, and `SOUL.md` if present. Include an optional OpenViking export scoped
to the agent's knowledge and explicitly selected shared resources. Exclude
session databases and transcripts, workspace files, scheduled jobs, runtime
files, and charm-managed configuration and credentials from both scopes. The
destination retains its own Juju configuration, OpenRouter secret, GitHub App
key, API identity, and relation credentials.

Action behavior:

- `backup` creates a protected archive with a manifest recording the knowledge
  scopes, archive format version, Hermes/OpenViking versions, source identities,
  and file checksums. Coordinate local writes and pending remote extraction for
  a consistent export, then restore the prior service state. Return the archive
  path and checksum for manual retrieval off the unit. Requested remote content
  that cannot be exported must fail or explicitly mark the result incomplete;
  never silently report a complete backup.
- `restore` accepts a staged archive, validates its contents, paths, checksums,
  and version compatibility before changing state, and requires an explicit
  replacement option when destination knowledge already exists. Stop Hermes
  during local replacement, preserve a rollback copy, restore service-user
  ownership and permissions, then restart and check health. For an OpenViking
  destination, import into the destination identity and verify retrieval. Define
  recovery from a partial restore across the two stores and avoid duplicate
  imports when reconnecting to the original context.

Acceptance checks:

- Back up representative memory, a skill with supporting files, and optional
  persona content; restore into a fresh Hermes home and verify Hermes can read
  the memory and discover the skill.
- Export remote knowledge and restore it to a fresh OpenViking-backed deployment;
  verify recall, identity mapping, and completion of any required reindexing.
- Verify excluded files never enter the archive and destination configuration,
  credentials, sessions, workspace, and scheduled jobs remain intact.
- Reject corrupt or incompatible archives and unsafe paths or links without
  modifying destination knowledge; verify replacement and rollback behavior.
- Exercise the actions on LXD and document manual archive transfer and any
  external tools, repositories, or paths needed by restored skills.

Conversation continuity, workspace recovery, scheduled-job migration, automated
retention, and an object-storage relation are follow-up scope.

## 8. Restore exported knowledge without OpenViking

Extend restore with an explicit conversion path for a destination that has no
OpenViking relation. Preserve the original provider export alongside converted
local documents, provenance, and a retrieval skill or index that makes the
knowledge discoverable. Keep native memory bounded; do not silently truncate a
remote corpus or claim that local retrieval reproduces OpenViking's semantic
search and extraction behavior.

Acceptance: export an OpenViking-backed agent, restore onto a fresh Hermes
instance with no access to the source service, and verify retrieval of
representative exported knowledge. Report content that could not be converted
and preserve it in the original export. Destination credentials remain intact.

## 9. Later integrations

An MCP relation can follow against a small test tool service with an observable
result. Define endpoint discovery, authentication, credential rotation, and
removal behavior on both ends.

COS integration, additional memory providers, A2A specialists, distributed
workers, high availability, and a Kubernetes charm remain later milestones with
their own lifecycle requirements.

## Initial repository deliverables

- README with prerequisites, deployment, configuration, and smoke-test commands.
- Charm metadata, dependency declarations, implementation, and focused tests.
- Recorded Hermes/runtime/dependency versions and tested configuration examples.
- Ignore rules for local credentials, build outputs, and runtime state.

## Upstream references

- [Hermes installation](https://hermes-agent.nousresearch.com/docs/getting-started/installation)
- [Hermes gateway and systemd support](https://hermes-agent.nousresearch.com/docs/user-guide/messaging)
- [Hermes model configuration](https://hermes-agent.nousresearch.com/docs/user-guide/configuring-models)
- [Hermes profiles and state ownership](https://hermes-agent.nousresearch.com/docs/user-guide/profiles)
- [Juju LXD provider](https://documentation.ubuntu.com/juju/3.6/reference/cloud/list-of-supported-clouds/the-lxd-cloud-and-juju/)

Verify these contracts against the selected release during implementation;
upstream documentation tracks a moving version.
