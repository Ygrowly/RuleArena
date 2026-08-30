from __future__ import annotations

from typing import cast

from arq import run_worker
from arq.typing import WorkerSettingsBase

from .settings import WorkerSettings


def run() -> None:
    run_worker(cast(type[WorkerSettingsBase], WorkerSettings))
