"""activity storage / common repository implementation."""

from app.core.models import ActivityProductLabelTrace, ActivityRecord
from collections import Counter
from typing import Any, Dict, List, Optional
import json


class ActivityRepository:
    def readiness_check(self) -> Dict[str, Any]:
        return {
            "available": False,
            "audit_log_ready": False,
            "reason": "readiness_check_not_implemented",
        }

    def save(self, activity: ActivityRecord) -> None:
        raise NotImplementedError

    def list_by_user_id(self, user_id: int, limit: int = 20) -> List[ActivityRecord]:
        raise NotImplementedError

    def top_unknown_intent_messages(self, limit: int = 20) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def fallback_summary(self, limit: int = 20) -> Dict[str, Any]:
        raise NotImplementedError

    def recent_fallback_logs(self, limit: int = 20) -> List[Dict[str, Any]]:
        raise NotImplementedError


def _normalize_unknown_message(message: Optional[str]) -> str:
    if not message:
        return ""
    return " ".join(str(message).strip().lower().split())


def _activity_created_at_sort_value(activity: ActivityRecord) -> str:
    return str(activity.created_at or "")


def _response_product_label_trace_json(activity: ActivityRecord) -> Optional[str]:
    """Serialize only the bounded response-time label trace, never raw label data."""

    trace = activity.response_product_label_trace
    if trace is None:
        return None
    return json.dumps(
        trace.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _decode_response_product_label_trace(
    value: Any,
) -> Optional[ActivityProductLabelTrace]:
    """Decode a persisted trace; missing/invalid legacy values fail closed to None."""

    if value is None or value == "":
        return None
    if isinstance(value, (bytes, bytearray)):
        try:
            value = bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if not isinstance(value, dict):
        return None
    try:
        return ActivityProductLabelTrace.model_validate(value)
    except (TypeError, ValueError):
        # The nested model has an exact seven-field allowlist. Corrupt or
        # unexpectedly expanded DB JSON must never become a trusted audit trace.
        return None


def _normalize_nullable_activity_list_fields(payload: Dict[str, Any]) -> None:
    """Keep nullable legacy JSON columns compatible with typed list defaults."""

    for field in (
        "confidence_reasons",
        "validation_failure_types",
        "external_ai_services",
        "applied_rule_ids",
        "source_ids",
    ):
        if payload.get(field) in (None, ""):
            payload[field] = []


def _fallback_failure_types(activity: ActivityRecord) -> List[str]:
    return list(activity.validation_failure_types or [])


def _message_preview(message: Optional[str], max_length: int = 80) -> Optional[str]:
    if not message:
        return None
    normalized = " ".join(str(message).split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[:max_length].rstrip()}..."


def _fallback_log_payload(activity: ActivityRecord) -> Dict[str, Any]:
    return {
        "user_id": activity.user_id,
        "barcode": activity.barcode,
        "report_number": activity.report_number,
        "message_preview": _message_preview(activity.message),
        "message_length": len(activity.message) if activity.message else 0,
        "intent": activity.intent,
        "answer_source": activity.answer_source,
        "pipeline_stage": activity.pipeline_stage,
        "risk_level": activity.risk_level,
        "confidence": activity.confidence,
        "fallback_reason": activity.fallback_reason,
        "validation_failure_types": _fallback_failure_types(activity),
        "critical_safety_violation": activity.critical_safety_violation,
        "blocked_llm_output_stored": activity.blocked_llm_output_stored,
        "response_id": activity.response_id,
        "response_product_label_trace": (
            activity.response_product_label_trace.model_dump(
                mode="json",
            )
            if activity.response_product_label_trace is not None
            else None
        ),
        "external_ai_used": activity.external_ai_used,
        "external_ai_services": list(activity.external_ai_services),
        "policy_version": activity.policy_version,
        "applied_rule_ids": list(activity.applied_rule_ids),
        "source_ids": list(activity.source_ids),
        "created_at": activity.created_at,
    }


def _fallback_summary_from_records(records: List[ActivityRecord], limit: int = 20) -> Dict[str, Any]:
    fallback_records = [
        record
        for record in records
        if record.fallback_reason or record.validation_failure_types or record.critical_safety_violation
    ]
    reason_counts = Counter(record.fallback_reason or "unspecified" for record in fallback_records)
    failure_type_counts: Counter[str] = Counter()
    intent_counts = Counter(record.intent or "unknown" for record in fallback_records)
    answer_source_counts = Counter(record.answer_source or "unknown" for record in fallback_records)
    for record in fallback_records:
        for failure_type in _fallback_failure_types(record):
            failure_type_counts[failure_type] += 1

    recent_records = sorted(fallback_records, key=_activity_created_at_sort_value, reverse=True)[:limit]
    return {
        "total_fallbacks": len(fallback_records),
        "critical_safety_violation_count": sum(1 for record in fallback_records if record.critical_safety_violation),
        "blocked_llm_output_stored_count": sum(1 for record in fallback_records if record.blocked_llm_output_stored),
        "by_fallback_reason": [
            {"fallback_reason": reason, "count": count}
            for reason, count in reason_counts.most_common(limit)
        ],
        "by_validation_failure_type": [
            {"validation_failure_type": failure_type, "count": count}
            for failure_type, count in failure_type_counts.most_common(limit)
        ],
        "by_intent": [
            {"intent": intent, "count": count}
            for intent, count in intent_counts.most_common(limit)
        ],
        "by_answer_source": [
            {"answer_source": answer_source, "count": count}
            for answer_source, count in answer_source_counts.most_common(limit)
        ],
        "recent_samples": [_fallback_log_payload(record) for record in recent_records],
    }
