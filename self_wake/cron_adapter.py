"""Plugin-owned compatibility adapter for cron-delivery session wake.

Hermes 21895bd39d does not expose a public post-delivery hook. This module
therefore installs two wrappers transactionally at plugin registration time:
an outer ``cron.scheduler._deliver_result`` ContextVar scope and the host's
per-target post-success ``_maybe_mirror_cron_delivery`` seam. Each helper call
uses the route that actually succeeded (including user/thread mutation), even
when mirroring itself is disabled or later fanout aggregation fails. Wake still
requires ``cron.wake_agent_on_delivery=true`` and an existing target session,
and dispatches through ``GatewayRunner.wake_session``. Every stable private
callable is source-pinned; live runner attributes are adoption-validated without
digesting runtime object identity. Host drift fails closed before mutation.
"""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import inspect
import logging
import uuid
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

ADAPTER_CAPABILITY = "cron_delivery_wake_v1"
SUPPORTED_HOSTS = {
    # Exact inspect.getsource(_deliver_result) identity on the dispatched
    # current-host checkout.  The commit is operator-facing provenance; the
    # source digest is the runtime adoption gate and works for installed source
    # trees that do not retain .git metadata.
    "dda5f6f956ec1a6069d2f227ca4d2ba012cfc72600947c82cfca5b468051140f": {
        "commit": "21895bd39d9bc8a1cda307c9b0a0eb6fc98a8844",
        "function": "cron.scheduler._deliver_result",
    },
}
SUPPORTED_PRIVATE_SEAMS = {
    "cron.scheduler._maybe_mirror_cron_delivery": {
        "6f51d0efb37d1613380fece7e2c7ac7d1035d54825e4c8b45bb2dd843ba16f6c"
    },
    "cron.scheduler.load_config": {
        "8680b219e570cf6db520ee21c4a043ad29a1cec19525162395e0c4dd15b54dbb"
    },
    "gateway.session.SessionStore.list_sessions": {
        "1ce4bdb06b080936723a6beaf13eadc76734d1eceb154d033138da322362d5fb"
    },
}

_original_delivery: Optional[Callable[..., Any]] = None
_original_mirror: Optional[Callable[..., Any]] = None
_installed_delivery_wrapper: Optional[Callable[..., Any]] = None
_installed_mirror_wrapper: Optional[Callable[..., Any]] = None
_installed_module: Any = None
_delivery_context: contextvars.ContextVar[Optional[dict[str, Any]]] = contextvars.ContextVar(
    "self_wake_cron_delivery", default=None
)
_install_report: dict[str, Any] = {
    "installed": False,
    "reason": "not_attempted",
}


def _function_digest(fn: Callable[..., Any]) -> str:
    source = inspect.getsource(fn).encode("utf-8")
    return hashlib.sha256(source).hexdigest()


def _load_scheduler_module():
    from cron import scheduler  # type: ignore

    return scheduler


