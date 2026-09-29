# OpenViking Operator

Experimental, single-unit Juju machine charm for
[OpenViking 0.4.22](https://github.com/volcengine/OpenViking/releases/tag/v0.4.22),
targeting Ubuntu 24.04 amd64 on a private LXD network. It runs as the unprivileged
`openviking` user under systemd, with persistent context in
`/var/lib/openviking/data`.

The workload uses uv 0.11.6, Python 3.12 and a committed frozen dependency lock
under `src/workload-deps/`. That lock is separate from the charm's own `uv.lock`.
Model configuration is in `/var/lib/openviking/ov.conf`, mode 0600. Installed code
is root-owned under `/opt/openviking`.

## Build and deploy

The published charm is [openviking](https://charmhub.io/openviking), available on
`latest/edge` for Ubuntu 24.04 amd64. To install it from Charmhub:

```sh
juju deploy -m hermes:hermes-dev openviking --channel latest/edge --base ubuntu@24.04 \
  --constraints 'arch=amd64 cores=2 mem=4G root-disk=20G'
```

For a local build, run from `charms/openviking` instead:

```sh
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff check src tests
charmcraft pack
juju deploy -m hermes:hermes-dev ./openviking_amd64.charm \
  --base ubuntu@24.04 --constraints 'arch=amd64 cores=2 mem=4G root-disk=20G'
```

For either installation method, grant and configure the OpenRouter secret, then
relate the applications:

```sh
juju grant-secret -m hermes:hermes-dev <openrouter-secret-id> openviking
juju config -m hermes:hermes-dev openviking model-secret=<openrouter-secret-id>
juju integrate -m hermes:hermes-dev hermes:context-store openviking:context
```

The OpenRouter secret must contain `api-key`. It can be the existing agent's
user-owned secret, explicitly granted to both applications, or a separate key.
There are two model roles:

| Option | Default | Purpose |
| --- | --- | --- |
| `model-secret` | unset | OpenRouter secret for both models |
| `embedding-model` | `openai/text-embedding-3-small` | Semantic index and query vectors |
| `embedding-dimension` | `1536` | Vector dimension |
| `extraction-model` | `z-ai/glm-5.3` | Text memory extraction and summaries |

The embedding model and dimension cannot change after first configuration;
changing either would make the existing index incompatible. Restore the original
settings if blocked. Migration/reindexing needs a separate procedure. Extraction
model or credential changes restart the service while retaining context.
This configuration targets text knowledge; image processing is not validated.

## Relation and lifecycle

Each related application receives an isolated account and regular user key in a
relation-granted Juju secret. The server root key stays in a provider-owned Juju
secret. See the [versioned relation contract](../../interfaces/openviking/README.md)
for identity handover, readiness, revocation retries, and export scope.

```sh
juju run -m hermes:hermes-dev openviking/0 check-context
juju run -m hermes:hermes-dev hermes/0 check-context
juju show-unit -m hermes:hermes-dev openviking/0  # find context relation-id
juju run -m hermes:hermes-dev openviking/0 rotate-client-key relation-id=<id>
```

`check-context` makes authenticated access checks without model requests. Active
status establishes server readiness and relation provisioning; it does not prove
OpenRouter authentication or successful extraction/indexing. The repository's
[integration smoke test](../../tests/integration/README.md) exercises those paths.

Disconnecting invalidates the client key and retains the account's data. Do not
delete the provider unit or machine to disconnect an agent. There are no backup
or restore actions yet, and local machine storage does not survive machine loss.
There is no clustering or scale-out support in this first charm.

Port 1933 uses HTTP inside the trusted private LXD network; Juju exposure is off.
TLS termination, custom CA relations, public deployment and HA are later work.
Model traffic to OpenRouter uses HTTPS. The charm installs packages from Ubuntu
and PyPI and requires outbound connectivity.
