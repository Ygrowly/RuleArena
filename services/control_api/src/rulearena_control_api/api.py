from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from typing import Annotated, Protocol
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from rulearena_attack_runtime import (
    AttackStatus,
    Budget,
    CompileResult,
    CompileStatus,
    RuleCompiler,
    RuleVersionStore,
    RuntimeStore,
    VersionStore,
    validate_rule_spec,
)
from rulearena_evaluation import BenchmarkStore, public_metric_summary, scan_forbidden_markers
from rulearena_observability import TraceSink
from rulearena_policy_schema import RuleSpec


class CompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str
    chinese_modification: str


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed_rule_spec: RuleSpec | None = None


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_version_id: str
    scenario_version_id: str
    sandbox_version: str = "fixed"
    oracle_version: str = "1.0"
    budget: Budget
    random_seed: int = 0


class RunEnqueuer(Protocol):
    async def enqueue(self, run_id: str, rule_spec: RuleSpec) -> None: ...


class NullRunEnqueuer:
    async def enqueue(self, run_id: str, rule_spec: RuleSpec) -> None:
        return None


class PolicyService:
    def __init__(self, compiler: RuleCompiler, versions: VersionStore | None = None) -> None:
        self.compiler = compiler
        self.versions = versions or RuleVersionStore()

    async def compile(self, request: CompileRequest) -> tuple[str, CompileResult]:
        policy_id = str(uuid4())
        result = await self.compiler.compile(request.template_id, request.chinese_modification)
        self.versions.record_compile(policy_id, request.chinese_modification, result)
        return policy_id, result

    def confirm(self, policy_id: str, confirmed: RuleSpec | None) -> object:
        try:
            draft = self.versions.get_draft(policy_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="policy draft not found") from exc
        result = draft
        if confirmed is not None:
            if draft.status is not CompileStatus.NEEDS_CONFIRMATION:
                raise HTTPException(
                    status_code=409,
                    detail="confirmed_rule_spec is only accepted for an ambiguous draft",
                )
            if confirmed.ambiguities:
                raise HTTPException(status_code=409, detail="ambiguities remain unresolved")
            scenario = self.compiler.templates[draft.template_id]
            errors = validate_rule_spec(confirmed, scenario)
            if errors:
                raise HTTPException(status_code=422, detail=list(errors))
            result = CompileResult(
                status=CompileStatus.COMPILED,
                template_id=draft.template_id,
                rule_spec=confirmed,
                llm_call=draft.llm_call,
            )
        try:
            return self.versions.confirm(policy_id, result)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


BUILTIN_TEMPLATE_CATALOG: tuple[dict[str, str], ...] = (
    {
        "id": "promotion",
        "scenario_type": "PROMOTION",
        "label": "优惠券",
        "description": "满减券的门槛、折扣与退款恢复规则。",
        "example_modification": "满 150 元减 50 元，全额退款时不恢复优惠券。",
    },
    {
        "id": "refund-points",
        "scenario_type": "REFUND_POINTS",
        "label": "退款与积分",
        "description": "消费得积分、退款撤销积分与兑换规则。",
        "example_modification": "每消费 1 元获得 1 积分，退款时按退款金额撤销积分。",
    },
    {
        "id": "membership-entitlement",
        "scenario_type": "MEMBERSHIP_ENTITLEMENT",
        "label": "次数型会员权益",
        "description": "会员购买、权益消费与退款一致性规则。",
        "example_modification": "会员卡 50 元，含 2 次权益，仅未使用可退款。",
    },
)


class RunRateLimiter:
    """Fixed-window per-client limiter for the public live-run endpoint."""

    def __init__(self, limit: int = 10, window_seconds: int = 300) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with self._lock:
            window = self._hits[client]
            while window and now - window[0] > self.window_seconds:
                window.popleft()
            if len(window) >= self.limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="live run rate limit exceeded; explore the frozen demo instead",
                )
            window.append(now)


