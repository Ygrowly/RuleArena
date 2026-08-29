from decimal import Decimal

import pytest
from pydantic import ValidationError
from rulearena_policy_schema import RuleSpec


def valid_rule_spec() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "scenario_type": "PROMOTION",
        "participants": [{"participant_id": "user-1", "kind": "USER"}],
        "assets": [
            {
                "asset_id": "wallet-1",
                "kind": "BALANCE",
                "initial_money": {"currency": "CNY", "amount": Decimal("100.1200")},
            }
        ],
        "rules": [
            {
                "rule_type": "PROMOTION",
                "minimum_order_amount": {"currency": "CNY", "amount": Decimal("100")},
                "discount_amount": {"currency": "CNY", "amount": Decimal("20.50")},
                "new_users_only": True,
                "restore_on_full_refund": False,
            }
        ],
        "invariants": [{"invariant_type": "COUPON_SINGLE_VALUE", "invariant_id": "INV-03"}],
        "ambiguities": [],
    }


def test_rule_spec_round_trip_is_stable() -> None:
    spec = RuleSpec.model_validate(valid_rule_spec())
    payload = spec.model_dump_json()
    assert RuleSpec.model_validate_json(payload) == spec
    assert '"100.1200"' in payload


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("assets", 0, "initial_money", "amount"), 1.25),
        (("assets", 0, "initial_money", "amount"), Decimal("-1")),
        (("assets", 0, "initial_money", "currency"), "BTC"),
        (("scenario_type",), "OTHER"),
    ],
)
def test_rule_spec_rejects_invalid_values(path: tuple[object, ...], value: object) -> None:
    payload = valid_rule_spec()
    target: object = payload
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]
    with pytest.raises(ValidationError):
        RuleSpec.model_validate(payload)


def test_rule_spec_rejects_unknown_fields() -> None:
    payload = valid_rule_spec()
    payload["ground_truth"] = "hidden"
    with pytest.raises(ValidationError):
        RuleSpec.model_validate(payload)


def test_rule_spec_rejects_code_expression() -> None:
    payload = valid_rule_spec()
    payload["ambiguities"] = [
        {"ambiguity_id": "a1", "field_path": "rules.0", "question": "eval(user_input)"}
    ]
    with pytest.raises(ValidationError):
        RuleSpec.model_validate(payload)


def test_rule_spec_json_schema_is_strict_and_discriminated() -> None:
    schema = RuleSpec.model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == "1.0"
    rule_items = schema["properties"]["rules"]["items"]
    assert rule_items["discriminator"]["propertyName"] == "rule_type"
