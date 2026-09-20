# Resume the intended conversation

Version 1.4.0 keeps native background-process delivery, preserves the native Kanban repair, and adds a durable same-profile bridge for cron results and explicit messages between sessions. Home owns installation and live checks. Only Zoe issues `/restart`.

## Supported host and limits

The current integration targets Hermes `dcbf5b71bc65fc6f7168c601bc04e84b22f912cd`. The plugin checks the actual private methods, not a version label. It changes no host source files. The old cron adapter remains available only for its separately pinned older host.

The clean released-host probes do not establish support for arbitrary current releases: `v2026.9.14` refuses the changed Kanban notifier, and `v2026.8.31` loads the plugin but differs at native admission and cron delivery. Do not force these fingerprints. The supported current host is a specific checkout, not a promise that upgrading Hermes preserves compatibility.

Cron and explicit-message inboxes support one profile per gateway. Named single-profile gateways keep separate state. Multiplexed gateways report this inbox as unsupported and do not dispatch its requests; the existing Kanban path remains separate. A sender must use the receiving gateway's exact `HERMES_HOME`.

| Path | Owner | What counts as success |
|---|---|---|
| Terminal background completion | Native Hermes process watcher and completion routing | The intended parent consumes the result and acts; native adapter acceptance alone is weaker evidence |
| Kanban terminal completion | Native subscriptions/SQLite plus the existing plugin admission repair | Exact injected user row; an owned final reply can link an assistant row |
| Cron completion | Real successful origin text delivery, then plugin durable inbox and native admission | A `cron_delivery` receipt for the exact existing session, followed by its response/action |
| Explicit session message | Plugin CLI, profile-local inbox, native admission | The request links to a `session_message` receipt; the receiving session responds/acts |

Ordinary `hermes send` remains a visible platform send, not a wake. Use `hermes self-wake send` when another session must act. The public plugin injector lacks an expected session ID and a durable consumption acknowledgment; this bridge reuses the existing stricter admission adapter rather than pretending its enqueue boolean is a receipt.

Cron wakes only confirmed, nonempty text delivered through the running gateway to an existing origin route. Telegram channel-DM topics retain their topic identity; genuine thread fallback uses the actual unthreaded route. Media-only, timed-out, standalone, relay, unrelated fanout and newly created continuation-thread deliveries do not cause guessed wakes. Doctor includes the last observed skip reason. Keep those cases separate from a failed visible send.

## Install the reviewed source

Use the exact full source commit from the delivery handoff, not a moving branch. Keep the checkout and JSON receipts outside a disposable worker directory. The script stages committed files, checks Python syntax, preserves the old plugin directory, swaps the package, verifies its hashes and restores the predecessor if the swap fails. It does not edit config, enable the plugin, restart Hermes or modify the host checkout.

```bash
# SOURCE is a persistent clone of mentatzoe/hermes-self-wake-plugin.
# SOURCE_COMMIT is the full reviewed commit from the task handoff.
# PROFILE_HOME is Home's actual Hermes home; HOST is its installed host checkout.
git -C "$SOURCE" fetch origin
python3 "$SOURCE/scripts/install_plugin.py" \
  --source "$SOURCE" --commit "$SOURCE_COMMIT" --home "$PROFILE_HOME"

# Home applies only after checking the dry-run target and hashes.
python3 "$SOURCE/scripts/install_plugin.py" \
  --source "$SOURCE" --commit "$SOURCE_COMMIT" --home "$PROFILE_HOME" --apply \
  > "$PROFILE_HOME/self-wake-install-receipt.json"
```

Merge these keys into the existing config; preserve all other settings. Do not replace the enabled-plugin or platform-toolset lists.

```yaml
self_wake:
  compat_shim_enabled: true
cron:
  wake_agent_on_delivery: true
plugins:
  entries:
    self-wake:
      allow_gateway_injection: true
```

`hermes plugins enable self-wake` handles the enabled list. The grant permits trusted processes sharing that profile home to request an internal turn. The receiver rechecks both the grant and current user authorization before retrying. Keep the home directory private. No network endpoint or cross-profile grant exists.

After Zoe's `/restart`, run `/self-wake doctor` inside Home. Require separate healthy Kanban, cron-delivery and session-message results. The session-message check must say its receiver consumer is running. A CLI doctor has no live gateway and cannot prove adoption. Disk hashes and a plugin-load report also do not prove adoption.

## Run the isolated checks

