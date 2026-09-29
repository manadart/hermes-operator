# Charmhub publication

Published on 2026-09-29 by Joseph Phillips (`manadart`). Both charms target
Ubuntu 24.04 amd64 and are available on `latest/edge`.

| Install name | Revision | Charmhub ID |
| --- | --- | --- |
| [hermes-operator](https://charmhub.io/hermes-operator) | 2 | `SuTpcmx28d8q1Ubp92hPaupeumzEVaq0` |
| [openviking](https://charmhub.io/openviking) | 1 | `YjWKFVzsNUVTktavF8s7P56VNQs3ntrW` |

Use the install names with `juju deploy`; the opaque IDs are recorded for
reference. The [root README](../README.md) gives deployment, secret configuration
and relation commands. Deploying `hermes-operator` with the explicit application
name `hermes` keeps those commands consistent.

## Verification

- `juju info` reports both releases on `latest/edge`, with Ubuntu 24.04 amd64 as
  the supported base and architecture.
- Public `juju download` calls, without publisher credentials, fetched both
  archives. Their SHA-256 digests exactly match the local uploaded artifacts.
- Every packaged workload/charm source file matches the corresponding local
  source. The archives contain no `.env`, PEM, private-key or credential files.
- Hermes was rebuilt after changing only its charm metadata name to
  `hermes-operator`. Its 70 unit tests and Ruff lint passed again. The workload
  source and dependency locks are unchanged.
- OpenViking uses the same artifact as the fresh development deployment.
  The earlier 18 OpenViking unit tests and live integration checks are recorded
  in [validation.md](validation.md).
- Publication did not refresh or redeploy the running development applications.
  Live validation describes the local packages; the new Hermes package name
  was verified through packaging and public download. The builder was stopped
  after packaging.

Artifact SHA-256 digests:

```text
hermes-operator_amd64.charm
1c2f6d9ee1e4eb5707b29c29e33a86156058cf41256d319eb8276b9266588714

openviking_amd64.charm
cca1176b6345e8a57628a6e238c1a035bc79fc73726d7a1bf2118b43718d06c1
```

## Later releases

Run Charmcraft from each charm's own directory, where its `charmcraft.yaml` is
available. Authenticate with `charmcraft login`, or provide exported credentials
through `CHARMCRAFT_AUTH` as described in the
[Charmcraft authentication documentation](https://documentation.ubuntu.com/charmcraft/en/stable/reference/commands/login/).
Keep credentials out of the repository and shell arguments.

From `charms/hermes`:

```sh
charmcraft pack
charmcraft upload hermes-operator_amd64.charm --name hermes-operator
charmcraft release hermes-operator --revision <approved-revision> --channel latest/edge
```

From `charms/openviking`:

```sh
charmcraft pack
charmcraft upload openviking_amd64.charm --name openviking
charmcraft release openviking --revision <approved-revision> --channel latest/edge
```

Use each upload's actual approved revision; the two revision sequences are
independent. Check `charmcraft status <name>` after release.

## Superseded registrations

`hermes` and `hermes-agent` were unavailable, so the selected Hermes name is
`hermes-operator`. The unused `manadart-openviking` registration was removed.
`manadart-hermes` has an uploaded revision but was never released to any channel.
Charmhub rejected its removal because names with existing revisions cannot be
unregistered. Do not use it for deployments or future releases.

The first upload under `hermes-operator` was rejected as a duplicate of that
prefixed archive. Rebuilding with the correct package metadata produced the
distinct, approved revision 2 released above.
