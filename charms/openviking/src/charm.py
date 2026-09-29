#!/usr/bin/env python3
"""Single-unit OpenViking provider with persistent, isolated client identities."""

import hashlib
import json
import logging
import re
import secrets
import subprocess

import ops

from api import APIError, OpenVikingAPI
from workload import VERSION, OpenVikingWorkload

logger = logging.getLogger(__name__)
ROOT_LABEL = "openviking-root"
IDENTITY_PATTERN = r"[a-z0-9][a-z0-9-]{0,62}"


class ConfigurationError(Exception):
    """An operator-facing message without credential values."""


class RelationError(Exception):
    """A consumer request that must not interrupt other clients."""


class PeerPending(ConfigurationError):
    """Peer data is not available during initial deployment yet."""


def account_for(identity: str) -> str:
    return "ctx-" + hashlib.sha256(identity.encode()).hexdigest()[:32]


class OpenVikingCharm(ops.CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        self.workload = OpenVikingWorkload()
        for event in (
            self.on.install,
            self.on.start,
            self.on.config_changed,
            self.on.upgrade_charm,
            self.on.leader_elected,
            self.on.update_status,
            self.on.secret_changed,
            self.on.peers_relation_created,
            self.on.peers_relation_joined,
            self.on.peers_relation_departed,
            self.on.context_relation_joined,
            self.on.context_relation_changed,
        ):
            self.framework.observe(event, self._reconcile)
        self.framework.observe(self.on.context_relation_broken, self._broken)
        self.framework.observe(self.on.stop, self._stop)
        self.framework.observe(self.on.rotate_client_key_action, self._rotate)
        self.framework.observe(self.on.check_context_action, self._check)

    def _root_secret(self):
        try:
            return self.model.get_secret(label=ROOT_LABEL)
        except ops.SecretNotFoundError:
            return self.app.add_secret({"api-key": secrets.token_urlsafe(48)}, label=ROOT_LABEL)

    def _api(self):
        return OpenVikingAPI(self._root_secret().peek_content()["api-key"])

    def _records(self) -> tuple[dict, ops.Relation]:
        peer = self.model.get_relation("peers")
        if peer is None:
            raise PeerPending("Waiting for peer relation")
        return json.loads(peer.data[self.app].get("clients", "{}")), peer

    def _settings(self):
        if self.app.planned_units() != 1:
            raise ConfigurationError("This charm supports one unit; reduce application scale to 1")
        secret_id = self.config.get("model-secret")
        if not secret_id:
            raise ConfigurationError("Set model-secret to a Juju secret containing api-key")
        try:
            key = (
                self.model.get_secret(id=str(secret_id))
                .get_content(refresh=True)
                .get("api-key", "")
            )
        except ops.ModelError:
            raise ConfigurationError(
                "Cannot read model-secret; check the secret and its grant"
            ) from None
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,}", key):
            raise ConfigurationError("model-secret must contain a valid api-key")
        models = {}
        for name in ("embedding-model", "extraction-model"):
            value = str(self.config[name]).strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", value):
                raise ConfigurationError(f"Set {name} to a valid OpenRouter model ID")
            models[name.replace("-", "_")] = value
        dimension = int(self.config["embedding-dimension"])
        if not 1 <= dimension <= 65536:
            raise ConfigurationError("embedding-dimension must be between 1 and 65536")
        return {"model_key": key, "dimension": dimension, **models}

    def _publish(self, relation, identity: str, api: OpenVikingAPI, records: dict, peer):
        account = account_for(identity)
        record = records.get(str(relation.id))
        if record and record["context-id"] != identity:
            raise RelationError("Remove the context relation before changing its context-id")
        if any(
            key != str(relation.id) and value["context-id"] == identity
            for key, value in records.items()
        ):
            raise RelationError("A context-id can belong to only one active relation")
        key = api.ensure_client(account)
        label = "openviking-client-" + account
        try:
            secret = self.model.get_secret(label=label)
            if secret.peek_content() != {"api-key": key}:
                secret.set_content({"api-key": key})
        except ops.SecretNotFoundError:
            secret = self.app.add_secret({"api-key": key}, label=label)
        secret.grant(relation)
        records[str(relation.id)] = {
            "context-id": identity,
            "account-id": account,
            "secret-id": secret.id,
        }
        peer.data[self.app]["clients"] = json.dumps(records)
        address = str(self.model.get_binding(relation).network.ingress_address)
        host = f"[{address}]" if ":" in address else address
        relation.data[self.app].update(
            {
                "schema-version": "1",
                "endpoint": f"http://{host}:1933",
                "credentials": secret.id,
                "context-id": identity,
                "account-id": account,
                "user-id": "hermes",
                "server-version": VERSION,
            }
        )

    def _reconcile(self, event):
        if self.app.planned_units() != 1:
            self.workload.stop()
            self.unit.status = ops.BlockedStatus(
                "This charm supports one unit; reduce application scale to 1"
            )
            return
        if not self.unit.is_leader():
            self.unit.status = ops.WaitingStatus("Waiting for leadership")
            return
        try:
            if not self.workload.installed():
                self.unit.status = ops.MaintenanceStatus(f"Installing OpenViking {VERSION}")
                self.workload.install()
            self.unit.set_workload_version(VERSION)
            settings = self._settings()
            records, peer = self._records()
            # Bind all interfaces inside the private development machine, including loopback
            # used by the charm's administrative client. Juju does not expose the application.
            self.workload.configure(
                bind_address="0.0.0.0",
                root_key=self._root_secret().peek_content()["api-key"],
                **settings,
            )
            if not self.workload.healthy(attempts=15):
                self.unit.status = ops.WaitingStatus("Waiting for OpenViking health check")
                return
            api = self._api()
            # Also retry disconnections after a failed relation-broken hook or an outage.
            live_ids = {str(r.id) for r in self.model.relations["context"]}
            for relation_id in list(records):
                if relation_id not in live_ids:
                    self._retire(relation_id, records, peer, api)
            waiting = False
            relation_error = None
            for relation in self.model.relations["context"]:
                if relation.app is None:
                    continue
                request = relation.data[relation.app]
                identity = request.get("context-id", "")
                if not identity:
                    waiting = True
                    continue
                if request.get("schema-version") != "1" or not re.fullmatch(
                    IDENTITY_PATTERN, identity
                ):
                    relation_error = "Context relation has an invalid identity or schema version"
                    continue
                try:
                    self._publish(relation, identity, api, records, peer)
                except RelationError as exc:
                    relation_error = str(exc)
            if relation_error:
                self.unit.status = ops.BlockedStatus(relation_error)
                return
            self.unit.status = (
                ops.WaitingStatus("Waiting for consumer context identity")
                if waiting
                else ops.ActiveStatus(f"Context server ready; {len(records)} clients")
            )
        except PeerPending as exc:
            self.unit.status = ops.WaitingStatus(str(exc))
        except ConfigurationError as exc:
            self.workload.stop()
            self.unit.status = ops.BlockedStatus(str(exc))
        except ValueError:
            self.unit.status = ops.BlockedStatus(
                "OpenViking configuration is incompatible with stored state"
            )
        except APIError as exc:
            self.unit.status = ops.WaitingStatus(str(exc))
        except (OSError, subprocess.SubprocessError, ops.ModelError):
            logger.exception("OpenViking reconciliation failed")
            self.unit.status = ops.BlockedStatus(
                "OpenViking reconciliation failed; inspect juju debug-log"
            )

    def _retire(self, relation_id, records, peer, api):
        record = records[relation_id]
        # Rotate rather than delete: deleting an OpenViking user also deletes their data.
        key = api.rotate(record["account-id"])
        self.model.get_secret(id=record["secret-id"]).set_content({"api-key": key})
        del records[relation_id]
        peer.data[self.app]["clients"] = json.dumps(records)

    def _broken(self, event):
        if not self.unit.is_leader():
            return
        try:
            records, peer = self._records()
            record = records.get(str(event.relation.id))
            if not record:
                return
            self._retire(str(event.relation.id), records, peer, self._api())
            self.model.get_secret(id=record["secret-id"]).revoke(event.relation)
            self.unit.status = ops.ActiveStatus(f"Context server ready; {len(records)} clients")
        except (APIError, ops.ModelError, ConfigurationError):
            event.defer()
            self.unit.status = ops.WaitingStatus(
                "Waiting to revoke disconnected client's credential"
            )

    def _rotate(self, event):
        if not self.unit.is_leader():
            event.fail("Run this action on the leader")
            return
        try:
            records, _ = self._records()
            record = records[str(event.params["relation-id"])]
            key = self._api().rotate(record["account-id"])
            self.model.get_secret(id=record["secret-id"]).set_content({"api-key": key})
            event.set_results({"context-id": record["context-id"], "result": "Client key rotated"})
        except (KeyError, APIError, ops.ModelError, ConfigurationError):
            event.fail("Could not rotate client key; check relation-id and server health")

    def _check(self, event):
        if not self.unit.is_leader():
            event.fail("Run this action on the leader")
            return
        try:
            records, _ = self._records()
            if not self.workload.healthy():
                raise APIError()
            api = self._api()
            for record in records.values():
                api.check_client(
                    self.model.get_secret(id=record["secret-id"]).peek_content()["api-key"]
                )
            event.set_results({"clients": len(records), "server-version": VERSION})
        except (APIError, ops.ModelError, ConfigurationError):
            event.fail("Context access check failed; inspect server health and client credentials")

    def _stop(self, event):
        self.workload.stop()


if __name__ == "__main__":
    ops.main(OpenVikingCharm)
