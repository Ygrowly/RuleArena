import os

import pytest

from scripts.verify_database_isolation import verify_database_isolation


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_runtime_roles_cannot_access_each_others_schema() -> None:
    control_url = os.getenv("TEST_CONTROL_DATABASE_URL")
    sandbox_url = os.getenv("TEST_SANDBOX_DATABASE_URL")
    if not control_url or not sandbox_url:
        pytest.skip("set TEST_CONTROL_DATABASE_URL and TEST_SANDBOX_DATABASE_URL")

    await verify_database_isolation(control_url, sandbox_url)