def probe(*, module=None) -> dict[str, Any]:
    """Report whether cron delivery is actively wired to internal wake."""
    try:
        scheduler = module or _load_scheduler_module()
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "source": "absent",
            "reason": f"cron.scheduler not importable: {exc}",
        }
    fn = getattr(scheduler, "_deliver_result", None)
    if not callable(fn):
        return {
            "available": False,
            "source": "absent",
            "reason": "cron.scheduler._deliver_result missing",
        }
    mirror = getattr(scheduler, "_maybe_mirror_cron_delivery", None)
    outer_owned = bool(getattr(fn, "_self_wake_cron_delivery_v1", False))
    mirror_owned = bool(
        callable(mirror) and getattr(mirror, "_self_wake_cron_target_v2", False)
    )
    if outer_owned != mirror_owned:
        if outer_owned:
            return {
                "available": False,
                "source": "absent",
                "reason": (
                    "plugin outer wrapper present but owned "
                    "cron.scheduler._maybe_mirror_cron_delivery wrapper absent"
                ),
            }
        return {
            "available": False,
            "source": "absent",
            "reason": (
                "plugin target wrapper present but owned "
                "cron.scheduler._deliver_result wrapper absent"
            ),
        }
    if outer_owned and mirror_owned:
        if not callable(getattr(scheduler, "load_config", None)):
            return {
                "available": False,
                "source": "absent",
                "reason": "cron.scheduler.load_config missing",
            }
        prerequisites = _operational_prerequisites()
        if not prerequisites.get("ok"):
            return {
                "available": False,
                "source": "absent",
                "reason": str(prerequisites.get("reason") or "wake prerequisites unavailable"),
                "prerequisites": prerequisites,
            }
        return {
            "available": True,
            "source": "shim",
            "capability": ADAPTER_CAPABILITY,
            "wrappers": {
                "cron.scheduler._deliver_result": True,
                "cron.scheduler._maybe_mirror_cron_delivery": True,
            },
            "prerequisites": prerequisites,
        }
    # Native/reference-patch detection is structural and conservative.  A host
    # must both expose the scheduler helper and call it from the delivery body.
    native_helper = getattr(scheduler, "_schedule_cron_delivery_wake", None)
    try:
        source = inspect.getsource(fn)
    except (OSError, TypeError):
        source = ""
    if (
        callable(native_helper)
        and "_schedule_cron_delivery_wake" in source
        and "wake_on_delivery" in source
    ):
        return {
            "available": True,
            "source": "native",
            "capability": ADAPTER_CAPABILITY,
        }
    reason = "plugin cron adapter not adopted by this process"
    report = _install_report or {}
    if report.get("reason") == "host_drift":
        reason = str(report.get("detail") or "host_drift")
    return {"available": False, "source": "absent", "reason": reason}


def check_compatibility(*, module=None) -> dict[str, Any]:
    """Fail-closed exact source-shape preflight for the private host seam."""
    remediation = (
        "Install a self-wake plugin version matching this Hermes host, enable "
        "self_wake.compat_shim_enabled, and restart the gateway. Do not force "
        "the adapter across host drift."
    )
    try:
        scheduler = module or _load_scheduler_module()
    except Exception as exc:  # noqa: BLE001
        return {
            "compatible": False,
            "status": "host_drift",
            "detail": f"host_drift: cannot import cron.scheduler: {exc}",
            "remediation": remediation,
        }
    try:
        fn = getattr(scheduler, "_deliver_result")
        digest = _function_digest(fn)
    except Exception as exc:  # noqa: BLE001
        return {
            "compatible": False,
            "status": "host_drift",
            "detail": f"host_drift: cannot inspect cron.scheduler._deliver_result: {exc}",
            "remediation": remediation,
        }
    supported = SUPPORTED_HOSTS.get(digest)
    if supported is None:
        return {
            "compatible": False,
            "status": "host_drift",
            "detail": (
                "host_drift: cron.scheduler._deliver_result sha256="
                f"{digest} is not in the plugin compatibility manifest"
            ),
            "observed_sha256": digest,
            "supported_sha256": sorted(SUPPORTED_HOSTS),
            "remediation": remediation,
        }
    seam_callables: dict[str, Any] = {
        "cron.scheduler._maybe_mirror_cron_delivery": getattr(
            scheduler, "_maybe_mirror_cron_delivery", None
        ),
        "cron.scheduler.load_config": getattr(scheduler, "load_config", None),
    }
    # Name missing scheduler-local seams before attempting sibling imports so a
    # closed failure always identifies the direct absent production dependency.
    for seam_name, seam_fn in seam_callables.items():
        if not callable(seam_fn):
            return {
                "compatible": False,
                "status": "host_drift",
                "detail": f"host_drift: {seam_name} missing or not callable",
                "remediation": remediation,
            }
    try:
        from gateway.session import SessionStore  # type: ignore

        seam_callables["gateway.session.SessionStore.list_sessions"] = getattr(
            SessionStore, "list_sessions", None
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "compatible": False,
            "status": "host_drift",
            "detail": (
                "host_drift: cannot inspect "
                f"gateway.session.SessionStore.list_sessions: {exc}"
            ),
            "remediation": remediation,
        }
    observed_seams: dict[str, str] = {}
    for seam_name, seam_fn in seam_callables.items():
        if not callable(seam_fn):
            return {
                "compatible": False,
                "status": "host_drift",
                "detail": f"host_drift: {seam_name} missing or not callable",
                "remediation": remediation,
            }
        try:
            seam_digest = _function_digest(seam_fn)
        except Exception as exc:  # noqa: BLE001
            return {
                "compatible": False,
                "status": "host_drift",
                "detail": f"host_drift: cannot inspect {seam_name}: {exc}",
                "remediation": remediation,
            }
        observed_seams[seam_name] = seam_digest
        supported_digests = SUPPORTED_PRIVATE_SEAMS.get(seam_name, set())
        if seam_digest not in supported_digests:
            return {
                "compatible": False,
                "status": "host_drift",
                "detail": (
                    f"host_drift: {seam_name} sha256={seam_digest} is not in "
                    "the plugin compatibility manifest"
                ),
                "observed_sha256": seam_digest,
                "supported_sha256": sorted(supported_digests),
                "remediation": remediation,
            }

    return {
        "compatible": True,
        "status": "ready",
        "observed_sha256": digest,
        "observed_private_seams": observed_seams,
        "supported_host_commit": supported["commit"],
        "function": supported["function"],
    }


