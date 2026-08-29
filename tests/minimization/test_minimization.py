from rulearena_attack_runtime import minimize_trace
from rulearena_domain_contracts import ActionType
from rulearena_oracle import InvariantId
from rulearena_reference_simulator import SimAction


async def test_delta_debugging_returns_one_minimal_sequence() -> None:
    actions = tuple(
        SimAction.build(action_type, idempotency_key=f"key-{index}")
        for index, action_type in enumerate(
            (
                ActionType.CREATE_USER,
                ActionType.CREATE_ORDER,
                ActionType.PAY_ORDER,
                ActionType.REFUND_ORDER,
            )
        )
    )
    required = {ActionType.PAY_ORDER, ActionType.REFUND_ORDER}

    async def confirms(candidate: tuple[SimAction, ...], invariant: InvariantId) -> bool:
        assert invariant is InvariantId.REFUND_NOT_EXCEED_PAID
        return required <= {action.action_type for action in candidate}

    result = await minimize_trace(actions, InvariantId.REFUND_NOT_EXCEED_PAID, confirms)
    assert [action.action_type for action in result.minimized_actions] == [
        ActionType.PAY_ORDER,
        ActionType.REFUND_ORDER,
    ]
    assert result.one_minimal
    assert result.original_length == 4


async def test_minimizer_rejects_non_violating_input() -> None:
    async def never(candidate: tuple[SimAction, ...], invariant: InvariantId) -> bool:
        return False

    try:
        await minimize_trace((), InvariantId.IDEMPOTENT_EFFECT, never)
    except ValueError as error:
        assert "does not violate" in str(error)
    else:
        raise AssertionError("non-violating input must be rejected")
