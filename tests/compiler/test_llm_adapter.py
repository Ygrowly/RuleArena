import json

import httpx
import pytest
from rulearena_attack_runtime import (
    OpenAICompatibleLLMAdapter,
    proposal_json_schema,
)


@pytest.mark.asyncio
async def test_openai_compatible_adapter_uses_selected_schema_and_redacted_audit() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer secret-provider-key"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"proposal_type": "STOP", "reason": "bounded stop"}
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3, "cost": 0.01},
            },
        )

    adapter = OpenAICompatibleLLMAdapter(
        base_url="https://model.invalid/v1",
        api_key="secret-provider-key",
        model="structured-model",
        response_schema={
            "type": "object",
            "properties": {"proposal_type": {"type": "string"}},
            "required": ["proposal_type"],
            "additionalProperties": False,
        },
        schema_name="rulearena_agent_proposal",
        transport=httpx.MockTransport(handler),
    )
    response = await adapter.complete_structured(
        system="system boundary", untrusted_input="<UNTRUSTED>data</UNTRUSTED>"
    )

    assert json.loads(response.content)["proposal_type"] == "STOP"
    response_format = captured["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["json_schema"]["name"] == "rulearena_agent_proposal"
    assert adapter.last_call is not None
    assert adapter.last_call.input_tokens == 7
    assert adapter.last_call.output_tokens == 3
    assert adapter.last_call.cost == 0.01
    assert "secret-provider-key" not in adapter.last_call.model_dump_json()
    assert "bounded stop" not in adapter.last_call.model_dump_json()


@pytest.mark.asyncio
async def test_adapter_omits_response_format_for_union_roots() -> None:
    """Discriminated-union roots (no top-level "type") are rejected by providers
    like MiniMax; validation+retries remains the enforced boundary."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"proposal_type":"STOP","reason":"bounded stop"}'
                        }
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
        )

    adapter = OpenAICompatibleLLMAdapter(
        base_url="https://model.invalid/v1",
        api_key="secret-provider-key",
        model="structured-model",
        response_schema=proposal_json_schema(),
        schema_name="rulearena_agent_proposal",
        transport=httpx.MockTransport(handler),
    )
    await adapter.complete_structured(system="s", untrusted_input="u")
    assert "response_format" not in captured


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("usage", "input_price", "output_price", "expected_cost"),
    [
        ({"prompt_tokens": 1_000_000, "completion_tokens": 500_000}, 0.5, 4.0, 0.5 + 2.0),
        ({"prompt_tokens": 10, "completion_tokens": 20, "cost": 0.25}, 0.5, 4.0, 0.25),
        ({"prompt_tokens": 10, "completion_tokens": 20}, 0.0, 0.0, 0.0),
    ],
    ids=["estimated-from-pricing", "provider-cost-wins", "no-pricing-no-provider-cost"],
)
async def test_adapter_cost_falls_back_to_configured_pricing(
    usage: dict[str, object], input_price: float, output_price: float, expected_cost: float
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"proposal_type":"STOP","reason":"x"}'}}],
                "usage": usage,
            },
        )

    adapter = OpenAICompatibleLLMAdapter(
        base_url="https://model.invalid/v1",
        api_key="secret-provider-key",
        model="structured-model",
        response_schema=proposal_json_schema(),
        transport=httpx.MockTransport(handler),
        input_cost_per_million_tokens=input_price,
        output_cost_per_million_tokens=output_price,
    )
    await adapter.complete_structured(system="s", untrusted_input="u")
    assert adapter.last_call is not None
    assert adapter.last_call.cost == pytest.approx(expected_cost)


@pytest.mark.asyncio
async def test_adapter_strips_inline_think_blocks() -> None:
    """MiniMax-M2 inlines reasoning as <think>…</think> before the JSON payload."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '<think>\nreasoning about the rule\n</think>\n\n'
                                '{"proposal_type":"STOP","reason":"clean stop"}'
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 30},
            },
        )

    adapter = OpenAICompatibleLLMAdapter(
        base_url="https://model.invalid/v1",
        api_key="secret-provider-key",
        model="minimax-m2",
        response_schema=proposal_json_schema(),
        transport=httpx.MockTransport(handler),
    )
    response = await adapter.complete_structured(system="s", untrusted_input="u")
    assert json.loads(response.content)["proposal_type"] == "STOP"
