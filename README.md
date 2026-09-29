# Hermes and OpenViking operators

Experimental Juju machine charms for a persistent Hermes agent and its external
OpenViking context store, targeting Ubuntu on LXD.

- [Hermes](charms/hermes/README.md): the existing single-unit agent charm, using
  OpenRouter and optional GitHub App authentication.
- [OpenViking](charms/openviking/README.md): a single-unit context service with
  separate credentials for each related agent.
- [Development plan](PLAN.md): integration milestones, knowledge backup/restore,
  and restoration without an external context service.
- [Hermes validation](charms/hermes/docs/validation.md): completed checks and their limits.
- [Integration validation](docs/validation.md): live OpenViking checks and the
  development controller's refresh limitation.
- [Publishing](docs/publishing.md): Charmhub releases and verified artifacts.

Both charms are published on `latest/edge` for Ubuntu 24.04 amd64. In your
chosen Juju machine model, deploy
[hermes-operator](https://charmhub.io/hermes-operator) with the application name
`hermes`, and [openviking](https://charmhub.io/openviking):

```sh
juju deploy hermes-operator hermes --channel latest/edge --base ubuntu@24.04 \
  --constraints 'arch=amd64 cores=2 mem=4G root-disk=20G'
juju deploy openviking --channel latest/edge --base ubuntu@24.04 \
  --constraints 'arch=amd64 cores=2 mem=4G root-disk=20G'
juju integrate hermes:context-store openviking:context
```

Both applications need an OpenRouter secret containing `api-key`. Create one
using the [Hermes configuration helper](charms/hermes/README.md#deploy), or grant
an existing secret in that model and configure its reference:

```sh
juju grant-secret <openrouter-secret-id> hermes
juju grant-secret <openrouter-secret-id> openviking
juju config hermes openrouter-secret=<openrouter-secret-id>
juju config openviking model-secret=<openrouter-secret-id>
```

Each charm owns its dependencies, uv lockfile, tests, and build context. Run
development and packaging commands from the corresponding charm directory:

```sh
cd charms/hermes
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff check src tests scripts
charmcraft pack
```

Development uses controller `hermes`, model `hermes-dev`. The source directory
remains `charms/hermes`; its published charm name is `hermes-operator` and the
application name in these examples is `hermes`. Workload paths are unchanged.
The development model was recreated on 2026-09-29 and now contains fresh
`hermes` and `openviking` applications connected through the context relation.
The earlier refresh limitation and validation history are recorded in
[integration validation](docs/validation.md).

The [OpenViking interface](interfaces/openviking/README.md) defines connection,
identity and credential lifecycle. [Integration tests](tests/integration/README.md)
exercise the two workloads together. Knowledge backup/restore is the next feature.

For local development, build each charm from its directory, then deploy and
configure as described in its README. Connect them with:

```sh
juju integrate -m hermes:hermes-dev hermes:context-store openviking:context
```

This first integration uses HTTP on the trusted private LXD network, single-unit
services and local machine storage. It does not yet provide TLS, HA or recovery
after losing the provider machine.
