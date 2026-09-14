# Native Kanban return repair

This source change repairs the plugin's handling of ordinary native
`delivery_mode=wake` and `notify+wake` subscriptions. It is not evidence that a
running gateway loaded the change. Legacy `session:` / `session_id:` marker
and cron paths retain their existing dispatch behavior.

## What changes

Native subscriptions resolve the parent from the subscription's platform,
chat type, chat, thread, user and alternate user, `notifier_profile`, and scope
in `delivery_metadata`. Persisted `scope_id`, `slack_team_id` or `team_id` wins;
`adapter.scope_id_for_chat` supplies the fallback. The host's canonical key
builder and profile-aware adapter selector remain authoritative. Worker
`task.session_id` is never a parent destination.

A native wake requires an existing session whose stored route matches the
subscription. Each attempt pins that current session ID and key and passes
`gateway_session_strict` metadata. Native subscriptions follow a logical parent
key between attempts; a reset or origin change during admission rejects the
attempt. Unexpected post-admission session movement is not reported as delivery
to the originally selected session.

Native receipt claims run in one SQLite write transaction. A plugin-owned
`self_wake_claims` table supplies per-attempt tokens. Only the owning token can
transition its receipt. A recent request is pending, not successful deduplication;
a failed or stale pre-dispatch request may be reclaimed. The last token check
happens before final admission. A process lost after marking `dispatching`
leaves an uncertain outcome, not automatic permission to inject again.

`native_wake.py` uses an exact-source-gated admission adapter for Hermes commit
`dcbf5b71bc65fc6f7168c601bc04e84b22f912cd`. It validates the selected route,
current session, adapter ownership, draining state and busy guard without an
async gap before the host's `_start_session_processing`. That method starts the
ordinary runner handler; it does not bypass the handler's authorization. Native
wakes skip the lossy in-memory busy queue and retry from the board when idle.
The private method digests in `HOST_METHODS` are a compatibility dependency,
not a claim that arbitrary Hermes versions or custom adapters are supported.

An exact persisted user row with `platform_message_id=internal-wake:<receipt>`
in the selected session's message store establishes injection. A normal adapter
return, a matching payload without that ID, and unrelated user/assistant rows do
not. The native path deliberately does not infer `agent_responded` from a later
assistant row. The local smoke separately checks its deterministic response.

## Receipt and retry meanings

| Native status | Meaning | Board action |
|---|---|---|
| `requested` | One token owns a pre-admission attempt | Keep event pending |
| `dispatching` | Admission may be in progress; not a delivery receipt | Keep pending |
| `failure` | Rejected before the final host admission call | Retry |
| `pending` | Post-admission result is uncertain or lacks exact persistence evidence | Reconcile; do not blindly inject again |
| `dispatched` | Exact persisted wake row found in the target session | Advance cursor |
| `deduped` result | This native receipt already has confirmed injection evidence | Advance cursor without another injection |

Caller cancellation does not cancel an admitted turn. The runner retains a
strongly referenced settlement task; replay can inspect the durable receipt.
If the process exits before that task settles, a later attempt looks for the
exact user row. An absent row after ambiguous admission remains pending and
needs inspection of that receipt, the selected session and the host's turn
outcome. Age alone cannot prove safe retry.

Native collection reads unseen events without advancing the board cursor.
This closes the claim-before-delivery cancellation gap. Successful events are
acknowledged individually, monotonically, and only for the same subscription
creation timestamp. Completed rows remain subscribed. Archived task rows are
removed even if no later delivery event exists. Passive/legacy rows retain the
host-style preclaim and rewind contract; their fixture now actually advances
on claim, so its rewind assertion is meaningful.

A partial-native host with an incompatible `wake_session` signature retains
native events instead of acknowledging a failed call. The older Kanban/cron
capability doctor remains structural: `full` is not proof that this exact native
admission path ran. The live receipt and fresh integration gates below remain
required.

## Verification and activation

Portable unit tests cover the four original review findings, host drift,
uncertain admission, caller cancellation, payload conflicts, route mismatch,
profile message-store selection, and monotonic cursor acknowledgement:

    python -m pytest -q

The current-host cron test is independent. On the September host it detects a
cron fingerprint outside that adapter's existing manifest. Do not change or
force the cron manifest to make this repair's test report green.

The primitive probe uses real SessionStore, AsyncSessionDB and base-adapter
processing with local transport and a deterministic responder. It is not a
production message-handler or board-consumer test:

    python scripts/current_host_wake_smoke.py --host-checkout /path/to/hermes
    python scripts/current_host_wake_smoke.py --host-checkout /path/to/hermes --busy

The board probe also uses the real temporary Kanban board and notification
consumer. Its `busy` case requires retained cursor, zero queue/send/receive,
then exact idle consumption and unchanged replay. Run it from an authorized
parent/operator context. A delegated-child Kanban mutation guard refusal is a
probe blocker; leave the guard enabled:

    python scripts/current_host_kanban_smoke.py --host-checkout /path/to/hermes --case completed
    python scripts/current_host_kanban_smoke.py --host-checkout /path/to/hermes --case blocked
    python scripts/current_host_kanban_smoke.py --host-checkout /path/to/hermes --case crashed
    python scripts/current_host_kanban_smoke.py --host-checkout /path/to/hermes --case busy
    python scripts/current_host_kanban_smoke.py --host-checkout /path/to/hermes --case legacy
    python scripts/current_host_kanban_smoke.py --host-checkout /path/to/hermes --case notify

The crash case emits an explicitly synthetic crashed event, not a real worker
crash. Both scripts use temporary homes and local responders. Neither proves
live controller continuation, Discord delivery, inference, or preservation of
other running profiles by observation.

Independent exact-head source review and CI gate source merge. The operator
owns installation and any gateway restart. After adoption, commission one
labelled completion/failure/blocked/busy canary against the actual controller
parent; read its exact receipt, consumed session and controller outcome before
calling the native return fixed. The existing GitHub-result and two-hour
recovery routes remain the mitigation until that gate passes.