def runtime_router(
    policy_service: PolicyService,
    runtime_store: RuntimeStore,
    enqueuer: RunEnqueuer | None = None,
    benchmark_store: BenchmarkStore | None = None,
    trace_store: TraceSink | None = None,
    limiter: RunRateLimiter | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api")
    selected_enqueuer = enqueuer or NullRunEnqueuer()
    selected_limiter = limiter or RunRateLimiter()

    @router.post("/policies/compile")
    async def compile_policy(payload: CompileRequest) -> dict[str, object]:
        policy_id, result = await policy_service.compile(payload)
        return {"policy_id": policy_id, **result.model_dump(mode="json")}

    @router.post("/policies/{policy_id}/confirm")
    async def confirm_policy(policy_id: str, payload: ConfirmRequest) -> object:
        return policy_service.confirm(policy_id, payload.confirmed_rule_spec)

    @router.get("/policies/{version_id}")
    async def get_policy(version_id: str) -> object:
        try:
            return policy_service.versions.get(version_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="rule version not found") from exc

    @router.get("/templates")
    async def templates() -> object:
        return {"templates": BUILTIN_TEMPLATE_CATALOG}

    @router.post(
        "/runs",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(selected_limiter.check)],
    )
    async def create_run(
        payload: CreateRunRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> object:
        try:
            rule_version = policy_service.versions.get(payload.rule_version_id)
        except KeyError as exc:
            raise HTTPException(status_code=409, detail="rule version is not confirmed") from exc
        run = runtime_store.create_run(
            job_key=idempotency_key,
            rule_version_id=payload.rule_version_id,
            scenario_version_id=payload.scenario_version_id,
            sandbox_version=payload.sandbox_version,
            oracle_version=payload.oracle_version,
            budget=payload.budget,
            random_seed=payload.random_seed,
        )
        await selected_enqueuer.enqueue(run.run_id, rule_version.rule_spec)
        return run

    @router.get("/runs/{run_id}")
    async def get_run(run_id: str) -> object:
        try:
            return runtime_store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc

    @router.post("/runs/{run_id}/cancel")
    async def cancel_run(run_id: str) -> object:
        try:
            accepted = runtime_store.request_cancel(run_id)
            run = runtime_store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        return {"accepted": accepted, "run": run}

    @router.get("/runs/{run_id}/counterexamples")
    async def counterexamples(run_id: str) -> object:
        try:
            runtime_store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        return {"counterexamples": runtime_store.counterexamples(run_id)}

    @router.get("/runs/{run_id}/trace")
    async def trace(run_id: str) -> object:
        try:
            runtime_store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        records = trace_store.traces_for_run(run_id) if trace_store is not None else ()
        safe_records = []
        blocked = 0
        for record in records:
            if scan_forbidden_markers([record.model_dump(mode="json")]):
                blocked += 1
                continue
            safe_records.append(record)
        return {"trace": safe_records, "leakage_blocked": blocked}

    @router.get("/benchmarks/latest")
    async def latest_benchmark() -> object:
        benchmark = benchmark_store.latest_completed() if benchmark_store is not None else None
        if benchmark is None:
            raise HTTPException(status_code=404, detail="completed benchmark not found")
        return {
            "benchmark_run_id": benchmark.benchmark_run_id,
            "versions": benchmark.versions,
            "baseline": benchmark.baseline,
            "suite": benchmark.suite,
            "status": benchmark.status,
            "metrics": public_metric_summary(benchmark.metrics),
            "started_at": benchmark.started_at,
            "finished_at": benchmark.finished_at,
        }

    @router.get("/runs/{run_id}/events")
    async def events(
        request: Request,
        run_id: str,
        cursor: Annotated[int, Query(ge=0)] = 0,
        follow: bool = True,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> StreamingResponse:
        try:
            runtime_store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        if last_event_id is not None:
            try:
                cursor = max(cursor, int(last_event_id))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="invalid Last-Event-ID") from exc

        async def stream() -> AsyncIterator[str]:
            current_cursor = cursor
            while True:
                if await request.is_disconnected():
                    return
                values = runtime_store.events_after(run_id, current_cursor)
                for event in values:
                    if scan_forbidden_markers([event.data]):
                        yield (
                            f"id: {event.cursor}\nevent: LEAKAGE_BLOCKED\n"
                            "data: {}\n\n"
                        )
                    else:
                        data = json.dumps(
                            event.data, ensure_ascii=False, separators=(",", ":")
                        )
                        yield (
                            f"id: {event.cursor}\nevent: {event.event_type}\n"
                            f"data: {data}\n\n"
                        )
                    current_cursor = event.cursor
                run = runtime_store.get_run(run_id)
                if not follow or run.status in {
                    AttackStatus.COMPLETED,
                    AttackStatus.CANCELLED,
                    AttackStatus.FAILED,
                }:
                    return
                if not values:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return router