def _operational_prerequisites() -> dict[str, Any]:
    """Validate live adoption prerequisites without pinning object identity."""
    targets: dict[str, bool] = {}
    try:
        from gateway.run import GatewayRunner, _gateway_runner_ref  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "reason": f"gateway.run wake prerequisites not importable: {exc}",
            "targets": targets,
        }
    targets["gateway.run._gateway_runner_ref"] = callable(_gateway_runner_ref)
    if not targets["gateway.run._gateway_runner_ref"]:
        return {
            "ok": False,
            "reason": "gateway.run._gateway_runner_ref missing or not callable",
            "targets": targets,
        }
    class_wake = getattr(GatewayRunner, "wake_session", None)
    targets["gateway.run.GatewayRunner.wake_session"] = callable(class_wake)
    try:
        runner = _gateway_runner_ref()
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "reason": f"gateway.run._gateway_runner_ref call failed: {exc}",
            "targets": targets,
        }
    targets["active_gateway_runner"] = runner is not None
    if runner is None:
        return {
            "ok": False,
            "reason": "active GatewayRunner unavailable",
            "targets": targets,
        }
    runner_wake = getattr(runner, "wake_session", None)
    targets["active_gateway_runner.wake_session"] = callable(runner_wake)
    store = getattr(runner, "session_store", None)
    targets["active_gateway_runner.session_store.list_sessions"] = callable(
        getattr(store, "list_sessions", None)
    )
    for name in (
        "active_gateway_runner.wake_session",
        "active_gateway_runner.session_store.list_sessions",
    ):
        if not targets[name]:
            return {"ok": False, "reason": f"{name} missing", "targets": targets}
    assert runner_wake is not None
    try:
        params = inspect.signature(runner_wake).parameters
    except (TypeError, ValueError) as exc:
        return {
            "ok": False,
            "reason": f"active GatewayRunner.wake_session signature unavailable: {exc}",
            "targets": targets,
        }
    required = {"payload", "source_kind", "session_key", "session_id", "dedupe_key"}
    missing_params = sorted(required - set(params))
    if missing_params:
        return {
            "ok": False,
            "reason": f"active GatewayRunner.wake_session missing params: {missing_params}",
            "targets": targets,
        }
    return {"ok": True, "reason": "", "targets": targets}


def _active_gateway_runner():
    try:
        from gateway.run import _gateway_runner_ref  # type: ignore

        return _gateway_runner_ref()
    except Exception:
        return None


def _blankish(value: Any) -> str:
    return str(value or "")


def _platform_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").lower()


def _entry_matches_target(entry: Any, target: dict[str, Any]) -> bool:
    origin = getattr(entry, "origin", None)
    if origin is None:
        return False
    return (
        _platform_value(getattr(origin, "platform", ""))
        == str(target.get("platform") or "").lower()
        and _blankish(getattr(origin, "chat_id", ""))
        == _blankish(target.get("chat_id"))
        and _blankish(getattr(origin, "thread_id", ""))
        == _blankish(target.get("thread_id"))
        and _blankish(getattr(origin, "user_id", ""))
        == _blankish(target.get("user_id"))
    )


