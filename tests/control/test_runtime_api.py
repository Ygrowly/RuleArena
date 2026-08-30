from fastapi.testclient import TestClient
from rulearena_attack_runtime import (
    FakeLLMAdapter,
    InMemoryRuntimeStore,
    RuleCompiler,
    RuleVersionStore,
)
from rulearena_control_api import create_app
from rulearena_policy_schema import RuleSpec, ScenarioType

from tests.phase2_factories import rule_spec


class RecordingEnqueuer:
    def __init__(self) -> None:
        self.items: list[tuple[str, RuleSpec]] = []

    async def enqueue(self, run_id: str, spec: RuleSpec) -> None:
        self.items.append((run_id, spec))


def test_compile_confirm_create_idempotent_run_and_resume_sse(control_settings: object) -> None:
    spec = rule_spec(ScenarioType.PROMOTION)
    runtime = InMemoryRuntimeStore()
    versions = RuleVersionStore()
    enqueuer = RecordingEnqueuer()

    async def ready() -> None:
        return None

    app = create_app(
        control_settings,  # type: ignore[arg-type]
        ready,
        compiler=RuleCompiler(FakeLLMAdapter([spec.model_dump_json()])),
        runtime_store=runtime,
        version_store=versions,
        run_enqueuer=enqueuer,
    )
    with TestClient(app) as client:
        compiled = client.post(
            "/api/policies/compile",
            json={"template_id": "promotion", "chinese_modification": "满 150 减 50。"},
        )
        assert compiled.status_code == 200
        policy_id = compiled.json()["policy_id"]
        confirmed = client.post(f"/api/policies/{policy_id}/confirm", json={})
        assert confirmed.status_code == 200
        version_id = confirmed.json()["version_id"]
        payload = {
            "rule_version_id": version_id,
            "scenario_version_id": "scenario-1",
            "budget": {
                "max_steps": 3,
                "max_tokens": 100,
                "max_cost": 1,
                "max_time_seconds": 10,
            },
        }
        first = client.post(
            "/api/runs", json=payload, headers={"Idempotency-Key": "api-job-1"}
        )
        second = client.post(
            "/api/runs", json=payload, headers={"Idempotency-Key": "api-job-1"}
        )
        assert first.status_code == 201
        assert first.json()["run_id"] == second.json()["run_id"]
        assert len(enqueuer.items) == 2
        run_id = first.json()["run_id"]
        runtime.append_event(run_id, "PROGRESS", {"step": 1})
        all_events = client.get(f"/api/runs/{run_id}/events?follow=false").text
        assert "event: RUN_CREATED" in all_events
        assert "event: PROGRESS" in all_events
        resumed = client.get(
            f"/api/runs/{run_id}/events?follow=false", headers={"Last-Event-ID": "1"}
        ).text
        assert "event: RUN_CREATED" not in resumed
        assert "event: PROGRESS" in resumed
