#!/usr/bin/env python3
"""A single-unit, systemd-managed Hermes Agent machine charm."""

import logging
import re
import secrets
import subprocess
import uuid

import ops

from context import ContextConnection
from github_app import GitHubApp
from workload import API_URL, VERSION, HermesWorkload

logger = logging.getLogger(__name__)
API_SECRET_LABEL = "hermes-api"


class ConfigurationError(Exception):
    """A configuration problem safe to display in Juju status."""


class ContextPending(Exception):
    """The chosen context provider has not published its connection yet."""


class HermesCharm(ops.CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        self.workload = HermesWorkload()
        for event in (
            self.on.install,
            self.on.start,
            self.on.config_changed,
            self.on.upgrade_charm,
            self.on.secret_changed,
            self.on.secret_remove,
            self.on.secret_expired,
            self.on.leader_elected,
            self.on.update_status,
            self.on.peers_relation_joined,
            self.on.peers_relation_departed,
            self.on.context_store_relation_joined,
            self.on.context_store_relation_changed,
            self.on.context_store_relation_broken,
        ):
            self.framework.observe(event, self._reconcile)
        self.framework.observe(self.on.stop, self._stop)
        self.framework.observe(self.on.get_api_access_action, self._get_api_access)
        self.framework.observe(self.on.restart_action, self._restart)
        self.framework.observe(self.on.check_github_app_action, self._check_github_app)
        self.framework.observe(self.on.check_context_action, self._check_context)

    def _settings(self) -> tuple[str, int, str]:
        if self.app.planned_units() > 1:
            raise ConfigurationError("This charm supports one unit; reduce application scale to 1")
        model = str(self.config["model"]).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", model):
            raise ConfigurationError("Set model to a valid OpenRouter model ID")
        max_turns = int(self.config["max-turns"])
        if not 1 <= max_turns <= 1000:
            raise ConfigurationError("max-turns must be between 1 and 1000")
        secret_id = self.config.get("openrouter-secret")
        if not secret_id:
            raise ConfigurationError("Set openrouter-secret to a Juju secret containing api-key")
        try:
            content = self.model.get_secret(id=str(secret_id)).get_content(refresh=True)
        except ops.ModelError as exc:
            raise ConfigurationError(
                "Cannot read openrouter-secret; check the secret and its grant"
            ) from exc
        key = content.get("api-key", "")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,}", key):
            raise ConfigurationError("openrouter-secret must contain a valid non-empty api-key")
        return model, max_turns, key

    def _github_app(self) -> GitHubApp | None:
        secret_id = self.config.get("github-app-secret")
        if not secret_id:
            return None
        try:
            content = self.model.get_secret(id=str(secret_id)).get_content(refresh=True)
        except ops.ModelError as exc:
            raise ConfigurationError(
                "Cannot read github-app-secret; check the secret and its grant"
            ) from exc
        try:
            return GitHubApp.from_secret(
                str(self.config["github-app-id"]).strip(),
                str(self.config["github-installation-id"]).strip(),
                content.get("private-key", ""),
            )
        except ValueError as exc:
            raise ConfigurationError(str(exc)) from None

    def _api_secret(self) -> ops.Secret:
        try:
            return self.model.get_secret(label=API_SECRET_LABEL)
        except ops.SecretNotFoundError:
            return self.unit.add_secret(
                {"api-key": secrets.token_urlsafe(48)},
                label=API_SECRET_LABEL,
                description="Bearer token for this unit's Hermes HTTP API",
            )

    def _context_id(self) -> str:
        identity = str(self.config["context-id"]).strip() or str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"juju:{self.model.uuid}/{self.app.name}")
        )
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", identity):
            raise ConfigurationError(
                "context-id must contain 1-63 lowercase letters, digits or hyphens"
            )
        return identity

    def _context(self, event=None) -> ContextConnection | None:
        identity = self._context_id()
        relations = [
            relation
            for relation in self.model.relations["context-store"]
            if not isinstance(event, ops.RelationBrokenEvent) or relation.id != event.relation.id
        ]
        if not relations:
            return None
        relation = relations[0]
        if self.unit.is_leader():
            previous = relation.data[self.app].get("context-id")
            if previous and previous != identity:
                raise ConfigurationError(
                    "Remove the context-store relation before changing context-id"
                )
            relation.data[self.app].update({"schema-version": "1", "context-id": identity})
        data = relation.data[relation.app] if relation.app else {}
        if not data.get("endpoint") or not data.get("credentials"):
            raise ContextPending("Waiting for OpenViking connection and client credential")
        try:
            content = self.model.get_secret(id=data["credentials"]).get_content(refresh=True)
        except ops.ModelError:
            raise ContextPending("Waiting for the OpenViking credential grant") from None
        try:
            return ContextConnection.from_relation(data, content.get("api-key", ""), identity)
        except ValueError as exc:
            raise ConfigurationError(str(exc)) from None

    def _reconcile(self, event, *, force_restart: bool = False) -> None:
        try:
            if not self.workload.installed():
                self.unit.status = ops.MaintenanceStatus(f"Installing Hermes {VERSION}")
                self.workload.install()
            self.unit.set_workload_version(VERSION)
            model, max_turns, provider_key = self._settings()
            github_app = self._github_app()
            context = self._context(event)
            api_key = self._api_secret().get_content()["api-key"]
            self.workload.configure(
                model=model,
                max_turns=max_turns,
                provider_key=provider_key,
                api_key=api_key,
                github_app=github_app,
                context=context,
                force_restart=force_restart,
            )
            # The HTTP check establishes gateway liveness, not provider authentication.
            if self.workload.healthy(attempts=10):
                if context is not None and not context.available():
                    self.unit.status = ops.WaitingStatus(
                        "Gateway ready; OpenViking access unavailable"
                    )
                else:
                    self.unit.status = ops.ActiveStatus(f"Gateway ready; {model}")
            else:
                self.unit.status = ops.WaitingStatus("Waiting for Hermes API health check")
        except ContextPending as exc:
            self.unit.status = ops.WaitingStatus(str(exc))
        except ConfigurationError as exc:
            self.workload.stop()
            self.unit.status = ops.BlockedStatus(str(exc))
        except (OSError, ValueError, subprocess.SubprocessError, ops.ModelError):
            # Don't put exception text or credential-bearing command output in unit status.
            logger.exception("Hermes installation or reconciliation failed")
            self.unit.status = ops.BlockedStatus(
                "Hermes reconciliation failed; inspect juju debug-log"
            )

    def _stop(self, event) -> None:
        self.workload.stop()

    def _get_api_access(self, event: ops.ActionEvent) -> None:
        secret = self._api_secret()
        event.set_results({"url": API_URL, "secret-id": secret.id, "model": self.config["model"]})

    def _restart(self, event: ops.ActionEvent) -> None:
        self._reconcile(event, force_restart=True)
        if not isinstance(self.unit.status, ops.ActiveStatus):
            event.fail(self.unit.status.message)
        else:
            event.set_results({"result": "Hermes restarted"})

    def _check_github_app(self, event: ops.ActionEvent) -> None:
        if not self.config.get("github-app-secret"):
            event.fail("Set github-app-secret before checking GitHub App authentication")
            return
        self._reconcile(event)
        if not isinstance(self.unit.status, ops.ActiveStatus):
            event.fail(self.unit.status.message)
            return
        try:
            count = self.workload.check_github_app()
        except (OSError, ValueError, subprocess.SubprocessError):
            event.fail("GitHub App authentication failed; check the key, installation, and network")
            return
        event.set_results(
            {
                "app-id": self.config["github-app-id"],
                "installation-id": self.config["github-installation-id"],
                "repositories": count,
            }
        )

    def _check_context(self, event: ops.ActionEvent) -> None:
        try:
            connection = self._context()
            if connection is None:
                event.fail("Relate an OpenViking context store first")
            elif not connection.available():
                event.fail("OpenViking access check failed")
            else:
                event.set_results(
                    {"context-id": connection.identity, "endpoint": connection.endpoint}
                )
        except (ConfigurationError, ContextPending) as exc:
            event.fail(str(exc))


if __name__ == "__main__":
    ops.main(HermesCharm)