def _origin_matches_target(origin: dict[str, Any], target: dict[str, Any]) -> bool:
    return (
        str(origin.get("platform") or "").lower()
        == str(target.get("platform") or "").lower()
        and _blankish(origin.get("chat_id")) == _blankish(target.get("chat_id"))
        and _blankish(origin.get("thread_id")) == _blankish(target.get("thread_id"))
        and _blankish(origin.get("user_id")) == _blankish(target.get("user_id"))
    )


def _resolve_wake_target(
    job: dict[str, Any], target: dict[str, Any], runner: Any
) -> Optional[dict[str, str]]:
    """Resolve only an existing session; never synthesize a wake identity."""
    del job  # Persisted origin keys are hints, never trusted routing authority.
    store = getattr(runner, "session_store", None)
    if store is None:
        return None
    try:
        entries = list(store.list_sessions())
    except TypeError:
        try:
            entries = list(store.list_sessions(active_minutes=None))
        except Exception:
            entries = []
    except Exception:
        entries = []
    matches: list[dict[str, str]] = []
    for entry in entries:
        if not _entry_matches_target(entry, target):
            continue
        session_key = str(getattr(entry, "session_key", "") or "").strip()
        if session_key:
            matches.append({"session_key": session_key})
            continue
        session_id = str(getattr(entry, "session_id", "") or "").strip()
        if session_id:
            matches.append({"session_id": session_id})
    if len(matches) != 1:
        return None
    return matches[0]


def _schedule_wake(
    *,
    job: dict[str, Any],
    target: dict[str, Any],
    content: str,
    runner: Any,
    loop: Any,
    fire_identity: Optional[str] = None,
) -> dict[str, Any]:
    if runner is None:
        return {"status": "skipped", "reason": "gateway runner unavailable"}
    wake_fn = getattr(runner, "wake_session", None)
    if not callable(wake_fn):
        return {"status": "skipped", "reason": "wake_session unavailable"}
    if loop is None or not getattr(loop, "is_running", lambda: False)():
        return {"status": "skipped", "reason": "gateway loop unavailable"}
    wake_target = _resolve_wake_target(job, target, runner)
    if not wake_target:
        return {"status": "skipped", "reason": "target existing session unavailable"}

    platform = str(target.get("platform") or "").lower()
    chat_id = str(target.get("chat_id") or "")
    thread_id = str(target.get("thread_id") or "")
    job_id = str(job.get("id") or "")
    job_name = str(job.get("name") or job_id or "cron job")
    fire_identity = (
        str(fire_identity or "").strip()
        or str(job.get("execution_id") or "").strip()
        or uuid.uuid4().hex
    )
    material = "\0".join(
        [job_id, fire_identity, platform, chat_id, thread_id, content]
    )
    digest = hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()
    payload = (
        f"Internal wake from cron delivery: cron job '{job_name}' (id: {job_id}) "
        f"delivered output into this existing {platform} session. Consume the "
        "delivered cron result and continue if follow-through is needed.\n\n"
        f"Delivered content:\n{content}"
    )
    kwargs = {
        **wake_target,
        "payload": payload,
        "source_kind": "cron_delivery",
        "dedupe_key": (
            f"cron_delivery:{job_id}:{fire_identity}:{platform}:{chat_id}:"
            f"{thread_id}:{digest}"
        ),
    }
    coro = wake_fn(**kwargs)
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        task = loop.create_task(coro)
        task.add_done_callback(_log_background_result)
        return {"status": "queued", "reason": "scheduled on gateway loop"}
    try:
        future = asyncio.run_coroutine_threadsafe(coro, loop)
    except Exception as exc:  # noqa: BLE001
        # Scheduling never transferred ownership, so this is the only safe path
        # where the just-created coroutine may be closed locally.
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        return {"status": "failure", "error": f"{type(exc).__name__}: {exc}"}
    try:
        result = future.result(timeout=60)
        return result if isinstance(result, dict) else {"status": "unknown", "result": result}
    except TimeoutError:
        # ``run_coroutine_threadsafe`` returns a concurrent Future whose
        # ``cancel()`` may succeed even after its coroutine has started. Never
        # cancel at this boundary: wake_session may already have written a
        # receipt or injected the event. Leave the loop-owned work running and
        # observe its eventual result without changing visible-delivery status.
        future.add_done_callback(_log_background_result)
        return {
            "status": "in_flight",
            "reason": "wake dispatched and still running after confirmation timeout",
        }
    except Exception as exc:  # noqa: BLE001
        # Once scheduling succeeds, the future/event loop owns the coroutine.
        # Never close it here; report a non-raising optional-wake failure.
        return {"status": "failure", "error": f"{type(exc).__name__}: {exc}"}


