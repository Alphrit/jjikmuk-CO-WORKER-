"""HTTP-only projections. Internal validation/trace fields never cross the wire.

Copy the original constrained field definitions, not values, so public payloads
remain bounded without duplicating types or weakening internal safety checks.
The explicit allowlist makes newly added internal fields private by default.
"""

from copy import deepcopy
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, create_model, model_serializer

from app.core.models import (
    ChatResponse as InternalChatResponse,
    ConversationMessageResponse as InternalMessageResponse,
    ConversationDetailResponse as InternalDetailResponse,
    MAX_PUBLIC_CONVERSATION_MESSAGE_ITEMS,
)


PUBLIC_CHAT_FIELDS = (
    "response_id", "generated_at", "task_type", "answer_source", "intent", "answer",
    "risk_level", "overall_decision", "allergy_status", "diet_match", "preference_match",
    "nutrition_facts", "missing_information", "assessment_status", "confidence",
    "confidence_reasons", "data_completeness", "data_completeness_reasons",
    "assessment_summary", "product_data_trust", "reasons", "profile_name",
    "active_profile_names", "per_profile_results", "current_product", "conversation_state",
    "conversation_id", "conversation_title", "conversation_revision", "turn_id",
    "recommended_questions", "product_candidates", "recommended_products", "recipe",
    "comparison_products", "ui_guidance", "sources", "safety_guidance", "external_ai_processing",
    "backend_analysis",
)


class _PublicResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, revalidate_instances="always")

    @model_serializer(mode="wrap")
    def validate_before_serialization(self, serializer):
        return serializer(type(self).model_validate(dict(self.__dict__)))


def _fields(model, names):
    return {name: (model.model_fields[name].annotation, deepcopy(model.model_fields[name])) for name in names}


PublicChatResponse = create_model(
    "ChatResponse", __base__=_PublicResponse,
    **_fields(InternalChatResponse, PUBLIC_CHAT_FIELDS),
)
PublicConversationMessageResponse = create_model(
    "ConversationMessageResponse", __base__=_PublicResponse,
    **_fields(InternalMessageResponse, [key for key in InternalMessageResponse.model_fields if key != "response"]),
    response=(Optional[PublicChatResponse], None),
)
PublicConversationDetailResponse = create_model(
    "ConversationDetailResponse", __base__=_PublicResponse,
    **_fields(InternalDetailResponse, [key for key in InternalDetailResponse.model_fields if key != "messages"]),
    messages=(list[PublicConversationMessageResponse], Field(default_factory=list, max_length=MAX_PUBLIC_CONVERSATION_MESSAGE_ITEMS)),
)
