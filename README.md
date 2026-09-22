# Hermes Operator

A Juju machine charm for a persistent [Hermes Agent](https://github.com/NousResearch/hermes-agent),
initially targeting Ubuntu 24.04 on LXD. The default model is OpenRouter's
`z-ai/glm-5.3`.

The charm installs Hermes 0.21.3 (`v2026.9.14`) at commit
`345cd2b057a452236de401d3534b8502a7465e8d`. It verifies the source archive's
SHA-256 and uses uv 0.11.6 with the upstream frozen dependency lock, Python 3.12,
and the messaging/MCP extras. Ubuntu supplies system packages and Python security
updates; this is not a fully immutable operating-system image.

## Deploy

Requires a Juju controller supporting secrets, an Ubuntu 24.04 amd64 machine
cloud, and outbound access to Ubuntu archives, GitHub, PyPI, and OpenRouter.
Development uses controller `hermes` and model `hermes-dev`.

```sh
charmcraft pack
juju add-model --controller hermes hermes-dev  # only if the model does not exist
juju deploy --model hermes:hermes-dev ./hermes_amd64.charm \
  --base ubuntu@24.04 --constraints 'cores=2 mem=4G root-disk=20G'
```

Initial installation downloads the workload and its dependencies. The unit then
reports **blocked** until an OpenRouter secret is configured. On reconciliation,
the gateway stops if the required credential is removed or becomes inaccessible.

Enter the API key locally using the helper; input is hidden, and the key is sent
to Juju through a temporary mode-0600 file that is removed afterwards:

```sh
python3 scripts/configure-openrouter.py
juju status --model hermes:hermes-dev
```

Allow time for Juju to run the configuration hook. The `restart` action can force
immediate reconciliation if needed:
`juju run --model hermes:hermes-dev hermes/0 restart`.

The helper creates a user secret named `openrouter`, grants it to the application,
and sets the `openrouter-secret` configuration option. To rotate that secret:

```sh
python3 scripts/configure-openrouter.py --update
```

An existing Juju secret can also be used. It must contain an `api-key` field:

```sh
juju grant-secret --model hermes:hermes-dev <secret-id> hermes
juju config --model hermes:hermes-dev hermes openrouter-secret=<secret-id>
```

## Configuration and access

| Option | Default | Meaning |
| --- | --- | --- |
| `model` | `z-ai/glm-5.3` | OpenRouter model ID for new sessions |
| `openrouter-secret` | unset | Juju secret URI containing `api-key` |
| `max-turns` | `20` | Model/tool iterations per conversation turn, from 1 to 1000 |

The API listens only on `127.0.0.1:8642` inside the unit. Its bearer token is
generated separately from the OpenRouter key and stored as a unit-owned Juju
secret. Retrieve its **reference**, without putting its contents in action output:

```sh
juju run --model hermes:hermes-dev hermes/0 get-api-access
```

A model administrator can read that secret with `juju show-secret --reveal`.
Use an SSH tunnel to reach the API; add your SSH public key to the development
model first if needed. For example, with SSH access configured:

```sh
juju ssh --model hermes:hermes-dev --proxy=false hermes/0 -N -L 8642:127.0.0.1:8642
```

`GET /health` is unauthenticated and reports gateway liveness. `GET /v1/models`,
`GET /health/detailed`, and model/task requests require the API bearer token.
An **active** unit establishes gateway health; it does not establish that the
OpenRouter key is accepted, has credit, or can access the selected model.

## Runtime and state

- `hermes.service` runs the foreground gateway as the unprivileged `hermes` user.
- Installed code and its virtual environment live under `/opt/hermes`, owned by root.
- `HERMES_HOME=/var/lib/hermes` holds sessions, memory, generated skills, and config.
- The charm initializes the managed home's cron, sessions, logs, and memories directories.
- `/var/lib/hermes/workspace` is the initial working directory for agent commands.
- The charm owns `config.yaml` and `.env`; edit configuration through Juju.
- The API and CLI enable terminal, file, memory, session-search, todo, and delegation tools.
- Kanban dispatch is disabled in this first version. Subagent concurrency is capped at two.

Agent terminal commands execute directly as the service user inside the LXD
instance. This version does not create a separate sandbox for each task.
State survives service restarts and instance reboots. Removing the machine can
destroy that state; backup/restore and reattachment are not implemented.

Configuration changes and secret revisions restart the service when their
rendered values change. In-flight agent work may be interrupted. There is one
agent identity and one supported unit; scaling beyond one blocks and stops the
gateway until scale returns to one.

```sh
juju run --model hermes:hermes-dev hermes/0 restart
juju exec --model hermes:hermes-dev --unit hermes/0 -- systemctl status hermes.service
juju exec --model hermes:hermes-dev --unit hermes/0 -- journalctl -u hermes.service -n 50
```

## Development

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python
3.12. Runtime dependencies and the `dev` dependency group are declared in
`pyproject.toml`; `uv.lock` pins their resolved versions.

```sh
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff check src tests scripts
uv run --locked ruff format --check src tests scripts
```

Use `uv add <package>` for runtime dependencies and `uv add --dev <package>` for
development tools. After editing dependency declarations manually, run `uv lock`.
Keep `pyproject.toml` and `uv.lock` together in version control.

Charmcraft's [uv plugin](https://documentation.ubuntu.com/charmcraft/stable/reference/plugins/uv_plugin/)
builds the charm directly from the lockfile and excludes the development group.
The build environment installs the `astral-uv` snap; no requirements export is needed.

If Charmcraft is unavailable on the host, build in a disposable LXD container:

```sh
lxc launch ubuntu:24.04 hermes-charm-builder -c limits.cpu=2 -c limits.memory=4GiB
lxc exec hermes-charm-builder -- snap install charmcraft --classic
lxc exec hermes-charm-builder -- apt-get update
tar --exclude=__pycache__ -czf /tmp/hermes-charm-source.tar.gz \
  charmcraft.yaml src pyproject.toml uv.lock .python-version .jujuignore
lxc file push /tmp/hermes-charm-source.tar.gz hermes-charm-builder/root/
lxc exec hermes-charm-builder -- mkdir -p /root/hermes-operator
lxc exec hermes-charm-builder -- tar -xzf /root/hermes-charm-source.tar.gz -C /root/hermes-operator
lxc exec hermes-charm-builder --cwd /root/hermes-operator -- charmcraft pack --destructive-mode
lxc file pull hermes-charm-builder/root/hermes-operator/hermes_amd64.charm .
lxc stop hermes-charm-builder
```

Here `--destructive-mode` builds inside the disposable container. Ordinary host
builds should use `charmcraft pack` and its managed build environment.

### Live smoke check

`tests/integration/smoke.py` runs as root on the workload machine. It checks
health, API authentication, and preservation of an empty session, workspace
marker, and API token. It does not call OpenRouter or spend model credit.
The gateway must already be configured and running.

For the initial LXD deployment, using the instance ID shown in Juju status:

```sh
HERMES_INSTANCE=juju-c5e4a4-0  # substitute your workload instance ID
lxc file push tests/integration/smoke.py "$HERMES_INSTANCE/root/hermes-smoke.py"
lxc exec "$HERMES_INSTANCE" -- python3 /root/hermes-smoke.py seed
juju run --model hermes:hermes-dev hermes/0 restart
lxc exec "$HERMES_INSTANCE" -- python3 /root/hermes-smoke.py verify
lxc restart "$HERMES_INSTANCE"
# Wait for the instance and gateway to start, then:
lxc exec "$HERMES_INSTANCE" -- python3 /root/hermes-smoke.py verify
lxc exec "$HERMES_INSTANCE" -- python3 /root/hermes-smoke.py cleanup
```

This restarts the service and instance, so run it when no agent work is in flight.
Cleanup removes only this script's empty session and marker. Model inference,
tool execution, and delegation need separate checks with a real provider key.

### Model and tool smoke check

With a real OpenRouter key configured, this makes small billable GLM-5.3 requests
through the gateway. It verifies inference, writing/reading a scratch file,
terminal execution, and one child agent returning a result to its parent:

```sh
lxc file push tests/integration/model_smoke.py "$HERMES_INSTANCE/opt/hermes/model-smoke.py"
lxc exec "$HERMES_INSTANCE" -- runuser -u hermes -- python3 /opt/hermes/model-smoke.py run
```

The script prints its report path and retains its dedicated sessions and scratch
directory. To verify that their contents survive a restart, without more model calls:

```sh
lxc exec "$HERMES_INSTANCE" -- runuser -u hermes -- \
  python3 /opt/hermes/model-smoke.py verify --report <report-path>
```

Delegation is asynchronous: the initial chat response can say the child was
dispatched. This check waits for the completion in the parent session's history,
then submits a follow-up turn so the parent consumes the result. The script's
`resume --report <report-path>` mode rechecks recorded responses and can finish
that follow-up without spawning a second child.

See [docs/validation.md](docs/validation.md) for the first deployment's measured
results and remaining checks.

See [PLAN.md](PLAN.md) for subsequent milestones. MCP relations, observability,
distributed workers, and high availability are not part of this first charm.
