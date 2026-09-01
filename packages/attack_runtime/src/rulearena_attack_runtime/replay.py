from __future__ import annotations

from collections.abc import Sequence

import httpx
from rulearena_oracle import DeterministicOracle, InvariantId, OracleStatus
from rulearena_policy_schema import RuleSpec
from rulearena_reference_simulator import SimAction

from .minimization import minimize_trace
from .models import MinimizationResult, ReplayClassification, ReplayResult


class ActionUnknownError(RuntimeError):
    """A timed-out write had no authoritative receipt and must not be guessed or retried blindly."""


def classify_replay(status: OracleStatus) -> ReplayClassification:
    """Keep missing evidence distinct from a clean, non-violating replay."""
    if status is OracleStatus.VIOLATED:
        return ReplayClassification.CONFIRMED_VIOLATION
    if status is OracleStatus.INSUFFICIENT_EVIDENCE:
        return ReplayClassification.INSUFFICIENT_EVIDENCE
    return ReplayClassification.MODEL_DIVERGENCE


class SandboxReplayRunner:
    """HTTP-only adapter between candidate traces and a clean Sandbox RunSpace."""

    def __init__(
        self,
        base_url: str,
        internal_token: str,
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-Internal-Service-Token": internal_token}
        self.timeout = timeout
        self.transport = transport
        self.oracle = DeterministicOracle()

    async def replay(
        self,
        rule_spec: RuleSpec,
        actions: Sequence[SimAction],
        target_invariant: InvariantId,
        *,
        sandbox_version: str = "fixed",
    ) -> ReplayResult:
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            timeout=self.timeout,
            transport=self.transport,
        ) as client:
            created = await client.post(
                "/internal/runs",
                json={
                    "schema_version": "1.0",
                    "scenario_type": rule_spec.scenario_type.value,
                    "sandbox_version": sandbox_version,
                },
            )
            created.raise_for_status()
            created_data = created.json()
            run_id = str(created_data["run_id"])
            snapshots: list[dict[str, object]] = [dict(created_data["snapshot"])]
            receipts: list[dict[str, object]] = []
            aliases: dict[str, str] = {}
            alias_counts: dict[str, int] = {}
            known_alias_values: set[tuple[str, str]] = set()
            for index, action in enumerate(actions):
                payload = action.to_http_payload(key=action.idempotency_key or f"replay-{index}")
                if action.target_id:
                    payload["target_id"] = aliases.get(action.target_id, action.target_id)
                arguments = dict(payload["arguments"])
                for name, value in arguments.items():
                    if name.endswith("_id") and isinstance(value, str):
                        arguments[name] = aliases.get(value, value)
                payload["arguments"] = arguments
                key = str(payload["idempotency_key"])
                try:
                    response = await client.post(f"/internal/runs/{run_id}/actions", json=payload)
                    response.raise_for_status()
                    receipt = dict(response.json())
                except httpx.TimeoutException as exc:
                    authoritative = await client.get(
                        f"/internal/runs/{run_id}/receipts/{key}"
                    )
                    if authoritative.status_code == 404:
                        raise ActionUnknownError(
                            f"ACTION_UNKNOWN for stable idempotency key {key}"
                        ) from exc
                    authoritative.raise_for_status()
                    receipt = dict(authoritative.json())
                receipts.append(receipt)
                result = receipt.get("result")
                if isinstance(result, dict):
                    for name, value in result.items():
                        if not name.endswith("_id") or not isinstance(value, str):
                            continue
                        prefix = name.removesuffix("_id")
                        marker = (prefix, value)
                        if marker in known_alias_values:
                            continue
                        known_alias_values.add(marker)
                        alias_counts[prefix] = alias_counts.get(prefix, 0) + 1
                        aliases[f"{prefix}-{alias_counts[prefix]}"] = value
                snapshot = await client.get(f"/internal/runs/{run_id}/snapshot")
                snapshot.raise_for_status()
                snapshots.append(dict(snapshot.json()))
            event_response = await client.get(f"/internal/runs/{run_id}/events")
            event_response.raise_for_status()
            events = tuple(dict(item) for item in event_response.json()["events"])
        report = self.oracle.evaluate(
            rule_spec, snapshots=snapshots, receipts=receipts, events=events
        )
        finding = report.finding(target_invariant)
        classification = classify_replay(finding.status)
        return ReplayResult(
            classification=classification,
            target_invariant=target_invariant,
            run_id=run_id,
            actions=tuple(actions),
            report=report,
            snapshots=tuple(snapshots),
            receipts=tuple(receipts),
            events=events,
        )

    async def replay_repeated(
        self,
        rule_spec: RuleSpec,
        actions: Sequence[SimAction],
        target_invariant: InvariantId,
        *,
        repetitions: int = 3,
        sandbox_version: str = "fixed",
    ) -> tuple[ReplayResult, ...]:
        if repetitions < 1:
            raise ValueError("repetitions must be positive")
        results = []
        for _ in range(repetitions):
            results.append(
                await self.replay(
                    rule_spec, actions, target_invariant, sandbox_version=sandbox_version
                )
            )
        return tuple(results)

    async def minimize(
        self,
        rule_spec: RuleSpec,
        actions: Sequence[SimAction],
        target_invariant: InvariantId,
        *,
        sandbox_version: str = "fixed",
    ) -> MinimizationResult:
        async def confirms(candidate: tuple[SimAction, ...], invariant: InvariantId) -> bool:
            result = await self.replay(
                rule_spec,
                candidate,
                invariant,
                sandbox_version=sandbox_version,
            )
            return result.classification is ReplayClassification.CONFIRMED_VIOLATION

        return await minimize_trace(tuple(actions), target_invariant, confirms)
