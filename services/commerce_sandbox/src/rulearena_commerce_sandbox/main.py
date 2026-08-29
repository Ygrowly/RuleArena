import uvicorn

from .app import create_app


def run() -> None:
    uvicorn.run(create_app(), host="0.0.0.0", port=8001)
