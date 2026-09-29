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

The published charm is [hermes-operator](https://charmhub.io/hermes-operator),
available on `latest/edge`. Deploy it with the application name `hermes` used
throughout these instructions:

```sh
juju add-model --controller hermes hermes-dev  # only if the model does not exist
juju deploy --model hermes:hermes-dev hermes-operator hermes \
  --channel latest/edge --base ubuntu@24.04 \
  --constraints 'arch=amd64 cores=2 mem=4G root-disk=20G'
```

For a local build, run from `charms/hermes`:

```sh
charmcraft pack
juju deploy --model hermes:hermes-dev ./hermes-operator_amd64.charm hermes \
  --base ubuntu@24.04 --constraints 'arch=amd64 cores=2 mem=4G root-disk=20G'
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
| `github-app-id` | empty | Numeric GitHub App ID |
| `github-installation-id` | empty | Numeric installation ID |
| `github-app-secret` | unset | Optional Juju secret containing the PEM `private-key`; enables App authentication |
| `max-turns` | `20` | Model/tool iterations per conversation turn, from 1 to 1000 |
| `context-id` | derived from model UUID and application name | Stable identity for the optional OpenViking relation |

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

## External context with OpenViking

Relate a deployed [OpenViking charm](../openviking/README.md):

```sh
juju integrate -m hermes:hermes-dev hermes:context-store openviking:context
juju run -m hermes:hermes-dev hermes/0 check-context
```

The charm configures Hermes's bundled OpenViking memory provider and renders the
endpoint and client key into its restricted `.env`. The agent gains the provider's
search/read/remember tools and automatic recall/synchronization. Native local
memory and skills remain under `HERMES_HOME`; existing local knowledge is not
automatically bulk-migrated. Remote extraction is asynchronous.

Secret rotation and endpoint changes reconcile through the relation. Missing
connection data or unavailable authenticated access reports waiting separately
from gateway health. Disconnect removes the provider configuration and preserves
local knowledge; the provider retains remote knowledge and revokes the old key.
Knowledge held only remotely is unavailable while disconnected.

For a deliberate handover to a new application, record `context-id` from
`check-context`, disconnect the original application, and configure that same
value on the replacement before relating it. This reuses remote context; it does
not transfer local skills, persona, or sessions. See the
[relation contract](../../interfaces/openviking/README.md) for the trust boundary
and the [plan](../../PLAN.md) for coordinated backup/restore.

## GitHub App authentication

Use the App's downloaded **private key** (an unencrypted RSA PEM). Create a
separate Juju secret from the local file, then grant and configure it. The
`#file` syntax reads the file without putting its contents in shell arguments:

```sh
github_app_secret=$(juju add-secret --model hermes:hermes-dev github-app \
  'private-key#file=/path/to/gh-app-key.pem')
juju grant-secret --model hermes:hermes-dev "$github_app_secret" hermes
juju config --model hermes:hermes-dev hermes \
  github-app-id=4245402 github-installation-id=145176996 \
  github-app-secret="$github_app_secret"
```

Replace the key path above. The IDs can be configured before the
secret; GitHub support stays disabled until `github-app-secret` is set.

The charm writes `/var/lib/hermes/gh-app-key.pem` as mode 0600, owned by `hermes`,
and adds these settings to the managed `/var/lib/hermes/.env`:

```dotenv
GH_APP_ID="4245402"
GH_INSTALL_ID="145176996"
GH_APP_KEY="/var/lib/hermes/gh-app-key.pem"
```

These variables are available to Hermes terminal commands. The charm also sets
Hermes's native `GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, and
`GITHUB_APP_PRIVATE_KEY_PATH` aliases for built-in GitHub skill-source access.

Enabling this configuration installs Ubuntu's `gh` package and places a
charm-managed wrapper at `/opt/hermes/bin/gh` first on the gateway and terminal
shell paths. Each authenticated invocation exchanges a signed App JWT for a
fresh installation token and passes it to `/usr/bin/gh` through `GH_TOKEN`.
The wrapper does not store tokens in `.env` or a CLI login file. This follows
[GitHub's installation authentication flow](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)
and the [CLI's environment authentication](https://cli.github.com/manual/gh_help_environment).
It targets github.com. A single command running longer than the token's one-hour
lifetime may need to be rerun; renewal happens on the next invocation.

Check the configured installation using a read-only API request:

```sh
juju run --model hermes:hermes-dev hermes/0 check-github-app
```

The action reconciles configuration and reports only the App ID, installation
ID, and accessible repository count. It makes no model requests and does not
post reviews. Unit **active** status checks gateway health; use this action to
verify GitHub authentication. Repository operations depend on the App's granted
permissions and selected repositories.

For manual diagnostics on the unit, run the wrapper as the workload user:

```sh
juju exec --model hermes:hermes-dev --unit hermes/0 -- \
  runuser -u hermes -- /opt/hermes/bin/gh api /installation/repositories --jq .total_count
```

Rotate the key with:

```sh
juju update-secret --model hermes:hermes-dev github-app \
  'private-key#file=/path/to/new-gh-app-key.pem'
```

Secret revisions replace the local key and restart Hermes when its contents
change. Resetting `github-app-secret` removes the local key and App settings
on reconciliation, while retaining OpenRouter configuration and agent state.
An invalid or inaccessible configured secret blocks and stops the gateway.
Previously issued tokens retain GitHub's normal expiry/revocation behavior.

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
lxc file pull hermes-charm-builder/root/hermes-operator/hermes-operator_amd64.charm .
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

### GitHub CLI terminal smoke check

This installs the optional `gh` helper, then uses an isolated temporary Hermes
home to check wrapper resolution and environment handling through the real
terminal implementation. It reads no private keys and makes no GitHub or model
requests. Run it with the installed workload interpreter:

```sh
lxc file push tests/integration/github_cli_smoke.py "$HERMES_INSTANCE/opt/hermes/github-cli-smoke.py"
lxc exec "$HERMES_INSTANCE" -- \
  /opt/hermes/hermes-agent-345cd2b057a452236de401d3534b8502a7465e8d/.venv/bin/python \
  /opt/hermes/github-cli-smoke.py
```

Use `check-github-app` after configuring a real key to test GitHub authentication.

See [docs/validation.md](docs/validation.md) for the first deployment's measured
results and remaining checks.

See [PLAN.md](../../PLAN.md) for subsequent milestones. MCP relations, observability,
distributed workers, and high availability are not part of this first charm.
