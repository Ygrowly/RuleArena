from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import NoReturn

from dotenv import load_dotenv
from rulearena_attack_runtime import (
    Budget,
    OpenAICompatibleLLMAdapter,
    SandboxReplayRunner,
    proposal_json_schema,
)

from .baselines import AgentBaselineExecutor, DelegatingCaseExecutor, SearchBaselineExecutor
from .gate import ReleaseGate
from .historical_p0 import historical_p0_pass_rate
from .loader import (
    DevelopmentCaseLoader,
    EvaluationAccess,
    HiddenCaseLoader,
    load_hidden_manifest,
)
from .models import BaselineType, VersionTuple, Visibility
from .runner import BenchmarkRunner
from .store import PostgresBenchmarkStore


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _versions(args: argparse.Namespace) -> VersionTuple:
    model_descriptor = (
        f"{os.getenv('LLM_BASE_URL', 'none')}:{os.getenv('LLM_MODEL', 'none')}:"
        f"{args.temperature}"
    )
    return VersionTuple(
        benchmark_version=args.benchmark_version,
        runtime_version=args.runtime_version,
        rule_set_version=args.rule_set_version,
        scenario_set_version=args.scenario_set_version,
        sandbox_version=args.sandbox_version,
        oracle_version=args.oracle_version,
        model_config_hash=hashlib.sha256(model_descriptor.encode()).hexdigest(),
        prompt_version=args.prompt_version,
    )


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--benchmark-version", default="golden-v2")
    parser.add_argument("--runtime-version", default="runtime-v1")
    parser.add_argument("--rule-set-version", default="rules-v1")
    parser.add_argument("--scenario-set-version", default="scenarios-v1")
    parser.add_argument("--sandbox-version", default="sandbox-suite-v1")
    parser.add_argument("--oracle-version", default="1.0")
    parser.add_argument("--prompt-version", default="benchmark-v1")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=20260831)


async def _run(args: argparse.Namespace) -> int:
    root = Path(__file__).resolve().parents[4]
    cases = (
        DevelopmentCaseLoader(root / "benchmarks" / "development-v1.json").load()
        if args.suite == Visibility.DEVELOPMENT.value
        else HiddenCaseLoader(EvaluationAccess.from_environment()).load()
    )
    aliases = {
        "random": BaselineType.RANDOM,
        "bfs": BaselineType.BFS,
        "single": BaselineType.SINGLE_AGENT,
        "single_agent": BaselineType.SINGLE_AGENT,
        "multi": BaselineType.MULTI_STRATEGY,
        "multi_strategy": BaselineType.MULTI_STRATEGY,
    }
    try:
        selected = tuple(aliases[item.strip().casefold()] for item in args.baselines.split(","))
    except KeyError as error:
        raise ValueError(f"unknown baseline: {error.args[0]}") from error
    replay = SandboxReplayRunner(_required("SANDBOX_HTTP_URL"), _required("INTERNAL_SERVICE_TOKEN"))

    def adapter_factory(prompt_version: str) -> OpenAICompatibleLLMAdapter:
        return OpenAICompatibleLLMAdapter(
            base_url=_required("LLM_BASE_URL"),
            api_key=_required("LLM_API_KEY"),
            model=_required("LLM_MODEL"),
            temperature=args.temperature,
            prompt_version=prompt_version,
            response_schema=proposal_json_schema(),
            schema_name="rulearena_agent_proposal",
            input_cost_per_million_tokens=float(os.getenv("LLM_INPUT_COST_PER_MTOKEN", "0") or 0),
            output_cost_per_million_tokens=float(os.getenv("LLM_OUTPUT_COST_PER_MTOKEN", "0") or 0),
            timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "120") or 120),
        )

    executor = DelegatingCaseExecutor(
        SearchBaselineExecutor(replay), AgentBaselineExecutor(replay, adapter_factory)
    )
    store = PostgresBenchmarkStore(_required("CONTROL_DATABASE_URL"))
    regression_rate = historical_p0_pass_rate()
    try:
        runner = BenchmarkRunner(store, executor)
        for baseline in selected:
            result = await runner.run(
                cases,
                versions=_versions(args),
                baseline=baseline,
                repetitions=args.repetitions,
                random_seed=args.seed,
                historical_p0_pass_rate=regression_rate,
            )
            print(
                json.dumps(
                    {
                        "benchmark_run_id": result.benchmark_run_id,
                        "baseline": baseline.value,
                        "metrics": result.metrics,
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        store.close()
    return 0


def _manifest_budget(root: Path) -> Budget:
    """The release gate compares against the budget declared by the hidden manifest."""
    metadata = load_hidden_manifest(root / "benchmarks" / "hidden-manifest.json")
    budgets = {case.budget for case in metadata}
    if len(budgets) != 1:
        raise RuntimeError("hidden manifest must declare one normalized budget")
    return next(iter(budgets))


def _verify(args: argparse.Namespace) -> int:
    store = PostgresBenchmarkStore(_required("CONTROL_DATABASE_URL"))
    try:
        versions = _versions(args)
        run = store.latest(
            versions=versions,
            baseline=BaselineType.MULTI_STRATEGY,
            suite=Visibility.HIDDEN,
        )
        gate = ReleaseGate().evaluate(
            run,
            expected_versions=versions,
            expected_budget=_manifest_budget(Path(__file__).resolve().parents[4]),
            expected_seed=args.seed,
        )
        print(gate.model_dump_json())
        return 0 if gate.passed else 1
    finally:
        store.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rulearena")
    commands = parser.add_subparsers(dest="command", required=True)
    benchmark = commands.add_parser("benchmark")
    _common(benchmark)
    benchmark.add_argument("action", nargs="?", choices=("verify",))
    benchmark.add_argument("--latest", action="store_true")
    benchmark.add_argument("--suite", choices=tuple(item.value for item in Visibility))
    benchmark.add_argument(
        "--baselines", default="random,bfs,single_agent,multi_strategy"
    )
    benchmark.add_argument("--repetitions", type=int, default=1)
    return parser


def _die(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(2)


def main() -> None:
    load_dotenv(override=False)
    args = _parser().parse_args()
    try:
        if args.action == "verify":
            if not args.latest:
                _die("benchmark verify requires --latest")
            code = _verify(args)
        else:
            if args.suite is None:
                _die("benchmark run requires --suite")
            code = asyncio.run(_run(args))
    except (PermissionError, RuntimeError, ValueError) as error:
        _die(str(error))
    raise SystemExit(code)
