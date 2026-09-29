# Hermes ↔ OpenViking development checks

Build each charm in its directory and deploy into a disposable/private model.
Configure OpenRouter secrets and relate the applications as described in their
READMEs. Both should reach active before testing. These commands use the
development application `hermes-context`; substitute your own unit/model.

The pinned workload source is
`/opt/hermes/hermes-agent-345cd2b057a452236de401d3534b8502a7465e8d`.
Copy `context_smoke.py` onto the Hermes unit as `/opt/hermes/context-smoke.py`,
then run it as `hermes` with that source's `.venv/bin/python`:

```sh
juju scp -m hermes:hermes-dev tests/integration/context_smoke.py \
  hermes-context/0:/tmp/context-smoke.py
juju exec -m hermes:hermes-dev --unit hermes-context/0 -- \
  install -m 755 /tmp/context-smoke.py /opt/hermes/context-smoke.py

juju exec -m hermes:hermes-dev --unit hermes-context/0 -- \
  runuser -u hermes -- \
  /opt/hermes/hermes-agent-345cd2b057a452236de401d3534b8502a7465e8d/.venv/bin/python \
  /opt/hermes/context-smoke.py remember \
  --report /var/lib/hermes/workspace/context-smoke.json
```

Repeat the last command with `verify`, `chat`, and `export` in place of
`remember`. `remember`, `verify` and `chat` incur OpenRouter usage. `remember`
submits one synthetic project fact through Hermes's actual memory plugin;
`verify` waits for the returned extraction task and searches/reads it using a
fresh plugin session. `chat` makes a real GLM-5.3 request through the gateway,
requiring the agent to recall that code using OpenViking tools. The question
does not contain the answer, and the script rejects terminal/file/delegation
tools in that conversation. The native local memory files are not seeded.

`export` checks that the regular user can stream an archive of its own memories
containing the fact. Every mode also checks that the user cannot list accounts
or take a full-server backup. This export test does not quiesce active sessions
and is not a production backup implementation. The synthetic fact and test
conversations are retained for repeatable persistence checks; avoid running
`remember` again unless you want another fact.

Then exercise lifecycle changes, repeating `verify` after each completed change:

1. Restart Hermes using its `restart` action.
2. Find the provider's context relation ID with `juju show-unit openviking/0`,
   and run `rotate-client-key relation-id=<id>`. Wait for both units to settle.
   For this assertion, copy `credential_smoke.py` onto the Hermes unit and start
   it as `hermes` with `--replacement` before running the action. It holds the
   former key only in memory, waits for its 401 response, then checks the new key.
3. Remove only the context relation. Verify Hermes's configuration no longer
   selects OpenViking and the previous key fails; retain the OpenViking application.
   Use `credential_smoke.py` without `--replacement` to monitor this revocation.
4. Reintegrate using the same `context-id` and verify the fact is still searchable.
5. For a replacement test, disconnect, create a fresh Hermes application with
   the same `context-id`, grant its OpenRouter secret, and relate it. Copy only
   the non-secret test report to its workspace; repeat `verify`/`chat`.
6. Run `isolation_smoke.py` as `openviking` with its installed Python on the
   provider, passing `--primary-account` from relation data and `--uri` from the
   recall report. It checks a primary-account read, a 404 for another account,
   and a 403 for admin access, then deletes only its own temporary account.

Provider restart persistence can be checked with a systemd restart followed by
`check-context` and `verify`. Machine loss and backup/restore remain outside
this milestone. Record actual outcomes and limitations in
[validation.md](../../docs/validation.md).
