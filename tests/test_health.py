import pytest
from fastapi.testclient import TestClient
from rulearena_commerce_sandbox import create_app as create_sandbox_app
from rulearena_control_api import create_app as create_control_app
from rulearena_observability import ControlSettings, SandboxSettings


@pytest.mark.parametrize("service", ["control", "sandbox"])
def test_health_and_readiness_have_distinct_semantics(
    service: str,
    control_settings: ControlSettings,
    sandbox_settings: SandboxSettings,
) -> None:
    calls = 0

    async def broken_probe() -> None:
        nonlocal calls
        calls += 1
        raise ConnectionError("dependency unavailable")

    if service == "control":
        app = create_control_app(settings=control_settings, readiness_probe=broken_probe)
    else:
        app = create_sandbox_app(settings=sandbox_settings, readiness_probe=broken_probe)
    with TestClient(app) as client:
        health = client.get("/healthz")
        readiness = client.get("/readyz")
    assert health.status_code == 200
    assert health.json()["status"] == "alive"
    assert readiness.status_code == 503
    assert readiness.json()["status"] == "not-ready"
    assert calls == 1


@pytest.mark.parametrize("service", ["control", "sandbox"])
def test_readiness_succeeds_only_after_probe(
    service: str,
    control_settings: ControlSettings,
    sandbox_settings: SandboxSettings,
) -> None:
    async def healthy_probe() -> None:
        return None

    if service == "control":
        app = create_control_app(control_settings, healthy_probe)
    else:
        app = create_sandbox_app(sandbox_settings, healthy_probe)
    with TestClient(app) as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
