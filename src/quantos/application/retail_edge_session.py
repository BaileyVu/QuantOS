"""Bounded public transport orchestration around the replayable retail processor."""
import asyncio
from datetime import datetime, timezone
import time

from quantos.application.hft_trader import _clock_calibration
from quantos.application.retail_replay import RetailCausalProcessor
from quantos.application.retail_trader import RetailPaperEngine
from quantos.domain.market_data.hft import DepthDelta, HftBookError
from quantos.domain.market_data.hft_clock import HftClockMonitor


class RecoverableRetailFeedError(RuntimeError):
    """A failure raised only at a public transport or book-rebuild boundary."""


async def run_edge_session(*, config, rules, account, evaluation, policy, store,
                           identity, duration_seconds, client, stream_factory,
                           external_factories, journal, emit, kill_requested, reclaim=False, reliability=False,
                           feed_errors=(TimeoutError, ConnectionError)):
    if not 1 <= duration_seconds <= (43200 if reliability else 1800):
        raise ValueError("public session duration exceeds selected mode limit")
    engine = RetailPaperEngine(config, rules, account, evaluation, policy, unified=True, reclaim=reclaim, reliability=reliability)
    processor = RetailCausalProcessor(engine)
    monitor = HftClockMonitor(config.clock_negative_latency_tolerance_ms,
        config.clock_excessive_negative_limit, config.clock_recalibration_interval)
    deadline = time.monotonic() + duration_seconds
    reconnects = 0
    shutdown = "duration_expired"
    succeeded = False
    external_queue = asyncio.Queue(maxsize=4096)

    def dispatch(action, **arguments):
        # Record after processing so actual submission-time clock samples are
        # included. Any write failure immediately halts and preserves state;
        # captures without a terminal record are never accepted as complete.
        try:
            result = processor.apply(action, arguments, clock_ns=time.monotonic_ns if action == "event" else None)
        except HftBookError as error:
            raise RecoverableRetailFeedError("invalid market book; reconstruction required") from error
        journal.append("command", action=action, arguments=arguments)
        if reclaim and processor.last_decisions:
            from quantos.application.retail_replay import stable_json
            import json
            journal.append("decisions", records=json.loads(stable_json(processor.last_decisions)))
        if result and result.get("event") != "intelligence_ingested":
            emit(result)
        return result

    def remaining(limit):
        return max(.001, min(limit, deadline - time.monotonic()))

    def checkpoint():
        report = processor.report()
        store.save(identity, account, {"metrics": report, "reconnects": reconnects,
                   "updated_at": datetime.now(timezone.utc), "shutdown_reason": shutdown})
        emit({"event": "retail_status", "metrics": report, "reconnects": reconnects,
              "depth_age_ms": None if engine.book.last_received_monotonic_ns is None else
              (time.monotonic_ns() - engine.book.last_received_monotonic_ns) // 1_000_000})

    async def feed_call(awaitable, timeout):
        try:
            return await asyncio.wait_for(awaitable, timeout)
        except feed_errors as error:
            if getattr(error, "retryable", True) is False:
                raise
            raise RecoverableRetailFeedError("public transport failure") from error

    async def calibrate():
        calibration = await feed_call(_clock_calibration(client,
            config.clock_calibration_samples, config.clock_low_rtt_samples,
            lambda: datetime.now(timezone.utc), time.monotonic),
            timeout=remaining(config.clock_calibration_timeout.total_seconds()))
        monitor.apply_calibration(calibration)
        payload = {"event": "retail_clock_calibrated", "health": monitor.health.value,
                   **calibration.payload(),
                   "clock_uncertainty_bound_ms": calibration.selected_rtt_ms / 2}
        if reliability:
            journal.append("clock_calibration", **payload)
        emit(payload)

    async def external_worker(provider, source_kind, factory):
        while time.monotonic() < deadline:
            try:
                async for event in factory().events():
                    external_queue.put_nowait(("external", {"event": event}))
            except asyncio.CancelledError:
                raise
            except feed_errors as error:
                # A provider outage never makes up data or stops unrelated feeds.
                external_queue.put_nowait(("source_failure", {"provider": provider,
                    "source_kind": source_kind, "reason": f"{type(error).__name__}:{error}"}))
            await asyncio.sleep(remaining(2))

    workers = [asyncio.create_task(external_worker(*entry)) for entry in external_factories]
    try:
        while time.monotonic() < deadline:
            if kill_requested():
                dispatch("tick", processing_ns=time.monotonic_ns(), timestamp=datetime.now(timezone.utc), kill=True)
                shutdown = "operator_kill"
                break
            queue = asyncio.Queue(maxsize=4096)
            stream = stream_factory()

            async def produce():
                try:
                    async for event in stream.events(rules.symbol):
                        queue.put_nowait(event)
                except feed_errors as error:
                    raise RecoverableRetailFeedError("public stream disconnected") from error

            producer = asyncio.create_task(produce())
            try:
                if monitor.calibration is None:
                    await calibrate()
                await feed_call(stream.wait_public_ready(), remaining(config.bootstrap_timeout.total_seconds()))
                await feed_call(stream.wait_market_ready(), remaining(config.bootstrap_timeout.total_seconds()))
                snapshot = await feed_call(asyncio.to_thread(client.depth_snapshot, rules.symbol), remaining(config.snapshot_timeout.total_seconds()))
                buffered = []
                bridge_deadline = time.monotonic() + remaining(config.bootstrap_timeout.total_seconds())
                while time.monotonic() < bridge_deadline:
                    event = await feed_call(queue.get(), max(.001, bridge_deadline - time.monotonic()))
                    if isinstance(event, DepthDelta):
                        buffered.append(event)
                        if event.first_update_id <= snapshot.last_update_id <= event.final_update_id:
                            break
                else:
                    raise RecoverableRetailFeedError("snapshot bridge timeout")
                dispatch("bootstrap", snapshot=snapshot, buffered=buffered)
                emit({"event": "retail_book_ready", "symbol": rules.symbol, "update_id": engine.book.last_update_id})
                last_checkpoint = time.monotonic()
                while time.monotonic() < deadline:
                    if kill_requested():
                        dispatch("tick", processing_ns=time.monotonic_ns(), timestamp=datetime.now(timezone.utc), kill=True)
                        shutdown = "operator_kill"
                        break
                    if producer.done():
                        producer.result()
                        raise RecoverableRetailFeedError("public depth stream ended")
                    for worker in workers:
                        if worker.done():
                            worker.result()
                            raise RuntimeError("intelligence worker stopped unexpectedly")
                    if monitor.calibration_due(time.monotonic()):
                        dispatch("disarm", reason="clock_recalibration", timestamp=datetime.now(timezone.utc))
                        await calibrate()
                        dispatch("resume")
                    # Merge by actual availability to this processor. Never sort by source time.
                    while not external_queue.empty():
                        action, args = external_queue.get_nowait()
                        dispatch(action, **args)
                    try:
                        event = await asyncio.wait_for(queue.get(), remaining(.5))
                    except TimeoutError:
                        dispatch("tick", processing_ns=time.monotonic_ns(), timestamp=datetime.now(timezone.utc), kill=False)
                    else:
                        latency = monitor.observe(event.exchange_time, event.received_at)
                        healthy = monitor.quoting_allowed and latency.corrected_signed_latency_ms >= -config.clock_negative_latency_tolerance_ms
                        received_processing_ns = time.monotonic_ns()
                        if reliability and (latency.observed_feed_latency_ms > policy.maximum_event_age_ms
                                            or received_processing_ns - event.received_monotonic_ns > policy.maximum_event_age_ms * 1_000_000):
                            emit({"event": "retail_timing_rejection",
                                  "exchange_event_at": event.exchange_time, "received_at": event.received_at,
                                  "processing_ns": received_processing_ns,
                                  "queue_delay_ms": (received_processing_ns - event.received_monotonic_ns) / 1_000_000,
                                  "raw_feed_latency_ms": latency.raw_uncalibrated_latency_ms,
                                  "corrected_feed_latency_ms": latency.corrected_signed_latency_ms,
                                  "clock_health": monitor.health.value,
                                  "clock_offset_ms": monitor.calibration.offset_ms,
                                  "clock_uncertainty_bound_ms": monitor.calibration.selected_rtt_ms / 2})
                        dispatch("event", event=event, processing_ns=received_processing_ns,
                                 feed_latency_ms=latency.observed_feed_latency_ms, clock_healthy=healthy)
                    if engine.disarmed:
                        raise RecoverableRetailFeedError(f"engine disarmed: {engine.last_disarm_reason}; fresh reconstruction required")
                    if time.monotonic() - last_checkpoint >= 5:
                        checkpoint()
                        last_checkpoint = time.monotonic()
                if shutdown == "operator_kill":
                    break
            except RecoverableRetailFeedError as error:
                reconnects += 1
                dispatch("disarm", reason="feed_or_bootstrap_failure", timestamp=datetime.now(timezone.utc))
                emit({"event": "retail_reconnect", "reason": f"{type(error).__name__}:{error}"})
            finally:
                producer.cancel()
                outcomes = await asyncio.gather(producer, return_exceptions=True)
                for outcome in outcomes:
                    if isinstance(outcome, BaseException) and not isinstance(outcome, (asyncio.CancelledError, RecoverableRetailFeedError)):
                        raise outcome
            if time.monotonic() < deadline:
                await asyncio.sleep(remaining(.5))
        # Observe background failures before committing a successful terminal.
        for worker in workers:
            worker.cancel()
        outcomes = await asyncio.gather(*workers, return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError):
                raise outcome
        # Only a normal duration/controlled stop reaches the terminal commit.
        dispatch("disarm", reason="runtime_shutdown", timestamp=datetime.now(timezone.utc))
        checkpoint()  # validates and durably reconciles account before completion
        report = processor.report()
        journal.append("complete", report=report, shutdown_reason=shutdown, reconnects=reconnects)
        succeeded = True
    except BaseException as error:
        shutdown = f"runtime_failure:{type(error).__name__}:{error}"
        raise
    finally:
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        if not succeeded:
            # Preserve the in-memory ledger when possible; never invent replay
            # commands after an unrecorded event or label an aborted run complete.
            try:
                engine.disarm("runtime_shutdown", datetime.now(timezone.utc))
                checkpoint()
            except Exception:
                pass  # the managed unresolved-session marker remains authoritative
            try:
                journal.append("aborted", shutdown_reason=shutdown, reconnects=reconnects)
            except Exception:
                pass  # missing terminal evidence is itself an incomplete capture
    return {**report, "shutdown_reason": shutdown, "reconnects": reconnects}
