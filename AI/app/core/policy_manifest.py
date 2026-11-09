"""Source-controlled policy evidence for the allergy chatbot.

Only the fixed response for a clearly current, severe allergic reaction needs
an external health-policy source. Product decisions are derived from product
labels and active profiles, while diet results use explicit product claims;
neither is represented here as medical guidance.
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


_MANIFEST_PATH = Path(__file__).resolve().parents[1] / "policies" / "policy_manifest.json"
_ALLOWED_HOSTS = {"health.kdca.go.kr"}
_OFFICIAL_TYPES = {"official_primary", "professional_society"}
_REVIEWER_ROLES = {"engineering_policy_owner"}

_ANAPHYLAXIS_URL = (
    "https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/"
    "gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=6684"
)
_REQUIRED_SOURCE_CONTRACTS = {
    "official.kdca.anaphylaxis": {
        "source_type": "official_primary",
        "title": "아나필락시스",
        "publisher": "질병관리청 국가건강정보포털",
        "canonical_url": _ANAPHYLAXIS_URL,
    },
}
_REQUIRED_RULE_CONTRACTS = {
    "emergency.anaphylaxis.routing.v1": (
        "service_safety_routing",
        (),
        "internal_heuristic",
        "현재성 있는 중증 알레르기 증상 표현을 고정 응급 안내로 라우팅",
    ),
    "safety.allergic_reaction.routing.v1": (
        "service_safety_routing",
        (),
        "internal_heuristic",
        "음식 섭취 직후의 경증 알레르기 증상 표현을 고정 안전 안내(악화 시 119, 진료 권고, 진단 불가)로 라우팅",
    ),
    "emergency.anaphylaxis.action.v1": (
        "official_guidance",
        ("official.kdca.anaphylaxis",),
        "direct",
        "의심되는 아나필락시스 상황의 119 연락과 처방·교육에 따른 에피네프린 사용 안내",
    ),
}
REQUIRED_EMERGENCY_RULE_IDS = frozenset(_REQUIRED_RULE_CONTRACTS)


def _iso(value: str, field: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field} must use YYYY-MM-DD")
    return parsed


class PolicySource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]+$")
    source_type: Literal[
        "official_primary",
        "professional_society",
        "internal_service_policy",
        "product_database",
    ]
    title: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    canonical_url: str
    reviewed_at: str
    effective_from: str
    effective_to: str | None = None
    review_due: str
    reviewer_role: str = Field(min_length=3)
    status: str = Field(pattern=r"^(active|expired|withdrawn)$")

    @model_validator(mode="after")
    def validate_source(self) -> "PolicySource":
        reviewed = _iso(self.reviewed_at, "reviewed_at")
        effective = _iso(self.effective_from, "effective_from")
        due = _iso(self.review_due, "review_due")
        effective_to = _iso(self.effective_to, "effective_to") if self.effective_to else None
        if due <= reviewed or due < effective:
            raise ValueError("review_due must be after reviewed_at and effective_from")
        if effective_to is not None and effective_to <= effective:
            raise ValueError("effective_to must be after effective_from")
        if self.status == "active" and effective_to is not None and effective_to <= date.today():
            raise ValueError("an active source cannot have an elapsed effective_to")
        if self.reviewer_role not in _REVIEWER_ROLES:
            raise ValueError("reviewer_role is not an approved service role")

        parts = urlsplit(self.canonical_url)
        if (
            parts.scheme != "https"
            or parts.hostname not in _ALLOWED_HOSTS
            or parts.username
            or parts.password
            or parts.port not in (None, 443)
            or parts.fragment
            or parts.netloc != parts.hostname
        ):
            raise ValueError("source URL must use the approved canonical HTTPS origin")
        return self


class PolicyRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]+$")
    rule_kind: str = Field(
        pattern=(
            r"^(official_guidance|service_safety_routing|service_heuristic|"
            r"label_fact_match|unsupported_boundary)$"
        )
    )
    exact_claim_scope: str = Field(min_length=10)
    source_ids: tuple[str, ...]
    support_level: str = Field(
        pattern=r"^(direct|conservative_adaptation|internal_heuristic)$"
    )
    effective_from: str
    review_due: str
    reviewer_role: str = Field(min_length=3)

    @model_validator(mode="after")
    def validate_reviewer(self) -> "PolicyRule":
        if self.reviewer_role not in _REVIEWER_ROLES:
            raise ValueError("reviewer_role is not an approved service role")
        return self


class PolicyManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1)
    policy_version: str = Field(pattern=r"^\d{4}\.\d{2}\.\d{2}\.\d+$")
    sources: tuple[PolicySource, ...]
    rules: tuple[PolicyRule, ...]

    @model_validator(mode="after")
    def validate_relations(self) -> "PolicyManifest":
        sources = {source.source_id: source for source in self.sources}
        rules = {rule.rule_id: rule for rule in self.rules}
        if len(sources) != len(self.sources):
            raise ValueError("duplicate source_id")
        if len(rules) != len(self.rules):
            raise ValueError("duplicate rule_id")

        # The manifest is intentionally an allow-list. Adding another medical
        # domain must be an explicit product decision, not a stray JSON edit.
        if set(sources) != set(_REQUIRED_SOURCE_CONTRACTS):
            raise ValueError("manifest contains missing or unsupported sources")
        if set(rules) != set(_REQUIRED_RULE_CONTRACTS):
            raise ValueError("manifest contains missing or unsupported rules")

        for source_id, contract in _REQUIRED_SOURCE_CONTRACTS.items():
            source = sources[source_id]
            if any(getattr(source, field) != value for field, value in contract.items()):
                raise ValueError(f"{source_id}: required source contract changed")

        for rule in self.rules:
            effective = _iso(rule.effective_from, "effective_from")
            due = _iso(rule.review_due, "review_due")
            if due <= effective:
                raise ValueError(f"{rule.rule_id}: review_due must be after effective_from")
            if len(rule.source_ids) != len(set(rule.source_ids)):
                raise ValueError(f"{rule.rule_id}: duplicate source relation")
            if any(source_id not in sources for source_id in rule.source_ids):
                raise ValueError(f"{rule.rule_id}: unknown source_id")
            if rule.rule_kind == "service_safety_routing":
                if rule.source_ids or rule.support_level != "internal_heuristic":
                    raise ValueError("routing rules cannot claim external evidence")
            elif rule.rule_kind == "official_guidance":
                if not rule.source_ids or rule.support_level == "internal_heuristic":
                    raise ValueError("official guidance requires public evidence")
                if any(sources[source_id].source_type not in _OFFICIAL_TYPES for source_id in rule.source_ids):
                    raise ValueError("official guidance has a non-official source")
            else:
                raise ValueError("only allergy emergency policy rule kinds are supported")

        for rule_id, expected in _REQUIRED_RULE_CONTRACTS.items():
            rule = rules[rule_id]
            actual = (
                rule.rule_kind,
                tuple(rule.source_ids),
                rule.support_level,
                rule.exact_claim_scope,
            )
            if actual != expected:
                raise ValueError(f"{rule_id}: required rule contract changed")
        return self

    def source(self, source_id: str) -> PolicySource:
        return next(source for source in self.sources if source.source_id == source_id)

    def rule(self, rule_id: str) -> PolicyRule:
        return next(rule for rule in self.rules if rule.rule_id == rule_id)

    def source_is_current(self, source_id: str, *, on: date | None = None) -> bool:
        source = self.source(source_id)
        day = on or date.today()
        before_end = source.effective_to is None or day < _iso(source.effective_to, "effective_to")
        return (
            source.status == "active"
            and _iso(source.effective_from, "effective_from") <= day < _iso(source.review_due, "review_due")
            and before_end
        )

    def rule_is_current(self, rule_id: str, *, on: date | None = None) -> bool:
        rule = self.rule(rule_id)
        day = on or date.today()
        return (
            _iso(rule.effective_from, "effective_from") <= day < _iso(rule.review_due, "review_due")
            and all(self.source_is_current(source_id, on=day) for source_id in rule.source_ids)
        )

    def evidence_is_exact(self, evidence: Any) -> bool:
        try:
            rule = self.rule(evidence.rule_id)
            source = self.source(evidence.source_id) if evidence.source_id else None
        except (AttributeError, StopIteration):
            return False
        expected_sources = rule.source_ids or (None,)
        expected_due = min(
            [rule.review_due]
            + [self.source(source_id).review_due for source_id in rule.source_ids]
        )
        return (
            evidence.source_id in expected_sources
            and evidence.evidence_id == f"{rule.rule_id}:{evidence.source_id or 'internal'}"
            and evidence.claim_scope == rule.exact_claim_scope
            and evidence.evidence_type == rule.rule_kind
            and evidence.support_level == rule.support_level
            and evidence.reviewed_at == (source.reviewed_at if source else None)
            and evidence.review_due == expected_due
        )


def parse_policy_manifest(payload: dict[str, Any]) -> PolicyManifest:
    return PolicyManifest.model_validate(payload)


@lru_cache(maxsize=1)
def get_policy_manifest() -> PolicyManifest:
    with _MANIFEST_PATH.open(encoding="utf-8") as stream:
        return parse_policy_manifest(json.load(stream))


def reset_policy_manifest_cache() -> None:
    """Clear the manifest cache for configuration and isolated tests."""

    get_policy_manifest.cache_clear()


def policy_manifest_status() -> dict[str, Any]:
    manifest = get_policy_manifest()
    current = sum(manifest.rule_is_current(rule.rule_id) for rule in manifest.rules)
    return {
        "policy_manifest_valid": True,
        "policy_version": manifest.policy_version,
        "current_rule_count": current,
        "expired_rule_count": len(manifest.rules) - current,
        "all_registered_rules_current": current == len(manifest.rules),
        "required_emergency_rules_current": all(
            manifest.rule_is_current(rule_id) for rule_id in REQUIRED_EMERGENCY_RULE_IDS
        ),
    }


def assert_required_policy_ready() -> None:
    status = policy_manifest_status()
    if not (
        status["required_emergency_rules_current"]
        and status["all_registered_rules_current"]
    ):
        raise RuntimeError("The severe-allergy emergency policy is missing or expired.")


def response_evidence_matches_manifest(response: Any) -> bool:
    """Validate exact evidence coverage for every rule named by a response."""

    manifest = get_policy_manifest()
    if (
        response.policy_version != manifest.policy_version
        or response.provenance_status not in {"complete", "partial"}
        or not response.applied_rule_ids
        or len(response.applied_rule_ids) != len(set(response.applied_rule_ids))
    ):
        return False
    try:
        expected = {
            (rule_id, source_id)
            for rule_id in response.applied_rule_ids
            for source_id in (manifest.rule(rule_id).source_ids or (None,))
        }
    except StopIteration:
        return False
    actual = {(item.rule_id, item.source_id) for item in response.evidence}
    if not (
        expected == actual
        and len(actual) == len(response.evidence)
        and all(manifest.rule_is_current(rule_id) for rule_id in response.applied_rule_ids)
        and all(manifest.evidence_is_exact(item) for item in response.evidence)
    ):
        return False

    expected_details: list[tuple[Any, ...]] = []
    expected_urls: list[str] = []
    seen_sources: set[str] = set()
    for rule_id in response.applied_rule_ids:
        rule = manifest.rule(rule_id)
        for source_id in rule.source_ids:
            if source_id in seen_sources:
                continue
            seen_sources.add(source_id)
            source = manifest.source(source_id)
            expected_details.append((
                source.source_id,
                source.title,
                source.publisher,
                source.canonical_url,
                source.reviewed_at,
                rule.exact_claim_scope,
                source.review_due,
            ))
            expected_urls.append(source.canonical_url)
    actual_details = [
        (
            item.source_id,
            item.title,
            item.publisher,
            item.url,
            item.reviewed_at,
            item.claim_scope,
            item.review_due,
        )
        for item in response.source_details
    ]
    return actual_details == expected_details and list(response.sources) == expected_urls


def response_evidence_is_complete(response: Any) -> bool:
    return (
        getattr(response, "provenance_status", None) == "complete"
        and response_evidence_matches_manifest(response)
    )


def response_provenance(rule_ids: list[str]) -> dict[str, Any]:
    """Build public evidence metadata solely from validated manifest entries."""

    manifest = get_policy_manifest()
    evidence: list[dict[str, Any]] = []
    source_details: list[dict[str, Any]] = []
    seen_sources: set[str] = set()
    complete = True
    for rule_id in rule_ids:
        rule = manifest.rule(rule_id)
        complete = complete and manifest.rule_is_current(rule_id)
        if not rule.source_ids:
            evidence.append({
                "evidence_id": f"{rule_id}:internal",
                "rule_id": rule_id,
                "claim_scope": rule.exact_claim_scope,
                "evidence_type": rule.rule_kind,
                "source_id": None,
                "support_level": rule.support_level,
                "review_due": rule.review_due,
            })
        for source_id in rule.source_ids:
            source = manifest.source(source_id)
            evidence.append({
                "evidence_id": f"{rule_id}:{source_id}",
                "rule_id": rule_id,
                "claim_scope": rule.exact_claim_scope,
                "evidence_type": rule.rule_kind,
                "source_id": source_id,
                "support_level": rule.support_level,
                "reviewed_at": source.reviewed_at,
                "review_due": min(rule.review_due, source.review_due),
            })
            if source_id not in seen_sources:
                seen_sources.add(source_id)
                source_details.append({
                    "source_id": source_id,
                    "title": source.title,
                    "publisher": source.publisher,
                    "url": source.canonical_url,
                    "reviewed_at": source.reviewed_at,
                    "claim_scope": rule.exact_claim_scope,
                    "review_due": source.review_due,
                })
    return {
        "policy_version": manifest.policy_version,
        "applied_rule_ids": rule_ids,
        "evidence": evidence,
        "source_details": source_details,
        "sources": [source["url"] for source in source_details],
        "provenance_status": "complete" if complete else "partial",
    }
