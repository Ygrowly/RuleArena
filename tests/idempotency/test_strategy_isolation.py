from rulearena_attack_runtime import Budget, InMemoryRuntimeStore, StrategyType


def test_three_strategy_runs_and_checkpoints_have_distinct_ownership() -> None:
    store = InMemoryRuntimeStore()
    budget = Budget(max_steps=2, max_tokens=10, max_cost=1, max_time_seconds=2)
    run = store.create_run(
        job_key="job-isolation",
        rule_version_id="rule-1",
        scenario_version_id="scenario-1",
        sandbox_version="fixed",
        oracle_version="1.0",
        budget=budget,
        random_seed=0,
    )
    strategies = [store.ensure_strategy(run.run_id, item, budget) for item in StrategyType]
    assert len({item.strategy_run_id for item in strategies}) == 3
    for index, item in enumerate(strategies):
        store.save_checkpoint(item.strategy_run_id, {"private": [index]}, expected_version=0)
    loaded = [store.load_checkpoint(item.strategy_run_id) for item in strategies]
    assert [item.state["private"] for item in loaded if item is not None] == [[0], [1], [2]]
    assert len({id(item.state) for item in loaded if item is not None}) == 3
