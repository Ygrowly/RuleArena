# Attack worker

ARQ entrypoint for the phase 3 deterministic workflow. It requires the Control PostgreSQL URL,
Redis, internal Sandbox URL/token, and an OpenAI-compatible structured-output model endpoint.

The worker fails closed when model credentials are absent. Every queue job resumes the durable
AttackRun/StrategyRun checkpoint and all Sandbox writes use stable Runtime idempotency keys.
