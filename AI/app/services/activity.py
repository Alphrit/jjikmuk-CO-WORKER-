import logging
import os
from typing import List, Optional

from app.core.models import (
    ActivityProductLabelTrace,
    ActivityRecord,
    MAX_CHAT_MESSAGE_LENGTH,
    Product,
    Profile,
)
from app.infrastructure.repository import ActivityRepository, InMemoryActivityRepository


logger = logging.getLogger(__name__)


_TRUE_ENV_VALUES = {"1", "true", "yes", "on"}
_FALSE_ENV_VALUES = {"", "0", "false", "no", "off"}


def raw_chat_message_logging_enabled() -> bool:
    """Return true only for an explicit opt-in to retaining user message text."""
    raw_value = os.getenv("FOOD_CHATBOT_STORE_RAW_MESSAGES")
    if raw_value is None:
        return False
    value = raw_value.strip().lower()
    if value in _TRUE_ENV_VALUES:
        return True
    if value in _FALSE_ENV_VALUES:
        return False
    logger.warning(
        "Invalid FOOD_CHATBOT_STORE_RAW_MESSAGES value; raw messages will not be stored."
    )
    return False


def _message_for_audit_log(
    message: str,
    *,
    allow_raw_message_storage: bool = True,
) -> Optional[str]:
    if not allow_raw_message_storage or not raw_chat_message_logging_enabled():
        return None
    # API requests are already bounded by ChatRequest, but direct service callers
    # must not be able to create an unbounded audit-log value.
    return message[:MAX_CHAT_MESSAGE_LENGTH]


_default_activity_repository: ActivityRepository = InMemoryActivityRepository()


def get_activity_repository() -> ActivityRepository:
    return _default_activity_repository


def set_activity_repository(repository: ActivityRepository) -> None:
    global _default_activity_repository
    _default_activity_repository = repository


def log_chat_activity(
    profile: Optional[Profile],
    product: Optional[Product],
    barcode: Optional[str],
    report_number: Optional[str],
    message: str,
    intent: str,
    answer_source: Optional[str] = None,
    pipeline_stage: Optional[str] = None,
    risk_level: Optional[str] = None,
    confidence: Optional[float] = None,
    confidence_reasons: Optional[List[str]] = None,
    fallback_reason: Optional[str] = None,
    validation_failure_types: Optional[List[str]] = None,
    critical_safety_violation: bool = False,
    blocked_llm_output_stored: bool = False,
    response_id: Optional[str] = None,
    external_ai_used: bool = False,
    external_ai_services: Optional[List[str]] = None,
    policy_version: Optional[str] = None,
    applied_rule_ids: Optional[List[str]] = None,
    source_ids: Optional[List[str]] = None,
    response_product_label_trace: Optional[ActivityProductLabelTrace] = None,
    trusted_user_id: Optional[int] = None,
    allow_raw_message_storage: bool = True,
    repository: Optional[ActivityRepository] = None,
) -> None:
    repo = repository or get_activity_repository()
    try:
        repo.save(
            ActivityRecord(
                # Ownership is supplied only after request-level identity
                # verification. Profile.user_id is health/profile payload data,
                # not authentication authority.
                user_id=trusted_user_id,
                barcode=barcode or (product.barcode if product else None),
                report_number=report_number or (product.report_number if product else None),
                product_id=product.product_id if product else None,
                action_type="SCAN",
                response_id=response_id,
                external_ai_used=external_ai_used,
                external_ai_services=external_ai_services or [],
                policy_version=policy_version,
                applied_rule_ids=applied_rule_ids or [],
                source_ids=source_ids or [],
                response_product_label_trace=response_product_label_trace,
                message=_message_for_audit_log(
                    message,
                    allow_raw_message_storage=allow_raw_message_storage,
                ),
                intent=intent,
                answer_source=answer_source,
                pipeline_stage=pipeline_stage,
                risk_level=risk_level,
                confidence=confidence,
                confidence_reasons=confidence_reasons or [],
                fallback_reason=fallback_reason,
                validation_failure_types=validation_failure_types or [],
                critical_safety_violation=critical_safety_violation,
                blocked_llm_output_stored=blocked_llm_output_stored,
            )
        )
    except Exception as exc:
        logger.warning("Chat activity logging failed; preserving chatbot response: %s", exc)


def list_user_activities(
    user_id: int,
    limit: int = 20,
    repository: Optional[ActivityRepository] = None,
) -> List[ActivityRecord]:
    repo = repository or get_activity_repository()
    return repo.list_by_user_id(user_id=user_id, limit=limit)


def list_top_unknown_intent_messages(
    limit: int = 20,
    repository: Optional[ActivityRepository] = None,
) -> list[dict]:
    repo = repository or get_activity_repository()
    if limit < 1:
        limit = 1
    return repo.top_unknown_intent_messages(limit=min(limit, 100))


def get_fallback_summary(
    limit: int = 20,
    repository: Optional[ActivityRepository] = None,
) -> dict:
    repo = repository or get_activity_repository()
    if limit < 1:
        limit = 1
    return repo.fallback_summary(limit=min(limit, 100))


def list_recent_fallback_logs(
    limit: int = 20,
    repository: Optional[ActivityRepository] = None,
) -> list[dict]:
    repo = repository or get_activity_repository()
    if limit < 1:
        limit = 1
    return repo.recent_fallback_logs(limit=min(limit, 100))
