"""activity storage / sqlite repository implementation."""

from app.core.models import ActivityRecord
from typing import Any, Dict, List, Optional
import json
import sqlite3

from app.infrastructure.activity_storage.common import (
    ActivityRepository,
    _decode_response_product_label_trace,
    _fallback_log_payload,
    _fallback_summary_from_records,
    _normalize_nullable_activity_list_fields,
    _response_product_label_trace_json,
)


class SQLiteActivityRepository(ActivityRepository):
    def __init__(self, db_path: str, table_name: str = "activity_history"):
        self.db_path = db_path
        self.table_name = table_name
        self._ensure_table()

    def readiness_check(self) -> Dict[str, Any]:
        required_columns = {
            "id",
            "user_id",
            "barcode",
            "report_number",
            "product_id",
            "action_type",
            "message",
            "intent",
            "answer_source",
            "pipeline_stage",
            "risk_level",
            "confidence",
            "confidence_reasons",
            "fallback_reason",
            "validation_failure_types",
            "critical_safety_violation",
            "blocked_llm_output_stored",
            "response_id",
            "external_ai_used",
            "external_ai_services",
            "policy_version",
            "applied_rule_ids",
            "source_ids",
            "response_product_label_trace",
            "created_at",
        }
        try:
            with sqlite3.connect(self.db_path) as conn:
                table_exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
                    (self.table_name,),
                ).fetchone() is not None
                if not table_exists:
                    return {
                        "available": False,
                        "audit_log_ready": False,
                        "table": self.table_name,
                        "reason": "activity_table_missing",
                    }
                columns = {
                    row[1]
                    for row in conn.execute(f"PRAGMA table_info({self.table_name})").fetchall()
                }
        except (sqlite3.Error, OSError) as exc:
            return {
                "available": False,
                "audit_log_ready": False,
                "table": self.table_name,
                "reason": "sqlite_activity_check_failed",
                "error_type": type(exc).__name__,
            }

        missing_columns = sorted(required_columns - columns)
        audit_log_ready = not missing_columns
        return {
            "available": audit_log_ready,
            "audit_log_ready": audit_log_ready,
            "table": self.table_name,
            "reason": None if audit_log_ready else "activity_columns_missing",
            "missing_required_column_count": len(missing_columns),
        }

    def health_check(self) -> Dict[str, Any]:
        return self.readiness_check()

    def _ensure_table(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table_name} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    barcode TEXT,
                    report_number TEXT,
                    product_id INTEGER,
                    action_type TEXT NOT NULL,
                    message TEXT,
                    intent TEXT,
                    answer_source TEXT,
                    pipeline_stage TEXT,
                    risk_level TEXT,
                    confidence REAL,
                    confidence_reasons TEXT,
                    fallback_reason TEXT,
                    validation_failure_types TEXT,
                    critical_safety_violation INTEGER DEFAULT 0,
                    blocked_llm_output_stored INTEGER DEFAULT 0,
                    response_id TEXT,
                    external_ai_used INTEGER NOT NULL DEFAULT 0,
                    external_ai_services TEXT NOT NULL DEFAULT '[]',
                    policy_version TEXT,
                    applied_rule_ids TEXT NOT NULL DEFAULT '[]',
                    source_ids TEXT NOT NULL DEFAULT '[]',
                    response_product_label_trace TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            existing_columns = {
                row[1]
                for row in conn.execute(f"PRAGMA table_info({self.table_name})").fetchall()
            }
            if "confidence" not in existing_columns:
                conn.execute(f"ALTER TABLE {self.table_name} ADD COLUMN confidence REAL")
            if "confidence_reasons" not in existing_columns:
                conn.execute(f"ALTER TABLE {self.table_name} ADD COLUMN confidence_reasons TEXT")
            if "fallback_reason" not in existing_columns:
                conn.execute(f"ALTER TABLE {self.table_name} ADD COLUMN fallback_reason TEXT")
            if "validation_failure_types" not in existing_columns:
                conn.execute(f"ALTER TABLE {self.table_name} ADD COLUMN validation_failure_types TEXT")
            if "critical_safety_violation" not in existing_columns:
                conn.execute(f"ALTER TABLE {self.table_name} ADD COLUMN critical_safety_violation INTEGER DEFAULT 0")
            if "blocked_llm_output_stored" not in existing_columns:
                conn.execute(f"ALTER TABLE {self.table_name} ADD COLUMN blocked_llm_output_stored INTEGER DEFAULT 0")
            if "response_id" not in existing_columns:
                conn.execute(f"ALTER TABLE {self.table_name} ADD COLUMN response_id TEXT")
            if "external_ai_used" not in existing_columns:
                conn.execute(
                    f"ALTER TABLE {self.table_name} ADD COLUMN external_ai_used INTEGER NOT NULL DEFAULT 0"
                )
            if "external_ai_services" not in existing_columns:
                conn.execute(
                    f"ALTER TABLE {self.table_name} ADD COLUMN external_ai_services TEXT NOT NULL DEFAULT '[]'"
                )
            if "policy_version" not in existing_columns:
                conn.execute(f"ALTER TABLE {self.table_name} ADD COLUMN policy_version TEXT")
            if "applied_rule_ids" not in existing_columns:
                conn.execute(f"ALTER TABLE {self.table_name} ADD COLUMN applied_rule_ids TEXT NOT NULL DEFAULT '[]'")
            if "source_ids" not in existing_columns:
                conn.execute(f"ALTER TABLE {self.table_name} ADD COLUMN source_ids TEXT NOT NULL DEFAULT '[]'")
            if "response_product_label_trace" not in existing_columns:
                conn.execute(
                    f"ALTER TABLE {self.table_name} "
                    "ADD COLUMN response_product_label_trace TEXT"
                )
            conn.commit()

    def save(self, activity: ActivityRecord) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"""
                INSERT INTO {self.table_name}
                (
                    user_id, barcode, report_number, product_id, action_type,
                    message, intent, answer_source, pipeline_stage, risk_level,
                    confidence, confidence_reasons, fallback_reason,
                    validation_failure_types, critical_safety_violation,
                    blocked_llm_output_stored, response_id, external_ai_used,
                    external_ai_services, policy_version, applied_rule_ids, source_ids,
                    response_product_label_trace
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    activity.user_id,
                    activity.barcode,
                    activity.report_number,
                    activity.product_id,
                    activity.action_type,
                    activity.message,
                    activity.intent,
                    activity.answer_source,
                    activity.pipeline_stage,
                    activity.risk_level,
                    activity.confidence,
                    json.dumps(activity.confidence_reasons, ensure_ascii=False),
                    activity.fallback_reason,
                    json.dumps(activity.validation_failure_types, ensure_ascii=False),
                    int(activity.critical_safety_violation),
                    int(activity.blocked_llm_output_stored),
                    activity.response_id,
                    int(activity.external_ai_used),
                    json.dumps(activity.external_ai_services, ensure_ascii=False),
                    activity.policy_version,
                    json.dumps(activity.applied_rule_ids, ensure_ascii=False),
                    json.dumps(activity.source_ids, ensure_ascii=False),
                    _response_product_label_trace_json(activity),
                ),
            )
            conn.commit()

    def list_by_user_id(self, user_id: int, limit: int = 20) -> List[ActivityRecord]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT
                    user_id, barcode, report_number, product_id, action_type,
                    message, intent, answer_source, pipeline_stage, risk_level,
                    confidence, confidence_reasons, fallback_reason,
                    validation_failure_types, critical_safety_violation,
                    blocked_llm_output_stored, response_id, external_ai_used,
                    external_ai_services, policy_version, applied_rule_ids, source_ids,
                    response_product_label_trace
                FROM {self.table_name}
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()

        records = []
        for row in rows:
            payload = dict(row)
            raw_confidence_reasons = payload.get("confidence_reasons")
            if isinstance(raw_confidence_reasons, str) and raw_confidence_reasons:
                try:
                    payload["confidence_reasons"] = json.loads(raw_confidence_reasons)
                except json.JSONDecodeError:
                    payload["confidence_reasons"] = [raw_confidence_reasons]
            raw_validation_failure_types = payload.get("validation_failure_types")
            if isinstance(raw_validation_failure_types, str) and raw_validation_failure_types:
                try:
                    payload["validation_failure_types"] = json.loads(raw_validation_failure_types)
                except json.JSONDecodeError:
                    payload["validation_failure_types"] = [raw_validation_failure_types]
            raw_external_ai_services = payload.get("external_ai_services")
            if isinstance(raw_external_ai_services, str) and raw_external_ai_services:
                try:
                    payload["external_ai_services"] = json.loads(raw_external_ai_services)
                except json.JSONDecodeError:
                    payload["external_ai_services"] = []
            for field in ("applied_rule_ids", "source_ids"):
                raw = payload.get(field)
                if isinstance(raw, str) and raw:
                    try:
                        payload[field] = json.loads(raw)
                    except json.JSONDecodeError:
                        payload[field] = []
            payload["response_product_label_trace"] = (
                _decode_response_product_label_trace(
                    payload.get("response_product_label_trace")
                )
            )
            _normalize_nullable_activity_list_fields(payload)
            records.append(ActivityRecord(**payload))
        return records

    def top_unknown_intent_messages(self, limit: int = 20) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT
                    LOWER(TRIM(message)) AS message,
                    MIN(message) AS example,
                    COUNT(*) AS count,
                    MAX(created_at) AS last_seen_at
                FROM {self.table_name}
                WHERE intent = ?
                  AND message IS NOT NULL
                  AND TRIM(message) <> ''
                GROUP BY LOWER(TRIM(message))
                ORDER BY count DESC, last_seen_at DESC
                LIMIT ?
                """,
                ("unknown", limit),
            ).fetchall()

        return [
            {
                "rank": index + 1,
                "message": row["message"],
                "count": row["count"],
                "example": row["example"],
                "last_seen_at": row["last_seen_at"],
            }
            for index, row in enumerate(rows)
        ]

    def _fallback_activity_records(self, limit: Optional[int] = None) -> List[ActivityRecord]:
        limit_sql = "LIMIT ?" if limit is not None else ""
        params: tuple[Any, ...] = (limit,) if limit is not None else ()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT
                    user_id, barcode, report_number, product_id, action_type,
                    message, intent, answer_source, pipeline_stage, risk_level,
                    confidence, fallback_reason, validation_failure_types,
                    critical_safety_violation, blocked_llm_output_stored,
                    response_id, external_ai_used, external_ai_services,
                    response_product_label_trace,
                    created_at
                FROM {self.table_name}
                WHERE
                    (fallback_reason IS NOT NULL AND fallback_reason <> '')
                    OR (validation_failure_types IS NOT NULL AND validation_failure_types <> '' AND validation_failure_types <> '[]')
                    OR critical_safety_violation = 1
                ORDER BY created_at DESC, id DESC
                {limit_sql}
                """,
                params,
            ).fetchall()

        records = []
        for row in rows:
            payload = dict(row)
            raw_validation_failure_types = payload.get("validation_failure_types")
            if isinstance(raw_validation_failure_types, str) and raw_validation_failure_types:
                try:
                    payload["validation_failure_types"] = json.loads(raw_validation_failure_types)
                except json.JSONDecodeError:
                    payload["validation_failure_types"] = [raw_validation_failure_types]
            raw_external_ai_services = payload.get("external_ai_services")
            if isinstance(raw_external_ai_services, str) and raw_external_ai_services:
                try:
                    payload["external_ai_services"] = json.loads(raw_external_ai_services)
                except json.JSONDecodeError:
                    payload["external_ai_services"] = []
            payload["response_product_label_trace"] = (
                _decode_response_product_label_trace(
                    payload.get("response_product_label_trace")
                )
            )
            _normalize_nullable_activity_list_fields(payload)
            records.append(ActivityRecord(**payload))
        return records

    def fallback_summary(self, limit: int = 20) -> Dict[str, Any]:
        return _fallback_summary_from_records(self._fallback_activity_records(), limit=limit)

    def recent_fallback_logs(self, limit: int = 20) -> List[Dict[str, Any]]:
        return [
            _fallback_log_payload(record)
            for record in self._fallback_activity_records(limit=limit)
        ]
