FROM python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217 AS runtime
RUN pip install --no-cache-dir uv==0.11.19
WORKDIR /app
# Container network cannot always reach pythonhosted.org directly.
ENV UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY services ./services
COPY migrations ./migrations
COPY alembic.ini alembic-sandbox.ini ./
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
ARG SERVICE_MODULE
ENV SERVICE_MODULE=$SERVICE_MODULE
CMD ["sh", "-c", "uvicorn ${SERVICE_MODULE}:create_app --factory --host 0.0.0.0 --port ${PORT}"]
