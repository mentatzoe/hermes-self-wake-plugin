# Install and Use Guide

## Prerequisites

- Hermes Agent at a host version listed in `docs/compatibility.md`;
- Git and Python 3.10+;
- permission to restart the Hermes gateway from an outside shell.

## 1. Install and enable

```bash
hermes plugins install https://github.com/mentatzoe/hermes-self-wake-plugin.git --enable
```

For a local checkout/install smoke:

```bash
python -m venv /tmp/self-wake-venv
/tmp/self-wake-venv/bin/python -m pip install .
/tmp/self-wake-venv/bin/python -c 'import self_wake; print(self_wake.__version__)'
```

## 2. Configure the plugin-owned compatibility layer

```yaml
# ~/.hermes/config.yaml
self_wake:
  compat_shim_enabled: true

# Enable only if autonomous continuation after successful cron delivery is
# intended. Doctor fails if this is true but cron routing is not adopted.
cron:
  wake_agent_on_delivery: true
```

No Hermes source patch is applied. The plugin runtime applicator checks every
stable private host seam before transactionally installing both cron wrappers;
startup installation verifies wrapper ownership before the active runner exists.
After `GatewayRunner` construction, doctor requires the active runner, wake
signature, and session store without hashing runtime object identity.

Append `self_wake` to existing platform toolsets; do not replace existing
entries:

```yaml
platform_toolsets:
  default: [web, terminal, file, self_wake]
  discord: [web, terminal, file, self_wake]
```

The `/self-wake` command is registered independently of model toolsets.

## 3. Restart for adoption

Plugin installation/copying does not alter the already-running process.
Restart from an outside shell:

```bash
hermes gateway restart
```

Do not rely on a doctor result captured before restart.

## 4. Verify each surface

```text
/self-wake doctor
```

With cron wake enabled, require:

```text
ok: true
mode: full
kanban_wake_capability: ok ... source=shim|native
cron_delivery_capability: ok ... source=shim|native
cron_wake_config: ok ... wake_agent_on_delivery=true
```

`mode=full` alone is insufficient: it is the backward-compatible Kanban mode.
If cron is enabled and routing is absent, doctor must say `mode=degraded`,
`ok=false`, and identify not-adopted or host-drift remediation.
Unreadable/unparseable or absent cron policy plus absent routing is also
`mode=degraded`, `ok=false`; unknown policy is never reported as off.

## 5. Current-host smoke

From the plugin checkout:

```bash
python scripts/current_host_cron_smoke.py \
  --host-checkout /path/to/hermes-agent-at-21895bd39d
```

A successful JSON result proves both owned wrappers, real host scheduler delivery, internal wake,
durable `cron_delivery` receipt, and user/assistant rows in the target existing
session, using a disposable `HERMES_HOME` and local transport (no external send).

## 6. Subscribe Kanban

```text
/self-wake sessions --platform discord --chat-id <channel_id>
/self-wake subscribe --task-id t_abc123 --session-key "agent:default:discord:thread:..." --dry-run
/self-wake subscribe --task-id t_abc123 --session-key "agent:default:discord:thread:..."
```

## 7. Inspect receipts

```text
/self-wake receipts --source-kind kanban
/self-wake receipts --source-kind cron_delivery
```

`agent_responded` is the strongest outcome. `queued` means delivered to a busy
session; confirm its transcript when queued-finalization is unavailable.

## Upgrade

```bash
cd ~/.hermes/plugins/self-wake
git pull
hermes plugins enable self-wake
hermes gateway restart
```

Then rerun doctor and the harmless receipt-backed canary. A host update can
invalidate the exact adapter shape even when the plugin files survived.

## Rollback

```yaml
self_wake:
  compat_shim_enabled: false
cron:
  wake_agent_on_delivery: false
```

Then restart the gateway. Disabling the plugin does not delete existing Kanban
subscriptions or receipt rows; remove/upgrade subscriptions separately.

The old core patch under `docs/core-patch/` is retired and must not be applied.