def _log_background_result(task: Any) -> None:
    try:
        result = task.result()
        if isinstance(result, dict) and result.get("status") == "failure":
            logger.warning("cron delivery background wake failed: %s", result.get("error"))
    except Exception:  # noqa: BLE001
        logger.warning("cron delivery background wake raised", exc_info=True)


def _wake_enabled(scheduler: Any) -> bool:
    try:
        config = scheduler.load_config() or {}
        cron = config.get("cron", {}) if isinstance(config, dict) else {}
        return bool(cron.get("wake_agent_on_delivery", False))
    except Exception:
        return False


def _log_wake_outcome(job: dict[str, Any], target: dict[str, Any], result: dict[str, Any]) -> None:
    status = str(result.get("status") or "")
    if status == "failure":
        logger.warning(
            "Job '%s': cron delivery wake failed for %s:%s (%s)",
            job.get("id", "?"), target.get("platform"), target.get("chat_id"),
            result.get("error") or "unknown error",
        )
    elif status == "skipped":
        logger.info(
            "Job '%s': cron delivery wake skipped for %s:%s (%s)",
            job.get("id", "?"), target.get("platform"), target.get("chat_id"),
            result.get("reason") or "unknown reason",
        )
    else:
        logger.info(
            "Job '%s': cron delivery wake status=%s receipt=%s for %s:%s",
            job.get("id", "?"), status, result.get("receipt_id"),
            target.get("platform"), target.get("chat_id"),
        )


def _make_mirror_wrapper(original: Callable[..., Any], scheduler: Any):
    """Observe the host's per-target post-success helper without changing it."""
    del scheduler

    def _wrapped(
        job: dict,
        platform_name: str,
        chat_id: str,
        mirror_text: str,
        thread_id=None,
        user_id=None,
        *,
        enabled: bool = False,
    ):
        # Preserve the host mirror exactly, including enabled=False no-op and its
        # best-effort failure policy. Wake observation happens only afterwards.
        result = original(
            job, platform_name, chat_id, mirror_text,
            thread_id=thread_id, user_id=user_id, enabled=enabled,
        )
        context = _delivery_context.get()
        if not context or not context.get("wake_enabled") or context.get("job") is not job:
            return result
        target = {
            "platform": str(platform_name),
            "chat_id": str(chat_id),
            "thread_id": thread_id,
            "user_id": user_id,
        }
        # The host only forwards origin.user_id to this helper when transcript
        # mirroring is enabled. Cron wake is an independent opt-in, so recover
        # the per-user identity from the job origin only when the reported
        # successful route is exactly that origin route. Never copy it onto a
        # fan-out target merely because user_id is absent.
        if user_id is None:
            origin_value = job.get("origin")
            origin = origin_value if isinstance(origin_value, dict) else {}
            origin_route_matches = (
                str(origin.get("platform") or "").lower()
                == str(platform_name or "").lower()
                and _blankish(origin.get("chat_id")) == _blankish(chat_id)
                and _blankish(origin.get("thread_id")) == _blankish(thread_id)
            )
            if origin_route_matches and origin.get("user_id") is not None:
                target["user_id"] = origin.get("user_id")
        # Current host 21895bd39d reports a requested thread to this helper even
        # when DeliveryRouter's raw response says it flattened to the channel.
        # The exact outer-function digest pins these local names and branch
        # semantics. Correct the observed wake route from that raw response;
        # never claim/wake the stale requested thread.
        frame = inspect.currentframe()
        try:
            caller_locals = frame.f_back.f_locals if frame and frame.f_back else {}
            raw_response = caller_locals.get("send_raw_response")
            confirmed_live_delivery = (
                caller_locals.get("delivered") is True
                and caller_locals.get("timed_out") is False
            )
            if (
                confirmed_live_delivery
                and isinstance(raw_response, dict)
                and raw_response.get("thread_fallback")
                and thread_id is not None
                and str(raw_response.get("requested_thread_id") or thread_id)
                == str(thread_id)
            ):
                target["thread_id"] = None
        finally:
            del frame
        try:
            wake_result = _schedule_wake(
                job=job,
                target=target,
                content=str(context.get("content") or ""),
                runner=_active_gateway_runner(),
                loop=context.get("loop"),
                fire_identity=str(context.get("fire_identity") or ""),
            )
            _log_wake_outcome(job, target, wake_result)
        except Exception as exc:  # noqa: BLE001
            # Optional wake can never retroactively fail a successful visible
            # delivery or escape through the host's mirror helper.
            logger.warning(
                "Job '%s': cron delivery wake observer raised for %s:%s: %s",
                job.get("id", "?"), platform_name, chat_id, exc,
            )
        return result

    _wrapped.__name__ = getattr(original, "__name__", "_maybe_mirror_cron_delivery")
    _wrapped.__doc__ = getattr(original, "__doc__", None)
    setattr(_wrapped, "_self_wake_cron_target_v2", True)
    setattr(_wrapped, "_self_wake_original", original)
    return _wrapped


