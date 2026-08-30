import os
from uuid import uuid4

import pytest
from rulearena_control_api.queue import ArqRunEnqueuer
from rulearena_policy_schema import ScenarioType

from tests.phase2_factories import rule_spec


@pytest.mark.asyncio
async def test_arq_job_id_is_stable_across_duplicate_enqueue() -> None:
    redis_url = os.getenv("TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("set TEST_REDIS_URL to run the real ARQ queue test")
    enqueuer = ArqRunEnqueuer(redis_url)
    run_id = str(uuid4())
    spec = rule_spec(ScenarioType.PROMOTION)
    await enqueuer.enqueue(run_id, spec)
    await enqueuer.enqueue(run_id, spec)
    assert enqueuer.pool is not None
    jobs = await enqueuer.pool.queued_jobs()
    assert [job.job_id for job in jobs].count(f"attack:{run_id}") == 1
    await enqueuer.close()
