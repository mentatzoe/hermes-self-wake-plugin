# Install and Use Guide

Step-by-step install for a fresh Hermes setup.

## Prerequisites

- Hermes Agent installed (`curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash`)
- The self-wake plugin repo accessible (private Git repo)
- Git available on the host

## 1. Apply the core patch (or use a Hermes version that includes it)

The plugin requires the Hermes host capability `internal_session_wake_v1`.

**Option A — apply the patch to an existing Hermes install:**

```bash
cd $HERMES_HOME/hermes-agent
git apply /path/to/plugin/docs/core-patch/0001-internal-session-wake-v1.patch
# Run core tests
scripts/run_tests.sh tests/
```

**Option B — wait for upstream:**

If you are running vanilla upstream Hermes without the patch, the plugin will install and load in `inspect_only` mode. Wake-mutating operations will fail closed with `capability_missing` until the host capability is present.

See `docs/compatibility.md` for the full version story.

## 2. Install the plugin

```bash
hermes plugins install git@github.com:mentatzoe/hermes-self-wake-plugin.git --enable
```

This clones the repo into `~/.hermes/plugins/self-wake/` and registers it.

## 3. Enable the plugin

```bash
hermes plugins enable self-wake
```

## 4. Add `self_wake` to platform toolsets

Edit `~/.hermes/config.yaml` and add `self_wake` to the toolsets list for each platform where you want the tools available:

```yaml
platform_toolsets:
  default:
    - web
    - terminal
    - file
    - self_wake
  discord:
    - web
    - terminal
    - file
    - self_wake
  telegram:
    - web
    - terminal
    - file
    - self_wake
```

If you skip this step, the tools will not be exposed to sessions and `/self-wake` will not be available.

## 5. Restart Hermes / gateway

Plugin registration and toolset changes require a restart:

- **CLI:** exit and relaunch `hermes`
- **Gateway:** `hermes gateway restart` or `/restart` in a gateway session

## 6. Verify

Run diagnostics:

```
/self-wake doctor
```

Expected output in `full` mode:
- `mode: full`
- `core_capability: ok`
- `session_index: ok` (non-zero sessions)
- `receipt_table: ok`
- `kanban_db: ok`

If any check shows `fail`, see `docs/operator-runbook.md` for troubleshooting.

## 7. Resolve a target session

```
/self-wake sessions --platform discord --chat-id <channel_id>
```

Or by query:

```
/self-wake sessions --query "kanban worker"
```

The output includes `session_key`, `session_id`, `platform`, `chat_id`, `thread_id`, and `origin`.

## 8. Subscribe a Kanban task to internal wake

Always use `--dry-run` first:

```
/self-wake subscribe --task-id t_abc123 --session-key "agent:default:discord:thread:..." --dry-run
```

If the dry-run looks correct, run without `--dry-run`:

```
/self-wake subscribe --task-id t_abc123 --session-key "agent:default:discord:thread:..."
```

The subscription writes `kanban_notify_subs.user_id = session:<session_key>` so that terminal events wake the target session internally.

## 9. Verify via receipts

After a terminal event fires:

```
/self-wake receipts --source-kind kanban --status agent_responded
```

## Upgrade

To upgrade the plugin:

```bash
cd ~/.hermes/plugins/self-wake
git pull
hermes plugins enable self-wake
# Restart Hermes / gateway
```

## Uninstall

```bash
hermes plugins disable self-wake
# Remove from platform_toolsets in config.yaml
# Restart Hermes / gateway
```

Disabling the plugin stops operator tools/hooks/commands but does not remove the core capability or existing subscriptions.