def _make_delivery_wrapper(original: Callable[..., Any], scheduler: Any):
    """Carry one delivery call's content/fire identity to per-target success."""
    def _wrapped(job: dict, content: str, adapters=None, loop=None):
        fire_identity = (
            str(job.get("execution_id") or "").strip() or f"call-{uuid.uuid4().hex}"
        )
        token = _delivery_context.set({
            "job": job,
            "content": content,
            "loop": loop,
            "fire_identity": fire_identity,
            "wake_enabled": _wake_enabled(scheduler),
        })
        try:
            return original(job, content, adapters=adapters, loop=loop)
        finally:
            _delivery_context.reset(token)

    _wrapped.__name__ = getattr(original, "__name__", "_deliver_result")
    _wrapped.__doc__ = getattr(original, "__doc__", None)
    setattr(_wrapped, "_self_wake_cron_delivery_v1", True)
    setattr(_wrapped, "_self_wake_cron_outer_v2", True)
    setattr(_wrapped, "_self_wake_original", original)
    return _wrapped


def install(*, module=None, force: bool = False) -> dict[str, Any]:
    """Install both adapter wrappers as one transaction after preflight."""
    global _original_delivery, _original_mirror
    global _installed_delivery_wrapper, _installed_mirror_wrapper
    global _installed_module, _install_report
    try:
        scheduler = module or _load_scheduler_module()
    except Exception as exc:  # noqa: BLE001
        _install_report = {"installed": False, "reason": "host_drift", "detail": str(exc)}
        return dict(_install_report)

    active = probe(module=scheduler)
    if active["available"]:
        _install_report = {
            "installed": active["source"] == "shim",
            "reason": f"{active['source']}_cron_capability_present",
            "source": active["source"],
        }
        return dict(_install_report)

    compatibility = check_compatibility(module=scheduler)
    if not compatibility["compatible"] and not force:
        _install_report = {
            "installed": False,
            "reason": "host_drift",
            "detail": compatibility["detail"],
            "compatibility": compatibility,
        }
        return dict(_install_report)

    original_delivery = getattr(scheduler, "_deliver_result", None)
    original_mirror = getattr(scheduler, "_maybe_mirror_cron_delivery", None)
    if not callable(original_delivery) or not callable(original_mirror):
        missing = (
            "cron.scheduler._deliver_result" if not callable(original_delivery)
            else "cron.scheduler._maybe_mirror_cron_delivery"
        )
        _install_report = {
            "installed": False,
            "reason": "host_drift",
            "detail": f"{missing} missing",
        }
        return dict(_install_report)

    delivery_wrapper = _make_delivery_wrapper(original_delivery, scheduler)
    mirror_wrapper = _make_mirror_wrapper(original_mirror, scheduler)
    assigned: list[tuple[str, Any, Any]] = []
    try:
        setattr(scheduler, "_deliver_result", delivery_wrapper)
        assigned.append(("_deliver_result", delivery_wrapper, original_delivery))
        setattr(scheduler, "_maybe_mirror_cron_delivery", mirror_wrapper)
        assigned.append(("_maybe_mirror_cron_delivery", mirror_wrapper, original_mirror))
        # Plugin discovery runs inside GatewayRunner construction, before the
        # active runner weakref/session store exists. Prove only wrapper
        # ownership here; the public probe/doctor validates the live runner
        # prerequisites after startup. Rolling back at this point would make
        # the documented restart path permanently non-functional because
        # plugin discovery is idempotent later in startup.
        if getattr(scheduler, "_deliver_result", None) is not delivery_wrapper:
            raise RuntimeError("post-install adoption failed: _deliver_result wrapper absent")
        if getattr(scheduler, "_maybe_mirror_cron_delivery", None) is not mirror_wrapper:
            raise RuntimeError(
                "post-install adoption failed: _maybe_mirror_cron_delivery wrapper absent"
            )
    except Exception as exc:  # noqa: BLE001
        rollback_errors: list[str] = []
        for name, wrapper, original in reversed(assigned):
            try:
                if getattr(scheduler, name, None) is wrapper:
                    setattr(scheduler, name, original)
            except Exception as rollback_exc:  # noqa: BLE001
                rollback_errors.append(f"cron.scheduler.{name}: {rollback_exc}")
                logger.critical("cron adapter rollback failed for %s", name, exc_info=True)
        _install_report = {
            "installed": False,
            "reason": "install_failed",
            "detail": str(exc),
            "rollback_errors": rollback_errors,
        }
        return dict(_install_report)

    _original_delivery = original_delivery
    _original_mirror = original_mirror
    _installed_delivery_wrapper = delivery_wrapper
    _installed_mirror_wrapper = mirror_wrapper
    _installed_module = scheduler
    _install_report = {
        "installed": True,
        "reason": "cron_adapter_installed",
        "source": "shim",
        "compatibility": compatibility,
    }
    return dict(_install_report)


