from __future__ import annotations

from arq.connections import ArqRedis, RedisSettings, create_pool
from rulearena_policy_schema import RuleSpec


class ArqRunEnqueuer:
    def __init__(self, redis_url: str) -> None:
        self.settings = RedisSettings.from_dsn(redis_url)
        self.pool: ArqRedis | None = None

    async def enqueue(self, run_id: str, rule_spec: RuleSpec) -> None:
        if self.pool is None:
            self.pool = await create_pool(self.settings)
        await self.pool.enqueue_job(
            "execute_attack",
            run_id,
            rule_spec.model_dump(mode="json"),
            _job_id=f"attack:{run_id}",
        )

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.aclose()
            self.pool = None
