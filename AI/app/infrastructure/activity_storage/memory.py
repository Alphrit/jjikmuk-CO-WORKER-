"""activity storage / memory repository implementation."""

from app.core.models import ActivityRecord
from collections import Counter
from typing import Any, Dict, List, Optional

from app.infrastructure.activity_storage.common import (
    ActivityRepository,
    _activity_created_at_sort_value,
    _fallback_log_payload,
    _fallback_summary_from_records,
    _normalize_unknown_message,
)


class InMemoryActivityRepository(ActivityRepository):
    def __init__(self):
        self.records: List[ActivityRecord] = []

    def readiness_check(self) -> Dict[str, Any]:
        return {
            "available": True,
            "audit_log_ready": True,
            "provider": "memory",
        }

    def save(self, activity: ActivityRecord) -> None:
        self.records.append(activity)

    def list_by_user_id(self, user_id: int, limit: int = 20) -> List[ActivityRecord]:
        filtered = [record for record in self.records if record.user_id == user_id]
        return filtered[-limit:][::-1]

    def top_unknown_intent_messages(self, limit: int = 20) -> List[Dict[str, Any]]:
        unknown_records = [
            record
            for record in self.records
            if record.intent == "unknown" and _normalize_unknown_message(record.message)
        ]
        counts = Counter(_normalize_unknown_message(record.message) for record in unknown_records)
        first_examples: Dict[str, str] = {}
        last_seen: Dict[str, Optional[str]] = {}
        for record in unknown_records:
            normalized_message = _normalize_unknown_message(record.message)
            first_examples.setdefault(normalized_message, record.message or normalized_message)
            last_seen[normalized_message] = record.created_at

        return [
            {
                "rank": index + 1,
                "message": message,
                "count": count,
                "example": first_examples.get(message, message),
                "last_seen_at": last_seen.get(message),
            }
            for index, (message, count) in enumerate(counts.most_common(limit))
        ]

    def fallback_summary(self, limit: int = 20) -> Dict[str, Any]:
        return _fallback_summary_from_records(self.records, limit=limit)

    def recent_fallback_logs(self, limit: int = 20) -> List[Dict[str, Any]]:
        fallback_records = [
            record
            for record in self.records
            if record.fallback_reason or record.validation_failure_types or record.critical_safety_violation
        ]
        recent_records = sorted(fallback_records, key=_activity_created_at_sort_value, reverse=True)[:limit]
        return [_fallback_log_payload(record) for record in recent_records]
