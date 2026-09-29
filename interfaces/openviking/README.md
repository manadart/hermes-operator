# `openviking` relation, schema version 1

Experimental machine-charm interface: `hermes:context-store` requires one
`openviking:context` provider. Application data is written by the leader.

| Direction | Field | Meaning |
| --- | --- | --- |
| Consumer → provider | `schema-version` | Exactly `1` |
| Consumer → provider | `context-id` | Stable, operator-controlled identity, 1–63 lowercase letters, digits or hyphens; starts with a letter or digit |
| Provider → consumer | `schema-version` | Exactly `1` |
| Provider → consumer | `context-id` | Echo of the accepted identity |
| Provider → consumer | `endpoint` | HTTP(S) origin, including port; no userinfo, path, query or fragment |
| Provider → consumer | `credentials` | Relation-granted application Juju secret containing `api-key` |
| Provider → consumer | `account-id` | Isolated OpenViking account derived from `context-id` |
| Provider → consumer | `user-id` | `hermes`, a regular USER within that account |
| Provider → consumer | `server-version` | Pinned workload release, currently `0.4.22` |

The root server key, account administrator key, and model credentials are never
published. The client key selects its account and user; consumers must not need
identity override headers. The contract uses the bundled Hermes HTTP provider,
not a new agent tool or an OpenViking SDK installed in Hermes.

## Identity and lifecycle

Hermes defaults to a UUID derived from the Juju model UUID and application name.
Unit replacements retain that identity. To deliberately reuse a store from a
different application/model, set its `context-id` to the old value **before**
relating, after disconnecting the previous application. This is an operator
handover: anyone authorized to configure and relate consumers can request an
existing identity. The relation is not an untrusted public tenant-registration API.

One identity may have only one active relation. Changing identity while related
is rejected. The account is `ctx-` plus the first 32 hexadecimal characters of
SHA-256 of the identity; the account's regular user is always `hermes`.

The provider publishes only after server readiness, account provisioning, and
secret grant. Requests without an identity wait; invalid requests block without
stopping other clients. Hermes waits for connection data and checks authenticated
access separately from its gateway liveness. Outages do not silently switch the
selected provider to local memory.

`rotate-client-key relation-id=N` changes the API key and its Juju secret revision.
The old key immediately stops working; Hermes refreshes its credential and
restarts the gateway. Rotation is not zero-downtime. Reconciliation recovers a
successful server-side rotation whose secret update was interrupted.

Disconnect rotates the key and revokes the grant, retaining account/user data.
If the server is unavailable, revocation waits and retries; the old key may remain
usable until that succeeds. Hermes removes the external-provider settings on
relation-broken and resumes its local memory configuration. Reconnecting with
the same identity issues the current key for the retained data. Neither charm
deletes context on relation removal.

## Transport and persistence

The initial provider advertises HTTP on port 1933 inside the private LXD network.
It is not exposed by Juju. Credentials and context travel without TLS on that
network; deploy only on a trusted private network. HTTPS endpoints with system
trust are accepted by Hermes, but TLS termination and custom CA exchange are
future work and are not advertised capabilities of this provider.

OpenViking persists under `/var/lib/openviking/data`; Hermes keeps its own local
memory and skills. Both are single-unit services with local machine storage.
Restart/reconnect persistence does not cover machine loss. Removing the provider
unit/application can destroy its storage.

## Export boundary for the next milestone

A regular client can export its own `viking://user/hermes/memories/` through
`POST /api/v1/pack/export` with `include_vectors: false`. Full-server backup and
administration remain unavailable to that credential. This establishes a scoped
export boundary, not an implemented charm backup action.

Future coordinated backup must first stop new agent writes, drain Hermes's
pending conversation synchronization, await server extraction task completion,
and check indexing/queue state. A successful commit submission or health check
does not establish a complete export. A timeout must fail or explicitly mark
the snapshot incomplete. Local memories, skills and optional persona must then
be packaged separately from the scoped remote export. Restore without OpenViking
requires a conversion/retrieval design and is a later milestone.

See the [development plan](../../PLAN.md) and
[OpenViking API source](https://github.com/volcengine/OpenViking/tree/v0.4.22/openviking/server/routers).
