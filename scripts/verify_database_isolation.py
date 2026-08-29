import asyncio
import os

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


async def assert_role_isolation(
    engine: AsyncEngine,
    *,
    own_schema: str,
    other_schema: str,
) -> None:
    async with engine.connect() as connection:
        own = await connection.scalar(
            text("SELECT has_schema_privilege(current_user, :schema, 'USAGE')"),
            {"schema": own_schema},
        )
        other = await connection.scalar(
            text("SELECT has_schema_privilege(current_user, :schema, 'USAGE')"),
            {"schema": other_schema},
        )
        if own is not True or other is not False:
            raise AssertionError(
                f"unexpected schema privileges for {own_schema}: own={own}, other={other}"
            )
        try:
            await connection.execute(text(f"SELECT * FROM {other_schema}.alembic_version"))
        except ProgrammingError as error:
            if f"permission denied for schema {other_schema}" not in str(error):
                raise
        else:
            raise AssertionError(f"{own_schema} role unexpectedly read {other_schema}")


async def verify_database_isolation(control_url: str, sandbox_url: str) -> None:
    control_engine = create_async_engine(control_url)
    sandbox_engine = create_async_engine(sandbox_url)
    try:
        await assert_role_isolation(
            control_engine,
            own_schema="control",
            other_schema="sandbox",
        )
        await assert_role_isolation(
            sandbox_engine,
            own_schema="sandbox",
            other_schema="control",
        )
    finally:
        await control_engine.dispose()
        await sandbox_engine.dispose()


def main() -> None:
    control_url = os.environ["TEST_CONTROL_DATABASE_URL"]
    sandbox_url = os.environ["TEST_SANDBOX_DATABASE_URL"]
    asyncio.run(verify_database_isolation(control_url, sandbox_url))
    print("database role isolation verified")


if __name__ == "__main__":
    main()
