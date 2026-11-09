import json

import pytest
from pydantic import BaseModel, ValidationError
from pydantic_core import PydanticSerializationError

from app.api import main as api_main
from app.core.models import (
    ChatRequest,
    ChatResponse,
    ConversationDetailResponse,
    ConversationListResponse,
    SafetyGuidance,
)
from app.core.models import Product
from app.core.public_product import public_product_payload
from app.core.public_api_schema import (
    ConversationDeleteAllResponse,
    PublicConversationState,
    PublicPerProfileResult,
    PublicProduct,
)


def _per_profile_result(**updates):
    payload = {
        "profile_id": 7,
        "profile_name": "가족 프로필",
        "target_type": "family",
        "intent": "can_i_eat",
        "risk_level": "medium",
        "assessment_status": "complete",
        "answer": "주의해서 확인해 주세요.",
        "reasons": ["개별 프로필 기준 판정"],
    }
    payload.update(updates)
    return payload


def test_chat_response_uses_closed_dict_compatible_nested_models():
    response = ChatResponse(
        intent="can_i_eat",
        answer="프로필별 결과를 확인해 주세요.",
        risk_level="medium",
        per_profile_results=[_per_profile_result()],
        conversation_state={
            "version": 1,
            "last_intent": "product_confirmation",
            "last_task_type": "product_chat",
            "pending_product_candidates": [
                {
                    "product_name": "공개 제품",
                    "barcode": "8800000000001",
                }
            ],
        },
    )

    assert isinstance(response.per_profile_results[0], PublicPerProfileResult)
    assert response.per_profile_results[0]["profile_name"] == "가족 프로필"
    assert isinstance(response.conversation_state, PublicConversationState)
    assert response.conversation_state["last_intent"] == "product_confirmation"

    payload = response.model_dump(mode="json")
    assert payload["conversation_state"] == {
        "version": 1,
        "last_intent": "product_confirmation",
        "last_task_type": "product_chat",
        "pending_product_candidates": [
            {
                "product_name": "공개 제품",
                "barcode": "8800000000001",
            }
        ],
    }
    assert "product_id" not in json.dumps(payload, ensure_ascii=False)


def test_legacy_internal_diagnostics_are_marked_deprecated_in_public_schema():
    schema = ChatResponse.model_json_schema()

    for field in (
        "pipeline_stage", "source_details", "policy_version",
        "applied_rule_ids", "evidence", "fallback_reason",
        "validation_failure_types", "critical_safety_violation",
        "blocked_llm_output_stored",
    ):
        assert schema["properties"][field]["deprecated"] is True


def test_public_nested_contracts_reject_arbitrary_or_private_fields():
    base = {
        "intent": "can_i_eat",
        "answer": "확인이 필요해요.",
        "risk_level": "unknown",
    }

    with pytest.raises(ValidationError):
        ChatResponse(
            **base,
            per_profile_results=[
                _per_profile_result(product_id=991, internal_note="private")
            ],
        )
    with pytest.raises(ValidationError):
        ChatResponse(
            **base,
            conversation_state={
                "version": 1,
                "last_intent": "product_summary",
                "secret": "must-not-cross-contract",
            },
        )
    with pytest.raises(ValidationError):
        ChatResponse(
            **base,
            ui_guidance={
                "code": "OPEN_ARBITRARY_URL",
                "message": "외부로 이동",
                "url": "https://example.invalid",
            },
        )
    with pytest.raises(ValidationError):
        ChatResponse(
            **base,
            conversation_state={
                "version": 1,
                "last_confirmed_product": {
                    "product_name": "제품",
                    "barcode": "8800000000001",
                    "product_id": 991,
                },
            },
        )


def test_public_nested_contracts_are_bounded():
    base = {
        "intent": "can_i_eat",
        "answer": "확인이 필요해요.",
        "risk_level": "unknown",
    }

    with pytest.raises(ValidationError):
        ChatResponse(
            **base,
            per_profile_results=[
                _per_profile_result(answer="x" * 16_001)
            ],
        )
    with pytest.raises(ValidationError):
        ChatResponse(
            **base,
            per_profile_results=[
                _per_profile_result(profile_id=index)
                for index in range(21)
            ],
        )
    with pytest.raises(ValidationError):
        ChatResponse(
            **base,
            conversation_state={
                "version": 1,
                "last_intent": "x" * 257,
            },
        )
    with pytest.raises(ValidationError):
        ChatResponse(
            **base,
            conversation_state={
                "version": 1,
                "pending_product_candidates": [
                    {"barcode": str(index)} for index in range(4)
                ],
            },
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reasons", ["reason"] * 101),
        ("reasons", ["x" * 1001]),
        ("confidence_reasons", ["reason"] * 51),
        ("data_completeness_reasons", ["reason"] * 51),
        ("active_profile_names", [f"profile-{index}" for index in range(21)]),
        ("recommended_questions", ["question"] * 4),
        ("recommended_questions", ["x" * 1001]),
        ("sources", ["internal-policy:rules.md"] * 11),
        ("sources", ["x" * 2049]),
        ("validation_failure_types", ["failure"] * 51),
        ("grounding_context", ["context"] * 6),
    ],
)
def test_chat_response_metadata_lists_are_bounded(field, value):
    with pytest.raises(ValidationError):
        ChatResponse(
            intent="unknown",
            answer="확인이 필요해요.",
            risk_level="unknown",
            **{field: value},
        )