Use the host's interpreter with pytest installed. Every script creates a temporary home and replaces network/model I/O with local substitutes. None restarts a gateway, calls a model, changes a production board or proves live adoption.

```bash
"$HOST/venv/bin/python" "$SOURCE/scripts/current_host_paths_smoke.py" --host-checkout "$HOST"
"$HOST/venv/bin/python" "$SOURCE/scripts/current_host_wake_smoke.py" --host-checkout "$HOST" --busy
"$HOST/venv/bin/python" "$SOURCE/scripts/current_host_background_smoke.py" --host-checkout "$HOST"
for CASE in completed blocked crashed busy legacy notify; do
  "$HOST/venv/bin/python" "$SOURCE/scripts/current_host_kanban_smoke.py" \
    --host-checkout "$HOST" --case "$CASE" || break
done
```

The board-consumer script uses the real Kanban API. A delegated-child guard may refuse it even with a temporary board. Preserve that guard and run the script from Home's permitted parent context; never clear guard environment variables. The wake-transport script and unit tests do not substitute for a successful board-consumer run.

The paths smoke uses the real scheduler, SQLite routing index, real Hermes plugin CLI discovery in a separate process, real adapter admission and receipt persistence. It checks duplicate requests, a busy receiver and an untouched wrong-target session.

## Live acceptance in Home

First resolve Home's current session with `self_wake_sessions`; record its exact session ID and key. Keep one unique probe token per event and save receipt/message IDs plus the visible action. Do not reuse historical successful receipts as current proof.

1. From Home, run a harmless bounded terminal command with `background=true, notify=true`, for example `python3 -c 'print("wake-check TOKEN")'`. Do not consume it with wait/log first. Require Home's continuation to quote the token and record the result. Keep the native process handle separate from the conversation ID. Repeat with a nonzero exit.
2. Create a harmless Kanban task from the permitted parent context, subscribe it to Home with native `delivery_mode=wake`, and complete it. Repeat for a blocked task and a deliberately failing isolated worker. While Home is busy, require the cursor to remain pending until admission; then require one exact injected event and no duplicate turn. The earlier accepted successful case remains valid historical evidence, not proof of these new cases.
3. Create a one-shot, script-only cron from Home with the existing Home origin route and explicit `deliver=origin`. Have it print a unique token and an instruction to record that token on receipt. Require successful platform delivery, a `cron_delivery` receipt with the intended target ID and Home's follow-through. Repeat while Home is busy. A visible cron post alone is not completion.
4. From a separate same-profile session, write a file containing `Self-wake acceptance TOKEN: record this token in your response and state which conversation received it.` Then execute:

```bash
HERMES_HOME="$PROFILE_HOME" hermes self-wake send \
  --session-id "$HOME_SESSION_ID" --dedupe-key "$TOKEN" --file "$MESSAGE_FILE"
HERMES_HOME="$PROFILE_HOME" hermes self-wake status "$REQUEST_ID"
```

The first response returns `request_id` and initially `queued`. Read the request later and inspect `/self-wake receipts --source-kind session_message`. Require Home to act, not just a request row. Retry the identical send with the same key: it must return the same request and cause no second turn. A different payload with that key must fail. Repeat with Home busy; it must eventually receive the original event without clearing any guard.

A nonexistent session ID fails before submission. A reset or route change after submission marks the request `target_changed`; it never follows the new conversation automatically. Current authorization failures remain queued for a permitted retry. Uncertain post-admission outcomes remain pending and do not re-inject blindly.

`dispatched` proves an exact persisted wake user row. `agent_responded` additionally requires a unique persisted final assistant row matching the owned handler's returned or streamed result. A delivery mirror is not an agent response. Missing, altered, silent or ambiguous final text stays `dispatched`; inspect Home's actual continuation before claiming follow-through. Restart recovery can restore the exact injection link without inventing a response link.

## Retained and retired behavior

The native terminal watcher, native Kanban subscriptions, prior cursor/receipt protections, authorization and approval guards remain. Current cron no longer depends on the obsolete delivery fingerprint or treats the mirror callback alone as proof. SQLite routing takes precedence over a stale optional JSON mirror. Ordinary platform sends remain unchanged. The old core patch remains retired; no upstream patch is required.

Rollback: restore the predecessor path recorded in the installer receipt to `plugins/self-wake`, restore the previous config values if Home changed them, and let Zoe restart. Keep the durable inbox and receipt DBs for diagnosis. Do not delete queued requests to make doctor appear healthy.
