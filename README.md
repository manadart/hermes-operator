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

Each charm owns its dependencies, uv lockfile, tests, and build context. Run
development and packaging commands from the corresponding charm directory:

```sh
cd charms/hermes
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff check src tests scripts
charmcraft pack
```

Development uses controller `hermes`, model `hermes-dev`. Moving the source into
`charms/hermes` preserves the deployed charm name and workload paths.
The current integration deployment is `hermes-context` related to `openviking`;
the original `hermes` remains separate because this controller rejected adding
the new endpoint during refresh.

The [OpenViking interface](interfaces/openviking/README.md) defines connection,
identity and credential lifecycle. [Integration tests](tests/integration/README.md)
exercise the two workloads together. Knowledge backup/restore is the next feature.

Build each charm from its directory, then deploy and configure as described in
its README. Connect them with:

```sh
juju integrate -m hermes:hermes-dev hermes:context-store openviking:context
```

This first integration uses HTTP on the trusted private LXD network, single-unit
services and local machine storage. It does not yet provide TLS, HA or recovery
after losing the provider machine.