def test_model_copy_cannot_bypass_closed_public_nested_contracts():
    response = ChatResponse(
        intent="unknown",
        answer="확인이 필요해요.",
        risk_level="unknown",
    )

    with pytest.raises(ValidationError):
        response.model_copy(
            update={
                "conversation_state": {
                    "version": 1,
                    "private_repository_id": 991,
                }
            }
        )

    for field, value in (
        ("answer", "x" * 16_001),
        ("sources", ["source"] * 11),
        ("recommended_questions", ["x" * 1001]),
        ("validation_failure_types", ["failure"] * 51),
    ):
        with pytest.raises(ValidationError):
            response.model_copy(update={field: value})
    with pytest.raises(ValidationError):
        response.model_copy(
            update={
                "per_profile_results": [
                    _per_profile_result(account_user_id=1234)
                ]
            }
        )

    public_product = public_product_payload(Product(product_name="공개 제품"))
    with pytest.raises(ValidationError):
        response.model_copy(
            update={
                "current_product": {
                    **public_product,
                    "private_repository_id": 991,
                }
            }
        )
    with pytest.raises(ValidationError):
        response.model_copy(
            update={
                "product_candidates": [
                    {
                        **public_product,
                        "reason": "후보",
                        "selection_type": "product_candidate",
                        "private_repository_id": 991,
                    }
                ]
            }
        )
    with pytest.raises(ValidationError):
        response.model_copy(
            update={
                "recommended_products": [
                    {
                        "product_name": "대체 제품",
                        "reason": "상대 비교",
                        "caution": "포장지를 다시 확인하세요.",
                        "recommendation_basis": {
                            "profile_allergy_checked": False,
                            "profile_special_diets_checked": False,
                            "nutrition_basis_comparable": False,
                            "current_nutrition_basis": "미확인",
                        },
                        "private_repository_id": 991,
                    }
                ]
            }
        )


def test_final_serialization_revalidates_product_fields_after_unsafe_base_copy():
    response = ChatResponse(
        intent="unknown",
        answer="확인이 필요해요.",
        risk_level="unknown",
    )
    public_product = public_product_payload(Product(product_name="공개 제품"))
    bypassed = super(ChatResponse, response).model_copy(
        update={
            "current_product": {
                **public_product,
                "private_repository_id": 991,
            }
        }
    )

    with pytest.raises(PydanticSerializationError):
        bypassed.model_dump(mode="json")


def test_final_serialization_revalidates_all_fields_and_existing_nested_models():
    response = ChatResponse(
        intent="unknown",
        answer="확인이 필요해요.",
        risk_level="unknown",
    )

    oversized_answer = BaseModel.model_copy(
        response,
        update={"answer": "x" * 16_001},
    )
    with pytest.raises(PydanticSerializationError):
        oversized_answer.model_dump(mode="json")

    product = PublicProduct.model_validate(
        public_product_payload(Product(product_name="공개 제품"))
    )
    corrupted_product = BaseModel.model_copy(
        product,
        update={"product_name": "x" * 1_000_000},
    )
    response_with_corrupted_product = BaseModel.model_copy(
        response,
        update={"current_product": corrupted_product},
    )
    with pytest.raises(PydanticSerializationError):
        response_with_corrupted_product.model_dump(mode="json")

    guidance = SafetyGuidance(
        urgency="routine",
        headline="확인 안내",
        actions=["표시를 확인하세요."],
    )
    corrupted_guidance = BaseModel.model_copy(
        guidance,
        update={"actions": ["x" * 1_000_000]},
    )
    response_with_corrupted_guidance = BaseModel.model_copy(
        response,
        update={"safety_guidance": corrupted_guidance},
    )
    with pytest.raises(PydanticSerializationError):
        response_with_corrupted_guidance.model_dump(mode="json")


