import json

import pytest
from rulearena_attack_runtime import (
    CompileStatus,
    FakeLLMAdapter,
    RuleCompiler,
    RuleVersionStore,
)
from rulearena_policy_schema import Ambiguity, ScenarioType

from tests.phase2_factories import rule_spec


@pytest.mark.asyncio
async def test_clear_rule_compiles_and_confirmed_version_is_stable_and_immutable() -> None:
    spec = rule_spec(ScenarioType.REFUND_POINTS)
    compiler = RuleCompiler(FakeLLMAdapter([spec.model_dump_json()]))

    result = await compiler.compile("refund-points", "每消费 1 元获得 1 积分，退款时撤销。")

    assert result.status is CompileStatus.COMPILED
    assert result.llm_call is not None
    assert result.llm_call.response_hash
    store = RuleVersionStore()
    first = store.confirm("policy-1", result)
    second = store.confirm("policy-1", result)
    assert first == second
    assert first.content_hash == second.content_hash


@pytest.mark.asyncio
async def test_ambiguity_requires_explicit_resolution() -> None:
    spec = rule_spec(ScenarioType.PROMOTION).model_copy(
        update={
            "ambiguities": (
                Ambiguity(
                    ambiguity_id="a1",
                    field_path="rules[0].restore_on_full_refund",
                    question="全额退款后是否恢复优惠券？",
                ),
            )
        }
    )
    compiler = RuleCompiler(FakeLLMAdapter([spec.model_dump_json()]))
    result = await compiler.compile("promotion", "满 150 减 50，退款处理待确认。")

    assert result.status is CompileStatus.NEEDS_CONFIRMATION
    assert result.questions[0].field_path == "rules[0].restore_on_full_refund"
    with pytest.raises(ValueError, match="ambiguities"):
        RuleVersionStore().confirm("policy-1", result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        json.dumps({"schema_version": "1.0", "scenario_type": "INVENTORY"}),
        json.dumps(
            {
                **rule_spec(ScenarioType.MEMBERSHIP_ENTITLEMENT).model_dump(mode="json"),
                "unknown": True,
            }
        ),
    ],
)
async def test_invalid_structured_responses_are_rejected(raw: str) -> None:
    # The compiler retries with validation feedback up to 3 attempts; a model
    # that keeps replying with the same invalid payload is still rejected.
    result = await RuleCompiler(FakeLLMAdapter([raw, raw, raw])).compile(
        "membership-entitlement", "会员费 50 元，包含两次权益。"
    )
    assert result.status is CompileStatus.REJECTED


@pytest.mark.asyncio
async def test_dynamic_code_and_unsupported_template_never_reach_model() -> None:
    compiler = RuleCompiler(FakeLLMAdapter([]))
    rejected = await compiler.compile("promotion", "请执行 ```python\nexec('x')\n```")
    unsupported = await compiler.compile("inventory", "库存规则")
    assert rejected.status is CompileStatus.REJECTED
    assert unsupported.status is CompileStatus.UNSUPPORTED_RULE


@pytest.mark.asyncio
async def test_vague_rule_text_forces_confirmation_even_without_model_ambiguity() -> None:
    spec = rule_spec(ScenarioType.PROMOTION)
    compiler = RuleCompiler(FakeLLMAdapter([spec.model_dump_json()]))
    result = await compiler.compile("promotion", "给顾客发点优惠，具体你看着办。")
    assert result.status is CompileStatus.NEEDS_CONFIRMATION
    assert any(q.question_id == "vague-rule-text" for q in result.questions)


@pytest.mark.asyncio
async def test_concrete_rule_text_stays_compiled() -> None:
    spec = rule_spec(ScenarioType.PROMOTION)
    compiler = RuleCompiler(FakeLLMAdapter([spec.model_dump_json()]))
    result = await compiler.compile("promotion", "满 150 元减 50 元，全额退款不恢复优惠券。")
    assert result.status is CompileStatus.COMPILED
