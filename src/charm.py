#!/usr/bin/env python3
"""A single-unit, systemd-managed Hermes Agent machine charm."""

import logging
import re
import secrets
import subprocess

import ops

from workload import API_URL, VERSION, HermesWorkload

logger = logging.getLogger(__name__)
API_SECRET_LABEL = "hermes-api"


class ConfigurationError(Exception):
    """A configuration problem safe to display in Juju status."""


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
        ):
            self.framework.observe(event, self._reconcile)
        self.framework.observe(self.on.stop, self._stop)
        self.framework.observe(self.on.get_api_access_action, self._get_api_access)
        self.framework.observe(self.on.restart_action, self._restart)

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

    def _api_secret(self) -> ops.Secret:
        try:
            return self.model.get_secret(label=API_SECRET_LABEL)
        except ops.SecretNotFoundError:
            return self.unit.add_secret(
                {"api-key": secrets.token_urlsafe(48)},
                label=API_SECRET_LABEL,
                description="Bearer token for this unit's Hermes HTTP API",
            )

    def _reconcile(self, event, *, force_restart: bool = False) -> None:
        try:
            if not self.workload.installed():
                self.unit.status = ops.MaintenanceStatus(f"Installing Hermes {VERSION}")
                self.workload.install()
            self.unit.set_workload_version(VERSION)
            model, max_turns, provider_key = self._settings()
            api_key = self._api_secret().get_content()["api-key"]
            self.workload.configure(
                model=model,
                max_turns=max_turns,
                provider_key=provider_key,
                api_key=api_key,
                force_restart=force_restart,
            )
            # The HTTP check establishes gateway liveness, not provider authentication.
            if self.workload.healthy(attempts=10):
                self.unit.status = ops.ActiveStatus(f"Gateway ready; {model}")
            else:
                self.unit.status = ops.WaitingStatus("Waiting for Hermes API health check")
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


if __name__ == "__main__":
    ops.main(HermesCharm)