def uninstall() -> dict[str, Any]:
    """Restore only wrappers still owned by this plugin instance."""
    global _original_delivery, _original_mirror
    global _installed_delivery_wrapper, _installed_mirror_wrapper
    global _installed_module, _install_report
    if _installed_module is None:
        return {"uninstalled": False, "reason": "not_installed"}

    ownership_lost: list[str] = []
    restore_errors: list[str] = []
    surfaces = (
        ("_deliver_result", _installed_delivery_wrapper, _original_delivery),
        ("_maybe_mirror_cron_delivery", _installed_mirror_wrapper, _original_mirror),
    )
    for name, wrapper, original in surfaces:
        current = getattr(_installed_module, name, None)
        if current is not wrapper:
            ownership_lost.append(f"cron.scheduler.{name}")
            continue
        try:
            setattr(_installed_module, name, original)
        except Exception as exc:  # noqa: BLE001
            restore_errors.append(f"cron.scheduler.{name}: {exc}")

    _original_delivery = None
    _original_mirror = None
    _installed_delivery_wrapper = None
    _installed_mirror_wrapper = None
    _installed_module = None
    if restore_errors:
        _install_report = {
            "installed": False,
            "reason": "uninstall_failed",
            "restore_errors": restore_errors,
            "ownership_lost": ownership_lost,
        }
        return {"uninstalled": False, **_install_report}
    if ownership_lost:
        _install_report = {
            "installed": False,
            "reason": "ownership_lost",
            "ownership_lost": ownership_lost,
        }
        return {"uninstalled": False, **_install_report}
    _install_report = {"installed": False, "reason": "uninstalled"}
    return {"uninstalled": True}


def status() -> dict[str, Any]:
    return dict(_install_report)
