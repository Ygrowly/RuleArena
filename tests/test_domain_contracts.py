from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError
from rulearena_domain_contracts import ActionReceipt, ActionRequest


def test_action_request_and_receipt_have_stable_json() -> None:
    request = ActionRequest.model_validate(
        {
            "run_id": "run-1",
            "actor_id": "user-1",
            "idempotency_key": "run-1:step-1",
            "action": {
                "action_type": "CREATE_ORDER",
                "amount": {"currency": "CNY", "amount": Decimal("123.4500")},
            },
        }
    )
    receipt = ActionReceipt.model_validate(
        {
            "receipt_id": UUID("12345678-1234-5678-1234-567812345678"),
            "run_id": "run-1",
            "idempotency_key": "run-1:step-1",
            "action_type": "CREATE_ORDER",
            "status": "SUCCEEDED",
            "monetary_effects": [{"currency": "CNY", "amount": Decimal("123.4500")}],
            "occurred_at": datetime(2026, 8, 29, tzinfo=UTC),
        }
    )
    assert ActionRequest.model_validate_json(request.model_dump_json()) == request
    assert ActionReceipt.model_validate_json(receipt.model_dump_json()) == receipt
    assert '"123.4500"' in receipt.model_dump_json()


def test_write_action_requires_idempotency_key() -> None:
    with pytest.raises(ValidationError, match="idempotency_key"):
        ActionRequest.model_validate(
            {
                "run_id": "run-1",
                "actor_id": "user-1",
                "action": {
                    "action_type": "CREATE_USER",
                    "initial_balance": {"currency": "CNY", "amount": Decimal("1")},
                },
            }
        )


def test_float_money_is_rejected_in_action() -> None:
    with pytest.raises(ValidationError):
        ActionRequest.model_validate(
            {
                "run_id": "run-1",
                "actor_id": "user-1",
                "idempotency_key": "key",
                "action": {
                    "action_type": "CREATE_ORDER",
                    "amount": {"currency": "CNY", "amount": 0.1},
                },
            }
        )


def test_action_union_is_discriminated_and_action_specific() -> None:
    request = ActionRequest.model_validate(
        {
            "run_id": "run-1",
            "actor_id": "user-1",
            "idempotency_key": "key",
            "action": {
                "action_type": "APPLY_COUPON",
                "order_id": "order-1",
                "coupon_id": "coupon-1",
            },
        }
    )
    assert request.action.action_type == "APPLY_COUPON"

    with pytest.raises(ValidationError):
        ActionRequest.model_validate(
            {
                "run_id": "run-1",
                "actor_id": "user-1",
                "idempotency_key": "key",
                "action": {
                    "action_type": "PAY_ORDER",
                    "order_id": "order-1",
                    "coupon_id": "coupon-1",
                },
            }
        )


def test_contract_json_schema_is_strict_and_versioned() -> None:
    schema = ActionRequest.model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == "1.0"
    action_schema = schema["$defs"]["BusinessAction"]
    assert action_schema["discriminator"]["propertyName"] == "action_type"
