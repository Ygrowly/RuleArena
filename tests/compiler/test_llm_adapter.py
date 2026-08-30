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
        response_schema=proposal_json_schema(),
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
