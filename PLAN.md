# Initial development plan

## Agreed direction

- Build a machine charm for Hermes Agent, targeting LXD first.
- Use the existing `hermes` Juju controller for development.
- Start with an unprivileged LXD system container running Ubuntu 24.04.
- Use OpenRouter as the initial model provider.
- Start with one persistent agent identity and one Juju unit.
- Let Hermes manage subagents within that instance.

The first milestone is a deployed charm that installs and configures Hermes,
successfully runs a model-and-tool task, and preserves its state across service
restarts and an instance reboot.

## 1. Establish the development environment

**Implemented and deployed on 2026-09-21.** The charm is packaged and installed as
`hermes/0` in `hermes:hermes-dev`. The local API, secret
rotation, real GLM-5.3 inference, file/terminal tools, and a child result consumed
by its parent passed. The populated sessions and scratch file survived service
restart and LXD reboot. Provider/model switching remains untested. See
[docs/validation.md](docs/validation.md) for evidence and limits.

On 2026-09-22, development and charm packaging migrated to uv with dependencies
in `pyproject.toml` and `uv.lock`. Local revision 2 was built and refreshed on the
development unit; tests and the existing API/state checks passed.

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
machine loss. Define and validate backup/restore and any attachable-storage
requirements as a subsequent lifecycle milestone.

## 5. Add the first relation

After the installation milestone works, implement an MCP relation against a
small test tool service with an observable result. Define endpoint discovery,
authentication, credential rotation, and removal behavior on both ends.

COS integration follows as the next operational improvement. A2A specialists,
external memory, distributed workers, high availability, and a Kubernetes charm
remain later milestones with their own lifecycle requirements.

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