def test_public_state_round_trips_into_next_stateless_chat_request():
    response = ChatResponse(
        intent="product_summary",
        answer="제품을 선택했어요.",
        risk_level="unknown",
        conversation_state={
            "version": 1,
            "last_intent": "product_summary",
            "last_task_type": "product_chat",
            "last_confirmed_product": {
                "product_name": "공개 제품",
                "report_number": "REPORT-1",
            },
        },
    )

    request = ChatRequest(
        message="그 제품 나트륨은 어때?",
        conversation_state=response.conversation_state,
    )

    assert request.conversation_state == response.model_dump(mode="json")[
        "conversation_state"
    ]


def test_conversation_operation_response_is_closed():
    with pytest.raises(ValidationError):
        ConversationDeleteAllResponse(deleted=1, owner_user_id=1234)


def test_saved_conversation_response_pages_are_bounded():
    conversation = {
        "conversation_id": "00000000-0000-0000-0000-000000000001",
        "title": "저장 채팅",
        "revision": 0,
        "created_at": "2026-09-10T00:00:00Z",
        "updated_at": "2026-09-10T00:00:00Z",
    }
    message = {
        "message_id": "00000000-0000-0000-0000-000000000002",
        "sequence": 1,
        "role": "user",
        "content": "질문",
        "created_at": "2026-09-10T00:00:00Z",
    }

    with pytest.raises(ValidationError):
        ConversationListResponse(conversations=[conversation] * 101)
    with pytest.raises(ValidationError):
        ConversationDetailResponse(
            conversation=conversation,
            messages=[message] * 201,
        )
    with pytest.raises(ValidationError):
        ConversationDetailResponse(
            conversation=conversation,
            messages=[{**message, "content": "x" * 16_001}],
        )


def test_openapi_publishes_closed_nested_and_endpoint_response_models():
    openapi = api_main.app.openapi()
    schemas = openapi["components"]["schemas"]

    assert schemas["ChatResponse"]["additionalProperties"] is False
    for name in (
        "PublicPerProfileResult",
        "PublicConversationProductReference",
        "PublicConversationState",
        "ConversationDeleteAllResponse",
    ):
        assert schemas[name]["additionalProperties"] is False

    chat_properties = schemas["ChatResponse"]["properties"]
    assert chat_properties["conversation_state"]["$ref"].endswith(
        "/PublicConversationState"
    )
    result_items = chat_properties["per_profile_results"]["items"]
    assert result_items["$ref"].endswith("/PublicPerProfileResult")
    state_properties = schemas["PublicConversationState"]["properties"]
    assert "product_id" not in json.dumps(state_properties)
    product_properties = schemas["PublicProduct"]["properties"]
    from app.core.public_product_schema import MAX_PUBLIC_PRODUCT_NAME_LENGTH
    assert product_properties["product_name"]["anyOf"][0]["maxLength"] == MAX_PUBLIC_PRODUCT_NAME_LENGTH
    assert product_properties["ingredients"]["maxItems"] == 128
    assert product_properties["ingredients"]["items"]["maxLength"] == 512
    assert schemas["PublicNutritionFacts"]["properties"]["sodium"]["anyOf"][0][
        "minimum"
    ] == 0
    assert schemas["ChatResponse"]["properties"]["product_candidates"][
        "maxItems"
    ] == 3
    assert schemas["ChatResponse"]["properties"]["recommended_products"][
        "maxItems"
    ] == 10
    assert schemas["ChatResponse"]["properties"]["reasons"]["maxItems"] == 100
    assert schemas["ChatResponse"]["properties"]["reasons"]["items"][
        "maxLength"
    ] == 1000
    assert schemas["ChatResponse"]["properties"]["recommended_questions"][
        "maxItems"
    ] == 3
    assert schemas["ChatResponse"]["properties"]["sources"]["maxItems"] == 10
    assert schemas["ChatResponse"]["properties"]["sources"]["items"][
        "maxLength"
    ] == 2048
    assert "validation_failure_types" not in chat_properties
    assert "pipeline_stage" not in chat_properties
    assert "evidence" not in chat_properties
    assert schemas["ConversationListResponse"]["properties"]["conversations"][
        "maxItems"
    ] == 100
    assert schemas["ConversationDetailResponse"]["properties"]["messages"][
        "maxItems"
    ] == 200
    assert schemas["ConversationMessageResponse"]["properties"]["content"][
        "maxLength"
    ] == 16_000
    assert schemas["ConversationSummaryResponse"]["properties"]["title"][
        "maxLength"
    ] == 32

    expected_responses = {
        ("/conversations", "delete"): "ConversationDeleteAllResponse",
    }
    for (path, method), model_name in expected_responses.items():
        response_schema = openapi["paths"][path][method]["responses"]["200"][
            "content"
        ]["application/json"]["schema"]
        assert response_schema["$ref"].endswith(f"/{model_name}")
